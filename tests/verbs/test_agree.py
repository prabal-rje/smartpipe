"""The agree verb: two label files in, agreement stats + confusion out."""

from __future__ import annotations

import contextlib
import io
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.verbs.agree import AgreeRequest, run_agree

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _run(request: AgreeRequest) -> tuple[ExitCode, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = run_agree(request, stdout=out)
    return code, out.getvalue(), err.getvalue()


def test_row_order_agreement_emits_summary_then_matrix(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.jsonl", [{"label": "x"}, {"label": "y"}, {"label": "x"}])
    b = _write(tmp_path / "b.jsonl", [{"label": "x"}, {"label": "x"}, {"label": "x"}])
    code, out, _err = _run(AgreeRequest(file_a=a, file_b=b))
    assert code is ExitCode.OK
    rows = [json.loads(line) for line in out.splitlines()]
    summary = rows[0]
    assert summary["n"] == 3
    assert summary["observed_agreement"] == round(2 / 3, 4)  # 4-decimal output contract
    assert {"cohen_kappa", "krippendorff_alpha"} <= set(summary)
    cells = {(row["label_a"], row["label_b"]): row["count"] for row in rows[1:]}
    assert cells == {("x", "x"): 2, ("y", "x"): 1}


def test_key_alignment_notes_the_exclusions(tmp_path: Path) -> None:
    a = _write(
        tmp_path / "a.jsonl",
        [{"id": 1, "label": "x"}, {"id": 2, "label": "y"}, {"id": 9, "label": "x"}],
    )
    b = _write(
        tmp_path / "b.jsonl",
        [{"id": 2, "label": "y"}, {"id": 1, "label": "x"}, {"id": 7, "label": "x"}],
    )
    code, out, err = _run(AgreeRequest(file_a=a, file_b=b, on="id"))
    assert code is ExitCode.OK
    summary = json.loads(out.splitlines()[0])
    assert summary["n"] == 2
    assert summary["observed_agreement"] == 1.0
    assert "1 key only in" in err and "1 only in" in err


def test_unlabeled_rows_are_noted_not_fatal(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.jsonl", [{"id": 1, "label": "x"}, {"id": 2}])
    b = _write(tmp_path / "b.jsonl", [{"id": 1, "label": "x"}, {"id": 2, "label": "y"}])
    code, out, err = _run(AgreeRequest(file_a=a, file_b=b, on="id"))
    assert code is ExitCode.OK
    assert json.loads(out.splitlines()[0])["n"] == 1
    assert "unlabeled" in err


def test_single_class_kappa_is_null_and_says_why(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.jsonl", [{"label": "x"}, {"label": "x"}])
    b = _write(tmp_path / "b.jsonl", [{"label": "x"}, {"label": "x"}])
    code, out, err = _run(AgreeRequest(file_a=a, file_b=b))
    assert code is ExitCode.OK
    summary = json.loads(out.splitlines()[0])
    assert summary["observed_agreement"] == 1.0
    assert summary["cohen_kappa"] is None  # null, not NaN, not a pretended 1.0
    assert summary["krippendorff_alpha"] is None
    assert "undefined" in err


def test_length_mismatch_without_on_faults_loudly(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.jsonl", [{"label": "x"}, {"label": "y"}])
    b = _write(tmp_path / "b.jsonl", [{"label": "x"}])
    with pytest.raises(UsageFault, match="row-order alignment"):
        _run(AgreeRequest(file_a=a, file_b=b))


def test_missing_file_faults_with_the_path(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.jsonl", [{"label": "x"}])
    with pytest.raises(UsageFault, match="no such file"):
        _run(AgreeRequest(file_a=a, file_b=tmp_path / "nope.jsonl"))


def test_absent_label_field_faults_with_a_census(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.jsonl", [{"id": 1, "sentiment": "pos"}])
    b = _write(tmp_path / "b.jsonl", [{"id": 1, "label": "x"}])
    with pytest.raises(UsageFault, match="Fields seen"):
        _run(AgreeRequest(file_a=a, file_b=b, on="id"))


def test_plain_text_lines_become_text_records(tmp_path: Path) -> None:
    # a plain line's record form is {"text": ...} (the item law) - so the
    # census fault points at 'text' instead of pretending the file is empty
    a = tmp_path / "a.txt"
    a.write_text("spam\nham\n", encoding="utf-8")
    b = _write(tmp_path / "b.jsonl", [{"label": "spam"}, {"label": "ham"}])
    with pytest.raises(UsageFault, match=r"text \(2\)"):
        _run(AgreeRequest(file_a=a, file_b=b))
