"""Every user-facing stderr message flows through here (stdout stays sacred).

Message style contract: plan/ux.md "Error message style" — one-line what, short
why, copy-pasteable fix. Screens arrive pre-formatted with their own ``error:``
prefix; bare fault messages get the prefix added.
"""

from __future__ import annotations

import sys
import traceback
from typing import NoReturn

from smartpipe.core.errors import (
    ExitCode,
    SempipeError,
    SetupFault,
    TooManyFailures,
    UnsentError,
    UsageFault,
)
from smartpipe.io import tty

__all__ = [
    "DegradationLog",
    "die",
    "drain_timed_out",
    "internal_error",
    "interrupted_summary",
    "note",
    "preview",
    "report_error",
    "warn",
]

_RED = "\x1b[31m"
_RESET = "\x1b[0m"
_ISSUES_URL = "https://github.com/prabal-rje/smartpipe/issues/new"


def _paint(text: str, code: str) -> str:
    """stderr color, honestly gated: TTY only, NO_COLOR wins (D42)."""
    import os

    if not tty.stderr_is_tty() or os.environ.get("NO_COLOR"):
        return text
    return f"\x1b[{code}m{text}{_RESET}"


def _emit_line(text: str) -> None:
    """Every one-line diagnostic rides the terminal arbiter (C2 #32): the live
    status line is erased, the line lands whole, the status line redraws — from
    any thread (local NER fires notes from a worker). With no line up this is a
    plain byte-identical write. The import is lazy and one-way: diagnostics →
    progress only; progress must NEVER import diagnostics."""
    from smartpipe.io import progress

    def emit() -> None:
        sys.stderr.write(text)
        sys.stderr.flush()

    progress.interject(emit)


def warn(message: str) -> None:
    _emit_line(_paint(f"⚠ {message}", "33") + "\n")  # yellow — worth a glance


def preview(message: str) -> None:
    """Informational cost/awareness lines (D18/D21): TTY-only, never in pipes/cron."""
    if tty.stderr_is_tty():
        _emit_line(f"{message}\n")


def note(message: str) -> None:
    _emit_line(_paint(f"note: {message}", "2") + "\n")  # dim — informative, calm


def interrupted_summary(*, processed: int, skipped: int) -> None:
    """The ux.md §12 drain summary — exact wording is contract."""
    _emit_line(
        _paint(f"done: interrupted — {processed} processed · {skipped} skipped", "33") + "\n"
    )


def drain_timed_out() -> None:
    # fires from the watchdog task ON the loop thread — safe to take the arbiter
    # lock, unlike the raw SIGINT ack (cli/interrupts), which never may.
    _emit_line("done: interrupted — drain timed out\n")


def _emit_error(text: str) -> None:
    if tty.stderr_supports_color() and text.startswith("error:"):
        text = f"{_RED}error:{_RESET}{text.removeprefix('error:')}"
    # the screen's FIRST write rides the arbiter; the follow-up context lines
    # (die's --debug traceback, internal_error's report pointer) stay raw —
    # by then the status line is out of the way.
    _emit_line(f"{text}\n")


_DEGRADE_CAP = 5  # per conversion kind: first rows verbatim, then the rollup


class DegradationLog:
    """Per-run ledger of poor-man's conversions (D27) and per-item skips (B4):
    every degraded row and every skip is announced (capped per kind/reason), and
    one rollup line per bucket closes the run — so a corpus of identical outcomes
    stops repeating one absolute-path line apiece."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.skips: dict[str, int] = {}

    def note(self, where: str, kind: str, detail: str) -> None:
        count = self.counts.get(kind, 0) + 1
        self.counts[kind] = count
        if count <= _DEGRADE_CAP:
            warn(f"degraded: {where} {kind} ({detail})")
        elif count == _DEGRADE_CAP + 1:
            warn(f"more {kind} rows follow (suppressed; the rollup lands at the end)")

    def skip(self, where: str, reason: str) -> None:
        """Bucket a per-item skip the way ``note`` buckets a degrade: the first
        ``_DEGRADE_CAP`` per reason PREFIX print verbatim (full reason kept), then
        one suppression line, then ``finish`` rolls the rest up. Keying on the
        prefix (the human phrase before any echoed instance) collapses a run of
        identical failures that differ only in the blob or the source path (B4)."""
        key = _reason_key(reason)
        count = self.skips.get(key, 0) + 1
        self.skips[key] = count
        if count <= _DEGRADE_CAP:
            warn(f"skipped: {where} ({reason})")
        elif count == _DEGRADE_CAP + 1:
            warn(f"more {key} skips follow (suppressed; the rollup lands at the end)")

    def finish(self) -> None:
        _rollup("degraded", self.counts)
        _rollup("skipped", self.skips)


def _reason_key(reason: str) -> str:
    """The stable head of a skip reason — the human phrase before any echoed
    instance/data (split at the first colon), so identical failures bucket
    together regardless of the blob that follows."""
    head = reason.split(":", 1)[0].strip()
    return head or reason.strip()


def _rollup(label: str, counts: dict[str, int]) -> None:
    if not counts:
        return
    ranked = sorted(counts.items(), key=lambda pair: -pair[1])
    marks = " · ".join(f"{name} ×{count:,}" for name, count in ranked)  # noqa: RUF001 — the pinned rollup mark
    note(f"{label}: {marks}")


def report_error(screen: str) -> None:
    """Emit a full error screen without exiting — for commands that own their
    exit code after cleanup (e.g. ``smartpipe schema``'s empty-stdout guarantee)."""
    _emit_error(screen if screen.startswith("error:") else f"error: {screen}")


def die(fault: SempipeError, *, debug: bool = False) -> NoReturn:
    message = str(fault)
    _emit_error(message if message.startswith("error:") else f"error: {message}")
    if debug:
        sys.stderr.write("".join(traceback.format_exception(fault)))
        sys.stderr.flush()
    match fault:
        case UsageFault():
            raise SystemExit(int(ExitCode.USAGE))
        case SetupFault():
            raise SystemExit(int(ExitCode.SETUP))
        case TooManyFailures():
            raise SystemExit(int(ExitCode.ALL_FAILED))
        case UnsentError():
            # A read-phase belt exhaustion (--max-calls hit mid-OCR, A5.1) escapes
            # the reader as itself and is caught per-item by every streaming verb;
            # a whole-set verb (graph) reads into a list, so it can reach here with
            # nothing produced yet. Stop ALL_FAILED with the belt truth, never the
            # BUG screen a stray item error would otherwise get.
            raise SystemExit(int(ExitCode.ALL_FAILED))
        case _:
            # ItemError (or the bare base) reaching die() is a programming error:
            # per the taxonomy those are handled by the runner, not fatal paths.
            raise SystemExit(int(ExitCode.BUG))


def internal_error(exc: BaseException, *, debug: bool) -> NoReturn:
    summary = f"{type(exc).__name__}: {exc}".splitlines()[0]
    _emit_error("error: internal error — this is a bug in smartpipe, not in your usage")
    sys.stderr.write(f"  {summary}\n")
    if debug:
        sys.stderr.write("".join(traceback.format_exception(exc)))
        sys.stderr.write(f"  Please report it: {_ISSUES_URL}\n")
    else:
        sys.stderr.write("  Rerun with --debug for the full traceback, and please report it:\n")
        sys.stderr.write(f"  {_ISSUES_URL}\n")
    sys.stderr.flush()
    raise SystemExit(int(ExitCode.BUG))
