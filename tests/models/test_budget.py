"""``--max-calls`` (D18): a hard ceiling on model calls, drained gracefully.

Per-item verbs get a tripped ``stop`` event (the Ctrl-C drain machinery); whole-set
verbs (no stop) get the fatal screen — a partial collection is nothing usable.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.core.errors import ItemError, RetryableError, SetupFault, UnsentError
from smartpipe.models.base import CompletionRequest, ModelRef
from smartpipe.models.budget import (
    CallBudget,
    budgeted_chat,
    budgeted_embed,
    budgeted_parser,
    budgeted_transcriber,
)
from smartpipe.models.ocr import OcrPage
from tests.helpers.pdf import minimal_pdf

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class FakeChat:
    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake")
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> str:
        self.calls += 1
        return "ok"


class FakeEmbed:
    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake-embed")
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        return tuple((1.0,) for _ in texts)


REQUEST = CompletionRequest(system=None, user="x")


async def test_budget_trips_stop_at_the_limit_and_skips_after() -> None:
    stop = asyncio.Event()
    budget = CallBudget(limit=2, stop=stop)
    inner = FakeChat()
    model = budgeted_chat(inner, budget)
    assert model.ref == inner.ref  # the wrapper is invisible to callers
    await model.complete(REQUEST)
    assert not stop.is_set()
    await model.complete(REQUEST)
    assert stop.is_set()  # the limit call still ran, intake stops now
    assert budget.exhausted
    with pytest.raises(UnsentError, match="call budget"):
        await model.complete(REQUEST)  # a racing in-flight worker: skip, not crash
    assert inner.calls == 2  # the raced call never reached the wire


async def test_whole_set_budget_exhaustion_is_fatal() -> None:
    budget = CallBudget(limit=1, stop=None)  # whole-set verbs run without a stop event
    model = budgeted_embed(FakeEmbed(), budget)
    await model.embed(["a"])
    with pytest.raises(SetupFault, match="call budget reached mid-collection"):
        await model.embed(["b"])


async def test_charges_count_calls_not_texts() -> None:
    budget = CallBudget(limit=2, stop=asyncio.Event())
    model = budgeted_embed(FakeEmbed(), budget)
    await model.embed(["a", "b", "c"])  # one batched call = one charge
    assert budget.calls == 1


async def test_chat_preflight_failure_is_not_a_billable_call() -> None:
    class RejectingChat:
        ref = ModelRef("ollama", "no-audio")

        def preflight(self, request: CompletionRequest) -> None:
            del request
            raise ItemError("unsupported media")

        async def complete(self, request: CompletionRequest) -> str:
            del request
            raise AssertionError("preflight rejection must never reach the adapter send")

    stop = asyncio.Event()
    budget = CallBudget(limit=1, stop=stop)

    with pytest.raises(ItemError, match="unsupported media"):
        await budgeted_chat(RejectingChat(), budget).complete(REQUEST)

    assert budget.calls == 0
    assert not budget.exhausted
    assert not stop.is_set()


async def test_unsupported_ollama_media_neither_sends_nor_spends() -> None:
    from smartpipe.models.base import AudioData
    from smartpipe.models.ollama import OllamaChatModel

    sends = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sends
        sends += 1
        return httpx.Response(200, json={"message": {"content": "unexpected"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inner = OllamaChatModel(
            ref=ModelRef("ollama", "text-only"),
            client=client,
            host="http://localhost:11434",
        )
        budget = CallBudget(limit=1, stop=asyncio.Event())
        request = CompletionRequest(
            system=None,
            user="listen",
            media=(AudioData(b"wav", "audio/wav"),),
        )

        with pytest.raises(ItemError, match="can't hear audio"):
            await budgeted_chat(inner, budget).complete(request)

    assert sends == 0
    assert budget.calls == 0
    assert not budget.exhausted


async def test_ocr_pdf_reserves_every_page_before_upload(tmp_path: Path) -> None:
    """Mistral bills pages, so a document that does not fit never reaches the wire."""
    pdf = tmp_path / "three.pdf"
    pdf.write_bytes(minimal_pdf(["one", "two", "three"]))

    class FakeParser:
        ref = ModelRef("mistral", "mistral-ocr-latest")

        def __init__(self) -> None:
            self.pdf_calls = 0

        async def parse_image(self, image: object) -> str:
            raise AssertionError("not an image test")

        async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
            self.pdf_calls += 1
            return tuple(OcrPage(index, "page") for index in range(3))

    stop = asyncio.Event()
    budget = CallBudget(limit=2, stop=stop)
    inner = FakeParser()
    parser = budgeted_parser(inner, budget)  # type: ignore[arg-type]

    with pytest.raises(ItemError, match="call budget"):
        await parser.parse_pdf(pdf)

    assert inner.pdf_calls == 0
    assert budget.calls == 0
    assert budget.model_calls == 0 and budget.ocr_pages == 0
    assert budget.describe_usage() == "0 OCR pages processed"
    assert budget.exhausted and stop.is_set()


async def test_ocr_pdf_failure_releases_the_reserved_pages(tmp_path: Path) -> None:
    """A 429-exhausted upload converted no pages, so its reservation must be
    refunded — a failed document may not eat a later document's belt share."""
    pdf = tmp_path / "three.pdf"
    pdf.write_bytes(minimal_pdf(["one", "two", "three"]))

    class FailingParser:
        ref = ModelRef("mistral", "mistral-ocr-latest")

        async def parse_image(self, image: object) -> str:
            raise AssertionError("not an image test")

        async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
            del path
            raise RetryableError("429 rate limited")

    budget = CallBudget(limit=10, stop=asyncio.Event())
    parser = budgeted_parser(FailingParser(), budget)  # type: ignore[arg-type]

    with pytest.raises(RetryableError, match="rate limited"):
        await parser.parse_pdf(pdf)

    assert budget.calls == 0  # the three reserved pages were refunded
    assert budget.ocr_pages == 0
    assert not budget.exhausted  # 3 < 10, and the refund cleared the reservation


async def test_ocr_image_failure_releases_the_reserved_page() -> None:
    from smartpipe.models.base import ImageData

    class FailingParser:
        ref = ModelRef("mistral", "mistral-ocr-latest")

        async def parse_image(self, image: object) -> str:
            del image
            raise RetryableError("429 rate limited")

        async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
            raise AssertionError("not a pdf test")

    budget = CallBudget(limit=5, stop=asyncio.Event())
    parser = budgeted_parser(FailingParser(), budget)  # type: ignore[arg-type]

    with pytest.raises(RetryableError, match="rate limited"):
        await parser.parse_image(ImageData(b"png", "image/png"))

    assert budget.calls == 0 and budget.ocr_pages == 0
    assert not budget.exhausted


async def test_ocr_image_over_belt_reservation_is_never_refunded() -> None:
    """The reserve for parse_image can trip the belt and raise BEFORE it charges;
    that raise happens OUTSIDE the try, so no release runs. A refactor that moved
    the reserve inside the try would double-refund and drive ``calls`` negative,
    handing a later document phantom belt — this pins the "no refund owed" edge."""
    from smartpipe.models.base import ImageData

    class ProbeParser:
        ref = ModelRef("mistral", "mistral-ocr-latest")

        async def parse_image(self, image: object) -> str:
            del image
            raise AssertionError("an over-belt reservation must raise before the wire")

        async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
            raise AssertionError("not a pdf test")

    stop = asyncio.Event()
    budget = CallBudget(limit=1, stop=stop)
    budget.charge()  # the belt is now exactly full: one unit of one
    parser = budgeted_parser(ProbeParser(), budget)  # type: ignore[arg-type]

    with pytest.raises(UnsentError, match="call budget"):
        await parser.parse_image(ImageData(b"png", "image/png"))

    assert budget.calls == 1  # the charge stands — no phantom refund below the floor
    assert budget.ocr_pages == 0
    assert budget.exhausted and stop.is_set()


async def test_exact_fill_ocr_failure_refunds_but_keeps_the_drain_latched() -> None:
    """A reservation that EXACTLY fills the belt latches ``exhausted`` + ``stop`` on
    a SUCCESSFUL reserve; if that upload then fails, the refund lowers ``calls`` back
    but the latch stands — un-setting a drain already in motion is never safe. Guards
    a future "fix" that clears the latch on refund and un-freezes a drained run."""
    from smartpipe.models.base import ImageData

    class FailingParser:
        ref = ModelRef("mistral", "mistral-ocr-latest")

        async def parse_image(self, image: object) -> str:
            del image
            raise RetryableError("429 rate limited")

        async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
            raise AssertionError("not a pdf test")

    stop = asyncio.Event()
    budget = CallBudget(limit=1, stop=stop)  # one page exactly fills it
    parser = budgeted_parser(FailingParser(), budget)  # type: ignore[arg-type]

    with pytest.raises(RetryableError, match="rate limited"):
        await parser.parse_image(ImageData(b"png", "image/png"))

    assert budget.calls == 0  # the failed page was refunded...
    assert budget.ocr_pages == 0
    assert budget.exhausted  # ...but the exhausted latch it tripped stands
    assert stop.is_set()  # and the drain it set is never un-set


async def test_ocr_pdf_charges_its_page_count_once(tmp_path: Path) -> None:
    pdf = tmp_path / "two.pdf"
    pdf.write_bytes(minimal_pdf(["one", "two"]))

    class FakeParser:
        ref = ModelRef("mistral", "mistral-ocr-latest")

        async def parse_image(self, image: object) -> str:
            raise AssertionError("not an image test")

        async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
            return (OcrPage(0, "one"), OcrPage(1, "two"))

    budget = CallBudget(limit=3, stop=asyncio.Event())
    pages = await budgeted_parser(FakeParser(), budget).parse_pdf(pdf)  # type: ignore[arg-type]

    assert len(pages) == 2
    assert budget.calls == 2
    assert budget.model_calls == 0 and budget.ocr_pages == 2
    assert budget.describe_usage() == "2 OCR pages processed"


def test_budget_usage_describes_calls_pages_and_mixed_units() -> None:
    calls = CallBudget(limit=3, stop=asyncio.Event())
    calls.charge()
    assert calls.describe_usage() == "1 call made"

    mixed = CallBudget(limit=3, stop=asyncio.Event())
    mixed.charge()
    mixed.reserve_ocr_pages(2)
    assert mixed.describe_usage() == "3 units used: 1 model call + 2 OCR pages"


def test_limit_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be >= 1"):
        CallBudget(limit=0, stop=None)


async def test_media_embedder_keeps_embed_parts_under_the_belt() -> None:
    """Item 40: wrapping a JOINT embedder must not demote pixels to captions —
    the budget wrapper stays a MediaEmbeddingModel, and charges per call."""
    from smartpipe.models.base import ImageData, ModelRef, supports_media_embedding

    class FakeClip:
        ref = ModelRef("jina", "jina-clip-v2")

        def __init__(self) -> None:
            self.calls: list[list[str | ImageData]] = []

        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("the wrapper routes text through embed_parts")

        async def embed_parts(
            self, parts: Sequence[str | ImageData]
        ) -> tuple[tuple[float, ...], ...]:
            self.calls.append(list(parts))
            return tuple((1.0,) for _ in parts)

    inner = FakeClip()
    budget = CallBudget(limit=2, stop=asyncio.Event())
    model = budgeted_embed(inner, budget)
    await model.embed(["hello"])  # the text side charges too
    probe: object = model  # narrow a view; `model` keeps its EmbeddingModel face
    assert supports_media_embedding(probe)
    await probe.embed_parts([ImageData(b"png", "image/png")])
    assert budget.calls == 2
    assert len(inner.calls) == 2


async def test_remote_stt_charges_the_shared_call_budget() -> None:
    from smartpipe.models.base import AudioData

    class FakeTranscriber:
        ref = ModelRef("openai", "whisper-1")

        def __init__(self) -> None:
            self.calls = 0

        async def transcribe(self, audio: AudioData) -> str:
            self.calls += 1
            return audio.mime

    stop = asyncio.Event()
    budget = CallBudget(limit=1, stop=stop)
    inner = FakeTranscriber()
    transcriber = budgeted_transcriber(inner, budget)

    assert await transcriber.transcribe(AudioData(b"one", "audio/wav")) == "audio/wav"
    with pytest.raises(UnsentError, match="call budget"):
        await transcriber.transcribe(AudioData(b"two", "audio/wav"))

    assert inner.calls == 1
    assert budget.calls == 1 and budget.model_calls == 1
