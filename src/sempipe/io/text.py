"""Terminal display width — cells, not code points (DEFER-2).

Stdlib-only by design: ``wcwidth`` stays outside the dependency budget. The
rule: combining marks and zero-width format characters (ZWJ et al.) count 0,
East-Asian Wide/Fullwidth count 2, everything else counts 1. Emoji-ZWJ
sequences therefore measure as the *sum of their parts* — approximate on
purpose, and documented as such wherever alignment matters.
"""

from __future__ import annotations

import unicodedata

__all__ = ["clip_to_width", "display_width"]

_WIDE = frozenset({"W", "F"})


def _char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    if unicodedata.category(char) == "Cf":  # ZWJ, ZWNJ, direction marks — zero cells
        return 0
    return 2 if unicodedata.east_asian_width(char) in _WIDE else 1


def display_width(text: str) -> int:
    """How many terminal cells ``text`` occupies (approximate for emoji-ZWJ)."""
    return sum(_char_width(char) for char in text)


def clip_to_width(text: str, budget: int) -> str:
    """The longest prefix of ``text`` that fits in ``budget`` cells — a wide
    character that would straddle the boundary is dropped, never split."""
    used = 0
    for position, char in enumerate(text):
        used += _char_width(char)
        if used > budget:
            return text[:position]
    return text
