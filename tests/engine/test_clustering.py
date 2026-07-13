"""Leader clustering + kNN weirdness (D38): deterministic, order-stable."""

from __future__ import annotations

from smartpipe.engine.clustering import (
    knn_mean_distance,
    leader_clusters,
    numpy_leader_clusters,
)

NEARLY_RIGHT = (0.995, 0.0999)  # ~cos 0.995 to (1,0)
RIGHT = (1.0, 0.0)
UP = (0.0, 1.0)


def test_near_vectors_fold_into_the_first_leader() -> None:
    clusters = leader_clusters([RIGHT, NEARLY_RIGHT, UP], threshold=0.9)
    assert clusters == [[0, 1], [2]]  # leader = first member, input order kept


def test_threshold_separates() -> None:
    clusters = leader_clusters([RIGHT, NEARLY_RIGHT], threshold=0.9999)
    assert clusters == [[0], [1]]


def test_order_stability_is_a_feature() -> None:
    # the same corpus reversed founds clusters in reversed order — but the
    # GROUPING is the same; re-runs of the same input are identical
    forward = leader_clusters([RIGHT, NEARLY_RIGHT, UP], threshold=0.9)
    again = leader_clusters([RIGHT, NEARLY_RIGHT, UP], threshold=0.9)
    assert forward == again


def test_knn_ranks_the_planted_outlier_highest() -> None:
    cluster = [RIGHT, NEARLY_RIGHT, (0.99, 0.14)]
    corpus = [*cluster, UP]  # UP is far from the tight cluster
    scores = knn_mean_distance(corpus, k=2)
    assert max(range(len(corpus)), key=lambda index: scores[index]) == 3


def test_knn_on_tiny_corpus_is_zero() -> None:
    assert knn_mean_distance([RIGHT], k=5) == [0.0]


def test_merge_to_k_folds_smallest_into_most_similar() -> None:
    from smartpipe.engine.clustering import merge_to_k

    vectors = [RIGHT, NEARLY_RIGHT, UP, (0.01, 1.0)]
    clusters = [[0], [1], [2, 3]]  # three clusters, want two
    merged = merge_to_k(vectors, clusters, k=2)
    assert sorted(sorted(m) for m in merged) == [[0, 1], [2, 3]]  # [1] joined [0], its twin


def test_adaptive_threshold_lands_in_the_gap() -> None:
    # measured gemini-like geometry: same-theme ~0.62-0.75, background ~0.47-0.55
    import math

    from smartpipe.engine.clustering import adaptive_threshold

    def vec(angle: float) -> tuple[float, float]:
        return (math.cos(angle), math.sin(angle))

    corpus = [vec(0.0), vec(0.55), vec(0.72), vec(1.5), vec(1.35), vec(2.6)]
    threshold = adaptive_threshold(corpus)
    assert 0.4 < threshold < 0.9  # between background and same-theme tail


def test_adaptive_threshold_on_tiny_corpus_only_folds_identity() -> None:
    from smartpipe.engine.clustering import adaptive_threshold

    assert adaptive_threshold([RIGHT, UP]) == 0.99


def test_numpy_strategy_matches_pure_across_order_capacity_and_threshold_edges() -> None:
    vectors = [RIGHT, UP, NEARLY_RIGHT, (0.01, 1.0), (-1.0, 0.0), (0.8, 0.6)]
    for threshold in (0.0, 0.8, 0.9, 1.0):
        expected = leader_clusters(vectors, threshold=threshold)
        assert numpy_leader_clusters(vectors, threshold=threshold, chunk_size=2) == expected


def test_first_above_threshold_wins_not_most_similar() -> None:
    vectors = [RIGHT, (0.8, 0.6), (0.9, 0.435889894)]
    expected = [[0, 2], [1]]
    assert leader_clusters(vectors, threshold=0.89) == expected
    assert numpy_leader_clusters(vectors, threshold=0.89, chunk_size=2) == expected


def test_progress_is_per_name_and_stop_preserves_clean_partial() -> None:
    ticks: list[int] = []

    def stopped() -> bool:
        return len(ticks) == 3

    clusters = leader_clusters(
        [RIGHT, NEARLY_RIGHT, UP, (-1.0, 0.0)],
        threshold=0.9,
        should_stop=stopped,
        progress=lambda: ticks.append(1),
    )
    assert ticks == [1, 1, 1]
    assert clusters == [[0, 1], [2], [3]]


def test_numpy_handles_zero_vectors_exactly_like_pure() -> None:
    vectors = [(0.0, 0.0), RIGHT, (0.0, 0.0)]
    assert numpy_leader_clusters(vectors, threshold=0.9, chunk_size=1) == leader_clusters(
        vectors, threshold=0.9
    )


def test_numpy_rechecks_boundary_candidates_with_reference_cosine() -> None:
    vectors = [
        (0.5335262281018054, 0.07177402500803365),
        (0.2620289869780936, 0.06322371923677483),
    ]
    threshold = 0.9946965840963299
    assert (
        numpy_leader_clusters(vectors, threshold=threshold)
        == leader_clusters(vectors, threshold=threshold)
        == [[0], [1]]
    )


def test_numpy_strategy_matches_pure_on_a_seeded_random_corpus() -> None:
    """Property check across chunk boundaries: 300 seeded 8-dim vectors, a
    mid-range threshold, and chunks smaller than the corpus so prior-leader
    GEMM candidates AND within-chunk founders both exercise. The reference
    ``cosine`` arbiter makes the two paths decision-identical by construction;
    this pins it against regressions in the chunking bookkeeping."""
    import random

    rng = random.Random(20260712)
    vectors = [tuple(rng.uniform(-1.0, 1.0) for _ in range(8)) for _ in range(300)]
    for threshold in (0.55, 0.8, 0.95):
        expected = leader_clusters(vectors, threshold=threshold)
        for chunk_size in (7, 64, 512):
            assert (
                numpy_leader_clusters(vectors, threshold=threshold, chunk_size=chunk_size)
                == expected
            )


def test_numpy_stop_mid_chunk_yields_the_same_clean_partial_as_pure() -> None:
    ticks: list[int] = []

    def stopped() -> bool:
        return len(ticks) == 3

    clusters = numpy_leader_clusters(
        [RIGHT, NEARLY_RIGHT, UP, (-1.0, 0.0), (0.8, 0.6)],
        threshold=0.9,
        should_stop=stopped,
        progress=lambda: ticks.append(1),
        chunk_size=2,  # the stop lands mid-corpus, across a chunk boundary
    )
    assert ticks == [1, 1, 1]
    assert clusters == [[0, 1], [2], [3], [4]]
