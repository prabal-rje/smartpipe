"""The summarize grammar + fold (D38/07): KQL naming, honest nulls."""

from __future__ import annotations

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.engine.aggregate import GroupState, finish, fold, group_key, parse_summarize


def _summarize(expression: str, records: list[dict[str, object]]) -> list[dict[str, object]]:
    plan = parse_summarize(expression)
    groups: dict[tuple[object, ...], GroupState] = {}
    for record in records:
        key = group_key(plan, record)
        state = groups.setdefault(key, GroupState())
        fold(plan, state, record)
    return [finish(plan, key, state) for key, state in groups.items()]


def test_the_kql_workhorse_line() -> None:
    rows = _summarize(
        "count(), avg(total), p95(total) by region",
        [
            {"region": "EU", "total": 50},
            {"region": "EU", "total": 100},
            {"region": "US", "total": 10},
        ],
    )
    eu = next(row for row in rows if row["region"] == "EU")
    assert eu == {"region": "EU", "count": 2, "avg_total": 75.0, "p95_total": 100.0}


def test_missing_group_field_groups_under_null_visibly() -> None:
    rows = _summarize("count() by pass", [{"pass": True}, {"other": 1}])
    assert {row["pass"] for row in rows} == {True, None}


def test_non_numeric_values_skip_and_tally() -> None:
    plan = parse_summarize("avg(total)")
    state = GroupState()
    fold(plan, state, {"total": "n/a"})
    fold(plan, state, {"total": 10})
    assert state.skipped_non_numeric["total"] == 1
    assert finish(plan, (), state)["avg_total"] == 10.0


def test_dcount_counts_distinct() -> None:
    rows = _summarize("dcount(user)", [{"user": "a"}, {"user": "a"}, {"user": "b"}])
    assert rows[0]["dcount_user"] == 2


def test_all_null_numeric_agg_is_null_not_zero() -> None:
    rows = _summarize("avg(total)", [{"x": 1}])
    assert rows[0]["avg_total"] is None


def test_unknown_function_names_the_menu() -> None:
    with pytest.raises(UsageFault) as excinfo:
        parse_summarize("median(total)")
    assert "isn't an aggregation" in str(excinfo.value)
    assert "p95(f)" in str(excinfo.value)


def test_count_takes_no_field_and_others_need_one() -> None:
    with pytest.raises(UsageFault, match="count\\(\\) takes no field"):
        parse_summarize("count(total)")
    with pytest.raises(UsageFault, match="needs a field"):
        parse_summarize("avg()")


def test_bare_word_is_stuck_at() -> None:
    with pytest.raises(UsageFault, match="stuck at"):
        parse_summarize("count")


def test_bin_groups_by_utc_bucket_label() -> None:
    plan = parse_summarize("count() by bin(ts, 1h)")
    key = group_key(plan, {"ts": "2025-01-01T14:38:00Z"})
    assert key == ("14:00",)
    assert plan.by_names == ("ts_bin",)
    assert group_key(plan, {"ts": "junk"}) == (None,)  # unparseable groups under null
