"""--tally rendering (pure)."""

from __future__ import annotations

from smartpipe.engine.tally import Tally


def test_counts_and_orders_by_frequency() -> None:
    tally = Tally("label")
    for label in ["bug", "bug", "feature", "bug", "question", "feature"]:
        tally.add({"label": label})
    assert tally.final_line() == "tally: bug 3 · feature 2 · question 1"


def test_missing_field_is_counted_honestly() -> None:
    tally = Tally("label")
    tally.add({"label": "bug"})
    tally.add({"other": 1})
    assert "(missing) 1" in tally.final_line()


def test_live_segment_caps_at_the_leaders() -> None:
    tally = Tally("label")
    for index in range(5):
        tally.add({"label": f"kind-{index}"})
    live = tally.live_segment()
    assert live.endswith("…")
    assert live.count("·") == 3  # three leaders + the ellipsis marker


def test_non_string_values_stringify() -> None:
    tally = Tally("priority")
    tally.add({"priority": 1})
    tally.add({"priority": 1})
    assert tally.final_line() == "tally: 1 2"


def test_explode_makes_one_row_per_element() -> None:
    from smartpipe.engine.tally import explode_record

    rows = explode_record({"vendor": "Acme", "risks": ["late", "fx"]}, "risks")
    assert rows == [
        {"vendor": "Acme", "risks": "late"},
        {"vendor": "Acme", "risks": "fx"},
    ]


def test_explode_passes_non_lists_through() -> None:
    from smartpipe.engine.tally import explode_record

    assert explode_record({"risks": "single"}, "risks") == [{"risks": "single"}]
    assert explode_record({"other": 1}, "risks") == [{"other": 1}]


def test_explode_empty_list_is_zero_rows() -> None:
    from smartpipe.engine.tally import explode_record

    assert explode_record({"risks": []}, "risks") == []


# --- field paths (item 63): --tally and --explode read nested data ------------------


def test_tally_reads_a_path() -> None:
    tally = Tally("user.plan")
    tally.add({"user": {"plan": "pro"}})
    tally.add({"user": {"plan": "pro"}})
    tally.add({"user": {"plan": "free"}})
    assert tally.final_line() == "tally: pro 2 · free 1"


def test_tally_exact_flat_key_wins_over_traversal() -> None:
    # THE COMPAT RULE: a literal column named "user.plan" beats the nested path
    tally = Tally("user.plan")
    tally.add({"user.plan": "flat", "user": {"plan": "nested"}})
    assert tally.final_line() == "tally: flat 1"


def test_tally_path_miss_counts_as_missing() -> None:
    tally = Tally("user.plan")
    tally.add({"user": {}})
    assert "(missing) 1" in tally.final_line()


def test_explode_reads_a_path_and_lands_the_full_path_string() -> None:
    from smartpipe.engine.tally import explode_record

    rows = explode_record({"user": {"tags": ["a", "b"]}, "id": 7}, "user.tags")
    # the element lands as a FLAT column named by the full path string — the
    # compat rule makes it readable downstream; the nested record stays as-is
    assert rows == [
        {"user": {"tags": ["a", "b"]}, "id": 7, "user.tags": "a"},
        {"user": {"tags": ["a", "b"]}, "id": 7, "user.tags": "b"},
    ]


def test_explode_exact_flat_key_wins_over_traversal() -> None:
    from smartpipe.engine.tally import explode_record

    record = {"user.tags": ["flat"], "user": {"tags": ["nested", "pair"]}}
    assert explode_record(record, "user.tags") == [
        {"user.tags": "flat", "user": {"tags": ["nested", "pair"]}}
    ]


def test_explode_path_miss_passes_the_row_through() -> None:
    from smartpipe.engine.tally import explode_record

    assert explode_record({"user": {}}, "user.tags") == [{"user": {}}]
