"""Resilience combinators: retry, circuit-break + auto-fallback, rate limiting.

Native-Python decorator factories. Each wraps a plain async operation and adds
one cross-cutting robustness property, preserving the wrapped signature with
``ParamSpec`` so the decorated callable is a drop-in for the undecorated one at
every call site. Effects (clock/sleep/rand/announce) are INJECTED so tests drive
them with fakes; the knobs (degree of protection) are constructor arguments.

These RE-EXPRESS resilience the codebase already composes — the run-scoped
admission breaker (``models/admission.py``) and the adapters' bounded
``with_retries`` (``models/retry.py``) — as first-class combinators the
composition root stacks, so a verb calls a plain function and robustness happens
underneath it. Nothing here reaches for a global.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from functools import wraps
from typing import TYPE_CHECKING, ParamSpec, TypeVar

from smartpipe.core.errors import CircuitOpenTransport, RetryableError, UnsentError
from smartpipe.engine.runner import DEFAULT_BREAKER_LIMIT
from smartpipe.models.retry import RetryPolicy, with_retries

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from smartpipe.models.base import ModelRef

__all__ = [
    "Breaker",
    "Cooldown",
    "circuit_broken",
    "rate_limited",
    "retried",
]

P = ParamSpec("P")
R = TypeVar("R")


def _noop(_message: str) -> None:
    """The default announce sink: swallow, so the decorator is silent unless the
    composition root injects a diagnostics sink."""


def _is_transport(exc: BaseException) -> bool:
    """The default transport classifier: an exhausted bounded-retry ladder
    (429/5xx/connect/timeout) — the only failure a circuit breaker counts."""
    return isinstance(exc, RetryableError)


def retried(
    policy: RetryPolicy,
    *,
    is_retryable: Callable[[Exception], bool],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rand: Callable[[], float] = random.random,
    delay_hint: Callable[[Exception], float | None] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Wrap a plain async op in the adapters' bounded jittered backoff.

    The body REUSES ``models/retry.py::with_retries`` verbatim — no rewrite. This
    is reserved for genuinely plain inner calls (the OCR ladder): chat is NOT
    wrapped here, because its adapters already retry internally and the budget
    deliberately sits OUTSIDE that retry (``models/budget.py``).
    """

    def decorate(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            return await with_retries(
                policy,
                lambda: fn(*args, **kwargs),
                is_retryable=is_retryable,
                sleep=sleep,
                rand=rand,
                delay_hint=delay_hint,
            )

        return wrapped

    return decorate


@dataclass(frozen=True, slots=True)
class _OpenCircuit:
    """One run-scoped breaker event: the trip id and the message it opened with."""

    trip_id: int
    message: str


@dataclass(slots=True)
class Breaker:
    """Per-ref transport-failure streak and open-circuit state.

    Extracted from the run-scoped admission policy (``OutboundCallPolicy``): the
    streak is keyed by ``str(ref)`` so a dead primary never poisons its fallback,
    and an exhausted retry ladder is ONE failed actual call even when K coalesced
    waiters sit behind it. It also assigns ``series_id`` (one consecutive
    availability streak, through its trip) and ``call_id`` (one actual call whose
    failure may fan to several coalesced waiters), because ordered emission and
    coalescing fan-out both depend on those ids. Streak state is mutable — this is
    a stateful collaborator — but it is reached only through the methods below.
    """

    limit: int = DEFAULT_BREAKER_LIMIT
    _streaks: dict[str, int] = field(default_factory=dict[str, int])
    _series: dict[str, int] = field(default_factory=dict[str, int])
    _open: dict[str, _OpenCircuit] = field(default_factory=dict[str, _OpenCircuit])
    _series_serial: int = 0
    _call_serial: int = 0

    def opened(self, ref: ModelRef) -> _OpenCircuit | None:
        """The open circuit for ``ref``, or None while it is still closed."""
        return self._open.get(str(ref))

    def record_transport_failure(self, ref: ModelRef, fault: RetryableError) -> _OpenCircuit | None:
        """Stamp series/call ids on ``fault`` and bump the per-ref streak.

        Returns the open circuit when this failure trips the breaker (idempotent
        per ref: the first trip fixes the message and trip id), else None.
        """
        key = str(ref)
        self._call_serial += 1
        call_id = self._call_serial
        series_id = self._series.get(key)
        if series_id is None:
            self._series_serial += 1
            series_id = self._series_serial
            self._series[key] = series_id
        fault.series_id = series_id
        fault.call_id = call_id
        streak = self._streaks.get(key, 0) + 1
        self._streaks[key] = streak
        if self.limit > 0 and streak >= self.limit:
            opened = self._open.get(key)
            if opened is None:
                opened = _OpenCircuit(series_id, str(fault))
                self._open[key] = opened
            return opened
        return None

    def reset(self, ref: ModelRef) -> None:
        """A non-availability outcome proves the wire alive — clear the streak."""
        key = str(ref)
        self._streaks.pop(key, None)
        self._series.pop(key, None)


@dataclass(slots=True)
class _Route:
    """Mutable swap state for one resilient callable (shared by a ``complete`` and
    its deferred twin when a chat model routes both through one breaker)."""

    on_fallback: bool = False
    switched: bool = False


def circuit_broken(
    breaker: Breaker,
    *,
    ref: ModelRef,
    fallback: Callable[P, Awaitable[R]] | None = None,
    fallback_ref: ModelRef | None = None,
    announce: Callable[[str], None] = _noop,
    is_transport: Callable[[BaseException], bool] = _is_transport,
    route: _Route | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Guard an async op with the actual-call breaker and a single wholesale
    fallback swap — the combinator that RE-EXPRESSES the admission breaker plus
    ``verbs/common.py::make_failover``.

    On each call it invokes the current target; a success resets the streak. A
    transport error (``is_transport``) increments the per-ref streak; at
    ``streak >= limit`` it either (a) announces, swaps current→fallback if a fresh
    fallback exists, and raises ``CircuitOpenTransport`` (the signal the runner
    already replays on), or (b) with no fresh fallback raises ``CircuitOpenTransport``
    unchanged — honest death. A non-transport ``ItemError``
    (capability/``ExcludedError``) passes through after resetting the streak;
    ``UnsentError`` passes through leaving the streak untouched (an unsent call
    proves nothing about the wire).
    """
    state = _Route() if route is None else route

    def decorate(primary: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(primary)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            if state.on_fallback and fallback is not None:
                target = fallback
                target_ref = ref if fallback_ref is None else fallback_ref
            else:
                target = primary
                target_ref = ref
            opened = breaker.opened(target_ref)
            if opened is not None:
                raise CircuitOpenTransport(opened.message, trip_id=opened.trip_id)
            try:
                reply = await target(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except UnsentError:
                raise  # an unsent call leaves the streak untouched
            except Exception as exc:
                if not (isinstance(exc, RetryableError) and is_transport(exc)):
                    breaker.reset(target_ref)  # the endpoint answered, non-retryable
                    raise
                tripped = breaker.record_transport_failure(target_ref, exc)
                if tripped is None:
                    raise  # streak below the limit — re-raise the stamped fault
                if not state.on_fallback and fallback is not None:
                    announce(
                        f"{ref.provider} looks down "
                        f"({breaker.limit} consecutive transport failures) — "
                        f"switching to {fallback_ref} for the rest of the run"
                    )
                    state.on_fallback = True
                    state.switched = True
                raise CircuitOpenTransport(
                    tripped.message, trip_id=tripped.trip_id, call_id=exc.call_id
                ) from exc
            else:
                if reply is not None:
                    breaker.reset(target_ref)
                return reply

        return wrapped

    return decorate


@dataclass(slots=True)
class Cooldown:
    """A server-backoff seam the rate limiter carries for the A5.2 rung.

    Inert today: it records the most recent ``Retry-After`` hint (so the seam and
    its wiring exist and are tested) but never gates a call. A5.2 turns the
    recorded hint into an actual per-ref wait.
    """

    last_hint: float | None = None

    def penalize(self, seconds: float) -> None:
        """Record a server-supplied backoff (inert seam until A5.2)."""
        self.last_hint = seconds


def rate_limited(
    *,
    concurrency: int,
    cooldown: Cooldown,
    retry_after: Callable[[BaseException], float | None] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Bound the number of in-flight calls with a shared semaphore — the
    combinator that RE-EXPRESSES the concurrency half of ``admitted_chat``.

    ``retry_after`` (when a wire supplies a ``Retry-After`` hint) feeds the
    ``cooldown`` seam without gating yet; the semaphore is the live protection.
    """
    if concurrency < 1:
        raise ValueError(f"call concurrency must be >= 1, got {concurrency}")
    semaphore = asyncio.Semaphore(concurrency)

    def decorate(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            async with semaphore:
                if retry_after is None:
                    return await fn(*args, **kwargs)
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    hint = retry_after(exc)
                    if hint is not None:
                        cooldown.penalize(hint)  # inert until A5.2
                    raise

        return wrapped

    return decorate
