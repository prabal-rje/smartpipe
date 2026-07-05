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
from dataclasses import dataclass

from sempipe.core.errors import UsageFault

__all__ = [
    "BraceToken",
    "TextToken",
    "Token",
    "brace_fields",
    "has_brace",
    "parse_prompt",
    "render",
]

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
