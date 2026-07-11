"""The agree verb: two label files in, agreement stats + confusion out."""

from __future__ import annotations

import contextlib
import csv
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
    columns = {
        "n",
        "observed_agreement",
        "cohen_kappa",
        "krippendorff_alpha",
        "label_a",
        "label_b",
        "count",
    }
    assert all(set(row) == columns for row in rows)
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
    # agree compares records, not permissively-sniffed input lines.
    a = tmp_path / "a.txt"
    a.write_text("spam\nham\n", encoding="utf-8")
    b = _write(tmp_path / "b.jsonl", [{"label": "spam"}, {"label": "ham"}])
    with pytest.raises(UsageFault, match=r"invalid record.*a\.txt.*line 1"):
        _run(AgreeRequest(file_a=a, file_b=b))


def test_invalid_utf8_is_a_usage_fault_with_file_and_line(tmp_path: Path) -> None:
    a = tmp_path / "a.jsonl"
    a.write_bytes(b'{"label":"x"}\n\xff\n')
    b = _write(tmp_path / "b.jsonl", [{"label": "x"}, {"label": "y"}])
    with pytest.raises(UsageFault, match=r"invalid UTF-8.*a\.jsonl.*line 2"):
        _run(AgreeRequest(file_a=a, file_b=b))


def test_malformed_json_is_a_usage_fault_with_file_and_line(tmp_path: Path) -> None:
    a = tmp_path / "a.jsonl"
    a.write_text('{"label":"x"}\n{"label":}\n', encoding="utf-8")
    b = _write(tmp_path / "b.jsonl", [{"label": "x"}, {"label": "y"}])
    with pytest.raises(UsageFault, match=r"invalid record.*a\.jsonl.*line 2"):
        _run(AgreeRequest(file_a=a, file_b=b))


@pytest.mark.parametrize("mode,delimiter", [("csv", ","), ("tsv", "\t")])
def test_table_outputs_use_the_stable_seven_column_union(
    tmp_path: Path, mode: str, delimiter: str
) -> None:
    from smartpipe.io.writers import OutputFormat

    a = _write(tmp_path / "a.jsonl", [{"label": "x"}, {"label": "y"}])
    b = _write(tmp_path / "b.jsonl", [{"label": "x"}, {"label": "x"}])
    _code, out, err = _run(AgreeRequest(file_a=a, file_b=b, output=OutputFormat(mode)))
    rows = list(csv.reader(io.StringIO(out), delimiter=delimiter))
    assert rows[0] == [
        "n",
        "observed_agreement",
        "cohen_kappa",
        "krippendorff_alpha",
        "label_a",
        "label_b",
        "count",
    ]
    assert all(len(row) == 7 for row in rows[1:])
    assert {tuple(row[4:]) for row in rows[2:]} == {("x", "x", "1"), ("y", "x", "1")}
    assert "appeared after the header" not in err
