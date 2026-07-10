from __future__ import annotations

import pytest

from smartpipe.core.errors import SetupFault, TooManyFailures, UsageFault
from smartpipe.io import diagnostics


def _exit_code(excinfo: pytest.ExceptionInfo[SystemExit]) -> int:
    code = excinfo.value.code
    assert isinstance(code, int)
    return code


def test_warn_writes_marked_line_to_stderr_only(capsys: pytest.CaptureFixture[str]) -> None:
    diagnostics.warn("skipped: x")
    captured = capsys.readouterr()
    assert captured.err == "⚠ skipped: x\n"
    assert captured.out == ""


def test_note_writes_prefixed_line(capsys: pytest.CaptureFixture[str]) -> None:
    diagnostics.note("using ollama/qwen3:8b")
    assert capsys.readouterr().err == "note: using ollama/qwen3:8b\n"


def test_die_usage_fault_exits_64(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        diagnostics.die(UsageFault("bad"))
    assert _exit_code(excinfo) == 64
    assert capsys.readouterr().err == "error: bad\n"


def test_die_setup_fault_prints_screen_verbatim_and_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    screen = "error: no model configured, and no local Ollama found\n\n  Cloud (paid):"
    with pytest.raises(SystemExit) as excinfo:
        diagnostics.die(SetupFault(screen))
    assert _exit_code(excinfo) == 2
    # Screens carry their own "error:" prefix; die must not double it.
    assert capsys.readouterr().err == screen + "\n"


def test_die_too_many_failures_exits_3(capsys: pytest.CaptureFixture[str]) -> None:
    fault = TooManyFailures(failed=61, total=100, last_reason="invalid JSON")
    with pytest.raises(SystemExit) as excinfo:
        diagnostics.die(fault)
    assert _exit_code(excinfo) == 3
    assert "61 of 100" in capsys.readouterr().err


def test_die_with_debug_appends_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        diagnostics.die(UsageFault("bad"), debug=True)
    err = capsys.readouterr().err
    assert err.startswith("error: bad\n")
    assert "UsageFault" in err  # traceback present


def test_internal_error_exits_70_and_hides_traceback_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        diagnostics.internal_error(ValueError("boom"), debug=False)
    assert _exit_code(excinfo) == 70
    err = capsys.readouterr().err
    assert err.startswith("error: internal error — this is a bug in smartpipe")
    assert "ValueError: boom" in err
    assert "Rerun with --debug" in err
    assert "Traceback" not in err
    assert "issues/new" in err


def test_internal_error_with_debug_shows_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        raise ValueError("boom")
    except ValueError as exc:
        with pytest.raises(SystemExit):
            diagnostics.internal_error(exc, debug=True)
    err = capsys.readouterr().err
    assert "Traceback" in err
    assert "Rerun with --debug" not in err


def test_error_prefix_is_red_only_on_tty(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("smartpipe.io.tty.stderr_supports_color", lambda: True)
    with pytest.raises(SystemExit):
        diagnostics.die(UsageFault("bad"))
    assert capsys.readouterr().err == "\x1b[31merror:\x1b[0m bad\n"
