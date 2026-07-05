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

from sempipe.core.errors import ItemError, TooManyFailures

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from sempipe.io.items import Item, ItemSource

__all__ = [
    "Done",
    "FailurePolicy",
    "ItemOutcome",
    "Skipped",
    "run_ordered",
    "should_halt",
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


ItemOutcome = Done[R] | Skipped


@dataclass(frozen=True, slots=True)
class FailurePolicy:
    halt_ratio: float = 0.5
    min_sample: int = 20


def should_halt(policy: FailurePolicy, *, total: int, skipped: int) -> bool:
    """True once enough items have finished and a majority of them failed.
    ``min_sample`` prevents a 3-item pipe halting on 2 flukes."""
    return total >= policy.min_sample and skipped > total * policy.halt_ratio


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
                if should_halt(failure_policy, total=total, skipped=skipped):
                    raise TooManyFailures(skipped, total, outcome.reason)
    finally:
        intake_task.cancel()
        for task in pending.values():
            task.cancel()
        await asyncio.gather(intake_task, *pending.values(), return_exceptions=True)


async def _run_one(worker: Callable[[Item], Awaitable[R]], item: Item) -> ItemOutcome[R]:
    try:
        return Done(item.source.index, await worker(item))
    except ItemError as exc:
        return Skipped(item.source.index, str(exc), item.source)
