"""Join predicate plumbing (D21): side-qualified braces, pair interpolation,
and the judge request — the quality surface, golden where it meets the model."""

from __future__ import annotations

import pytest

from sempipe.core.errors import ItemError, UsageFault
from sempipe.engine.prompts import (
    JOIN_JUDGE_SYSTEM,
    JUDGE_SCHEMA,
    build_judge_request,
    interpolate_join,
    parse_join_predicate,
)
from sempipe.io.items import item_from_line


def _json_item(payload: str, index: int = 0):
    return item_from_line(payload + "\n", index)


# --- parsing ---------------------------------------------------------------------


def test_valid_predicate_parses_both_sides() -> None:
    tokens = parse_join_predicate("ticket {left.text} concerns {right.name}")
    assert len(tokens) == 4  # text, brace, text, brace


def test_bare_brace_is_the_ambiguity_screen() -> None:
    with pytest.raises(UsageFault) as excinfo:
        parse_join_predicate("ticket {text} concerns {right.name}")
    message = str(excinfo.value)
    assert message.startswith("{text} is ambiguous in join — say {left.text} or {right.text}")
    assert "--right products.jsonl" in message  # the example rides along


def test_unknown_side_is_rejected() -> None:
    with pytest.raises(UsageFault, match="side must be left or right"):
        parse_join_predicate("x {middle.field} y {right.name}")


def test_comma_groups_are_rejected() -> None:
    with pytest.raises(UsageFault, match="comma-separated braces"):
        parse_join_predicate("{left.a, left.b} vs {right.c}")


def test_predicate_must_mention_both_sides() -> None:
    with pytest.raises(UsageFault, match="never mentions the right side"):
        parse_join_predicate("just {left.text} alone")
    with pytest.raises(UsageFault, match="never mentions the left side"):
        parse_join_predicate("just {right.text} alone")


# --- interpolation -----------------------------------------------------------------


def test_interpolates_fields_from_both_sides() -> None:
    tokens = parse_join_predicate("ticket {left.body} concerns {right.name}")
    left = _json_item('{"body": "printer is on fire"}')
    right = _json_item('{"name": "LaserJet 9"}', 1)
    assert interpolate_join(tokens, left, right) == "ticket printer is on fire concerns LaserJet 9"


def test_dot_text_falls_back_to_the_raw_text() -> None:
    tokens = parse_join_predicate("does {left.text} match {right.text}?")
    left = item_from_line("plain left line\n", 0)
    right = _json_item('{"name": "x"}', 1)  # JSON without a "text" field
    rendered = interpolate_join(tokens, left, right)
    assert "plain left line" in rendered
    assert '{"name": "x"}' in rendered  # the whole right item as text


def test_missing_field_is_a_pair_skip_naming_side_and_fields() -> None:
    tokens = parse_join_predicate("x {left.body} y {right.name}")
    left = _json_item('{"other": 1}')
    right = _json_item('{"name": "x"}', 1)
    with pytest.raises(ItemError, match="left has no field 'body'; it has: other"):
        interpolate_join(tokens, left, right)


# --- the judge request ----------------------------------------------------------------


def test_judge_request_is_pinned() -> None:
    tokens = parse_join_predicate("ticket {left.text} concerns {right.name}")
    left = item_from_line("printer smoke\n", 0)
    right = _json_item('{"name": "LaserJet 9"}', 1)
    request = build_judge_request(tokens, left, right)
    assert request.system == JOIN_JUDGE_SYSTEM
    assert request.user == (
        "Statement about a pair of items:\n"
        "ticket printer smoke concerns LaserJet 9\n\n"
        "Is the statement true?"
    )
    assert request.json_schema == JUDGE_SCHEMA
    assert request.max_tokens == 64
