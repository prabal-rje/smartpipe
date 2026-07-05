"""Candidate selection for ``join`` (D21) — the *block* of embed-block-judge.

Pure math over an in-memory index: brute-force cosine, same envelope as
``top_k`` (no vector database, no ANN — recorded non-goals; the corpus lives in
the pipe). ``candidates`` is the recall knob's implementation: everything the
judge never sees is a match the user never gets, so ties and ordering are pinned
and property-tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sempipe.engine.ranking import rank, select

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["RightIndex", "build_index", "candidates"]


@dataclass(frozen=True, slots=True)
class RightIndex:
    """The build side, embedded once: position ↔ the caller's right-row payloads."""

    vectors: tuple[tuple[float, ...], ...]

    def __len__(self) -> int:
        return len(self.vectors)


def build_index(vectors: Sequence[Sequence[float]]) -> RightIndex:
    return RightIndex(vectors=tuple(tuple(vector) for vector in vectors))


def candidates(
    query: Sequence[float], index: RightIndex, *, k: int, threshold: float | None
) -> tuple[tuple[int, float], ...]:
    """The top-``k`` right positions for one left vector, best-first, ties by
    position; ``threshold`` filters before ``k`` (a floor, then a width)."""
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    return select(rank(query, index.vectors), k=k, threshold=threshold)
