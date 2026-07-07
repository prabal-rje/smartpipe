from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from smartpipe.core.errors import ItemError, TooManyFailures
from smartpipe.engine.runner import (
    Done,
    FailurePolicy,
    Skipped,
    run_ordered,
    should_halt,
)
from smartpipe.io.items import Item, ItemSource

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

    from smartpipe.engine.runner import ItemOutcome

NEVER_HALT = FailurePolicy(halt_ratio=1.0, min_sample=10**9, consecutive_limit=10**9)


def _item(index: int, text: str = "x") -> Item:
    return Item(raw=text, text=text, data=None, source=ItemSource("stdin", "-", index))


async def _stream(items: Sequence[Item]) -> AsyncIterator[Item]:
    for item in items:
        yield item


async def _collect(
    items: Sequence[Item],
    worker: Callable[[Item], Awaitable[object]],
    *,
    concurrency: int = 4,
    policy: FailurePolicy = NEVER_HALT,
) -> list[ItemOutcome[object]]:
    return [
        outcome
        async for outcome in run_ordered(
            _stream(items), worker, concurrency=concurrency, failure_policy=policy
        )
    ]


# --- ordering & basic behaviour ----------------------------------------------


async def test_empty_input_yields_nothing() -> None:
    async def worker(item: Item) -> str:
        return item.text

    assert await _collect([], worker) == []


async def test_results_are_in_input_order_despite_reversed_completion() -> None:
    # item i sleeps (N-i) ms, so they COMPLETE in reverse; output must still be in order.
    items = [_item(i, str(i)) for i in range(8)]

    async def worker(item: Item) -> str:
        await asyncio.sleep((8 - item.source.index) * 0.001)
        return item.text

    outcomes = await _collect(items, worker, concurrency=8)
    assert [o.index for o in outcomes] == list(range(8))
    assert [o.value for o in outcomes if isinstance(o, Done)] == [str(i) for i in range(8)]


async def test_item_error_becomes_skipped_and_others_continue() -> None:
    items = [_item(i) for i in range(5)]

    async def worker(item: Item) -> str:
        if item.source.index == 2:
            raise ItemError("boom on 2")
        return "ok"

    outcomes = await _collect(items, worker)
    assert isinstance(outcomes[2], Skipped)
    assert outcomes[2].reason == "boom on 2"
    assert all(isinstance(outcomes[i], Done) for i in (0, 1, 3, 4))


async def test_non_item_exception_propagates_loudly() -> None:
    items = [_item(i) for i in range(4)]

    async def worker(item: Item) -> str:
        if item.source.index == 1:
            raise ValueError("a real bug")
        return "ok"

    with pytest.raises(ValueError, match="a real bug"):
        await _collect(items, worker)


async def test_concurrency_is_bounded() -> None:
    items = [_item(i) for i in range(20)]

    @dataclass
    class Tracker:
        active: int = 0
        peak: int = 0

    tracker = Tracker()

    async def worker(item: Item) -> str:
        tracker.active += 1
        tracker.peak = max(tracker.peak, tracker.active)
        await asyncio.sleep(0.001)
        tracker.active -= 1
        return "ok"

    await _collect(items, worker, concurrency=4)
    assert tracker.peak <= 4


async def test_every_input_produces_exactly_one_outcome() -> None:
    items = [_item(i) for i in range(30)]

    async def worker(item: Item) -> str:
        if item.source.index % 3 == 0:
            raise ItemError("skip")
        return "ok"

    outcomes = await _collect(items, worker, concurrency=5)
    assert len(outcomes) == 30
    assert [o.index for o in outcomes] == list(range(30))


# --- failure policy -----------------------------------------------------------


def test_should_halt_needs_min_sample() -> None:
    policy = FailurePolicy(halt_ratio=0.5, min_sample=20)
    assert should_halt(policy, total=10, skipped=10) is False  # below sample
    assert should_halt(policy, total=20, skipped=11) is True
    assert should_halt(policy, total=20, skipped=10) is False  # exactly half is not > half


async def test_halts_past_the_failure_threshold() -> None:
    # isolate the RATIO policy: one early success disarms the consecutive rule (D18),
    # so this pins the >50%-of-≥20 behavior on a run that did work at first
    items = [_item(i) for i in range(100)]

    async def worker(item: Item) -> str:
        if item.source.index == 0:
            return "ok"
        raise ItemError(f"fail {item.source.index}")

    with pytest.raises(TooManyFailures) as excinfo:
        await _collect(items, worker, concurrency=4, policy=FailurePolicy(0.5, 20))
    assert excinfo.value.total >= 20
    assert "fail" in excinfo.value.last_reason


async def test_small_batch_does_not_halt_on_a_couple_flukes() -> None:
    # 2 of 3 fail (66%) but below min_sample, so no halt.
    items = [_item(i) for i in range(3)]

    async def worker(item: Item) -> str:
        if item.source.index < 2:
            raise ItemError("fluke")
        return "ok"

    outcomes = await _collect(items, worker, policy=FailurePolicy(0.5, 20))
    assert len(outcomes) == 3


# --- properties ---------------------------------------------------------------


@given(
    delays=st.lists(st.integers(min_value=0, max_value=5), min_size=1, max_size=25),
    concurrency=st.integers(min_value=1, max_value=8),
)
@settings(max_examples=40, deadline=None)
def test_order_preserved_for_any_completion_schedule(delays: list[int], concurrency: int) -> None:
    items = [_item(i) for i in range(len(delays))]

    async def worker(item: Item) -> int:
        await asyncio.sleep(delays[item.source.index] * 0.0005)
        return item.source.index

    outcomes = asyncio.run(_collect(items, worker, concurrency=concurrency))
    assert [o.index for o in outcomes] == list(range(len(delays)))
    assert [o.value for o in outcomes if isinstance(o, Done)] == list(range(len(delays)))


# --- stop (graceful interrupt: stop intake, drain in-flight) -------------------


async def test_stop_prevents_new_spawns_and_drains_in_flight() -> None:
    stop = asyncio.Event()
    started: list[int] = []
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def worker(item: Item) -> str:
        started.append(item.source.index)
        if len(started) == 2:
            both_started.set()
        await release.wait()
        return item.text

    outcomes: list[ItemOutcome[str]] = []
    gen = run_ordered(
        _stream([_item(i) for i in range(6)]),
        worker,
        concurrency=2,
        failure_policy=NEVER_HALT,
        stop=stop,
    )
    pull = asyncio.ensure_future(anext(gen))
    await asyncio.wait_for(both_started.wait(), timeout=2)  # first two workers running
    assert started == [0, 1]
    stop.set()  # interrupt: no NEW work may start...
    release.set()  # ...but in-flight work completes and is emitted
    outcomes.append(await pull)
    outcomes.extend([o async for o in gen])
    assert [o.index for o in outcomes] == [0, 1]  # drained in order, intake stopped
    assert started == [0, 1]  # items 2-5 never ran


async def test_stop_already_set_yields_nothing() -> None:
    stop = asyncio.Event()
    stop.set()

    async def worker(item: Item) -> str:  # pragma: no cover — must never run
        raise AssertionError("spawned despite stop")

    gen = run_ordered(
        _stream([_item(0)]), worker, concurrency=2, failure_policy=NEVER_HALT, stop=stop
    )
    assert [o async for o in gen] == []


async def test_emits_completed_results_while_intake_is_stalled() -> None:
    """THE streaming property at the engine level: a completed outcome must emit
    even though the source has more capacity and is still waiting for input.
    (Regression: the spawn loop used to block emission on ``anext``.)"""
    gate = asyncio.Event()

    async def source() -> AsyncIterator[Item]:
        yield _item(0, "first")
        await gate.wait()  # a live stream pausing — no EOF, no next item yet
        yield _item(1, "second")

    async def worker(item: Item) -> str:
        return item.text

    gen = run_ordered(source(), worker, concurrency=4, failure_policy=NEVER_HALT)
    first = await asyncio.wait_for(anext(gen), timeout=2)  # old runner hung here
    assert isinstance(first, Done) and first.value == "first"
    gate.set()
    rest = [o async for o in gen]
    assert [o.index for o in rest] == [1]


# --- D18: five consecutive failures with zero successes halt the doomed run --------


async def test_doomed_run_halts_after_five_consecutive_failures() -> None:
    items = [_item(i) for i in range(10)]

    async def worker(item: Item) -> int:
        raise ItemError("same failure")

    seen = 0
    with pytest.raises(TooManyFailures) as excinfo:
        async for _outcome in run_ordered(
            _stream(items), worker, concurrency=1, failure_policy=FailurePolicy()
        ):
            seen += 1
    assert seen == 5  # the 6th..10th items were never paid for
    assert excinfo.value.failed == 5


async def test_one_success_disarms_the_consecutive_rule_forever() -> None:
    items = [_item(i) for i in range(12)]

    async def worker(item: Item) -> int:
        if item.source.index == 0:
            return 0
        raise ItemError("bad patch of input")

    outcomes = [
        outcome
        async for outcome in run_ordered(
            _stream(items), worker, concurrency=1, failure_policy=FailurePolicy()
        )
    ]
    assert len(outcomes) == 12  # ran to completion; only the ratio policy applies now
