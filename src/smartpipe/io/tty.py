"""Terminal detection and the color decision.

``supports_color`` is pure — the environment is a parameter — so the whole truth
table is testable. The thin wrappers read real process state at call time.
"""

from __future__ import annotations

import os
import shutil
import sys
from enum import StrEnum
from typing import TYPE_CHECKING, assert_never

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import TextIO

__all__ = [
    "ColorMode",
    "enable_windows_vt",
    "stderr_is_tty",
    "stderr_supports_color",
    "stdout_is_tty",
    "stdout_supports_color",
    "supports_color",
    "terminal_width",
    "tty_asker",
]


class ColorMode(StrEnum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


def stdout_is_tty() -> bool:
    return sys.stdout.isatty()


def stderr_is_tty() -> bool:
    return sys.stderr.isatty()


def supports_color(stream_is_tty: bool, *, mode: ColorMode, env: Mapping[str, str]) -> bool:
    match mode:
        case ColorMode.ALWAYS:
            return True
        case ColorMode.NEVER:
            return False
        case ColorMode.AUTO:
            if not stream_is_tty:
                return False
            if "NO_COLOR" in env:  # any value disables — https://no-color.org
                return False
            return env.get("TERM") != "dumb"
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)


def stderr_supports_color(mode: ColorMode = ColorMode.AUTO) -> bool:
    return supports_color(stderr_is_tty(), mode=mode, env=os.environ)


def stdout_supports_color(mode: ColorMode = ColorMode.AUTO) -> bool:
    return supports_color(stdout_is_tty(), mode=mode, env=os.environ)


def terminal_width(default: int = 80) -> int:
    return shutil.get_terminal_size((default, 24)).columns


def tty_asker(stdin: TextIO) -> Callable[[str], bool] | None:
    """The one y/N confirm, TTY-only: piped stdin (data) or piped stderr (cron)
    can't ask — the caller's plan note stands and the belt governs. Shared by
    graph's ``CONFIRM_PARTIAL`` and the OCR belt-shortfall preflight (A8)."""
    if not (stdin.isatty() and stderr_is_tty()):
        return None

    def ask(question: str) -> bool:
        sys.stderr.write(f"{question} ")
        sys.stderr.flush()
        return stdin.readline().strip().lower() in ("y", "yes")

    return ask


def enable_windows_vt() -> bool:
    """Best-effort ANSI enablement on Windows consoles; True on other platforms."""
    if sys.platform == "win32":
        import ctypes

        enable_vt = 0x0004
        kernel32 = ctypes.windll.kernel32
        succeeded = True
        for std_handle in (-11, -12):  # stdout, stderr
            handle = kernel32.GetStdHandle(std_handle)
            mode = ctypes.c_ulong()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                succeeded = False
                continue
            if not kernel32.SetConsoleMode(handle, mode.value | enable_vt):
                succeeded = False
        return succeeded
    return True
