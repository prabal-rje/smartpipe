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
    from collections.abc import Iterable, Mapping

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
    records: Iterable[Mapping[str, object]]


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


def agreement(pairs: Iterable[tuple[str, str]]) -> AgreementStats:
    """The coefficients for aligned label pairs (side A first)."""
    cells: Counter[tuple[str, str]] = Counter()
    n = 0
    for pair in pairs:
        cells[pair] += 1
        n += 1
    if n == 0:
        raise ValueError("agreement needs at least one comparable pair")
    return _agreement_from_cells(cells, n)


def _agreement_from_cells(cells: Mapping[tuple[str, str], int], n: int) -> AgreementStats:
    """Coefficient calculation from online confusion counts."""
    matches = sum(count for (a, b), count in cells.items() if a == b)
    observed = matches / n
    labels = tuple(sorted({label for pair in cells for label in pair}))
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
    """Align and score online: row-order streams both; key mode indexes B."""
    if on is None:
        return _compare_by_order(a, b, label)
    return _compare_by_key(a, b, on, label)


def _compare_by_order(a: LabelFile, b: LabelFile, label: str) -> Comparison:
    iterator_a = iter(a.records)
    iterator_b = iter(b.records)
    census_a: Counter[str] = Counter()
    census_b: Counter[str] = Counter()
    label_seen_a = label_seen_b = False
    cells: Counter[tuple[str, str]] = Counter()
    unlabeled_a = unlabeled_b = 0
    count = 0
    while True:
        record_a = next(iterator_a, None)
        record_b = next(iterator_b, None)
        if record_a is None or record_b is None:
            count_a = count + (0 if record_a is None else 1) + sum(1 for _ in iterator_a)
            count_b = count + (0 if record_b is None else 1) + sum(1 for _ in iterator_b)
            if count_a != count_b:
                raise UsageFault(
                    f"agree: {a.name} has {_rows(count_a)}, {b.name} has "
                    f"{_rows(count_b)} - row-order alignment needs equal counts\n"
                    "  Give both files a shared key column and align on it: --on id"
                )
            break
        count += 1
        census_a.update(record_a.keys())
        census_b.update(record_b.keys())
        value_a = lookup(record_a, label)
        value_b = lookup(record_b, label)
        label_seen_a = label_seen_a or value_a is not MISSING
        label_seen_b = label_seen_b or value_b is not MISSING
        missing_a, missing_b = _add_pair(cells, value_a, value_b)
        unlabeled_a += missing_a
        unlabeled_b += missing_b
    _require_label_somewhere(a.name, label, label_seen_a, census_a)
    _require_label_somewhere(b.name, label, label_seen_b, census_b)
    return _comparison(
        a,
        b,
        label,
        cells,
        only_a=0,
        only_b=0,
        missing_key_a=0,
        missing_key_b=0,
        unlabeled_a=unlabeled_a,
        unlabeled_b=unlabeled_b,
    )


def _compare_by_key(a: LabelFile, b: LabelFile, on: str, label: str) -> Comparison:
    keyed_b: dict[str, Mapping[str, object]] = {}
    census_b: Counter[str] = Counter()
    label_seen_b = False
    missing_key_b = 0
    for record in b.records:
        census_b.update(record.keys())
        label_seen_b = label_seen_b or lookup(record, label) is not MISSING
        key = canonical_label(lookup(record, on))
        if key is None:
            missing_key_b += 1
            continue
        if key in keyed_b:
            _duplicate_key(b.name, on, key)
        keyed_b[key] = record

    seen_a: set[str] = set()
    census_a: Counter[str] = Counter()
    label_seen_a = False
    missing_key_a = only_a = unlabeled_a = unlabeled_b = 0
    cells: Counter[tuple[str, str]] = Counter()
    for record_a in a.records:
        census_a.update(record_a.keys())
        label_seen_a = label_seen_a or lookup(record_a, label) is not MISSING
        key = canonical_label(lookup(record_a, on))
        if key is None:
            missing_key_a += 1
            continue
        if key in seen_a:
            _duplicate_key(a.name, on, key)
        seen_a.add(key)
        record_b = keyed_b.pop(key, None)
        if record_b is None:
            only_a += 1
            continue
        missing_a, missing_b = _add_pair(
            cells,
            lookup(record_a, label),
            lookup(record_b, label),
        )
        unlabeled_a += missing_a
        unlabeled_b += missing_b

    _require_label_somewhere(a.name, label, label_seen_a, census_a)
    _require_label_somewhere(b.name, label, label_seen_b, census_b)
    return _comparison(
        a,
        b,
        label,
        cells,
        only_a=only_a,
        only_b=len(keyed_b),
        missing_key_a=missing_key_a,
        missing_key_b=missing_key_b,
        unlabeled_a=unlabeled_a,
        unlabeled_b=unlabeled_b,
    )


def _add_pair(cells: Counter[tuple[str, str]], value_a: object, value_b: object) -> tuple[int, int]:
    label_a = canonical_label(value_a)
    label_b = canonical_label(value_b)
    if label_a is not None and label_b is not None:
        cells[label_a, label_b] += 1
    return int(label_a is None), int(label_b is None)


def _comparison(
    a: LabelFile,
    b: LabelFile,
    label: str,
    cells: Counter[tuple[str, str]],
    *,
    only_a: int,
    only_b: int,
    missing_key_a: int,
    missing_key_b: int,
    unlabeled_a: int,
    unlabeled_b: int,
) -> Comparison:
    n = sum(cells.values())
    if n == 0:
        raise UsageFault(
            f"agree: no comparable pairs between {a.name} and {b.name}\n"
            "  Every aligned row was excluded (keys on one side only, or missing "
            f"'{label}' values).\n"
            "  Check --on and --label against the files' actual fields."
        )
    return Comparison(
        stats=_agreement_from_cells(cells, n),
        only_a=only_a,
        only_b=only_b,
        missing_key_a=missing_key_a,
        missing_key_b=missing_key_b,
        unlabeled_a=unlabeled_a,
        unlabeled_b=unlabeled_b,
    )


def _duplicate_key(name: str, on: str, key: str) -> None:
    raise UsageFault(
        f"agree: duplicate key {key!r} in {name} - --on '{on}' must identify each row uniquely"
    )


def _require_label_somewhere(name: str, label: str, seen_label: bool, census: Counter[str]) -> None:
    """Fault with a field census when NO row of a side carries the label."""
    if seen_label:
        return
    seen = ", ".join(f"{name} ({count})" for name, count in _census_order(census))
    raise UsageFault(
        f"agree: no field '{label}' in {name}\n"
        f"  Fields seen: {seen or '(no records)'}\n"
        "  Name the label field: --label FIELD"
    )


def _census_order(census: Counter[str]) -> list[tuple[str, int]]:
    return sorted(census.items(), key=lambda item: (-item[1], item[0]))


def _rows(count: int) -> str:
    return f"{count} row" if count == 1 else f"{count} rows"
