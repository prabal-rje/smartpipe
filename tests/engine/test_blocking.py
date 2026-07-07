"""``engine/blocking`` — candidate selection for join (D21, pure, 100 %)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smartpipe.engine.blocking import RightIndex, build_index, candidates
from smartpipe.engine.ranking import cosine, unit_score

RIGHT = build_index(
    (
        (1.0, 0.0),  # 0 — east
        (0.0, 1.0),  # 1 — north
        (0.7, 0.7),  # 2 — northeast
        (1.0, 0.0),  # 3 — east again (tie with 0)
    )
)


def test_candidates_rank_best_first_with_index_ties() -> None:
    got = candidates((1.0, 0.0), RIGHT, k=3, threshold=None)
    assert [position for position, _score in got] == [0, 3, 2]  # tie 0 vs 3 → lower index


def test_k_caps_the_candidate_count() -> None:
    assert len(candidates((1.0, 0.0), RIGHT, k=1, threshold=None)) == 1


def test_threshold_filters_before_k() -> None:
    got = candidates((1.0, 0.0), RIGHT, k=4, threshold=0.9)
    assert [position for position, _score in got] == [0, 3]  # north/northeast fall out


def test_k_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="k must be >= 1"):
        candidates((1.0, 0.0), RIGHT, k=0, threshold=None)


def test_empty_index_yields_nothing() -> None:
    assert candidates((1.0, 0.0), build_index(()), k=5, threshold=None) == ()


def test_index_is_sized() -> None:
    assert len(RIGHT) == 4
    assert isinstance(RIGHT, RightIndex)


@given(
    vectors=st.lists(
        st.tuples(st.floats(-1, 1, allow_nan=False), st.floats(-1, 1, allow_nan=False)),
        max_size=30,
    ),
    k=st.integers(min_value=1, max_value=6),
)
def test_candidates_are_the_true_top_k(vectors: list[tuple[float, float]], k: int) -> None:
    query = (1.0, 0.5)
    got = candidates(query, build_index(tuple(vectors)), k=k, threshold=None)
    brute = sorted(
        ((i, unit_score(cosine(query, v))) for i, v in enumerate(vectors)),
        key=lambda pair: (-pair[1], pair[0]),
    )[:k]
    assert list(got) == brute
    assert [s for _, s in got] == sorted((s for _, s in got), reverse=True)
