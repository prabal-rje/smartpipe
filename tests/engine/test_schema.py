from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smartpipe.core.errors import ItemError, SetupFault
from smartpipe.engine.schema import (
    BARE_PROPERTY,
    deterministic_repairs,
    is_strict_compatible,
    load_schema,
    reset_deterministic_repairs,
    shorthand_to_schema,
    validate_and_coerce,
)

# --- shorthand ----------------------------------------------------------------


def test_shorthand_builds_strict_object_schema() -> None:
    assert shorthand_to_schema(("vendor", "total")) == {
        "type": "object",
        # D48: bare fields are any-scalar-not-null, so strict wires can hold them
        "properties": {
            "vendor": dict(BARE_PROPERTY),
            "total": dict(BARE_PROPERTY),
        },
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


# --- rung 0: deterministic repair before the paid retry (item 58) --------------------


def test_rung_zero_fixes_a_fenced_reply_with_a_trailing_comma() -> None:
    reset_deterministic_repairs()
    reply = '```json\n{"vendor": "Acme", "total": 5,}\n```'
    assert validate_and_coerce(reply, _SCHEMA) == {"vendor": "Acme", "total": 5}
    assert deterministic_repairs() == 1


def test_rung_zero_fixes_a_python_repr_reply() -> None:
    reset_deterministic_repairs()
    assert validate_and_coerce("{'vendor': 'Acme', 'total': 5}", _SCHEMA) == {
        "vendor": "Acme",
        "total": 5,
    }
    assert deterministic_repairs() == 1


def test_rung_zero_never_counts_when_the_reply_parses_cleanly() -> None:
    reset_deterministic_repairs()
    validate_and_coerce('{"vendor": "Acme", "total": 5}', _SCHEMA)
    assert deterministic_repairs() == 0


def test_rung_zero_never_counts_a_repair_that_fails_the_schema() -> None:
    # parses after repair but violates the schema → the paid rung proceeds, uncounted
    reset_deterministic_repairs()
    with pytest.raises(ItemError, match="does not match"):
        validate_and_coerce('{"vendor": "Acme", "total": "not a number",}', _SCHEMA)
    assert deterministic_repairs() == 0


def test_rung_zero_leaves_hopeless_replies_to_the_paid_rung() -> None:
    reset_deterministic_repairs()
    with pytest.raises(ItemError, match="valid JSON"):
        validate_and_coerce("I cannot do that", _SCHEMA)
    assert deterministic_repairs() == 0


def test_repair_tally_accumulates_and_resets() -> None:
    reset_deterministic_repairs()
    validate_and_coerce('{"vendor": "a", "total": 1,}', _SCHEMA)
    validate_and_coerce('{"vendor": "b", "total": 2,}', _SCHEMA)
    assert deterministic_repairs() == 2
    reset_deterministic_repairs()
    assert deterministic_repairs() == 0


# --- strict-mode compatibility (workstream 10 Task 1) -------------------------------


def test_shorthand_schema_is_not_strict_untyped_properties() -> None:
    # live-caught: strict mode demands a 'type' per property; the brace
    # D48 upgraded bare fields to typed scalar unions - shorthand now rides
    # STRICT mode, so providers can't even emit the null we used to admit
    assert is_strict_compatible(shorthand_to_schema(["vendor", "total"])) is True


def test_optional_field_is_not_strict_compatible() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
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
                "properties": {"x": {"type": "string"}, "y": {"type": "string"}},
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
                "items": {
                    "type": "object",
                    "properties": {"a": {"type": "string"}},
                    "required": [],
                },
            }
        },
        "required": ["rows"],
        "additionalProperties": False,
    }
    assert is_strict_compatible(schema) is False  # items object is open + optional


def test_bare_fields_accept_scalars_but_not_null() -> None:
    # D48: bare {product} means "any scalar" - null needs an explicit '?'
    schema = shorthand_to_schema(("vendor", "total"))
    validate_and_coerce('{"vendor": "Acme", "total": 1250}', schema)  # number still fine
    with pytest.raises(ItemError, match="does not match"):
        validate_and_coerce('{"vendor": null, "total": 1}', schema)
    with pytest.raises(ItemError, match="does not match"):
        validate_and_coerce('{"vendor": {"nested": true}, "total": 1}', schema)


def test_shape_failure_truncates_the_echoed_instance_keeps_the_pinned_phrase() -> None:
    """B4: jsonschema echoes the FAILING INSTANCE into its message — for a
    wrong-shape reply that is the whole model reply, and one skip line per chunk
    then drowns the run. The echoed blob is truncated (~160 chars) while the
    pinned 'does not match the schema' phrasing and jsonschema's own reason
    survive verbatim."""
    schema = shorthand_to_schema(("vendor",))
    blob = "z" * 500
    reply = json.dumps({"vendor": {"buried": blob}})
    with pytest.raises(ItemError) as excinfo:
        validate_and_coerce(reply, schema)
    message = str(excinfo.value)
    assert "does not match the schema" in message  # the golden-pinned phrase, untouched
    assert "is not of type" in message  # jsonschema's own reason (the message tail) survives
    assert blob not in message  # the giant instance blob is gone
    assert "… (+" in message and "chars)" in message  # the truncation marker
    assert len(message) < 300  # bounded, not the whole reply


def test_short_shape_failure_is_left_intact() -> None:
    """A small instance is not truncated — nothing to collapse, so no marker."""
    schema = shorthand_to_schema(("vendor",))
    with pytest.raises(ItemError) as excinfo:
        validate_and_coerce('{"vendor": {"a": 1}}', schema)
    message = str(excinfo.value)
    assert "does not match the schema" in message
    assert "… (+" not in message  # nothing was truncated


def test_nullable_bare_field_admits_null() -> None:
    schema = shorthand_to_schema(("vendor",), nullable=frozenset({"vendor"}))
    assert validate_and_coerce('{"vendor": null}', schema) == {"vendor": None}


# --- example_instance (smartpipe schema --example) -----------------------------


def _validates(instance: object, schema: dict[str, object]) -> bool:
    import jsonschema

    try:
        jsonschema.validate(instance, schema)
    except jsonschema.ValidationError:
        return False
    return True


def test_example_instance_covers_the_dsl_vocabulary() -> None:
    from smartpipe.engine.schema import example_instance

    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "vendor": {"type": "string", "minLength": 6, "maxLength": 10},
            "total": {"type": "number", "minimum": 3},
            "count": {"type": "integer", "maximum": -2},
            "paid": {"type": "boolean"},
            "status": {"enum": ["todo", "done"]},
            "tags": {"type": "array", "items": {"type": "string"}},
            "note": {"type": ["string", "null"]},
        },
        "required": ["vendor", "total", "count", "paid", "status", "tags", "note"],
        "additionalProperties": False,
    }
    example = example_instance(schema)
    assert _validates(example, schema)
    assert example == {
        "vendor": "xxxxxx",  # padded to minLength
        "total": 3,  # sits on the minimum
        "count": -2,  # pulled under the maximum
        "paid": True,
        "status": "todo",  # the first enum value
        "tags": ["text"],
        "note": "text",  # the non-null side of a nullable union
    }


def test_example_instance_is_deterministic() -> None:
    from smartpipe.engine.schema import example_instance, shorthand_to_schema

    schema = shorthand_to_schema(("vendor", "total"))
    assert example_instance(schema) == example_instance(schema)
    assert _validates(example_instance(schema), schema)


def test_example_instance_handles_bare_and_edge_schemas() -> None:
    from smartpipe.engine.schema import BARE_PROPERTY, example_instance

    assert example_instance(dict(BARE_PROPERTY)) == "text"  # scalar-or-list picks a scalar
    assert example_instance({"type": "array"}) == []  # no items to imitate
    assert example_instance({"type": "null"}) is None
    assert example_instance({"type": ["null"]}) is None
    assert example_instance({"type": "string", "maxLength": 2}) == "te"
    assert example_instance({}) is None  # unknown vocabulary: honest null


# --- date/datetime canonicalization (ledger item 56) --------------------------------

_DATED = {
    "type": "object",
    "properties": {
        "due": {"type": "string", "format": "date"},
        "ts": {"type": "string", "format": "date-time"},
    },
    "required": ["due", "ts"],
    "additionalProperties": False,
}


def test_temporal_fields_canonicalize_to_iso() -> None:
    reply = '{"due": "Jan 5, 2026", "ts": "2026/01/05 9:00"}'
    assert validate_and_coerce(reply, _DATED) == {
        "due": "2026-01-05",
        "ts": "2026-01-05T09:00:00",
    }


def test_temporal_iso_values_pass_through_canonical() -> None:
    reply = '{"due": "2026-01-05T10:00:00Z", "ts": "2026-01-05T10:00:00+05:30"}'
    assert validate_and_coerce(reply, _DATED) == {
        "due": "2026-01-05",  # a datetime answering a date ask keeps its day
        "ts": "2026-01-05T10:00:00+05:30",  # explicit offset preserved, never invented
    }


def test_naive_datetime_stays_naive() -> None:
    reply = '{"due": "2026-01-05", "ts": "2026-01-05T10:00:00"}'
    assert validate_and_coerce(reply, _DATED)["ts"] == "2026-01-05T10:00:00"


def test_unparseable_date_is_an_item_error_naming_the_field() -> None:
    with pytest.raises(ItemError, match=r"'due'.*YYYY-MM-DD"):
        validate_and_coerce('{"due": "next Tuesday", "ts": "2026-01-05T10:00:00"}', _DATED)


def test_unparseable_datetime_is_an_item_error_naming_the_field() -> None:
    with pytest.raises(ItemError, match=r"'ts'.*ISO-8601"):
        validate_and_coerce('{"due": "2026-01-05", "ts": "mid-afternoon"}', _DATED)


def test_non_string_date_fails_type_validation() -> None:
    with pytest.raises(ItemError, match="does not match"):
        validate_and_coerce('{"due": 20260105, "ts": "2026-01-05T10:00:00"}', _DATED)


def test_nullable_date_admits_null() -> None:
    schema = {
        "type": "object",
        "properties": {"due": {"type": ["string", "null"], "format": "date"}},
        "required": ["due"],
        "additionalProperties": False,
    }
    assert validate_and_coerce('{"due": null}', schema) == {"due": None}
    assert validate_and_coerce('{"due": "Jan 5, 2026"}', schema) == {"due": "2026-01-05"}


def test_ambiguous_date_notes_the_month_first_reading() -> None:
    messages: list[str] = []
    result = validate_and_coerce(
        '{"due": "01/02/2026", "ts": "2026-01-05T10:00:00"}', _DATED, note=messages.append
    )
    assert result["due"] == "2026-01-02"
    assert messages == ["field 'due': ambiguous date '01/02/2026' read month-first as 2026-01-02"]


def test_unambiguous_dates_note_nothing() -> None:
    messages: list[str] = []
    validate_and_coerce(
        '{"due": "15/01/2026", "ts": "2026-01-05T10:00:00"}', _DATED, note=messages.append
    )
    assert messages == []


def test_ambiguity_without_a_note_callback_is_silent() -> None:
    assert (
        validate_and_coerce('{"due": "01/02/2026", "ts": "2026-01-05"}', _DATED)["due"]
        == "2026-01-02"
    )


# --- the open-world check artifact (item 46) -----------------------------------------


def test_open_check_schema_ignores_undeclared_fields() -> None:
    from smartpipe.engine.schema import open_check_schema, shorthand_to_schema

    schema = shorthand_to_schema(["vendor"])
    opened = open_check_schema(schema)
    assert opened["additionalProperties"] is True
    assert opened["required"] == ["vendor"]
    assert schema["additionalProperties"] is False  # the request artifact is untouched


def test_open_check_schema_lets_nullable_fields_be_absent() -> None:
    from smartpipe.engine.schema import open_check_schema, shorthand_to_schema

    schema = shorthand_to_schema(["vendor", "note"], nullable=frozenset({"note"}))
    opened = open_check_schema(schema)
    assert opened["required"] == ["vendor"]  # a ? field may be absent, not just null


def test_open_check_schema_keeps_per_field_schemas_verbatim() -> None:
    from smartpipe.engine.schema import open_check_schema
    from smartpipe.engine.schema_dsl import dsl_to_schema

    schema = dsl_to_schema("total number >= 0")
    opened = open_check_schema(schema)
    assert opened["properties"] == schema["properties"]


def test_open_check_schema_handles_schemas_without_properties() -> None:
    from smartpipe.engine.schema import open_check_schema

    assert open_check_schema({"type": "object"})["additionalProperties"] is True


def test_open_check_schema_keeps_required_names_without_a_property() -> None:
    from smartpipe.engine.schema import open_check_schema

    # a hand-written schema may require a name it never describes - not nullable
    schema: dict[str, object] = {"type": "object", "required": ["ghost"]}
    assert open_check_schema(schema)["required"] == ["ghost"]
