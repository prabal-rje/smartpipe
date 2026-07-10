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


# --- field paths (ledger item 63): {a.b.c} reads nested input -------------------


def test_path_group_parses_and_interpolates() -> None:
    tokens = parse_prompt("plan is {user.plan}", allow_paths=True)
    reject_comma_groups(tokens)  # a lone path is interpolation, not a comma group
    assert interpolate_fields(tokens, {"user": {"plan": "pro"}}) == "plan is pro"


def test_index_and_quoted_key_paths_interpolate() -> None:
    tokens = parse_prompt("{items[0].name} / {a['weird key']}", allow_paths=True)
    data = {"items": [{"name": "first"}], "a": {"weird key": 7}}
    assert interpolate_fields(tokens, data) == "first / 7"


def test_dotted_literal_column_wins_over_the_path() -> None:
    # THE COMPAT RULE: an exact flat key (CSV headers make these) wins
    tokens = parse_prompt("{user.plan}", allow_paths=True)
    data = {"user.plan": "the column", "user": {"plan": "nested"}}
    assert interpolate_fields(tokens, data) == "the column"


def test_path_miss_is_item_error_naming_available_fields() -> None:
    tokens = parse_prompt("{user.plan}", allow_paths=True)
    with pytest.raises(ItemError) as excinfo:
        interpolate_fields(tokens, {"id": 1, "user": {"name": "x"}})
    message = str(excinfo.value)
    assert "no field 'user.plan'" in message
    assert "id, user" in message


def test_malformed_path_is_loud_at_parse_time() -> None:
    with pytest.raises(UsageFault, match=r"a\.b\. - trailing dot"):
        parse_prompt("{a.b.}", allow_paths=True)
    with pytest.raises(UsageFault, match=r"items\[x\] - index must be a number"):
        parse_prompt("{items[x]}", allow_paths=True)


def test_plain_names_stay_byte_identical_with_paths_enabled() -> None:
    tokens = parse_prompt("{priority} for {title}", allow_paths=True)
    assert tokens == parse_prompt("{priority} for {title}")


def test_paths_stay_invalid_without_the_flag() -> None:
    # join and other callers keep today's grammar untouched
    with pytest.raises(UsageFault, match="invalid field group"):
        parse_prompt("{user.plan.tier}")


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
