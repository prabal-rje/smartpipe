"""Read and write ``config.toml``.

Rules (plan/decisions.md D09 + DEFER-1): the file is optional; unknown keys are
*ignored* on read (forward compatibility) but *preserved* on write — an older
sempipe never strips a newer one's settings; wrong-typed values fail loudly
with the key named; API keys are never stored here. Writes are atomic
(same-directory temp file + ``os.replace``), so a concurrent reader can never
see a torn file. Comments do not survive a rewrite: tomli-w cannot round-trip
them and tomlkit stays outside the dependency budget — docs/reference/cli.md
says so out loud.
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

from sempipe.config.paths import human_path
from sempipe.core.errors import SetupFault
from sempipe.core.jsontools import as_record

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = [
    "BUILTIN_PROFILES",
    "Config",
    "load_config",
    "profile_names",
    "save_config",
    "set_active_profile",
]


@dataclass(frozen=True, slots=True)
class Config:
    model: str | None = None
    embed_model: str | None = None
    concurrency: int | None = None
    output: str | None = None
    profile: str | None = None  # the active profile's name (D30)
    allow_captions: bool | None = None  # cloud conversions consent (D35; flag wins)
    cache: bool | None = None  # result caching (D38/15) — account-level posture
    cache_days: int | None = None  # sweep TTL (D39/02); default 30
    cache_max_mb: int | None = None  # LRU size cap (D39/02); default 500


_EMPTY_PROFILE: Mapping[str, object] = {}

# D30: shipped presets, generated here so they can't rot in user files. A
# profile is ONLY a bundle of existing config keys — that's the fence.
BUILTIN_PROFILES: Mapping[str, Mapping[str, object]] = {
    # picking a cloud preset IS the consent for paid image/audio-to-text
    # conversions (D35) — the wizard says so; per-row disclosures continue
    # gpt-5.4-mini works on BOTH the key wire and the ChatGPT-login (Codex)
    # wire — 4o-mini is rejected by Codex accounts (owner-hit). Audio input is
    # assumed UNSUPPORTED on OpenAI: the ladder falls to whisper automatically.
    "openai": {
        "model": "gpt-5.4-mini",
        "embed-model": "text-embedding-3-small",
        "allow-captions": True,
    },
    "gemini": {
        "model": "gemini-2.5-flash",
        "embed-model": "gemini/gemini-embedding-001",
        "allow-captions": True,
    },
    "local": {
        "model": "ollama/gemma-4-e2b",  # multimodal 2.3B-effective, 128k
        "embed-model": "ollama/embeddinggemma",  # the pivot anchor: 308M, multilingual
    },
}


def load_config(path: Path, environ: Mapping[str, str] | None = None) -> Config:
    """Flat keys win over the active profile (a direct set is the most recent
    intent); the active profile is SEMPIPE_PROFILE > the file's `profile` key."""
    data = _read_raw(path)
    active = _active_profile(data, environ or {}, path)
    base: Mapping[str, object] = _profile_values(data, active, path) if active is not None else {}
    merged = {**base, **{k: v for k, v in data.items() if k != "profiles"}}
    return Config(
        model=_string(merged, "model", path),
        embed_model=_string(merged, "embed-model", path),
        concurrency=_positive_int(merged, "concurrency", path),
        output=_string(merged, "output", path),
        profile=active,
        allow_captions=_boolean(merged, "allow-captions", path),
        cache=_boolean(merged, "cache", path),
        cache_days=_positive_int(merged, "cache-days", path),
        cache_max_mb=_positive_int(merged, "cache-max-mb", path),
    )


def set_active_profile(path: Path, name: str | None) -> None:
    """Flip ONLY the profile key — never the flat keys, so resolved profile
    values are not materialized into the file (they'd shadow every later
    profile switch)."""
    merged = dict(_read_raw(path))
    if name is None:
        merged.pop("profile", None)
    else:
        merged["profile"] = name
    _write_raw(path, merged)


def profile_names(path: Path) -> tuple[str, ...]:
    """Every selectable profile: user-defined tables plus the shipped presets."""
    defined = as_record(_read_raw(path).get("profiles"))
    names = set(BUILTIN_PROFILES)
    if defined is not None:
        names.update(defined)
    return tuple(sorted(names))


def _active_profile(
    data: Mapping[str, object], environ: Mapping[str, str], path: Path
) -> str | None:
    from_env = environ.get("SEMPIPE_PROFILE", "").strip()
    name = from_env or _string(data, "profile", path)
    if not name:
        return None
    defined = as_record(data.get("profiles"))
    known: set[str] = set(BUILTIN_PROFILES)
    if defined is not None:
        known |= set(defined)
    if name not in known:
        raise SetupFault(
            f"error: profile {name!r} doesn't exist\n"
            f"  Known profiles: {', '.join(sorted(known))}\n"
            "  Pick one: sempipe config profile NAME — or define [profiles."
            f"{name}] in {human_path(path)}"
        )
    return name


def _profile_values(data: Mapping[str, object], name: str, path: Path) -> Mapping[str, object]:
    del path  # errors here don't need the file location — the key names suffice
    defined = as_record(data.get("profiles"))
    table = as_record(defined.get(name)) if defined is not None else None
    if table is not None:
        allowed = {"model", "embed-model", "concurrency", "output", "allow-captions"}
        unknown = set(table) - allowed
        if unknown:
            raise SetupFault(
                f"error: profile {name!r} has unknown key {sorted(unknown)[0]!r}\n"
                f"  A profile bundles: {', '.join(sorted(allowed))}"
            )
        return table
    return BUILTIN_PROFILES.get(name, _EMPTY_PROFILE)


def save_config(path: Path, config: Config) -> None:
    merged = dict(_read_raw(path))  # a corrupt file fails loudly before we overwrite evidence
    ours: dict[str, str | int | None] = {
        "model": config.model,
        "embed-model": config.embed_model,
        "concurrency": config.concurrency,
        "output": config.output,
        "profile": config.profile,
        "allow-captions": config.allow_captions,
        "cache": config.cache,
        "cache-days": config.cache_days,
        "cache-max-mb": config.cache_max_mb,
    }
    for key, value in ours.items():
        if value is None:
            merged.pop(key, None)  # None = unset (pinned semantics)
        else:
            merged[key] = value
    _write_raw(path, merged)


def _write_raw(path: Path, merged: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
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
        "  Fix the line, or start fresh: sempipe config"
    )


def _wrong_type_screen(path: Path, key: str, expected: str, value: object) -> str:
    return (
        f"error: config value '{key}' should be {expected}\n"
        f"  {human_path(path)} has: {key} = {value!r}\n"
        f"  Fix the line, or reset it: sempipe config"
    )
