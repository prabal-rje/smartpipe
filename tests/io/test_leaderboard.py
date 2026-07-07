from __future__ import annotations

import io

from smartpipe.io.leaderboard import LiveBoard, render_frame


def test_render_frame_is_pure_and_bounded() -> None:
    lines = render_frame([(1.0, "abc")], width=40)
    assert lines == ["1.00  abc"]
    assert render_frame([], width=40) == []


def test_paint_throttles_then_forces() -> None:
    now = {"t": 0.0}
    out = io.StringIO()
    board = LiveBoard(stream=out, width=40, clock=lambda: now["t"])
    board.paint([(0.9, "first")])
    first_len = len(out.getvalue())
    assert "0.90  first" in out.getvalue()

    board.paint([(0.9, "second")])  # same instant: throttled, nothing written
    assert len(out.getvalue()) == first_len

    board.paint([(0.9, "second")], force=True)  # force bypasses the throttle
    assert "0.90  second" in out.getvalue()
    assert "\x1b[1A" in out.getvalue()  # cursor-up over the previous 1-line block

    now["t"] = 1.0
    board.paint([(0.9, "third"), (0.5, "fourth")])  # time passed: repaints, 2 lines
    assert "third" in out.getvalue() and "fourth" in out.getvalue()
