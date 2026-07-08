"""Progress feedback — always stderr, always TTY-gated, gone on completion.

Spec §6.1: a single spinner line overwritten in place, with count, percent, and
an ETA that appears only after a few completions. When stderr is not a terminal
(a cron job, a pipe), progress is suppressed entirely — stdout stays sacred and
the log stays clean. The render functions are pure; ``Spinner`` adds the clock,
throttling, and the stderr writes.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smartpipe.io import tty

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from typing import TextIO

    from smartpipe.io.writers import TextSink

__all__ = ["Spinner", "format_eta", "make_stderr_spinner", "render_known", "render_unknown"]

_BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_ASCII = "-\\|/"
_ETA_WARMUP = 5  # completions before an ETA is trustworthy enough to show
_MIN_REDRAW_S = 0.1  # ≤ 10 fps
_CLEAR_LINE = "\x1b[K"


def format_eta(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes}m"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"


def render_known(frame: str, *, done: int, total: int, eta_seconds: float | None) -> str:
    percent = int(done / total * 100) if total else 100
    line = f"{frame} Processing {total} items [{done}/{total}] {percent}%"
    if eta_seconds is not None:
        line += f"  ~{format_eta(eta_seconds)} remaining"
    return line


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
        if self.total is None:
            line = render_unknown(
                frame, done=self._done, rate=rate, matched=self.matched, extra=self.extra
            )
        else:
            eta = (self.total - self._done) / rate if self._done >= _ETA_WARMUP and rate else None
            line = render_known(frame, done=self._done, total=self.total, eta_seconds=eta)
        from smartpipe.io import metering

        consumed = metering.status_segment()  # D40: live observed units
        if self._color():
            line = f"\x1b[36m{frame}\x1b[0m{line[len(frame) :]}"
            if consumed:
                line += f"   \x1b[2m{consumed}\x1b[0m"
        elif consumed:
            line += f"   {consumed}"
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
    """A spinner wired to the real stderr — enabled only when stderr is a TTY,
    with a Braille or ASCII frame set depending on the encoding."""
    encoding = (sys.stderr.encoding or "").lower()
    return Spinner(
        stream=sys.stderr,
        enabled=tty.stderr_is_tty(),
        ascii_only="utf" not in encoding,
        clock=time.monotonic,
    )
