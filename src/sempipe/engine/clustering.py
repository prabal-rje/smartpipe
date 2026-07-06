"""Vector clustering for distinct/outliers/cluster/diff (D38) — pure, stdlib.

Hand-rolled on purpose (frozen dependency budget: no sklearn, ever). Leader
clustering is greedy and input-order stable: deterministic re-runs are a
feature — the answer must not change under the user.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sempipe.engine.ranking import cosine

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["knn_mean_distance", "leader_clusters"]


def leader_clusters(vectors: Sequence[tuple[float, ...]], *, threshold: float) -> list[list[int]]:
    """Greedy leader clustering: each vector joins the FIRST cluster whose
    leader (its first member) is ≥ ``threshold`` cosine-similar, else founds
    its own. Returns clusters as index lists, in founding order."""
    clusters: list[list[int]] = []
    for index, vector in enumerate(vectors):
        for members in clusters:
            if cosine(vectors[members[0]], vector) >= threshold:
                members.append(index)
                break
        else:
            clusters.append([index])
    return clusters


def knn_mean_distance(vectors: Sequence[tuple[float, ...]], *, k: int) -> list[float]:
    """Mean cosine DISTANCE (1 - similarity) to each vector's k nearest
    neighbors — the weirdness score for ``outliers``. Robust on multi-cluster
    corpora where centroid distance lies. O(n²): fine at corpus sizes where a
    human will read the answer."""
    n = len(vectors)
    neighbors = min(k, n - 1)
    if neighbors <= 0:
        return [0.0] * n
    scores: list[float] = []
    for index, vector in enumerate(vectors):
        distances = sorted(
            1.0 - cosine(vector, other)
            for position, other in enumerate(vectors)
            if position != index
        )
        scores.append(sum(distances[:neighbors]) / neighbors)
    return scores
