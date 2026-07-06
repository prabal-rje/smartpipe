"""Structured-output schemas (plan/decisions.md D07): shorthand synthesis,
JSON-Schema loading, and validate-with-light-coercion of a model reply.

``validate_and_coerce`` raises ``ItemError`` on any failure, with a message that
names the problem — the ``map`` verb feeds that message back to the model as the
single repair retry, so the message is repair context, not just a log line.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from sempipe.core.errors import ItemError, SetupFault
from sempipe.core.jsontools import as_items, as_record, as_str

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

__all__ = [
    "is_strict_compatible",
    "load_schema",
    "parse_schema_draft",
    "shorthand_to_schema",
    "validate_and_coerce",
]

_FENCE = re.compile(r"^```[A-Za-z0-9]*\n?|\n?```$")


def shorthand_to_schema(
    fields: Sequence[str], *, descriptions: Mapping[str, str] | None = None
) -> dict[str, object]:
    """Turn ``{vendor, total}`` fields into a strict JSON Schema; the model infers
    value types (permissive ``{}`` per property). Rung-2 descriptions (D22) ride
    each property as guidance — they never affect strict-compatibility."""
    notes = descriptions or {}
    properties: dict[str, object] = {
        field: ({"description": notes[field]} if field in notes else {}) for field in fields
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(fields),
        "additionalProperties": False,
    }


def is_strict_compatible(schema: Mapping[str, object]) -> bool:
    """Would OpenAI/Mistral ``strict: true`` json_schema mode accept this schema?

    Strict mode demands, at every object layer: every property listed in
    ``required`` and ``additionalProperties: false``. Brace-shorthand schemas
    qualify by construction; a user ``--schema`` with optional fields does not —
    claiming strict for it draws a 400 and skips items for the wrong reason.
    """
    if _looks_like_object(schema):
        if schema.get("additionalProperties") is not False:
            return False
        properties = as_record(schema.get("properties")) or {}
        required = as_items(schema.get("required")) or ()
        required_names = {name for name in required if isinstance(name, str)}
        if not set(properties) <= required_names:
            return False
        for value in properties.values():
            child = as_record(value)
            # live-caught (2026-07-05): strict mode also demands a 'type' per
            # property — an untyped {} (the brace shorthand) draws the same 400
            if child is None or ("type" not in child and "enum" not in child):
                return False
            if not is_strict_compatible(child):
                return False
    items = as_record(schema.get("items"))
    return items is None or is_strict_compatible(items)


def _looks_like_object(schema: Mapping[str, object]) -> bool:
    return schema.get("type") == "object" or as_record(schema.get("properties")) is not None


def load_schema(path: Path) -> dict[str, object]:
    if not path.exists():
        raise SetupFault(
            f"error: no schema file at {path}\n  Check the --schema path and try again."
        )
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SetupFault(
            f"error: {path} isn't valid JSON\n  {exc}\n  A --schema file must be a JSON Schema."
        ) from exc
    record = as_record(loaded)
    if record is None:
        raise SetupFault(
            f"error: {path} isn't a JSON Schema\n  The top level must be a JSON object."
        )
    return dict(record)


def parse_schema_draft(reply: str) -> dict[str, object]:
    """Rung 4 (D22): a model-drafted schema, validated against the JSON-Schema
    meta-schema AND our loader rules — ``ItemError`` names what's wrong (repair
    context). An invalid draft must never reach stdout."""
    import jsonschema

    record = as_record(_extract_json(reply))  # ItemError when there's no JSON at all
    if record is None:
        raise ItemError("the draft isn't a JSON object")
    candidate = dict(record)
    try:
        jsonschema.Draft202012Validator.check_schema(candidate)
    except jsonschema.SchemaError as exc:
        raise ItemError(f"not a valid JSON Schema: {exc.message}") from exc
    return candidate


def validate_and_coerce(reply: str, schema: Mapping[str, object]) -> dict[str, object]:
    import jsonschema  # function-local: --help must not pay for the validator stack

    record = as_record(_extract_json(reply))
    if record is None:
        raise ItemError("model returned JSON but not an object")
    coerced = _coerce(record, schema)
    trimmed = _drop_extra(coerced, schema)
    try:
        jsonschema.validate(trimmed, dict(schema))
    except jsonschema.ValidationError as exc:
        raise ItemError(f"output does not match the schema: {exc.message}") from exc
    return trimmed


def _extract_json(reply: str) -> object:
    text = reply.strip()
    if text.startswith("```"):
        text = _FENCE.sub("", text).strip()
    for candidate in _json_candidates(text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ItemError("model did not return valid JSON")


def _json_candidates(text: str) -> tuple[str, ...]:
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return (text, text[start : end + 1])
    return (text,)


def _coerce(record: Mapping[str, object], schema: Mapping[str, object]) -> dict[str, object]:
    properties = as_record(schema.get("properties")) or {}
    return {
        key: _coerce_scalar(value, as_record(properties.get(key))) for key, value in record.items()
    }


def _coerce_scalar(value: object, prop_schema: Mapping[str, object] | None) -> object:
    if prop_schema is None or not isinstance(value, str):
        return value
    text = value.strip()
    match as_str(prop_schema.get("type")):
        case "integer":
            return int(text) if _is_int(text) else value
        case "number":
            return float(text) if _is_float(text) else value
        case "boolean":
            return _as_bool(text, fallback=value)
        case "null":
            return None if text == "null" else value
        case _:
            return value


def _drop_extra(record: dict[str, object], schema: Mapping[str, object]) -> dict[str, object]:
    if schema.get("additionalProperties") is not False:
        return record
    allowed = as_record(schema.get("properties")) or {}
    return {key: value for key, value in record.items() if key in allowed}


def _is_int(text: str) -> bool:
    return bool(re.fullmatch(r"[+-]?\d+", text))


def _is_float(text: str) -> bool:
    return bool(re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", text))


def _as_bool(text: str, *, fallback: object) -> object:
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return fallback
