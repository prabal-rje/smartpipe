"""Proportional stratum allocation (item 65c): largest-remainder rounding.

Pure integer arithmetic (Hamilton apportionment) - no floating point, so the
result is exact: floors of the proportional quotas, then the leftover slots
go to the largest remainders (a seeded digest breaks ties). When the request
covers the whole population, every stratum yields everything.

A stratum can never be allocated more than it holds: with ``total < population``
each quota is strictly below the stratum's count, so ``floor + 1 <= count``;
with ``total >= population`` the allocation IS the count. The verb therefore
has no shortfall path - the property test pins the invariant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["allocate", "tie_digest"]

K = TypeVar("K")


def tie_digest(seed: int, token: str) -> str:
    """Stable pseudorandom rank for a tied stratum (independent of arrival)."""
    import hashlib

    return hashlib.sha256(f"{seed}\0{token}".encode()).hexdigest()


def allocate(total: int, counts: Mapping[K, int], *, seed: int = 0) -> dict[K, int]:
    """How many rows each stratum contributes to a sample of ``total``."""
    population = sum(counts.values())
    if total >= population:
        return dict(counts)  # the whole population fits - everything is kept
    taken = {key: (total * count) // population for key, count in counts.items()}
    remainders = sorted(
        (key for key in counts if (total * counts[key]) % population),
        key=lambda key: (
            -((total * counts[key]) % population),
            tie_digest(seed, repr(key)),
            repr(key),
        ),
    )
    for key in remainders[: total - sum(taken.values())]:
        taken[key] += 1
    assert all(taken[key] <= counts[key] for key in counts)  # provable; belt anyway
    return taken
