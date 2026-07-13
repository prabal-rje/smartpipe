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

    from numpy import float64
    from numpy.typing import NDArray

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
    chunk_size: int = 512,
) -> list[list[int]]:
    """Exact greedy clustering, accelerated: one NumPy GEMM per QUERY chunk.

    Chunking queries (not leaders) reads the leader matrix once per chunk
    instead of once per name — compute-bound GEMM, not memory-bound GEMV. The
    GEMM is a PREFILTER only: every candidate is re-checked with the reference
    ``cosine`` before it can win, so the decision arithmetic (and therefore the
    clustering) is identical to ``leader_clusters`` for rectangular inputs —
    ragged (mixed-dimension) inputs delegate to the reference loop, which
    tolerates them. The prefilter's margin covers embedding-scale float drift
    (BLAS dot error is O(d·eps), orders below 1e-9 at d≈1536); a low BLAS
    rounding therefore cannot hide a true candidate, and no acceptance ever
    rides BLAS alone. Leaders founded INSIDE the current chunk are checked by
    the in-order loop below the GEMM, preserving first-match founding order
    exactly."""
    import numpy as np

    if not vectors:
        return []
    width = len(vectors[0])
    if any(len(vector) != width for vector in vectors):
        # a ragged corpus cannot form the rectangle the GEMM needs; the
        # reference loop compares tuples of any shape — byte-identity holds
        return leader_clusters(
            vectors, threshold=threshold, should_stop=should_stop, progress=progress
        )
    matrix = np.asarray(vectors, dtype=np.float64)
    norms = np.sqrt(np.sum(matrix * matrix, axis=1))
    normalized = np.divide(
        matrix, norms[:, None], out=np.zeros_like(matrix), where=norms[:, None] != 0.0
    )
    step = max(1, chunk_size)
    clusters: list[list[int]] = []
    leaders: list[int] = []
    total = len(vectors)
    stopped_at: int | None = None
    # leaders founded inside the CURRENT chunk, as a contiguous buffer so the
    # in-chunk check below is one small numpy product, never a Python scan
    fresh_buffer: NDArray[float64] = np.empty((step, normalized.shape[1]), dtype=np.float64)
    for start in range(0, total, step):
        end = min(start + step, total)
        prior = len(leaders)  # leaders founded before this chunk, in founding order
        fresh_count = 0
        similarities = (
            normalized[start:end] @ normalized[np.asarray(leaders)].T if leaders else None
        )
        for index in range(start, end):
            if should_stop is not None and should_stop():
                stopped_at = index
                break
            match_index: int | None = None
            if similarities is not None:
                row: NDArray[float64] = similarities[index - start]
                mask = row >= threshold - 1e-9
                candidates: list[int] = [int(p) for p in mask.nonzero()[0].tolist()]
                for position in candidates:  # ascending = founding order
                    if cosine(vectors[leaders[position]], vectors[index]) >= threshold:
                        match_index = position
                        break
            if match_index is None and fresh_count:  # then leaders founded in this chunk
                fresh_row: NDArray[float64] = fresh_buffer[:fresh_count] @ normalized[index]
                fresh_mask = fresh_row >= threshold - 1e-9
                fresh_hits: list[int] = [int(p) for p in fresh_mask.nonzero()[0].tolist()]
                for offset in fresh_hits:  # ascending = founding order within the chunk
                    if cosine(vectors[leaders[prior + offset]], vectors[index]) >= threshold:
                        match_index = prior + offset
                        break
            if match_index is None:
                fresh_buffer[fresh_count] = normalized[index]
                fresh_count += 1
                leaders.append(index)
                clusters.append([index])
            else:
                clusters[match_index].append(index)
            if progress is not None:
                progress()
        if stopped_at is not None:
            break
    if stopped_at is not None:
        clusters.extend([position] for position in range(stopped_at, total))
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
