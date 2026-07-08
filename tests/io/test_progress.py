from __future__ import annotations

import io

import pytest

from smartpipe.io import tty
from smartpipe.io.progress import (
    Spinner,
    format_eta,
    make_stderr_spinner,
    render_known,
    render_unknown,
)

# --- pure formatting ----------------------------------------------------------


def test_format_eta() -> None:
    assert format_eta(45) == "45s"
    assert format_eta(132) == "2m12s"
    assert format_eta(3723) == "1h2m"
    assert format_eta(0) == "0s"


def test_render_known_matches_the_spec_line() -> None:
    line = render_known("⠋", done=243, total=500, eta_seconds=132)
    assert line == "⠋ Processing 500 items [243/500] 48%  ~2m12s remaining"


def test_render_known_without_eta_before_warmup() -> None:
    line = render_known("⠋", done=2, total=500, eta_seconds=None)
    assert line == "⠋ Processing 500 items [2/500] 0%"


def test_render_unknown_shows_rate() -> None:
    assert render_unknown("⠋", done=243, rate=3.1) == "⠋ Processing [243] 3.1/s"


# --- Spinner behaviour --------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_disabled_spinner_writes_nothing() -> None:
    stream = io.StringIO()
    spinner = Spinner(stream=stream, enabled=False, ascii_only=False, clock=_Clock())
    spinner.start(total=10)
    for _ in range(10):
        spinner.advance()
    spinner.finish()
    assert stream.getvalue() == ""


def test_enabled_spinner_draws_and_clears() -> None:
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=3)
    clock.t = 1.0
    spinner.advance()
    output = stream.getvalue()
    assert "\r" in output
    assert "Processing 3 items" in output
    assert "[1/3]" in output
    spinner.finish()
    assert stream.getvalue().endswith("\x1b[K")  # line cleared on finish


def test_spinner_throttles_redraws() -> None:
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=100)
    # three advances within the same 100ms window → at most one draw
    for _ in range(3):
        clock.t += 0.01
        spinner.advance()
    draws = stream.getvalue().count("\r")
    assert draws <= 1


def test_spinner_always_draws_final_item() -> None:
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=2)
    clock.t += 0.001
    spinner.advance()  # throttled maybe
    clock.t += 0.001
    spinner.advance()  # last item — must draw regardless of throttle
    assert "[2/2]" in stream.getvalue()


def test_unknown_total_spinner_shows_running_count_and_rate() -> None:
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=None)
    clock.t = 2.0
    spinner.advance()
    assert "Processing [1]" in stream.getvalue()
    assert "/s" in stream.getvalue()


# --- terminal arbiter: results must never land under the status line -----------


class _Terminal(io.StringIO):
    """A fake TTY that records every write in order — the spinner (stderr) and
    the result writer (stdout) share one terminal, exactly like a real run."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []

    def write(self, s: str, /) -> int:
        self.writes.append(s)
        return super().write(s)


def _writes_while_status_line_up(writes: list[str]) -> list[str]:
    """Every non-spinner write that landed while the status line was drawn."""
    violations: list[str] = []
    drawn = False
    for chunk in writes:
        if chunk == "\r\x1b[K":  # the erase primitive
            drawn = False
        elif chunk.startswith("\r"):  # a draw (or redraw)
            drawn = True
        elif drawn:
            violations.append(chunk)
    return violations


def test_guarded_writes_never_interleave_with_the_status_line() -> None:
    terminal = _Terminal()
    clock = _Clock()
    spinner = Spinner(stream=terminal, enabled=True, ascii_only=True, clock=clock)
    results = spinner.guard(terminal)
    spinner.start(total=4)
    for index in range(4):
        clock.t += 1.0
        spinner.advance()
        results.write(f"result {index}\n")
    spinner.finish()
    assert _writes_while_status_line_up(terminal.writes) == []
    assert "result 3\n" in terminal.writes  # the results themselves still land


def test_paused_erases_then_redraws_the_status_line() -> None:
    terminal = _Terminal()
    clock = _Clock()
    spinner = Spinner(stream=terminal, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=2)
    clock.t = 1.0
    spinner.advance()
    drawn = terminal.writes[-1]
    with spinner.paused():
        marker = len(terminal.writes)
    assert terminal.writes[marker - 1] == "\r\x1b[K"  # erased before the block
    assert terminal.writes[-1] == drawn  # the same line came back after


def test_paused_before_any_draw_touches_nothing() -> None:
    stream = io.StringIO()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=_Clock())
    spinner.start(total=2)
    with spinner.paused():
        pass
    assert stream.getvalue() == ""


def test_guard_is_a_passthrough_when_the_spinner_is_disabled() -> None:
    stream = io.StringIO()
    spinner = Spinner(stream=io.StringIO(), enabled=False, ascii_only=True, clock=_Clock())
    assert spinner.guard(stream) is stream


def test_spinner_disabled_when_stdout_is_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A piped stdout means another stage owns the terminal — no animation."""
    monkeypatch.setattr(tty, "stderr_is_tty", lambda: True)
    monkeypatch.setattr(tty, "stdout_is_tty", lambda: False)
    assert make_stderr_spinner().enabled is False


def test_spinner_disabled_when_stderr_is_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tty, "stderr_is_tty", lambda: False)
    monkeypatch.setattr(tty, "stdout_is_tty", lambda: True)
    assert make_stderr_spinner().enabled is False


def test_spinner_enabled_only_at_the_final_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tty, "stderr_is_tty", lambda: True)
    monkeypatch.setattr(tty, "stdout_is_tty", lambda: True)
    assert make_stderr_spinner().enabled is True


def test_guarded_flush_reaches_the_target() -> None:
    class _FlushCounter(io.StringIO):
        flushes = 0

        def flush(self) -> None:
            self.flushes += 1
            super().flush()

    target = _FlushCounter()
    spinner = Spinner(stream=io.StringIO(), enabled=True, ascii_only=True, clock=_Clock())
    guarded = spinner.guard(target)
    guarded.flush()
    assert target.flushes == 1
