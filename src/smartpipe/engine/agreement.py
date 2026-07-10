"""Inter-rater agreement (item 65b): the pure math behind ``smartpipe agree``.

Hand-rolled and literature-verified (tests pin Cohen's 1960 kappa example and
both numerical examples in Krippendorff 2011, "Computing Krippendorff's
Alpha-Reliability"): observed agreement, Cohen's kappa, Krippendorff's alpha
(nominal), and the confusion matrix. Alignment (by key or by row order) and
label extraction live here too - all pure, all loudly faulting on structural
problems (length mismatch, duplicate keys, a label field nobody has).

Degenerate honesty: when every rating is one identical class, kappa is 0/0
and alpha's expected disagreement is zero - both are ``None`` (JSON null),
never NaN, and never a pretended 1.0.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import UsageFault
from smartpipe.engine.fieldpath import MISSING, lookup

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "AgreementStats",
    "Comparison",
    "LabelFile",
    "agreement",
    "canonical_label",
    "compare_labels",
]


@dataclass(frozen=True, slots=True)
class LabelFile:
    """One side of the comparison: a name (for messages) and its records."""

    name: str
    records: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class AgreementStats:
    n: int
    observed: float
    kappa: float | None  # None = undefined (single class), said out loud
    alpha: float | None  # nominal Krippendorff; None mirrors kappa
    labels: tuple[str, ...]
    matrix: tuple[tuple[str, str, int], ...]  # (label_a, label_b, count), heaviest first


@dataclass(frozen=True, slots=True)
class Comparison:
    stats: AgreementStats
    only_a: int  # --on keys present only in file A
    only_b: int
    missing_key_a: int  # rows lacking the --on field
    missing_key_b: int
    unlabeled_a: int  # rows lacking the label field (or labeled null)
    unlabeled_b: int


def canonical_label(value: object) -> str | None:
    """A value's canonical label text; ``None`` for missing/null (unlabeled).

    Strings stay themselves; every other JSON value compares as its JSON
    text, so ``1`` and ``"1"`` agree across a CSV-flavored and a JSON-flavored
    export instead of silently never matching.
    """
    if value is None or value is MISSING:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def agreement(pairs: Sequence[tuple[str, str]]) -> AgreementStats:
    """The coefficients for aligned label pairs (side A first)."""
    n = len(pairs)
    cells = Counter(pairs)
    matches = sum(count for (a, b), count in cells.items() if a == b)
    observed = matches / n
    labels = tuple(sorted({label for pair in pairs for label in pair}))
    matrix = tuple(
        (a, b, count)
        for (a, b), count in sorted(cells.items(), key=lambda item: (-item[1], item[0]))
    )
    return AgreementStats(
        n=n,
        observed=observed,
        kappa=_cohen_kappa(cells, n, observed),
        alpha=_krippendorff_alpha_nominal(cells, n),
        labels=labels,
        matrix=matrix,
    )


def _cohen_kappa(cells: Mapping[tuple[str, str], int], n: int, observed: float) -> float | None:
    """kappa = (po - pe)/(1 - pe); None when pe = 1 (one identical class)."""
    a_marginals: Counter[str] = Counter()
    b_marginals: Counter[str] = Counter()
    for (a, b), count in cells.items():
        a_marginals[a] += count
        b_marginals[b] += count
    expected = sum(a_marginals[label] * b_marginals[label] for label in a_marginals) / (n * n)
    if expected == 1.0:
        return None
    return (observed - expected) / (1.0 - expected)


def _krippendorff_alpha_nominal(cells: Mapping[tuple[str, str], int], n: int) -> float | None:
    """alpha = 1 - Do/De over the coincidence matrix (Krippendorff 2011).

    Two coders, no missing data: every unit contributes both ordered pairs,
    so Do = disagreements/N and De = (n_v^2 - sum n_c^2)/(n_v(n_v - 1)) with
    n_v = 2N pooled values. None when De = 0 (a single pooled class).
    """
    pooled: Counter[str] = Counter()
    disagreements = 0
    for (a, b), count in cells.items():
        pooled[a] += count
        pooled[b] += count
        if a != b:
            disagreements += count
    values = 2 * n
    expected = (values * values - sum(c * c for c in pooled.values())) / (values * (values - 1))
    if expected == 0.0:
        return None
    return 1.0 - (disagreements / n) / expected


def compare_labels(a: LabelFile, b: LabelFile, *, on: str | None, label: str) -> Comparison:
    """Align two label files, extract labels, and score the agreement."""
    _require_label_somewhere(a, label)
    _require_label_somewhere(b, label)
    if on is None:
        aligned = _align_by_order(a, b)
        missing_key_a = missing_key_b = only_a = only_b = 0
    else:
        aligned, only_a, only_b, missing_key_a, missing_key_b = _align_by_key(a, b, on)
    pairs: list[tuple[str, str]] = []
    unlabeled_a = unlabeled_b = 0
    for record_a, record_b in aligned:
        label_a = canonical_label(lookup(record_a, label))
        label_b = canonical_label(lookup(record_b, label))
        if label_a is None:
            unlabeled_a += 1
        if label_b is None:
            unlabeled_b += 1
        if label_a is None or label_b is None:
            continue
        pairs.append((label_a, label_b))
    if not pairs:
        raise UsageFault(
            f"agree: no comparable pairs between {a.name} and {b.name}\n"
            "  Every aligned row was excluded (keys on one side only, or missing "
            f"'{label}' values).\n"
            "  Check --on and --label against the files' actual fields."
        )
    return Comparison(
        stats=agreement(pairs),
        only_a=only_a,
        only_b=only_b,
        missing_key_a=missing_key_a,
        missing_key_b=missing_key_b,
        unlabeled_a=unlabeled_a,
        unlabeled_b=unlabeled_b,
    )


def _align_by_order(
    a: LabelFile, b: LabelFile
) -> list[tuple[Mapping[str, object], Mapping[str, object]]]:
    if len(a.records) != len(b.records):
        raise UsageFault(
            f"agree: {a.name} has {_rows(len(a.records))}, {b.name} has "
            f"{_rows(len(b.records))} - row-order alignment needs equal counts\n"
            "  Give both files a shared key column and align on it: --on id"
        )
    return list(zip(a.records, b.records, strict=True))


def _align_by_key(
    a: LabelFile, b: LabelFile, on: str
) -> tuple[list[tuple[Mapping[str, object], Mapping[str, object]]], int, int, int, int]:
    keyed_a, missing_a = _key_map(a, on)
    keyed_b, missing_b = _key_map(b, on)
    aligned = [(record_a, keyed_b[key]) for key, record_a in keyed_a.items() if key in keyed_b]
    shared = sum(1 for key in keyed_a if key in keyed_b)
    return aligned, len(keyed_a) - shared, len(keyed_b) - shared, missing_a, missing_b


def _key_map(side: LabelFile, on: str) -> tuple[dict[str, Mapping[str, object]], int]:
    keyed: dict[str, Mapping[str, object]] = {}
    missing = 0
    for record in side.records:
        key = canonical_label(lookup(record, on))
        if key is None:
            missing += 1
            continue
        if key in keyed:
            raise UsageFault(
                f"agree: duplicate key {key!r} in {side.name} - "
                f"--on '{on}' must identify each row uniquely"
            )
        keyed[key] = record
    return keyed, missing


def _require_label_somewhere(side: LabelFile, label: str) -> None:
    """Fault with a field census when NO row of a side carries the label."""
    if any(lookup(record, label) is not MISSING for record in side.records):
        return
    census: Counter[str] = Counter(field for record in side.records for field in record)
    seen = ", ".join(f"{name} ({count})" for name, count in _census_order(census))
    raise UsageFault(
        f"agree: no field '{label}' in {side.name}\n"
        f"  Fields seen: {seen or '(no records)'}\n"
        "  Name the label field: --label FIELD"
    )


def _census_order(census: Counter[str]) -> list[tuple[str, int]]:
    return sorted(census.items(), key=lambda item: (-item[1], item[0]))


def _rows(count: int) -> str:
    return f"{count} row" if count == 1 else f"{count} rows"
