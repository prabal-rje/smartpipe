"""Every user-facing stderr message flows through here (stdout stays sacred).

Message style contract: plan/ux.md "Error message style" — one-line what, short
why, copy-pasteable fix. Screens arrive pre-formatted with their own ``error:``
prefix; bare fault messages get the prefix added.
"""

from __future__ import annotations

import sys
import traceback
from typing import NoReturn

from sempipe.core.errors import (
    ExitCode,
    SempipeError,
    SetupFault,
    TooManyFailures,
    UsageFault,
)
from sempipe.io import tty

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
_ISSUES_URL = "https://github.com/prabal-rje/sempipe/issues/new"


def warn(message: str) -> None:
    sys.stderr.write(f"⚠ {message}\n")
    sys.stderr.flush()


def preview(message: str) -> None:
    """Informational cost/awareness lines (D18/D21): TTY-only, never in pipes/cron."""
    if tty.stderr_is_tty():
        sys.stderr.write(f"{message}\n")
        sys.stderr.flush()


def note(message: str) -> None:
    sys.stderr.write(f"note: {message}\n")
    sys.stderr.flush()


def interrupted_summary(*, processed: int, skipped: int) -> None:
    """The ux.md §12 drain summary — exact wording is contract."""
    sys.stderr.write(f"done: interrupted — {processed} processed · {skipped} skipped\n")
    sys.stderr.flush()


def drain_timed_out() -> None:
    sys.stderr.write("done: interrupted — drain timed out\n")
    sys.stderr.flush()


def _emit_error(text: str) -> None:
    if tty.stderr_supports_color() and text.startswith("error:"):
        text = f"{_RED}error:{_RESET}{text.removeprefix('error:')}"
    sys.stderr.write(f"{text}\n")
    sys.stderr.flush()


_DEGRADE_CAP = 5  # per conversion kind: first rows verbatim, then the rollup


class DegradationLog:
    """Per-run ledger of poor-man's conversions (D27): every degraded row is
    announced (capped per kind), and one rollup line closes the run."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def note(self, where: str, kind: str, detail: str) -> None:
        count = self.counts.get(kind, 0) + 1
        self.counts[kind] = count
        if count <= _DEGRADE_CAP:
            warn(f"degraded: {where} {kind} ({detail})")
        elif count == _DEGRADE_CAP + 1:
            warn(f"more {kind} rows follow (suppressed; the rollup lands at the end)")

    def finish(self) -> None:
        if not self.counts:
            return
        ranked = sorted(self.counts.items(), key=lambda pair: -pair[1])
        marks = " · ".join(f"{kind} ×{count:,}" for kind, count in ranked)  # noqa: RUF001 — the pinned rollup mark
        note(f"degraded: {marks}")


def report_error(screen: str) -> None:
    """Emit a full error screen without exiting — for commands that own their
    exit code after cleanup (e.g. ``sempipe schema``'s empty-stdout guarantee)."""
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
        case _:
            # ItemError (or the bare base) reaching die() is a programming error:
            # per the taxonomy those are handled by the runner, not fatal paths.
            raise SystemExit(int(ExitCode.BUG))


def internal_error(exc: BaseException, *, debug: bool) -> NoReturn:
    summary = f"{type(exc).__name__}: {exc}".splitlines()[0]
    _emit_error("error: internal error — this is a bug in sempipe, not in your usage")
    sys.stderr.write(f"  {summary}\n")
    if debug:
        sys.stderr.write("".join(traceback.format_exception(exc)))
        sys.stderr.write(f"  Please report it: {_ISSUES_URL}\n")
    else:
        sys.stderr.write("  Rerun with --debug for the full traceback, and please report it:\n")
        sys.stderr.write(f"  {_ISSUES_URL}\n")
    sys.stderr.flush()
    raise SystemExit(int(ExitCode.BUG))
