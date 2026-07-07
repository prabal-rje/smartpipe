"""The usage ledger (D41): windows, pruning, reset with memory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from smartpipe.io import metering
from smartpipe.io.usage import read_ledger, record_run, reset_ledger, usage_path

if TYPE_CHECKING:
    from pathlib import Path

NOW = 1_800_000_000.0  # a fixed "now" — the ledger takes time as a parameter
HOUR = 3_600.0
DAY = 86_400.0


def _snapshot(tokens_in: int) -> metering.Snapshot:
    return metering.Snapshot(
        tokens_in=tokens_in,
        tokens_out=tokens_in // 10,
        media_bytes={},
        media_count={},
        audio_seconds=0.0,
        conversions=0,
    )


def _env(tmp_path: Path) -> dict[str, str]:
    return {"XDG_STATE_HOME": str(tmp_path)}


def test_windows_slice_by_event_age(tmp_path: Path) -> None:
    env = _env(tmp_path)
    record_run(_snapshot(100), env, now=NOW - 30 * DAY + HOUR)  # inside the month only
    record_run(_snapshot(10), env, now=NOW - 2 * DAY)  # week + month
    record_run(_snapshot(1), env, now=NOW - 60)  # every window
    windows, _first, _reset = read_ledger(env, now=NOW)
    assert windows["past hour"].tokens_in == 1
    assert windows["past day"].tokens_in == 1
    assert windows["past week"].tokens_in == 11
    assert windows["past month"].tokens_in == 111
    assert windows["lifetime"].tokens_in == 111
    assert windows["lifetime"].runs == 3


def test_pruning_keeps_lifetime_intact(tmp_path: Path) -> None:
    env = _env(tmp_path)
    record_run(_snapshot(1000), env, now=NOW - 90 * DAY)  # ancient
    record_run(_snapshot(1), env, now=NOW)  # triggers the prune
    windows, _first, _reset = read_ledger(env, now=NOW)
    assert windows["past month"].tokens_in == 1  # the ancient event is gone
    assert windows["lifetime"].tokens_in == 1001  # but lifetime remembers


def test_reset_zeroes_and_remembers(tmp_path: Path) -> None:
    env = _env(tmp_path)
    record_run(_snapshot(500), env, now=NOW - 100)
    previous = reset_ledger(env, now=NOW)
    assert previous.tokens_in == 500
    windows, first_seen, last_reset = read_ledger(env, now=NOW + 10)
    assert windows["lifetime"].runs == 0
    assert last_reset == NOW
    assert first_seen is not None  # first use survives the reset


def test_empty_snapshot_records_nothing(tmp_path: Path) -> None:
    env = _env(tmp_path)
    record_run(
        metering.Snapshot(0, 0, {}, {}, 0.0, 0),
        env,
        now=NOW,
    )
    assert not usage_path(env).exists()


def test_corrupt_file_reads_as_empty(tmp_path: Path) -> None:
    env = _env(tmp_path)
    path = usage_path(env)
    path.parent.mkdir(parents=True)
    path.write_text("not json{", encoding="utf-8")
    windows, _first, _reset = read_ledger(env, now=NOW)
    assert windows["lifetime"].runs == 0
    record_run(_snapshot(5), env, now=NOW)  # and writing over it heals it
    windows, _first, _reset = read_ledger(env, now=NOW)
    assert windows["lifetime"].tokens_in == 5
