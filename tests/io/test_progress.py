from __future__ import annotations

import io

from smartpipe.io.progress import (
    Spinner,
    format_eta,
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
