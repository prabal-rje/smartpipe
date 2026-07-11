"""The --manifest collector: run-scoped facts in, one atomic file at run end."""

from __future__ import annotations

import dataclasses
import json
import os
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
    assert document["items"] == {"in": 3, "succeeded": 2, "skipped": 1, "failed": 0}
    run = document["run"]
    assert run["exit_code"] == 1
    assert run["exit_status"] == "partial"
    stamp = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    assert stamp.match(run["started_at"]) and stamp.match(run["finished_at"])


def test_begin_reserves_one_temp_and_finish_reuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "run.json"
    target.write_text("old manifest", encoding="utf-8")
    real_mkstemp = manifest.tempfile.mkstemp
    reservations: list[str] = []

    def tracked_mkstemp(
        suffix: str | None = None,
        prefix: str | None = None,
        dir: str | os.PathLike[str] | None = None,  # noqa: A002 - tempfile signature
        text: bool = False,
    ) -> tuple[int, str]:
        fd, name = real_mkstemp(suffix=suffix, prefix=prefix, dir=dir, text=text)
        reservations.append(name)
        return fd, name

    monkeypatch.setattr(manifest.tempfile, "mkstemp", tracked_mkstemp)
    manifest.begin(target, verb="map", argv=("map",))

    assert target.read_text(encoding="utf-8") == "old manifest"
    assert len(reservations) == 1
    reserved = tmp_path / os.path.basename(reservations[0])
    assert reserved.exists()

    manifest.finish(ExitCode.OK)

    assert len(reservations) == 1  # finish uses the temp held since begin
    assert not reserved.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["verb"] == "map"


def test_finish_disarms_the_collector(tmp_path: Path) -> None:
    target = tmp_path / "run.json"
    manifest.begin(target, verb="map", argv=("map",))
    manifest.finish(ExitCode.OK)
    target.unlink()
    manifest.finish(ExitCode.OK)  # already settled - a second finish is a no-op
    assert not target.exists()


def test_abandon_disarms_without_writing(tmp_path: Path) -> None:
    target = tmp_path / "run.json"
    manifest.begin(target, verb="graph", argv=("graph",))
    manifest.abandon()
    manifest.finish(ExitCode.OK)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_reset_discards_the_reserved_temp(tmp_path: Path) -> None:
    target = tmp_path / "run.json"
    manifest.begin(target, verb="graph", argv=("graph",))
    assert list(tmp_path.iterdir())

    manifest.reset()

    assert list(tmp_path.iterdir()) == []


def test_discard_reservation_unlinks_the_reserved_temp(tmp_path: Path) -> None:
    # B6: the hard-exit seam — os._exit skips the normal abandon/finish unwind,
    # so this unlinks the 0-byte *.manifest.tmp the reservation held.
    target = tmp_path / "run.json"
    manifest.begin(target, verb="graph", argv=("graph",))
    assert any(p.name.endswith(".manifest.tmp") for p in tmp_path.iterdir())

    manifest.discard_reservation()

    assert list(tmp_path.iterdir()) == []


def test_discard_reservation_is_a_noop_when_unarmed(tmp_path: Path) -> None:
    manifest.discard_reservation()  # nothing armed — must not raise
    assert list(tmp_path.iterdir()) == []


def _alias_path(source: Path, tmp_path: Path, kind: str) -> Path:
    if kind == "canonical":
        nested = tmp_path / "nested"
        nested.mkdir()
        return nested / ".." / source.name
    alias = tmp_path / f"{kind}.json"
    if kind == "hardlink":
        os.link(source, alias)
        return alias
    try:
        alias.symlink_to(source)
    except OSError as exc:  # pragma: no cover - Windows without symlink privilege
        pytest.skip(f"symlinks unavailable: {exc}")
    return alias


@pytest.mark.parametrize("kind", ("canonical", "symlink", "hardlink"))
def test_manifest_alias_guard_preserves_inputs_and_cleans_reservation(
    kind: str, tmp_path: Path
) -> None:
    source = tmp_path / "input.txt"
    original = b"irreplaceable input\n"
    source.write_bytes(original)
    target = _alias_path(source, tmp_path, kind)
    manifest.begin(target, verb="read", argv=("input.txt",))

    with pytest.raises(UsageFault, match="aliases input"):
        manifest.guard_manifest_alias(source, role="input")

    assert source.read_bytes() == original
    assert target.read_bytes() == original
    assert not [path for path in tmp_path.rglob("*") if path.name.endswith(".tmp")]


@pytest.mark.parametrize("kind", ("canonical", "symlink", "hardlink"))
def test_manifest_tree_guard_catches_nested_and_inode_aliases(kind: str, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    inside = vault / "index.md"
    inside.write_text("irreplaceable\n", encoding="utf-8")
    if kind == "canonical":
        target = inside
    else:
        target = tmp_path / f"{kind}.json"
        if kind == "hardlink":
            os.link(inside, target)
        else:
            try:
                target.symlink_to(inside)
            except OSError as exc:  # pragma: no cover - Windows without symlink privilege
                pytest.skip(f"symlinks unavailable: {exc}")
    manifest.begin(target, verb="graph", argv=("graph",))

    with pytest.raises(UsageFault, match="inside --save vault"):
        manifest.guard_manifest_tree(vault, role="--save vault")

    assert inside.read_text(encoding="utf-8") == "irreplaceable\n"
    assert target.read_text(encoding="utf-8") == "irreplaceable\n"
    assert not [path for path in tmp_path.rglob("*") if path.name.endswith(".tmp")]


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


def test_begin_faults_early_when_the_target_is_a_directory(tmp_path: Path) -> None:
    target = tmp_path / "run.json"
    target.mkdir()
    with pytest.raises(UsageFault, match="is a directory"):
        manifest.begin(target, verb="map", argv=("map",))


def test_begin_probes_parent_writability_before_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(*_args: object, **_kwargs: object) -> tuple[int, str]:
        raise PermissionError("read-only directory")

    monkeypatch.setattr(manifest.tempfile, "mkstemp", denied)
    with pytest.raises(UsageFault, match="is not writable"):
        manifest.begin(tmp_path / "run.json", verb="map", argv=("map",))


def test_write_trouble_warns_and_never_dies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "taken"
    manifest.begin(target, verb="map", argv=("map",))
    target.mkdir()  # the path is now a directory - the atomic replace will fail
    manifest.finish(ExitCode.OK)  # results already shipped: warn, don't mask the exit
    assert "manifest" in capsys.readouterr().err
    assert list(tmp_path.glob("*.tmp")) == []


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
