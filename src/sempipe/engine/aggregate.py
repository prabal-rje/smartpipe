"""The ``summarize`` grammar and fold (D38/07) — KQL's micro-syntax, pure.

`count(), avg(total), p95(total) by region` parses once into a plan; records
fold into per-group state in one pass. Percentile aggregations keep each
group's numeric values (the one memory note); everything else streams.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, assert_never

from sempipe.core.errors import UsageFault

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "SUMMARIZE_MENU",
    "Aggregation",
    "GroupState",
    "SummarizePlan",
    "finish",
    "fold",
    "parse_summarize",
]

SUMMARIZE_MENU = (
    "error: can't parse the summarize expression\n"
    "  Shape:        AGG[, AGG…] [by FIELD[, FIELD…]]\n"
    "  Aggregations: count() · sum(f) · avg(f) · min(f) · max(f)\n"
    "                p50(f) · p90(f) · p95(f) · p99(f) · dcount(f)\n"
    "  Example:      sempipe summarize 'count(), avg(total), p95(total) by region'"
)

_FUNCTIONS = ("count", "sum", "avg", "min", "max", "p50", "p90", "p95", "p99", "dcount")
_Fn = Literal["count", "sum", "avg", "min", "max", "p50", "p90", "p95", "p99", "dcount"]


@dataclass(frozen=True, slots=True)
class Aggregation:
    fn: _Fn
    field: str | None  # None only for count()
    name: str  # KQL's output naming: count, avg_total, p95_total, dcount_user


@dataclass(frozen=True, slots=True)
class SummarizePlan:
    aggregations: tuple[Aggregation, ...]
    by: tuple[str, ...]


def parse_summarize(text: str) -> SummarizePlan:
    head, _, tail = text.partition(" by ")
    by = tuple(name.strip() for name in tail.split(",") if name.strip()) if tail else ()
    if tail and not by:
        raise UsageFault(SUMMARIZE_MENU + "\n  ('by' needs at least one field)")
    aggregations = tuple(_parse_agg(part.strip()) for part in head.split(",") if part.strip())
    if not aggregations:
        raise UsageFault(SUMMARIZE_MENU + "\n  (name at least one aggregation)")
    return SummarizePlan(aggregations, by)


def _parse_agg(token: str) -> Aggregation:
    name, paren, rest = token.partition("(")
    name = name.strip()
    if not paren or not rest.endswith(")"):
        raise UsageFault(SUMMARIZE_MENU + f"\n  (stuck at: {token!r})")
    if name not in _FUNCTIONS:
        raise UsageFault(SUMMARIZE_MENU + f"\n  ({name!r} isn't an aggregation)")
    inner = rest[:-1].strip()
    if name == "count":
        if inner:
            raise UsageFault(SUMMARIZE_MENU + "\n  (count() takes no field)")
        return Aggregation("count", None, "count")
    if not inner:
        raise UsageFault(SUMMARIZE_MENU + f"\n  ({name}() needs a field)")
    fn: _Fn = name  # type: ignore[assignment]  # narrowed by the membership check
    return Aggregation(fn, inner, f"{name}_{inner}")


@dataclass(slots=True)
class GroupState:
    count: int = 0
    sums: dict[str, float] = field(default_factory=dict[str, float])
    mins: dict[str, float] = field(default_factory=dict[str, float])
    maxes: dict[str, float] = field(default_factory=dict[str, float])
    numeric_counts: dict[str, int] = field(default_factory=dict[str, int])
    values: dict[str, list[float]] = field(default_factory=dict[str, "list[float]"])
    distinct: dict[str, set[str]] = field(default_factory=dict[str, "set[str]"])
    skipped_non_numeric: dict[str, int] = field(default_factory=dict[str, int])


def fold(plan: SummarizePlan, state: GroupState, record: Mapping[str, object]) -> None:
    state.count += 1
    for aggregation in plan.aggregations:
        if aggregation.field is None:
            continue
        value = record.get(aggregation.field)
        if aggregation.fn == "dcount":
            if value is not None:
                state.distinct.setdefault(aggregation.field, set()).add(str(value))
            continue
        number = _numeric(value)
        if number is None:
            if value is not None:
                state.skipped_non_numeric[aggregation.field] = (
                    state.skipped_non_numeric.get(aggregation.field, 0) + 1
                )
            continue
        state.numeric_counts[aggregation.field] = state.numeric_counts.get(aggregation.field, 0) + 1
        state.sums[aggregation.field] = state.sums.get(aggregation.field, 0.0) + number
        held = state.mins.get(aggregation.field)
        state.mins[aggregation.field] = number if held is None else min(held, number)
        held = state.maxes.get(aggregation.field)
        state.maxes[aggregation.field] = number if held is None else max(held, number)
        if aggregation.fn in ("p50", "p90", "p95", "p99"):
            state.values.setdefault(aggregation.field, []).append(number)


def finish(plan: SummarizePlan, key: tuple[object, ...], state: GroupState) -> dict[str, object]:
    row: dict[str, object] = dict(zip(plan.by, key, strict=True))
    for aggregation in plan.aggregations:
        row[aggregation.name] = _value(aggregation, state)
    return row


def _value(aggregation: Aggregation, state: GroupState) -> object:
    field_name = aggregation.field
    match aggregation.fn:
        case "count":
            return state.count
        case "dcount":
            assert field_name is not None
            return len(state.distinct.get(field_name, set()))
        case "sum":
            assert field_name is not None
            return _round(state.sums.get(field_name)) if field_name in state.sums else None
        case "avg":
            assert field_name is not None
            seen = state.numeric_counts.get(field_name, 0)
            return _round(state.sums[field_name] / seen) if seen else None
        case "min":
            assert field_name is not None
            return _round(state.mins.get(field_name))
        case "max":
            assert field_name is not None
            return _round(state.maxes.get(field_name))
        case "p50" | "p90" | "p95" | "p99":
            assert field_name is not None
            values = sorted(state.values.get(field_name, ()))
            if not values:
                return None
            quantile = int(aggregation.fn[1:]) / 100
            rank = min(len(values) - 1, max(0, round(quantile * (len(values) - 1))))
            return _round(values[rank])
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4)


def _numeric(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
