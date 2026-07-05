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

__all__ = ["die", "internal_error", "note", "warn"]

_RED = "\x1b[31m"
_RESET = "\x1b[0m"
_ISSUES_URL = "https://github.com/prabal-rje/sempipe/issues/new"


def warn(message: str) -> None:
    sys.stderr.write(f"⚠ {message}\n")
    sys.stderr.flush()


def note(message: str) -> None:
    sys.stderr.write(f"note: {message}\n")
    sys.stderr.flush()


def _emit_error(text: str) -> None:
    if tty.stderr_supports_color() and text.startswith("error:"):
        text = f"{_RED}error:{_RESET}{text.removeprefix('error:')}"
    sys.stderr.write(f"{text}\n")
    sys.stderr.flush()


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
