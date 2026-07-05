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

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from sempipe.core.errors import UsageFault
from sempipe.engine.schema import shorthand_to_schema
from sempipe.models.base import CompletionRequest  # a shared request value type, not behavior

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "MAP_JSON_SYSTEM",
    "MAP_PLAIN_SYSTEM",
    "BraceToken",
    "MapPlan",
    "TextToken",
    "Token",
    "brace_fields",
    "build_map_request",
    "build_repair_request",
    "has_brace",
    "parse_prompt",
    "plan_map",
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
_PLAIN_MAX_TOKENS = 4096
_STRUCTURED_MAX_TOKENS = 8192

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True, slots=True)
class TextToken:
    text: str


@dataclass(frozen=True, slots=True)
class BraceToken:
    fields: tuple[str, ...]
    raw: str  # the original "{a, b}" text, kept for error messages and round-trips


Token = TextToken | BraceToken


def parse_prompt(text: str) -> tuple[Token, ...]:
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
            token, index = _parse_group(text, index)
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
    comma-joined field names (``Extract {a, b}`` → ``Extract a, b``)."""
    parts = [
        token.text if isinstance(token, TextToken) else ", ".join(token.fields) for token in tokens
    ]
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
        return MapPlan("structured", shorthand_to_schema(brace_fields(tokens)), MAP_JSON_SYSTEM)
    return MapPlan("plain", None, MAP_PLAIN_SYSTEM)


def build_map_request(plan: MapPlan, instruction: str, item_text: str) -> CompletionRequest:
    max_tokens = _STRUCTURED_MAX_TOKENS if plan.mode == "structured" else _PLAIN_MAX_TOKENS
    return CompletionRequest(
        system=plan.system,
        user=f"{instruction}\n\n{item_text}",
        json_schema=plan.schema,
        max_tokens=max_tokens,
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


def _parse_group(text: str, start: int) -> tuple[BraceToken, int]:
    close = text.find("}", start + 1)
    if close == -1:
        raise UsageFault("unclosed '{' in prompt — did you mean '{{' for a literal brace?")
    raw = text[start : close + 1]
    inner = text[start + 1 : close]
    fields = tuple(part.strip() for part in inner.split(","))
    if any(not _IDENT.match(field) for field in fields):
        raise UsageFault(
            f"invalid field group: {raw}\n"
            "  field names must be identifiers (letters, digits, underscores), comma-separated"
        )
    return BraceToken(fields, raw), close + 1
