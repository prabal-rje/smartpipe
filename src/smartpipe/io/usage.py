"""The usage ledger (D41): what the meter observed, remembered over time.

One event per model-touching run, persisted at container exit; windows are
computed at read time; ``lifetime`` accumulates separately so pruning old
events loses nothing. Telemetry must never fail a run — every filesystem
error here is swallowed.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from smartpipe.core.jsontools import as_items, as_record

if TYPE_CHECKING:
    from collections.abc import Mapping

    from smartpipe.io.metering import Snapshot

__all__ = ["Totals", "read_ledger", "record_run", "reset_ledger", "usage_path"]

_EVENT_HORIZON_DAYS = 32  # windows top out at 30 days; older events prune
_FIELDS = ("runs", "tokens_in", "tokens_out", "media_bytes", "audio_seconds", "conversions")


@dataclass(frozen=True, slots=True)
class Totals:
    runs: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    media_bytes: int = 0
    audio_seconds: float = 0.0
    conversions: int = 0

    def plus(self, other: Totals) -> Totals:
        return Totals(
            runs=self.runs + other.runs,
            tokens_in=self.tokens_in + other.tokens_in,
            tokens_out=self.tokens_out + other.tokens_out,
            media_bytes=self.media_bytes + other.media_bytes,
            audio_seconds=self.audio_seconds + other.audio_seconds,
            conversions=self.conversions + other.conversions,
        )


def usage_path(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_STATE_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "smartpipe" / "usage.json"


def record_run(snapshot: Snapshot, env: Mapping[str, str], *, now: float | None = None) -> None:
    """Persist one run's observed usage. Best-effort: never raises."""
    if snapshot.empty:
        return
    moment = time.time() if now is None else now
    try:
        path = usage_path(env)
        document = _load(path)
        event = {
            "ts": moment,
            "runs": 1,
            "tokens_in": snapshot.tokens_in,
            "tokens_out": snapshot.tokens_out,
            "media_bytes": sum(snapshot.media_bytes.values()),
            "audio_seconds": snapshot.audio_seconds,
            "conversions": snapshot.conversions,
        }
        horizon = moment - _EVENT_HORIZON_DAYS * 86_400
        kept = [entry for entry in _document_events(document) if _event_ts(entry) >= horizon]
        document["events"] = [*kept, event]
        document["lifetime"] = _totals_dict(
            _totals_from(_document_lifetime(document)).plus(_totals_from(event))
        )
        if document.get("first_seen") is None:
            document["first_seen"] = moment
        _store(path, document)
    except OSError:
        return


def read_ledger(
    env: Mapping[str, str], *, now: float | None = None
) -> tuple[dict[str, Totals], float | None, float | None]:
    """(windows, first_seen, last_reset) — windows keyed hour/day/week/month/lifetime."""
    moment = time.time() if now is None else now
    document = _load(usage_path(env))
    events = _document_events(document)
    windows: dict[str, Totals] = {}
    for name, seconds in (
        ("past hour", 3_600),
        ("past day", 86_400),
        ("past week", 7 * 86_400),
        ("past month", 30 * 86_400),
    ):
        recent = [entry for entry in events if _event_ts(entry) >= moment - seconds]
        totals = Totals()
        for entry in recent:
            totals = totals.plus(_totals_from(entry))
        windows[name] = totals
    windows["lifetime"] = _totals_from(_document_lifetime(document))
    first_seen = document.get("first_seen")
    last_reset = document.get("last_reset")
    return (
        windows,
        first_seen if isinstance(first_seen, (int, float)) else None,
        last_reset if isinstance(last_reset, (int, float)) else None,
    )


def reset_ledger(env: Mapping[str, str], *, now: float | None = None) -> Totals:
    """Zero everything, stamp the reset, return the PREVIOUS lifetime."""
    moment = time.time() if now is None else now
    path = usage_path(env)
    document = _load(path)
    previous = _totals_from(_document_lifetime(document))
    fresh = _empty_document()
    fresh["first_seen"] = document.get("first_seen") or moment
    fresh["last_reset"] = moment
    _store(path, fresh)
    return previous


def stamp(moment: float) -> str:
    return datetime.fromtimestamp(moment, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


# --- the file ----------------------------------------------------------------------


def _empty_document() -> dict[str, object]:
    return {
        "version": 1,
        "first_seen": None,
        "last_reset": None,
        "lifetime": _totals_dict(Totals()),
        "events": [],
    }


def _load(path: Path) -> dict[str, object]:
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_document()
    record = as_record(parsed)
    if record is None:
        return _empty_document()
    document = _empty_document()
    document["first_seen"] = record.get("first_seen")
    document["last_reset"] = record.get("last_reset")
    lifetime = as_record(record.get("lifetime"))
    if lifetime is not None:
        document["lifetime"] = dict(lifetime)
    events = as_items(record.get("events"))
    if events is not None:
        document["events"] = [dict(entry) for item in events if (entry := as_record(item))]
    return document


def _store(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(".tmp")
    scratch.write_text(json.dumps(document), encoding="utf-8")
    os.replace(scratch, path)


def _document_events(document: dict[str, object]) -> list[dict[str, object]]:
    held = as_items(document.get("events"))
    if held is None:
        return []
    return [dict(entry) for item in held if (entry := as_record(item)) is not None]


def _document_lifetime(document: dict[str, object]) -> Mapping[str, object]:
    held = as_record(document.get("lifetime"))
    return held if held is not None else {}


def _event_ts(entry: Mapping[str, object]) -> float:
    value = entry.get("ts")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _totals_from(entry: Mapping[str, object]) -> Totals:
    def _number(key: str) -> float:
        value = entry.get(key)
        return float(value) if isinstance(value, (int, float)) else 0.0

    return Totals(
        runs=int(_number("runs")),
        tokens_in=int(_number("tokens_in")),
        tokens_out=int(_number("tokens_out")),
        media_bytes=int(_number("media_bytes")),
        audio_seconds=_number("audio_seconds"),
        conversions=int(_number("conversions")),
    )


def _totals_dict(totals: Totals) -> dict[str, object]:
    return {
        "runs": totals.runs,
        "tokens_in": totals.tokens_in,
        "tokens_out": totals.tokens_out,
        "media_bytes": totals.media_bytes,
        "audio_seconds": totals.audio_seconds,
        "conversions": totals.conversions,
    }
