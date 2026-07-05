"""Chunking math for the recursive ``reduce`` (spec §3.5) — pure.

The reduce tree "just works": when the input exceeds what the model can hold,
sempipe splits it into chunks, summarizes each, and recurses on the summaries —
no flags, no strategy selection. This module owns the arithmetic (token estimate,
context budget, item-boundary splitting); the verb drives the recursion, since
each level's size isn't known until the model produces the notes.

Token estimation is deliberately crude (≈4 chars/token) and used with a safety
factor — being conservative just means more levels, never a truncated call.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["budget_for", "chunk_indices", "estimate_tokens", "fits_in_one"]

# Conservative per-provider input windows; ollama is deliberately small because we
# can't cheaply know a local model's context length (a smaller budget is always safe).
_CONTEXT: dict[str, int] = {
    "ollama": 8000,
    "openai": 128000,
    "anthropic": 200000,
    "mistral": 128000,
}
_SAFETY = 0.6
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / _CHARS_PER_TOKEN) if text else 0


def budget_for(provider: str, *, prompt_overhead: int) -> int:
    window = _CONTEXT.get(provider, _CONTEXT["ollama"])
    return int(window * _SAFETY) - prompt_overhead


def fits_in_one(sizes: Sequence[int], budget: int) -> bool:
    return sum(sizes) <= budget


def chunk_indices(sizes: Sequence[int], budget: int) -> tuple[tuple[int, ...], ...]:
    """Group consecutive item indexes into chunks whose total estimate fits the
    budget. An item is never split; one that alone exceeds the budget gets its own
    (over-budget) chunk rather than being dropped."""
    chunks: list[tuple[int, ...]] = []
    current: list[int] = []
    current_size = 0
    for index, size in enumerate(sizes):
        if current and current_size + size > budget:
            chunks.append(tuple(current))
            current = []
            current_size = 0
        current.append(index)
        current_size += size
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)
