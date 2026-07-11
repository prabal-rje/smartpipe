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
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from smartpipe.core.errors import (
    CircuitOpenTransport,
    ExcludedError,
    ItemError,
    LateSetupFault,
    RetryableError,
    SetupFault,
    SourceCounts,
    TooManyFailures,
    UnsentError,
    UsageFault,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

    from smartpipe.io.items import Item, ItemSource

__all__ = [
    "DEFAULT_BREAKER_LIMIT",
    "Done",
    "FailurePolicy",
    "ItemOutcome",
    "Skipped",
    "resolve_breaker_limit",
    "run_ordered",
    "should_halt",
    "should_halt_consecutive",
    "should_trip_breaker",
]

R = TypeVar("R")

DEFAULT_BREAKER_LIMIT = 5


class HaltSourceCounter(Protocol):
    """Optional logical-source fold used only when a run halts mid-prefetch."""

    def skip(self, source: ItemSource, *, failed: bool) -> None: ...

    @property
    def counts(self) -> SourceCounts: ...


def resolve_breaker_limit(raw: str) -> int:
    """Parse the shared SMARTPIPE_BREAKER value once at each composition edge."""
    cleaned = raw.strip()
    if not cleaned:
        return DEFAULT_BREAKER_LIMIT
    if cleaned.isdigit():
        return int(cleaned)
    raise UsageFault(f"SMARTPIPE_BREAKER must be a whole number >= 0, got {cleaned!r}")


@dataclass(frozen=True, slots=True)
class Done(Generic[R]):
    index: int
    value: R


@dataclass(frozen=True, slots=True)
class Skipped:
    index: int
    reason: str
    source: ItemSource
    transport: bool = False  # provider availability failed (circuit-breaker input)
    transport_series: int | None = None  # one completion-ordered policy streak
    transport_call: int | None = None  # one actual call may fan to K item waiters
    circuit_trip: int | None = None  # one real-call trip may fan to K packed waiters
    failed: bool = True  # False means accepted but intentionally not submitted


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
    (429/connect/timeout/5xx after retries) mean the provider is unavailable —
    stop paying a full retry ladder per item. Unlike the consecutive rule this
    fires even late in a healthy run; any non-availability outcome proves the
    wire is alive and resets the streak. ``transport_limit=0`` disarms it."""
    return policy.transport_limit > 0 and transport_streak >= policy.transport_limit


async def run_ordered(
    items: AsyncIterator[Item],
    worker: Callable[[Item], Awaitable[R]],
    *,
    concurrency: int,
    failure_policy: FailurePolicy,
    stop: asyncio.Event | None = None,
    failover: Callable[[], Awaitable[bool]] | None = None,
    halt_sources: HaltSourceCounter | None = None,
) -> AsyncIterator[ItemOutcome[R]]:
    """``stop`` (set by the interrupt shell) halts *intake*: no new workers spawn,
    but everything already in flight completes and is emitted in order — the drain
    contract of ux.md §12.

    Intake runs as its OWN task so that waiting for the next input item never
    blocks the emission of already-completed outcomes — the streaming property
    (a live stream can pause mid-flow; results must still come out).

    ``failover`` (fallback-model): while it is armed, transport skips are HELD
    rather than emitted — they may yet be answered. At the breaker threshold the
    hook runs once: True means the caller switched models, so the held window
    re-runs through ``worker`` (in order) and the run continues; False (nothing
    configured / fallback unusable) flushes the held skips and dies on the
    provider-down screen. One fallback, then honest death — a second trip goes
    through the ordinary breaker path above.
    """
    item_iter = aiter(items)
    pending: dict[int, asyncio.Task[ItemOutcome[R]]] = {}
    originals: dict[int, Item] = {}  # in-flight items, kept for the failover re-run
    slots = asyncio.Semaphore(concurrency)
    intake_allowed = asyncio.Event()
    intake_allowed.set()
    progressed = asyncio.Event()  # set whenever intake adds a task or finishes
    intake_done = False
    intake_fault: Exception | None = None
    consumed = 0
    next_to_emit = 0
    emitted_succeeded = 0
    emitted_skipped = 0
    failures = 0
    policy_total = 0
    policy_failures = 0
    consecutive = 0
    transport_streak = 0
    transport_calls: set[int] = set()
    ever_succeeded = False
    failover_pending = failover if failure_policy.transport_limit > 0 else None
    window: list[tuple[Item, Skipped]] = []  # the held transport streak
    handled_trips: set[int] = set()
    late_replays: list[Item] = []

    def availability_calls(held: list[tuple[Item, Skipped]]) -> int:
        """Count actual failed calls, not coalesced item waiters.

        Policy-owned outcomes carry a call id shared by every waiter behind one
        packed flight. Legacy/unadmitted workers carry no id, so each outcome
        remains one call for backward-compatible direct runner use.
        """
        identified = {
            skip.transport_call for _item, skip in held if skip.transport_call is not None
        }
        unidentified = sum(skip.transport_call is None for _item, skip in held)
        return len(identified) + unidentified

    def stopping() -> bool:
        return stop is not None and stop.is_set()

    def account(outcome: ItemOutcome[R], *, breaker: bool = True) -> None:
        """Halt bookkeeping for one EMITTED outcome; raises when a rule fires."""
        nonlocal consecutive, emitted_skipped, emitted_succeeded
        nonlocal ever_succeeded, failures, policy_failures, policy_total
        nonlocal transport_streak
        if isinstance(outcome, Skipped):
            emitted_skipped += 1
            if not outcome.failed:
                if not outcome.transport:
                    policy_total += 1
                return
            failures += 1
            if outcome.transport:
                if outcome.transport_call is None:
                    transport_streak += 1  # legacy direct workers: one item = one call
                elif outcome.transport_call not in transport_calls:
                    transport_calls.add(outcome.transport_call)
                    transport_streak += 1
                if breaker and should_trip_breaker(
                    failure_policy, transport_streak=transport_streak
                ):
                    raise SetupFault(failure_policy.transport_screen)
                # Availability is governed by actual outbound calls. A packed
                # call may fan to many item waiters, so none of those waiters
                # participate in the content-failure ratio/consecutive rules.
                return
            policy_total += 1
            policy_failures += 1
            consecutive += 1
            transport_streak = 0
            transport_calls.clear()
            source_counts = SourceCounts(
                succeeded=emitted_succeeded,
                skipped=emitted_skipped,
                failed=failures,
            )
            if should_halt(
                failure_policy,
                total=policy_total,
                skipped=policy_failures,
            ):
                raise TooManyFailures(
                    policy_failures,
                    policy_total,
                    outcome.reason,
                    source_counts=source_counts,
                )
            if should_halt_consecutive(
                failure_policy, succeeded=ever_succeeded, consecutive=consecutive
            ):
                raise TooManyFailures(
                    policy_failures,
                    policy_total,
                    outcome.reason,
                    source_counts=source_counts,
                )
        else:
            emitted_succeeded += 1
            policy_total += 1
            consecutive = 0
            transport_streak = 0
            transport_calls.clear()
            ever_succeeded = True

    async def replay(replay_items: Sequence[Item]) -> AsyncIterator[ItemOutcome[R]]:
        """Re-run a fallback window concurrently while preserving input order."""
        async for retry in _run_replay_ordered(
            replay_items,
            worker,
            concurrency=concurrency,
        ):
            yield retry
            circuit_open = isinstance(retry, Skipped) and retry.circuit_trip is not None
            account(retry, breaker=not circuit_open)
            if circuit_open:
                raise SetupFault(failure_policy.transport_screen)

    async def report_skips(
        held: Sequence[tuple[Item, Skipped]],
    ) -> AsyncIterator[Skipped]:
        """Report a terminal availability window exactly once before exit 2."""
        for _held_item, held_skip in held:
            yield held_skip
            account(held_skip, breaker=False)

    async def collect_trip_waiters(trip_id: int) -> list[tuple[Item, Skipped]]:
        """Collect adjacent waiters from the actual call that opened the circuit.

        Ordered emission reaches the trip marker at the first waiter, while the
        rest of that packed call still sit in ``pending``. Gathering the whole
        series before switching lets the fallback re-form the same packed-call
        shape instead of replaying the marker and its siblings in two waves.
        """
        nonlocal next_to_emit
        collected: list[tuple[Item, Skipped]] = []
        while (task := pending.get(next_to_emit)) is not None:
            candidate = await task
            if not (isinstance(candidate, Skipped) and candidate.transport_series == trip_id):
                break
            candidate_item = originals.pop(next_to_emit)
            del pending[next_to_emit]
            slots.release()
            next_to_emit += 1
            collected.append((candidate_item, candidate))
        return collected

    def settle_pending_sources(
        base: SourceCounts | None,
        *,
        implicit_current_failure: bool,
    ) -> SourceCounts:
        """Fold work already accepted when a fatal run-level decision lands."""
        if halt_sources is not None:
            for position, pending_item in originals.items():
                halt_sources.skip(
                    pending_item.source,
                    failed=_completed_failure(pending[position]),
                )
            return halt_sources.counts
        if base is None:
            current = int(implicit_current_failure)
            base = SourceCounts(
                succeeded=emitted_succeeded,
                skipped=emitted_skipped + current,
                failed=failures + current,
            )
            represented_pending = current
        else:
            represented_pending = max(
                0,
                base.total - (emitted_succeeded + emitted_skipped),
            )
        prefetched = consumed - base.total
        if prefetched < 0:
            raise ValueError("halt source counts exceed runner-consumed items")
        pending_positions = sorted(originals)[represented_pending:]
        completed_failures = sum(
            _completed_failure(pending[position]) for position in pending_positions[:prefetched]
        )
        return SourceCounts(
            succeeded=base.succeeded,
            skipped=base.skipped + prefetched,
            failed=base.failed + completed_failures,
        )

    async def intake() -> None:
        nonlocal consumed, intake_done, intake_fault
        index = 0
        try:
            while not stopping():
                await slots.acquire()
                await intake_allowed.wait()
                if stopping():  # woke up into a drain — don't start new work
                    slots.release()
                    break
                try:
                    item = await anext(item_iter)
                except StopAsyncIteration:
                    slots.release()
                    break
                except Exception as exc:
                    slots.release()
                    intake_fault = exc
                    break
                consumed += 1
                originals[index] = item
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
                    async for retry in replay(late_replays):
                        yield retry
                    late_replays.clear()
                    for _held_item, held_skip in window:  # a trailing streak still reports
                        yield held_skip
                        account(held_skip, breaker=False)
                    if intake_fault is not None:
                        raise intake_fault
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
            item = originals.pop(next_to_emit)
            del pending[next_to_emit]
            slots.release()
            next_to_emit += 1
            if (
                isinstance(outcome, Skipped)
                and outcome.transport_series is not None
                and outcome.transport_series in handled_trips
            ):
                # This primary failure completed before the marker but sits at
                # a higher input index, so ordered emission reaches it only
                # after the provider switch. Accumulate adjacent waiters so a
                # batching worker can coalesce their fallback calls again.
                late_replays.append(item)
                continue
            async for retry in replay(late_replays):
                yield retry
            late_replays.clear()
            if isinstance(outcome, Skipped) and outcome.circuit_trip is not None:
                trip_id = outcome.circuit_trip
                held = [*window, (item, outcome)]
                window.clear()
                intake_allowed.clear()  # freeze primary intake before collecting this trip
                held.extend(await collect_trip_waiters(trip_id))
                if failover_pending is None:
                    async for held_skip in report_skips(held):
                        yield held_skip
                    raise SetupFault(failure_policy.transport_screen)
                switch = failover_pending
                failover_pending = None  # one fallback, then honest death
                try:
                    switched = await switch()
                except SetupFault:
                    async for held_skip in report_skips(held):
                        yield held_skip
                    raise
                if not switched:
                    async for held_skip in report_skips(held):
                        yield held_skip
                    raise SetupFault(failure_policy.transport_screen)
                handled_trips.add(trip_id)
                async for retry in replay([held_item for held_item, _held_skip in held]):
                    yield retry
                intake_allowed.set()
                continue
            if failover_pending is not None and isinstance(outcome, Skipped) and outcome.transport:
                window.append((item, outcome))
                if availability_calls(window) < failure_policy.transport_limit:
                    continue
                switch = failover_pending
                failover_pending = None  # one fallback, then honest death
                held = list(window)
                window.clear()
                intake_allowed.clear()  # replay owns the outbound-call window
                try:
                    switched = await switch()
                except SetupFault:
                    async for held_skip in report_skips(held):
                        yield held_skip
                    raise
                if not switched:
                    # the window still reports, but no halt rule may outrun the
                    # provider-down screen — this death IS the breaker's verdict
                    async for held_skip in report_skips(held):
                        yield held_skip
                    raise SetupFault(failure_policy.transport_screen)
                handled_trips.update(
                    held_skip.transport_series
                    for _held_item, held_skip in held
                    if held_skip.transport_series is not None
                )
                async for retry in replay([held_item for held_item, _held_skip in held]):
                    yield retry
                intake_allowed.set()
                continue
            for _held_item, held_skip in window:  # the wire answered — flush the streak
                yield held_skip
                account(held_skip, breaker=False)
            window.clear()
            yield outcome
            account(outcome)
    except TooManyFailures as halt:
        # Freeze intake before attaching source accounting. The halt's display
        # denominator may be another unit (join pairs), so it never participates
        # in these item-count invariants.
        intake_task.cancel()
        await asyncio.gather(intake_task, return_exceptions=True)
        settled_counts = settle_pending_sources(
            halt.source_counts,
            implicit_current_failure=halt.source_counts is None,
        )
        raise TooManyFailures(
            failed=halt.failed,
            total=halt.total,
            last_reason=halt.last_reason,
            source_counts=settled_counts,
        ) from None
    except SetupFault as fault:
        intake_task.cancel()
        await asyncio.gather(intake_task, return_exceptions=True)
        base = (
            fault.source_counts
            if isinstance(fault, LateSetupFault)
            else SourceCounts(
                succeeded=emitted_succeeded,
                skipped=emitted_skipped,
                failed=failures,
            )
        )
        counts = settle_pending_sources(base, implicit_current_failure=False)
        raise LateSetupFault(str(fault), source_counts=counts) from None
    finally:
        intake_task.cancel()
        for task in pending.values():
            task.cancel()
        await asyncio.gather(intake_task, *pending.values(), return_exceptions=True)


def _completed_failure(task: asyncio.Task[ItemOutcome[R]]) -> bool:
    """Whether a prefetched task settled as a real failure before cancellation."""
    if not task.done() or task.cancelled():
        return False
    try:
        outcome = task.result()
    except Exception:
        return True
    return isinstance(outcome, Skipped) and outcome.failed


async def _run_replay_ordered(
    items: Sequence[Item],
    worker: Callable[[Item], Awaitable[R]],
    *,
    concurrency: int,
) -> AsyncIterator[ItemOutcome[R]]:
    """Bound fallback work without serializing it; yield in source order."""
    item_iter = iter(items)
    pending: deque[asyncio.Task[ItemOutcome[R]]] = deque()
    for _slot in range(concurrency):
        try:
            item = next(item_iter)
        except StopIteration:
            break
        pending.append(asyncio.create_task(_run_one(worker, item)))
    try:
        while pending:
            outcome = await pending.popleft()
            yield outcome
            if not pending:
                for _slot in range(concurrency):
                    try:
                        item = next(item_iter)
                    except StopIteration:
                        break
                    pending.append(asyncio.create_task(_run_one(worker, item)))
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def _run_one(worker: Callable[[Item], Awaitable[R]], item: Item) -> ItemOutcome[R]:
    try:
        return Done(item.source.index, await worker(item))
    except ItemError as exc:
        return Skipped(
            item.source.index,
            str(exc),
            item.source,
            transport=isinstance(exc, RetryableError),
            transport_series=exc.series_id if isinstance(exc, RetryableError) else None,
            transport_call=exc.call_id if isinstance(exc, RetryableError) else None,
            circuit_trip=exc.trip_id if isinstance(exc, CircuitOpenTransport) else None,
            failed=not isinstance(exc, (ExcludedError, UnsentError)),
        )
