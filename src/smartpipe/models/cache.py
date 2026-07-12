"""Result caching for model calls (D38/15, KQL ``materialize``; widened by #22).

Iteration stops re-paying unchanged work. Sound because of D36: temperature
is 0.0 everywhere, so identical request → identical reply is the contract —
and embeddings/transcriptions are deterministic per (model, input) too.
Every cache wraps OUTSIDE the call budget and admission — a hit costs
nothing, must not count against ``--max-calls`` (the belt caps spend, not
answers), and never takes the outbound semaphore.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import os
import threading
from contextlib import suppress
from typing import TYPE_CHECKING

from smartpipe.core.errors import ItemError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
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
    "CachingChatModel",
    "CachingDocumentParser",
    "CachingEmbeddingModel",
    "CachingMediaEmbeddingModel",
    "CachingTranscriber",
    "TranscriptBank",
    "cache_key",
    "cached_embed",
    "embed_cache_key",
    "fastembed_version",
    "ocr_cache_key",
    "stt_cache_key",
    "sweep",
]


def cache_key(ref: ModelRef, request: CompletionRequest) -> str:
    """Anything that changes the reply changes the key."""
    payload: dict[str, object] = {
        "provider": ref.provider,
        "model": ref.name,
        "system": request.system,
        "user": request.user,
        "schema": request.json_schema,
        "temperature": request.temperature,
        "presence": request.presence_penalty,
        "frequency": request.frequency_penalty,
        "max_tokens": request.max_tokens,
        "media": [(part.mime, hashlib.sha256(part.data).hexdigest()) for part in request.media],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CachingChatModel:
    """ChatModel-shaped wrapper: hit → stored reply, miss → inner + store."""

    def __init__(self, inner: ChatModel, directory: Path) -> None:
        self.inner = inner
        self.directory = directory
        self.hits = 0
        self.misses = 0
        self._inflight: dict[str, asyncio.Task[str]] = {}

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def complete(self, request: CompletionRequest) -> str:
        key = cache_key(self.inner.ref, request)
        path = self.directory / key[:2] / f"{key}.json"
        stored = _read(path)
        if stored is not None:
            self.hits += 1
            return stored
        shared = self._inflight.get(key)
        if shared is not None:
            reply = await asyncio.shield(shared)
            self.hits += 1
            return reply
        task = asyncio.create_task(self._complete_miss(request, path))
        self._inflight[key] = task
        task.add_done_callback(lambda done: self._finish_inflight(key, done))
        return await asyncio.shield(task)

    async def _complete_miss(self, request: CompletionRequest, path: Path) -> str:
        reply = await self.inner.complete(request)
        self.misses += 1
        _write(path, reply)
        return reply

    def _finish_inflight(self, key: str, task: asyncio.Task[str]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled():
            _ = task.exception()  # retrieve failures even if every waiter was cancelled


def ocr_cache_key(ref: ModelRef, route: str, data: bytes) -> str:
    """The paid OCR conversion's identity (A7): provider+model, the route (image
    vs pdf, so a shared byte-hash never collides across the two paths), and the
    input document bytes. A different OCR model, route, or document is a new key.
    """
    payload: dict[str, object] = {
        "provider": ref.provider,
        "model": ref.name,
        "route": route,
        "bytes": hashlib.sha256(data).hexdigest(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CachingDocumentParser:
    """DocumentParser-shaped wrapper that banks paid conversions across runs (A7).

    A hit returns the stored markdown/pages WITHOUT touching the inner parser, so
    a rerun never re-pays admission or the per-page belt. Wraps OUTERMOST, mirroring
    ``CachingChatModel``; ``ref`` (and, through the ``inner`` walk ``parser_billing``
    follows, ``billing``) passes through so disclosure and accounting still read the
    wrapped wire's identity.
    """

    def __init__(self, inner: DocumentParser, directory: Path) -> None:
        self.inner = inner
        self.directory = directory
        self.hits = 0
        self.misses = 0

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def parse_image(self, image: ImageData) -> str:
        # A7 review: the mime rides the request body (``_data_url``), so it rides the
        # key too — the same bytes under a different format is a different conversion.
        key = ocr_cache_key(self.inner.ref, f"image:{image.mime}", image.data)
        path = self.directory / key[:2] / f"{key}.json"
        stored = _read(path)
        if stored is not None:
            self.hits += 1
            return stored
        markdown = await self.inner.parse_image(image)
        self.misses += 1
        _write(path, markdown)
        return markdown

    async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
        data = await asyncio.to_thread(path.read_bytes)  # the document's bytes ARE the key
        key = ocr_cache_key(self.inner.ref, "pdf", data)
        cache_path = self.directory / key[:2] / f"{key}.json"
        stored = _read_pages(cache_path)
        if stored is not None:
            self.hits += 1
            return stored
        pages = await self.inner.parse_pdf(path)
        self.misses += 1
        _write(cache_path, _dump_pages(pages))
        return pages


# --- embedding cache (#22): per-TEXT keys, so a rerun re-embeds only what changed ---


@functools.cache
def fastembed_version() -> str:
    """The local embedder's installed version, memoized once per process. It
    rides every ``local`` embed key: a fastembed upgrade can change the vectors,
    so banked entries must not survive it. Function-local import — metadata
    scanning is not startup-budget material."""
    from importlib import metadata

    try:
        return metadata.version("fastembed")
    except metadata.PackageNotFoundError:
        return "unknown"


def embed_cache_key(ref: ModelRef, text: str) -> str:
    """One TEXT's banked-vector identity: provider + model + the text itself
    (plus the fastembed version for the on-device wire). Per-text — never
    per-batch — so any batch composition hits the same entries."""
    payload: dict[str, object] = {
        "provider": ref.provider,
        "model": ref.name,
        "text": text,
    }
    if ref.provider == "local":
        payload["fastembed"] = fastembed_version()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _embed_banked(
    ref: ModelRef,
    directory: Path,
    texts: Sequence[str],
    fill: Callable[[list[str]], Awaitable[tuple[tuple[float, ...], ...]]],
) -> tuple[int, int, tuple[tuple[float, ...], ...]]:
    """The five-step recomposition shared by both embed wrappers: keys →
    banked/missing split (first-occurrence dedupe via the ``missing`` dict —
    a naive list would double-pay duplicates or crash the strict zip) → ONE
    ``fill`` call over exactly the missing texts, in first-seen order →
    best-effort write-through (per-vector; one failed write never sinks the
    computed batch, e683b84 posture) → reassembly in input order. Returns
    ``(hits, misses, vectors)`` with hits + misses == len(texts); zero misses
    means ``fill`` is NEVER awaited."""
    keys = [embed_cache_key(ref, text) for text in texts]
    banked: dict[str, tuple[float, ...]] = {}
    missing: dict[str, str] = {}
    for key, text in zip(keys, texts, strict=True):
        if key in banked or key in missing:
            continue
        stored = _read_vector(directory / key[:2] / f"{key}.json")
        if stored is not None:
            banked[key] = stored
        else:
            missing[key] = text
    if missing:
        fresh = await fill(list(missing.values()))
        if len(fresh) != len(missing):
            raise ItemError(f"endpoint returned {len(fresh)} vectors for {len(missing)} texts")
        for key, vector in zip(missing, fresh, strict=True):
            banked[key] = vector
            # JSON floats are repr-shortest, so the round-trip is EXACT
            _write(directory / key[:2] / f"{key}.json", json.dumps(list(vector)))
    misses = sum(1 for key in keys if key in missing)
    return len(keys) - misses, misses, tuple(banked[key] for key in keys)


class CachingEmbeddingModel:
    """EmbeddingModel-shaped wrapper banking PER-TEXT vectors across runs (#22).

    Wraps OUTERMOST (mirrors ``CachingChatModel``): a hit returns the banked
    vector without touching admission or the belt; ``ref`` passes through so
    disclosure reads the wrapped wire's identity.
    """

    def __init__(self, inner: EmbeddingModel, directory: Path) -> None:
        self.inner = inner
        self.directory = directory
        self.hits = 0
        self.misses = 0

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        hits, misses, vectors = await _embed_banked(
            self.inner.ref, self.directory, texts, self.inner.embed
        )
        self.hits += hits
        self.misses += misses
        return vectors


class CachingMediaEmbeddingModel:
    """The marker-preserving twin (#22): ``verbs/common.native_route`` probes
    ``supports_media_embedding`` on the WRAPPED model, so a joint embedder must
    keep its ``embed_parts`` surface through the cache layer. Text batches bank
    per text (misses route through ``embed_parts``, exactly as the admitted
    media wrapper's ``embed`` does); ``embed_parts`` itself is a v1
    pass-through — pixels re-embed every run, uncached."""

    def __init__(self, inner: MediaEmbeddingModel, directory: Path) -> None:
        self.inner = inner
        self.directory = directory
        self.hits = 0
        self.misses = 0

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        hits, misses, vectors = await _embed_banked(
            self.inner.ref, self.directory, texts, self.inner.embed_parts
        )
        self.hits += hits
        self.misses += misses
        return vectors

    async def embed_parts(self, parts: Sequence[str | ImageData]) -> tuple[tuple[float, ...], ...]:
        return await self.inner.embed_parts(parts)


def cached_embed(inner: EmbeddingModel, directory: Path) -> EmbeddingModel:
    """Cache an embedding wire OUTERMOST, splitting on the media capability
    exactly as ``admitted_embed`` does — the wrapper must not strip the
    ``embed_parts`` marker the verbs probe for."""
    from smartpipe.models.base import supports_media_embedding

    if supports_media_embedding(inner):
        return CachingMediaEmbeddingModel(inner, directory)
    return CachingEmbeddingModel(inner, directory)


def _read_vector(path: Path) -> tuple[float, ...] | None:
    """Reload one banked vector, or ``None`` on any malformed entry — a corrupt
    cache file is a miss, never a crash (mirrors ``_read``)."""
    raw = _read(path)
    if raw is None:
        return None
    from smartpipe.core.jsontools import as_items

    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    items = as_items(parsed)
    if items is None:
        return None
    values: list[float] = []
    for entry in items:
        if isinstance(entry, bool) or not isinstance(entry, int | float):
            return None
        values.append(float(entry))
    return tuple(values)


# --- STT caches (#22): the remote wire and the local whisper bank -------------------


def stt_cache_key(provider: str, model: str, mime: str, data: bytes, compute: str = "") -> str:
    """One transcription's identity: the wire (provider/model), the audio's
    mime + bytes, and — for local whisper — the compute type. sha256 over the
    payload bytes: the remote path is capped ~25 MB, and the local path hashes
    in worker threads."""
    payload: dict[str, object] = {
        "provider": provider,
        "model": model,
        "mime": mime,
        "bytes": hashlib.sha256(data).hexdigest(),
        "compute": compute,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CachingTranscriber:
    """Transcriber-shaped wrapper banking remote transcriptions across runs (#22).

    Wraps OUTERMOST: a hit returns the banked text without touching admission
    or the belt; ``ref`` passes through for disclosure."""

    def __init__(self, inner: Transcriber, directory: Path) -> None:
        self.inner = inner
        self.directory = directory
        self.hits = 0
        self.misses = 0

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def transcribe(self, audio: AudioData) -> str:
        key = stt_cache_key(self.inner.ref.provider, self.inner.ref.name, audio.mime, audio.data)
        path = self.directory / key[:2] / f"{key}.json"
        stored = _read(path)
        if stored is not None:
            self.hits += 1
            return stored
        text = await self.inner.transcribe(audio)
        self.misses += 1
        _write(path, text)
        return text


class TranscriptBank:
    """The LOCAL whisper transcript bank (#22): SYNCHRONOUS, because
    ``transcribe_audio`` runs inside ``asyncio.to_thread`` workers — counters
    take a ``threading.Lock``, and the file I/O is the same atomic
    ``_read``/``_write`` pair the async caches use. A hit is looked up BEFORE
    the whisper model loads, so a fully banked corpus never pays the load."""

    _COMPUTE = "int8"  # transcribe_audio's fixed compute type — part of the key

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()

    def lookup(self, size: str, audio: AudioData) -> str | None:
        stored = _read(self._path(size, audio))
        if stored is not None:
            with self._lock:
                self.hits += 1
        return stored

    def store(self, size: str, audio: AudioData, transcript: str) -> None:
        with self._lock:
            self.misses += 1  # counted here: a failed transcription never stores
        _write(self._path(size, audio), transcript)

    def _path(self, size: str, audio: AudioData) -> Path:
        key = stt_cache_key("local", f"whisper-{size}", audio.mime, audio.data, self._COMPUTE)
        return self.directory / key[:2] / f"{key}.json"


def _read(path: Path) -> str | None:
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
        os.utime(path)  # a hit refreshes recency — the LRU truth (D39/02)
    except (OSError, json.JSONDecodeError):
        return None  # missing or corrupt — a miss, never a crash
    from smartpipe.core.jsontools import as_record

    record = as_record(parsed)
    if record is not None:
        reply = record.get("reply")
        if isinstance(reply, str):
            return reply
    return None


def _write(path: Path, reply: str) -> None:
    """Bank a reply best-effort. A cache write is never the user's problem (mirrors
    ``_read``'s OSError swallow): an unwritable dir or a full disk must not sink a run
    that already produced — and, on the paid OCR wire, already PAID for — its result
    (A7 review). On failure the half-written temp is cleaned up, nothing is banked."""
    scratch = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text(json.dumps({"reply": reply}, ensure_ascii=False), encoding="utf-8")
        os.replace(scratch, path)  # atomic on POSIX — never a half-written entry
    except OSError:
        with suppress(OSError):
            scratch.unlink(missing_ok=True)  # don't leave a half-written temp behind


def _dump_pages(pages: tuple[OcrPage, ...]) -> str:
    """Serialize a page tuple deterministically — the content-agnostic ``_write``
    stores the result as its ``reply`` string. ``OcrPage`` carries only index +
    markdown, so this captures the whole value."""
    payload = [{"index": page.index, "markdown": page.markdown} for page in pages]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_pages(path: Path) -> tuple[OcrPage, ...] | None:
    """Reload a page tuple byte-identically, or ``None`` on any malformed entry —
    a corrupt cache file is a miss, never a crash (mirrors ``_read``)."""
    raw = _read(path)
    if raw is None:
        return None
    from smartpipe.core.jsontools import as_items, as_record
    from smartpipe.models.ocr import OcrPage

    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    items = as_items(parsed)
    if items is None:
        return None
    pages: list[OcrPage] = []
    for entry in items:
        record = as_record(entry)
        if record is None:
            return None
        index = record.get("index")
        markdown = record.get("markdown")
        if isinstance(index, bool) or not isinstance(index, int) or not isinstance(markdown, str):
            return None
        pages.append(OcrPage(index=index, markdown=markdown))
    return tuple(pages)


_DAY_SECONDS = 86_400


def sweep(directory: Path, *, ttl_days: int, max_mb: int, now: float) -> tuple[int, int]:
    """Expire entries past the TTL, then LRU-evict (oldest mtime first) until
    under the size cap. Returns (entries removed, bytes removed). Pure walk —
    the caller owns the once-a-day gating and error tolerance."""
    entries: list[tuple[float, int, Path]] = []
    for path in directory.rglob("*.json"):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((stat.st_mtime, stat.st_size, path))
    removed = 0
    freed = 0
    survivors: list[tuple[float, int, Path]] = []
    horizon = now - ttl_days * _DAY_SECONDS
    for mtime, size, path in entries:
        if mtime < horizon:
            try:
                path.unlink()
                removed += 1
                freed += size
            except OSError:
                survivors.append((mtime, size, path))
        else:
            survivors.append((mtime, size, path))
    survivors.sort()  # oldest first
    total = sum(size for _mtime, size, _path in survivors)
    cap = max_mb * 1_048_576
    for _mtime, size, path in survivors:
        if total <= cap:
            break
        try:
            path.unlink()
            removed += 1
            freed += size
            total -= size
        except OSError:
            continue
    return removed, freed
