"""Temporal values (ledger item 56): two jobs, one small pure module.

1. **Coercion** — a model asked for a ``{due date}`` answers in whatever shape
   it likes; ``coerce_date``/``coerce_datetime`` read a deliberately SMALL
   table of shapes (ISO first, then a handful of explicit human formats) and
   canonicalize to ISO-8601: ``YYYY-MM-DD`` for dates, full ISO for datetimes.
   An explicit UTC offset is preserved; a naive value stays naive — smartpipe
   never invents a timezone. ``XX/YY/ZZZZ`` with both fields ≤ 12 is read
   month-first and FLAGGED so the caller can disclose the guess.

2. **Comparison** — ``temporal_key`` is the try-parse where/sort share: an
   ISO-extended date or datetime string becomes an epoch key (dates promote to
   midnight; naive values read as UTC, exactly like ``timebin``), anything
   else is ``None`` so the existing string/number rules keep the wheel. The
   messy human table above is coercion-only — comparison stays strict ISO.

Hand-rolled on purpose: no dateutil, no locale-dependent ``strptime`` names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

__all__ = ["CoercedTemporal", "coerce_date", "coerce_datetime", "temporal_key"]


@dataclass(frozen=True, slots=True)
class CoercedTemporal:
    """A canonical ISO rendering; ``ambiguous`` marks a month-first guess."""

    canonical: str
    ambiguous: bool = False


_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may", "may"),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}

_MONTH_FIRST = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\Z")  # Jan 15, 2026
_DAY_FIRST = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\Z")  # 15 Jan 2026
_SLASH_YMD = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})\Z")  # 2026/01/15
_SLASH_XY = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\Z")  # 01/15/2026 · 15/01/2026
_TIMED = re.compile(  # DATE-PART ⋅ T or space ⋅ time ⋅ optional offset
    r"(.+?)[T ](\d{1,2}):(\d{2})((?::\d{2}(?:\.\d{1,6})?)?)(Z|[+-]\d{2}:\d{2})?\Z"
)
_ISO_HEAD = re.compile(r"\d{4}-\d{2}-\d{2}([T ]|\Z)")  # the comparison fence


def _checked(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:  # 13th month, 32nd day, Feb 29 off-cycle, …
        return None


def _from_table(text: str) -> tuple[date, bool] | None:
    """The explicit human formats; the bool marks a month-first guess."""
    if (named := _MONTH_FIRST.fullmatch(text)) is not None:
        month = _MONTHS.get(named.group(1).lower())
        if month is None:
            return None
        parsed = _checked(int(named.group(3)), month, int(named.group(2)))
        return None if parsed is None else (parsed, False)
    if (named := _DAY_FIRST.fullmatch(text)) is not None:
        month = _MONTHS.get(named.group(2).lower())
        if month is None:
            return None
        parsed = _checked(int(named.group(3)), month, int(named.group(1)))
        return None if parsed is None else (parsed, False)
    if (slashed := _SLASH_YMD.fullmatch(text)) is not None:
        parsed = _checked(int(slashed.group(1)), int(slashed.group(2)), int(slashed.group(3)))
        return None if parsed is None else (parsed, False)
    if (slashed := _SLASH_XY.fullmatch(text)) is not None:
        first, second, year = (int(part) for part in slashed.groups())
        if first > 12:  # first field can only be a day → day-first
            parsed = _checked(year, second, first)
            return None if parsed is None else (parsed, False)
        parsed = _checked(year, first, second)  # month-first: sure when second > 12
        return None if parsed is None else (parsed, second <= 12)
    return None


def _parse_date(text: str) -> tuple[date, bool] | None:
    """One date from ISO variants, then the table; None means unreadable."""
    try:
        return date.fromisoformat(text), False
    except ValueError:
        pass
    try:  # a full datetime offered where a date was asked: keep its day
        return datetime.fromisoformat(text).date(), False
    except ValueError:
        pass
    return _from_table(text)


def coerce_date(text: str) -> CoercedTemporal | None:
    """Canonical ``YYYY-MM-DD``, or None when no table row reads the text."""
    parsed = _parse_date(text.strip())
    if parsed is None:
        return None
    day, ambiguous = parsed
    return CoercedTemporal(day.isoformat(), ambiguous)


def coerce_datetime(text: str) -> CoercedTemporal | None:
    """Canonical full ISO (offset preserved, naive stays naive), or None."""
    text = text.strip()
    try:
        return CoercedTemporal(datetime.fromisoformat(text).isoformat())
    except ValueError:
        pass
    if (timed := _TIMED.fullmatch(text)) is not None:
        day_text, hour, minute, seconds, offset = timed.groups()
        day = _parse_date(day_text.strip())
        if day is None:
            return None
        assembled = f"{day[0].isoformat()}T{hour.zfill(2)}:{minute}{seconds or ':00'}{offset or ''}"
        try:
            return CoercedTemporal(datetime.fromisoformat(assembled).isoformat(), day[1])
        except ValueError:
            return None
    day = _from_table(text)  # a bare table date reads as its (naive) midnight
    if day is None:
        return None
    return CoercedTemporal(datetime.combine(day[0], datetime.min.time()).isoformat(), day[1])


def temporal_key(value: object) -> float | None:
    """Epoch seconds when the value is an ISO-extended date/datetime string.

    Strictly fenced: the text must open with ``YYYY-MM-DD`` (compact forms
    like ``20260115`` could be identifiers). Dates promote to midnight; naive
    values read as UTC so mixed columns hold one total order (timebin's rule).
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if _ISO_HEAD.match(text) is None:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()
