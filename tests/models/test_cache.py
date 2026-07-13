"""Result caching (D38/15): key sensitivity, hit short-circuits, honest misses."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smartpipe.models.base import CompletionRequest, ImageData, ModelRef
from smartpipe.models.cache import (
    CachingChatModel,
    CachingDocumentParser,
    cache_key,
    ocr_cache_key,
)
from smartpipe.models.ocr import OcrPage
from tests.helpers.pdf import minimal_pdf

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

REF = ModelRef("openai", "gpt-5.4-mini")


def _request(**kwargs: object) -> CompletionRequest:
    return CompletionRequest(system="s", user="u", **kwargs)  # type: ignore[arg-type]


def test_key_is_stable_and_sensitive() -> None:
    base = cache_key(REF, _request())
    assert base == cache_key(REF, _request())  # stable
    assert base != cache_key(ModelRef("openai", "other"), _request())  # model flips it
    different = CompletionRequest(system="s", user="different")
    assert base != cache_key(REF, different)
    assert base != cache_key(REF, _request(max_tokens=64))
    assert base != cache_key(REF, _request(media=(ImageData(b"px", "image/png"),)))


class CountingModel:
    def __init__(self) -> None:
        self.ref = REF
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> str:
        self.calls += 1
        return f"reply-{self.calls}"


async def test_hit_short_circuits_the_inner_model(tmp_path: Path) -> None:
    inner = CountingModel()
    cached = CachingChatModel(inner, tmp_path)
    first = await cached.complete(_request())
    second = await cached.complete(_request())
    assert (first, second) == ("reply-1", "reply-1")  # the stored reply, verbatim
    assert inner.calls == 1  # the second call never reached the wire
    assert (cached.hits, cached.misses) == (1, 1)


async def test_hits_do_not_consume_the_call_budget(tmp_path: Path) -> None:
    from smartpipe.models.budget import CallBudget, budgeted_chat

    inner = CountingModel()
    budget = CallBudget(limit=1, stop=None)
    cached = CachingChatModel(budgeted_chat(inner, budget), tmp_path)
    await cached.complete(_request())  # spends the single budgeted call
    reply = await cached.complete(_request())  # a hit — must NOT trip the budget
    assert reply == "reply-1"
    assert inner.calls == 1


async def test_concurrent_identical_misses_share_one_inner_result(tmp_path: Path) -> None:
    import asyncio

    class HeldModel:
        ref = REF

        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def complete(self, request: CompletionRequest) -> str:
            del request
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return f"reply-{self.calls}"

    inner = HeldModel()
    cached = CachingChatModel(inner, tmp_path)
    requests = [asyncio.create_task(cached.complete(_request())) for _ in range(8)]
    await inner.started.wait()
    inner.release.set()
    assert await asyncio.gather(*requests) == ["reply-1"] * 8
    assert inner.calls == 1
    assert (cached.hits, cached.misses) == (7, 1)


async def test_cancelling_one_waiter_does_not_cancel_the_shared_fill(tmp_path: Path) -> None:
    import asyncio

    class HeldModel:
        ref = REF

        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def complete(self, request: CompletionRequest) -> str:
            del request
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return "shared"

    inner = HeldModel()
    cached = CachingChatModel(inner, tmp_path)
    cancelled = asyncio.create_task(cached.complete(_request()))
    await inner.started.wait()
    survivor = asyncio.create_task(cached.complete(_request()))
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    inner.release.set()
    assert await survivor == "shared"
    assert await cached.complete(_request()) == "shared"
    assert inner.calls == 1


async def test_corrupt_entry_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    inner = CountingModel()
    cached = CachingChatModel(inner, tmp_path)
    key = cache_key(REF, _request())
    target = tmp_path / key[:2] / f"{key}.json"
    target.parent.mkdir(parents=True)
    target.write_text("not json{", encoding="utf-8")
    reply = await cached.complete(_request())
    assert reply == "reply-1"  # re-fetched and re-stored
    assert await cached.complete(_request()) == "reply-1"  # now a clean hit


# --- sweep: TTL + LRU (D39/02) -----------------------------------------------------


def _entry(tmp_path: Path, name: str, *, age_days: float, size: int) -> Path:
    import os
    import time

    path = tmp_path / name[:2] / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"reply": "' + "x" * size + '"}', encoding="utf-8")
    stamp = time.time() - age_days * 86_400
    os.utime(path, (stamp, stamp))
    return path


def test_sweep_expires_ttl_then_lru_evicts_to_cap(tmp_path: Path) -> None:
    import time

    from smartpipe.models.cache import sweep

    ancient = _entry(tmp_path, "aa" * 32, age_days=40, size=10)
    old_big = _entry(tmp_path, "bb" * 32, age_days=10, size=2_000_000)
    fresh = _entry(tmp_path, "cc" * 32, age_days=0.1, size=10)
    removed, freed = sweep(tmp_path, ttl_days=30, max_mb=1, now=time.time())
    assert removed == 2 and freed > 2_000_000
    assert not ancient.exists()  # past the TTL
    assert not old_big.exists()  # LRU-evicted to get under the cap
    assert fresh.exists()


async def test_hits_refresh_recency(tmp_path: Path) -> None:
    import os

    inner = CountingModel()
    cached = CachingChatModel(inner, tmp_path)
    await cached.complete(_request())
    key = cache_key(REF, _request())
    path = tmp_path / key[:2] / f"{key}.json"
    stale = path.stat().st_mtime - 86_400
    os.utime(path, (stale, stale))
    await cached.complete(_request())  # the hit
    assert path.stat().st_mtime > stale + 3600  # touched — LRU sees recent use


# --- OCR document cache (A7): bank paid Mistral conversions across runs -------------

OCR_REF = ModelRef("mistral", "mistral-ocr-latest")


class CountingParser:
    """A DocumentParser fake that counts real (paid) parses. Returns a page tuple
    with a non-ASCII page to prove the round-trip is byte-identical."""

    def __init__(self, ref: ModelRef = OCR_REF) -> None:
        self._ref = ref
        self.image_calls = 0
        self.pdf_calls = 0

    @property
    def ref(self) -> ModelRef:
        return self._ref

    async def parse_image(self, image: ImageData) -> str:
        del image
        self.image_calls += 1
        return f"markdown-{self.image_calls}"

    async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
        del path
        self.pdf_calls += 1
        return (OcrPage(0, f"page-one-{self.pdf_calls}"), OcrPage(1, "café ☕ 世界\n\n# H"))


def test_ocr_key_distinguishes_route_ref_and_bytes() -> None:
    base = ocr_cache_key(OCR_REF, "pdf", b"bytes")
    assert base == ocr_cache_key(OCR_REF, "pdf", b"bytes")  # stable
    assert base != ocr_cache_key(OCR_REF, "image", b"bytes")  # route tag, no cross-collision
    assert base != ocr_cache_key(ModelRef("mistral", "other"), "pdf", b"bytes")  # model flips it
    assert base != ocr_cache_key(OCR_REF, "pdf", b"other")  # the document bytes


async def test_second_pdf_parse_is_served_from_cache(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(minimal_pdf(["one", "two"]))
    inner = CountingParser()
    cached = CachingDocumentParser(inner, tmp_path / "cache")
    first = await cached.parse_pdf(pdf)
    second = await cached.parse_pdf(pdf)
    assert first == second  # byte-identical page tuple, reloaded from disk
    assert first == (OcrPage(0, "page-one-1"), OcrPage(1, "café ☕ 世界\n\n# H"))
    assert inner.pdf_calls == 1  # the wire was paid exactly once
    assert (cached.hits, cached.misses) == (1, 1)


async def test_second_pdf_parse_meters_zero_paid_conversions(tmp_path: Path) -> None:
    """The headline: a rerun of the same document never reaches the page belt or
    admission — the inner wire is untouched, so ZERO further conversions are paid."""
    from smartpipe.models.admission import OutboundCallPolicy, admitted_parser
    from smartpipe.models.budget import CallBudget, budgeted_parser

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(minimal_pdf(["one", "two"]))
    inner = CountingParser()
    budget = CallBudget(limit=10, stop=None)
    wired = CachingDocumentParser(
        admitted_parser(budgeted_parser(inner, budget), OutboundCallPolicy(concurrency=2)),
        tmp_path / "cache",
    )
    await wired.parse_pdf(pdf)
    assert (inner.pdf_calls, budget.ocr_pages) == (1, 2)  # both pages charged, once
    await wired.parse_pdf(pdf)  # a rerun
    assert inner.pdf_calls == 1  # the wire was never touched again
    assert budget.ocr_pages == 2  # the belt never metered the banked pages


async def test_image_parse_is_cached(tmp_path: Path) -> None:
    inner = CountingParser()
    cached = CachingDocumentParser(inner, tmp_path)
    image = ImageData(b"\x89PNGscan", "image/png")
    first = await cached.parse_image(image)
    second = await cached.parse_image(image)
    assert (first, second) == ("markdown-1", "markdown-1")  # the stored markdown, verbatim
    assert inner.image_calls == 1
    assert (cached.hits, cached.misses) == (1, 1)


async def test_a_different_ocr_model_is_a_miss(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(minimal_pdf(["one"]))
    latest = CachingDocumentParser(
        CountingParser(ModelRef("mistral", "mistral-ocr-latest")), tmp_path
    )
    other = CachingDocumentParser(CountingParser(ModelRef("mistral", "mistral-ocr-2099")), tmp_path)
    await latest.parse_pdf(pdf)
    await other.parse_pdf(pdf)  # same bytes, different OCR model → not a hit
    assert (latest.hits, latest.misses) == (0, 1)
    assert (other.hits, other.misses) == (0, 1)


async def test_image_and_pdf_of_the_same_bytes_never_collide(tmp_path: Path) -> None:
    payload = minimal_pdf(["one"])
    (tmp_path / "doc.pdf").write_bytes(payload)
    inner = CountingParser()
    cached = CachingDocumentParser(inner, tmp_path / "cache")
    await cached.parse_image(ImageData(payload, "application/pdf"))
    await cached.parse_pdf(tmp_path / "doc.pdf")  # same bytes, other route → separate entry
    assert (inner.image_calls, inner.pdf_calls) == (1, 1)  # both wires ran; no false hit


async def test_corrupt_page_entry_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(minimal_pdf(["one"]))
    inner = CountingParser()
    directory = tmp_path / "cache"
    cached = CachingDocumentParser(inner, directory)
    key = ocr_cache_key(inner.ref, "pdf", pdf.read_bytes())
    target = directory / key[:2] / f"{key}.json"
    target.parent.mkdir(parents=True)
    # a valid cache envelope whose reply is not a page list — junk payload
    target.write_text('{"reply": "not-a-page-list"}', encoding="utf-8")
    pages = await cached.parse_pdf(pdf)  # re-parses rather than crashing
    assert pages == (OcrPage(0, "page-one-1"), OcrPage(1, "café ☕ 世界\n\n# H"))
    assert await cached.parse_pdf(pdf) == pages  # now a clean hit


async def test_ocr_cache_ref_passes_through(tmp_path: Path) -> None:
    cached = CachingDocumentParser(CountingParser(), tmp_path)
    assert str(cached.ref) == "mistral/mistral-ocr-latest"  # disclosure reads the wire's identity


async def test_image_cache_keys_on_the_mime_not_just_bytes(tmp_path: Path) -> None:
    """A7 review: the OCR request body embeds the image mime (``_data_url``), so the
    same bytes under a different mime is a different conversion — it must MISS, never
    hand back the other format's banked reply."""
    inner = CountingParser()
    cached = CachingDocumentParser(inner, tmp_path)
    png = await cached.parse_image(ImageData(b"samebytes", "image/png"))
    jpeg = await cached.parse_image(ImageData(b"samebytes", "image/jpeg"))
    assert inner.image_calls == 2  # mime flips the key — the wire ran once per format
    assert png != jpeg  # each format kept its own paid reply; no cross-serving


async def test_a_cache_write_failure_never_sinks_a_paid_conversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A7 review: the OCR pages are already PAID for by the time ``_write`` runs. A
    disk-full / unwritable-dir ``OSError`` while banking them must not propagate and
    discard the conversion — the cache is never the user's problem (mirrors ``_read``)."""
    import smartpipe.models.cache as cache_mod

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cache_mod.os, "replace", _boom)
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(minimal_pdf(["one"]))
    inner = CountingParser()
    cached = CachingDocumentParser(inner, tmp_path / "cache")
    pages = await cached.parse_pdf(pdf)  # the write fails, but the paid pages come back
    assert pages == (OcrPage(0, "page-one-1"), OcrPage(1, "café ☕ 世界\n\n# H"))
    assert (cached.hits, cached.misses) == (0, 1)
    await cached.parse_pdf(pdf)  # nothing banked → the rerun re-parses, no false hit
    assert inner.pdf_calls == 2


# --- embedding cache (#22): per-TEXT keys, recomposition, exact floats --------------

EMBED_REF = ModelRef("openai", "text-embedding-3-small")


def _gnarly(seed: int) -> tuple[float, ...]:
    """Repr-shortest stress floats: the round-trip must be EXACT, no tolerance."""
    return (0.1 + seed, 1 / 3, 1e-300, 6.02214076e23, -0.0)


class CountingEmbedder:
    """Deterministic per-text vectors; records every wire batch."""

    def __init__(self, ref: ModelRef = EMBED_REF) -> None:
        self._ref = ref
        self.batches: list[list[str]] = []

    @property
    def ref(self) -> ModelRef:
        return self._ref

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.batches.append(list(texts))
        return tuple(_gnarly(len(text)) for text in texts)


def test_embed_key_is_stable_and_sensitive() -> None:
    from smartpipe.models.cache import embed_cache_key

    base = embed_cache_key(EMBED_REF, "hello")
    assert base == embed_cache_key(EMBED_REF, "hello")  # stable
    assert base != embed_cache_key(ModelRef("openai", "other"), "hello")  # model flips it
    assert base != embed_cache_key(ModelRef("ollama", "text-embedding-3-small"), "hello")
    assert base != embed_cache_key(EMBED_REF, "other text")  # the text itself


def test_embed_key_local_provider_rides_the_fastembed_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fastembed upgrade can change the local vectors — banked entries must
    not survive it. Non-local providers never consult the version."""
    import importlib.metadata

    from smartpipe.models.cache import embed_cache_key, fastembed_version

    local = ModelRef("local", "nomic-embed-text-v1.5")

    def _version_one(_name: str) -> str:
        return "1.0.0"

    def _version_two(_name: str) -> str:
        return "2.0.0"

    monkeypatch.setattr(importlib.metadata, "version", _version_one)
    fastembed_version.cache_clear()
    first = embed_cache_key(local, "hello")
    remote_first = embed_cache_key(EMBED_REF, "hello")
    monkeypatch.setattr(importlib.metadata, "version", _version_two)
    fastembed_version.cache_clear()
    assert embed_cache_key(local, "hello") != first  # the version flips local keys
    assert embed_cache_key(EMBED_REF, "hello") == remote_first  # remote keys ignore it
    fastembed_version.cache_clear()  # drop the fake from the memo


async def test_embed_partial_hit_recomposes_input_order(tmp_path: Path) -> None:
    from smartpipe.models.cache import CachingEmbeddingModel

    primer = CountingEmbedder()
    await CachingEmbeddingModel(primer, tmp_path).embed(["A9", "C4321"])  # bank two texts

    inner = CountingEmbedder()
    cached = CachingEmbeddingModel(inner, tmp_path)
    vectors = await cached.embed(["A9", "B88", "C4321", "D666666"])
    assert inner.batches == [["B88", "D666666"]]  # the wire saw EXACTLY the misses, in order
    assert vectors == (_gnarly(2), _gnarly(3), _gnarly(5), _gnarly(7))  # input order restored
    assert (cached.hits, cached.misses) == (2, 2)  # hits + misses == len(texts)


async def test_embed_duplicates_in_one_call_pay_once(tmp_path: Path) -> None:
    """The strict-zip guard: a naive miss LIST would double-pay the duplicate
    (or crash the strict zip); the first-occurrence dict pays once."""
    from smartpipe.models.cache import CachingEmbeddingModel

    inner = CountingEmbedder()
    cached = CachingEmbeddingModel(inner, tmp_path)
    vectors = await cached.embed(["dup", "dup", "solo1"])
    assert inner.batches == [["dup", "solo1"]]  # deduped BEFORE the wire
    assert vectors[0] == vectors[1] == _gnarly(3)
    assert (cached.hits, cached.misses) == (0, 3)  # both dup rows count as misses


async def test_embed_zero_misses_never_wakes_the_inner_model(tmp_path: Path) -> None:
    from smartpipe.models.cache import CachingEmbeddingModel

    primer = CountingEmbedder()
    await CachingEmbeddingModel(primer, tmp_path).embed(["A9", "B88"])
    inner = CountingEmbedder()
    cached = CachingEmbeddingModel(inner, tmp_path)
    vectors = await cached.embed(["A9", "B88"])
    assert inner.batches == []  # zero misses: the inner model was NEVER called
    assert vectors == (_gnarly(2), _gnarly(3))
    assert (cached.hits, cached.misses) == (2, 0)


async def test_embed_float_round_trip_is_exact(tmp_path: Path) -> None:
    """JSON floats serialize repr-shortest, so the banked vector reloads EXACTLY
    equal — the tests assert equality, never tolerance."""
    from smartpipe.models.cache import CachingEmbeddingModel

    await CachingEmbeddingModel(CountingEmbedder(), tmp_path).embed(["A9"])
    reloaded = await CachingEmbeddingModel(CountingEmbedder(), tmp_path).embed(["A9"])
    assert reloaded == (_gnarly(2),)


async def test_embed_cache_write_failure_never_sinks_a_computed_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """e683b84 posture: the vectors are already computed (and, on a paid wire,
    already PAID for) when banking fails — the batch must come back whole."""
    import smartpipe.models.cache as cache_mod
    from smartpipe.models.cache import CachingEmbeddingModel

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cache_mod.os, "replace", _boom)
    inner = CountingEmbedder()
    cached = CachingEmbeddingModel(inner, tmp_path)
    assert await cached.embed(["A9", "B88"]) == (_gnarly(2), _gnarly(3))
    assert (cached.hits, cached.misses) == (0, 2)
    monkeypatch.undo()
    rerun = CountingEmbedder()
    await CachingEmbeddingModel(rerun, tmp_path).embed(["A9"])
    assert rerun.batches == [["A9"]]  # nothing was banked — no false hit


class JointEmbedder:
    """A media-capable fake: embed_parts is the only wire (like the admitted
    media wrapper, whose embed() routes through embed_parts)."""

    def __init__(self) -> None:
        self._ref = ModelRef("jina", "jina-clip-v2")
        self.part_batches: list[list[object]] = []

    @property
    def ref(self) -> ModelRef:
        return self._ref

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return await self.embed_parts(list(texts))

    async def embed_parts(self, parts: Sequence[str | ImageData]) -> tuple[tuple[float, ...], ...]:
        self.part_batches.append(list(parts))
        return tuple(_gnarly(len(part) if isinstance(part, str) else 1) for part in parts)


async def test_cached_embed_preserves_the_media_capability_marker(tmp_path: Path) -> None:
    """verbs/common.native_route probes supports_media_embedding on the WRAPPED
    model — a cache layer that strips embed_parts would silently regress every
    image-native corpus to the caption pivot."""
    from smartpipe.models.base import supports_media_embedding
    from smartpipe.models.cache import (
        CachingEmbeddingModel,
        CachingMediaEmbeddingModel,
        cached_embed,
    )

    joint = cached_embed(JointEmbedder(), tmp_path)
    assert isinstance(joint, CachingMediaEmbeddingModel)
    assert supports_media_embedding(joint)
    plain = cached_embed(CountingEmbedder(), tmp_path)
    assert isinstance(plain, CachingEmbeddingModel)
    assert not supports_media_embedding(plain)


async def test_media_embed_parts_pass_through_uncached(tmp_path: Path) -> None:
    from smartpipe.models.cache import CachingMediaEmbeddingModel

    inner = JointEmbedder()
    cached = CachingMediaEmbeddingModel(inner, tmp_path)
    image = ImageData(b"px", "image/png")
    first = await cached.embed_parts([image])
    second = await cached.embed_parts([image])  # v1: pixels re-embed every run
    assert first == second
    assert len(inner.part_batches) == 2  # no banking on the parts path
    assert (cached.hits, cached.misses) == (0, 0)  # and no counter noise
    assert list(tmp_path.rglob("*.json")) == []  # nothing written


async def test_media_embed_texts_bank_per_text_through_embed_parts(tmp_path: Path) -> None:
    from smartpipe.models.cache import CachingMediaEmbeddingModel

    inner = JointEmbedder()
    cached = CachingMediaEmbeddingModel(inner, tmp_path)
    await cached.embed(["A9", "B88"])
    again = CachingMediaEmbeddingModel(inner, tmp_path)
    vectors = await again.embed(["A9", "B88"])
    assert vectors == (_gnarly(2), _gnarly(3))
    assert len(inner.part_batches) == 1  # the rerun was served from the bank
    assert (again.hits, again.misses) == (2, 0)


# --- STT caches (#22): the remote wire and the local whisper bank -------------------


def test_stt_key_is_stable_and_sensitive() -> None:
    from smartpipe.models.cache import stt_cache_key

    base = stt_cache_key("openai", "whisper-1", "audio/wav", b"bytes")
    assert base == stt_cache_key("openai", "whisper-1", "audio/wav", b"bytes")  # stable
    assert base != stt_cache_key("openai", "gpt-4o-transcribe", "audio/wav", b"bytes")
    assert base != stt_cache_key("local", "whisper-1", "audio/wav", b"bytes")
    assert base != stt_cache_key("openai", "whisper-1", "audio/mpeg", b"bytes")  # the mime
    assert base != stt_cache_key("openai", "whisper-1", "audio/wav", b"other")  # the bytes
    assert base != stt_cache_key("openai", "whisper-1", "audio/wav", b"bytes", compute="int8")


class CountingTranscriber:
    def __init__(self) -> None:
        self._ref = ModelRef("openai", "whisper-1")
        self.calls = 0

    @property
    def ref(self) -> ModelRef:
        return self._ref

    async def transcribe(self, audio: object) -> str:
        del audio
        self.calls += 1
        return f"transcript-{self.calls}"


async def test_transcriber_hit_serves_the_banked_text(tmp_path: Path) -> None:
    from smartpipe.models.base import AudioData
    from smartpipe.models.cache import CachingTranscriber

    inner = CountingTranscriber()
    cached = CachingTranscriber(inner, tmp_path)
    clip = AudioData(b"waveform", "audio/wav")
    first = await cached.transcribe(clip)
    second = await cached.transcribe(clip)
    assert (first, second) == ("transcript-1", "transcript-1")  # verbatim
    assert inner.calls == 1  # the wire was paid exactly once
    assert (cached.hits, cached.misses) == (1, 1)
    # a different mime over the same bytes is a different conversion — a MISS
    await cached.transcribe(AudioData(b"waveform", "audio/mpeg"))
    assert inner.calls == 2


async def test_transcriber_ref_passes_through(tmp_path: Path) -> None:
    from smartpipe.models.cache import CachingTranscriber

    cached = CachingTranscriber(CountingTranscriber(), tmp_path)
    assert str(cached.ref) == "openai/whisper-1"  # disclosure reads the wire's identity


# --- the local whisper TranscriptBank (#22): sync, thread-counted -------------------


def test_transcript_bank_round_trip_and_counters(tmp_path: Path) -> None:
    from smartpipe.models.base import AudioData
    from smartpipe.models.cache import TranscriptBank

    bank = TranscriptBank(tmp_path)
    clip = AudioData(b"waveform", "audio/wav")
    assert bank.lookup("tiny", clip) is None  # cold: a miss is None, no crash
    bank.store("tiny", clip, "the spoken words")
    assert bank.lookup("tiny", clip) == "the spoken words"
    assert bank.lookup("small", clip) is None  # the whisper size flips the key
    assert bank.lookup("tiny", AudioData(b"other", "audio/wav")) is None  # the bytes too
    assert (bank.hits, bank.misses) == (1, 1)  # one banked store, one served hit
