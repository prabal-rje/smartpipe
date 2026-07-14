"""Terminal and output-endpoint detection plus the color decision.

``supports_color`` is pure — the environment is a parameter — so the whole truth
table is testable. ``classify_output_endpoint`` is the corresponding pure core
for progress safety. The thin wrappers read real process state at call time.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from contextlib import suppress
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, assert_never

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import TextIO

__all__ = [
    "ColorMode",
    "OutputEndpoint",
    "classify_output_endpoint",
    "enable_windows_vt",
    "output_allows_progress",
    "output_endpoint",
    "stderr_is_tty",
    "stderr_supports_color",
    "stdout_allows_progress",
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


class OutputEndpoint(Enum):
    TERMINAL = "terminal"
    REGULAR_FILE = "regular_file"
    NULL_DEVICE = "null_device"
    FIFO = "fifo"
    SOCKET = "socket"
    UNKNOWN = "unknown"


def classify_output_endpoint(
    is_tty: bool,
    *,
    mode: int | None,
    rdev: int | None,
    null_rdev: int | None,
) -> OutputEndpoint:
    """Classify already-observed descriptor facts without performing I/O."""
    if is_tty:
        return OutputEndpoint.TERMINAL
    if mode is None:
        return OutputEndpoint.UNKNOWN
    match stat.S_IFMT(mode):
        case stat.S_IFREG:
            return OutputEndpoint.REGULAR_FILE
        case stat.S_IFIFO:
            return OutputEndpoint.FIFO
        case stat.S_IFSOCK:
            return OutputEndpoint.SOCKET
        case stat.S_IFCHR:
            if rdev is not None and null_rdev is not None and rdev == null_rdev:
                return OutputEndpoint.NULL_DEVICE
            return OutputEndpoint.UNKNOWN
        case _:
            return OutputEndpoint.UNKNOWN


_DESCRIPTOR_ERRORS = (OSError, OverflowError, TypeError, ValueError)


def output_endpoint(stream: TextIO) -> OutputEndpoint:
    """Classify the exact descriptor currently backing ``stream``.

    A positive ``isatty`` is conclusive. If that probe is unavailable, descriptor
    inspection still gets a chance. Expected descriptor-boundary failures fail
    closed; unrelated exceptions remain visible as programming errors.
    """
    try:
        is_tty = stream.isatty()
    except _DESCRIPTOR_ERRORS:
        is_tty = False
    if is_tty:
        return OutputEndpoint.TERMINAL

    try:
        descriptor_stat = os.fstat(stream.fileno())
    except _DESCRIPTOR_ERRORS:
        return OutputEndpoint.UNKNOWN

    null_rdev: int | None = None
    if stat.S_ISCHR(descriptor_stat.st_mode):
        with suppress(*_DESCRIPTOR_ERRORS):
            null_rdev = os.stat(os.devnull).st_rdev
    return classify_output_endpoint(
        False,
        mode=descriptor_stat.st_mode,
        rdev=descriptor_stat.st_rdev,
        null_rdev=null_rdev,
    )


def output_allows_progress(endpoint: OutputEndpoint) -> bool:
    """Whether carriage-return animation is safe for this stdout endpoint."""
    match endpoint:
        case OutputEndpoint.TERMINAL | OutputEndpoint.REGULAR_FILE | OutputEndpoint.NULL_DEVICE:
            return True
        case OutputEndpoint.FIFO | OutputEndpoint.SOCKET | OutputEndpoint.UNKNOWN:
            return False
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)


def stdout_allows_progress() -> bool:
    """Classify the current stdout, including intentional in-process rebinding."""
    return output_allows_progress(output_endpoint(sys.stdout))


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
