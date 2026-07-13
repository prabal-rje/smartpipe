from __future__ import annotations

import asyncio
import io

import pytest

from smartpipe.core.errors import ExitCode
from smartpipe.io import diagnostics
from smartpipe.io.progress import Spinner
from smartpipe.verbs.common import interrupted_exit_code, outcome_exit_code, spin_pending


def test_outcome_exit_code() -> None:
    assert outcome_exit_code(done=3, skipped=0) == ExitCode.OK
    assert outcome_exit_code(done=2, skipped=1) == ExitCode.PARTIAL
    assert outcome_exit_code(done=0, skipped=2) == ExitCode.ALL_FAILED
    assert outcome_exit_code(done=0, skipped=0) == ExitCode.OK  # empty input is success


def test_outcome_tracks_attempted_failures_without_copying_all_skips() -> None:
    assert outcome_exit_code(done=2, skipped=3, failed=1) is ExitCode.PARTIAL
    assert outcome_exit_code(done=2, skipped=0, partial=True) is ExitCode.PARTIAL
    with pytest.raises(ValueError, match="failed cannot exceed skipped"):
        outcome_exit_code(done=2, skipped=1, failed=2)
    with pytest.raises(ValueError, match="input count"):
        outcome_exit_code(done=2, skipped=1, input_count=4)


def test_interrupted_exit_code_preserves_outcome() -> None:
    assert interrupted_exit_code(done=2, skipped=0) == ExitCode.OK
    assert interrupted_exit_code(done=1, skipped=1) == ExitCode.PARTIAL
    assert interrupted_exit_code(done=0, skipped=1) == ExitCode.ALL_FAILED
    # ...except when nothing finished at all: that's a plain interrupt
    assert interrupted_exit_code(done=0, skipped=0) == ExitCode.INTERRUPTED


def test_interrupted_summary_wording_is_contract(capsys: pytest.CaptureFixture[str]) -> None:
    diagnostics.interrupted_summary(processed=7, skipped=2)
    diagnostics.drain_timed_out()
    err = capsys.readouterr().err
    assert "done: interrupted — 7 processed · 2 skipped\n" in err
    assert "done: interrupted — drain timed out\n" in err


async def test_spin_pending_animates_the_row_until_the_awaitable_resolves() -> None:
    stream = io.StringIO()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=lambda: 0.0)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0)  # yield so the wrapped work can make progress

    async def work() -> str:
        for _ in range(3):
            await asyncio.sleep(0)
        return "ready"

    result = await spin_pending(spinner, "preparing local NER model", work(), sleep=fake_sleep)
    assert result == "ready"
    assert "preparing local NER model" in stream.getvalue()  # the row was animated
    assert stream.getvalue().endswith("\x1b[K")  # the line was cleared on finish
    assert sleeps and sleeps[0] == pytest.approx(0.1)  # the injected pending cadence


async def test_spin_pending_stays_silent_but_still_returns_with_a_disabled_spinner() -> None:
    stream = io.StringIO()
    spinner = Spinner(stream=stream, enabled=False, ascii_only=True, clock=lambda: 0.0)

    async def fake_sleep(seconds: float) -> None:
        del seconds
        await asyncio.sleep(0)

    async def work() -> int:
        await asyncio.sleep(0)
        return 42

    result = await spin_pending(spinner, "preparing", work(), sleep=fake_sleep)
    assert result == 42
    assert stream.getvalue() == ""  # a piped run pays nothing


def test_note_ambiguous_temporal_caps_at_five(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from smartpipe.verbs import common

    monkeypatch.setattr(common, "_ambiguous_dates_seen", 0)  # restored on teardown
    for position in range(7):
        common.note_ambiguous_temporal(f"ambiguity {position}")
    err = capsys.readouterr().err
    assert "ambiguity 0" in err and "ambiguity 4" in err  # the first five land verbatim
    assert "ambiguity 5" not in err and "ambiguity 6" not in err
    assert err.count("more ambiguous dates follow (suppressed)") == 1
