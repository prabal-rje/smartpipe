from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smartpipe.engine.ranking import cosine, rank, select, unit_score

_FINITE = st.floats(min_value=-100, max_value=100, allow_nan=False, allow_infinity=False, width=32)


# --- cosine -------------------------------------------------------------------


def test_cosine_of_identical_is_one() -> None:
    assert cosine((1.0, 2.0, 3.0), (1.0, 2.0, 3.0)) == pytest.approx(1.0)


def test_cosine_of_opposite_is_minus_one() -> None:
    assert cosine((1.0, 0.0), (-1.0, 0.0)) == pytest.approx(-1.0)


def test_cosine_of_orthogonal_is_zero() -> None:
    assert cosine((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)


def test_zero_vector_is_zero_not_nan() -> None:
    result = cosine((0.0, 0.0), (1.0, 2.0))
    assert result == 0.0
    assert not math.isnan(result)


# --- unit_score ---------------------------------------------------------------


def test_unit_score_maps_to_zero_one() -> None:
    assert unit_score(1.0) == pytest.approx(1.0)
    assert unit_score(-1.0) == pytest.approx(0.0)
    assert unit_score(0.0) == pytest.approx(0.5)


@given(cos=st.floats(min_value=-1, max_value=1))
def test_unit_score_always_in_range(cos: float) -> None:
    assert 0.0 <= unit_score(cos) <= 1.0


# --- rank ---------------------------------------------------------------------


def test_rank_sorts_by_similarity_descending() -> None:
    query = (1.0, 0.0)
    vectors = [(0.0, 1.0), (1.0, 0.0), (0.7, 0.7)]  # orthogonal, identical, 45°
    ranked = rank(query, vectors)
    assert [index for index, _ in ranked] == [1, 2, 0]


def test_rank_breaks_ties_by_index() -> None:
    query = (1.0, 0.0)
    vectors = [(1.0, 0.0), (1.0, 0.0)]  # equal scores
    ranked = rank(query, vectors)
    assert [index for index, _ in ranked] == [0, 1]


def test_rank_scores_are_unit() -> None:
    ranked = rank((1.0, 0.0), [(1.0, 0.0), (0.0, 1.0)])
    assert ranked[0][1] == pytest.approx(1.0)
    assert ranked[1][1] == pytest.approx(0.5)


# --- select -------------------------------------------------------------------


def _ranked() -> tuple[tuple[int, float], ...]:
    return ((3, 0.9), (1, 0.8), (0, 0.6), (2, 0.4))


def test_select_top_k() -> None:
    assert select(_ranked(), k=2, threshold=None) == ((3, 0.9), (1, 0.8))


def test_select_threshold() -> None:
    assert select(_ranked(), k=None, threshold=0.7) == ((3, 0.9), (1, 0.8))


def test_select_k_and_threshold_intersect() -> None:
    # up to 3 items, but only those >= 0.5
    assert select(_ranked(), k=3, threshold=0.5) == ((3, 0.9), (1, 0.8), (0, 0.6))


def test_select_neither_returns_all() -> None:
    assert select(_ranked(), k=None, threshold=None) == _ranked()


# --- properties ---------------------------------------------------------------


@given(a=st.lists(_FINITE, min_size=1, max_size=8), b=st.lists(_FINITE, min_size=1, max_size=8))
def test_cosine_is_symmetric_and_bounded(a: list[float], b: list[float]) -> None:
    if len(a) != len(b):
        b = (b + a)[: len(a)]
    forward = cosine(tuple(a), tuple(b))
    assert forward == pytest.approx(cosine(tuple(b), tuple(a)))
    assert -1.0001 <= forward <= 1.0001
    assert not math.isnan(forward)


@given(
    scores=st.lists(st.floats(min_value=0, max_value=1), min_size=1, max_size=20),
    k=st.integers(min_value=1, max_value=25),
)
def test_thresholded_is_subset_of_unthresholded(scores: list[float], k: int) -> None:
    ranked = rank((1.0,), [(s,) for s in scores])  # 1-D: cosine is sign of s
    with_threshold = set(select(ranked, k=k, threshold=0.6))
    without = set(select(ranked, k=k, threshold=None))
    assert with_threshold <= without
