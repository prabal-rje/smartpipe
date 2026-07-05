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
) -> AsyncIterator[ItemOutcome[R]]:
    item_iter = aiter(items)
    pending: dict[int, asyncio.Task[ItemOutcome[R]]] = {}
    next_to_emit = 0
    next_index = 0
    exhausted = False
    total = 0
    skipped = 0
    last_reason = ""

    async def spawn() -> None:
        nonlocal next_index, exhausted
        try:
            item = await anext(item_iter)
        except StopAsyncIteration:
            exhausted = True
            return
        index = next_index
        next_index += 1
        pending[index] = asyncio.create_task(_run_one(worker, item))

    try:
        while not exhausted and len(pending) < concurrency:
            await spawn()
        while pending:
            # pending always holds a contiguous range starting at next_to_emit,
            # so this key is present; awaiting it lets the other in-flight
            # workers keep running (head-of-line emission, full concurrency).
            outcome = await pending.pop(next_to_emit)
            next_to_emit += 1
            yield outcome
            total += 1
            if isinstance(outcome, Skipped):
                skipped += 1
                last_reason = outcome.reason
                if should_halt(failure_policy, total=total, skipped=skipped):
                    raise TooManyFailures(skipped, total, last_reason)
            while not exhausted and len(pending) < concurrency:
                await spawn()
    finally:
        for task in pending.values():
            task.cancel()
        if pending:
            await asyncio.gather(*pending.values(), return_exceptions=True)


async def _run_one(worker: Callable[[Item], Awaitable[R]], item: Item) -> ItemOutcome[R]:
    try:
        return Done(item.source.index, await worker(item))
    except ItemError as exc:
        return Skipped(item.source.index, str(exc), item.source)
