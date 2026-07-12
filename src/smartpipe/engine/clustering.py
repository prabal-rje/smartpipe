"""Vector clustering for distinct/outliers/cluster/diff (D38) — pure, stdlib.

Hand-rolled on purpose (frozen dependency budget: no sklearn, ever). Leader
clustering is greedy and input-order stable: deterministic re-runs are a
feature — the answer must not change under the user.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeAlias

from smartpipe.engine.ranking import cosine

if TYPE_CHECKING:
    from collections.abc import Sequence

ClusteringStrategy: TypeAlias = Callable[..., list[list[int]]]

__all__ = [
    "ClusteringStrategy",
    "adaptive_threshold",
    "knn_mean_distance",
    "leader_clusters",
    "merge_to_k",
    "numpy_leader_clusters",
    "select_leader_clustering",
]


def leader_clusters(
    vectors: Sequence[tuple[float, ...]],
    *,
    threshold: float,
    should_stop: Callable[[], bool] | None = None,
    progress: Callable[[], None] | None = None,
) -> list[list[int]]:
    """Exact greedy reference strategy, with one cooperative check per name."""
    clusters: list[list[int]] = []
    for index, vector in enumerate(vectors):
        if should_stop is not None and should_stop():
            clusters.extend([position] for position in range(index, len(vectors)))
            break
        for members in clusters:
            if cosine(vectors[members[0]], vector) >= threshold:
                members.append(index)
                break
        else:
            clusters.append([index])
        if progress is not None:
            progress()
    return clusters


def numpy_leader_clusters(
    vectors: Sequence[tuple[float, ...]],
    *,
    threshold: float,
    should_stop: Callable[[], bool] | None = None,
    progress: Callable[[], None] | None = None,
    chunk_size: int = 4_096,
) -> list[list[int]]:
    """Exact greedy clustering using chunked NumPy GEMM against current leaders."""
    import numpy as np

    if not vectors:
        return []
    matrix = np.asarray(vectors, dtype=np.float64)
    norms = np.sqrt(np.sum(matrix * matrix, axis=1))
    normalized = np.divide(
        matrix, norms[:, None], out=np.zeros_like(matrix), where=norms[:, None] != 0.0
    )
    clusters: list[list[int]] = []
    leaders: list[int] = []
    for index in range(len(vectors)):
        if should_stop is not None and should_stop():
            clusters.extend([position] for position in range(index, len(vectors)))
            break
        match_index: int | None = None
        for start in range(0, len(leaders), chunk_size):
            positions = leaders[start : start + chunk_size]
            similarities = normalized[positions] @ normalized[index]
            hit_positions = tuple(
                position
                for position, similarity in enumerate(similarities.tolist())
                if similarity >= threshold
                and cosine(vectors[positions[position]], vectors[index]) >= threshold
            )
            if hit_positions:
                match_index = start + hit_positions[0]
                break
        if match_index is None:
            leaders.append(index)
            clusters.append([index])
        else:
            clusters[match_index].append(index)
        if progress is not None:
            progress()
    return clusters


def select_leader_clustering() -> ClusteringStrategy:
    """Select the accelerated strategy at the composition seam, with no hard dependency."""
    try:
        import numpy as np
    except ImportError:
        return leader_clusters
    _ = np
    return numpy_leader_clusters


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


def merge_to_k(
    vectors: Sequence[tuple[float, ...]], clusters: list[list[int]], *, k: int
) -> list[list[int]]:
    """Force exactly ``k`` clusters: repeatedly merge the SMALLEST into the
    cluster whose leader is most similar to its leader (ties: earliest).
    Deterministic; returns a new list, founding order preserved."""
    merged = [list(members) for members in clusters]
    while len(merged) > max(1, k):
        smallest = min(range(len(merged)), key=lambda index: (len(merged[index]), index))
        leader = vectors[merged[smallest][0]]
        best = max(
            (index for index in range(len(merged)) if index != smallest),
            key=lambda index: (cosine(vectors[merged[index][0]], leader), -index),
        )
        merged[best].extend(merged[smallest])
        del merged[smallest]
    return merged


_PAIR_BUDGET = 20_000  # pairwise sample cap — clustering stays interactive


def adaptive_threshold(vectors: Sequence[tuple[float, ...]]) -> float:
    """A grouping threshold derived from the corpus itself: median pairwise
    similarity (the cross-theme background) plus 35% of the gap to the 95th
    percentile (the same-theme tail). Fixed thresholds can't serve every
    embedder's geometry — gemini's same-theme pairs sit near 0.7 where
    synthetic unit vectors sit near 1.0 (measured, D38/05). Deterministic
    given the corpus; pairs are stride-sampled past the budget."""
    n = len(vectors)
    if n < 3:
        return 0.99  # nothing to group — only near-identity folds
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    if len(pairs) > _PAIR_BUDGET:
        stride = len(pairs) // _PAIR_BUDGET + 1
        pairs = pairs[::stride]
    similarities = sorted(cosine(vectors[i], vectors[j]) for i, j in pairs)
    median = similarities[len(similarities) // 2]
    p95 = similarities[min(len(similarities) - 1, int(len(similarities) * 0.95))]
    return median + 0.35 * (p95 - median)
