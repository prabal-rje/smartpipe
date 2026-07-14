from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest

from smartpipe.io import progress, tty
from smartpipe.io.progress import (
    Spinner,
    make_stderr_spinner,
    render_pending,
    render_unknown,
    set_stage_label,
    stage_label,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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
        # both the initial zero-state paint (D1) and the advance carry the segment
        assert stream.getvalue().count("   ↑100 ↓20 tok") == 2
    finally:
        metering.reset()


# --- Spinner behaviour --------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


# --- D1: start() paints the zero state immediately (the one rule, ux.md) -------


def test_start_paints_the_zero_state_bar_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """A known-total phase owns its bar from start(): the 0% zero state lands
    BEFORE the first completion — a stalled first item is visibly a stall."""
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=_Clock())
    spinner.start(total=4)
    assert stream.getvalue() == "\r[...............] 0% · 0/4\x1b[K"


def test_start_paints_the_zero_count_line_for_unknown_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=_Clock())
    spinner.start(total=None)
    assert stream.getvalue() == "\r- Processing [0] 0.0/s\x1b[K"


def test_start_paints_the_pending_caption_when_a_message_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    spinner = Spinner(
        stream=stream, enabled=True, ascii_only=True, clock=_Clock(), message="preparing"
    )
    spinner.start(total=None)
    assert stream.getvalue() == "\r- preparing\x1b[K"


def test_start_with_a_zero_total_paints_nothing() -> None:
    """start(0) = known-empty work: nothing to watch, nothing painted — and
    finish() stays silent too (keyed on _drew)."""
    stream = io.StringIO()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=_Clock())
    spinner.start(total=0)
    spinner.finish()
    assert stream.getvalue() == ""


def test_disabled_start_paints_nothing() -> None:
    stream = io.StringIO()
    spinner = Spinner(stream=stream, enabled=False, ascii_only=True, clock=_Clock())
    spinner.start(total=4)
    spinner.start(total=None)
    assert stream.getvalue() == ""


def test_initial_paint_counts_against_the_redraw_throttle() -> None:
    """The zero-state paint IS a draw: an advance in the same 100ms window is
    throttled, so start-then-first-item never double-draws."""
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=100)
    clock.t = 0.01
    spinner.advance()
    assert stream.getvalue().count("\r") == 1  # the initial paint, nothing else


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
    spinner.start(total=4)  # D1: paints the 0% zero state immediately
    clock.t = 2.0
    spinner.advance()  # done=1, rate 0.5/s → eta (3 / 0.5) = 6s
    assert stream.getvalue() == (
        "\r[...............] 0% · 0/4\x1b[K\r[==>............] 25% · 1/4 · 0.5/s · ~6s left\x1b[K"
    )


def test_unknown_total_draw_lines_are_the_pinned_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=None)  # D1: the initial paint consumes ASCII frame '-'
    clock.t = 2.0
    spinner.advance()  # the next frame is '\'
    assert stream.getvalue() == ("\r- Processing [0] 0.0/s\x1b[K\r\\ Processing [1] 0.5/s\x1b[K")


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
    spinner.start(total=4)  # D1: the zero state wears the label too
    clock.t = 2.0
    spinner.advance()
    assert stream.getvalue() == (
        "\r[extract] [...............] 0% · 0/4\x1b[K"
        "\r[extract] [==>............] 25% · 1/4 · 0.5/s · ~6s left\x1b[K"
    )


def test_stage_label_prefixes_the_unknown_total_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=clock, label="extract")
    spinner.start(total=None)  # D1: initial paint takes '-'; the advance draws '\'
    clock.t = 2.0
    spinner.advance()
    assert stream.getvalue() == (
        "\r[extract] - Processing [0] 0.0/s\x1b[K\r[extract] \\ Processing [1] 0.5/s\x1b[K"
    )


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
    spinner.start(total=None)  # D1: paints the pending caption immediately (frame '-')
    clock.t = 1.0
    spinner.tick()
    assert stream.getvalue() == "\r- preparing\x1b[K\r\\ preparing\x1b[K"
    assert spinner._done == 0  # pyright: ignore[reportPrivateUsage] — a wait is not progress


def test_tick_colorizes_the_frame_like_the_unknown_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    # C2 review NIT: an ambient NO_COLOR (CI shells export it) would strip the
    # very escapes this exact-byte pin asserts — make the color path hermetic.
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(
        stream=stream, enabled=True, ascii_only=True, clock=clock, message="preparing"
    )
    spinner.start(total=None)  # D1: the initial pending paint is colorized the same way
    clock.t = 1.0
    spinner.tick()
    assert stream.getvalue() == (
        "\r\x1b[36m-\x1b[0m preparing\x1b[K\r\x1b[36m\\\x1b[0m preparing\x1b[K"
    )


def test_tick_wears_the_stage_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    clock = _Clock()
    spinner = Spinner(
        stream=stream, enabled=True, ascii_only=True, clock=clock, message="warming", label="graph"
    )
    spinner.start(total=None)  # D1: the initial pending paint wears the label too
    clock.t = 1.0
    spinner.tick()
    assert stream.getvalue() == "\r[graph] - warming\x1b[K\r[graph] \\ warming\x1b[K"


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


# --- D2: the module arbiter — diagnostics never land under the status line -----


def _emit(terminal: _Terminal, text: str) -> Callable[[], None]:
    """A void emit callback (interject takes ``() -> None``; write returns int)."""

    def emit() -> None:
        terminal.write(text)

    return emit


def test_interject_erases_emits_and_redraws_around_the_active_line() -> None:
    terminal = _Terminal()
    clock = _Clock()
    spinner = Spinner(stream=terminal, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=4)
    clock.t = 1.0
    spinner.advance()
    drawn = terminal.writes[-1]
    marker = len(terminal.writes)
    progress.interject(_emit(terminal, "note: hello\n"))
    assert terminal.writes[marker] == "\r\x1b[K"  # erased first
    assert terminal.writes[marker + 1] == "note: hello\n"  # the line landed whole
    assert terminal.writes[-1] == drawn  # the same status line came back
    assert _writes_while_status_line_up(terminal.writes) == []


def test_interject_with_no_active_line_emits_plainly() -> None:
    terminal = _Terminal()
    progress.interject(_emit(terminal, "note: hello\n"))
    assert terminal.writes == ["note: hello\n"]


def test_finish_deregisters_the_active_line() -> None:
    terminal = _Terminal()
    clock = _Clock()
    spinner = Spinner(stream=terminal, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=2)
    clock.t = 1.0
    spinner.advance()
    spinner.finish()
    progress.interject(_emit(terminal, "note: after\n"))
    assert terminal.writes[-1] == "note: after\n"  # no erase, no redraw — plain


def test_interject_inside_a_guarded_write_emits_plainly() -> None:
    """The reentry guard: a diagnostic fired from INSIDE a guarded result write
    (the line is already erased) must land plainly — never redraw into a row the
    surrounding pause is about to redraw itself."""
    terminal = _Terminal()
    clock = _Clock()
    spinner = Spinner(stream=terminal, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=2)
    clock.t = 1.0
    spinner.advance()
    with spinner.paused():
        marker = len(terminal.writes)
        progress.interject(_emit(terminal, "note: nested\n"))
        assert terminal.writes[marker:] == ["note: nested\n"]  # plain — no erase/redraw
    assert terminal.writes[-1].startswith("\r")  # the pause exit redrew exactly once
    assert _writes_while_status_line_up(terminal.writes) == []


def test_cross_thread_advances_and_interjects_never_scribble() -> None:
    """A worker thread redrawing the bar races the loop thread's diagnostics —
    both must serialize under the arbiter lock, no write ever landing mid-row."""
    import threading

    terminal = _Terminal()
    clock = _Clock()
    spinner = Spinner(stream=terminal, enabled=True, ascii_only=True, clock=clock)
    spinner.start(total=None)
    done = threading.Event()

    def advancing() -> None:
        while not done.is_set():
            clock.t += 0.2
            spinner.advance()

    thread = threading.Thread(target=advancing)
    thread.start()
    try:
        for n in range(300):
            progress.interject(_emit(terminal, f"note: {n}\n"))
    finally:
        done.set()
        thread.join()
    spinner.finish()
    assert _writes_while_status_line_up(terminal.writes) == []
    assert "note: 299\n" in terminal.writes


async def test_ner_note_never_lands_against_the_pending_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact #32 repro: the local-NER pending caption is up (spin_pending)
    while a worker thread fires diagnostics.note — the visible text must never
    read '…modelnote:' (the note gluing onto the un-erased caption)."""
    import asyncio
    import re
    import sys
    import time

    from smartpipe.io import diagnostics
    from smartpipe.verbs.common import spin_pending

    monkeypatch.setenv("NO_COLOR", "1")
    terminal = _Terminal()
    monkeypatch.setattr(sys, "stderr", terminal)
    spinner = Spinner(stream=terminal, enabled=True, ascii_only=True, clock=time.monotonic)

    async def load_in_a_thread() -> None:
        # runs after spin_pending's start() painted the caption (start paints
        # synchronously before the first await), so the note truly collides
        await asyncio.to_thread(diagnostics.note, "3 files skipped")

    await spin_pending(spinner, "preparing local NER model", load_in_a_thread())
    plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", terminal.getvalue())
    assert "modelnote:" not in plain
    assert "note: 3 files skipped\n" in terminal.writes
    assert _writes_while_status_line_up(terminal.writes) == []


def test_paused_before_any_draw_touches_nothing() -> None:
    # start(0) is the never-paints arrange (D1 made start(total=2) paint)
    stream = io.StringIO()
    spinner = Spinner(stream=stream, enabled=True, ascii_only=True, clock=_Clock())
    spinner.start(total=0)
    with spinner.paused():
        pass
    assert stream.getvalue() == ""


def test_guard_is_a_passthrough_when_the_spinner_is_disabled() -> None:
    stream = io.StringIO()
    spinner = Spinner(stream=io.StringIO(), enabled=False, ascii_only=True, clock=_Clock())
    assert spinner.guard(stream) is stream


@pytest.mark.parametrize("endpoint_name", ["TERMINAL", "REGULAR_FILE", "NULL_DEVICE"])
def test_spinner_enabled_for_progress_safe_stdout(
    monkeypatch: pytest.MonkeyPatch, endpoint_name: str
) -> None:
    monkeypatch.setattr(tty, "stderr_is_tty", lambda: True)
    endpoint = getattr(tty.OutputEndpoint, endpoint_name)
    monkeypatch.setattr(tty, "stdout_allows_progress", lambda: tty.output_allows_progress(endpoint))
    assert make_stderr_spinner().enabled is True


@pytest.mark.parametrize("endpoint_name", ["FIFO", "SOCKET", "UNKNOWN"])
def test_spinner_disabled_for_progress_unsafe_stdout(
    monkeypatch: pytest.MonkeyPatch, endpoint_name: str
) -> None:
    monkeypatch.setattr(tty, "stderr_is_tty", lambda: True)
    endpoint = getattr(tty.OutputEndpoint, endpoint_name)
    monkeypatch.setattr(tty, "stdout_allows_progress", lambda: tty.output_allows_progress(endpoint))
    assert make_stderr_spinner().enabled is False


def test_spinner_disabled_when_stderr_is_piped_without_probing_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tty, "stderr_is_tty", lambda: False)

    def unexpected_probe() -> bool:
        raise AssertionError("short-circuit must not inspect irrelevant stdout")

    monkeypatch.setattr(tty, "stdout_allows_progress", unexpected_probe)
    assert make_stderr_spinner().enabled is False


def test_make_stderr_spinner_wears_the_current_stage_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tty, "stderr_is_tty", lambda: True)
    monkeypatch.setattr(tty, "stdout_allows_progress", lambda: True)
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
