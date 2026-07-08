"""Install-channel detection for ``smartpipe update`` — pure, no I/O.

Path strings in, channel out: the interpreter and package paths carry the
installer's fingerprint (Homebrew's Cellar, uv's tools dir, pipx's venvs, a
bare site-packages for pip). Manager markers are checked before the pip
fallback because every managed install *also* contains site-packages; nothing
recognizable is honestly ``UNKNOWN`` — guessing an upgrade command would
corrupt someone's environment.
"""

from __future__ import annotations

from enum import StrEnum
from typing import assert_never

__all__ = ["Channel", "detect_channel", "upgrade_command"]


class Channel(StrEnum):
    HOMEBREW = "homebrew"
    UV_TOOL = "uv tool"
    PIPX = "pipx"
    PIP = "pip"
    UNKNOWN = "unknown"


_MANAGER_MARKERS: tuple[tuple[str, Channel], ...] = (
    ("/cellar/", Channel.HOMEBREW),
    ("linuxbrew", Channel.HOMEBREW),
    ("/uv/tools/", Channel.UV_TOOL),
    ("/pipx/venvs/", Channel.PIPX),
)
_PIP_MARKERS = ("site-packages", "dist-packages")


def detect_channel(executable: str, module_path: str) -> Channel:
    haystack = " ".join(p.replace("\\", "/").lower() for p in (executable, module_path))
    manager = next((channel for marker, channel in _MANAGER_MARKERS if marker in haystack), None)
    if manager is not None:
        return manager
    if any(marker in haystack for marker in _PIP_MARKERS):
        return Channel.PIP
    return Channel.UNKNOWN


def upgrade_command(channel: Channel) -> tuple[str, ...] | None:
    """The channel's own upgrade invocation — ``None`` says "don't guess"."""
    match channel:
        case Channel.HOMEBREW:
            return ("brew", "upgrade", "smartpipe")
        case Channel.UV_TOOL:
            return ("uv", "tool", "upgrade", "smartpipe-cli")
        case Channel.PIPX:
            return ("pipx", "upgrade", "smartpipe-cli")
        case Channel.PIP:
            return ("pip", "install", "-U", "smartpipe-cli")
        case Channel.UNKNOWN:
            return None
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)
