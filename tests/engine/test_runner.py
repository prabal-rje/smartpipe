from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from smartpipe.core.errors import (
    CircuitOpenTransport,
    ExcludedError,
    ItemError,
    LateSetupFault,
    RetryableError,
    SetupFault,
    SourceCounts,
    TooManyFailures,
    TransportError,
    UnsentError,
    UsageFault,
)
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


async def test_unsent_item_is_skipped_without_becoming_a_failure() -> None:
    async def worker(item: Item) -> str:
        if item.source.index == 0:
            raise UnsentError("run stopping — not sent")
        raise ItemError("model rejected the item")

    outcomes = await _collect([_item(0), _item(1)], worker)
    assert [outcome.failed for outcome in outcomes if isinstance(outcome, Skipped)] == [
        False,
        True,
    ]


async def test_excluded_item_is_skipped_without_becoming_a_failure() -> None:
    async def worker(item: Item) -> str:
        if item.source.index == 0:
            raise ExcludedError("item excluded before primary model submission")
        raise ItemError("model rejected the item")

    outcomes = await _collect([_item(0), _item(1)], worker)
    assert [outcome.failed for outcome in outcomes if isinstance(outcome, Skipped)] == [
        False,
        True,
    ]


async def test_non_item_exception_propagates_loudly() -> None:
    items = [_item(i) for i in range(4)]

    async def worker(item: Item) -> str:
        if item.source.index == 1:
            raise ValueError("a real bug")
        return "ok"

    with pytest.raises(ValueError, match="a real bug"):
        await _collect(items, worker)


async def test_source_exception_propagates_after_prior_outcomes_settle() -> None:
    async def source() -> AsyncIterator[Item]:
        yield _item(0, "settled first")
        raise UsageFault("bad streamed input")

    async def worker(item: Item) -> str:
        return item.text

    outcomes: list[ItemOutcome[str]] = []
    with pytest.raises(UsageFault, match="bad streamed input"):
        async for outcome in run_ordered(
            source(),
            worker,
            concurrency=2,
            failure_policy=NEVER_HALT,
        ):
            outcomes.append(outcome)

    assert [outcome.index for outcome in outcomes] == [0]


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


async def test_worker_halt_display_units_do_not_constrain_source_counts() -> None:
    async def worker(_item: Item) -> str:
        raise TooManyFailures(5, 5, "five failed pairs")

    with pytest.raises(TooManyFailures) as excinfo:
        await _collect([_item(0)], worker, concurrency=1)

    halt = excinfo.value
    assert (halt.failed, halt.total, halt.consumed) == (5, 5, 1)
    assert halt.source_counts == SourceCounts(succeeded=0, skipped=1, failed=1)


async def test_worker_halt_rejects_source_counts_beyond_consumed_input() -> None:
    async def worker(_item: Item) -> str:
        raise TooManyFailures(
            1,
            1,
            "bad source accounting",
            source_counts=SourceCounts(succeeded=2, skipped=0, failed=0),
        )

    with pytest.raises(ValueError, match="exceed runner-consumed items"):
        await _collect([_item(0)], worker, concurrency=1)


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


# --- circuit breaker (problems.md #6): consecutive transport failures ----------

BREAKER_SCREEN = "error: fake looks down — 3 consecutive transport failures"


def _breaker_policy(limit: int = 3) -> FailurePolicy:
    return FailurePolicy(
        halt_ratio=1.0,
        min_sample=10**9,
        consecutive_limit=10**9,
        transport_limit=limit,
        transport_screen=BREAKER_SCREEN,
    )


def _scripted_worker(script: str) -> Callable[[Item], Awaitable[object]]:
    """'t' = TransportError, 'c' = content ItemError, '.' = success, per index."""

    async def worker(item: Item) -> str:
        match script[item.source.index]:
            case "t":
                raise TransportError("connect timeout after retries")
            case "c":
                raise ItemError("model returned invalid JSON after retry")
            case _:
                return item.text

    return worker


async def test_breaker_trips_on_consecutive_transport_failures() -> None:
    items = [_item(i) for i in range(6)]
    with pytest.raises(SetupFault, match="looks down"):
        await _collect(items, _scripted_worker("tttttt"), concurrency=1, policy=_breaker_policy())


async def test_breaker_yields_the_window_skips_before_dying() -> None:
    items = [_item(i) for i in range(6)]
    seen: list[ItemOutcome[object]] = []
    with pytest.raises(SetupFault):
        async for outcome in run_ordered(
            _stream(items),
            _scripted_worker("tttttt"),
            concurrency=1,
            failure_policy=_breaker_policy(),
        ):
            seen.append(outcome)
    assert len(seen) == 3  # the window's skips were reported, then the screen
    assert all(isinstance(outcome, Skipped) for outcome in seen)


async def test_provider_down_fault_carries_the_settled_source_ledger() -> None:
    items = [_item(index) for index in range(3)]
    with pytest.raises(LateSetupFault) as caught:
        await _collect(
            items,
            _scripted_worker(".tt"),
            concurrency=1,
            policy=_breaker_policy(limit=2),
        )

    assert caught.value.source_counts == SourceCounts(succeeded=1, skipped=2, failed=2)


async def test_a_success_resets_the_transport_streak() -> None:
    # 2 transport failures, a success, 2 more — never 3 consecutive
    items = [_item(i) for i in range(6)]
    outcomes = await _collect(
        items, _scripted_worker("tt.tt."), concurrency=1, policy=_breaker_policy()
    )
    assert len(outcomes) == 6


async def test_a_content_failure_resets_the_transport_streak() -> None:
    # a validation failure proves the provider answered — the wire is up
    items = [_item(i) for i in range(6)]
    outcomes = await _collect(
        items, _scripted_worker("ttcttc"), concurrency=1, policy=_breaker_policy()
    )
    assert len(outcomes) == 6


async def test_breaker_zero_disables() -> None:
    items = [_item(i) for i in range(8)]
    outcomes = await _collect(
        items, _scripted_worker("tttttttt"), concurrency=1, policy=_breaker_policy(limit=0)
    )
    assert len(outcomes) == 8  # unlimited transport failures, no breaker


# --- failover (fallback-model): the breaker window re-runs on model B ----------


async def test_failover_reruns_the_window_and_the_rest_on_model_b() -> None:
    switched = False

    async def worker(item: Item) -> str:
        if not switched and item.source.index >= 3:
            raise TransportError("connect timeout after retries")
        return f"{'B' if switched else 'A'}:{item.source.index}"

    async def failover() -> bool:
        nonlocal switched
        switched = True
        return True

    items = [_item(i) for i in range(10)]
    outcomes = [
        outcome
        async for outcome in run_ordered(
            _stream(items),
            worker,
            concurrency=1,
            failure_policy=_breaker_policy(),
            failover=failover,
        )
    ]
    # every item answered, in order — the buffered window (3,4,5) by model B
    assert [outcome.index for outcome in outcomes] == list(range(10))
    values = [outcome.value for outcome in outcomes if isinstance(outcome, Done)]
    assert values == [f"A:{i}" for i in range(3)] + [f"B:{i}" for i in range(3, 10)]


async def test_packed_429_waiters_count_as_one_actual_call_and_replay_on_fallback() -> None:
    switched = False
    switch_count = 0

    async def worker(item: Item) -> str:
        if switched:
            return f"B:{item.source.index}"
        if item.source.index < 3:
            # Three waiters behind one packed actual call.
            raise RetryableError("429", series_id=17, call_id=101)
        # The second actual call reaches the policy threshold and opens.
        raise CircuitOpenTransport("429", trip_id=17, call_id=102)

    async def failover() -> bool:
        nonlocal switched, switch_count
        switched = True
        switch_count += 1
        return True

    outcomes = [
        outcome
        async for outcome in run_ordered(
            _stream([_item(index) for index in range(6)]),
            worker,
            concurrency=6,
            failure_policy=_breaker_policy(limit=2),
            failover=failover,
        )
    ]

    assert switch_count == 1
    assert [outcome.value for outcome in outcomes if isinstance(outcome, Done)] == [
        f"B:{index}" for index in range(6)
    ]


async def test_packed_429_waiters_do_not_trip_no_fallback_breaker_per_item() -> None:
    async def worker(item: Item) -> str:
        del item
        raise RetryableError("429", series_id=17, call_id=101)

    outcomes = await _collect(
        [_item(index) for index in range(6)],
        worker,
        concurrency=6,
        policy=_breaker_policy(limit=2),
    )

    assert len(outcomes) == 6
    assert all(isinstance(outcome, Skipped) for outcome in outcomes)


async def test_one_packed_transport_call_does_not_trip_item_failure_rules() -> None:
    async def worker(item: Item) -> str:
        del item
        raise RetryableError("429", series_id=17, call_id=101)

    policy = FailurePolicy(
        halt_ratio=0.5,
        min_sample=5,
        consecutive_limit=5,
        transport_limit=2,
        transport_screen=BREAKER_SCREEN,
    )
    outcomes = await _collect(
        [_item(index) for index in range(6)],
        worker,
        concurrency=6,
        policy=policy,
    )

    assert len(outcomes) == 6
    assert all(isinstance(outcome, Skipped) for outcome in outcomes)


async def test_failover_replays_the_held_window_concurrently() -> None:
    switched = False
    active = 0
    peak = 0
    concurrent_replay = asyncio.Event()
    release_replay = asyncio.Event()

    async def worker(item: Item) -> str:
        nonlocal active, peak
        if not switched:
            if item.source.index < 3:
                raise RetryableError("429", series_id=17, call_id=101)
            raise CircuitOpenTransport("429", trip_id=17, call_id=102)
        active += 1
        peak = max(peak, active)
        if active >= 2:
            concurrent_replay.set()
        await release_replay.wait()
        active -= 1
        return f"B:{item.source.index}"

    async def failover() -> bool:
        nonlocal switched
        switched = True
        return True

    async def collect() -> list[ItemOutcome[str]]:
        return [
            outcome
            async for outcome in run_ordered(
                _stream([_item(index) for index in range(4)]),
                worker,
                concurrency=4,
                failure_policy=_breaker_policy(limit=2),
                failover=failover,
            )
        ]

    collecting = asyncio.create_task(collect())
    replay_was_concurrent = False
    try:
        await asyncio.wait_for(concurrent_replay.wait(), timeout=0.1)
        replay_was_concurrent = True
    except TimeoutError:
        pass
    finally:
        release_replay.set()
    outcomes = await collecting

    assert replay_was_concurrent is True
    assert peak == 4
    assert [outcome.value for outcome in outcomes if isinstance(outcome, Done)] == [
        f"B:{index}" for index in range(4)
    ]


async def test_failover_coalesces_late_same_series_replays() -> None:
    switched = False
    primary_started = 0
    all_primary_started = asyncio.Event()
    release_primary = asyncio.Event()
    active = 0
    peak = 0
    concurrent_replay = asyncio.Event()
    release_replay = asyncio.Event()

    async def worker(item: Item) -> str:
        nonlocal active, peak, primary_started
        if not switched:
            primary_started += 1
            if primary_started == 4:
                all_primary_started.set()
            await release_primary.wait()
            if item.source.index == 0:
                raise CircuitOpenTransport("429", trip_id=17, call_id=102)
            raise RetryableError("429", series_id=17, call_id=101)
        if item.source.index == 0:
            return "B:0"
        active += 1
        peak = max(peak, active)
        if active >= 2:
            concurrent_replay.set()
        await release_replay.wait()
        active -= 1
        return f"B:{item.source.index}"

    async def failover() -> bool:
        nonlocal switched
        switched = True
        return True

    async def collect() -> list[ItemOutcome[str]]:
        return [
            outcome
            async for outcome in run_ordered(
                _stream([_item(index) for index in range(4)]),
                worker,
                concurrency=4,
                failure_policy=_breaker_policy(limit=2),
                failover=failover,
            )
        ]

    collecting = asyncio.create_task(collect())
    await asyncio.wait_for(all_primary_started.wait(), timeout=1)
    release_primary.set()
    replay_was_coalesced = False
    try:
        await asyncio.wait_for(concurrent_replay.wait(), timeout=0.1)
        replay_was_coalesced = True
    except TimeoutError:
        pass
    finally:
        release_replay.set()
    outcomes = await collecting

    assert replay_was_coalesced is True
    assert peak == 3
    assert [outcome.value for outcome in outcomes if isinstance(outcome, Done)] == [
        f"B:{index}" for index in range(4)
    ]


async def test_failover_declined_flushes_the_window_then_dies() -> None:
    async def worker(item: Item) -> str:
        raise TransportError("boom")

    async def failover() -> bool:
        return False  # nothing configured / fallback unusable

    items = [_item(i) for i in range(5)]
    seen: list[ItemOutcome[str]] = []
    with pytest.raises(SetupFault, match="looks down"):
        async for outcome in run_ordered(
            _stream(items),
            worker,
            concurrency=1,
            failure_policy=_breaker_policy(),
            failover=failover,
        ):
            seen.append(outcome)
    assert len(seen) == 3  # the held window was still reported before death
    assert all(isinstance(outcome, Skipped) for outcome in seen)


async def test_breaker_on_the_fallback_dies_loudly() -> None:
    switches = 0

    async def worker(item: Item) -> str:
        raise TransportError("boom")  # both providers down

    async def failover() -> bool:
        nonlocal switches
        switches += 1
        return True

    items = [_item(i) for i in range(12)]
    seen: list[ItemOutcome[str]] = []
    with pytest.raises(SetupFault, match="looks down"):
        async for outcome in run_ordered(
            _stream(items),
            worker,
            concurrency=1,
            failure_policy=_breaker_policy(),
            failover=failover,
        ):
            seen.append(outcome)
    assert switches == 1  # one fallback, then honest death — never a chain
    assert len(seen) == 3  # the re-run window's skips were reported


async def test_window_flushes_in_order_when_the_wire_answers_again() -> None:
    async def worker(item: Item) -> str:
        if item.source.index in (0, 1):
            raise TransportError("blip")
        return "ok"

    async def failover() -> bool:  # pragma: no cover — the streak never reaches 3
        raise AssertionError("failover consulted below the threshold")

    items = [_item(i) for i in range(4)]
    outcomes = [
        outcome
        async for outcome in run_ordered(
            _stream(items),
            worker,
            concurrency=1,
            failure_policy=_breaker_policy(),
            failover=failover,
        )
    ]
    assert [outcome.index for outcome in outcomes] == [0, 1, 2, 3]  # order held
    assert [isinstance(outcome, Skipped) for outcome in outcomes] == [True, True, False, False]


async def test_trailing_window_flushes_at_end_of_input() -> None:
    async def worker(item: Item) -> str:
        if item.source.index >= 2:
            raise TransportError("blip")
        return "ok"

    async def failover() -> bool:  # pragma: no cover — the streak never reaches 3
        raise AssertionError("failover consulted below the threshold")

    items = [_item(i) for i in range(4)]
    outcomes = [
        outcome
        async for outcome in run_ordered(
            _stream(items),
            worker,
            concurrency=1,
            failure_policy=_breaker_policy(),
            failover=failover,
        )
    ]
    assert [outcome.index for outcome in outcomes] == [0, 1, 2, 3]
    assert [isinstance(outcome, Skipped) for outcome in outcomes] == [False, False, True, True]


async def test_halt_source_counter_collapses_prefetched_ocr_pages() -> None:
    from smartpipe.io import source_accounting

    source_accounting.reset()
    group = source_accounting.new_group(size=3)
    items = [
        Item(
            raw=f"page {index}",
            text=f"page {index}",
            data=None,
            source=ItemSource("file", "book.pdf", index, "pages", group=group),
        )
        for index in range(3)
    ]
    sources = source_accounting.SourceCounter()

    async def worker(_item: Item) -> str:
        raise ItemError("bad page")

    policy = FailurePolicy(min_sample=10**9, consecutive_limit=2)
    with pytest.raises(TooManyFailures) as caught:
        async for outcome in run_ordered(
            _stream(items),
            worker,
            concurrency=3,
            failure_policy=policy,
            halt_sources=sources,
        ):
            assert isinstance(outcome, Skipped)
            sources.skip(outcome.source, failed=outcome.failed)

    assert caught.value.source_counts == SourceCounts(succeeded=0, skipped=1, failed=1)


async def test_halt_counts_completed_unemitted_failures_as_failed_sources() -> None:
    later_failures_ready = asyncio.Event()
    later_failures = 0

    async def worker(item: Item) -> str:
        nonlocal later_failures
        if item.source.index == 0:
            await later_failures_ready.wait()
        else:
            later_failures += 1
            if later_failures == 4:
                later_failures_ready.set()
        raise ItemError(f"failed {item.source.index}")

    with pytest.raises(TooManyFailures) as caught:
        async for _outcome in run_ordered(
            _stream([_item(index) for index in range(5)]),
            worker,
            concurrency=5,
            failure_policy=FailurePolicy(min_sample=10**9, consecutive_limit=1),
        ):
            pass

    assert caught.value.source_counts == SourceCounts(succeeded=0, skipped=5, failed=5)
