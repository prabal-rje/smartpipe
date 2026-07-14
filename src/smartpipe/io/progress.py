"""Progress feedback — always stderr, always TTY-gated, gone on completion.

Spec §6.1 + ledger item 67: one status line overwritten in place — a
determinate bar (``engine/progressbar``) when the total is known, the running
count + rate when it isn't. Animation requires terminal stderr and a
progress-safe stdout endpoint: terminal, regular-file redirects, and the null
device animate; process pipes, sockets, and unknown endpoints suppress it. A
piped stderr (cron, ``2>log``) also suppresses it entirely — stdout stays sacred
and the log stays clean. The render functions are pure; ``Spinner`` adds the
clock, throttling, and the stderr writes.
"""

from __future__ import annotations

import sys
import threading
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
    "interject",
    "make_stderr_spinner",
    "render_pending",
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

# The terminal arbiter (C2 #32) — the same documented exception, and for the
# same reason: one terminal, at most one live status line at a time. ``_active``
# is whichever spinner last painted; ``interject`` erases it, lets a diagnostic
# own the row, and redraws it. The RLock (never a plain Lock) serializes redraws
# against diagnostics from worker threads AND survives the same-thread nesting
# ``interject → paused()`` needs; it is never held across an ``await`` (every
# guarded body is synchronous). ``_suspended`` marks "the line is currently
# erased" (inside ``paused``): a diagnostic fired from within a guarded result
# write then emits plainly instead of redrawing into a row the surrounding
# pause is about to redraw itself.
_lock = threading.RLock()
_active: Spinner | None = None
_suspended = False


def interject(emit: Callable[[], None]) -> None:
    """Route one whole stderr line around the live status line: erase → emit →
    redraw, atomically under the arbiter lock. With no line up (piped stderr,
    cron, or between stages) this is a plain pass-through — byte-identical
    output. ``diagnostics`` routes every one-line message through here; the raw
    SIGINT acknowledgement deliberately does NOT (it must never take a lock)."""
    with _lock:
        active = _active
        if active is None or _suspended:
            emit()
            return
        with active.paused():
            emit()


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


def render_pending(frame: str, message: str) -> str:
    """An indeterminate wait line: the spinner frame then a caller-owned message
    (no count, no rate — nothing is being iterated, just held)."""
    return f"{frame} {message}"


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
    message: str | None = None  # the indeterminate-wait caption drawn by tick()
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
        self._draw_initial()

    def _draw_initial(self) -> None:
        """D1 (the one rule, ux.md): the first paint happens AT ``start`` — a
        phase that stalls before its first completion still owns a visible zero
        state. A set ``message`` paints the pending caption; a known nonzero
        total paints the ``0% · 0/N`` bar; an unknown total paints
        ``Processing [0]``; a known-empty total (0) paints nothing — there is
        no work to watch. Only ``start`` paints eagerly: construction never
        writes, so a bar built and abandoned stays silent."""
        if not self.enabled:
            return
        if self.message is not None:
            self.tick()  # start() reset the throttle, so this always paints
            return
        if self.total == 0:
            return
        now = self.clock()
        self._last_draw = now  # the zero state counts against the redraw throttle
        self._draw(now)

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

    def tick(self) -> None:
        """Redraw the indeterminate ``message`` line, cycling the frame — the
        animation for a blocking wait (a model load) where nothing is being
        iterated. Throttled like ``advance`` and never touches ``_done``, so a
        pending wait is not miscounted as progress."""
        if not self.enabled:
            return
        now = self.clock()
        if now - self._last_draw < _MIN_REDRAW_S:
            return
        self._last_draw = now
        global _active
        with _lock:
            frame = self._next_frame()
            line = render_pending(frame, self.message or "")
            if self._color():
                line = f"\x1b[36m{frame}\x1b[0m{line[len(frame) :]}"
            if self.label is not None:
                line = f"[{self.label}] {line}"
            self._line = line
            self.stream.write(f"\r{line}{_CLEAR_LINE}")
            self.stream.flush()
            self._drew = True
            _active = self

    def finish(self) -> None:
        global _active
        with _lock:
            if _active is self:
                _active = None  # deregister — later diagnostics emit plainly
            if self.enabled and self._drew:
                self.stream.write(f"\r{_CLEAR_LINE}")
                self.stream.flush()

    @contextmanager
    def paused(self) -> Generator[None]:
        """The terminal arbiter primitive: erase the status line, let the caller
        own the terminal, then redraw the same line. Result emission wraps itself
        in this so no result byte ever lands between a draw and its erase; the
        whole span holds the arbiter lock (reentrant — ``interject`` nests here)
        and marks ``_suspended`` so a diagnostic fired from inside the body
        lands plainly on the already-erased row."""
        global _suspended
        with _lock:
            if not self._drew:
                yield
                return
            self.stream.write(f"\r{_CLEAR_LINE}")
            self.stream.flush()
            was_suspended = _suspended
            _suspended = True
            try:
                yield
            finally:
                _suspended = was_suspended
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

    def _next_frame(self) -> str:
        frames = _ASCII if self.ascii_only else _BRAILLE
        frame = frames[self._frame % len(frames)]
        self._frame += 1
        return frame

    def _draw(self, now: float) -> None:
        global _active
        with _lock:
            frame = self._next_frame()
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
            _active = self


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
    """A spinner wired to stderr and gated by both terminal streams' roles.

    Stderr must be a TTY. Stdout must be a terminal, regular file, or null device;
    FIFO/socket/unknown endpoints suppress animation because this process's
    arbiter cannot coordinate with a downstream process writing to the same
    terminal. The stderr check stays first so cron and in-process ``run`` stages
    never inspect an irrelevant rebound stdout. Inside a ``run`` pipeline the
    stage's name rides along so any drawn line wears its receipt prefix.
    """
    encoding = (sys.stderr.encoding or "").lower()
    return Spinner(
        stream=sys.stderr,
        enabled=tty.stderr_is_tty() and tty.stdout_allows_progress(),
        ascii_only="utf" not in encoding,
        clock=time.monotonic,
        label=stage_label(),
    )
