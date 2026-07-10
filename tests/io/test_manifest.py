"""The --manifest collector: run-scoped facts in, one atomic file at run end."""

from __future__ import annotations

import dataclasses
import json
import re
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.io import manifest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def fresh_collector() -> None:
    manifest.reset()


def test_recorders_are_noops_until_begin(tmp_path: Path) -> None:
    manifest.record_model("chat", "ollama/qwen3:8b")
    manifest.record_schema({"type": "object"})
    manifest.record_counts(done=3, skipped=0)
    manifest.finish(ExitCode.OK)  # nothing armed - nothing written, nothing raised
    assert list(tmp_path.iterdir()) == []


def test_begin_finish_writes_the_document(tmp_path: Path) -> None:
    target = tmp_path / "run.json"
    manifest.begin(target, verb="map", argv=("map", "x"), prompt="x")
    manifest.record_model("chat", "ollama/qwen3:8b")
    manifest.record_schema({"type": "object"})
    manifest.record_counts(done=2, skipped=1)
    manifest.finish(ExitCode.PARTIAL)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["verb"] == "map"
    assert document["argv"] == ["map", "x"]
    assert document["models"] == {"chat": "ollama/qwen3:8b"}
    assert document["schema"] == {"type": "object"}
    assert document["items"] == {"in": 3, "succeeded": 2, "skipped": 1, "failed": 1}
    run = document["run"]
    assert run["exit_code"] == 1
    assert run["exit_status"] == "partial"
    stamp = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    assert stamp.match(run["started_at"]) and stamp.match(run["finished_at"])


def test_finish_disarms_the_collector(tmp_path: Path) -> None:
    target = tmp_path / "run.json"
    manifest.begin(target, verb="map", argv=("map",))
    manifest.finish(ExitCode.OK)
    target.unlink()
    manifest.finish(ExitCode.OK)  # already settled - a second finish is a no-op
    assert not target.exists()


def test_a_second_run_overwrites_the_record(tmp_path: Path) -> None:
    target = tmp_path / "run.json"
    manifest.begin(target, verb="map", argv=("map",))
    manifest.finish(ExitCode.OK)
    manifest.begin(target, verb="embed", argv=("embed",))
    manifest.finish(ExitCode.OK)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["verb"] == "embed"  # the file is a record of THIS run


def test_begin_faults_early_on_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(UsageFault, match="does not exist"):
        manifest.begin(tmp_path / "no-such-dir" / "run.json", verb="map", argv=("map",))


def test_write_trouble_warns_and_never_dies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "taken"
    manifest.begin(target, verb="map", argv=("map",))
    target.mkdir()  # the path is now a directory - the atomic replace will fail
    manifest.finish(ExitCode.OK)  # results already shipped: warn, don't mask the exit
    assert "manifest" in capsys.readouterr().err


def test_temperature_matches_the_pinned_completion_default(tmp_path: Path) -> None:
    # D36 pins temperature 0.0 on every request; the manifest must record the
    # same number the wire actually sends.
    from smartpipe.models.base import CompletionRequest

    default = next(
        field.default
        for field in dataclasses.fields(CompletionRequest)
        if field.name == "temperature"
    )
    target = tmp_path / "run.json"
    manifest.begin(target, verb="map", argv=("map",))
    manifest.finish(ExitCode.OK)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["determinism"] == {"temperature": default}
