"""Inter-rater agreement math (item 65b), verified against the literature.

Worked examples pinned here:
- Cohen's kappa: the classic 2x2 from Cohen (1960) as worked on the
  Wikipedia "Cohen's kappa" page: cells [[20,5],[10,15]] over 50 items give
  po=0.70, pe=0.50, kappa=0.40; and [[45,15],[25,15]] over 100 items give
  po=0.60, pe=0.54, kappa=0.06/0.46 = 0.1304....
- Krippendorff's alpha (nominal): both numerical examples from Krippendorff
  (2011), "Computing Krippendorff's Alpha-Reliability" (University of
  Pennsylvania): the binary Meg/Owen 2-by-10 matrix (alpha = 0.095) and the
  nominal Ben/Gerry 2-by-12 matrix (alpha = 0.692).
"""

from __future__ import annotations

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.engine.agreement import (
    AgreementStats,
    LabelFile,
    agreement,
    compare_labels,
)


def _pairs(*groups: tuple[str, str, int]) -> list[tuple[str, str]]:
    """Expand (label_a, label_b, count) cells into a flat pair list."""
    return [(a, b) for a, b, count in groups for _ in range(count)]


# --- the coefficients, against the literature ---------------------------------------


def test_cohen_kappa_matches_the_cohen_1960_worked_example() -> None:
    # [[20,5],[10,15]] over 50 items: po = 35/50 = 0.70,
    # pe = (25/50)(30/50) + (25/50)(20/50) = 0.30 + 0.20 = 0.50,
    # kappa = (0.70 - 0.50)/(1 - 0.50) = 0.40
    stats = agreement(
        _pairs(("yes", "yes", 20), ("yes", "no", 5), ("no", "yes", 10), ("no", "no", 15))
    )
    assert stats.n == 50
    assert stats.observed == pytest.approx(0.70)
    assert stats.kappa == pytest.approx(0.40)


def test_cohen_kappa_matches_the_second_wikipedia_worked_example() -> None:
    # [[45,15],[25,15]] over 100 items: po = 0.60, pe = 0.6*0.7 + 0.4*0.3 = 0.54,
    # kappa = 0.06/0.46 = 0.130434...
    stats = agreement(
        _pairs(("yes", "yes", 45), ("yes", "no", 15), ("no", "yes", 25), ("no", "no", 15))
    )
    assert stats.kappa == pytest.approx(0.06 / 0.46)


def test_krippendorff_alpha_matches_the_binary_meg_owen_example() -> None:
    # Krippendorff (2011) section A: Meg = 0100000010, Owen = 1110010000,
    # coincidences o01 = o10 = 4, n0 = 14, n1 = 6, n = 20:
    # alpha = 1 - (20-1) * 4/(14*6) = 0.095238... (the paper prints 0.095)
    meg = "0100000010"
    owen = "1110010000"
    stats = agreement(list(zip(meg, owen, strict=True)))
    assert stats.alpha == pytest.approx(1 - 19 * 4 / 84)
    assert round(stats.alpha or 0.0, 3) == 0.095


def test_krippendorff_alpha_matches_the_nominal_ben_gerry_example() -> None:
    # Krippendorff (2011) section B: 12 units,
    # Ben   = a a b b d c c c e d d a
    # Gerry = b a b b b c c c e d d d
    # margins n_a=4, n_b=6, n_c=6, n_d=6, n_e=2, n=24; alpha = 0.692
    ben = ["a", "a", "b", "b", "d", "c", "c", "c", "e", "d", "d", "a"]
    gerry = ["b", "a", "b", "b", "b", "c", "c", "c", "e", "d", "d", "d"]
    stats = agreement(list(zip(ben, gerry, strict=True)))
    assert round(stats.alpha or 0.0, 3) == 0.692
    assert stats.observed == pytest.approx(9 / 12)


def test_perfect_agreement_with_two_classes_scores_one_everywhere() -> None:
    stats = agreement(_pairs(("x", "x", 3), ("y", "y", 3)))
    assert stats.observed == 1.0
    assert stats.kappa == 1.0
    assert stats.alpha == 1.0


def test_single_class_agreement_is_honest_about_undefined_coefficients() -> None:
    # every rating identical: po = pe = 1 for kappa (0/0) and De = 0 for
    # alpha - both undefined, reported as None (null in JSON), never NaN
    stats = agreement(_pairs(("x", "x", 5)))
    assert stats.observed == 1.0
    assert stats.kappa is None
    assert stats.alpha is None


def test_systematic_total_disagreement_defines_both_coefficients() -> None:
    # A always says x, B always says y: po = 0, pe = 0 -> kappa = 0;
    # alpha goes negative (systematic disagreement is worse than chance)
    stats = agreement(_pairs(("x", "y", 4)))
    assert stats.observed == 0.0
    assert stats.kappa == 0.0
    assert stats.alpha is not None and stats.alpha < 0


def test_confusion_matrix_counts_every_observed_cell() -> None:
    stats = agreement(_pairs(("x", "x", 3), ("x", "y", 2), ("y", "y", 1)))
    assert stats.matrix == (("x", "x", 3), ("x", "y", 2), ("y", "y", 1))
    assert stats.labels == ("x", "y")


def test_matrix_orders_by_count_then_labels() -> None:
    stats = agreement(_pairs(("b", "b", 2), ("a", "a", 2), ("a", "b", 1)))
    assert stats.matrix == (("a", "a", 2), ("b", "b", 2), ("a", "b", 1))


# --- alignment + extraction ----------------------------------------------------------


def _file(name: str, *records: dict[str, object]) -> LabelFile:
    return LabelFile(name=name, records=tuple(records))


def test_row_order_alignment_needs_equal_counts() -> None:
    a = _file("a.jsonl", {"label": "x"}, {"label": "y"})
    b = _file("b.jsonl", {"label": "x"})
    with pytest.raises(UsageFault, match=r"2 rows.*1 row"):
        compare_labels(a, b, on=None, label="label")


def test_row_order_mode_compares_in_order() -> None:
    a = _file("a.jsonl", {"label": "x"}, {"label": "y"})
    b = _file("b.jsonl", {"label": "x"}, {"label": "x"})
    comparison = compare_labels(a, b, on=None, label="label")
    assert comparison.stats.n == 2
    assert comparison.stats.observed == pytest.approx(0.5)


def test_key_alignment_pairs_rows_regardless_of_order() -> None:
    a = _file("a.jsonl", {"id": 1, "label": "x"}, {"id": 2, "label": "y"})
    b = _file("b.jsonl", {"id": 2, "label": "y"}, {"id": 1, "label": "x"})
    comparison = compare_labels(a, b, on="id", label="label")
    assert comparison.stats.observed == 1.0
    assert comparison.only_a == 0 and comparison.only_b == 0


def test_keys_on_one_side_only_are_excluded_and_counted() -> None:
    a = _file("a.jsonl", {"id": 1, "label": "x"}, {"id": 3, "label": "x"})
    b = _file("b.jsonl", {"id": 1, "label": "x"}, {"id": 4, "label": "x"}, {"id": 5, "label": "x"})
    comparison = compare_labels(a, b, on="id", label="label")
    assert comparison.stats.n == 1
    assert comparison.only_a == 1  # id 3
    assert comparison.only_b == 2  # ids 4, 5


def test_rows_missing_the_key_are_excluded_and_counted() -> None:
    a = _file("a.jsonl", {"id": 1, "label": "x"}, {"label": "y"})
    b = _file("b.jsonl", {"id": 1, "label": "x"})
    comparison = compare_labels(a, b, on="id", label="label")
    assert comparison.stats.n == 1
    assert comparison.missing_key_a == 1
    assert comparison.missing_key_b == 0


def test_duplicate_keys_fault_loudly() -> None:
    a = _file("a.jsonl", {"id": 1, "label": "x"}, {"id": 1, "label": "y"})
    b = _file("b.jsonl", {"id": 1, "label": "x"})
    with pytest.raises(UsageFault, match="duplicate"):
        compare_labels(a, b, on="id", label="label")


def test_unlabeled_rows_are_excluded_and_counted_per_side() -> None:
    # a null label IS an unlabeled row - excluded like a missing field
    a = _file("a.jsonl", {"id": 1, "label": "x"}, {"id": 2, "label": None}, {"id": 3, "label": "x"})
    b = _file("b.jsonl", {"id": 1, "label": "x"}, {"id": 2, "label": "y"}, {"id": 3})
    comparison = compare_labels(a, b, on="id", label="label")
    assert comparison.stats.n == 1
    assert comparison.unlabeled_a == 1
    assert comparison.unlabeled_b == 1


def test_label_field_absent_everywhere_faults_with_a_field_census() -> None:
    a = _file("a.jsonl", {"id": 1, "sentiment": "pos"}, {"id": 2, "sentiment": "neg"})
    b = _file("b.jsonl", {"id": 1, "label": "x"}, {"id": 2, "label": "y"})
    with pytest.raises(UsageFault, match=r"no field 'label' in a\.jsonl") as caught:
        compare_labels(a, b, on="id", label="label")
    assert "id (2)" in str(caught.value)
    assert "sentiment (2)" in str(caught.value)


def test_no_comparable_pairs_faults_loudly() -> None:
    a = _file("a.jsonl", {"id": 1, "label": "x"})
    b = _file("b.jsonl", {"id": 2, "label": "y"})
    with pytest.raises(UsageFault, match="no comparable pairs"):
        compare_labels(a, b, on="id", label="label")


def test_non_string_labels_compare_canonically() -> None:
    # numbers/booleans canonicalize to their JSON text, so 1 == 1 across
    # sides even when one file wrote it as a string
    a = _file("a.jsonl", {"label": 1}, {"label": True})
    b = _file("b.jsonl", {"label": "1"}, {"label": True})
    comparison = compare_labels(a, b, on=None, label="label")
    assert comparison.stats.observed == 1.0
    assert comparison.stats.labels == ("1", "true")


def test_stats_type_is_frozen() -> None:
    stats = agreement([("x", "y")])
    assert isinstance(stats, AgreementStats)
    with pytest.raises(AttributeError):
        stats.n = 5  # type: ignore[misc]
