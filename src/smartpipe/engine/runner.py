"""Ordered, bounded-concurrency execution — the one primitive every per-item
verb runs on (plan/architecture.md "Execution engine").

Guarantees (all property-tested):
1. Order — outcomes yield in input order, always. Grep-shaped tools that reorder
   lines break every downstream diff/paste/log habit.
2. Boundedness — at most ``concurrency`` workers in flight; memory O(concurrency)
   regardless of input size, so it streams unbounded input the same as a batch.
3. Isolation — a worker raising ``ItemError`` yields ``Skipped`` and the run
   continues; any other exception propagates (it's a bug, crash loudly).
4. Accounting — once enough items finish, a majority-failure run halts with
   ``TooManyFailures`` rather than burning the whole input on a broken config.

The worker is injected (a first-class async function), so this module does no
I/O of its own and orders purely by arrival.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

from smartpipe.core.errors import ItemError, SetupFault, TooManyFailures, TransportError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from smartpipe.io.items import Item, ItemSource

__all__ = [
    "Done",
    "FailurePolicy",
    "ItemOutcome",
    "Skipped",
    "run_ordered",
    "should_halt",
    "should_halt_consecutive",
    "should_trip_breaker",
]

R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class Done(Generic[R]):
    index: int
    value: R


@dataclass(frozen=True, slots=True)
class Skipped:
    index: int
    reason: str
    source: ItemSource
    transport: bool = False  # the wire failed, not the content (circuit breaker input)


ItemOutcome = Done[R] | Skipped


@dataclass(frozen=True, slots=True)
class FailurePolicy:
    halt_ratio: float = 0.5
    min_sample: int = 20
    consecutive_limit: int = 5  # D18: a doomed run must not wait for the ratio
    transport_limit: int = 0  # circuit breaker: 0 = disarmed (verbs arm it with a screen)
    transport_screen: str = ""  # the rendered provider-down screen (cli/screens)


def should_halt(policy: FailurePolicy, *, total: int, skipped: int) -> bool:
    """True once enough items have finished and a majority of them failed.
    ``min_sample`` prevents a 3-item pipe halting on 2 flukes."""
    return total >= policy.min_sample and skipped > total * policy.halt_ratio


def should_halt_consecutive(policy: FailurePolicy, *, succeeded: bool, consecutive: int) -> bool:
    """D18's cost guardrail: N consecutive failures with zero successes *ever* means
    the run was doomed from item 1 — stop paying. Any success disarms this rule
    permanently (a working run with a bad patch of input must not die early)."""
    return not succeeded and consecutive >= policy.consecutive_limit


def should_trip_breaker(policy: FailurePolicy, *, transport_streak: int) -> bool:
    """The circuit breaker (problems.md #6): N consecutive wire-level failures
    (connect/timeout/5xx after retries) mean the provider is down — stop paying
    a full retry ladder per item. Unlike the consecutive rule this fires even
    late in a healthy run; any non-transport outcome proves the wire is alive
    and resets the streak. ``transport_limit=0`` disarms it."""
    return policy.transport_limit > 0 and transport_streak >= policy.transport_limit


async def run_ordered(
    items: AsyncIterator[Item],
    worker: Callable[[Item], Awaitable[R]],
    *,
    concurrency: int,
    failure_policy: FailurePolicy,
    stop: asyncio.Event | None = None,
) -> AsyncIterator[ItemOutcome[R]]:
    """``stop`` (set by the interrupt shell) halts *intake*: no new workers spawn,
    but everything already in flight completes and is emitted in order — the drain
    contract of ux.md §12.

    Intake runs as its OWN task so that waiting for the next input item never
    blocks the emission of already-completed outcomes — the streaming property
    (a live stream can pause mid-flow; results must still come out).
    """
    item_iter = aiter(items)
    pending: dict[int, asyncio.Task[ItemOutcome[R]]] = {}
    slots = asyncio.Semaphore(concurrency)
    progressed = asyncio.Event()  # set whenever intake adds a task or finishes
    intake_done = False
    next_to_emit = 0
    total = 0
    skipped = 0
    consecutive = 0
    transport_streak = 0
    succeeded = False

    def stopping() -> bool:
        return stop is not None and stop.is_set()

    async def intake() -> None:
        nonlocal intake_done
        index = 0
        try:
            while not stopping():
                await slots.acquire()
                if stopping():  # woke up into a drain — don't start new work
                    slots.release()
                    break
                try:
                    item = await anext(item_iter)
                except StopAsyncIteration:
                    slots.release()
                    break
                pending[index] = asyncio.create_task(_run_one(worker, item))
                index += 1
                progressed.set()
        finally:
            intake_done = True
            progressed.set()

    intake_task = asyncio.create_task(intake())
    try:
        while True:
            task = pending.get(next_to_emit)
            if task is None:
                if intake_done and not pending:
                    return
                progressed.clear()
                # re-check before sleeping: intake may have progressed between the
                # get() above and the clear() — a real race, so the branch can't be
                # hit deterministically in a test; excluded rather than pretended at.
                if next_to_emit in pending or (intake_done and not pending):  # pragma: no cover
                    continue  # pragma: no cover
                await progressed.wait()
                continue
            outcome = await task
            del pending[next_to_emit]
            slots.release()
            next_to_emit += 1
            yield outcome
            total += 1
            if isinstance(outcome, Skipped):
                skipped += 1
                consecutive += 1
                transport_streak = transport_streak + 1 if outcome.transport else 0
                if should_trip_breaker(failure_policy, transport_streak=transport_streak):
                    raise SetupFault(failure_policy.transport_screen)
                if should_halt(failure_policy, total=total, skipped=skipped):
                    raise TooManyFailures(skipped, total, outcome.reason)
                if should_halt_consecutive(
                    failure_policy, succeeded=succeeded, consecutive=consecutive
                ):
                    raise TooManyFailures(skipped, total, outcome.reason)
            else:
                consecutive = 0
                transport_streak = 0
                succeeded = True
    finally:
        intake_task.cancel()
        for task in pending.values():
            task.cancel()
        await asyncio.gather(intake_task, *pending.values(), return_exceptions=True)


async def _run_one(worker: Callable[[Item], Awaitable[R]], item: Item) -> ItemOutcome[R]:
    try:
        return Done(item.source.index, await worker(item))
    except ItemError as exc:
        return Skipped(
            item.source.index, str(exc), item.source, transport=isinstance(exc, TransportError)
        )
