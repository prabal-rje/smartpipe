"""``circuit_broken`` reproduces the breaker+failover contract IN ISOLATION.

These mirror the pinned map/runner failover numbers
(``tests/verbs/test_map.py::test_failover_switches_wholesale_and_answers_everything``
and ``::test_failover_on_a_dead_backup_dies_loudly``) at the decorator level,
proving the combinator owns the state machine the runner used to reach through
``make_failover``: trip at the limit, raise ``CircuitOpenTransport``, swap to the
fallback wholesale, then honest death when the fallback trips too.

The tiny ``while`` loop stands in for ``run_ordered``'s held-window + replay: the
items that failed on the wire before the trip are re-invoked on the now-swapped
target, exactly as the runner replays its window. The decorator's internal swap
means the replay routes to the backup with no caller branching.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from smartpipe.core.errors import CircuitOpenTransport, TransportError
from smartpipe.models.base import ModelRef
from smartpipe.models.resilience import Breaker, circuit_broken

PRIMARY = ModelRef("ollama", "fake")
BACKUP = ModelRef("openai", "gpt-4o-mini")


def _ready(
    target: Callable[[], Awaitable[str]],
) -> Callable[[], Awaitable[Callable[[], Awaitable[str]]]]:
    """Wrap an already-built target as circuit_broken's lazy fallback factory."""

    async def build() -> Callable[[], Awaitable[str]]:
        return target

    return build


async def test_circuit_broken_reproduces_the_wholesale_switch_numbers() -> None:
    """Mirror ``test_failover_switches_wholesale_and_answers_everything``:
    7 items, breaker_limit 5 → primary.calls == 5, backup.calls == 7, nothing lost."""
    breaker = Breaker(limit=5)
    primary_calls = 0
    backup_calls = 0
    notices: list[str] = []

    async def primary() -> str:
        nonlocal primary_calls
        primary_calls += 1
        raise TransportError("openai error 503: overloaded")

    async def backup() -> str:
        nonlocal backup_calls
        backup_calls += 1
        return "B"

    guarded = circuit_broken(
        breaker,
        ref=PRIMARY,
        fallback_factory=_ready(backup),
        fallback_ref=BACKUP,
        announce=notices.append,
    )(primary)

    results: list[str] = []
    window: list[int] = []  # items that failed on the wire before the trip
    item = 0
    while item < 7:
        try:
            results.append(await guarded())
        except CircuitOpenTransport:
            # the trip: replay the held window on the swapped target (the runner's job)
            window.append(item)
            results.extend([await guarded() for _held in window])
        except TransportError:
            window.append(item)  # a sacrificial pre-trip failure, held for replay
        item += 1

    assert results == ["B"] * 7  # nothing lost: the window re-ran on the backup
    assert primary_calls == 5  # 4 pre-trip failures + the trip, then never again
    assert backup_calls == 7  # the replayed window (5) + items 5 and 6
    assert notices == [
        "ollama looks down (5 consecutive transport failures) — "
        "switching to openai/gpt-4o-mini for the rest of the run"
    ]


async def test_concurrent_trips_announce_the_switch_only_once() -> None:
    """Under concurrency, several in-flight primary calls can each reach the trip
    in the SAME window (each captured ``on_fallback=False`` before the swap). The
    pinned "switching to …" line must fire exactly once — not once per tripped
    call. The old ``run_ordered`` set ``failover_pending = None`` after its single
    ``await switch()``, so the hook ran once; the decorator must match that."""
    breaker = Breaker(limit=2)
    notices: list[str] = []
    arrivals = 0
    release = asyncio.Event()

    async def primary() -> str:
        nonlocal arrivals
        arrivals += 1
        await release.wait()  # hold every call in flight until they all arrive
        raise TransportError("primary down")

    async def backup() -> str:
        return "B"

    guarded = circuit_broken(
        breaker,
        ref=PRIMARY,
        fallback_factory=_ready(backup),
        fallback_ref=BACKUP,
        announce=notices.append,
    )(primary)

    async def call() -> None:
        # the trip / a sacrificial pre-trip failure — the runner replays
        with contextlib.suppress(CircuitOpenTransport, TransportError):
            await guarded()

    tasks = [asyncio.create_task(call()) for _ in range(4)]
    while arrivals < 4:  # let all four park in flight before any of them fails
        await asyncio.sleep(0)
    release.set()  # now they fail together; streaks 2/3/4 each reach the limit
    await asyncio.gather(*tasks)

    assert notices == [
        "ollama looks down (2 consecutive transport failures) — "
        "switching to openai/gpt-4o-mini for the rest of the run"
    ]  # ONE announce, not one per concurrently-tripped call


async def test_circuit_broken_honest_death_when_the_backup_is_also_down() -> None:
    """Mirror ``test_failover_on_a_dead_backup_dies_loudly``: one window on the
    primary (5), one on the backup (5), then a second trip is honest death."""
    breaker = Breaker(limit=5)
    primary_calls = 0
    backup_calls = 0

    async def primary() -> str:
        nonlocal primary_calls
        primary_calls += 1
        raise TransportError("primary down")

    async def backup() -> str:
        nonlocal backup_calls
        backup_calls += 1
        raise TransportError("backup down too")

    guarded = circuit_broken(
        breaker, ref=PRIMARY, fallback_factory=_ready(backup), fallback_ref=BACKUP
    )(primary)

    window: list[int] = []
    died = False
    item = 0
    while item < 12 and not died:
        try:
            await guarded()
        except CircuitOpenTransport:
            window.append(item)
            for _held in window:  # replay on the backup; a second trip is terminal
                try:
                    await guarded()
                except CircuitOpenTransport:
                    died = True
                    break
                except TransportError:
                    pass  # a sacrificial backup failure — keep replaying
        except TransportError:
            window.append(item)
        item += 1

    assert died
    assert primary_calls == 5  # one window on the primary
    assert backup_calls == 5  # one window on the backup, then honest death
