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
from sempipe.core.jsontools import as_record, as_str

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

__all__ = ["load_schema", "shorthand_to_schema", "validate_and_coerce"]

_FENCE = re.compile(r"^```[A-Za-z0-9]*\n?|\n?```$")


def shorthand_to_schema(fields: Sequence[str]) -> dict[str, object]:
    """Turn ``{vendor, total}`` fields into a strict JSON Schema; the model
    infers value types (permissive ``{}`` per property)."""
    return {
        "type": "object",
        "properties": {field: {} for field in fields},
        "required": list(fields),
        "additionalProperties": False,
    }


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
