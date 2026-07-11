"""The manifest settling wrapper: every results-producing exit path writes."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.cli import interrupts
from smartpipe.cli.manifest_option import begin_manifest, settled
from smartpipe.core.errors import (
    ExitCode,
    ItemError,
    LateSetupFault,
    SourceCounts,
    TooManyFailures,
    UnsentError,
)
from smartpipe.engine.runner import FailurePolicy, run_ordered
from smartpipe.io import manifest, source_accounting
from smartpipe.io.items import Item, ItemSource
from smartpipe.verbs.common import outcome_exit_code

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


async def test_the_majority_failure_halt_still_writes_the_manifest(tmp_path: Path) -> None:
    # results streamed before the halt tripped - the record must land, with
    # the halt's own accounting and the all_failed exit it maps to
    manifest.reset()
    target = tmp_path / "run.json"
    manifest.begin(target, verb="map", argv=("map", "x"))

    async def exploding() -> ExitCode:
        raise TooManyFailures(12, 20, "the same way")

    with pytest.raises(TooManyFailures):
        await settled(exploding(), None)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 20, "succeeded": 8, "skipped": 12, "failed": 12}
    assert document["run"]["exit_status"] == "all_failed"


async def test_halt_manifest_counts_prefetched_cancelled_inputs(tmp_path: Path) -> None:
    manifest.reset()
    target = tmp_path / "run.json"
    manifest.begin(target, verb="map", argv=("map", "x"))
    all_started = asyncio.Event()
    blocked = asyncio.Event()
    started = 0
    cancelled: set[int] = set()

    async def source() -> AsyncIterator[Item]:
        for index in range(3):
            yield Item(raw="x", text="x", data=None, source=ItemSource("stdin", "-", index))
        await blocked.wait()

    async def worker(item: Item) -> str:
        nonlocal started
        started += 1
        if started == 3:
            all_started.set()
        await all_started.wait()
        if item.source.index == 0:
            raise ItemError("same failure")
        try:
            await blocked.wait()
        except asyncio.CancelledError:
            cancelled.add(item.source.index)
            raise
        return item.text  # pragma: no cover - the halt cancels these workers

    async def work() -> ExitCode:
        async for _outcome in run_ordered(
            source(),
            worker,
            concurrency=3,
            failure_policy=FailurePolicy(
                halt_ratio=1.0,
                min_sample=10**9,
                consecutive_limit=1,
            ),
        ):
            pass
        return ExitCode.OK  # pragma: no cover - the failure policy halts

    with pytest.raises(TooManyFailures) as excinfo:
        await settled(work(), None)

    halt = excinfo.value
    assert (halt.failed, halt.total, halt.consumed) == (1, 1, 3)
    assert halt.source_counts == SourceCounts(succeeded=0, skipped=3, failed=1)
    assert str(halt) == "stopping — 1 of 1 items failed the same way"
    assert cancelled == {1, 2}
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 3, "succeeded": 0, "skipped": 3, "failed": 1}


async def test_halt_manifest_does_not_count_an_emitted_unsent_skip_as_success(
    tmp_path: Path,
) -> None:
    manifest.reset()
    target = tmp_path / "run.json"
    manifest.begin(target, verb="map", argv=("map", "x"))

    async def source() -> AsyncIterator[Item]:
        for index in range(2):
            yield Item(raw="x", text="x", data=None, source=ItemSource("stdin", "-", index))

    async def worker(item: Item) -> str:
        if item.source.index == 0:
            raise UnsentError("run stopping — not sent")
        raise ItemError("same failure")

    async def work() -> ExitCode:
        async for _outcome in run_ordered(
            source(),
            worker,
            concurrency=1,
            failure_policy=FailurePolicy(
                halt_ratio=1.0,
                min_sample=10**9,
                consecutive_limit=1,
            ),
        ):
            pass
        return ExitCode.OK  # pragma: no cover - the failure policy halts

    with pytest.raises(TooManyFailures) as excinfo:
        await settled(work(), None)

    halt = excinfo.value
    assert (halt.failed, halt.total) == (1, 2)
    assert halt.source_counts == SourceCounts(succeeded=0, skipped=2, failed=1)
    assert str(halt) == "stopping — 1 of 2 items failed the same way"
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 2, "succeeded": 0, "skipped": 2, "failed": 1}


async def test_settled_merges_dropped_ingestion_sources_into_exit_and_manifest(
    tmp_path: Path,
) -> None:
    source_accounting.reset()
    manifest.reset()
    target = tmp_path / "run.json"
    manifest.begin(target, verb="map", argv=("map", "x"))
    source_accounting.record_ingestion_skip(failed=True)

    code = await settled(_returning(outcome_exit_code(done=1, skipped=0)), None)

    assert code is ExitCode.PARTIAL
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 2, "succeeded": 1, "skipped": 1, "failed": 1}


async def test_halt_source_counts_include_dropped_ingestion_sources(tmp_path: Path) -> None:
    source_accounting.reset()
    manifest.reset()
    target = tmp_path / "run.json"
    manifest.begin(target, verb="map", argv=("map", "x"))
    source_accounting.record_ingestion_skip(failed=True)

    async def halt() -> ExitCode:
        raise TooManyFailures(
            5,
            5,
            "pair failures",
            source_counts=SourceCounts(succeeded=1, skipped=1, failed=1),
        )

    with pytest.raises(TooManyFailures) as excinfo:
        await settled(halt(), None)

    assert excinfo.value.source_counts == SourceCounts(succeeded=1, skipped=2, failed=2)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 3, "succeeded": 1, "skipped": 2, "failed": 2}


async def test_late_setup_fault_finalizes_the_started_manifest(tmp_path: Path) -> None:
    source_accounting.reset()
    manifest.reset()
    target = tmp_path / "run.json"
    manifest.begin(target, verb="map", argv=("map", "x"))
    source_accounting.record_ingestion_skip(failed=True)

    async def provider_down() -> ExitCode:
        raise LateSetupFault(
            "provider unavailable",
            source_counts=SourceCounts(succeeded=1, skipped=2, failed=2),
        )

    with pytest.raises(LateSetupFault) as caught:
        await settled(provider_down(), None)

    assert caught.value.source_counts == SourceCounts(succeeded=1, skipped=3, failed=3)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 4, "succeeded": 1, "skipped": 3, "failed": 3}
    assert document["run"]["exit_status"] == "setup"


def test_begin_manifest_registers_a_hard_exit_cleanup_that_unlinks_the_temp(
    tmp_path: Path,
) -> None:
    # B6: a hard exit (second Ctrl-C / watchdog escalation) runs os._exit, which
    # bypasses settled()'s abandon. begin_manifest registers a cleanup so the
    # reserved 0-byte temp is unlinked on that path instead of leaking.
    interrupts._reset_interrupt_state()  # pyright: ignore[reportPrivateUsage] — test isolation
    manifest.reset()
    target = tmp_path / "run.json"

    begin_manifest(target, verb="graph")
    assert any(p.name.endswith(".manifest.tmp") for p in tmp_path.iterdir())

    # what _hard_exit runs before os._exit:
    interrupts._run_hard_exit_cleanups()  # pyright: ignore[reportPrivateUsage] — path under test

    assert list(tmp_path.iterdir()) == []  # no *.manifest.tmp left behind
    manifest.reset()
    interrupts._reset_interrupt_state()  # pyright: ignore[reportPrivateUsage] — test isolation


def test_begin_manifest_without_a_path_registers_nothing(tmp_path: Path) -> None:
    interrupts._reset_interrupt_state()  # pyright: ignore[reportPrivateUsage] — test isolation
    manifest.reset()
    begin_manifest(None, verb="graph")  # no --manifest → no reservation, no cleanup
    interrupts._run_hard_exit_cleanups()  # pyright: ignore[reportPrivateUsage] — clean no-op
    assert list(tmp_path.iterdir()) == []
    interrupts._reset_interrupt_state()  # pyright: ignore[reportPrivateUsage] — test isolation


async def _returning(code: ExitCode) -> ExitCode:
    return code
