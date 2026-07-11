from __future__ import annotations

import pytest

from smartpipe.core.errors import SetupFault, TooManyFailures, UnsentError, UsageFault
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


def test_die_read_phase_belt_exhaustion_exits_3_not_bug(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A5.1: a page belt (--max-calls) exhausted mid-read escapes a whole-set verb
    as a raw UnsentError; die maps it to ALL_FAILED (3) with the belt truth, never
    the BUG screen (70) a stray item error would otherwise get."""
    with pytest.raises(SystemExit) as excinfo:
        diagnostics.die(UnsentError("stopped by --max-calls (0 OCR pages processed)"))
    assert _exit_code(excinfo) == 3
    err = capsys.readouterr().err
    assert "stopped by --max-calls" in err
    assert "bug in smartpipe" not in err  # not the internal-error screen


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


def test_degradation_log_buckets_repeated_skips(capsys: pytest.CaptureFixture[str]) -> None:
    """B4: a flood of same-reason skips (one absolute-path line per chunk) collapses
    to the first-N verbatim, one suppression line, then a rollup — the same shape
    the degrade ledger uses, keyed by the reason PREFIX before the echoed instance."""
    log = diagnostics.DegradationLog()
    for i in range(8):
        log.skip(f"corpus/chunk-{i}", f"output does not match the schema: instance {i}")
    log.finish()
    err = capsys.readouterr().err
    verbatim = [line for line in err.splitlines() if line.startswith("⚠ skipped:")]
    assert len(verbatim) == 5  # _DEGRADE_CAP: first N verbatim, keeping the full reason
    # the verbatim line keeps the FULL reason (only the rollup collapses to the prefix)
    assert verbatim[0] == "⚠ skipped: corpus/chunk-0 (output does not match the schema: instance 0)"
    assert err.count("skips follow") == 1  # exactly one suppression line
    assert "note: skipped: output does not match the schema ×8" in err  # the rollup  # noqa: RUF001


def test_degradation_log_skip_rollup_ranks_reasons(capsys: pytest.CaptureFixture[str]) -> None:
    """Distinct reason prefixes bucket independently and rank heaviest-first."""
    log = diagnostics.DegradationLog()
    for i in range(3):
        log.skip(f"a-{i}", f"model did not return valid JSON: {i}")
    for i in range(2):
        log.skip(f"b-{i}", "the model named no relation")
    log.finish()
    err = capsys.readouterr().err
    rollup = "note: skipped: model did not return valid JSON ×3 · the model named no relation ×2"  # noqa: RUF001
    assert rollup in err


def test_degradation_log_finish_still_rolls_up_degrades(capsys: pytest.CaptureFixture[str]) -> None:
    """The existing degrade rollup is untouched by the new skip bucket."""
    log = diagnostics.DegradationLog()
    log.note("scan.pdf", "document → markdown", "parsed by mistral")
    log.finish()
    err = capsys.readouterr().err
    assert "note: degraded: document → markdown ×1" in err  # noqa: RUF001 — the pinned rollup mark
    assert "skipped:" not in err  # no skips this run


def test_error_prefix_is_red_only_on_tty(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("smartpipe.io.tty.stderr_supports_color", lambda: True)
    with pytest.raises(SystemExit):
        diagnostics.die(UsageFault("bad"))
    assert capsys.readouterr().err == "\x1b[31merror:\x1b[0m bad\n"
