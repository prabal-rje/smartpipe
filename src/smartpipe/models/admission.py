"""Run-scoped admission for real model calls.

The composition root shares one boundary across every remote role.  The
boundary owns API-call concurrency and the retryable-call circuit breaker;
the adapters still own bounded wire retries, and the budget stays immediately
inside admission so a queued call cannot reserve spend before it may run.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, TypeGuard, TypeVar, runtime_checkable

from smartpipe.core.errors import (
    CircuitOpenTransport,
    RetryableError,
    UnsentError,
)
from smartpipe.engine.runner import DEFAULT_BREAKER_LIMIT
from smartpipe.models.base import preflight_chat

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
    "AdmittedChatModel",
    "AdmittedDocumentParser",
    "AdmittedEmbeddingModel",
    "AdmittedMediaEmbeddingModel",
    "AdmittedTranscriber",
    "DeferredChatModel",
    "OutboundCallPolicy",
    "admitted_chat",
    "admitted_embed",
    "admitted_parser",
    "admitted_transcriber",
    "supports_deferred_chat",
]

T = TypeVar("T")

# The abuse ceiling for a server-supplied Retry-After, mirroring retry.py's
# ``_HINT_CEILING_SECONDS``: past 60 s a "hint" stops being a hint and starts being
# a request to hang the run, so a hostile ask is clamped before it paces a ref.
_RETRY_AFTER_CEILING_SECONDS = 60.0


@runtime_checkable
class DeferredChatModel(Protocol):
    """Chat admission with a lazy request factory for coalesced flights."""

    @property
    def ref(self) -> ModelRef: ...

    async def complete(self, request: CompletionRequest) -> str: ...

    async def complete_deferred(
        self, request: Callable[[], CompletionRequest | None]
    ) -> str | None: ...


@dataclass(frozen=True, slots=True)
class _OpenCircuit:
    trip_id: int
    message: str


@dataclass(slots=True)
class OutboundCallPolicy:
    """One invocation's concurrency gate and actual-call breaker.

    Breaker state is keyed by full model ref, so a dead primary does not poison
    its fallback.  An exhausted bounded retry ladder (429, 5xx, timeout) is one
    failed *actual call*, even when a coalescer has K item waiters behind it.
    """

    concurrency: int = 4
    breaker_limit: int = DEFAULT_BREAKER_LIMIT
    # Injected effects so tests drive the cooldown with a fake clock (like
    # with_retries' sleep/rand and the breaker's cooldown); production reads the
    # monotonic clock, since a Retry-After is a RELATIVE "wait N seconds from now".
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _configured: bool = False
    _started: bool = False
    _semaphore: asyncio.Semaphore = field(init=False)
    _retryable_streaks: dict[str, int] = field(default_factory=dict[str, int])
    _retryable_series: dict[str, int] = field(default_factory=dict[str, int])
    _open: dict[str, _OpenCircuit] = field(default_factory=dict[str, _OpenCircuit])
    # A5.2: per-ref "not-before" floor (monotonic seconds). A 429 carrying a
    # Retry-After records it here; a later admission of that ref waits it out.
    _not_before: dict[str, float] = field(default_factory=dict[str, float])
    _series_serial: int = 0
    _call_serial: int = 0

    def __post_init__(self) -> None:
        self._validate(self.concurrency, self.breaker_limit)
        self._semaphore = asyncio.Semaphore(self.concurrency)

    def configure(self, *, concurrency: int, breaker_limit: int) -> None:
        """Resolve startup configuration once without replacing a live gate."""
        self._validate(concurrency, breaker_limit)
        if self._configured:
            if (concurrency, breaker_limit) == (self.concurrency, self.breaker_limit):
                return
            raise RuntimeError("outbound call policy is already configured")
        if self._started:
            raise RuntimeError("cannot configure outbound call policy after calls have started")
        self.concurrency = concurrency
        self.breaker_limit = breaker_limit
        self._semaphore = asyncio.Semaphore(concurrency)
        self._configured = True

    async def execute(self, ref: ModelRef, operation: Callable[[], Awaitable[T]]) -> T:
        """Admit one real call and update only actual-call breaker state.

        ``None`` is allowed as an operation result for the coalescer's lazy
        cancellation seam: it means no adapter was invoked, so it leaves the
        existing streak untouched.  Budget refusals are likewise explicitly
        unsent and cannot prove a provider healthy or unhealthy.
        """
        self._started = True
        key = str(ref)
        # A tripped breaker dies BEFORE paying a cooldown wait; then pace this ref
        # OUTSIDE the gate so a cooling ref frees its slot for the other roles that
        # share this one policy (embed/OCR/STT), instead of holding it idle.
        self._raise_if_open(key)
        await self._await_cooldown(key)
        async with self._semaphore:
            self._raise_if_open(key)  # re-check: a concurrent trip may have landed while pacing
            try:
                reply = await operation()
            except asyncio.CancelledError:
                raise
            except UnsentError:
                raise
            except RetryableError as fault:
                self._note_cooldown(key, fault)  # honour the server's floor for the NEXT call
                self._call_serial += 1
                call_id = self._call_serial
                series_id = self._retryable_series.get(key)
                if series_id is None:
                    self._series_serial += 1
                    series_id = self._series_serial
                    self._retryable_series[key] = series_id
                fault.series_id = series_id
                fault.call_id = call_id
                streak = self._retryable_streaks.get(key, 0) + 1
                self._retryable_streaks[key] = streak
                if self.breaker_limit > 0 and streak >= self.breaker_limit:
                    opened = self._open.get(key)
                    if opened is None:
                        opened = _OpenCircuit(series_id, str(fault))
                        self._open[key] = opened
                    raise CircuitOpenTransport(
                        opened.message,
                        trip_id=opened.trip_id,
                        call_id=call_id,
                    ) from fault
                raise
            except Exception:
                self._reset(key)  # the endpoint answered with a non-retryable outcome
                raise
            else:
                if reply is not None:
                    self._reset(key)
                return reply

    def _reset(self, key: str) -> None:
        self._retryable_streaks.pop(key, None)
        self._retryable_series.pop(key, None)
        # NOT the cooldown: a floor recorded from a concurrent 429 must survive one
        # sibling call succeeding, or the pacing would leak the moment any page got
        # through. An expired floor is inert (its ``remaining`` is already <= 0).

    def _raise_if_open(self, key: str) -> None:
        opened = self._open.get(key)
        if opened is not None:
            raise CircuitOpenTransport(opened.message, trip_id=opened.trip_id)

    async def _await_cooldown(self, key: str) -> None:
        """Wait out any recorded per-ref cooldown before proceeding.

        Loops so a floor EXTENDED by a concurrent 429 while this coroutine slept is
        still honoured; the map read and the guard run in one step (no ``await``
        between them), so asyncio's single-threaded scheduling makes them atomic.
        """
        while True:
            not_before = self._not_before.get(key)
            if not_before is None:
                return
            remaining = not_before - self.clock()
            if remaining <= 0:
                return
            await self.sleep(remaining)

    def _note_cooldown(self, key: str, fault: RetryableError) -> None:
        """Record the server's Retry-After as this ref's not-before floor (A5.2).

        Clamped to the abuse ceiling; a fresh hint only EXTENDS an existing floor,
        never shrinks it (a later, smaller ask must not undo a wait already owed).
        """
        hint = fault.retry_after
        if hint is None:
            return
        not_before = self.clock() + min(hint, _RETRY_AFTER_CEILING_SECONDS)
        existing = self._not_before.get(key)
        if existing is None or not_before > existing:
            self._not_before[key] = not_before

    @staticmethod
    def _validate(concurrency: int, breaker_limit: int) -> None:
        if concurrency < 1:
            raise ValueError(f"call concurrency must be >= 1, got {concurrency}")
        if breaker_limit < 0:
            raise ValueError(f"breaker limit must be >= 0, got {breaker_limit}")


@dataclass(frozen=True, slots=True)
class AdmittedChatModel:
    inner: ChatModel
    policy: OutboundCallPolicy

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def complete(self, request: CompletionRequest) -> str:
        preflight_chat(self.inner, request)
        return await self.policy.execute(self.ref, lambda: self.inner.complete(request))

    async def complete_deferred(
        self, request: Callable[[], CompletionRequest | None]
    ) -> str | None:
        """Build the packed/solo request only after admission is granted.

        A coalescer can therefore remove cancelled waiters and honor a stop
        while it sat behind the semaphore, without learning how admission or
        budgeting is implemented.
        """

        async def send() -> str | None:
            prepared = request()
            if prepared is None:
                return None
            preflight_chat(self.inner, prepared)
            return await self.inner.complete(prepared)

        return await self.policy.execute(self.ref, send)


@dataclass(frozen=True, slots=True)
class AdmittedEmbeddingModel:
    inner: EmbeddingModel
    policy: OutboundCallPolicy

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return await self.policy.execute(self.ref, lambda: self.inner.embed(texts))


@dataclass(frozen=True, slots=True)
class AdmittedMediaEmbeddingModel:
    """Admission that preserves the joint text+image capability marker."""

    inner: MediaEmbeddingModel
    policy: OutboundCallPolicy

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return await self.policy.execute(self.ref, lambda: self.inner.embed_parts(texts))

    async def embed_parts(self, parts: Sequence[str | ImageData]) -> tuple[tuple[float, ...], ...]:
        return await self.policy.execute(self.ref, lambda: self.inner.embed_parts(parts))


@dataclass(frozen=True, slots=True)
class AdmittedDocumentParser:
    inner: DocumentParser
    policy: OutboundCallPolicy

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def parse_image(self, image: ImageData) -> str:
        return await self.policy.execute(self.ref, lambda: self.inner.parse_image(image))

    async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
        return await self.policy.execute(self.ref, lambda: self.inner.parse_pdf(path))


@dataclass(frozen=True, slots=True)
class AdmittedTranscriber:
    inner: Transcriber
    policy: OutboundCallPolicy

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def transcribe(self, audio: AudioData) -> str:
        return await self.policy.execute(self.ref, lambda: self.inner.transcribe(audio))


def admitted_chat(inner: ChatModel, policy: OutboundCallPolicy) -> ChatModel:
    return AdmittedChatModel(inner, policy)


def supports_deferred_chat(model: object) -> TypeGuard[DeferredChatModel]:
    return isinstance(model, DeferredChatModel)


def admitted_embed(inner: EmbeddingModel, policy: OutboundCallPolicy) -> EmbeddingModel:
    from smartpipe.models.base import supports_media_embedding

    if supports_media_embedding(inner):
        return AdmittedMediaEmbeddingModel(inner, policy)
    return AdmittedEmbeddingModel(inner, policy)


def admitted_parser(inner: DocumentParser, policy: OutboundCallPolicy) -> DocumentParser:
    return AdmittedDocumentParser(inner, policy)


def admitted_transcriber(inner: Transcriber, policy: OutboundCallPolicy) -> Transcriber:
    return AdmittedTranscriber(inner, policy)
