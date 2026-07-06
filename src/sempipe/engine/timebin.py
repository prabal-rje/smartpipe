"""Time bucketing for chart/summarize (D38/13, KQL ``bin()``) — pure, fenced.

The fence IS the design: timestamps parse as ISO-8601 or epoch
seconds/milliseconds, nothing else — timestamp-format hell is the swamp KQL
never had to cross (its ingest normalizes time), and we refuse to cross it
one format at a time. Labels are UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sempipe.core.errors import UsageFault

__all__ = ["BUCKETS_MENU", "bucket_label", "parse_bucket", "parse_timestamp"]

_BUCKETS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3_600,
    "6h": 21_600,
    "1d": 86_400,
}

BUCKETS_MENU = (
    "error: unknown time bucket\n"
    "  Buckets: 1m · 5m · 15m · 1h · 6h · 1d\n"
    "  Example: sempipe chart --by-time ts:1h"
)

_EPOCH_MILLIS_FLOOR = 1e11  # numbers past this are milliseconds, not seconds


def parse_bucket(text: str) -> int:
    seconds = _BUCKETS.get(text.strip())
    if seconds is None:
        raise UsageFault(BUCKETS_MENU + f"\n  (got: {text!r})")
    return seconds


def parse_timestamp(value: object) -> float | None:
    """Epoch seconds from ISO-8601 or epoch numbers; None means unparseable
    (the caller tallies and discloses — silence must never lie)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if number >= _EPOCH_MILLIS_FLOOR else number
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)  # naive reads as UTC, documented
        return moment.timestamp()
    return None


def bucket_label(epoch: float, bucket_seconds: int) -> str:
    floored = int(epoch // bucket_seconds) * bucket_seconds
    moment = datetime.fromtimestamp(floored, tz=UTC)
    if bucket_seconds >= 86_400:
        return moment.strftime("%Y-%m-%d")
    return moment.strftime("%H:%M")
