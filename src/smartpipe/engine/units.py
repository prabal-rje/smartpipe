"""``split --by`` grammar (D26/D27): UNIT[:N], parsed deterministically, free."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from smartpipe.core.errors import UsageFault

__all__ = ["SplitBy", "parse_by"]

Unit = Literal["tokens", "pages", "minutes", "seconds"]

_DEFAULTS: dict[Unit, int] = {"tokens": 2_000, "pages": 1, "minutes": 10, "seconds": 600}
_HELP = "\n  Examples: --by pages · --by pages:5 · --by minutes:10 · --by tokens:2000"


@dataclass(frozen=True, slots=True)
class SplitBy:
    unit: Unit
    amount: int

    @property
    def slice_seconds(self) -> int:
        """The duration units, normalized to seconds."""
        assert self.unit in ("minutes", "seconds")
        return self.amount * 60 if self.unit == "minutes" else self.amount


def parse_by(text: str) -> SplitBy:
    """``"pages"`` / ``"pages:5"`` / ``"minutes:10"`` → a validated SplitBy."""
    unit_text, colon, amount_text = text.partition(":")
    unit = unit_text.strip()
    if unit not in ("tokens", "pages", "minutes", "seconds"):
        raise UsageFault(
            f"--by wants UNIT or UNIT:N — units: tokens, pages, minutes, seconds{_HELP}"
        )
    if not colon:
        return SplitBy(unit, _DEFAULTS[unit])
    stripped = amount_text.strip()
    if not stripped.isdigit() or int(stripped) < 1:
        raise UsageFault(f"--by {unit}:N wants a positive whole number, got {stripped!r}{_HELP}")
    return SplitBy(unit, int(stripped))
