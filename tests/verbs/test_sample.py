"""The sample verb: reproducible by default, representative, order-kept."""

from __future__ import annotations

import io
import json
import os
import tempfile

import pytest

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.verbs.sample import SampleRequest, run_sample

CORPUS = "".join(f"row-{n}\n" for n in range(100))


def _run(count: int, seed: int = 0, stdin_text: str = CORPUS) -> tuple[str, str]:
    import contextlib

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = run_sample(
            SampleRequest(count=count, seed=seed), stdin=io.StringIO(stdin_text), stdout=out
        )
    assert code is ExitCode.OK
    return out.getvalue(), err.getvalue()


def test_same_input_same_seed_same_sample() -> None:
    first, _ = _run(10)
    second, _ = _run(10)
    assert first == second  # the default is reproducible, no flags needed


def test_seed_varies_the_sample() -> None:
    default, _ = _run(10)
    other, _ = _run(10, seed=7)
    assert default != other


def test_output_preserves_input_order() -> None:
    out, _ = _run(10)
    numbers = [int(line.split("-")[1]) for line in out.splitlines()]
    assert numbers == sorted(numbers)


def test_small_input_passes_through_with_note() -> None:
    out, err = _run(20, stdin_text="a\nb\nc\n")
    assert out == "a\nb\nc\n"
    assert "3 rows ≤ 20 — all kept" in err


def test_receipt_names_the_seed() -> None:
    _out, err = _run(10)
    assert "10 of 100 (seed 0)" in err


def test_zero_count_is_a_fault() -> None:
    with pytest.raises(UsageFault, match="positive count"):
        _run(0)


# --- stratified sampling: sample N --by FIELD (item 65c) -----------------------------


def _labeled_corpus(*groups: tuple[str, int]) -> str:
    return "".join(
        json.dumps({"label": label, "row": f"{label}-{n}"}) + "\n"
        for label, size in groups
        for n in range(size)
    )


def _run_by(count: int, by: str = "label", seed: int = 0, stdin_text: str = "") -> tuple[str, str]:
    import contextlib

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = run_sample(
            SampleRequest(count=count, seed=seed, by=by),
            stdin=io.StringIO(stdin_text),
            stdout=out,
        )
    assert code is ExitCode.OK
    return out.getvalue(), err.getvalue()


def test_stratified_allocation_is_proportional() -> None:
    out, err = _run_by(10, stdin_text=_labeled_corpus(("a", 80), ("b", 20)))
    labels = [json.loads(line)["label"] for line in out.splitlines()]
    assert len(labels) == 10
    assert labels.count("a") == 8 and labels.count("b") == 2
    assert "2 strata by 'label'" in err


def test_stratified_sample_is_deterministic_and_seed_varies_it() -> None:
    corpus = _labeled_corpus(("a", 50), ("b", 50))
    first, _ = _run_by(10, stdin_text=corpus)
    second, _ = _run_by(10, stdin_text=corpus)
    other, _ = _run_by(10, seed=7, stdin_text=corpus)
    assert first == second
    assert first != other


def test_stratified_output_preserves_input_order() -> None:
    out, _ = _run_by(10, stdin_text=_labeled_corpus(("a", 30), ("b", 30)))
    rows = [json.loads(line)["row"] for line in out.splitlines()]
    a_positions = [int(row.split("-")[1]) for row in rows if row.startswith("a-")]
    assert a_positions == sorted(a_positions)


def test_missing_field_rows_form_their_own_null_stratum() -> None:
    corpus = _labeled_corpus(("a", 40)) + "plain text line\n" * 40
    out, err = _run_by(10, stdin_text=corpus)
    lines = out.splitlines()
    assert len(lines) == 10
    nulls = [line for line in lines if not line.startswith("{")]
    assert len(nulls) == 5  # 50/50 split: the null stratum draws its share
    assert "40 rows lacked 'label' - grouped as null" in err


def test_explicit_null_labels_join_the_null_stratum_without_the_lacked_note() -> None:
    corpus = "".join(json.dumps({"label": None, "row": n}) + "\n" for n in range(20))
    out, err = _run_by(4, stdin_text=corpus)
    assert len(out.splitlines()) == 4
    assert "lacked" not in err  # the field IS there - null is its value


def test_small_stratified_input_keeps_everything_with_the_pinned_note() -> None:
    corpus = _labeled_corpus(("a", 2), ("b", 1))
    out, err = _run_by(20, stdin_text=corpus)
    assert len(out.splitlines()) == 3
    assert "3 rows ≤ 20 — all kept" in err  # the existing wording, unchanged


def test_stratified_total_is_exactly_n() -> None:
    out, _ = _run_by(10, stdin_text=_labeled_corpus(("a", 5), ("b", 5), ("c", 2)))
    assert len(out.splitlines()) == 10


def test_stratified_skips_blank_lines_and_counts_json_rows_lacking_the_field() -> None:
    corpus = "".join(json.dumps({"label": "a", "n": n}) + "\n\n" for n in range(8)) + "".join(
        json.dumps({"other": n}) + "\n" for n in range(4)
    )
    out, err = _run_by(6, stdin_text=corpus)
    assert len(out.splitlines()) == 6
    assert "4 rows lacked 'label' - grouped as null" in err


def test_non_string_field_values_stratify_by_their_json_text() -> None:
    corpus = "".join(json.dumps({"label": n % 2, "n": n}) + "\n" for n in range(40))
    out, err = _run_by(10, stdin_text=corpus)
    labels = [json.loads(line)["label"] for line in out.splitlines()]
    assert labels.count(0) == 5 and labels.count(1) == 5
    assert "2 strata by 'label'" in err


def test_stratum_identity_keeps_json_scalar_types_distinct() -> None:
    values: list[object] = [True, 1, 1.0, "1"]
    corpus = "".join(
        json.dumps({"label": value, "row": f"{type(value).__name__}-{n}"}) + "\n"
        for value in values
        for n in range(10)
    )
    out, err = _run_by(4, stdin_text=corpus)
    rows = [json.loads(line) for line in out.splitlines()]
    assert {type(row["label"]).__name__ for row in rows} == {"bool", "int", "float", "str"}
    assert "4 strata by 'label'" in err


def test_high_cardinality_strata_use_an_owned_temp_store_and_clean_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = tempfile.TemporaryDirectory
    created: list[str] = []

    def tracked(*, prefix: str) -> tempfile.TemporaryDirectory[str]:
        directory = real(prefix=prefix)
        created.append(directory.name)
        return directory

    monkeypatch.setattr(tempfile, "TemporaryDirectory", tracked)
    corpus = "".join(
        json.dumps({"label": f"stratum-{n}", "payload": "x" * 200}) + "\n" for n in range(2_000)
    )
    out, _err = _run_by(25, seed=11, stdin_text=corpus)
    assert len(out.splitlines()) == 25
    assert created and all(not os.path.exists(path) for path in created)
