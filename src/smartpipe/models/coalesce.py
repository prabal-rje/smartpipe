"""The async half of request coalescing (item 62): queue, window, flight.

Sits INSIDE the result cache and OUTSIDE the call budget
(cache → coalescer → budget → adapter): a cache hit never enqueues, and one
packed flight charges ``--max-calls`` exactly once — a batch of twelve items
IS one call. All grouping/packing/salvage math is pure (``engine/coalesce``);
this module owns time and the wire only.

Failure contract (§9 accounting honesty): when a packed call fails — transport,
budget, a strict wire balking — every member re-runs SOLO through the inner
model, so every skip the runner sees is backed by a real call and the circuit
breaker/retry machinery keep their per-call meaning. Salvage failures (missing
or invalid keys) re-run solo the same way; failures are never re-batched.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, assert_never

from smartpipe.core.errors import ItemError
from smartpipe.engine.coalesce import (
    Resend,
    Salvaged,
    coalesce_key,
    eligible,
    labels,
    max_group,
    pack,
    pack_budget,
    split_reply,
    submission_tokens,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from smartpipe.engine.coalesce import BatchSettings
    from smartpipe.models.base import ChatModel, CompletionRequest, ModelRef

__all__ = ["STOPPED_BEFORE_SEND", "CoalescingChatModel"]

# ux.md §12 with batching: an in-flight batch drains; queued-but-unflown
# submissions obey the stop — no new wire calls after Ctrl-C.
STOPPED_BEFORE_SEND = "run stopping — not sent"


@dataclass(slots=True)
class _Pending:
    request: CompletionRequest
    future: asyncio.Future[str]
    tokens: int


@dataclass(slots=True)
class _Group:
    key: str
    pending: list[_Pending] = field(default_factory=list["_Pending"])
    tokens: int = 0
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
    ) -> None:
        self.inner = inner
        self.settings = settings
        self.stop = stop
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep if sleep is None else sleep
        self._groups: dict[str, _Group] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._budget_tokens = (
            pack_budget(inner.ref.provider) if budget_tokens is None else budget_tokens
        )
        self.batched_items = 0  # answers that came out of packed calls
        self.packed_calls = 0  # packed requests that actually flew

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def complete(self, request: CompletionRequest) -> str:
        if not eligible(request, self.settings.size):
            return await self.inner.complete(request)
        if self._stopping():
            raise ItemError(STOPPED_BEFORE_SEND)
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._enqueue(request, future)
        return await future

    def _stopping(self) -> bool:
        return self.stop is not None and self.stop.is_set()

    def _enqueue(self, request: CompletionRequest, future: asyncio.Future[str]) -> None:
        key = coalesce_key(request)
        entry = _Pending(request, future, submission_tokens(request))
        group = self._groups.get(key)
        if group is not None and group.tokens + entry.tokens > self._budget_tokens:
            self._dispatch(key)  # full by tokens — fly what's queued, start fresh
            group = None
        if group is None:
            group = _Group(key)
            self._groups[key] = group
            group.timer = self._spawn(self._window(group))
        group.pending.append(entry)
        group.tokens += entry.tokens
        if len(group.pending) >= max_group(request.json_schema, self.settings.size):
            self._dispatch(key)

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
        if self._stopping():
            # queued-but-unflown obeys the stop like today's pending items
            for entry in pending:
                _resolve_error(entry.future, ItemError(STOPPED_BEFORE_SEND))
            return
        if len(pending) == 1:
            await self._solo(pending[0])  # a group of one flies as the ORIGINAL request
            return
        packed = pack(tuple(entry.request for entry in pending))
        try:
            reply = await self.inner.complete(packed)
        except asyncio.CancelledError:
            for entry in pending:
                _resolve_error(entry.future, ItemError(STOPPED_BEFORE_SEND))
            raise
        except ItemError:
            # the packed call failed — every member re-runs solo so each outcome
            # is backed by a real call (breaker/budget keep per-call meaning)
            for entry in pending:
                await self._solo(entry)
            return
        except Exception as fault:  # fatal (SetupFault, bugs): the waiters crash loudly
            for entry in pending:
                _resolve_error(entry.future, fault)
            return
        self.packed_calls += 1
        base = pending[0].request
        outcomes = split_reply(reply, labels(len(pending)), base.json_schema)
        for entry, outcome in zip(pending, outcomes, strict=True):
            match outcome:
                case Salvaged(reply=text):
                    self.batched_items += 1
                    _resolve(entry.future, text)
                case Resend():
                    await self._solo(entry)  # the named item retries solo, never re-batched
                case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                    assert_never(unreachable)

    async def _solo(self, entry: _Pending) -> None:
        if entry.future.done():  # pragma: no cover — waiter already cancelled
            return
        if self._stopping():
            _resolve_error(entry.future, ItemError(STOPPED_BEFORE_SEND))
            return
        try:
            reply = await self.inner.complete(entry.request)
        except asyncio.CancelledError:
            _resolve_error(entry.future, ItemError(STOPPED_BEFORE_SEND))
            raise
        except Exception as fault:
            _resolve_error(entry.future, fault)
        else:
            _resolve(entry.future, reply)


def _resolve(future: asyncio.Future[str], reply: str) -> None:
    if not future.done():
        future.set_result(reply)


def _resolve_error(future: asyncio.Future[str], fault: BaseException) -> None:
    if not future.done():
        future.set_exception(fault)
