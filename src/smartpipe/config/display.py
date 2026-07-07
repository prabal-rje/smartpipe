"""Rendering ``config show`` — pure, so the exact layout is golden-testable.

The point of the origin column (plan/decisions.md D09): precedence is never a
mystery. Each effective value is shown with where it came from — env var,
config file, or built-in default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.io.text import display_width

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from smartpipe.config.store import Config

__all__ = ["Setting", "render_show", "settings_with_origin"]

_DEFAULTS = {
    "model": "(auto-detect)",
    "embed-model": "nomic-embed-text",
    "concurrency": "4",
    "output": "auto",
}


@dataclass(frozen=True, slots=True)
class Setting:
    key: str
    value: str
    origin: str  # "env" | "config file" | "default"


def settings_with_origin(env: Mapping[str, str], config: Config) -> tuple[Setting, ...]:
    return (
        _resolve("model", env.get("SMARTPIPE_MODEL"), config.model),
        _resolve("embed-model", env.get("SMARTPIPE_EMBED_MODEL"), config.embed_model),
        _resolve("concurrency", env.get("SMARTPIPE_CONCURRENCY"), config.concurrency),
        _resolve("output", env.get("SMARTPIPE_OUTPUT"), config.output),
    )


def render_show(settings: Sequence[Setting], config_file: str) -> str:
    key_width = max(display_width(s.key) for s in (*settings, Setting("config file", "", ""))) + 2
    value_width = max(display_width(s.value) for s in settings) + 2
    from smartpipe.cli.screens import tint

    lines = [
        f"{tint(_pad(s.key, key_width), '2')}{_pad(s.value, value_width)}"
        f"{tint(f'({s.origin})', '2')}"
        for s in settings
    ]
    lines.append(f"{tint(_pad('config file', key_width), '2')}{config_file}")
    return "\n".join(lines)


def _pad(text: str, width: int) -> str:
    """Pad to ``width`` terminal cells (DEFER-2) — f-string ``<`` pads code points."""
    return text + " " * max(0, width - display_width(text))


def _resolve(key: str, env_value: str | None, config_value: object) -> Setting:
    if env_value is not None and env_value.strip():
        return Setting(key, env_value.strip(), "env")
    if config_value is not None:
        return Setting(key, str(config_value), "config file")
    return Setting(key, _DEFAULTS[key], "default")
