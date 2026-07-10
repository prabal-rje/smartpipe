"""The where-predicate grammar (D38/01): closed menu, KQL-flavored semantics."""

from __future__ import annotations

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.engine.predicate import FieldTally, evaluate, parse_predicate
from smartpipe.io.items import item_from_line


def _matches(predicate: str, line: str) -> bool:
    tally = FieldTally()
    return evaluate(parse_predicate(predicate), item_from_line(line, 0), tally)


# --- operators -------------------------------------------------------------------


def test_has_is_word_bounded_and_case_insensitive() -> None:
    assert _matches('text has "error"', "an ERROR occurred")
    assert not _matches('text has "error"', "terrorform initialized")  # not a word hit
    assert not _matches('text has "error"', "all fine")


def test_contains_is_substring_case_insensitive() -> None:
    assert _matches('text contains "error"', "terrorform initialized")
    assert _matches('text contains "ERR"', "an error occurred")


def test_matches_is_case_sensitive_regex() -> None:
    assert _matches("text matches /ERR-\\d+/", "code ERR-42 raised")
    assert not _matches("text matches /err-\\d+/", "code ERR-42 raised")


def test_regex_with_escaped_slash() -> None:
    assert _matches("text matches /api\\/v2/", "GET api/v2/users")


def test_equality_is_numeric_when_both_sides_are() -> None:
    assert _matches("total == 5", '{"total": 5.0}')  # 5 == 5.0 numerically
    assert _matches('status == "open"', '{"status": "open"}')
    assert not _matches('status == "OPEN"', '{"status": "open"}')  # == is case-sensitive


def test_ordered_comparators_require_numbers() -> None:
    assert _matches("total > 100", '{"total": 240}')
    assert not _matches("total > 100", '{"total": "n/a"}')  # non-numeric → no match


def test_not_equal() -> None:
    assert _matches('level != "debug"', '{"level": "error"}')


# --- combinators and precedence --------------------------------------------------


def test_and_or_precedence() -> None:
    # a or (b and c): and binds tighter
    line = '{"a": 1, "b": 0, "c": 0}'
    assert _matches("a == 1 or b == 1 and c == 1", line)


def test_parentheses_override() -> None:
    line = '{"a": 1, "b": 0, "c": 0}'
    assert not _matches("(a == 1 or b == 1) and c == 1", line)


def test_not_binds_tightest() -> None:
    assert _matches('not text has "error" or text has "warn"', "all fine")


# --- missing fields: false, tallied, never fatal ----------------------------------


def test_missing_field_is_false_and_tallied() -> None:
    tally = FieldTally()
    item = item_from_line('{"other": 1}', 0)
    assert evaluate(parse_predicate('level == "error"'), item, tally) is False
    assert tally.missing["level"] == 1


def test_plain_line_field_refs_are_missing_but_text_works() -> None:
    tally = FieldTally()
    item = item_from_line("plain line", 0)
    assert evaluate(parse_predicate("total > 1"), item, tally) is False
    assert tally.missing["total"] == 1
    assert evaluate(parse_predicate('text contains "plain"'), item, tally) is True


def test_non_numeric_ordered_compare_is_tallied() -> None:
    tally = FieldTally()
    item = item_from_line('{"total": "n/a"}', 0)
    assert evaluate(parse_predicate("total > 5"), item, tally) is False
    assert tally.non_numeric["total"] == 1


# --- grammar errors print the menu ------------------------------------------------


def test_garbage_is_a_usage_fault_with_the_menu() -> None:
    with pytest.raises(UsageFault) as excinfo:
        parse_predicate('text hazz "x"')
    message = str(excinfo.value)
    assert "has" in message and "contains" in message and "matches" in message
    assert "filter" in message  # points at the semantic sibling


def test_unclosed_string_is_loud() -> None:
    with pytest.raises(UsageFault, match="unclosed"):
        parse_predicate('text has "oops')


def test_unclosed_paren_is_loud() -> None:
    with pytest.raises(UsageFault, match=r"\)"):
        parse_predicate('(text has "x" or total > 1')


def test_bad_regex_quotes_the_re_error() -> None:
    with pytest.raises(UsageFault, match="regex"):
        parse_predicate("text matches /((/")


def test_trailing_tokens_are_rejected() -> None:
    with pytest.raises(UsageFault):
        parse_predicate('text has "x" total')


# --- temporal comparisons (ledger item 56) ----------------------------------------


def test_iso_dates_compare_temporally() -> None:
    assert _matches('due >= "2026-01-01"', '{"due": "2026-01-15"}')
    assert not _matches('due >= "2026-01-01"', '{"due": "2025-12-31"}')
    assert _matches('due < "2026-02-01"', '{"due": "2026-01-15"}')


def test_date_vs_datetime_promotes_date_to_midnight() -> None:
    assert _matches('ts >= "2026-01-15"', '{"ts": "2026-01-15T00:00:01"}')
    assert not _matches('ts < "2026-01-15"', '{"ts": "2026-01-15T00:00:00"}')
    assert _matches('ts == "2026-01-15"', '{"ts": "2026-01-15T00:00:00"}')


def test_temporal_equality_sees_through_spellings() -> None:
    # the same instant, two ISO spellings — string equality would miss it
    assert _matches('ts == "2026-01-15T02:00:00+02:00"', '{"ts": "2026-01-15T00:00:00Z"}')
    assert _matches('ts != "2026-01-15T00:00:01Z"', '{"ts": "2026-01-15T00:00:00Z"}')


def test_offsets_are_honored_in_ordering() -> None:
    # 09:00+05:30 is 03:30Z — before 04:00Z
    assert _matches('ts < "2026-01-15T04:00:00Z"', '{"ts": "2026-01-15T09:00:00+05:30"}')


def test_temporal_against_non_temporal_falls_back_to_existing_rules() -> None:
    # ordered compare on a non-ISO string: no match, tallied non-numeric
    tally = FieldTally()
    node = parse_predicate('due >= "2026-01-01"')
    assert evaluate(node, item_from_line('{"due": "soonish"}', 0), tally) is False
    assert tally.non_numeric["due"] == 1
    # equality falls back to plain string equality
    assert _matches('due == "soonish"', '{"due": "soonish"}')


def test_numbers_keep_numeric_rules_not_temporal() -> None:
    assert _matches("n >= 20260101", '{"n": 20260115}')  # plain numbers, untouched


# --- field paths (ledger item 63) ---------------------------------------------------


def test_dotted_path_reads_nested_fields() -> None:
    assert _matches('user.plan has "pro"', '{"user": {"plan": "pro plus"}}')
    assert not _matches('user.plan has "pro"', '{"user": {"plan": "free"}}')


def test_index_path_reads_list_elements() -> None:
    assert _matches("items[0].total >= 100", '{"items": [{"total": 240}, {"total": 1}]}')
    assert not _matches("items[0].total >= 100", '{"items": [{"total": 40}]}')
    assert _matches("items[-1].total == 1", '{"items": [{"total": 240}, {"total": 1}]}')


def test_quoted_key_path() -> None:
    assert _matches("a['weird key'] == 7", '{"a": {"weird key": 7}}')


def test_dotted_literal_column_wins_over_the_path() -> None:
    # THE COMPAT RULE: a record with a literal "user.plan" column reads that
    # column, never the traversal
    line = '{"user.plan": "column", "user": {"plan": "nested"}}'
    assert _matches('user.plan == "column"', line)
    assert not _matches('user.plan == "nested"', line)


def test_path_miss_counts_as_a_field_miss_row() -> None:
    tally = FieldTally()
    item = item_from_line('{"user": {"name": "x"}}', 0)
    assert evaluate(parse_predicate('user.plan == "pro"'), item, tally) is False
    assert tally.missing["user.plan"] == 1


def test_path_type_mismatch_is_a_miss_not_an_error() -> None:
    tally = FieldTally()
    # a Key hop into a list, an Index hop into a mapping: misses, tallied
    item = item_from_line('{"items": [1, 2], "user": {"a": 1}}', 0)
    assert evaluate(parse_predicate("items.total > 0"), item, tally) is False
    assert evaluate(parse_predicate("user[0] > 0"), item, tally) is False
    assert tally.missing["items.total"] == 1
    assert tally.missing["user[0]"] == 1


def test_malformed_path_is_loud_when_no_literal_column_claims_it() -> None:
    tally = FieldTally()
    item = item_from_line('{"a": {"b": [1]}}', 0)
    node = parse_predicate("a.b[x] > 0")
    with pytest.raises(UsageFault, match=r"a\.b\[x\] - index must be a number"):
        evaluate(node, item, tally)


# --- referenced_fields (ledger item 19: strict-rows tally scoping) -------------------


def test_referenced_fields_walks_the_whole_tree() -> None:
    from smartpipe.engine.predicate import parse_predicate, referenced_fields

    node = parse_predicate('text has "ERROR" and (level == "warn" or not msg matches /x/)')
    assert referenced_fields(node) == frozenset({"text", "level", "msg"})


def test_referenced_fields_text_only_predicate() -> None:
    from smartpipe.engine.predicate import parse_predicate, referenced_fields

    assert referenced_fields(parse_predicate('text contains "retry"')) == frozenset({"text"})
