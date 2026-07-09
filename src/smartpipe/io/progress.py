"""Progress feedback — always stderr, always TTY-gated, gone on completion.

Spec §6.1 + ledger item 67: one status line overwritten in place — a
determinate bar (``engine/progressbar``) when the total is known, the running
count + rate when it isn't. The animation renders only in a pipeline's FINAL
stage — stderr and stdout both terminals. A piped stdout (mid-pipe stage) or a
piped stderr (cron) suppresses it entirely — stdout stays sacred, the log
stays clean, and two smartpipes in one pipe never fight over the terminal row.
The render functions are pure; ``Spinner`` adds the clock, throttling, and the
stderr writes.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smartpipe.engine.progressbar import render_bar
from smartpipe.io import tty

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from typing import TextIO

    from smartpipe.io.writers import TextSink

__all__ = [
    "Spinner",
    "make_stderr_spinner",
    "render_unknown",
    "set_stage_label",
    "stage_label",
]

_BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_ASCII = "-\\|/"
_MIN_REDRAW_S = 0.1  # ≤ 10 fps
_CLEAR_LINE = "\x1b[K"

# The current pipeline stage's name — set by ``cli/run_cmd`` around each stage
# so a stage's status line wears the same ``[name]`` prefix its receipts do.
# A metering-style documented exception to no-globals (one stage at a time).
_stage_label: str | None = None


def set_stage_label(name: str | None) -> None:
    """Name the pipeline stage whose status lines are being drawn (None clears)."""
    global _stage_label
    _stage_label = name


def stage_label() -> str | None:
    return _stage_label


def render_unknown(
    frame: str, *, done: int, rate: float, matched: int | None = None, extra: str | None = None
) -> str:
    line = f"{frame} Processing [{done}] {rate:.1f}/s"
    if matched is not None:
        line += f" · {matched} matched"
    if extra:
        line += f" · {extra}"
    return line


@dataclass(slots=True)
class Spinner:
    stream: TextIO
    enabled: bool
    ascii_only: bool
    clock: Callable[[], float]
    total: int | None = None
    matched: int | None = None  # filter's status-line segment
    extra: str | None = None  # map's live --tally segment
    label: str | None = None  # pipeline stage name — prefixes the line like receipts
    _done: int = 0
    _start: float = 0.0
    _last_draw: float = field(default=-1.0)
    _frame: int = 0
    _drew: bool = False
    _line: str = ""  # last rendered status line, redrawn verbatim after paused()

    def start(self, total: int | None) -> None:
        self.total = total
        self._done = 0
        self._start = self.clock()
        self._last_draw = -1.0

    def advance(self) -> None:
        self._done += 1
        if not self.enabled:
            return
        now = self.clock()
        is_last = self.total is not None and self._done >= self.total
        if not is_last and now - self._last_draw < _MIN_REDRAW_S:
            return
        self._last_draw = now
        self._draw(now)

    def finish(self) -> None:
        if self.enabled and self._drew:
            self.stream.write(f"\r{_CLEAR_LINE}")
            self.stream.flush()

    @contextmanager
    def paused(self) -> Generator[None]:
        """The terminal arbiter primitive: erase the status line, let the caller
        own the terminal, then redraw the same line. Result emission wraps itself
        in this so no result byte ever lands between a draw and its erase."""
        if not self._drew:
            yield
            return
        self.stream.write(f"\r{_CLEAR_LINE}")
        self.stream.flush()
        try:
            yield
        finally:
            self.stream.write(f"\r{self._line}{_CLEAR_LINE}")
            self.stream.flush()

    def guard(self, stream: TextSink) -> TextSink:
        """Route a result stream through the arbiter: each write pauses the
        status line. A disabled spinner returns the stream untouched — piped
        runs pay nothing."""
        if not self.enabled:
            return stream
        return _GuardedSink(target=stream, spinner=self)

    def _color(self) -> bool:
        import os

        return self.enabled and not os.environ.get("NO_COLOR")

    def _draw(self, now: float) -> None:
        frames = _ASCII if self.ascii_only else _BRAILLE
        frame = frames[self._frame % len(frames)]
        self._frame += 1
        elapsed = max(now - self._start, 1e-9)
        rate = self._done / elapsed
        color = self._color()
        if self.total is None:
            line = render_unknown(
                frame, done=self._done, rate=rate, matched=self.matched, extra=self.extra
            )
            if color:
                line = f"\x1b[36m{frame}\x1b[0m{line[len(frame) :]}"
            if self.label is not None:
                line = f"[{self.label}] {line}"
        else:
            line = render_bar(
                self._done,
                self.total,
                rate=rate,
                ascii_only=self.ascii_only,
                label=self.label,
            )
        from smartpipe.io import metering

        consumed = metering.status_segment()  # D40: live observed units
        if consumed:
            line += f"   \x1b[2m{consumed}\x1b[0m" if color else f"   {consumed}"
        self._line = line
        self.stream.write(f"\r{line}{_CLEAR_LINE}")
        self.stream.flush()
        self._drew = True


@dataclass(frozen=True, slots=True)
class _GuardedSink:
    """A result stream routed through the arbiter: writes never land under the
    status line. The target is flushed inside the pause so the bytes reach the
    terminal before the line is redrawn."""

    target: TextSink
    spinner: Spinner

    def write(self, s: str, /) -> int:
        with self.spinner.paused():
            count = self.target.write(s)
            self.target.flush()
        return count

    def flush(self) -> None:
        self.target.flush()


def make_stderr_spinner() -> Spinner:
    """A spinner wired to the real stderr — animated only in a pipeline's final
    stage (stderr AND stdout both TTYs; a piped stdout means a downstream process
    owns the terminal, so mid-pipe stages keep line-atomic notes and the receipt
    but never a ``\\r`` animation), with a Braille or ASCII frame set depending
    on the encoding. Inside a ``run`` pipeline the stage's name rides along so
    any drawn line wears the same ``[name]`` prefix its receipts do."""
    encoding = (sys.stderr.encoding or "").lower()
    return Spinner(
        stream=sys.stderr,
        enabled=tty.stderr_is_tty() and tty.stdout_is_tty(),
        ascii_only="utf" not in encoding,
        clock=time.monotonic,
        label=stage_label(),
    )
