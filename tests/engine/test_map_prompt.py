from __future__ import annotations

from sempipe.engine.prompts import (
    MAP_JSON_SYSTEM,
    MAP_PLAIN_SYSTEM,
    build_map_request,
    build_repair_request,
    parse_prompt,
    plan_map,
    to_instruction,
)
from sempipe.models.base import CompletionRequest

# --- instruction rendering ----------------------------------------------------


def test_to_instruction_renders_braces_as_field_lists() -> None:
    assert to_instruction(parse_prompt("translate to French")) == "translate to French"
    assert to_instruction(parse_prompt("Extract {vendor, date}")) == "Extract vendor, date"
    assert to_instruction(parse_prompt("Get {total}")) == "Get total"
    assert to_instruction(parse_prompt("keep {{brace}}")) == "keep {brace}"


# --- planning -----------------------------------------------------------------


def test_plain_prompt_plans_plain_mode() -> None:
    plan = plan_map(parse_prompt("translate to French"), schema=None)
    assert plan.mode == "plain"
    assert plan.schema is None
    assert plan.system == MAP_PLAIN_SYSTEM


def test_shorthand_plans_structured_with_synthesized_schema() -> None:
    plan = plan_map(parse_prompt("Extract {vendor, total}"), schema=None)
    assert plan.mode == "structured"
    assert plan.schema == {
        "type": "object",
        "properties": {"vendor": {}, "total": {}},
        "required": ["vendor", "total"],
        "additionalProperties": False,
    }
    assert plan.system == MAP_JSON_SYSTEM


def test_explicit_schema_takes_precedence_over_braces() -> None:
    external = {"type": "object", "properties": {"x": {"type": "string"}}}
    plan = plan_map(parse_prompt("Extract {vendor}"), schema=external)
    assert plan.mode == "structured"
    assert plan.schema == external


# --- request building ---------------------------------------------------------


def test_build_plain_request() -> None:
    plan = plan_map(parse_prompt("translate to French"), schema=None)
    request = build_map_request(plan, "translate to French", "hello")
    assert request == CompletionRequest(
        system=MAP_PLAIN_SYSTEM,
        user="translate to French\n\nhello",
        json_schema=None,
        max_tokens=4096,
    )


def test_build_structured_request_attaches_schema() -> None:
    plan = plan_map(parse_prompt("Extract {vendor}"), schema=None)
    request = build_map_request(plan, "Extract vendor", "Acme invoice")
    assert request.json_schema == plan.schema
    assert request.system == MAP_JSON_SYSTEM
    assert request.user == "Extract vendor\n\nAcme invoice"
    assert request.max_tokens == 8192


# --- repair -------------------------------------------------------------------


def test_repair_request_includes_the_bad_reply_and_error() -> None:
    plan = plan_map(parse_prompt("Extract {vendor}"), schema=None)
    original = build_map_request(plan, "Extract vendor", "Acme")
    repair = build_repair_request(original, bad_reply="not json", error="did not return valid JSON")
    assert repair.system == original.system
    assert repair.json_schema == original.json_schema
    assert "not json" in repair.user
    assert "did not return valid JSON" in repair.user
    assert original.user in repair.user  # keeps the original ask
