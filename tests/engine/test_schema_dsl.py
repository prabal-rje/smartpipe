"""Rung 3 (D22): ``--schema-from`` — deterministic, free, fails at parse time."""

from __future__ import annotations

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.engine.schema import is_strict_compatible
from smartpipe.engine.schema_dsl import dsl_to_schema, type_token


def test_the_flagship_example() -> None:
    schema = dsl_to_schema("vendor string; total number >= 0; status enum(paid, unpaid)")
    assert schema == {
        "type": "object",
        "properties": {
            "vendor": {"type": "string"},
            "total": {"type": "number", "minimum": 0},
            "status": {"enum": ["paid", "unpaid"]},
        },
        "required": ["vendor", "total", "status"],
        "additionalProperties": False,
    }
    assert is_strict_compatible(schema) is True


def test_every_type_maps() -> None:
    from smartpipe.core.jsontools import as_record

    schema = dsl_to_schema("a string; b number; c integer; d boolean; e string[]; f number[]")
    properties = as_record(schema["properties"])
    assert properties is not None
    assert properties["c"] == {"type": "integer"}
    assert properties["d"] == {"type": "boolean"}
    assert properties["e"] == {"type": "array", "items": {"type": "string"}}
    assert properties["f"] == {"type": "array", "items": {"type": "number"}}


def test_string_length_constraints() -> None:
    from smartpipe.core.jsontools import as_record

    schema = dsl_to_schema("summary string minLength=1 maxLength=280")
    properties = as_record(schema["properties"])
    assert properties is not None
    assert properties["summary"] == {"type": "string", "minLength": 1, "maxLength": 280}


def test_optional_downgrades_required_and_strictness() -> None:
    schema = dsl_to_schema("a string; b number optional")
    assert schema["required"] == ["a"]
    assert is_strict_compatible(schema) is False  # the 1.1 fix keeps the wire honest


def test_unknown_type_is_the_pinned_error() -> None:
    with pytest.raises(UsageFault) as excinfo:
        dsl_to_schema("priority enun(low,high)")
    message = str(excinfo.value)
    assert message.startswith("--schema-from: unexpected 'enun(low,high)' for field 'priority'")
    assert "enum(a, b," in message  # the type list rides the screen


def test_leftover_junk_is_named() -> None:
    with pytest.raises(UsageFault, match="unexpected 'wat' for field 'total'"):
        dsl_to_schema("total number wat")


def test_length_constraints_reject_numbers() -> None:
    with pytest.raises(UsageFault, match="minLength only applies to string"):
        dsl_to_schema("total number minLength=3")


def test_bounds_reject_strings() -> None:
    with pytest.raises(UsageFault, match=">= only applies to number"):
        dsl_to_schema("name string >= 3")


def test_empty_enum_is_rejected() -> None:
    with pytest.raises(UsageFault, match="enum needs at least one value"):
        dsl_to_schema("status enum()")


def test_duplicate_field_is_rejected() -> None:
    with pytest.raises(UsageFault, match="names 'a' more than once"):
        dsl_to_schema("a string; a number")


def test_bad_name_is_rejected() -> None:
    with pytest.raises(UsageFault, match="field names must be identifiers"):
        dsl_to_schema("2fast string")


def test_blank_input_is_rejected() -> None:
    with pytest.raises(UsageFault, match="describes no fields"):
        dsl_to_schema("  ;  ")


def test_nullable_type_tokens() -> None:
    assert type_token("string?") == {"type": ["string", "null"]}
    assert type_token("number?") == {"type": ["number", "null"]}
    assert type_token("boolean?") == {"type": ["boolean", "null"]}
    assert type_token("string[]?") == {"type": ["array", "null"], "items": {"type": "string"}}


def test_nullable_enum_is_guided_to_a_value() -> None:
    with pytest.raises(UsageFault, match="explicit value"):
        type_token("enum(a, b)?")
