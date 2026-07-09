from __future__ import annotations

import pytest

from smartpipe.core.errors import ExitCode
from smartpipe.io import diagnostics
from smartpipe.verbs.common import interrupted_exit_code, outcome_exit_code


def test_outcome_exit_code() -> None:
    assert outcome_exit_code(done=3, skipped=0) == ExitCode.OK
    assert outcome_exit_code(done=2, skipped=1) == ExitCode.PARTIAL
    assert outcome_exit_code(done=0, skipped=2) == ExitCode.ALL_FAILED
    assert outcome_exit_code(done=0, skipped=0) == ExitCode.OK  # empty input is success


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
