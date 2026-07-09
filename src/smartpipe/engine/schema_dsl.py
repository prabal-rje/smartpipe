"""Rung 3 of the schema ladder (D22): a deterministic mini-DSL → JSON Schema.

``vendor string; total number >= 0; status enum(paid, unpaid)`` — parsed with
zero model calls and zero I/O, so a typo fails at argv time, before anything
could cost money (D18 applied to schemas). Everything richer than this grammar
belongs in a ``--schema`` file; the error screens say so.

Grammar (pinned in ux.md):
    fields      ::= field (";" field)*
    field       ::= name type constraint*
    type        ::= string | number | integer | boolean | date | datetime
                  | enum(a, b, …) | string[] | number[]
    constraint  ::= ">=" N | "<=" N | minLength=N | maxLength=N | optional
"""

from __future__ import annotations

import re

from smartpipe.core.errors import UsageFault

__all__ = ["TYPE_MENU", "dsl_to_schema", "type_token"]

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_HELP = (
    "\n  Types: string · number · integer · boolean · date · datetime"
    " · enum(a, b, …) · string[] · number[]"
    "\n  Constraints: >= N · <= N · minLength=N · maxLength=N · optional"
)

_SIMPLE_TYPES: dict[str, dict[str, object]] = {
    "string": {"type": "string"},
    "number": {"type": "number"},
    "integer": {"type": "integer"},
    "boolean": {"type": "boolean"},
    # item 56: the ONLY temporal types — calendar day and point in time.
    # Both are strings on the wire; the coercion layer canonicalizes to ISO.
    "date": {"type": "string", "format": "date"},
    "datetime": {"type": "string", "format": "date-time"},
    "string[]": {"type": "array", "items": {"type": "string"}},
    "number[]": {"type": "array", "items": {"type": "number"}},
    # D48: integer[]/boolean[] round out the primitive arrays
    "integer[]": {"type": "array", "items": {"type": "integer"}},
    "boolean[]": {"type": "array", "items": {"type": "boolean"}},
}

_BOUND = re.compile(r"(>=|<=)\s*(-?\d+(?:\.\d+)?)")
_LENGTH = re.compile(r"(minLength|maxLength)=(\d+)")


TYPE_MENU = (
    "string · number · integer · boolean · date · datetime · enum(a, b, …)"
    " · string[] · number[] · any of them with ? for nullable (string?)"
)


def type_token(token: str) -> dict[str, object] | None:
    """One type token → a property dict, or None when it isn't a type.

    Shared vocabulary with the braces (D37): one grammar, two homes.
    A trailing ``?`` makes the field nullable (D48) — the type becomes a
    union with null, which every wire either supports (OpenAI strict,
    Ollama) or degrades safely (Gemini: nullable flag)."""
    nullable = token.endswith("?")
    if nullable:
        token = token[:-1].rstrip()
        if token.startswith("enum("):
            raise UsageFault(
                "enum(…)? — model 'no answer' as an explicit value instead\n"
                "  Example: status enum(paid, unpaid, unknown)\n"
                "  (a null union inside enum is rejected by strict provider modes)"
            )
    simple = _SIMPLE_TYPES.get(token)
    if simple is not None:
        prop = dict(simple)
        if nullable:
            base = prop["type"]
            prop["type"] = [base, "null"] if isinstance(base, str) else [*base, "null"]  # type: ignore[list-item]
        return prop
    if token.startswith("enum(") and token.endswith(")"):
        values = [value.strip() for value in token[5:-1].split(",") if value.strip()]
        return {"enum": values} if values else None
    return None


def dsl_to_schema(text: str) -> dict[str, object]:
    """Parse the ``--schema-from`` DSL; every problem is a loud, free UsageFault."""
    properties: dict[str, object] = {}
    required: list[str] = []
    fields = [field.strip() for field in text.split(";") if field.strip()]
    if not fields:
        raise UsageFault(f"--schema-from describes no fields{_HELP}")
    for field in fields:
        name, prop, is_required = _parse_field(field)
        if name in properties:
            raise UsageFault(f"--schema-from names {name!r} more than once{_HELP}")
        properties[name] = prop
        if is_required:
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _parse_field(field: str) -> tuple[str, dict[str, object], bool]:
    name, _space, rest = field.partition(" ")
    if not _NAME.match(name):
        raise UsageFault(f"--schema-from: field names must be identifiers, got {name!r}{_HELP}")
    rest = rest.strip()
    prop, remainder = _parse_type(name, rest)
    prop, remainder, is_required = _parse_constraints(name, prop, remainder)
    if remainder:
        raise UsageFault(f"--schema-from: unexpected {remainder!r} for field {name!r}{_HELP}")
    return name, prop, is_required


def _parse_type(name: str, rest: str) -> tuple[dict[str, object], str]:
    if rest.startswith("enum("):
        close = rest.find(")")
        if close == -1:
            raise UsageFault(f"--schema-from: unclosed enum( for field {name!r}{_HELP}")
        values = [value.strip() for value in rest[len("enum(") : close].split(",")]
        values = [value for value in values if value]
        if not values:
            raise UsageFault(f"--schema-from: enum needs at least one value for {name!r}{_HELP}")
        return {"enum": values}, rest[close + 1 :].strip()
    head, _space, tail = rest.partition(" ")
    simple = _SIMPLE_TYPES.get(head)
    if simple is None:
        offending = head if head else "(nothing)"
        raise UsageFault(f"--schema-from: unexpected {offending!r} for field {name!r}{_HELP}")
    return dict(simple), tail.strip()


def _parse_constraints(
    name: str, prop: dict[str, object], remainder: str
) -> tuple[dict[str, object], str, bool]:
    is_required = True
    kind = prop.get("type")
    while remainder:
        if remainder.startswith("optional"):
            is_required = False
            remainder = remainder[len("optional") :].strip()
            continue
        bound = _BOUND.match(remainder)
        if bound is not None:
            if kind not in ("number", "integer"):
                raise UsageFault(
                    f"--schema-from: {bound.group(1)} only applies to number/integer "
                    f"(field {name!r} is {kind or 'enum'}){_HELP}"
                )
            key = "minimum" if bound.group(1) == ">=" else "maximum"
            value = bound.group(2)
            prop[key] = int(value) if "." not in value else float(value)
            remainder = remainder[bound.end() :].strip()
            continue
        length = _LENGTH.match(remainder)
        if length is not None:
            if kind != "string":
                raise UsageFault(
                    f"--schema-from: {length.group(1)} only applies to string "
                    f"(field {name!r} is {kind or 'enum'}){_HELP}"
                )
            prop[length.group(1)] = int(length.group(2))
            remainder = remainder[length.end() :].strip()
            continue
        break  # unconsumed — the caller names it
    return prop, remainder, is_required
