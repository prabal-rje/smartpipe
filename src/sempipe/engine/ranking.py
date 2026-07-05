"""Similarity ranking for ``top_k`` (plan/architecture.md, spec §3.4) — pure math.

Brute-force cosine: no vector database, the embeddings live in the pipe (D16). The
zero-vector guard matters — a cosine with a zero vector is defined as 0, never NaN,
so an empty or degenerate embedding can't poison the ranking.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["cosine", "rank", "select", "unit_score"]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def unit_score(cos: float) -> float:
    """Map cosine [-1, 1] to a [0, 1] score (spec §3.4)."""
    return max(0.0, min(1.0, (1.0 + cos) / 2.0))


def rank(
    query: Sequence[float], vectors: Sequence[Sequence[float]]
) -> tuple[tuple[int, float], ...]:
    """Score every vector against the query, sorted best-first; ties by input order."""
    scored = [(index, unit_score(cosine(query, vector))) for index, vector in enumerate(vectors)]
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return tuple(scored)


def select(
    ranked: Sequence[tuple[int, float]], *, k: int | None, threshold: float | None
) -> tuple[tuple[int, float], ...]:
    """Apply a similarity threshold and/or a top-K limit (spec §3.4)."""
    kept = ranked if threshold is None else [p for p in ranked if p[1] >= threshold]
    limited = kept if k is None else kept[:k]
    return tuple(limited)
