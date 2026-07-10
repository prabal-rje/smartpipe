"""Daily catalog cache + probe capability cache, in the state dir (D41's home).

Both files are conveniences, never sources of truth: every read degrades to a
miss and every write is best-effort — a broken state directory must never
break the picker or ``doctor --probe`` (the usage ledger's posture, kept).
The dated catalog filename IS the TTL: today's file is fresh, anything else
is a miss (and gets swept on the next write).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from smartpipe.config.picker import ProbeChip, RegistryCaps
from smartpipe.core.jsontools import as_items, as_record, as_str

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "catalog_path",
    "load_catalog",
    "load_probe_chips",
    "load_registry",
    "probe_path",
    "record_probe",
    "registry_path",
    "store_catalog",
    "store_registry",
]


def _state_root(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_STATE_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "smartpipe"


# --- the daily catalog cache ---------------------------------------------------------


def catalog_path(env: Mapping[str, str], provider: str, day: str) -> Path:
    return _state_root(env) / "catalogs" / f"{provider}-{day}.json"


def load_catalog(path: Path) -> tuple[str, ...] | None:
    """Today's cached names, or None (a miss — fetch live)."""
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    record = as_record(parsed)
    entries = as_items(record.get("names")) if record is not None else None
    if entries is None:
        return None
    names = [as_str(entry) for entry in entries]
    if any(name is None for name in names):
        return None
    return tuple(name for name in names if name is not None)


def store_catalog(path: Path, names: tuple[str, ...]) -> None:
    """Write today's catalog and sweep the provider's stale days. Best-effort.
    The glob pins the date shape so ``openai-*`` can never eat ``openai-embed-*``."""
    provider = path.name.rsplit("-", 3)[0]  # {provider}-YYYY-MM-DD.json
    try:
        _write_json(path, {"names": list(names)})
        for stale in path.parent.glob(f"{provider}-????-??-??.json"):
            if stale.name != path.name:
                with contextlib.suppress(OSError):
                    stale.unlink()
    except OSError:
        return


# --- the models.dev registry cache (day-stamped, like catalogs) ------------------------


def registry_path(env: Mapping[str, str], day: str) -> Path:
    return _state_root(env) / "registry" / f"models-dev-{day}.json"


def load_registry(path: Path) -> dict[str, RegistryCaps] | None:
    """Today's cached capability map, or None (a miss — fetch live)."""
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    record = as_record(parsed)
    models = as_record(record.get("models")) if record is not None else None
    if models is None:
        return None
    caps: dict[str, RegistryCaps] = {}
    for ref, value in models.items():
        entry = as_record(value)
        if entry is None:
            continue
        image, audio = entry.get("image"), entry.get("audio")
        if isinstance(image, bool) and isinstance(audio, bool):
            caps[ref] = RegistryCaps(image=image, audio=audio)
    return caps


def store_registry(path: Path, caps: Mapping[str, RegistryCaps]) -> None:
    """Write today's registry snapshot and sweep stale days. Best-effort."""
    document: dict[str, object] = {
        "models": {ref: {"image": c.image, "audio": c.audio} for ref, c in caps.items()}
    }
    try:
        _write_json(path, document)
        for stale in path.parent.glob("models-dev-????-??-??.json"):
            if stale.name != path.name:
                with contextlib.suppress(OSError):
                    stale.unlink()
    except OSError:
        return


# --- the probe capability cache (chips) ------------------------------------------------


def probe_path(env: Mapping[str, str]) -> Path:
    return _state_root(env) / "probe.json"


def load_probe_chips(path: Path) -> dict[str, ProbeChip]:
    """Model ref → what a probe observed; empty on any trouble (no cache = no chips)."""
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    record = as_record(parsed)
    models = as_record(record.get("models")) if record is not None else None
    chips: dict[str, ProbeChip] = {}
    for ref, value in (models or {}).items():
        entry = as_record(value)
        if entry is None:
            continue
        sees, hears, ts = entry.get("sees"), entry.get("hears"), entry.get("ts")
        if isinstance(sees, bool) and isinstance(hears, bool) and isinstance(ts, (int, float)):
            chips[ref] = ProbeChip(sees=sees, hears=hears, ts=float(ts))
    return chips


def record_probe(path: Path, ref: str, *, sees: bool, hears: bool, now: float) -> None:
    """Merge one model's probe verdict into the cache. Best-effort: never raises."""
    existing = load_probe_chips(path)
    existing[ref] = ProbeChip(sees=sees, hears=hears, ts=now)
    document: dict[str, object] = {
        "version": 1,
        "models": {
            name: {"sees": chip.sees, "hears": chip.hears, "ts": chip.ts}
            for name, chip in existing.items()
        },
    }
    with contextlib.suppress(OSError):
        _write_json(path, document)


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        os.replace(tmp, path)  # atomic — a concurrent reader never sees a torn file
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
