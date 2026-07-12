"""The async half of request coalescing (item 62): queue, window, flight.

Sits inside the result cache and outside the generic call boundary
(cache → coalescer → admission → budget → adapter): a cache hit never
enqueues, and one packed flight charges ``--max-calls`` exactly once — a
batch of twelve items IS one call. All grouping/packing/salvage math is pure
(``engine/coalesce``); this module owns timing and request shape only.

Failure contract (§9 accounting honesty): recoverable packed-call or salvage
failures re-run affected members SOLO through the inner model. Fatal faults and
an open circuit fan out without multiplying doomed calls; stop/budget refusals
remain explicitly unsent. Recovery calls are never re-batched.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, assert_never

from smartpipe.core.errors import (
    CircuitOpenTransport,
    ItemError,
    RetryableError,
    SchemaRejected,
    UnsentError,
)
from smartpipe.engine.coalesce import (
    Resend,
    Salvaged,
    coalesce_key,
    eligible,
    labels,
    max_group,
    pack,
    pack_budget,
    packed_submission_tokens,
    split_reply,
)
from smartpipe.models.admission import (
    DeferredChatModel,
    OutboundCallPolicy,
    admitted_chat,
    supports_deferred_chat,
)
from smartpipe.models.base import ModelRef

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from smartpipe.engine.coalesce import BatchSettings
    from smartpipe.models.base import ChatModel, CompletionRequest

__all__ = [
    "STOPPED_BEFORE_SEND",
    "CoalescingChatModel",
    "OutboundCallPolicy",
    "PackingMeasurement",
    "packing_withheld",
    "packing_withheld_for",
]

# ux.md §12 with batching: an in-flight batch drains; queued-but-unflown
# submissions obey the stop — no new wire calls after Ctrl-C.
STOPPED_BEFORE_SEND = "run stopping — not sent"
_PACKING_WITHHOLDING_POINTS = 20


def packing_withheld(*, solo_success: int, packed_success: int) -> bool:
    """Whether measured packed success trails solo by more than 20 points."""
    if not (0 <= solo_success <= 100 and 0 <= packed_success <= 100):
        raise ValueError("success percentages must be in 0..100")
    return solo_success - packed_success > _PACKING_WITHHOLDING_POINTS


@dataclass(frozen=True, slots=True)
class PackingMeasurement:
    ref: ModelRef
    solo_success: int
    packed_success: int


# C7 live matrix, 2026-07-12: grounded solo 100%, real packed shape 0%.
_PACKING_MEASUREMENTS = (PackingMeasurement(ModelRef("ollama", "glm-5.2:cloud"), 100, 0),)


def packing_withheld_for(ref: ModelRef) -> bool:
    """Withhold only model refs whose measured gap crossed the H1c threshold."""
    return any(
        measurement.ref == ref
        and packing_withheld(
            solo_success=measurement.solo_success,
            packed_success=measurement.packed_success,
        )
        for measurement in _PACKING_MEASUREMENTS
    )


@dataclass(slots=True)
class _Pending:
    key: str
    request: CompletionRequest
    future: asyncio.Future[str]


@dataclass(slots=True)
class _Group:
    key: str
    pending: list[_Pending] = field(default_factory=list["_Pending"])
    timer: asyncio.Task[None] | None = None


class CoalescingChatModel:
    """ChatModel-shaped: eligible submissions wait a beat and fly together.

    Ineligible requests (no ``BatchHint``, media aboard, a return shape too
    wide to share) pass straight through — byte-identical to the unwrapped
    path. ``sleep`` is injectable so tests run on a fake clock.
    """

    def __init__(
        self,
        inner: ChatModel,
        *,
        settings: BatchSettings,
        stop: asyncio.Event | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        budget_tokens: int | None = None,  # test seam; None = the provider's pack budget
        calls: OutboundCallPolicy | None = None,
    ) -> None:
        if supports_deferred_chat(inner):
            if calls is not None:
                raise ValueError("an admitted chat model cannot take a second call policy")
            self.inner: DeferredChatModel = inner
        else:
            admitted = admitted_chat(inner, OutboundCallPolicy() if calls is None else calls)
            assert supports_deferred_chat(admitted)
            self.inner = admitted
        self.settings = settings
        self.stop = stop
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep if sleep is None else sleep
        self._groups: dict[str, _Group] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._budget_tokens = (
            pack_budget(inner.ref.provider) if budget_tokens is None else budget_tokens
        )
        self.packed_items = 0  # members submitted in packed calls (success or failure)
        self.packed_calls = 0  # packed requests attempted at the actual call boundary
        self.solo_recoveries = 0  # original requests resent after a packed attempt
        self._closed = False

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def complete(self, request: CompletionRequest) -> str:
        if self._closed or self._stopping():
            raise UnsentError(STOPPED_BEFORE_SEND)
        if packing_withheld_for(self.ref) or not eligible(request, self.settings.size):
            return await self.inner.complete(request)
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        entry = self._enqueue(request, future)
        try:
            return await future
        except asyncio.CancelledError:
            self._remove_pending(entry)
            raise

    @property
    def pending_groups(self) -> int:
        return len(self._groups)

    @property
    def pending_tasks(self) -> int:
        return len(self._tasks)

    async def aclose(self) -> None:
        """Stop queued work, then join every timer/flight before the container
        closes the underlying HTTP client. In-flight calls drain."""
        if self._closed:
            return
        self._closed = True
        groups = tuple(self._groups.values())
        self._groups.clear()
        for group in groups:
            if group.timer is not None:
                group.timer.cancel()
            for entry in group.pending:
                _resolve_error(entry.future, UnsentError(STOPPED_BEFORE_SEND))
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    def _stopping(self) -> bool:
        return self.stop is not None and self.stop.is_set()

    def _enqueue(self, request: CompletionRequest, future: asyncio.Future[str]) -> _Pending:
        key = coalesce_key(request)
        entry = _Pending(key, request, future)
        group = self._groups.get(key)
        if (
            group is not None
            and packed_submission_tokens(
                tuple(candidate.request for candidate in (*group.pending, entry))
            )
            > self._budget_tokens
        ):
            self._dispatch(key)  # full by tokens — fly what's queued, start fresh
            group = None
        if group is None:
            group = _Group(key)
            self._groups[key] = group
            group.timer = self._spawn(self._window(group))
        group.pending.append(entry)
        if len(group.pending) >= max_group(request.json_schema, self.settings.size):
            self._dispatch(key)
        return entry

    def _remove_pending(self, entry: _Pending) -> None:
        group = self._groups.get(entry.key)
        if group is None:
            return  # already dispatched; _fly filters the cancelled future again
        group.pending = [candidate for candidate in group.pending if candidate is not entry]
        if group.pending:
            return
        self._groups.pop(entry.key, None)
        if group.timer is not None:
            group.timer.cancel()

    def _spawn(self, work: Awaitable[None]) -> asyncio.Task[None]:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _window(self, group: _Group) -> None:
        """The coalesce window: when it elapses first, the group flies as-is —
        streams must stay live, so nothing waits longer than this."""
        await self._sleep(self.settings.window_seconds)
        if self._groups.get(group.key) is group:
            self._dispatch(group.key, cancel_timer=False)

    def _dispatch(self, key: str, *, cancel_timer: bool = True) -> None:
        group = self._groups.pop(key, None)
        if group is None:  # pragma: no cover — belt: dispatch races are benign
            return
        if cancel_timer and group.timer is not None:
            group.timer.cancel()
        self._spawn(self._fly(tuple(group.pending)))

    async def _fly(self, pending: tuple[_Pending, ...]) -> None:
        chosen = self._live(pending)
        packed_flight = False

        def send() -> CompletionRequest | None:
            nonlocal chosen, packed_flight
            chosen = self._live(pending)  # cancellations while waiting never leave the machine
            if not chosen:
                return None
            if self._stopping():
                self._stop(chosen)
                return None
            if len(chosen) == 1:
                return chosen[0].request
            packed_flight = True
            self.packed_calls += 1
            self.packed_items += len(chosen)
            return pack(tuple(entry.request for entry in chosen))

        try:
            reply = await self.inner.complete_deferred(send)
        except asyncio.CancelledError:
            self._stop(chosen)
            raise
        except CircuitOpenTransport as fault:
            self._fanout(chosen, fault)
            return
        except SchemaRejected as fault:
            if packed_flight:
                await self._recover(chosen)
            else:
                self._fanout(chosen, fault)
            return
        except RetryableError as fault:
            # The adapter already spent its one bounded retry ladder.  Replaying
            # K solos would amplify a provider-wide 429/outage into K more
            # ladders; one actual-call failure fans to the K item waiters.
            self._fanout(chosen, fault)
            return
        except ItemError as fault:
            if packed_flight:
                await self._recover(chosen)
            else:
                self._fanout(chosen, fault)
            return
        except Exception as fault:  # fatal auth/model/setup faults and bugs
            self._fanout(chosen, fault)
            return
        if reply is None or not chosen:
            return
        if not packed_flight:
            _resolve(chosen[0].future, reply)
            return
        base = chosen[0].request
        outcomes = split_reply(reply, labels(len(chosen)), base.json_schema)
        recoveries: list[_Pending] = []
        for entry, outcome in zip(chosen, outcomes, strict=True):
            match outcome:
                case Salvaged(reply=text):
                    _resolve(entry.future, text)
                case Resend():
                    recoveries.append(entry)
                case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                    assert_never(unreachable)
        await self._recover(tuple(recoveries))

    async def _recover(self, pending: tuple[_Pending, ...]) -> None:
        if not pending:
            return
        outcomes = await asyncio.gather(
            *(self._solo(entry, recovery=True) for entry in pending),
            return_exceptions=True,
        )
        trip = next(
            (outcome for outcome in outcomes if isinstance(outcome, CircuitOpenTransport)),
            None,
        )
        if trip is not None:
            self._fanout(pending, trip)

    async def _solo(self, entry: _Pending, *, recovery: bool = False) -> None:
        if entry.future.done():
            return

        def send() -> CompletionRequest | None:
            if entry.future.done():
                return None
            if self._stopping():
                _resolve_error(entry.future, UnsentError(STOPPED_BEFORE_SEND))
                return None
            if recovery:
                self.solo_recoveries += 1
            return entry.request

        try:
            reply = await self.inner.complete_deferred(send)
        except asyncio.CancelledError:
            _resolve_error(entry.future, UnsentError(STOPPED_BEFORE_SEND))
            raise
        except CircuitOpenTransport:
            raise
        except Exception as fault:
            _resolve_error(entry.future, fault)
        else:
            if reply is not None:
                _resolve(entry.future, reply)

    def _live(self, pending: tuple[_Pending, ...]) -> tuple[_Pending, ...]:
        return tuple(entry for entry in pending if not entry.future.done())

    @staticmethod
    def _fanout(pending: tuple[_Pending, ...], fault: BaseException) -> None:
        for entry in pending:
            _resolve_error(entry.future, fault)

    @staticmethod
    def _stop(pending: tuple[_Pending, ...]) -> None:
        for entry in pending:
            _resolve_error(entry.future, UnsentError(STOPPED_BEFORE_SEND))


def _resolve(future: asyncio.Future[str], reply: str) -> None:
    if not future.done():
        future.set_result(reply)


def _resolve_error(future: asyncio.Future[str], fault: BaseException) -> None:
    if not future.done():
        future.set_exception(fault)
