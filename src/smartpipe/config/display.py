"""Rendering ``smartpipe using`` (alias: ``config show``) — pure, so the exact
layout is golden-testable.

The point of the origin column (plan/decisions.md D09): precedence is never a
mystery. Each effective value is shown with where it came from — env var,
config file, or built-in default. Every config key the runtime reads appears
here (item 48: the role keys and postures included) — a setting that can
change behavior but hides from ``using`` would make precedence a mystery
again. Unset roles show the honest default wording, not silence.
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
    "stt-model": "(auto: whisper-1 with an OpenAI key, else local whisper)",
    "ocr-model": "(built-in local extraction)",
    "media-embed-model": "(none - media rides embed-model)",
    "concurrency": "4",
    "output": "auto",
    "cache": "on",
    "batching": "on",
    "update-check": "on",
    "media-previews": "on",
}

_CACHE_ON = ("1", "true", "on", "yes")
_CACHE_OFF = ("0", "false", "off", "no")


@dataclass(frozen=True, slots=True)
class Setting:
    key: str
    value: str
    origin: str  # "env" | "config file" | "default"


def settings_with_origin(env: Mapping[str, str], config: Config) -> tuple[Setting, ...]:
    return (
        _resolve("model", env.get("SMARTPIPE_MODEL"), config.model),
        _resolve("fallback-model", env.get("SMARTPIPE_FALLBACK_MODEL"), config.fallback_model),
        _resolve("embed-model", env.get("SMARTPIPE_EMBED_MODEL"), config.embed_model),
        _resolve("stt-model", env.get("SMARTPIPE_STT_MODEL"), config.stt_model),
        _resolve("ocr-model", env.get("SMARTPIPE_OCR_MODEL"), config.ocr_model),
        _resolve(
            "media-embed-model", env.get("SMARTPIPE_MEDIA_EMBED_MODEL"), config.media_embed_model
        ),
        _resolve("concurrency", env.get("SMARTPIPE_CONCURRENCY"), config.concurrency),
        _resolve("output", env.get("SMARTPIPE_OUTPUT"), config.output),
        _cache_setting(env, config),
        _batching_setting(env, config),
        _update_check_setting(env, config),
        _resolve("media-previews", None, config.media_previews),
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


def _resolve(key: str, env_value: str | None, config_value: object) -> Setting:
    if env_value is not None and env_value.strip():
        return Setting(key, env_value.strip(), "env")
    if config_value is not None:
        return Setting(key, _display(config_value), "config file")
    return Setting(key, _DEFAULTS[key], "default")


def _display(value: object) -> str:
    """Booleans read as the postures they toggle — 'on'/'off', never 'True'."""
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def _cache_setting(env: Mapping[str, str], config: Config) -> Setting:
    """Mirrors the container's ``_cache_enabled`` exactly: a valid env flag
    wins, junk falls through — the grid must never claim what the run ignores."""
    flag = env.get("SMARTPIPE_CACHE", "").strip().lower()
    if flag in _CACHE_ON:
        return Setting("cache", "on", "env")
    if flag in _CACHE_OFF:
        return Setting("cache", "off", "env")
    return _resolve("cache", None, config.cache)


def _batching_setting(env: Mapping[str, str], config: Config) -> Setting:
    """Mirrors the container's ``_batching_enabled`` exactly: a valid
    SMARTPIPE_BATCH flag wins, junk falls through, unset defaults ON (item 62)."""
    flag = env.get("SMARTPIPE_BATCH", "").strip().lower()
    if flag in _CACHE_ON:
        return Setting("batching", "on", "env")
    if flag in _CACHE_OFF:
        return Setting("batching", "off", "env")
    if config.batching is None:
        return Setting("batching", "on", "default")
    return _resolve("batching", None, config.batching)


def _update_check_setting(env: Mapping[str, str], config: Config) -> Setting:
    """SMARTPIPE_NO_UPDATE_CHECK is a kill switch (any non-empty value): shown
    as the env-sourced 'off' it effects."""
    if env.get("SMARTPIPE_NO_UPDATE_CHECK", "").strip():
        return Setting("update-check", "off", "env")
    return _resolve("update-check", None, config.update_check)
