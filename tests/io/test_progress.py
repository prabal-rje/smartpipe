from __future__ import annotations

import io

import pytest

from smartpipe.io import tty
from smartpipe.io.progress import (
    Spinner,
    make_stderr_spinner,
    render_pending,
    render_unknown,
    set_stage_label,
    stage_label,
)

# --- pure formatting ----------------------------------------------------------


def test_render_unknown_shows_rate() -> None:
    assert render_unknown("⠋", done=243, rate=3.1) == "⠋ Processing [243] 3.1/s"


def test_render_unknown_carries_matched_and_extra_segments() -> None:
    line = render_unknown("⠋", done=9, rate=3.0, matched=4, extra="bug 3 · praise 1")
    assert line == "⠋ Processing [9] 3.0/s · 4 matched · bug 3 · praise 1"


def test_render_pending_is_frame_then_message() -> None:
    assert render_pending("⠋", "preparing local NER model") == "⠋ preparing local NER model"


def test_draw_appends_the_live_metering_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    from smartpipe.io import metering

    monkeypatch.setenv("NO_COLOR", "1")
    metering.add_tokens(tokens_in=100, tokens_out=20)
    try:
        stream = io.StringIO()
        clock = _Clock()
        spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock)
        spinner.start(total=4)
        clock.t = 2.0
        spinner.advance()
        assert stream.getvalue().count("   ↑100 ↓20 tok") == 1
    finally:
        metering.reset()


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


def test_enabled_spinner_draws_the_bar_and_clears() -> None:
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=3)
    clock.t = 1.0
    spinner.advance()
    output = stream.getvalue()
    assert "\r" in output
    assert "33% · 1/3" in output  # the determinate bar, not the old count line
    assert "Processing" not in output
    spinner.finish()
    assert stream.getvalue().endswith("\x1b[K")  # line cleared on finish


def test_known_total_draw_is_the_pinned_bar_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=4)
    clock.t = 2.0
    spinner.advance()  # done=1, rate 0.5/s → eta (3 / 0.5) = 6s
    assert stream.getvalue() == "\r[==>............] 25% · 1/4 · 0.5/s · ~6s left\x1b[K"


def test_unknown_total_draw_is_byte_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=None)
    clock.t = 2.0
    spinner.advance()
    assert stream.getvalue() == "\r- Processing [1] 0.5/s\x1b[K"


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
    assert "100% · 2/2" in stream.getvalue()


def test_stage_label_prefixes_the_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock, label="extract")
    spinner.start(total=4)
    clock.t = 2.0
    spinner.advance()
    assert stream.getvalue().startswith("\r[extract] [==>............] 25% · 1/4")


def test_stage_label_prefixes_the_unknown_total_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock, label="extract")
    spinner.start(total=None)
    clock.t = 2.0
    spinner.advance()
    assert stream.getvalue() == "\r[extract] - Processing [1] 0.5/s\x1b[K"


def test_unknown_total_spinner_shows_running_count_and_rate() -> None:
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=None)
    clock.t = 2.0
    spinner.advance()
    assert "Processing [1]" in stream.getvalue()
    assert "/s" in stream.getvalue()


# --- the pending tick: a caller-owned status row for a blocking wait -----------


def test_tick_draws_the_pending_line_without_counting_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(
        stream=stream, enabled=True, ascii_only=True, clock=clock, message="preparing"
    )
    spinner.start(total=None)
    clock.t = 1.0
    spinner.tick()
    assert stream.getvalue() == "\r- preparing\x1b[K"
    assert spinner._done == 0  # pyright: ignore[reportPrivateUsage] — a wait is not progress


def test_tick_colorizes_the_frame_like_the_unknown_bar() -> None:
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(
        stream=stream, enabled=True, ascii_only=True, clock=clock, message="preparing"
    )
    spinner.start(total=None)
    clock.t = 1.0
    spinner.tick()
    assert stream.getvalue() == "\r\x1b[36m-\x1b[0m preparing\x1b[K"


def test_tick_wears_the_stage_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(
        stream=stream, enabled=True, ascii_only=True, clock=clock, message="warming", label="graph"
    )
    spinner.start(total=None)
    clock.t = 1.0
    spinner.tick()
    assert stream.getvalue() == "\r[graph] - warming\x1b[K"


def test_disabled_spinner_tick_writes_nothing() -> None:
    stream = io.StringIO()
    spinner = Spinner(
        stream=stream, enabled=False, ascii_only=True, clock=_Clock(), message="preparing"
    )
    spinner.start(total=None)
    for _ in range(5):
        spinner.tick()
    assert stream.getvalue() == ""


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


def test_spinner_enabled_when_only_stdout_is_redirected(monkeypatch: pytest.MonkeyPatch) -> None:
    """B3 re-pin: the bar lives on stderr, so redirecting stdout (``graph … >
    edges.jsonl``, the verb's normal usage) must NOT suppress it — like curl/rsync
    showing progress on stderr while stdout is piped. The gate keys on stderr alone."""
    monkeypatch.setattr(tty, "stderr_is_tty", lambda: True)
    monkeypatch.setattr(tty, "stdout_is_tty", lambda: False)
    assert make_stderr_spinner().enabled is True


def test_spinner_disabled_when_stderr_is_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A piped/redirected stderr (cron, ``2>log``) still suppresses the animation
    entirely — stdout being a TTY is irrelevant now that the gate is stderr-only."""
    monkeypatch.setattr(tty, "stderr_is_tty", lambda: False)
    monkeypatch.setattr(tty, "stdout_is_tty", lambda: True)
    assert make_stderr_spinner().enabled is False


def test_spinner_enabled_when_stderr_is_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tty, "stderr_is_tty", lambda: True)
    monkeypatch.setattr(tty, "stdout_is_tty", lambda: True)
    assert make_stderr_spinner().enabled is True


def test_make_stderr_spinner_wears_the_current_stage_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tty, "stderr_is_tty", lambda: True)
    set_stage_label("extract")
    try:
        assert make_stderr_spinner().label == "extract"
        assert stage_label() == "extract"
    finally:
        set_stage_label(None)
    assert make_stderr_spinner().label is None


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
