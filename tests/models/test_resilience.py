"""Unit tests for the resilience combinators (models/resilience.py).

Each decorator is exercised against the semantics it inherits from the mechanism
it re-expresses: ``retried`` ↔ the adapters' ``with_retries``; ``Breaker`` /
``circuit_broken`` ↔ the admission breaker + ``make_failover`` swap; ``rate_limited``
↔ the admission concurrency gate. The end-to-end "failover numbers" proof that
``circuit_broken`` reproduces the map/runner contract lives in
``test_circuit_broken_failover.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest

from smartpipe.core.errors import (
    CircuitOpenTransport,
    ExcludedError,
    ItemError,
    RetryableError,
    TransportError,
    UnsentError,
)
from smartpipe.models.admission import supports_deferred_chat
from smartpipe.models.base import CompletionRequest, ModelRef
from smartpipe.models.resilience import (
    Breaker,
    Cooldown,
    ResilientChatModel,
    circuit_broken,
    rate_limited,
    retried,
)
from smartpipe.models.retry import RetryPolicy

PRIMARY = ModelRef("ollama", "fake")
BACKUP = ModelRef("openai", "gpt-4o-mini")


async def _nosleep(_seconds: float) -> None:
    return None


def _request(user: str = "hi") -> CompletionRequest:
    return CompletionRequest(system=None, user=user)


# --- retried ------------------------------------------------------------------


async def test_retried_recovers_after_transient_failures() -> None:
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TransportError("openai error 503")
        return "ok"

    resilient = retried(
        RetryPolicy(attempts=5),
        is_retryable=lambda exc: isinstance(exc, RetryableError),
        sleep=_nosleep,
        rand=lambda: 0.0,
    )(flaky)

    assert await resilient() == "ok"
    assert attempts == 3


async def test_retried_reraises_a_non_retryable_immediately() -> None:
    attempts = 0

    async def declines() -> str:
        nonlocal attempts
        attempts += 1
        raise ItemError("model declined")

    resilient = retried(
        RetryPolicy(attempts=5),
        is_retryable=lambda exc: isinstance(exc, RetryableError),
        sleep=_nosleep,
    )(declines)

    with pytest.raises(ItemError):
        await resilient()
    assert attempts == 1  # not retried


async def test_retried_gives_up_after_the_attempt_budget() -> None:
    attempts = 0

    async def always_down() -> str:
        nonlocal attempts
        attempts += 1
        raise TransportError("openai error 429")

    resilient = retried(
        RetryPolicy(attempts=3),
        is_retryable=lambda exc: isinstance(exc, RetryableError),
        sleep=_nosleep,
        rand=lambda: 0.0,
    )(always_down)

    with pytest.raises(TransportError):
        await resilient()
    assert attempts == 3


# --- Breaker ------------------------------------------------------------------


def test_breaker_stamps_ids_and_trips_at_the_limit() -> None:
    breaker = Breaker(limit=3)
    faults = [RetryableError("429") for _ in range(3)]

    assert breaker.record_transport_failure(PRIMARY, faults[0]) is None
    assert breaker.record_transport_failure(PRIMARY, faults[1]) is None
    opened = breaker.record_transport_failure(PRIMARY, faults[2])

    assert opened is not None
    # every fault in one streak shares its series id; each call gets a fresh id
    assert {f.series_id for f in faults} == {opened.trip_id}
    assert [f.call_id for f in faults] == [1, 2, 3]
    assert breaker.opened(PRIMARY) is opened


def test_breaker_keys_streaks_by_ref_so_a_dead_primary_spares_the_backup() -> None:
    breaker = Breaker(limit=2)
    breaker.record_transport_failure(PRIMARY, RetryableError("429"))
    breaker.record_transport_failure(PRIMARY, RetryableError("429"))

    assert breaker.opened(PRIMARY) is not None
    assert breaker.opened(BACKUP) is None
    # the backup keeps its own fresh streak
    assert breaker.record_transport_failure(BACKUP, RetryableError("429")) is None


def test_breaker_reset_clears_the_streak() -> None:
    breaker = Breaker(limit=2)
    breaker.record_transport_failure(PRIMARY, RetryableError("429"))
    breaker.reset(PRIMARY)
    # the streak restarts from zero, so one more failure does not trip
    assert breaker.record_transport_failure(PRIMARY, RetryableError("429")) is None


def test_breaker_limit_zero_never_trips() -> None:
    breaker = Breaker(limit=0)
    for _ in range(10):
        assert breaker.record_transport_failure(PRIMARY, RetryableError("429")) is None
    assert breaker.opened(PRIMARY) is None


# --- circuit_broken -----------------------------------------------------------


def _cooldown() -> Cooldown:
    return Cooldown()


async def test_circuit_broken_passes_success_through_and_resets() -> None:
    breaker = Breaker(limit=2)

    async def ok(value: str) -> str:
        return value.upper()

    guarded = circuit_broken(breaker, ref=PRIMARY)(ok)
    # one prior failure, then a success clears the streak
    breaker.record_transport_failure(PRIMARY, RetryableError("429"))
    assert await guarded("hi") == "HI"
    assert breaker.record_transport_failure(PRIMARY, RetryableError("429")) is None


async def test_circuit_broken_reraises_below_the_limit_with_stamped_ids() -> None:
    breaker = Breaker(limit=3)

    async def down() -> str:
        raise TransportError("openai error 503")

    guarded = circuit_broken(breaker, ref=PRIMARY)(down)
    with pytest.raises(TransportError) as first:
        await guarded()
    assert first.value.series_id is not None
    assert first.value.call_id == 1


async def test_circuit_broken_swaps_to_a_fresh_fallback_and_announces() -> None:
    breaker = Breaker(limit=2)
    primary_calls = 0
    backup_calls = 0
    notices: list[str] = []

    async def primary() -> str:
        nonlocal primary_calls
        primary_calls += 1
        raise TransportError("ollama down")

    async def backup() -> str:
        nonlocal backup_calls
        backup_calls += 1
        return "B"

    guarded = circuit_broken(
        breaker,
        ref=PRIMARY,
        fallback=backup,
        fallback_ref=BACKUP,
        announce=notices.append,
    )(primary)

    with pytest.raises(TransportError):
        await guarded()  # streak 1
    with pytest.raises(CircuitOpenTransport):
        await guarded()  # streak 2 == limit: trips and swaps
    # the very next call routes to the fallback transparently
    assert await guarded() == "B"

    assert primary_calls == 2
    assert backup_calls == 1
    assert notices == [
        "ollama looks down (2 consecutive transport failures) — "
        "switching to openai/gpt-4o-mini for the rest of the run"
    ]


async def test_circuit_broken_without_a_fallback_dies_honestly() -> None:
    breaker = Breaker(limit=2)
    notices: list[str] = []

    async def down() -> str:
        raise TransportError("ollama down")

    guarded = circuit_broken(breaker, ref=PRIMARY, announce=notices.append)(down)
    with pytest.raises(TransportError):
        await guarded()
    with pytest.raises(CircuitOpenTransport):
        await guarded()
    # an already-open circuit refuses without calling the target again
    with pytest.raises(CircuitOpenTransport):
        await guarded()
    assert notices == []  # no swap, so no "switching to" line


async def test_circuit_broken_dies_when_the_fallback_also_trips() -> None:
    breaker = Breaker(limit=1)

    async def primary() -> str:
        raise TransportError("primary down")

    async def backup() -> str:
        raise TransportError("backup down too")

    guarded = circuit_broken(breaker, ref=PRIMARY, fallback=backup, fallback_ref=BACKUP)(primary)
    with pytest.raises(CircuitOpenTransport):
        await guarded()  # primary trips at limit 1, swaps to backup
    with pytest.raises(CircuitOpenTransport):
        await guarded()  # backup trips at its own limit — honest death


async def test_circuit_broken_passes_content_errors_through_and_resets() -> None:
    breaker = Breaker(limit=2)
    breaker.record_transport_failure(PRIMARY, RetryableError("429"))

    async def declines() -> str:
        raise ItemError("model declined")

    guarded = circuit_broken(breaker, ref=PRIMARY)(declines)
    with pytest.raises(ItemError):
        await guarded()
    # a content answer proved the wire alive — the streak reset
    assert breaker.record_transport_failure(PRIMARY, RetryableError("429")) is None


async def test_circuit_broken_excluded_error_resets_the_streak() -> None:
    breaker = Breaker(limit=2)
    breaker.record_transport_failure(PRIMARY, RetryableError("429"))

    async def excluded() -> str:
        raise ExcludedError("excluded before submission")

    guarded = circuit_broken(breaker, ref=PRIMARY)(excluded)
    with pytest.raises(ExcludedError):
        await guarded()
    assert breaker.record_transport_failure(PRIMARY, RetryableError("429")) is None


async def test_circuit_broken_unsent_error_leaves_the_streak_untouched() -> None:
    breaker = Breaker(limit=2)
    breaker.record_transport_failure(PRIMARY, RetryableError("429"))

    async def unsent() -> str:
        raise UnsentError("call budget reached")

    guarded = circuit_broken(breaker, ref=PRIMARY)(unsent)
    with pytest.raises(UnsentError):
        await guarded()
    # unsent proves nothing about the wire: the next failure trips at the limit
    assert breaker.record_transport_failure(PRIMARY, RetryableError("429")) is not None


# --- rate_limited / Cooldown --------------------------------------------------


async def test_rate_limited_bounds_concurrency() -> None:
    active = 0
    peak = 0
    release = asyncio.Event()
    started = asyncio.Semaphore(0)

    async def work() -> int:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.release()
        try:
            await release.wait()
            return active
        finally:
            active -= 1

    guarded = rate_limited(concurrency=2, cooldown=_cooldown())(work)

    async def call() -> int:
        return await guarded()

    tasks = [asyncio.create_task(call()) for _ in range(5)]
    for _ in range(2):
        await started.acquire()
    await asyncio.sleep(0)
    assert peak == 2  # only two admitted at once
    release.set()
    await asyncio.gather(*tasks)
    assert peak == 2


async def test_rate_limited_rejects_zero_concurrency() -> None:
    with pytest.raises(ValueError, match="concurrency must be >= 1"):
        rate_limited(concurrency=0, cooldown=_cooldown())


async def test_rate_limited_feeds_a_retry_after_hint_to_the_cooldown() -> None:
    cooldown = Cooldown()

    async def down() -> str:
        raise TransportError("429")

    guarded = rate_limited(
        concurrency=1,
        cooldown=cooldown,
        retry_after=lambda _exc: 4.5,
    )(down)
    with pytest.raises(TransportError):
        await guarded()
    assert cooldown.last_hint == 4.5


def test_cooldown_records_the_last_hint() -> None:
    cooldown = Cooldown()
    assert cooldown.last_hint is None
    cooldown.penalize(2.0)
    assert cooldown.last_hint == 2.0


# --- ResilientChatModel -------------------------------------------------------


@dataclass(slots=True)
class _ScriptedChat:
    """A chat adapter whose ``complete`` runs an injected script per call."""

    ref: ModelRef
    script: Callable[[CompletionRequest], Awaitable[str]]

    async def complete(self, request: CompletionRequest) -> str:
        return await self.script(request)


def _resilient(
    chat: _ScriptedChat, *, breaker: Breaker, concurrency: int = 4
) -> ResilientChatModel:
    return ResilientChatModel(chat, breaker=breaker, concurrency=concurrency, cooldown=Cooldown())


async def test_resilient_chat_is_a_deferred_chat_model() -> None:
    async def ok(_request: CompletionRequest) -> str:
        return "ok"

    model = _resilient(_ScriptedChat(PRIMARY, ok), breaker=Breaker(limit=5))
    # the coalescer nests OUTSIDE it precisely because it admits the deferred shape
    assert supports_deferred_chat(model)
    assert model.ref == PRIMARY


async def test_resilient_chat_completes_both_entry_points() -> None:
    async def echo(request: CompletionRequest) -> str:
        return request.user.upper()

    model = _resilient(_ScriptedChat(PRIMARY, echo), breaker=Breaker(limit=5))
    assert await model.complete(_request("hi")) == "HI"
    # the deferred twin builds its request only after admission, then completes
    assert await model.complete_deferred(lambda: _request("yo")) == "YO"


async def test_resilient_chat_deferred_none_is_the_lazy_cancellation_seam() -> None:
    calls = 0

    async def counted(_request: CompletionRequest) -> str:
        nonlocal calls
        calls += 1
        return "sent"

    model = _resilient(_ScriptedChat(PRIMARY, counted), breaker=Breaker(limit=5))
    # a factory that yields None means every waiter cancelled — no adapter call
    assert await model.complete_deferred(lambda: None) is None
    assert calls == 0


async def test_resilient_chat_shares_one_breaker_across_both_entry_points() -> None:
    breaker = Breaker(limit=3)

    async def down(_request: CompletionRequest) -> str:
        raise TransportError("openai error 503")

    model = _resilient(_ScriptedChat(PRIMARY, down), breaker=breaker)
    # two solo failures, then a deferred failure trips at the shared limit of 3
    with pytest.raises(TransportError):
        await model.complete(_request())
    with pytest.raises(TransportError):
        await model.complete(_request())
    with pytest.raises(CircuitOpenTransport) as tripped:
        await model.complete_deferred(lambda: _request())
    assert tripped.value.series_id is not None
    assert tripped.value.call_id == 3  # one streak, three calls, however they arrived
    # the circuit is open now: a fourth call is refused without touching the wire
    with pytest.raises(CircuitOpenTransport):
        await model.complete(_request())


async def test_resilient_chat_bounds_concurrency_across_both_entry_points() -> None:
    active = 0
    peak = 0
    release = asyncio.Event()
    started = asyncio.Semaphore(0)

    async def work(_request: CompletionRequest) -> str:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.release()
        try:
            await release.wait()
            return "done"
        finally:
            active -= 1

    model = _resilient(_ScriptedChat(PRIMARY, work), breaker=Breaker(limit=5), concurrency=2)

    async def solo() -> str:
        return await model.complete(_request())

    async def deferred() -> str | None:
        return await model.complete_deferred(lambda: _request())

    # a mix of both entry points must share ONE gate: at most two in flight
    tasks = [
        asyncio.create_task(solo()),
        asyncio.create_task(deferred()),
        asyncio.create_task(solo()),
        asyncio.create_task(deferred()),
    ]
    for _ in range(2):
        await started.acquire()
    await asyncio.sleep(0)
    assert peak == 2
    release.set()
    await asyncio.gather(*tasks)
    assert peak == 2
