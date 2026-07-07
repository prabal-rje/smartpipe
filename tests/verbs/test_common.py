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
