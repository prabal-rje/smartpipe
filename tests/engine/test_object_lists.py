"""Object lists in braces (ledger item 16): ``{triples {subject, relation,
object}[]}`` compiles to an array of typed objects — one level deep, ever."""

from __future__ import annotations

import pytest

from smartpipe.core.errors import ItemError, UsageFault
from smartpipe.engine.prompts import (
    BraceToken,
    parse_prompt,
    plan_map,
    render,
    to_instruction,
)
from smartpipe.engine.schema import (
    BARE_PROPERTY,
    is_strict_compatible,
    validate_and_coerce,
)

CEILING = "object lists nest one level deep - flatten the inner structure or extract in two passes"


def _schema(prompt: str) -> dict[str, object]:
    plan = plan_map(parse_prompt(prompt, allow_descriptions=True), schema=None)
    assert plan.schema is not None
    return dict(plan.schema)


def _properties(prompt: str) -> dict[str, object]:
    return _record(_schema(prompt)["properties"])


def _record(value: object) -> dict[str, object]:
    from smartpipe.core.jsontools import as_record

    record = as_record(value)
    assert record is not None
    return dict(record)


def _inner_properties(prop: object) -> dict[str, object]:
    return _record(_record(_record(prop)["items"])["properties"])


# --- compilation: the pinned schemas ----------------------------------------------


def test_bare_object_list_compiles_to_the_pinned_schema() -> None:
    assert _schema("Extract {triples {subject, relation, object}[]}") == {
        "type": "object",
        "properties": {
            "triples": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "subject": dict(BARE_PROPERTY),
                        "relation": dict(BARE_PROPERTY),
                        "object": dict(BARE_PROPERTY),
                    },
                    "required": ["subject", "relation", "object"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["triples"],
        "additionalProperties": False,
    }


def test_inner_fields_speak_the_full_type_vocabulary() -> None:
    assert _properties("List {events {name string, when date, severity enum(low, high)}[]}") == {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "when": {"type": "string", "format": "date"},
                    "severity": {"enum": ["low", "high"]},
                },
                "required": ["name", "when", "severity"],
                "additionalProperties": False,
            },
        }
    }


def test_guidance_rides_the_outer_field_and_inner_fields_alike() -> None:
    properties = _properties(
        "List {events {name string: the event name, when date}[]: every notable event}"
    )
    events = _record(properties["events"])
    assert events["description"] == "every notable event"
    name = _record(_inner_properties(properties["events"])["name"])
    assert name["description"] == "the event name"


def test_object_lists_sit_beside_ordinary_fields() -> None:
    properties = _properties("Extract {summary string, events {name}[], total number}")
    assert set(properties) == {"summary", "events", "total"}
    assert properties["summary"] == {"type": "string"}
    events = _record(properties["events"])
    assert events["type"] == "array"


def test_nullable_and_typed_inner_fields_compile() -> None:
    properties = _properties("Extract {rows {label string?, n integer}[]}")
    inner = _inner_properties(properties["rows"])
    assert inner["label"] == {"type": ["string", "null"]}
    assert inner["n"] == {"type": "integer"}


def test_a_bare_object_list_stays_strict_compatible() -> None:
    assert is_strict_compatible(_schema("Extract {triples {subject, relation, object}[]}"))


def test_instruction_names_the_outer_field_only() -> None:
    tokens = parse_prompt(
        "List {events {name string: the event name, when date}[]: every notable event}",
        allow_descriptions=True,
    )
    assert to_instruction(tokens) == "List events (every notable event)"


def test_render_round_trips_object_lists() -> None:
    text = "Extract {triples {subject, relation string: the verb, object}[]: all triples}"
    tokens = parse_prompt(text, allow_descriptions=True)
    assert render(tokens) == text
    assert parse_prompt(render(tokens), allow_descriptions=True) == tokens


def test_the_same_object_list_twice_dedupes() -> None:
    schema = _schema("Compare {rows {a}[]} with {rows {a}[]}")
    from smartpipe.core.jsontools import as_items

    assert list(as_items(schema["required"]) or ()) == ["rows"]


# --- rejections --------------------------------------------------------------------


def test_the_one_level_ceiling_is_a_usage_fault() -> None:
    with pytest.raises(UsageFault, match="nest one level deep"):
        parse_prompt("Extract {a {b {c}[]}[]}", allow_descriptions=True)


def test_the_ceiling_fault_says_the_pinned_sentence() -> None:
    with pytest.raises(UsageFault) as excinfo:
        parse_prompt("Extract {a {b {c}[]}[]}", allow_descriptions=True)
    assert CEILING in str(excinfo.value)


def test_an_inner_group_without_brackets_is_refused() -> None:
    with pytest.raises(UsageFault, match=r"must be a list"):
        parse_prompt("Extract {a {b, c}}", allow_descriptions=True)


def test_a_space_before_the_brackets_is_refused() -> None:
    with pytest.raises(UsageFault, match=r"must be a list"):
        parse_prompt("Extract {a {b} []}", allow_descriptions=True)


def test_junk_after_the_brackets_is_refused() -> None:
    with pytest.raises(UsageFault, match="after the object list"):
        parse_prompt("Extract {a {b}[]?}", allow_descriptions=True)


def test_an_empty_outer_description_is_refused() -> None:
    with pytest.raises(UsageFault, match="empty description"):
        parse_prompt("Extract {a {b}[]:  }", allow_descriptions=True)


def test_a_nameless_object_list_is_an_invalid_group() -> None:
    with pytest.raises(UsageFault, match="invalid field group"):
        parse_prompt("Extract { {a}[]}", allow_descriptions=True)


def test_a_non_identifier_name_is_an_invalid_group() -> None:
    with pytest.raises(UsageFault, match="invalid field group"):
        parse_prompt("Extract {two words {a}[]}", allow_descriptions=True)


def test_duplicate_inner_names_are_refused() -> None:
    with pytest.raises(UsageFault, match="named twice inside"):
        parse_prompt("Extract {a {x, x}[]}", allow_descriptions=True)


def test_an_empty_inner_group_is_an_invalid_group() -> None:
    with pytest.raises(UsageFault, match="invalid field group"):
        parse_prompt("Extract {a {}[]}", allow_descriptions=True)


def test_an_unclosed_inner_group_is_loud() -> None:
    with pytest.raises(UsageFault, match="unclosed"):
        parse_prompt("Extract {a {b", allow_descriptions=True)


def test_object_lists_stay_invalid_outside_map() -> None:
    # filter/reduce braces reference input fields — nesting means nothing there
    with pytest.raises(UsageFault, match="invalid field group"):
        parse_prompt("keep if {a {b}[]} is true")


def test_typing_a_field_scalar_then_object_list_is_the_typed_twice_fault() -> None:
    with pytest.raises(UsageFault, match="typed twice"):
        plan_map(
            parse_prompt("Compare {a string} and {a {b}[]}", allow_descriptions=True),
            schema=None,
        )


def test_literal_braces_still_escape_next_to_object_lists() -> None:
    tokens = parse_prompt("Return {{json}} with {rows {a}[]}", allow_descriptions=True)
    brace = next(token for token in tokens if isinstance(token, BraceToken))
    assert brace.fields == ("rows",)


# --- the downstream pipeline: coercion per inner record ------------------------------


def _events_schema() -> dict[str, object]:
    return _schema("List {events {name string, when date, n number}[]}")


def test_inner_records_coerce_dates_and_numbers() -> None:
    reply = (
        '{"events": [{"name": "kickoff", "when": "Jan 15, 2026", "n": "3"},'
        ' {"name": "launch", "when": "2026-02-01", "n": 7}]}'
    )
    record = validate_and_coerce(reply, _events_schema())
    assert record == {
        "events": [
            {"name": "kickoff", "when": "2026-01-15", "n": 3.0},
            {"name": "launch", "when": "2026-02-01", "n": 7},
        ]
    }


def test_an_unreadable_inner_date_is_an_item_error_naming_the_field() -> None:
    reply = '{"events": [{"name": "kickoff", "when": "sometime soon", "n": 1}]}'
    with pytest.raises(ItemError, match="'when' is not a date"):
        validate_and_coerce(reply, _events_schema())


def test_ambiguous_inner_dates_still_reach_the_note_callback() -> None:
    notes: list[str] = []
    reply = '{"events": [{"name": "kickoff", "when": "01/02/2026", "n": 1}]}'
    record = validate_and_coerce(reply, _events_schema(), note=notes.append)
    assert record["events"] == [{"name": "kickoff", "when": "2026-01-02", "n": 1}]  # month-first
    assert notes and "ambiguous" in notes[0]


def test_extra_inner_fields_are_dropped_like_top_level_ones() -> None:
    reply = '{"events": [{"name": "kickoff", "when": "2026-01-15", "n": 1, "extra": true}]}'
    record = validate_and_coerce(reply, _events_schema())
    assert record["events"] == [{"name": "kickoff", "when": "2026-01-15", "n": 1}]


def test_non_record_elements_fail_validation_not_crash() -> None:
    reply = '{"events": ["not an object"]}'
    with pytest.raises(ItemError, match="does not match the schema"):
        validate_and_coerce(reply, _events_schema())


def test_conflicting_enum_and_object_list_spell_both_types() -> None:
    with pytest.raises(UsageFault) as excinfo:
        plan_map(
            parse_prompt("Compare {a enum(x, y)} and {a {b}[]}", allow_descriptions=True),
            schema=None,
        )
    message = str(excinfo.value)
    assert "enum(x, y)" in message
    assert "object[]" in message


def test_hand_written_item_schemas_tolerate_mixed_lists() -> None:
    """A user --schema may leave items untyped; non-record elements pass
    through untouched while records still coerce."""
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "items": {"properties": {"when": {"type": "string", "format": "date"}}},
            }
        },
    }
    reply = '{"events": ["free text", {"when": "Jan 15, 2026"}]}'
    record = validate_and_coerce(reply, schema)
    assert record["events"] == ["free text", {"when": "2026-01-15"}]
