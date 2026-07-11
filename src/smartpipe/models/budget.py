"""``--max-calls`` (D18): a hard ceiling on outbound billable units.

The budget wraps models at the composition root, so verbs stay ignorant. A
regular chat, embedding, or remote-STT request is one unit; dedicated OCR is
one unit per page. A repair re-ask charges again, while wire retries of the
same request do not because the wrapper sits outside ``with_retries``.

Two exhaustion behaviors, pinned in ux.md: with a ``stop`` event (the per-item
verbs' drain machinery) the limit call still runs, intake stops, and any racing
in-flight worker skips its item; without one (whole-set verbs — a partial
collection is nothing usable) exhaustion is fatal with the fix screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import SetupFault, UnsentError
from smartpipe.models.base import preflight_chat

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Sequence
    from pathlib import Path

    from smartpipe.models.base import (
        AudioData,
        ChatModel,
        CompletionRequest,
        EmbeddingModel,
        ImageData,
        MediaEmbeddingModel,
        ModelRef,
    )
    from smartpipe.models.ocr import DocumentParser, OcrPage
    from smartpipe.models.stt import Transcriber

__all__ = [
    "CallBudget",
    "budgeted_chat",
    "budgeted_embed",
    "budgeted_parser",
    "budgeted_transcriber",
]


@dataclass(slots=True)
class CallBudget:
    """Mutable by design (documented Spinner-style exception): the counter is
    per-run state shared by every model the container hands out."""

    limit: int
    stop: asyncio.Event | None  # per-item verbs trip intake; None = whole-set (fatal)
    calls: int = 0
    exhausted: bool = False
    model_calls: int = 0
    ocr_pages: int = 0
    _saw_ocr: bool = False

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError(f"--max-calls must be >= 1, got {self.limit}")

    def _reserve(self, units: int) -> None:
        """Reserve billable units atomically before an outbound request.

        Ordinary model envelopes reserve one unit. Dedicated OCR reserves the
        document's full page count so an over-belt PDF never uploads partially.
        """
        if units < 1:
            raise ValueError(f"budget units must be >= 1, got {units}")
        if self.calls + units > self.limit:
            self.exhausted = True
            if self.stop is None:
                raise SetupFault(
                    f"error: call budget reached mid-collection (--max-calls {self.limit})\n"
                    "  This verb needs every item before it can produce a result — a cap\n"
                    "  that stops early leaves nothing usable.\n"
                    "  Raise --max-calls, shrink the input, or drop the cap."
                )
            self.stop.set()
            raise UnsentError(f"call budget reached (--max-calls {self.limit})")
        self.calls += units
        if self.calls >= self.limit:
            self.exhausted = True
            if self.stop is not None:
                self.stop.set()  # the limit call runs; nothing new starts

    def charge(self) -> None:
        self._reserve(1)
        self.model_calls += 1

    def reserve_ocr_pages(self, pages: int) -> None:
        self._saw_ocr = True
        self._reserve(pages)
        self.ocr_pages += pages

    def release_ocr_pages(self, pages: int) -> None:
        """Refund a page reservation whose OCR upload failed before it converted
        anything (a 429 ladder exhausted, the breaker open). Reservation charges
        the belt the instant a document's page count is known — before the wire
        call — so an over-belt PDF never uploads partially. When that call then
        fails, the pages were never processed, so counting them would let a dead
        document eat a later one's belt share. Only ever called for a reservation
        that SUCCEEDED: one that itself hit the belt raised before charging, so
        there is nothing to refund (the ``exhausted`` latch, once tripped, stands
        — un-setting a drain already in motion is never safe)."""
        if pages < 1:
            raise ValueError(f"budget units must be >= 1, got {pages}")
        self.calls -= pages
        self.ocr_pages -= pages

    def describe_usage(self) -> str:
        if not self._saw_ocr:
            return _count(self.model_calls, "call", suffix="made")
        if self.model_calls == 0:
            return _count(self.ocr_pages, "OCR page", suffix="processed")
        return (
            f"{_count(self.calls, 'unit', suffix='used')}: "
            f"{_count(self.model_calls, 'model call')} + "
            f"{_count(self.ocr_pages, 'OCR page')}"
        )


@dataclass(frozen=True, slots=True)
class _BudgetedChat:
    inner: ChatModel
    budget: CallBudget

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    def preflight(self, request: CompletionRequest) -> None:
        preflight_chat(self.inner, request)

    async def complete(self, request: CompletionRequest) -> str:
        self.preflight(request)
        self.budget.charge()
        return await self.inner.complete(request)


@dataclass(frozen=True, slots=True)
class _BudgetedEmbed:
    inner: EmbeddingModel
    budget: CallBudget

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.budget.charge()
        return await self.inner.embed(texts)


@dataclass(frozen=True, slots=True)
class _BudgetedMediaEmbed:
    """The budget belt for a JOINT text+image embedder: the wrapper must keep
    ``embed_parts`` visible, or the belt would silently demote pixels to the
    caption pivot (capability follows the wrapper, item 40)."""

    inner: MediaEmbeddingModel
    budget: CallBudget

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.budget.charge()
        return await self.inner.embed_parts(list(texts))

    async def embed_parts(self, parts: Sequence[str | ImageData]) -> tuple[tuple[float, ...], ...]:
        self.budget.charge()
        return await self.inner.embed_parts(parts)


@dataclass(frozen=True, slots=True)
class _BudgetedParser:
    """Page-denominated belt for the dedicated OCR wire (item 48).

    Only the Mistral parser needs it. The vision rung's chat model is already
    wrapped per request, so wrapping that parser too would double-charge.
    """

    inner: DocumentParser
    budget: CallBudget

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def parse_image(self, image: ImageData) -> str:
        self.budget.reserve_ocr_pages(1)
        try:
            return await self.inner.parse_image(image)
        except BaseException:  # the upload never converted the page — refund it
            self.budget.release_ocr_pages(1)
            raise

    async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
        import asyncio

        from smartpipe.models.ocr import pdf_page_count

        pages = await asyncio.to_thread(pdf_page_count, path)
        self.budget.reserve_ocr_pages(pages)  # may raise before charging: no refund owed
        try:
            return await self.inner.parse_pdf(path)
        except BaseException:  # the upload failed — refund every reserved page
            self.budget.release_ocr_pages(pages)
            raise


@dataclass(frozen=True, slots=True)
class _BudgetedTranscriber:
    inner: Transcriber
    budget: CallBudget

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def transcribe(self, audio: AudioData) -> str:
        self.budget.charge()
        return await self.inner.transcribe(audio)


def budgeted_chat(inner: ChatModel, budget: CallBudget) -> ChatModel:
    return _BudgetedChat(inner, budget)


def budgeted_parser(inner: DocumentParser, budget: CallBudget) -> DocumentParser:
    return _BudgetedParser(inner, budget)


def budgeted_embed(inner: EmbeddingModel, budget: CallBudget) -> EmbeddingModel:
    from smartpipe.models.base import supports_media_embedding

    if supports_media_embedding(inner):
        return _BudgetedMediaEmbed(inner, budget)
    return _BudgetedEmbed(inner, budget)


def budgeted_transcriber(inner: Transcriber, budget: CallBudget) -> Transcriber:
    return _BudgetedTranscriber(inner, budget)


def _count(value: int, noun: str, *, suffix: str = "") -> str:
    plural = "" if value == 1 else "s"
    tail = f" {suffix}" if suffix else ""
    return f"{value} {noun}{plural}{tail}"
