"""Chunking math for the recursive ``reduce`` (spec §3.5) — pure.

The reduce tree "just works": when the input exceeds what the model can hold,
smartpipe splits it into chunks, summarizes each, and recurses on the summaries —
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

__all__ = [
    "budget_for",
    "chunk_indices",
    "estimate_tokens",
    "fits_in_one",
    "halve",
    "is_context_overflow",
    "mean_pool",
    "split_text",
]

# Conservative per-provider input windows; ollama is deliberately small because we
# can't cheaply know a local model's context length (a smaller budget is always safe).
_CONTEXT: dict[str, int] = {
    "ollama": 8000,
    "openai": 128000,
    "anthropic": 200000,
    "mistral": 128000,
    "gemini": 128000,  # conservative: flash models carry ≥1M, but budget for the floor
    "openrouter": 32000,  # unknowable per-model — the safe floor for routed models
}
_SAFETY = 0.6
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / _CHARS_PER_TOKEN) if text else 0


def budget_for(provider: str, *, prompt_overhead: int, window: int | None = None) -> int:
    """Token budget for one call. ``window`` (from a live probe or the
    SMARTPIPE_CONTEXT_TOKENS override) beats the static table when present."""
    resolved = window if window is not None else _CONTEXT.get(provider, _CONTEXT["ollama"])
    return int(resolved * _SAFETY) - prompt_overhead


_OVERFLOW_MARKERS = (
    "context_length",
    "context length",
    "context window",
    "maximum context",
    "too long",
    "too large",
    "input length",
    "prompt is too long",
    "exceeds the limit",
)


def is_context_overflow(message: str) -> bool:
    """Does this per-item error text look like a context-window overflow?

    The classifier that lets reduce self-correct (D26): estimates are hints,
    the wire's own rejection is ground truth — matched loosely on the phrases
    the five wired providers actually use."""
    lowered = message.lower()
    return any(marker in lowered for marker in _OVERFLOW_MARKERS)


def halve(chunk: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Split a chunk of item indexes into two non-empty halves (len must be ≥ 2)."""
    middle = len(chunk) // 2
    return chunk[:middle], chunk[middle:]


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


def split_text(text: str, budget: int) -> tuple[str, ...]:
    """Break one oversized text into ≤-budget chunks (D26 layer 3).

    Paragraph boundaries first, then lines, then a hard character cut — and the
    chunks concatenate back to the original text exactly (nothing added, nothing
    lost; separators travel with their preceding piece)."""
    if estimate_tokens(text) <= budget:
        return (text,)
    pieces = _carrying_separators(text, r"\n\n+")
    if len(pieces) == 1:
        pieces = _carrying_separators(text, r"\n")
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        for part in _hard_cut(piece, budget):
            if current and estimate_tokens(current + part) > budget:
                chunks.append(current)
                current = ""
            current += part
    if current:
        chunks.append(current)
    return tuple(chunks)


def _carrying_separators(text: str, pattern: str) -> list[str]:
    """Split by a separator regex, attaching each separator to the piece before
    it, so ``"".join(result) == text``."""
    import re

    tokens = re.split(f"({pattern})", text)
    pieces: list[str] = []
    for token in tokens:
        if pieces and re.fullmatch(pattern, token or " ") and token:
            pieces[-1] += token
        elif token:
            pieces.append(token)
    return pieces or [text]


def _hard_cut(piece: str, budget: int) -> tuple[str, ...]:
    """A single piece that alone exceeds the budget is cut at character bounds."""
    limit = max(budget * _CHARS_PER_TOKEN, 1)
    if len(piece) <= limit:
        return (piece,)
    return tuple(piece[i : i + limit] for i in range(0, len(piece), limit))


def mean_pool(vectors: Sequence[tuple[float, ...]]) -> tuple[float, ...]:
    """Component-wise mean of chunk embeddings — the standard whole-document
    vector when one text had to be embedded in pieces (D26)."""
    assert vectors, "mean_pool needs at least one vector"
    count = len(vectors)
    return tuple(sum(component) / count for component in zip(*vectors, strict=True))
