"""Structured-output schemas (plan/decisions.md D07): shorthand synthesis,
JSON-Schema loading, and validate-with-light-coercion of a model reply.

``validate_and_coerce`` raises ``ItemError`` on any failure, with a message that
names the problem — the ``map`` verb feeds that message back to the model as the
single repair retry, so the message is repair context, not just a log line.
Before that paid retry, a reply that doesn't even parse gets rung 0 (item 58):
``engine/repair.repair_json``, free and deterministic; a success is tallied so
the container can disclose it once per run.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from smartpipe.core.errors import ItemError, SetupFault
from smartpipe.core.jsontools import as_items, as_record, as_str
from smartpipe.engine.temporal import CoercedTemporal, coerce_date, coerce_datetime

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    import jsonschema

__all__ = [
    "BARE_PROPERTY",
    "deterministic_repairs",
    "example_instance",
    "is_strict_compatible",
    "load_schema",
    "open_check_schema",
    "parse_schema_draft",
    "reset_deterministic_repairs",
    "shorthand_to_schema",
    "validate_and_coerce",
]

_FENCE = re.compile(r"^```[A-Za-z0-9]*\n?|\n?```$")


_SCALARS: tuple[str, ...] = ("string", "number", "integer", "boolean")
# D48: what a BARE braces field admits - any scalar, or a list of scalars
# (--explode workflows need lists); never null, never objects, never nesting
BARE_PROPERTY: dict[str, object] = {
    "type": [*_SCALARS, "array"],
    "items": {"type": list(_SCALARS)},
}


def shorthand_to_schema(
    fields: Sequence[str],
    *,
    descriptions: Mapping[str, str] | None = None,
    types: Mapping[str, Mapping[str, object]] | None = None,
    nullable: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Turn ``{vendor, total}`` fields into a strict JSON Schema. Inline types
    (D37) and rung-2 descriptions (D22) ride each property. Bare fields mean
    "any scalar" — never null, never nested (D48): the model picks string vs
    number sensibly, but absence must be declared with ``?``."""
    notes = descriptions or {}
    typed = types or {}

    def _property(field: str) -> dict[str, object]:
        prop: dict[str, object] = dict(typed.get(field, {}))
        if not prop:
            prop = dict(BARE_PROPERTY)
            if field in nullable:
                prop["type"] = [*_SCALARS, "array", "null"]
        if field in notes:
            prop["description"] = notes[field]
        return prop

    properties: dict[str, object] = {field: _property(field) for field in fields}
    return {
        "type": "object",
        "properties": properties,
        "required": list(fields),
        "additionalProperties": False,
    }


def open_check_schema(schema: Mapping[str, object]) -> dict[str, object]:
    """The CHECK artifact (item 46): open-world validation of the declared
    fields only. Undeclared fields — user data and the ``__`` spine alike —
    are ignored (``additionalProperties: true``), and a nullable ``?`` field
    may be absent, not just null (dropped from ``required``). Per-field
    schemas ride verbatim. The extraction-time REQUEST schema is a different
    artifact and stays closed — strict-mode wires demand it."""
    opened = dict(schema)
    opened["additionalProperties"] = True
    properties = as_record(schema.get("properties")) or {}
    required = as_items(schema.get("required")) or ()
    opened["required"] = [
        name
        for name in required
        if isinstance(name, str) and not _admits_null(as_record(properties.get(name)))
    ]
    return opened


def _admits_null(prop: Mapping[str, object] | None) -> bool:
    """Whether the property was marked ``?`` (its type union includes null)."""
    if prop is None:
        return False
    union = as_items(prop.get("type"))
    return union is not None and "null" in union


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


def example_instance(schema: Mapping[str, object]) -> object:
    """One deterministic instance that validates (``smartpipe schema --example``).

    Covers the vocabulary our own ladder emits — objects, the primitives,
    arrays, enums, nullable unions, bounds, lengths. Anything richer falls
    back to an honest null rather than a guess.
    """
    enum = as_items(schema.get("enum"))
    if enum:
        return enum[0]
    kind: object = schema.get("type")
    union = as_items(kind)  # a list "type" is a union of bases
    if union is not None:  # a union: prefer the first non-null base
        bases = [entry for entry in union if entry != "null"]
        if not bases:
            return None
        return example_instance({**schema, "type": bases[0]})
    match kind if isinstance(kind, str) else None:
        case "object":
            properties = as_record(schema.get("properties")) or {}
            return {
                name: example_instance(as_record(prop) or {}) for name, prop in properties.items()
            }
        case "array":
            items = as_record(schema.get("items"))
            return [] if items is None else [example_instance(items)]
        case "string":
            return _example_string(schema)
        case "integer":
            return int(_example_number(schema))
        case "number":
            return _example_number(schema)
        case "boolean":
            return True
        case _:  # "null", or vocabulary we don't speak
            return None


def _example_string(schema: Mapping[str, object]) -> str:
    text = "text"
    minimum = schema.get("minLength")
    if isinstance(minimum, int) and len(text) < minimum:
        text = "x" * minimum
    maximum = schema.get("maxLength")
    if isinstance(maximum, int) and len(text) > maximum:
        text = text[:maximum]
    return text


def _example_number(schema: Mapping[str, object]) -> int | float:
    value: int | float = 0
    minimum = schema.get("minimum")
    if isinstance(minimum, int | float) and not isinstance(minimum, bool):
        value = minimum
    maximum = schema.get("maximum")
    if isinstance(maximum, int | float) and not isinstance(maximum, bool) and value > maximum:
        value = maximum
    return value


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


_repairs = 0  # run-scoped rung-0 tally — a metering-style documented exception to no-globals


def deterministic_repairs() -> int:
    """How many replies rung 0 saved this run — the container turns a non-zero
    count into the one dim per-run note next to the metering receipt."""
    return _repairs


def reset_deterministic_repairs() -> None:
    """A fresh run's tally — called where ``metering.reset()`` is called."""
    global _repairs
    _repairs = 0


def _count_repair() -> None:
    global _repairs
    _repairs += 1


def validate_and_coerce(
    reply: str,
    schema: Mapping[str, object],
    *,
    note: Callable[[str], None] | None = None,
) -> dict[str, object]:
    try:
        parsed = _extract_json(reply)
    except ItemError:
        # rung 0 (item 58): a free deterministic repair before the paid retry —
        # counted only when the repaired reply also passes the schema below
        from smartpipe.engine.repair import repair_json

        repaired = repair_json(reply)
        if repaired is None:
            raise
        result = _validated(json.loads(repaired), schema, note=note)
        _count_repair()
        return result
    return _validated(parsed, schema, note=note)


def _validated(
    parsed: object,
    schema: Mapping[str, object],
    *,
    note: Callable[[str], None] | None = None,
) -> dict[str, object]:
    import jsonschema  # function-local: --help must not pay for the validator stack

    record = as_record(parsed)
    if record is None:
        raise ItemError("model returned JSON but not an object")
    coerced = _coerce(record, schema, note)
    trimmed = _drop_extra(coerced, schema)
    try:
        jsonschema.validate(trimmed, dict(schema))
    except jsonschema.ValidationError as exc:
        raise ItemError(f"output does not match the schema: {_condensed_miss(exc)}") from exc
    _check_temporal(trimmed, schema)  # format: date/date-time — a miss reads like a type miss
    return trimmed


_INSTANCE_CHARS = 160  # of an echoed failing instance; the rest collapses to a marker


def _condensed_miss(exc: jsonschema.ValidationError) -> str:
    """jsonschema echoes the failing instance verbatim into ``exc.message`` (a
    wrong-shape reply lands the WHOLE reply there). Truncate that echoed repr —
    and only it — so the pinned 'does not match the schema' phrasing and
    jsonschema's own reason stay intact while the blob stops flooding skip lines
    (B4). A message that doesn't echo the instance is returned untouched."""
    blob = repr(exc.instance)
    if len(blob) <= _INSTANCE_CHARS or blob not in exc.message:
        return exc.message
    condensed = f"{blob[:_INSTANCE_CHARS]}… (+{len(blob) - _INSTANCE_CHARS:,} chars)"
    return exc.message.replace(blob, condensed, 1)


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


def _coerce(
    record: Mapping[str, object],
    schema: Mapping[str, object],
    note: Callable[[str], None] | None = None,
) -> dict[str, object]:
    properties = as_record(schema.get("properties")) or {}
    return {
        key: _coerce_value(value, as_record(properties.get(key)), key=key, note=note)
        for key, value in record.items()
    }


def _coerce_value(
    value: object,
    prop_schema: Mapping[str, object] | None,
    *,
    key: str,
    note: Callable[[str], None] | None,
) -> object:
    """Scalars coerce in place; an object list (item 16) coerces each inner
    record against the items schema — dates canonicalize per element."""
    if prop_schema is None:
        return value
    items = _object_items(prop_schema)
    elements = as_items(value) if items is not None else None
    if items is not None and elements is not None:
        return [
            _coerce(inner, items, note) if (inner := as_record(element)) is not None else element
            for element in elements
        ]
    return _coerce_scalar(value, prop_schema, key=key, note=note)


def _object_items(prop_schema: Mapping[str, object]) -> Mapping[str, object] | None:
    """The items schema when the property is an array of objects, else None."""
    items = as_record(prop_schema.get("items"))
    if items is not None and _looks_like_object(items):
        return items
    return None


def _coerce_scalar(
    value: object,
    prop_schema: Mapping[str, object] | None,
    *,
    key: str = "",
    note: Callable[[str], None] | None = None,
) -> object:
    if prop_schema is None or not isinstance(value, str):
        return value
    text = value.strip()
    coerced = _coerce_temporal(text, prop_schema)  # item 56: canonicalize to ISO
    if coerced is not None:
        if coerced.ambiguous and note is not None:
            note(f"field {key!r}: ambiguous date {text!r} read month-first as {coerced.canonical}")
        return coerced.canonical
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


def _coerce_temporal(text: str, prop_schema: Mapping[str, object]) -> CoercedTemporal | None:
    """The property's canonical temporal reading, or None (not temporal, or
    unreadable — ``_check_temporal`` turns unreadable into the repair message)."""
    match as_str(prop_schema.get("format")):
        case "date":
            return coerce_date(text)
        case "date-time":
            return coerce_datetime(text)
        case _:
            return None


def _check_temporal(record: Mapping[str, object], schema: Mapping[str, object]) -> None:
    """A date/datetime field the coercion could not read is a normal type miss:
    the ItemError feeds the single repair rung, then the item skips. Object
    lists (item 16) check each inner record against the items schema."""
    properties = as_record(schema.get("properties")) or {}
    for key, value in record.items():
        prop = as_record(properties.get(key))
        if prop is None:
            continue
        items = _object_items(prop)
        elements = as_items(value) if items is not None else None
        if items is not None and elements is not None:
            for element in elements:
                inner = as_record(element)
                if inner is not None:
                    _check_temporal(inner, items)
            continue
        if not isinstance(value, str):
            continue
        match as_str(prop.get("format")):
            case "date" if coerce_date(value) is None:
                raise ItemError(f"field {key!r} is not a date: {value!r} (write it as YYYY-MM-DD)")
            case "date-time" if coerce_datetime(value) is None:
                raise ItemError(
                    f"field {key!r} is not a datetime: {value!r} "
                    "(write it as ISO-8601, e.g. 2026-01-15T14:30:00)"
                )
            case _:
                pass


def _drop_extra(record: dict[str, object], schema: Mapping[str, object]) -> dict[str, object]:
    properties = as_record(schema.get("properties")) or {}
    strict = schema.get("additionalProperties") is False
    return {
        key: _drop_extra_value(value, as_record(properties.get(key)))
        for key, value in record.items()
        if not strict or key in properties
    }


def _drop_extra_value(value: object, prop_schema: Mapping[str, object] | None) -> object:
    """Object lists (item 16) trim each inner record by the items schema —
    the same drop-extra courtesy the top level gets."""
    if prop_schema is None:
        return value
    items = _object_items(prop_schema)
    elements = as_items(value) if items is not None else None
    if items is None or elements is None:
        return value
    return [
        _drop_extra(dict(inner), items) if (inner := as_record(element)) is not None else element
        for element in elements
    ]


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
