"""Where the config file lives (plan/decisions.md D09).

``~/.config/sempipe/config.toml`` on every Unix including macOS — the spec
names this path and terminal users expect it — and ``%APPDATA%\\sempipe`` on
Windows. Environment and platform are parameters so the logic is pure.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["config_path", "human_path"]


def config_path(env: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    resolved_env = os.environ if env is None else env
    resolved_platform = sys.platform if platform is None else platform
    if resolved_platform == "win32":
        appdata = resolved_env.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    else:
        xdg = resolved_env.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "sempipe" / "config.toml"


def human_path(path: Path) -> str:
    """``~/.config/sempipe/config.toml`` beats an absolute path in messages."""
    try:
        return f"~/{path.relative_to(Path.home()).as_posix()}"
    except ValueError:
        return str(path)
