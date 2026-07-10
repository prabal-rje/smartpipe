"""Rendering ``config show`` — pure, so the exact layout is golden-testable.

The point of the origin column (plan/decisions.md D09): precedence is never a
mystery. Each effective value is shown with where it came from — env var,
config file, or built-in default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.io.richui import Cell, UiStyle, render_grid

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from smartpipe.config.store import Config

__all__ = ["Setting", "render_show", "settings_with_origin"]

_DEFAULTS = {
    "model": "(auto-detect)",
    "fallback-model": "(none)",
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
    profile = _active_profile(env, config)
    return (
        *((profile,) if profile is not None else ()),
        _resolve("model", env.get("SMARTPIPE_MODEL"), config.model),
        _resolve("fallback-model", env.get("SMARTPIPE_FALLBACK_MODEL"), config.fallback_model),
        _resolve("embed-model", env.get("SMARTPIPE_EMBED_MODEL"), config.embed_model),
        _resolve("concurrency", env.get("SMARTPIPE_CONCURRENCY"), config.concurrency),
        _resolve("output", env.get("SMARTPIPE_OUTPUT"), config.output),
    )


def render_show(
    settings: Sequence[Setting],
    config_file: str,
    *,
    color: bool,
) -> str:
    """Render the effective settings as a Rich grid with a stable plain form."""
    key_width = max(len(setting.key) for setting in settings) if settings else len("config file")
    key_width = max(key_width, len("config file"))
    setting_rows = tuple(
        (
            Cell(setting.key, UiStyle.DIM),
            Cell(setting.value),
            Cell(f"({setting.origin})", UiStyle.DIM),
        )
        for setting in settings
    )
    rendered_settings = render_grid(
        setting_rows,
        color=color,
        column_widths=(key_width, None, None),
    )
    rendered_path = render_grid(
        ((Cell("config file", UiStyle.DIM), Cell(config_file)),),
        color=color,
        column_widths=(key_width, None),
    )
    return "\n".join(part for part in (rendered_settings, rendered_path) if part)


def _active_profile(env: Mapping[str, str], config: Config) -> Setting | None:
    env_value = env.get("SMARTPIPE_PROFILE", "").strip()
    if env_value:
        return Setting("profile", env_value, "env")
    if config.profile is not None:
        return Setting("profile", config.profile, "config file")
    return None


def _resolve(key: str, env_value: str | None, config_value: object) -> Setting:
    if env_value is not None and env_value.strip():
        return Setting(key, env_value.strip(), "env")
    if config_value is not None:
        return Setting(key, str(config_value), "config file")
    return Setting(key, _DEFAULTS[key], "default")
