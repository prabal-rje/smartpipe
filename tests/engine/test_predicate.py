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
