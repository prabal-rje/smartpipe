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

from smartpipe.core.errors import CircuitOpenTransport, RetryableError, SetupFault, UnsentError
from smartpipe.engine.runner import DEFAULT_BREAKER_LIMIT
from smartpipe.models.base import preflight_chat
from smartpipe.models.retry import RetryPolicy, with_retries

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from smartpipe.models.base import ChatModel, CompletionRequest, ModelRef

__all__ = [
    "Breaker",
    "Cooldown",
    "ResilientChatModel",
    "WiredChat",
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
        # A CircuitOpenTransport IS already an open-circuit verdict (the inner wire
        # concluded it is down) — trip NOW, without waiting for the streak to reach
        # the limit. A plain transport fault trips only at the configured threshold.
        if (self.limit > 0 and streak >= self.limit) or isinstance(fault, CircuitOpenTransport):
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
    fallback_factory: Callable[[], Awaitable[Callable[P, Awaitable[R]]]] | None = None,
    fallback_ref: ModelRef | None = None,
    announce: Callable[[str], None] = _noop,
    note: Callable[[str], None] = _noop,
    is_transport: Callable[[BaseException], bool] = _is_transport,
    route: _Route | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Guard an async op with the actual-call breaker and a single wholesale
    fallback swap — the combinator that RE-EXPRESSES the admission breaker plus
    ``verbs/common.py::make_failover``.

    On each call it invokes the current target; a success resets the streak. A
    transport error (``is_transport``) increments the per-ref streak; at
    ``streak >= limit`` it either (a) builds the fallback LAZILY through
    ``fallback_factory`` (keys/login are checked HERE, exactly as make_failover's
    ``switch()`` — an unusable fallback is ``note``-d and the run dies honestly),
    announces, swaps the shared ``route`` current→fallback, and raises
    ``CircuitOpenTransport`` with ``switched=True`` (the runner replays its held
    window onto the now-swapped target); or (b) with no fresh fallback raises
    ``CircuitOpenTransport`` with ``switched=False`` — honest death. A non-transport
    ``ItemError`` (capability/``ExcludedError``) passes through after resetting the
    streak; ``UnsentError`` passes through leaving the streak untouched (an unsent
    call proves nothing about the wire). ``fallback_factory`` is invoked at most
    once per decorated callable; the built target is reused for every later call.
    """
    state = _Route() if route is None else route

    def decorate(primary: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        fallback_target: Callable[P, Awaitable[R]] | None = None

        @wraps(primary)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            nonlocal fallback_target
            on_fb = state.on_fallback and fallback_factory is not None
            if on_fb:
                if fallback_target is None:
                    assert fallback_factory is not None  # narrowed by ``on_fb``
                    # The shared route already swapped (another entry point tripped);
                    # materialise THIS path's target from the memoised backup adapter.
                    fallback_target = await fallback_factory()
                target = fallback_target
                target_ref = ref if fallback_ref is None else fallback_ref
            else:
                target = primary
                target_ref = ref
            opened = breaker.opened(target_ref)
            if opened is not None:
                raise CircuitOpenTransport(
                    opened.message,
                    trip_id=opened.trip_id,
                    switched=state.switched and not on_fb,
                )
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
                if not on_fb and fallback_factory is not None and not state.switched:
                    # First trip on the primary: CLAIM the swap synchronously (so a
                    # concurrent trip in the same window sees the claim and does NOT
                    # re-announce — the pinned "switching to …" line fires once, as the
                    # old run_ordered's single ``await switch()`` did), then build the
                    # backup LAZILY and swap the shared route wholesale. An unusable
                    # fallback (missing key) releases the claim, is noted, and the run
                    # dies honestly — the backup is never called.
                    state.switched = True  # claim before the await; a concurrent trip observes it
                    try:
                        fallback_target = await fallback_factory()
                    except SetupFault as unusable:
                        state.switched = False  # release: nobody swapped, honest death
                        first = str(unusable).splitlines()[0].removeprefix("error: ")
                        note(f"fallback model unusable — {first}")
                        raise CircuitOpenTransport(
                            tripped.message,
                            trip_id=tripped.trip_id,
                            call_id=exc.call_id,
                            switched=False,
                        ) from exc
                    announce(
                        f"{ref.provider} looks down "
                        f"({breaker.limit} consecutive transport failures) — "
                        f"switching to {fallback_ref} for the rest of the run"
                    )
                    state.on_fallback = True
                raise CircuitOpenTransport(
                    tripped.message,
                    trip_id=tripped.trip_id,
                    call_id=exc.call_id,
                    switched=state.switched and not on_fb,
                ) from exc
            else:
                if reply is not None:
                    breaker.reset(target_ref)
                return reply

        return wrapped

    return decorate


class ResilientChatModel:
    """A chat model made resilient by COMPOSED combinators — the composition-root
    replacement for ``AdmittedChatModel`` (``models/admission.py``).

    It routes both entry points (the solo ``complete`` and the coalescer's lazy
    ``complete_deferred``) through ONE shared ``rate_limited`` gate and ONE shared
    ``circuit_broken`` breaker, so an exhausted retry ladder is a single per-ref
    streak increment however the call arrived — and a coalesced flight of K waiters
    behind one packed ``call_id`` still counts once. It implements the
    ``DeferredChatModel`` shape structurally (``ref``/``complete``/
    ``complete_deferred``) so the coalescer nests OUTSIDE it and never re-admits.

    The wholesale fallback swap (retiring ``make_failover``) rides the shared
    ``_Route``: when ``fallback_factory`` is supplied, the breaker's trip builds the
    backup adapter ONCE (lazily, both entry points share it), swaps ``route``, and
    the runner replays its held window onto the backup. The composition root reads
    ``.route`` to keep the answered-per-model receipt (item 11) honest.
    """

    __slots__ = ("_complete", "_complete_deferred", "_fallback_ref", "_inner", "_route")

    # Declared so the composed-decorator types survive (``rate_limited``'s type
    # parameters live only in its return, so pyright cannot re-infer them at the
    # application site — the annotations pin what each entry point resolves to).
    _inner: ChatModel
    _route: _Route
    _fallback_ref: ModelRef | None
    _complete: Callable[[CompletionRequest], Awaitable[str]]
    _complete_deferred: Callable[[Callable[[], CompletionRequest | None]], Awaitable[str | None]]

    def __init__(
        self,
        inner: ChatModel,
        *,
        breaker: Breaker,
        concurrency: int,
        cooldown: Cooldown,
        fallback_factory: Callable[[], Awaitable[ChatModel]] | None = None,
        fallback_ref: ModelRef | None = None,
        announce: Callable[[str], None] = _noop,
        note: Callable[[str], None] = _noop,
    ) -> None:
        self._inner = inner
        self._fallback_ref = fallback_ref
        # ONE semaphore and ONE route shared by both entry points: the solo and
        # deferred paths must count against the same concurrency budget and, once
        # fallback lands, swap together.
        semaphore = asyncio.Semaphore(concurrency)
        route = _Route()
        self._route = route

        # The backup ADAPTER (budget → provider) is built once, on the first trip,
        # and memoised so both entry points share it — keys/login are checked here,
        # so an unusable fallback surfaces as circuit_broken's "unusable" note.
        backup_holder: list[ChatModel] = []
        backup_lock = asyncio.Lock()

        async def ensure_backup() -> ChatModel:
            async with backup_lock:
                if not backup_holder:
                    assert fallback_factory is not None  # armed ⟹ factory present
                    backup_holder.append(await fallback_factory())
                return backup_holder[0]

        async def build_fb_complete() -> Callable[[CompletionRequest], Awaitable[str]]:
            return (await ensure_backup()).complete

        async def build_fb_deferred() -> Callable[
            [Callable[[], CompletionRequest | None]], Awaitable[str | None]
        ]:
            backup = await ensure_backup()

            async def deferred_fb(request: Callable[[], CompletionRequest | None]) -> str | None:
                prepared = request()
                if prepared is None:
                    return None
                preflight_chat(backup, prepared)
                return await backup.complete(prepared)

            return deferred_fb

        async def deferred(request: Callable[[], CompletionRequest | None]) -> str | None:
            # Build the packed/solo request only after admission is granted, so a
            # coalescer can drop cancelled waiters or honor a stop while it sat
            # behind the gate — exactly ``AdmittedChatModel.complete_deferred``.
            prepared = request()
            if prepared is None:
                return None
            preflight_chat(inner, prepared)
            return await inner.complete(prepared)

        armed = fallback_factory is not None
        self._complete = rate_limited(
            concurrency=concurrency, cooldown=cooldown, semaphore=semaphore
        )(
            circuit_broken(
                breaker,
                ref=inner.ref,
                fallback_factory=build_fb_complete if armed else None,
                fallback_ref=fallback_ref,
                announce=announce,
                note=note,
                route=route,
            )(inner.complete)
        )
        self._complete_deferred = rate_limited(
            concurrency=concurrency, cooldown=cooldown, semaphore=semaphore
        )(
            circuit_broken(
                breaker,
                ref=inner.ref,
                fallback_factory=build_fb_deferred if armed else None,
                fallback_ref=fallback_ref,
                announce=announce,
                note=note,
                route=route,
            )(deferred)
        )

    @property
    def ref(self) -> ModelRef:
        return self._inner.ref

    @property
    def inner(self) -> ChatModel:
        """The wrapped (budget → adapter) chat model — the construction tests
        peel the wire the same way they peel ``AdmittedChatModel.inner``."""
        return self._inner

    @property
    def route(self) -> _Route:
        """The shared swap state, read by the composition root for the receipt."""
        return self._route

    @property
    def fallback_ref(self) -> ModelRef | None:
        return self._fallback_ref

    async def complete(self, request: CompletionRequest) -> str:
        preflight_chat(self._inner, request)  # OUTSIDE the gate, like admission
        return await self._complete(request)

    async def complete_deferred(
        self, request: Callable[[], CompletionRequest | None]
    ) -> str | None:
        return await self._complete_deferred(request)


@dataclass(frozen=True, slots=True)
class WiredChat:
    """One verb's resilient chat model plus the seam the receipt reads — the
    composition-root replacement for ``verbs/common.py::ModelSlot``.

    The verb calls ``model`` (a plain ``ChatModel`` whose resilient stack swaps to
    the fallback UNDERNEATH it) and never branches on failover. ``route`` is the
    shared swap state ``circuit_broken`` flips on a trip; ``switched`` /
    ``answering_ref`` read it so the answered-per-model receipt (item 11) stays
    honest without the verb holding a mutable slot. ``armed`` tells the runner
    whether to hold its window for a replay at all. The model/route/refs are
    fixed once wired (frozen); only the answered tally accumulates.
    """

    model: ChatModel
    route: _Route
    primary_ref: ModelRef
    fallback_ref: ModelRef | None
    _counts: dict[str, int] = field(default_factory=dict[str, int])

    @property
    def armed(self) -> bool:
        """Whether a fallback is configured — the runner holds its window for a
        replay only when a swap could land somewhere."""
        return self.fallback_ref is not None

    @property
    def switched(self) -> bool:
        """Whether the breaker tripped and swapped to the fallback this run."""
        return self.route.switched

    def answering_ref(self) -> ModelRef:
        """The ref that answers RIGHT NOW — the fallback once the route swapped,
        else the primary. Read per item at tally time (mirrors ``slot.current.ref``)."""
        if self.route.on_fallback and self.fallback_ref is not None:
            return self.fallback_ref
        return self.primary_ref

    def tally(self, answering: ModelRef | None = None) -> None:
        """Count one answered item under the model that answered it (item 11).

        Pass the ref captured at worker ENTRY (mirroring the old ``slot.current``
        capture) so a trip on another concurrent item mid-call cannot re-attribute
        this answer to the wrong wire; omit it to read the route at tally time."""
        label = str(answering if answering is not None else self.answering_ref())
        self._counts[label] = self._counts.get(label, 0) + 1

    def receipt(self) -> str:
        split = " · ".join(
            f"{label} ×{count}"  # noqa: RUF001 — the pinned count mark (D27 rollup style)
            for label, count in self._counts.items()
        )
        return f"answers: {split}"


@dataclass(slots=True)
class Cooldown:
    """A server-backoff seam the chat rate limiter carries.

    Inert on the chat path: it records the most recent ``Retry-After`` hint (so the
    seam and its wiring exist and are tested) but never gates a call, and the chat
    ``rate_limited`` is built without a ``retry_after=`` reader so ``penalize`` is
    never even reached. A5.2's actual per-ref pacing landed on the paid outbound
    wire instead — ``OutboundCallPolicy`` (OCR/embed/STT), which is where the 429
    storms are paid for; this chat seam stays a tested-but-dormant hook.
    """

    last_hint: float | None = None

    def penalize(self, seconds: float) -> None:
        """Record a server-supplied backoff (a dormant seam on the chat path)."""
        self.last_hint = seconds


def rate_limited(
    *,
    concurrency: int,
    cooldown: Cooldown,
    retry_after: Callable[[BaseException], float | None] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Bound the number of in-flight calls with a shared semaphore — the
    combinator that RE-EXPRESSES the concurrency half of ``admitted_chat``.

    ``retry_after`` (when a wire supplies a ``Retry-After`` hint) feeds the
    ``cooldown`` seam without gating yet; the semaphore is the live protection.
    Inject ``semaphore`` to SHARE one gate across several decorated callables
    (a chat model's ``complete`` and its deferred twin must count against the
    same concurrency budget); omit it for an independent per-decorator gate.
    """
    if concurrency < 1:
        raise ValueError(f"call concurrency must be >= 1, got {concurrency}")
    gate = asyncio.Semaphore(concurrency) if semaphore is None else semaphore

    def decorate(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            async with gate:
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
