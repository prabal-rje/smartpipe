from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sempipe.core.errors import ItemError, SetupFault
from sempipe.engine.schema import (
    is_strict_compatible,
    load_schema,
    shorthand_to_schema,
    validate_and_coerce,
)

# --- shorthand ----------------------------------------------------------------


def test_shorthand_builds_strict_object_schema() -> None:
    assert shorthand_to_schema(("vendor", "total")) == {
        "type": "object",
        "properties": {"vendor": {}, "total": {}},
        "required": ["vendor", "total"],
        "additionalProperties": False,
    }


# --- loading ------------------------------------------------------------------


def test_load_schema_reads_json(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    path.write_text(json.dumps(schema), encoding="utf-8")
    assert load_schema(path) == schema


def test_load_missing_file_is_setup_fault(tmp_path: Path) -> None:
    with pytest.raises(SetupFault, match="no schema file"):
        load_schema(tmp_path / "nope.json")


def test_load_broken_json_is_setup_fault(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SetupFault, match="isn't valid JSON"):
        load_schema(path)


def test_load_non_object_schema_is_setup_fault(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SetupFault):
        load_schema(path)


# --- validate + coerce --------------------------------------------------------

_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": {"type": "string"},
        "total": {"type": "number"},
        "count": {"type": "integer"},
        "paid": {"type": "boolean"},
    },
    "required": ["vendor", "total"],
    "additionalProperties": False,
}


def test_valid_json_passes_through() -> None:
    reply = '{"vendor": "Acme", "total": 1250.0}'
    assert validate_and_coerce(reply, _SCHEMA) == {"vendor": "Acme", "total": 1250.0}


def test_coerces_stringified_scalars() -> None:
    reply = '{"vendor": "Acme", "total": "1250.0", "count": "3", "paid": "true"}'
    assert validate_and_coerce(reply, _SCHEMA) == {
        "vendor": "Acme",
        "total": 1250.0,
        "count": 3,
        "paid": True,
    }


def test_drops_extra_keys_when_additional_properties_false() -> None:
    reply = '{"vendor": "Acme", "total": 5, "junk": "drop me"}'
    result = validate_and_coerce(reply, _SCHEMA)
    assert "junk" not in result


def test_strips_markdown_code_fence() -> None:
    reply = '```json\n{"vendor": "Acme", "total": 5}\n```'
    assert validate_and_coerce(reply, _SCHEMA)["vendor"] == "Acme"


def test_extracts_object_amid_prose() -> None:
    reply = 'Here is the data:\n{"vendor": "Acme", "total": 5}\nHope that helps!'
    assert validate_and_coerce(reply, _SCHEMA)["vendor"] == "Acme"


def test_non_json_reply_is_item_error() -> None:
    with pytest.raises(ItemError, match="valid JSON"):
        validate_and_coerce("I cannot do that", _SCHEMA)


def test_json_array_reply_is_item_error() -> None:
    with pytest.raises(ItemError, match="not an object"):
        validate_and_coerce("[1, 2, 3]", _SCHEMA)


def test_missing_required_field_is_item_error() -> None:
    with pytest.raises(ItemError):
        validate_and_coerce('{"vendor": "Acme"}', _SCHEMA)


def test_uncoercible_type_fails_validation() -> None:
    with pytest.raises(ItemError):
        validate_and_coerce('{"vendor": "Acme", "total": "not a number"}', _SCHEMA)


def test_error_message_carries_repair_context() -> None:
    with pytest.raises(ItemError) as excinfo:
        validate_and_coerce('{"total": 5}', _SCHEMA)
    # the message is fed back to the model on repair, so it must name the problem
    assert "vendor" in str(excinfo.value).lower() or "required" in str(excinfo.value).lower()


def test_coerces_null_typed_field() -> None:
    schema = {
        "type": "object",
        "properties": {"note": {"type": "null"}},
        "required": ["note"],
        "additionalProperties": False,
    }
    assert validate_and_coerce('{"note": "null"}', schema) == {"note": None}


def test_boolean_false_and_uncoercible_are_handled() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "boolean"}, "b": {"type": "string"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    assert validate_and_coerce('{"a": "false", "b": "maybe"}', schema) == {"a": False, "b": "maybe"}


def test_extra_keys_kept_when_additional_properties_not_forbidden() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    result = validate_and_coerce('{"a": "x", "extra": 1}', schema)
    assert result == {"a": "x", "extra": 1}


def test_uncoercible_boolean_falls_back_then_fails_validation() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "boolean"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    with pytest.raises(ItemError):  # "maybe" stays a string, then jsonschema rejects it
        validate_and_coerce('{"a": "maybe"}', schema)


@given(st.text())
def test_never_raises_other_than_item_error(text: str) -> None:
    with contextlib.suppress(ItemError):
        validate_and_coerce(text, _SCHEMA)


# --- strict-mode compatibility (workstream 10 Task 1) -------------------------------


def test_shorthand_schema_is_strict_compatible() -> None:
    assert is_strict_compatible(shorthand_to_schema(["vendor", "total"])) is True


def test_optional_field_is_not_strict_compatible() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"a": {}, "b": {}},
        "required": ["a"],  # b is optional — strict mode 400s on this
        "additionalProperties": False,
    }
    assert is_strict_compatible(schema) is False


def test_open_object_is_not_strict_compatible() -> None:
    assert is_strict_compatible({"type": "object"}) is False  # additionalProperties unset


def test_nested_optional_is_not_strict_compatible() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "inner": {
                "type": "object",
                "properties": {"x": {}, "y": {}},
                "required": ["x"],
                "additionalProperties": False,
            }
        },
        "required": ["inner"],
        "additionalProperties": False,
    }
    assert is_strict_compatible(schema) is False


def test_nested_closed_object_is_strict_compatible() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "inner": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
                "additionalProperties": False,
            }
        },
        "required": ["inner"],
        "additionalProperties": False,
    }
    assert is_strict_compatible(schema) is True


def test_array_items_recurse_for_strictness() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {"type": "object", "properties": {"a": {}}, "required": []},
            }
        },
        "required": ["rows"],
        "additionalProperties": False,
    }
    assert is_strict_compatible(schema) is False  # items object is open + optional
