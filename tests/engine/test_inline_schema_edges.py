"""Edge cases for inline brace schemas (D22/D37): whitespace, colons, commas,
parens, duplicates, arrays, case, empties — every way a prompt author will
actually bend the grammar."""

from __future__ import annotations

import pytest

from sempipe.core.errors import UsageFault
from sempipe.engine.prompts import BraceToken, parse_prompt, plan_map, to_instruction
from sempipe.engine.schema import is_strict_compatible


def _schema(prompt: str) -> dict[str, object]:
    plan = plan_map(parse_prompt(prompt, allow_descriptions=True), schema=None)
    assert plan.schema is not None
    return dict(plan.schema)


def _properties(prompt: str) -> dict[str, object]:
    from sempipe.core.jsontools import as_record

    properties = as_record(_schema(prompt)["properties"])
    assert properties is not None
    return dict(properties)


# --- whitespace tolerance -------------------------------------------------------


def test_no_space_after_colon() -> None:
    assert _properties("Extract {vendor string:the supplier}") == {
        "vendor": {"type": "string", "description": "the supplier"}
    }


def test_generous_whitespace_everywhere() -> None:
    assert _properties("Extract {  vendor   string  :  the supplier  ,  total   number  }") == {
        "vendor": {"type": "string", "description": "the supplier"},
        "total": {"type": "number"},
    }


def test_space_before_colon_after_enum() -> None:
    assert _properties("Extract {status enum(paid, unpaid) : payment state}") == {
        "status": {"enum": ["paid", "unpaid"], "description": "payment state"}
    }


# --- enum spacing and commas ----------------------------------------------------


def test_enum_without_spaces() -> None:
    assert _properties("Extract {status enum(paid,unpaid)}") == {
        "status": {"enum": ["paid", "unpaid"]}
    }


def test_enum_with_inner_padding() -> None:
    assert _properties("Extract {status enum( paid , unpaid )}") == {
        "status": {"enum": ["paid", "unpaid"]}
    }


def test_empty_enum_is_a_helpful_error() -> None:
    with pytest.raises(UsageFault, match="enum needs at least one value"):
        parse_prompt("Extract {status enum()}", allow_descriptions=True)


def test_unclosed_enum_paren_is_loud() -> None:
    with pytest.raises(UsageFault, match="unbalanced"):
        parse_prompt("Extract {status enum(paid, unpaid}", allow_descriptions=True)


# --- colons inside descriptions -------------------------------------------------


def test_description_may_contain_colons() -> None:
    assert _properties("Extract {note string: format: freeform prose}")["note"] == {
        "type": "string",
        "description": "format: freeform prose",
    }


def test_description_may_be_a_url() -> None:
    assert _properties("Extract {link string: like https://example.com/x}")["link"] == {
        "type": "string",
        "description": "like https://example.com/x",
    }


# --- commas: the documented ambiguity stays honest -------------------------------


def test_comma_in_description_reads_as_a_second_field() -> None:
    # inherent grammar ambiguity (documented since D22): the comma separates
    # fields, so the tail becomes a real second field, never a silent drop
    assert _properties("Extract {a string: hello, world}") == {
        "a": {"type": "string", "description": "hello"},
        "world": {},
    }


def test_trailing_comma_is_an_error() -> None:
    with pytest.raises(UsageFault, match="invalid field group"):
        parse_prompt("Extract {a string: hi,}", allow_descriptions=True)


def test_double_comma_is_an_error() -> None:
    with pytest.raises(UsageFault, match="invalid field group"):
        parse_prompt("Extract {a,,b}", allow_descriptions=True)


def test_empty_group_is_an_error() -> None:
    with pytest.raises(UsageFault, match="invalid field group"):
        parse_prompt("Extract {}", allow_descriptions=True)


def test_unbalanced_paren_in_description_cannot_swallow_fields() -> None:
    # without the balance check, "weird (unclosed, b" would eat field b silently
    with pytest.raises(UsageFault, match="unbalanced"):
        parse_prompt("Extract {a string: weird (unclosed, b}", allow_descriptions=True)


def test_balanced_parens_in_description_are_fine() -> None:
    assert _properties("Extract {a string: amount (usd), b number}") == {
        "a": {"type": "string", "description": "amount (usd)"},
        "b": {"type": "number"},
    }


# --- arrays, case, near-miss types ----------------------------------------------


def test_array_types() -> None:
    assert _properties("Extract {tags string[], scores number[]: 0 to 1}") == {
        "tags": {"type": "array", "items": {"type": "string"}},
        "scores": {"type": "array", "items": {"type": "number"}, "description": "0 to 1"},
    }


def test_space_before_brackets_is_not_a_type() -> None:
    with pytest.raises(UsageFault, match="isn't a type"):
        parse_prompt("Extract {tags string []}", allow_descriptions=True)


def test_types_are_case_sensitive_with_the_menu_shown() -> None:
    with pytest.raises(UsageFault) as excinfo:
        parse_prompt("Extract {a String}", allow_descriptions=True)
    assert "'String' isn't a type" in str(excinfo.value)
    assert "string · number" in str(excinfo.value)


def test_bool_is_not_boolean() -> None:
    with pytest.raises(UsageFault, match="'bool' isn't a type"):
        parse_prompt("Extract {done bool}", allow_descriptions=True)


def test_type_without_a_name_is_an_error() -> None:
    with pytest.raises(UsageFault, match="invalid field group"):
        parse_prompt("Extract {enum(a,b)}", allow_descriptions=True)


# --- duplicates across and within groups ----------------------------------------


def test_duplicate_field_same_type_dedupes() -> None:
    schema = _schema("Compare {a string} against {a string}")
    from sempipe.core.jsontools import as_items

    required = as_items(schema["required"])
    assert required is not None
    assert list(required) == ["a"]  # no duplicate required entries (strict mode 400s on them)


def test_duplicate_field_conflicting_types_is_an_error() -> None:
    with pytest.raises(UsageFault, match="typed twice"):
        plan_map(
            parse_prompt("Compare {a string} against {a number}", allow_descriptions=True),
            schema=None,
        )


# --- rendering + strictness interplay --------------------------------------------


def test_instruction_keeps_descriptions_but_not_types() -> None:
    instruction = to_instruction(
        parse_prompt("Extract {vendor string: the supplier, total number}", allow_descriptions=True)
    )
    assert "the supplier" in instruction  # guidance reaches the model
    assert "string" not in instruction  # the schema enforces types; prose stays clean


def test_literal_braces_coexist_with_typed_groups() -> None:
    tokens = parse_prompt("Return {{json}} with {status enum(a, b)}", allow_descriptions=True)
    brace = next(t for t in tokens if isinstance(t, BraceToken))
    assert brace.fields == ("status",)


def test_typed_enum_group_is_strict() -> None:
    assert is_strict_compatible(_schema("Extract {status enum(paid, unpaid), n number}")) is True
