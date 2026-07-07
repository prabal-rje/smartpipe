"""The live top_k leaderboard (stage-08 §4.3): a K-line block repainted in place.

``render_frame`` is pure (goldens pin it); ``LiveBoard`` adds the clock, the
≤4-repaints/s throttle, and the ANSI cursor-up block rewrite. TTY only — pipe
mode uses NDJSON snapshots through the ordinary writer instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import TextIO

__all__ = ["LiveBoard", "render_frame"]

_CLEAR_LINE = "\x1b[K"
_MIN_REPAINT_S = 0.25  # ≤ 4 repaints/s (spec) — dumb full-block rewrite, no partials


def render_frame(rows: Sequence[tuple[float, str]], width: int) -> list[str]:
    """``(score, text)`` rows → display lines, truncated to the terminal width."""
    lines: list[str] = []
    for score, text in rows:
        prefix = f"{score:0.2f}  "
        budget = max(width - len(prefix), 8)
        body = text if len(text) <= budget else text[: budget - 1] + "…"
        lines.append(prefix + body)
    return lines


@dataclass(slots=True)
class LiveBoard:
    stream: TextIO
    width: int
    clock: Callable[[], float]
    _painted: int = 0  # lines currently on screen
    _last: float = field(default=-1.0)

    def paint(self, rows: Sequence[tuple[float, str]], *, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._last < _MIN_REPAINT_S:
            return
        self._last = now
        if self._painted:
            self.stream.write(f"\x1b[{self._painted}A")  # cursor up over the old block
        for line in render_frame(rows, self.width):
            self.stream.write(f"\r{line}{_CLEAR_LINE}\n")
        self._painted = len(rows)
        self.stream.flush()
