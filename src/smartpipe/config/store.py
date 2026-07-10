"""Read and write ``config.toml``.

Rules (plan/decisions.md D09 + DEFER-1): the file is optional; unknown keys are
*ignored* on read (forward compatibility) but *preserved* on write — an older
smartpipe never strips a newer one's settings; wrong-typed values fail loudly
with the key named; API keys are never stored here. Writes are atomic
(same-directory temp file + ``os.replace``), so a concurrent reader can never
see a torn file. Comments do not survive a rewrite: tomli-w cannot round-trip
them and tomlkit stays outside the dependency budget — docs/reference/cli.md
says so out loud. The ONE exception is the provenance header ("the receipt",
item 30): every stamped save writes ``# stamped by: smartpipe use (…)`` as the
first line, and unstamped rewrites carry the existing header forward — the
file documents how it got that way.

Profiles are retired (item 30): the ``profile`` key and ``[profiles.*]``
tables are ignored on read (one warn through the injected channel, never a
crash) and stripped on the next save — the rewrite is the cure.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import tomli_w

from smartpipe.config.paths import human_path
from smartpipe.core.errors import SetupFault
from smartpipe.core.jsontools import as_items, as_str

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

__all__ = [
    "Config",
    "load_config",
    "save_config",
]


@dataclass(frozen=True, slots=True)
class Config:
    model: str | None = None
    fallback_model: str | None = None  # chat failover when the breaker trips (item 11)
    embed_model: str | None = None
    concurrency: int | None = None
    output: str | None = None
    allow_captions: bool | None = None  # cloud conversions consent (D35; flag wins)
    stt_model: str | None = None  # remote transcription role (D39/05); unset = the ladder
    ocr_model: str | None = None  # document parsing role (item 40); unset = local extraction
    media_embed_model: str | None = None  # joint-space media embedder role (item 40)
    cache: bool | None = None  # result caching (D38/15) — account-level posture
    cache_days: int | None = None  # sweep TTL (D39/02); default 30
    cache_max_mb: int | None = None  # LRU size cap (D39/02); default 500
    update_check: bool | None = None  # daily PyPI release check + notice; default on
    media_previews: bool | None = None  # TTY media previews kill switch; unset = on
    # declared capability chips for the configured model — self-hosted endpoints the
    # registry can't know about (e.g. ["image"]); display only, runtime stays attempt-based
    model_capabilities: tuple[str, ...] | None = None


# Retired by item 30 (the `use` collapse): ignored on read, stripped on write.
_RETIRED_KEYS = ("profile", "profiles")
_PROFILES_REMOVED = "profiles were removed - run smartpipe use"
_HEADER_PREFIX = "# stamped by: "


def load_config(path: Path, *, warn: Callable[[str], None] | None = None) -> Config:
    """The file's flat keys, typed. Retired profile keys are ignored — one
    warn through the injected channel (None = silent, e.g. shell completion,
    which must never print)."""
    data = _read_raw(path)
    if warn is not None and any(key in data for key in _RETIRED_KEYS):
        warn(_PROFILES_REMOVED)
    return Config(
        model=_string(data, "model", path),
        fallback_model=_string(data, "fallback-model", path),
        embed_model=_string(data, "embed-model", path),
        concurrency=_positive_int(data, "concurrency", path),
        output=_string(data, "output", path),
        allow_captions=_boolean(data, "allow-captions", path),
        stt_model=_string(data, "stt-model", path),
        ocr_model=_string(data, "ocr-model", path),
        media_embed_model=_string(data, "media-embed-model", path),
        cache=_boolean(data, "cache", path),
        cache_days=_positive_int(data, "cache-days", path),
        cache_max_mb=_positive_int(data, "cache-max-mb", path),
        update_check=_boolean(data, "update-check", path),
        media_previews=_boolean(data, "media-previews", path),
        model_capabilities=_capability_list(data, "model-capabilities", path),
    )


def save_config(path: Path, config: Config, *, stamped_by: str | None = None) -> None:
    """Write the file atomically. ``stamped_by`` names the door that wrote it
    ("smartpipe use", "smartpipe config", …) — the receipt header; None keeps
    whatever header the file already carries."""
    merged = dict(_read_raw(path))  # a corrupt file fails loudly before we overwrite evidence
    for retired in _RETIRED_KEYS:
        merged.pop(retired, None)  # profiles are gone; the rewrite is the cure
    ours: dict[str, str | int | list[str] | None] = {
        "model": config.model,
        "fallback-model": config.fallback_model,
        "embed-model": config.embed_model,
        "concurrency": config.concurrency,
        "output": config.output,
        "allow-captions": config.allow_captions,
        "stt-model": config.stt_model,
        "ocr-model": config.ocr_model,
        "media-embed-model": config.media_embed_model,
        "cache": config.cache,
        "cache-days": config.cache_days,
        "cache-max-mb": config.cache_max_mb,
        "update-check": config.update_check,
        "media-previews": config.media_previews,
        "model-capabilities": (
            list(config.model_capabilities) if config.model_capabilities is not None else None
        ),
    }
    for key, value in ours.items():
        if value is None:
            merged.pop(key, None)  # None = unset (pinned semantics)
        else:
            merged[key] = value
    _write_raw(path, merged, header=_header_line(path, stamped_by))


def _header_line(path: Path, stamped_by: str | None) -> str | None:
    """The receipt: a fresh stamp for this door, or the file's existing header."""
    if stamped_by is not None:
        from datetime import UTC, datetime

        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
        return f"{_HEADER_PREFIX}{stamped_by} ({stamp})"
    if not path.exists():
        return None
    with contextlib.suppress(OSError):
        first = path.read_text(encoding="utf-8").split("\n", 1)[0]
        if first.startswith(_HEADER_PREFIX):
            return first
    return None


def _write_raw(path: Path, merged: dict[str, object], *, header: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if header is not None:
                handle.write(header + "\n")
            handle.write(tomli_w.dumps(merged))
        os.replace(tmp, path)  # atomic on POSIX & Windows (same volume by construction)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _read_raw(path: Path) -> Mapping[str, object]:
    """The file as parsed TOML — ``{}`` if missing, the broken-file screen if corrupt."""
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SetupFault(_broken_screen(path, exc)) from exc


def _string(data: Mapping[str, object], key: str, path: Path) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SetupFault(_wrong_type_screen(path, key, "a string", value))
    return value


def _boolean(data: Mapping[str, object], key: str, path: Path) -> bool | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise SetupFault(_wrong_type_screen(path, key, "true or false", value))
    return value


_CAPABILITY_WORDS = ("text", "image", "audio")


def _capability_list(data: Mapping[str, object], key: str, path: Path) -> tuple[str, ...] | None:
    value = data.get(key)
    if value is None:
        return None
    items = as_items(value)
    words = [as_str(item) for item in items] if items is not None else None
    if words is None or any(word is None for word in words):
        raise SetupFault(_wrong_type_screen(path, key, 'a list like ["image"]', value))
    cleaned = tuple(word for word in words if word is not None)
    unknown = [word for word in cleaned if word not in _CAPABILITY_WORDS]
    if unknown:
        raise SetupFault(
            f"error: config value '{key}' has unknown capability {unknown[0]!r}\n"
            f"  Known capabilities: {', '.join(_CAPABILITY_WORDS)}\n"
            f"  Fix the line in {human_path(path)}"
        )
    return cleaned


def _positive_int(data: Mapping[str, object], key: str, path: Path) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SetupFault(_wrong_type_screen(path, key, "a whole number ≥ 1", value))
    return value


def _broken_screen(path: Path, exc: tomllib.TOMLDecodeError) -> str:
    detail = str(exc)
    located = re.search(r"at line (\d+)", detail)
    location = f", line {located.group(1)}" if located else ""
    detail = re.sub(r"\s*\(at line [^)]*\)$", "", detail)
    return (
        "error: config file has a syntax error\n"
        f"  {human_path(path)}{location}: {detail}\n"
        "  Fix the line, or start fresh: smartpipe config"
    )


def _wrong_type_screen(path: Path, key: str, expected: str, value: object) -> str:
    return (
        f"error: config value '{key}' should be {expected}\n"
        f"  {human_path(path)} has: {key} = {value!r}\n"
        f"  Fix the line, or reset it: smartpipe config"
    )
