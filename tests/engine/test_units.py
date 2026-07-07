"""The --by grammar: deterministic, free, loud."""

from __future__ import annotations

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.engine.units import SplitBy, parse_by


@pytest.mark.parametrize(
    ("text", "unit", "amount"),
    [
        ("tokens", "tokens", 2000),
        ("tokens:500", "tokens", 500),
        ("pages", "pages", 1),
        ("pages:5", "pages", 5),
        ("minutes:10", "minutes", 10),
        ("seconds:90", "seconds", 90),
    ],
)
def test_units_parse(text: str, unit: str, amount: int) -> None:
    assert parse_by(text) == SplitBy(unit, amount)  # type: ignore[arg-type]


def test_duration_normalizes_to_seconds() -> None:
    assert parse_by("minutes:10").slice_seconds == 600
    assert parse_by("seconds:45").slice_seconds == 45


def test_unknown_unit_lists_the_menu() -> None:
    with pytest.raises(UsageFault, match="units: tokens, pages, minutes, seconds"):
        parse_by("chapters")


def test_bad_amounts_are_loud() -> None:
    with pytest.raises(UsageFault, match="positive whole number"):
        parse_by("pages:0")
    with pytest.raises(UsageFault, match="positive whole number"):
        parse_by("minutes:ten")
