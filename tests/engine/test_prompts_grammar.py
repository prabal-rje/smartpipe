from __future__ import annotations

import contextlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smartpipe.core.errors import UsageFault
from smartpipe.engine.prompts import (
    BraceToken,
    TextToken,
    brace_fields,
    has_brace,
    parse_prompt,
    render,
)
from smartpipe.engine.schema import BARE_PROPERTY

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


# --- rung 2: brace descriptions (D22, map only) -------------------------------------


def test_description_parses_and_keeps_the_name() -> None:
    tokens = parse_prompt("Extract {vendor: the supplier name, total}", allow_descriptions=True)
    brace = next(t for t in tokens if isinstance(t, BraceToken))
    assert brace.fields == ("vendor", "total")
    assert brace.notes == ("the supplier name", None)


def test_description_reaches_the_schema() -> None:
    from smartpipe.engine.prompts import plan_map

    tokens = parse_prompt("Extract {vendor: who sent it, total}", allow_descriptions=True)
    plan = plan_map(tokens, schema=None)
    assert plan.schema is not None
    properties = plan.schema["properties"]
    assert properties == {
        "vendor": {**BARE_PROPERTY, "description": "who sent it"},
        "total": dict(BARE_PROPERTY),
    }


def test_descriptions_do_not_change_strictness() -> None:
    # shorthand schemas are honestly non-strict either way (untyped properties —
    # live-caught); a description must not flip that verdict in either direction
    from smartpipe.engine.prompts import plan_map
    from smartpipe.engine.schema import is_strict_compatible

    described = plan_map(parse_prompt("Extract {a: x, b}", allow_descriptions=True), schema=None)
    bare = plan_map(parse_prompt("Extract {a, b}"), schema=None)
    assert described.schema is not None and bare.schema is not None
    assert is_strict_compatible(described.schema) == is_strict_compatible(bare.schema)


def test_description_stays_in_the_instruction_text() -> None:
    from smartpipe.engine.prompts import to_instruction

    tokens = parse_prompt("Extract {vendor: the supplier name}", allow_descriptions=True)
    assert "the supplier name" in to_instruction(tokens)  # guidance reaches the model


def test_empty_description_is_the_pinned_error() -> None:
    with pytest.raises(UsageFault, match="names field 'vendor' with an empty description"):
        parse_prompt("Extract {vendor: }", allow_descriptions=True)


def test_colon_stays_invalid_without_the_map_flag() -> None:
    # filter/reduce/join input references never grow descriptions (ux.md, D22)
    with pytest.raises(UsageFault, match="invalid field group"):
        parse_prompt("keep {priority: high}")


# --- inline types (D37) ---------------------------------------------------------


def test_type_and_description_together() -> None:
    from smartpipe.engine.prompts import plan_map

    tokens = parse_prompt(
        "Extract {vendor string: the supplier name, total number, "
        "status enum(paid, unpaid): payment state}",
        allow_descriptions=True,
    )
    plan = plan_map(tokens, schema=None)
    assert plan.schema is not None
    assert plan.schema["properties"] == {
        "vendor": {"type": "string", "description": "the supplier name"},
        "total": {"type": "number"},
        "status": {"enum": ["paid", "unpaid"], "description": "payment state"},
    }
    assert plan.schema["required"] == ["vendor", "total", "status"]


def test_enum_commas_survive_the_brace_split() -> None:
    tokens = parse_prompt("Pick {mood enum(happy, sad, neutral)}", allow_descriptions=True)
    brace = next(t for t in tokens if isinstance(t, BraceToken))
    assert brace.fields == ("mood",)
    assert brace.prop_for(0) == {"enum": ["happy", "sad", "neutral"]}


def test_fully_typed_braces_regain_strict_mode() -> None:
    from smartpipe.engine.prompts import plan_map
    from smartpipe.engine.schema import is_strict_compatible

    typed = plan_map(
        parse_prompt("Extract {a string, b number}", allow_descriptions=True), schema=None
    )
    assert typed.schema is not None
    assert is_strict_compatible(typed.schema) is True  # every property carries a type
    mixed = plan_map(parse_prompt("Extract {a string, b}", allow_descriptions=True), schema=None)
    assert mixed.schema is not None
    # D48: bare fields carry the scalar union now, so mixed groups are strict too
    assert is_strict_compatible(mixed.schema) is True


def test_unknown_inline_type_names_the_menu() -> None:
    with pytest.raises(UsageFault) as excinfo:
        parse_prompt("Extract {total numbr: the total}", allow_descriptions=True)
    message = str(excinfo.value)
    assert "'numbr' isn't a type" in message
    assert "enum(a, b," in message
    assert "--schema-from" in message  # constraints live over there


def test_types_stay_invalid_outside_map() -> None:
    with pytest.raises(UsageFault, match="invalid field group"):
        parse_prompt("keep {price number}")  # filter/reduce/join: bare idents only


# --- field paths (item 63): extraction field names stay flat -------------------------


def test_extraction_into_a_path_is_the_flat_fields_error() -> None:
    with pytest.raises(UsageFault) as excinfo:
        parse_prompt("Extract {user.name}", allow_descriptions=True)
    message = str(excinfo.value)
    assert "can't extract into 'user.name'" in message
    assert "flat" in message


def test_typed_path_field_is_the_flat_fields_error() -> None:
    with pytest.raises(UsageFault) as excinfo:
        parse_prompt("Extract {user.name string}", allow_descriptions=True)
    assert "can't extract into 'user.name'" in str(excinfo.value)


def test_bracket_path_in_extraction_is_the_flat_fields_error() -> None:
    with pytest.raises(UsageFault, match="can't extract into 'items\\[0\\]'"):
        parse_prompt("Extract {items[0]}", allow_descriptions=True)


def test_flat_extraction_fields_are_untouched_by_the_path_grammar() -> None:
    tokens = parse_prompt("Extract {vendor, total}", allow_descriptions=True)
    assert tokens == parse_prompt("Extract {vendor, total}")
