"""Brace-grammar parsing (plan/ux.md "Brace grammar", plan/decisions.md D13).

The tokenizer is verb-neutral: it splits a prompt into literal text and brace
groups. What a brace group *means* differs by verb — in ``map`` the groups name
output fields; in ``filter``/``reduce`` a single-field group interpolates an
input value — but both build on these tokens. This module owns only the parse
and the verb-neutral helpers; per-verb prompt assembly lives alongside.

Grammar:
    prompt      ::= (text | brace_group | "{{" | "}}")*
    brace_group ::= "{" ws ident (ws "," ws ident)* ws "}"
    ident       ::= [A-Za-z_][A-Za-z0-9_]*
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from sempipe.core.errors import ItemError, UsageFault
from sempipe.engine.schema import shorthand_to_schema
from sempipe.models.base import (  # shared request value types, not behavior
    CompletionRequest,
    ImageData,
    MediaData,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sempipe.io.items import Item

__all__ = [
    "FILTER_JUDGE_SYSTEM",
    "IMAGE_ITEM_PREFIX",
    "JOIN_JUDGE_SYSTEM",
    "JUDGE_SCHEMA",
    "MAP_JSON_SYSTEM",
    "MAP_PLAIN_SYSTEM",
    "REDUCE_FINAL_JSON_SYSTEM",
    "REDUCE_FINAL_SYSTEM",
    "REDUCE_INTERMEDIATE_SYSTEM",
    "SCHEMA_DRAFT_SYSTEM",
    "BraceToken",
    "MapPlan",
    "TextToken",
    "Token",
    "brace_fields",
    "brace_notes",
    "brace_props",
    "build_filter_request",
    "build_judge_request",
    "build_map_request",
    "build_reduce_final",
    "build_reduce_intermediate",
    "build_repair_request",
    "build_schema_request",
    "has_brace",
    "interpolate_fields",
    "interpolate_join",
    "parse_join_predicate",
    "parse_prompt",
    "plan_map",
    "reject_comma_groups",
    "render",
    "to_instruction",
]

MAP_PLAIN_SYSTEM = (
    "You transform text. Reply with ONLY the transformed text for the item — "
    "no preamble, no quotes, no commentary."
)
MAP_JSON_SYSTEM = (
    "Extract exactly the requested fields as a single JSON object matching the schema. "
    "Reply with ONLY the JSON object — no preamble, no code fences, no commentary."
)
IMAGE_ITEM_PREFIX = "The item is an image. "  # stage-07 contract, verbatim
_PLAIN_MAX_TOKENS = 4096
_STRUCTURED_MAX_TOKENS = 8192

FILTER_JUDGE_SYSTEM = (
    "You judge whether an item satisfies a condition. "
    'Reply with ONLY JSON: {"match": true} if it satisfies the condition, '
    'or {"match": false} if it does not. No preamble, no explanation.'
)
JUDGE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"match": {"type": "boolean"}},
    "required": ["match"],
    "additionalProperties": False,
}
_JUDGE_MAX_TOKENS = 64

REDUCE_FINAL_SYSTEM = (
    "You synthesize many items into a single result. Follow the user's instruction "
    "exactly and reply with only the result — no preamble, no meta-commentary."
)
REDUCE_FINAL_JSON_SYSTEM = (
    "You synthesize many items into a single JSON object matching the schema. "
    "Reply with ONLY the JSON object — no preamble, no code fences."
)
REDUCE_INTERMEDIATE_SYSTEM = (
    "You are condensing PART of a larger collection. Produce dense notes that "
    "preserve every detail relevant to the stated goal. Do NOT write a conclusion "
    "or a final answer — only notes that a later step will combine with others."
)
_REDUCE_MAX_TOKENS = 8192

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SIDED_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?\Z")


@dataclass(frozen=True, slots=True)
class TextToken:
    text: str


@dataclass(frozen=True, slots=True)
class BraceToken:
    fields: tuple[str, ...]
    raw: str  # the original "{a, b}" text, kept for error messages and round-trips
    notes: tuple[str | None, ...] = ()  # rung-2 descriptions, aligned with fields (D22)
    props: tuple[Mapping[str, object] | None, ...] = ()  # inline types (D37)

    def note_for(self, position: int) -> str | None:
        return self.notes[position] if position < len(self.notes) else None

    def prop_for(self, position: int) -> Mapping[str, object] | None:
        return self.props[position] if position < len(self.props) else None


Token = TextToken | BraceToken


def parse_prompt(
    text: str, *, ident: re.Pattern[str] = _IDENT, allow_descriptions: bool = False
) -> tuple[Token, ...]:
    tokens: list[Token] = []
    literal: list[str] = []
    index = 0
    length = len(text)

    def flush() -> None:
        if literal:
            tokens.append(TextToken("".join(literal)))
            literal.clear()

    while index < length:
        char = text[index]
        pair = text[index : index + 2]
        if pair == "{{":
            literal.append("{")
            index += 2
        elif pair == "}}":
            literal.append("}")
            index += 2
        elif char == "{":
            flush()
            token, index = _parse_group(text, index, ident, allow_descriptions=allow_descriptions)
            tokens.append(token)
        elif char == "}":
            raise UsageFault("unexpected '}' in prompt — use '}}' for a literal brace")
        else:
            literal.append(char)
            index += 1
    flush()
    return tuple(tokens)


def brace_fields(tokens: tuple[Token, ...]) -> tuple[str, ...]:
    """Every field named across all brace groups, deduped, first-seen order."""
    seen: dict[str, None] = {}
    for token in tokens:
        if isinstance(token, BraceToken):
            for field in token.fields:
                seen.setdefault(field, None)
    return tuple(seen)


def brace_props(tokens: tuple[Token, ...]) -> dict[str, Mapping[str, object]]:
    """field → inline type (D37), for every typed field across all groups."""
    props: dict[str, Mapping[str, object]] = {}
    for token in tokens:
        if isinstance(token, BraceToken):
            for position, name in enumerate(token.fields):
                typed = token.prop_for(position)
                if typed is not None:
                    props[name] = typed
    return props


def brace_notes(tokens: tuple[Token, ...]) -> dict[str, str]:
    """field → rung-2 description, for every described field across all groups."""
    notes: dict[str, str] = {}
    for token in tokens:
        if isinstance(token, BraceToken):
            for position, name in enumerate(token.fields):
                described = token.note_for(position)
                if described is not None:
                    notes[name] = described
    return notes


def has_brace(tokens: tuple[Token, ...]) -> bool:
    return any(isinstance(token, BraceToken) for token in tokens)


def render(tokens: tuple[Token, ...]) -> str:
    """Reconstruct a prompt string; ``parse(render(parse(x))) == parse(x)``."""
    parts = [
        token.text.replace("{", "{{").replace("}", "}}")
        if isinstance(token, TextToken)
        else token.raw
        for token in tokens
    ]
    return "".join(parts)


def to_instruction(tokens: tuple[Token, ...]) -> str:
    """The model-facing instruction: literal text as-is, brace groups as their
    comma-joined field names (``Extract {a, b}`` → ``Extract a, b``); a rung-2
    description rides its field as guidance (``vendor (the supplier name)``)."""

    def group(token: BraceToken) -> str:
        rendered = (
            f"{name} ({note})" if (note := token.note_for(position)) is not None else name
            for position, name in enumerate(token.fields)
        )
        return ", ".join(rendered)

    parts = [token.text if isinstance(token, TextToken) else group(token) for token in tokens]
    return "".join(parts)


@dataclass(frozen=True, slots=True)
class MapPlan:
    mode: Literal["plain", "structured"]
    schema: Mapping[str, object] | None
    system: str


def plan_map(tokens: tuple[Token, ...], *, schema: Mapping[str, object] | None) -> MapPlan:
    """Decide plain vs structured: an explicit --schema wins; else braces imply a
    synthesized schema; else plain text (spec §3.1)."""
    if schema is not None:
        return MapPlan("structured", schema, MAP_JSON_SYSTEM)
    if has_brace(tokens):
        synthesized = shorthand_to_schema(
            brace_fields(tokens), descriptions=brace_notes(tokens), types=brace_props(tokens)
        )
        return MapPlan("structured", synthesized, MAP_JSON_SYSTEM)
    return MapPlan("plain", None, MAP_PLAIN_SYSTEM)


def build_map_request(
    plan: MapPlan,
    instruction: str,
    item_text: str,
    *,
    media: tuple[MediaData, ...] = (),
) -> CompletionRequest:
    max_tokens = _STRUCTURED_MAX_TOKENS if plan.mode == "structured" else _PLAIN_MAX_TOKENS
    prefixed = any(isinstance(part, ImageData) for part in media)
    system = f"{IMAGE_ITEM_PREFIX}{plan.system}" if prefixed else plan.system
    return CompletionRequest(
        system=system,
        user=f"{instruction}\n\n{item_text}" if item_text else instruction,
        json_schema=plan.schema,
        max_tokens=max_tokens,
        media=media,
    )


def build_repair_request(
    original: CompletionRequest, *, bad_reply: str, error: str
) -> CompletionRequest:
    """Re-ask with the validator's complaint so the model can self-correct once."""
    user = (
        f"{original.user}\n\n"
        f"Your previous reply was:\n{bad_reply}\n\n"
        f"That was invalid: {error}\n"
        "Reply again with ONLY a corrected JSON object."
    )
    return replace(original, user=user)


def reject_comma_groups(tokens: tuple[Token, ...]) -> None:
    """In filter/reduce, ``{field}`` reads one input field — comma-groups are a
    map-only shorthand (plan/decisions.md D13). Fail fast, don't guess."""
    for token in tokens:
        if isinstance(token, BraceToken) and len(token.fields) > 1:
            raise UsageFault(
                f"{token.raw} — comma-separated braces only work in 'map'\n"
                "  In map, braces name the output fields. In filter and reduce, {field}\n"
                "  inserts a field from each input item, one field per brace group."
            )


def interpolate_fields(tokens: tuple[Token, ...], data: Mapping[str, object] | None) -> str:
    """Substitute each single-field ``{field}`` with the item's value. Raises
    ``ItemError`` (→ skip-and-warn) when the item isn't JSON or lacks the field."""
    parts: list[str] = []
    for token in tokens:
        if isinstance(token, TextToken):
            parts.append(token.text)
            continue
        field = token.fields[0]  # comma-groups already rejected
        if data is None:
            raise ItemError(f"no field '{field}' (this item isn't JSON)")
        if field not in data:
            available = ", ".join(data) if data else "no fields"
            raise ItemError(f"no field '{field}'; this item has: {available}")
        parts.append(_render_value(data[field]))
    return "".join(parts)


def build_filter_request(condition: str, item_text: str) -> CompletionRequest:
    return CompletionRequest(
        system=FILTER_JUDGE_SYSTEM,
        user=f"Condition: {condition}\n\nItem:\n{item_text}",
        json_schema=JUDGE_SCHEMA,
        max_tokens=_JUDGE_MAX_TOKENS,
    )


SCHEMA_DRAFT_SYSTEM = (
    "You design JSON Schemas (draft 2020-12). Reply with ONLY the JSON Schema "
    "object — no preamble, no code fences. The top level must be an object schema "
    'with "type": "object", "properties", "required", and '
    '"additionalProperties": false. Mark a field optional only when the request '
    "implies it. Prefer precise types, enums for closed sets, and short "
    '"description" strings.'
)
_SCHEMA_DRAFT_MAX_TOKENS = 2048


def build_schema_request(description: str) -> CompletionRequest:
    """Rung 4 (D22): exactly one drafting call; the reply is meta-validated
    before stdout ever sees a byte."""
    return CompletionRequest(
        system=SCHEMA_DRAFT_SYSTEM,
        user=f"Design a JSON Schema for: {description}",
        max_tokens=_SCHEMA_DRAFT_MAX_TOKENS,
    )


JOIN_JUDGE_SYSTEM = (
    "You judge whether a statement about a pair of items is true. "
    'Reply with ONLY a JSON object: {"match": true} or {"match": false}.'
)

_JOIN_EXAMPLE = (
    '  Example: sempipe join "ticket {left.text} concerns {right.name}" --right products.jsonl'
)


def parse_join_predicate(text: str) -> tuple[Token, ...]:
    """The join grammar (D21): filter-rule braces, side-qualified. Every brace
    names a side; the predicate must read both sides (one-sided predicates match
    everything or nothing — a mistake, refused up front)."""
    tokens = parse_prompt(text, ident=_SIDED_IDENT)
    reject_comma_groups(tokens)
    sides: set[str] = set()
    for token in tokens:
        if not isinstance(token, BraceToken):
            continue
        field = token.fields[0]
        side, dot, _name = field.partition(".")
        if not dot:
            raise UsageFault(
                f"{{{field}}} is ambiguous in join — say {{left.{field}}} or {{right.{field}}}\n"
                "  join reads two inputs; each brace names a side's field.\n" + _JOIN_EXAMPLE
            )
        if side not in ("left", "right"):
            raise UsageFault(
                f"{token.raw} — the side must be left or right\n"
                "  join reads two inputs; each brace names a side's field.\n" + _JOIN_EXAMPLE
            )
        sides.add(side)
    for missing in ("left", "right"):
        if missing not in sides:
            raise UsageFault(
                f"the predicate never mentions the {missing} side — "
                f"say {{{missing}.field}} somewhere\n"
                "  join judges PAIRS; a predicate that reads one side matches everything "
                "or nothing."
            )
    return tokens


def interpolate_join(tokens: tuple[Token, ...], left: Item, right: Item) -> str:
    """Substitute ``{left.x}``/``{right.x}`` from the pair. ``.text`` falls back to
    the item's whole text; any other missing field is an ``ItemError`` (pair-skip)."""
    items = {"left": left, "right": right}
    parts: list[str] = []
    for token in tokens:
        if isinstance(token, TextToken):
            parts.append(token.text)
            continue
        side, _dot, name = token.fields[0].partition(".")
        item = items[side]
        parts.append(_join_value(side, name, item))
    return "".join(parts)


def _join_value(side: str, name: str, item: Item) -> str:
    if item.data is not None and name in item.data:
        return _render_value(item.data[name])
    if name == "text":
        return item.text  # the pinned fallback: the whole item as text
    available = ", ".join(item.data) if item.data else "no fields (not JSON)"
    raise ItemError(f"{side} has no field '{name}'; it has: {available}")


def build_judge_request(tokens: tuple[Token, ...], left: Item, right: Item) -> CompletionRequest:
    statement = interpolate_join(tokens, left, right)
    return CompletionRequest(
        system=JOIN_JUDGE_SYSTEM,
        user=f"Statement about a pair of items:\n{statement}\n\nIs the statement true?",
        json_schema=JUDGE_SCHEMA,
        max_tokens=_JUDGE_MAX_TOKENS,
    )


def build_reduce_final(
    instruction: str, texts: Sequence[str], schema: Mapping[str, object] | None
) -> CompletionRequest:
    system = REDUCE_FINAL_JSON_SYSTEM if schema is not None else REDUCE_FINAL_SYSTEM
    return CompletionRequest(
        system=system,
        user=f"{instruction}\n\nItems:\n{_numbered(texts)}",
        json_schema=schema,
        max_tokens=_REDUCE_MAX_TOKENS,
    )


def build_reduce_intermediate(goal: str, texts: Sequence[str]) -> CompletionRequest:
    return CompletionRequest(
        system=REDUCE_INTERMEDIATE_SYSTEM,
        user=f"Goal: {goal}\n\nItems:\n{_numbered(texts)}",
        json_schema=None,
        max_tokens=_REDUCE_MAX_TOKENS,
    )


def _numbered(texts: Sequence[str]) -> str:
    return "\n\n---\n\n".join(f"[{index + 1}] {text}" for index, text in enumerate(texts))


def _render_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _find_close(text: str, start: int) -> int:
    close = text.find("}", start + 1)
    if close == -1:
        raise UsageFault("unclosed '{' in prompt — did you mean '{{' for a literal brace?")
    return close


def _split_top_level(inner: str) -> list[str]:
    """Split on commas OUTSIDE parentheses — enum(a, b) survives whole (D37)."""
    parts: list[str] = []
    depth = 0
    current = ""
    for char in inner:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return parts


def _parse_group(
    text: str, start: int, ident: re.Pattern[str], *, allow_descriptions: bool = False
) -> tuple[BraceToken, int]:
    close = _find_close(text, start)
    raw = text[start : close + 1]
    inner = text[start + 1 : close]
    parts = tuple(part.strip() for part in _split_top_level(inner))
    names: list[str] = []
    notes: list[str | None] = []
    props: list[Mapping[str, object] | None] = []
    for part in parts:
        head, colon, description = part.partition(":")
        head = head.strip()
        if not allow_descriptions:
            names.append(part)
            notes.append(None)
            props.append(None)
            continue
        name, _space, type_text = head.partition(" ")
        type_text = type_text.strip()
        prop: Mapping[str, object] | None = None
        if type_text:
            # D37: an inline type, straight from the --schema-from vocabulary
            from sempipe.engine.schema_dsl import TYPE_MENU, type_token

            prop = type_token(type_text)
            if prop is None:
                raise UsageFault(
                    f"{{{part}}} — {type_text!r} isn't a type\n"
                    f"  Types: {TYPE_MENU}\n"
                    "  Constraints (>=, lengths, optional) live in --schema-from or --schema."
                )
        if colon:
            description = description.strip()
            if not description:
                raise UsageFault(
                    f"{{{part}}} names field {name!r} with an empty description\n"
                    f"  Write a description after the colon, or drop the colon: {{{name}}}"
                )
            names.append(name)
            notes.append(description)
        else:
            names.append(name)
            notes.append(None)
        props.append(prop)
    if any(not ident.match(name) for name in names):
        raise UsageFault(
            f"invalid field group: {raw}\n"
            "  field names must be identifiers (letters, digits, underscores), comma-separated"
        )
    described = tuple(notes) if any(note is not None for note in notes) else ()
    typed = tuple(props) if any(prop is not None for prop in props) else ()
    return BraceToken(tuple(names), raw, described, typed), close + 1
