"""Blocking-recall eval for ``join`` (D21) — non-gating, deterministic.

Measures what fraction of true matches survive the blocking stage at each ``k``
on a synthetic corpus with known ground truth (8 categories x 5 items per side;
an item's true matches are its category's right-side rows). Deterministic fake
embeddings: category anchors on the unit circle plus seeded within-category
noise — a controlled stand-in for "similar things embed nearby".

This justifies the ``--k 5`` default in docs/verbs/join.md and demonstrates the
spot-check recipe. It is NOT a gate: real recall depends on your embedding
model and your data — rerun the spot-check there.

Run: ``make join-eval`` (or ``uv run python scripts/join_eval.py``).
"""

from __future__ import annotations

import math
import random

from sempipe.engine.blocking import build_index, candidates

CATEGORIES = 8
PER_SIDE = 5  # items per category per side -> 40 x 40
KS = (1, 3, 5, 10)
NOISE = 0.35  # radians of within-category spread — overlapping but separable


def _vector(rng: random.Random, category: int) -> tuple[float, float]:
    angle = (2 * math.pi * category / CATEGORIES) + rng.uniform(-NOISE, NOISE)
    return (math.cos(angle), math.sin(angle))


def main() -> None:
    rng = random.Random(42)
    left = [
        (category, _vector(rng, category))
        for category in range(CATEGORIES)
        for _ in range(PER_SIDE)
    ]
    right = [
        (category, _vector(rng, category))
        for category in range(CATEGORIES)
        for _ in range(PER_SIDE)
    ]
    index = build_index([vector for _category, vector in right])

    print("blocking recall@k on the synthetic 40x40 corpus (8 categories):")
    print("k    recall   (fraction of true matches that reach the judge)")
    for k in KS:
        hits = 0
        truths = 0
        for category, vector in left:
            retrieved = {
                position for position, _score in candidates(vector, index, k=k, threshold=None)
            }
            true_positions = {i for i, (rc, _v) in enumerate(right) if rc == category}
            hits += len(retrieved & true_positions)
            truths += len(true_positions)
        print(f"{k:<4} {hits / truths:.2f}")
    print("\nspot-check recipe for YOUR data: rerun a sample with --k 20 --threshold 0")
    print("and compare match counts — a jump means the default k is dropping matches.")


if __name__ == "__main__":
    main()
