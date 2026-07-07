"""``--tally FIELD``: count a field's values across structured results (pure)."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["Tally", "explode_record", "render_tally"]

_MISSING = "(missing)"
_TOP_LIVE = 3  # the status line shows the leaders; the final line shows everything


class Tally:
    """Counts one field's values as structured results land."""

    def __init__(self, field: str) -> None:
        self.field = field
        self.counts: Counter[str] = Counter()

    def add(self, record: Mapping[str, object]) -> None:
        value = record.get(self.field, None)
        self.counts[_MISSING if value is None else str(value)] += 1

    def live_segment(self) -> str:
        return render_tally(self.counts, limit=_TOP_LIVE)

    def final_line(self) -> str:
        return f"tally: {render_tally(self.counts, limit=None)}"


def render_tally(counts: Counter[str], *, limit: int | None) -> str:
    ranked = counts.most_common(limit)
    rendered = " · ".join(f"{value} {count}" for value, count in ranked)
    if limit is not None and len(counts) > limit:
        rendered += " · …"
    return rendered


def explode_record(record: Mapping[str, object], field: str) -> list[dict[str, object]]:
    """``--explode``: one row per element of a list-valued field, sibling fields
    copied. Non-lists (including a missing field) pass through as one row."""
    from smartpipe.core.jsontools import as_items

    value = as_items(record.get(field))
    if value is None:
        return [dict(record)]
    if not value:
        return []  # an empty list is zero rows — nothing to say, honestly
    return [{**record, field: element} for element in value]
