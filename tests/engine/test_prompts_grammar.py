from __future__ import annotations

import contextlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sempipe.core.errors import UsageFault
from sempipe.engine.prompts import (
    BraceToken,
    TextToken,
    brace_fields,
    has_brace,
    parse_prompt,
    render,
)

# --- tokenizing ---------------------------------------------------------------


def test_plain_text_is_one_token() -> None:
    assert parse_prompt("translate to French") == (TextToken("translate to French"),)


def test_single_field_group() -> None:
    tokens = parse_prompt("Extract {total}")
    assert tokens == (TextToken("Extract "), BraceToken(("total",), "{total}"))


def test_comma_group_collects_all_fields() -> None:
    tokens = parse_prompt("Extract {vendor, date, total}")
    assert tokens[-1] == BraceToken(("vendor", "date", "total"), "{vendor, date, total}")


def test_multiple_groups() -> None:
    tokens = parse_prompt("{a} and {b}")
    braces = [t for t in tokens if isinstance(t, BraceToken)]
    assert [b.fields for b in braces] == [("a",), ("b",)]


def test_escaped_braces_are_literal() -> None:
    tokens = parse_prompt("use {{ and }} literally")
    assert tokens == (TextToken("use { and } literally"),)


def test_escaped_and_real_braces_mix() -> None:
    tokens = parse_prompt("literal {{x}} but field {y}")
    assert tokens == (
        TextToken("literal {x} but field "),
        BraceToken(("y",), "{y}"),
    )


def test_whitespace_inside_braces_tolerated() -> None:
    assert parse_prompt("{  a ,b  }")[0] == BraceToken(("a", "b"), "{  a ,b  }")


# --- errors -------------------------------------------------------------------


def test_unclosed_brace_is_usage_fault() -> None:
    with pytest.raises(UsageFault, match="unclosed"):
        parse_prompt("Extract {vendor")


def test_unmatched_closing_brace_is_usage_fault() -> None:
    with pytest.raises(UsageFault, match="unexpected"):
        parse_prompt("Extract vendor}")


def test_bad_identifier_is_usage_fault_with_the_group() -> None:
    with pytest.raises(UsageFault) as excinfo:
        parse_prompt("Extract {bad name!}")
    assert "{bad name!}" in str(excinfo.value)


def test_empty_group_is_usage_fault() -> None:
    with pytest.raises(UsageFault):
        parse_prompt("Extract {}")


def test_empty_field_in_group_is_usage_fault() -> None:
    with pytest.raises(UsageFault):
        parse_prompt("Extract {a,,b}")


def test_field_starting_with_digit_is_rejected() -> None:
    with pytest.raises(UsageFault):
        parse_prompt("Extract {1st}")


# --- helpers ------------------------------------------------------------------


def test_brace_fields_dedupes_preserving_order() -> None:
    tokens = parse_prompt("{a, b} then {b, c}")
    assert brace_fields(tokens) == ("a", "b", "c")


def test_has_brace() -> None:
    assert has_brace(parse_prompt("Extract {x}")) is True
    assert has_brace(parse_prompt("Extract x")) is False
    assert has_brace(parse_prompt("literal {{x}}")) is False


def test_render_round_trips_text_and_braces() -> None:
    tokens = parse_prompt("literal {{x}} and field {a, b}")
    assert render(tokens) == "literal {{x}} and field {a, b}"
    # explicit brace-token render path
    assert render((BraceToken(("a",), "{a}"),)) == "{a}"


# --- properties ---------------------------------------------------------------


@given(st.text())
def test_parse_never_raises_unexpectedly(text: str) -> None:
    with contextlib.suppress(UsageFault):  # the only allowed failure
        parse_prompt(text)


@given(st.text())
def test_parse_is_idempotent_through_render(text: str) -> None:
    try:
        tokens = parse_prompt(text)
    except UsageFault:
        return
    assert parse_prompt(render(tokens)) == tokens
