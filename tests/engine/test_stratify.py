"""Largest-remainder (Hamilton) allocation for stratified sampling (item 65c)."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from smartpipe.engine.stratify import allocate


def test_exact_proportions_need_no_remainder() -> None:
    assert allocate(10, {"a": 60, "b": 40}) == {"a": 6, "b": 4}


def test_largest_remainder_takes_the_leftover_slot() -> None:
    # quotas 4.1667 / 4.1667 / 1.6667: floors 4+4+1, the leftover slot goes
    # to c (largest fractional remainder), so the total is exactly N
    assert allocate(10, {"a": 5, "b": 5, "c": 2}) == {"a": 4, "b": 4, "c": 2}


def test_remainder_ties_use_seeded_order_not_first_seen_order() -> None:
    forward = allocate(5, {"a": 3, "b": 3}, seed=9)
    reverse = allocate(5, {"b": 3, "a": 3}, seed=9)
    assert forward == reverse
    winners = {
        next(key for key, count in allocate(5, {"a": 3, "b": 3}, seed=seed).items() if count == 3)
        for seed in range(32)
    }
    assert winners == {"a", "b"}


def test_a_request_covering_everything_takes_everything() -> None:
    assert allocate(10, {"a": 2, "b": 3}) == {"a": 2, "b": 3}
    assert allocate(5, {"a": 2, "b": 3}) == {"a": 2, "b": 3}


def test_tiny_strata_can_receive_zero() -> None:
    # proportional means proportional: 1 row in 101 gets no slot at N=10
    taken = allocate(10, {"a": 100, "b": 1})
    assert taken == {"a": 10, "b": 0}


def test_empty_counts_allocate_nothing() -> None:
    assert allocate(5, {}) == {}


@given(
    total=st.integers(min_value=1, max_value=200),
    counts=st.dictionaries(
        st.text(min_size=1, max_size=3), st.integers(min_value=1, max_value=50), max_size=8
    ),
)
def test_allocation_invariants(total: int, counts: dict[str, int]) -> None:
    taken = allocate(total, counts)
    assert set(taken) == set(counts)
    # never over-draws a stratum, and the total is exactly N when possible
    assert all(0 <= taken[key] <= counts[key] for key in counts)
    assert sum(taken.values()) == min(total, sum(counts.values()))
