"""Full-stack ``agree`` tests: real CLI, real files, zero config, zero network."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.conftest import RunCli

if TYPE_CHECKING:
    from pathlib import Path


def _label_file(path: Path, rows: list[dict[str, object]]) -> str:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return str(path)


def test_agree_runs_with_zero_config_and_zero_network(run_cli: RunCli, tmp_path: Path) -> None:
    # no SMARTPIPE_MODEL, no config file, no respx mock: any network call
    # or model resolution would blow up loudly - agree needs neither
    a = _label_file(
        tmp_path / "rater1.jsonl",
        [{"id": 1, "label": "spam"}, {"id": 2, "label": "ham"}, {"id": 3, "label": "spam"}],
    )
    b = _label_file(
        tmp_path / "rater2.jsonl",
        [{"id": 3, "label": "ham"}, {"id": 1, "label": "spam"}, {"id": 2, "label": "ham"}],
    )
    code, out, err = run_cli(["agree", a, b, "--on", "id"])
    assert code == 0
    rows = [json.loads(line) for line in out.splitlines()]
    assert rows[0]["n"] == 3
    assert rows[0]["observed_agreement"] == round(2 / 3, 4)
    assert any(
        row["label_a"] == "spam" and row["label_b"] == "spam" and row["count"] == 1
        for row in rows[1:]
    )
    assert err == ""


def test_agree_custom_label_field(run_cli: RunCli, tmp_path: Path) -> None:
    a = _label_file(tmp_path / "a.jsonl", [{"id": 1, "sentiment": "pos"}])
    b = _label_file(tmp_path / "b.jsonl", [{"id": 1, "sentiment": "pos"}])
    code, out, _err = run_cli(["agree", a, b, "--on", "id", "--label", "sentiment"])
    assert code == 0
    assert json.loads(out.splitlines()[0])["observed_agreement"] == 1.0


def test_agree_length_mismatch_exits_64(run_cli: RunCli, tmp_path: Path) -> None:
    a = _label_file(tmp_path / "a.jsonl", [{"label": "x"}, {"label": "y"}])
    b = _label_file(tmp_path / "b.jsonl", [{"label": "x"}])
    code, out, err = run_cli(["agree", a, b])
    assert code == 64
    assert out == ""
    assert "row-order alignment needs equal counts" in err
    assert "--on id" in err  # the fix is on the screen


def test_agree_absent_label_field_exits_64_with_census(run_cli: RunCli, tmp_path: Path) -> None:
    a = _label_file(tmp_path / "a.jsonl", [{"id": 1, "topic": "x"}])
    b = _label_file(tmp_path / "b.jsonl", [{"id": 1, "label": "x"}])
    code, _out, err = run_cli(["agree", a, b, "--on", "id"])
    assert code == 64
    assert "no field 'label'" in err
    assert "topic (1)" in err
