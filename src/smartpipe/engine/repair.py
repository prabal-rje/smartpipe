"""Deterministic JSON repair — rung 0 of the structured-output ladder (item 58).

A malformed structured reply used to cost one paid model round trip before the
item could skip. This module fixes the boring failures for free: markdown code
fences, prose around the JSON island, trailing commas, Python literals
(``True``/``False``/``None``), unquoted keys, and single-quoted strings.

Strategy: a pipeline of candidate transforms, each followed by a parse attempt —
the first candidate ``json.loads`` accepts wins, returned as *text* so the
caller revalidates it through the existing schema-coercion path. ``None`` means
nothing landed and the paid repair rung proceeds exactly as before. Pure and
total: no I/O, no state, never raises.
"""

from __future__ import annotations

import ast
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["repair_json"]

_FENCE_OPEN = re.compile(r"^```[A-Za-z0-9]*[ \t]*\n")
_FENCE_CLOSE = re.compile(r"\n?```\s*$")
# a complete JSON string literal, escapes included — the spans transforms must not touch
_STRING_SPAN = re.compile(r'"(?:[^"\\]|\\.)*"')
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_PYTHON_LITERAL = re.compile(r"\b(True|False|None)\b")
_BARE_KEY = re.compile(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)")

_JSON_LITERALS = {"True": "true", "False": "false", "None": "null"}


def repair_json(text: str) -> str | None:
    """The repaired reply as parseable JSON text, or ``None`` when no
    deterministic transform lands. Valid input returns itself (stripped)."""
    stripped = text.strip()
    if not stripped:
        return None
    if _parses(stripped):
        return stripped
    base = _island(_unfenced(stripped))
    if base is None:
        return None
    if _parses(base):
        return base  # fences/prose were the only problem
    as_python = _python_literal(base)
    if as_python is not None:
        return as_python  # a Python-repr reply: quotes, literals, and commas at once
    candidate = base
    for transform in (_drop_trailing_commas, _fix_literals, _quote_bare_keys, _swap_quotes):
        candidate = transform(candidate)
        if _parses(candidate):
            return candidate
    return None


def _parses(candidate: str) -> bool:
    try:
        json.loads(candidate)
    except (json.JSONDecodeError, RecursionError):  # depth bombs must not escape rung 0
        return False
    return True


def _unfenced(text: str) -> str:
    """The body of a leading markdown fence (```json … ```); prose after the
    closing fence is the island step's problem, not ours."""
    if not text.startswith("```"):
        return text
    opened = _FENCE_OPEN.sub("", text, count=1)
    return _FENCE_CLOSE.sub("", opened, count=1).strip()


def _island(text: str) -> str | None:
    """The outermost ``{…}`` or ``[…]`` span — whichever container opens first."""
    starts = [(position, mark) for mark in "{[" if (position := text.find(mark)) != -1]
    if not starts:
        return None  # no container in sight: prose repairs nothing
    position, opener = min(starts)
    end = text.rfind("}" if opener == "{" else "]")
    if end <= position:
        return None
    return text[position : end + 1]


def _python_literal(text: str) -> str | None:
    """A reply that is a Python literal (``{'a': True}``): evaluate it safely,
    re-serialize as JSON — single quotes, literals, and trailing commas in one
    string-safe step."""
    try:
        value = ast.literal_eval(text)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    if not isinstance(value, dict | list):
        return None
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None  # sets, bytes, NaN — no JSON spelling exists


def _outside_strings(text: str, fix: Callable[[str], str]) -> str:
    """Apply ``fix`` only between complete string literals — a transform must
    never rewrite the inside of a value."""
    pieces: list[str] = []
    last = 0
    for span in _STRING_SPAN.finditer(text):
        pieces.append(fix(text[last : span.start()]))
        pieces.append(span.group())
        last = span.end()
    pieces.append(fix(text[last:]))
    return "".join(pieces)


def _drop_trailing_commas(text: str) -> str:
    return _outside_strings(text, lambda part: _TRAILING_COMMA.sub(r"\1", part))


def _fix_literals(text: str) -> str:
    return _outside_strings(
        text, lambda part: _PYTHON_LITERAL.sub(lambda hit: _JSON_LITERALS[hit.group(1)], part)
    )


def _quote_bare_keys(text: str) -> str:
    return _outside_strings(text, lambda part: _BARE_KEY.sub(r'\1"\2"\3', part))


def _swap_quotes(text: str) -> str:
    """The last resort: every single quote becomes a double quote. Blind on
    purpose — the pipeline's parse check accepts it only when it yields JSON."""
    return text.replace("'", '"')
