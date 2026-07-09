from __future__ import annotations

import pytest

from smartpipe.core.errors import ItemError, UsageFault
from smartpipe.engine.prompts import (
    FILTER_JUDGE_SYSTEM,
    JUDGE_SCHEMA,
    build_filter_request,
    interpolate_fields,
    parse_prompt,
    reject_comma_groups,
)

# --- comma-group rejection ----------------------------------------------------


def test_single_field_groups_are_allowed() -> None:
    reject_comma_groups(parse_prompt("{priority} vs {description}"))  # no raise


def test_comma_group_is_rejected_with_the_map_only_message() -> None:
    with pytest.raises(UsageFault) as excinfo:
        reject_comma_groups(parse_prompt("{priority, description}"))
    message = str(excinfo.value)
    assert "{priority, description}" in message
    assert "map" in message


# --- interpolation ------------------------------------------------------------


def test_no_braces_returns_condition_unchanged() -> None:
    tokens = parse_prompt("reviewer is sarcastic")
    assert interpolate_fields(tokens, None) == "reviewer is sarcastic"


def test_substitutes_scalar_fields() -> None:
    tokens = parse_prompt("{priority} for {title}")
    data = {"priority": "high", "title": "Login bug"}
    assert interpolate_fields(tokens, data) == "high for Login bug"


def test_renders_json_scalars_plainly() -> None:
    tokens = parse_prompt("n={count} ok={done} note={note}")
    data = {"count": 42, "done": True, "note": None}
    assert interpolate_fields(tokens, data) == "n=42 ok=true note=null"


def test_renders_objects_as_compact_json() -> None:
    tokens = parse_prompt("meta={meta}")
    assert interpolate_fields(tokens, {"meta": {"a": 1}}) == 'meta={"a":1}'


def test_missing_field_is_item_error_naming_available_fields() -> None:
    tokens = parse_prompt("{priority}")
    with pytest.raises(ItemError) as excinfo:
        interpolate_fields(tokens, {"id": 1, "title": "x"})
    message = str(excinfo.value)
    assert "no field 'priority'" in message
    assert "id, title" in message


def test_braces_on_non_json_item_is_item_error() -> None:
    tokens = parse_prompt("{priority}")
    with pytest.raises(ItemError, match="isn't JSON"):
        interpolate_fields(tokens, None)


# --- request building ---------------------------------------------------------


def test_build_filter_request_shape() -> None:
    # the payload arrives pre-rendered as an <input> block (item 57)
    request = build_filter_request("reviewer is sarcastic", "<input>\nThis is FINE.\n</input>")
    assert request.system == FILTER_JUDGE_SYSTEM
    assert request.json_schema == JUDGE_SCHEMA
    assert request.user == "Condition: reviewer is sarcastic\n\n<input>\nThis is FINE.\n</input>"


def test_judge_schema_is_a_boolean_match() -> None:
    assert JUDGE_SCHEMA == {
        "type": "object",
        "properties": {"match": {"type": "boolean"}},
        "required": ["match"],
        "additionalProperties": False,
    }
