"""Nested field paths (ledger item 63): one hand-rolled mini-grammar, five surfaces.

Grammar:
    PATH := NAME ( "." NAME | "[" INT "]" | "['" KEY "']" )*

``a.b.c``, ``a.b[0]``, ``a.b['weird key']``, and mixes like ``items[2].name``.
Three layers, deliberately separate:

- ``parse_path`` - text → accessors, with loud deterministic ``UsageFault``
  errors (``a.b[x] - index must be a number``).
- ``resolve`` - traversal over ``Mapping``/``Sequence``; a miss at ANY hop is
  the ``MISSING`` sentinel, never an exception. Negative indexes keep Python
  semantics; sequences reject Key hops and mappings reject Index hops (a miss,
  not an error). Strings and bytes never index - they are scalars here.
- ``lookup`` - THE COMPAT RULE: an exact flat key always wins before any path
  parsing, so a record with a literal column named ``user.name`` (CSV headers
  do this) resolves that column, never the traversal. Text with no path
  punctuation is exact-key-or-miss and is never parsed at all.

Every surface (prompt interpolation, where, sort, chart, summarize) goes
through ``lookup``. Pure: no I/O, no state beyond a parse cache.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Final, NoReturn, assert_never

from smartpipe.core.errors import UsageFault
from smartpipe.core.jsontools import as_items, as_record

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "MISSING",
    "Accessor",
    "Index",
    "Key",
    "Missing",
    "has_path_syntax",
    "lookup",
    "parse_path",
    "resolve",
    "validate_field",
]


@dataclass(frozen=True, slots=True)
class Key:
    """One mapping hop: ``.name`` or ``['name']``."""

    name: str


@dataclass(frozen=True, slots=True)
class Index:
    """One sequence hop: ``[0]`` (negative indexes keep Python semantics)."""

    position: int


Accessor = Key | Index


@dataclass(frozen=True, slots=True)
class Missing:
    """The 'no value at this path' sentinel - compare with ``is MISSING``."""


MISSING: Final[Missing] = Missing()

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INT = re.compile(r"-?\d+\Z")


def has_path_syntax(text: str) -> bool:
    """True when ``text`` contains path punctuation (``.`` or ``[``). Flat
    names never parse - the discrimination every surface shares."""
    return "." in text or "[" in text


def _fail(text: str, reason: str) -> NoReturn:
    raise UsageFault(f"{text} - {reason}")


@lru_cache(maxsize=256)
def parse_path(text: str) -> tuple[Accessor, ...]:
    """Tokenize-and-fold ``text`` into accessors; malformed paths are loud."""
    if not text:
        raise UsageFault("field path is empty")
    if text[0] == ".":
        _fail(text, "leading dot")
    head = _NAME.match(text)
    if head is None:
        _fail(text, "a path must start with a field name")
    accessors: list[Accessor] = [Key(head.group())]
    position = head.end()
    while position < len(text):
        char = text[position]
        if char == ".":
            if position + 1 == len(text):
                _fail(text, "trailing dot")
            name = _NAME.match(text, position + 1)
            if name is None:
                _fail(text, "expected a field name after '.'")
            accessors.append(Key(name.group()))
            position = name.end()
        elif char == "[":
            accessor, position = _bracket(text, position)
            accessors.append(accessor)
        else:
            _fail(text, f"unexpected {char!r}")
    return tuple(accessors)


def _bracket(text: str, start: int) -> tuple[Accessor, int]:
    """One ``[…]`` segment beginning at ``start``: a quoted key or an index."""
    if text[start + 1 : start + 2] == "'":
        closing = text.find("'", start + 2)
        if closing < 0:
            _fail(text, "unclosed quote")
        if text[closing + 1 : closing + 2] != "]":
            _fail(text, "expected ']' after the quoted key")
        return Key(text[start + 2 : closing]), closing + 2
    end = text.find("]", start + 1)
    if end < 0:
        _fail(text, "unclosed '['")
    body = text[start + 1 : end]
    if _INT.match(body) is None:
        _fail(text, "index must be a number")
    return Index(int(body)), end + 1


def resolve(record: object, path: tuple[Accessor, ...]) -> object:
    """Walk ``path`` through ``record``; any miss returns ``MISSING``."""
    current: object = record
    for accessor in path:
        match accessor:
            case Key(name):
                mapping = as_record(current)
                if mapping is None or name not in mapping:
                    return MISSING
                current = mapping[name]
            case Index(position):
                items = as_items(current)
                if items is None:
                    return MISSING
                try:
                    current = items[position]
                except IndexError:
                    return MISSING
            case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                assert_never(unreachable)
    return current


def validate_field(text: str) -> str:
    """The flag-edge grammar gate (fail-before-spend): text with path syntax
    must parse; flat names pass untouched — resolution stays ``lookup``'s job,
    so the compat rule still applies to valid path text."""
    if has_path_syntax(text):
        parse_path(text)
    return text


def lookup(record: Mapping[str, object], text: str) -> object:
    """The one field read every surface shares: exact key → path parse →
    resolve. Flat text (no ``.``/``[``) never parses; malformed path text is
    loud only when no literal column claims it first."""
    if text in record:
        return record[text]
    if not has_path_syntax(text):
        return MISSING
    return resolve(record, parse_path(text))
