"""The temporal helpers (ledger item 56): messy model dates → canonical ISO,
plus the ISO try-parse key that where/sort share."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from smartpipe.engine.temporal import CoercedTemporal, coerce_date, coerce_datetime, temporal_key

# --- coerce_date: the acceptance table ------------------------------------------


@pytest.mark.parametrize(
    ("text", "canonical"),
    [
        ("2026-01-15", "2026-01-15"),  # ISO passes through
        (" 2026-01-15 ", "2026-01-15"),  # whitespace tolerated
        ("2026-01-15T14:30:00Z", "2026-01-15"),  # a datetime answers a date ask
        ("2026-01-15 14:30", "2026-01-15"),
        ("Jan 15, 2026", "2026-01-15"),  # month-name, US comma form
        ("January 15, 2026", "2026-01-15"),
        ("jan 15 2026", "2026-01-15"),  # case-insensitive, comma optional
        ("15 Jan 2026", "2026-01-15"),  # day-first month-name form
        ("15 January 2026", "2026-01-15"),
        ("2026/01/15", "2026-01-15"),  # slashed year-first
        ("01/15/2026", "2026-01-15"),  # 15 can only be a day — month-first
        ("15/01/2026", "2026-01-15"),  # 15 can only be a day — day-first
        ("7/4/2026", "2026-07-04"),  # ambiguous: month-first wins (flagged below)
    ],
)
def test_coerce_date_accepts_the_table(text: str, canonical: str) -> None:
    coerced = coerce_date(text)
    assert coerced is not None
    assert coerced.canonical == canonical


def test_coerce_date_flags_ambiguous_slashed_dates() -> None:
    coerced = coerce_date("01/02/2026")
    assert coerced == CoercedTemporal("2026-01-02", ambiguous=True)


def test_coerce_date_unambiguous_slashed_dates_are_not_flagged() -> None:
    for text in ("01/15/2026", "15/01/2026", "2026/01/15", "2026-01-15"):
        coerced = coerce_date(text)
        assert coerced is not None
        assert coerced.ambiguous is False


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not a date",
        "13/13/2026",  # no reading makes both fields valid
        "02/29/2027",  # not a leap year
        "32 Jan 2026",
        "Jam 15, 2026",  # not a month
        "15-01-2026",  # dashed non-ISO stays outside the fence (deliberate)
        "1750000000",  # epoch numbers are deliberately NOT dates
    ],
)
def test_coerce_date_rejects_what_it_cannot_read(text: str) -> None:
    assert coerce_date(text) is None


# --- coerce_datetime -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "canonical"),
    [
        ("2026-01-15T14:30:00", "2026-01-15T14:30:00"),  # naive stays naive
        ("2026-01-15 14:30", "2026-01-15T14:30:00"),
        ("2026-01-15T14:30:00Z", "2026-01-15T14:30:00+00:00"),  # explicit offset preserved
        ("2026-01-15T14:30:00+05:30", "2026-01-15T14:30:00+05:30"),
        ("2026-01-15", "2026-01-15T00:00:00"),  # a bare date reads as midnight, still naive
        ("Jan 15, 2026 14:30", "2026-01-15T14:30:00"),
        ("15/01/2026 14:30:05", "2026-01-15T14:30:05"),
        ("2026/01/15 9:05", "2026-01-15T09:05:00"),  # single-digit hour normalizes
        ("15 Jan 2026 23:59:59+00:00", "2026-01-15T23:59:59+00:00"),
    ],
)
def test_coerce_datetime_accepts_the_table(text: str, canonical: str) -> None:
    coerced = coerce_datetime(text)
    assert coerced is not None
    assert coerced.canonical == canonical


def test_coerce_datetime_flags_ambiguity_from_the_date_part() -> None:
    coerced = coerce_datetime("01/02/2026 09:00")
    assert coerced == CoercedTemporal("2026-01-02T09:00:00", ambiguous=True)


@pytest.mark.parametrize(
    "text",
    ["", "whenever", "2026-01-15T25:00:00", "14:30", "Jan 15, 2026 at noon"],
)
def test_coerce_datetime_rejects_what_it_cannot_read(text: str) -> None:
    assert coerce_datetime(text) is None


# --- temporal_key: the where/sort try-parse --------------------------------------


def _epoch(year: int, month: int, day: int) -> float:
    return datetime(year, month, day, tzinfo=UTC).timestamp()


def test_temporal_key_reads_iso_dates_as_utc_midnight() -> None:
    assert temporal_key("2026-01-15") == _epoch(2026, 1, 15)


def test_temporal_key_promotes_dates_to_midnight_so_datetimes_compare() -> None:
    assert temporal_key("2026-01-15") == temporal_key("2026-01-15T00:00:00")
    key_date = temporal_key("2026-01-15")
    key_later = temporal_key("2026-01-15T00:00:01")
    assert key_date is not None and key_later is not None
    assert key_date < key_later


def test_temporal_key_honors_explicit_offsets() -> None:
    assert temporal_key("2026-01-15T02:00:00+02:00") == temporal_key("2026-01-15T00:00:00Z")
    assert temporal_key("2026-01-15T00:00:00Z") == _epoch(2026, 1, 15)


@pytest.mark.parametrize(
    "value",
    [
        None,
        42,  # numbers keep their numeric rules — epochs are not ISO
        True,
        "20260115",  # basic format stays outside the fence (could be an ID)
        "2026-01-15x",
        "Jan 15, 2026",  # the messy table is coercion-only; where/sort read ISO
        "not a date",
        "",
    ],
)
def test_temporal_key_rejects_non_iso_values(value: object) -> None:
    assert temporal_key(value) is None


# --- completing the corners --------------------------------------------------------


def test_day_first_month_name_must_be_a_month() -> None:
    assert coerce_date("15 Jam 2026") is None


def test_coerce_datetime_needs_a_readable_date_part() -> None:
    assert coerce_datetime("wat 14:30") is None


def test_coerce_datetime_accepts_a_bare_table_date_as_midnight() -> None:
    assert coerce_datetime("Jan 15, 2026") == CoercedTemporal("2026-01-15T00:00:00")


def test_temporal_key_rejects_iso_shaped_nonsense() -> None:
    assert temporal_key("2026-13-45") is None  # shape fits the fence, calendar refuses
