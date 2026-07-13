"""One run-scoped actual-call boundary shared by every remote model role."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import CircuitOpenTransport, RetryableError, UnsentError
from smartpipe.models.admission import (
    OutboundCallPolicy,
    admitted_chat,
    admitted_embed,
    admitted_parser,
    admitted_transcriber,
)
from smartpipe.models.base import (
    AudioData,
    CompletionRequest,
    ImageData,
    ModelRef,
    supports_media_embedding,
)
from smartpipe.models.ocr import OcrPage

if TYPE_CHECKING:
    from collections.abc import Sequence


class _Tracker:
    def __init__(self) -> None:
        self.active = 0
        self.maximum = 0
        self.started = 0
        self.first = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self) -> None:
        self.active += 1
        self.started += 1
        self.maximum = max(self.maximum, self.active)
        self.first.set()
        try:
            await self.release.wait()
        finally:
            self.active -= 1


class _Chat:
    ref = ModelRef("openai", "chat")

    def __init__(self, tracker: _Tracker) -> None:
        self.tracker = tracker

    async def complete(self, request: CompletionRequest) -> str:
        await self.tracker.run()
        return request.user


class _Embed:
    ref = ModelRef("openai", "embed")

    def __init__(self, tracker: _Tracker) -> None:
        self.tracker = tracker

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        await self.tracker.run()
        return tuple((1.0,) for _ in texts)


class _Parser:
    ref = ModelRef("mistral", "ocr")

    def __init__(self, tracker: _Tracker) -> None:
        self.tracker = tracker

    async def parse_image(self, image: ImageData) -> str:
        await self.tracker.run()
        return image.mime

    async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
        await self.tracker.run()
        return (OcrPage(0, path.name),)


class _Transcriber:
    ref = ModelRef("openai", "whisper-1")

    def __init__(self, tracker: _Tracker) -> None:
        self.tracker = tracker

    async def transcribe(self, audio: AudioData) -> str:
        await self.tracker.run()
        return audio.mime


async def test_one_policy_limits_actual_calls_across_remote_role_types() -> None:
    tracker = _Tracker()
    policy = OutboundCallPolicy(concurrency=1)
    chat = admitted_chat(_Chat(tracker), policy)
    embed = admitted_embed(_Embed(tracker), policy)
    parser = admitted_parser(_Parser(tracker), policy)
    transcriber = admitted_transcriber(_Transcriber(tracker), policy)

    tasks = (
        asyncio.create_task(chat.complete(CompletionRequest(system=None, user="hello"))),
        asyncio.create_task(embed.embed(("hello",))),
        asyncio.create_task(parser.parse_image(ImageData(b"png", "image/png"))),
        asyncio.create_task(transcriber.transcribe(AudioData(b"wav", "audio/wav"))),
    )
    await tracker.first.wait()
    await asyncio.sleep(0)
    assert tracker.started == 1
    tracker.release.set()
    await asyncio.gather(*tasks)
    assert tracker.started == 4
    assert tracker.maximum == 1


async def test_exhausted_429_counts_at_the_actual_call_breaker() -> None:
    policy = OutboundCallPolicy(concurrency=1, breaker_limit=2)
    calls = 0

    async def limited() -> str:
        nonlocal calls
        calls += 1
        raise RetryableError("openai error 429: slow down")

    with pytest.raises(RetryableError):
        await policy.execute(ModelRef("openai", "chat"), limited)
    with pytest.raises(CircuitOpenTransport) as tripped:
        await policy.execute(ModelRef("openai", "chat"), limited)
    with pytest.raises(CircuitOpenTransport) as opened:
        await policy.execute(ModelRef("openai", "chat"), limited)

    assert opened.value.trip_id == tripped.value.trip_id
    assert calls == 2


async def test_an_unsent_budget_refusal_does_not_reset_the_actual_call_streak() -> None:
    policy = OutboundCallPolicy(concurrency=1, breaker_limit=2)
    ref = ModelRef("openai", "chat")

    async def retryable() -> str:
        raise RetryableError("openai error 429")

    async def unsent() -> str:
        raise UnsentError("call budget reached")

    with pytest.raises(RetryableError):
        await policy.execute(ref, retryable)
    with pytest.raises(UnsentError):
        await policy.execute(ref, unsent)
    with pytest.raises(CircuitOpenTransport):
        await policy.execute(ref, retryable)


class _FakeClock:
    """A driven monotonic clock: ``sleep`` advances it, so a cooldown wait is
    deterministic and instant. Mirrors ``tests/models/test_retry.py``'s recorder,
    but the clock ADVANCES on sleep so a ``while now < not-before`` gate settles."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def read(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_a_retry_after_hint_paces_the_next_admission_of_that_ref() -> None:
    clock = _FakeClock()
    # breaker_limit=0 disables the trip so the cooldown is isolated from it
    policy = OutboundCallPolicy(concurrency=1, breaker_limit=0, clock=clock.read, sleep=clock.sleep)
    ref = ModelRef("mistral", "ocr")

    async def rate_limited_call() -> str:
        raise RetryableError("ocr error 429: rate limited", retry_after=5.0)

    with pytest.raises(RetryableError):
        await policy.execute(ref, rate_limited_call)
    assert clock.sleeps == []  # the failed call itself never sleeps

    async def ok() -> str:
        return "done"

    # the NEXT admission of that ref honours the server's floor before proceeding
    assert await policy.execute(ref, ok) == "done"
    assert clock.sleeps == [5.0]
    assert clock.now == 5.0


async def test_a_tripped_breaker_dies_before_paying_a_stale_cooldown() -> None:
    """A5.2 review: the breaker check must run BEFORE the cooldown wait. A run whose
    OCR wire the breaker condemned is STOPPING, not pacing — it must die on the open
    circuit immediately, never sleep out a floor recorded from the same 429 first.
    Pins the ordering the comments claim (a reversed order would sleep 5s here)."""
    clock = _FakeClock()
    # breaker_limit=1: one 429 both ARMS the floor and TRIPS the breaker
    policy = OutboundCallPolicy(concurrency=1, breaker_limit=1, clock=clock.read, sleep=clock.sleep)
    ref = ModelRef("mistral", "ocr")

    async def rate_limited_call() -> str:
        raise RetryableError("ocr error 429: rate limited", retry_after=5.0)

    with pytest.raises(CircuitOpenTransport):
        await policy.execute(ref, rate_limited_call)  # arms the 5s floor AND opens the circuit
    assert clock.sleeps == []  # the tripping call itself never slept

    async def would_succeed() -> str:
        return "unreachable"

    with pytest.raises(CircuitOpenTransport):
        await policy.execute(ref, would_succeed)  # the open circuit dies FIRST
    assert clock.sleeps == []  # the 5s floor was NEVER slept — the breaker won the race


async def test_a_cooldown_on_one_ref_does_not_gate_a_different_ref() -> None:
    clock = _FakeClock()
    policy = OutboundCallPolicy(concurrency=2, breaker_limit=0, clock=clock.read, sleep=clock.sleep)
    hot = ModelRef("mistral", "ocr")
    cool = ModelRef("openai", "embed")

    async def rate_limited_call() -> str:
        raise RetryableError("429", retry_after=9.0)

    with pytest.raises(RetryableError):
        await policy.execute(hot, rate_limited_call)

    async def ok() -> str:
        return "ok"

    assert await policy.execute(cool, ok) == "ok"  # the other ref is untouched
    assert clock.sleeps == []
    assert await policy.execute(hot, ok) == "ok"  # the hot ref still owes the wait
    assert clock.sleeps == [9.0]


async def test_a_429_without_a_retry_after_records_no_cooldown() -> None:
    clock = _FakeClock()
    policy = OutboundCallPolicy(concurrency=1, breaker_limit=0, clock=clock.read, sleep=clock.sleep)
    ref = ModelRef("mistral", "ocr")

    async def bare_429() -> str:
        raise RetryableError("429")  # no server hint

    with pytest.raises(RetryableError):
        await policy.execute(ref, bare_429)

    async def ok() -> str:
        return "ok"

    assert await policy.execute(ref, ok) == "ok"
    assert clock.sleeps == []  # no hint -> no cross-call pacing


async def test_a_hostile_retry_after_is_clamped_before_it_paces() -> None:
    clock = _FakeClock()
    policy = OutboundCallPolicy(concurrency=1, breaker_limit=0, clock=clock.read, sleep=clock.sleep)
    ref = ModelRef("mistral", "ocr")

    async def hostile() -> str:
        raise RetryableError("429", retry_after=86_400.0)  # a full day

    with pytest.raises(RetryableError):
        await policy.execute(ref, hostile)

    async def ok() -> str:
        return "ok"

    await policy.execute(ref, ok)
    assert clock.sleeps == [60.0]  # the abuse ceiling, not a day


async def test_a_smaller_concurrent_hint_never_shrinks_a_larger_floor() -> None:
    clock = _FakeClock()
    policy = OutboundCallPolicy(concurrency=2, breaker_limit=0, clock=clock.read, sleep=clock.sleep)
    ref = ModelRef("mistral", "ocr")
    b_in_flight = asyncio.Event()
    a_recorded = asyncio.Event()

    async def big() -> str:
        await b_in_flight.wait()  # B is already past its cooldown check (sees no floor)
        a_recorded.set()  # ... then the BIG floor records first
        raise RetryableError("429", retry_after=30.0)

    async def small() -> str:
        b_in_flight.set()
        await a_recorded.wait()  # the small ask lands AFTER the big floor is in place
        raise RetryableError("429", retry_after=1.0)

    task_a = asyncio.create_task(policy.execute(ref, big))
    task_b = asyncio.create_task(policy.execute(ref, small))
    results = await asyncio.gather(task_a, task_b, return_exceptions=True)
    assert all(isinstance(r, RetryableError) for r in results)
    assert clock.sleeps == []  # no prior floor -> neither concurrent call waited

    async def ok() -> str:
        return "ok"

    await policy.execute(ref, ok)
    assert clock.sleeps == [30.0]  # the big floor survived the small concurrent one


async def test_media_embedding_capability_survives_admission() -> None:
    class _MediaEmbed:
        ref = ModelRef("jina", "clip")

        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            return tuple((1.0,) for _ in texts)

        async def embed_parts(
            self, parts: Sequence[str | ImageData]
        ) -> tuple[tuple[float, ...], ...]:
            return tuple((1.0,) for _ in parts)

    model = admitted_embed(_MediaEmbed(), OutboundCallPolicy())
    probe: object = model
    assert supports_media_embedding(probe)
    assert await probe.embed_parts((ImageData(b"png", "image/png"),)) == ((1.0,),)
