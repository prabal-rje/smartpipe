"""The custom-verb contract (D39/06): sem discovery + entry-point Protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click
import pytest

from tests.conftest import RunCli

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def verbs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    directory = tmp_path / "sempipe" / "verbs"
    directory.mkdir(parents=True)
    return directory


def test_a_sem_file_becomes_a_command(verbs_dir: Path, run_cli: RunCli) -> None:
    (verbs_dir / "hot.sem").write_text(
        'verb = "where"\npredicate = \'text has "ERROR"\'\n', encoding="utf-8"
    )
    code, out, _err = run_cli(["hot"], stdin="ERROR one\nfine\n")
    assert code == 0
    assert out == "ERROR one\n"


def test_sem_pipelines_work_as_verbs_too(verbs_dir: Path, run_cli: RunCli) -> None:
    (verbs_dir / "triage.sem").write_text(
        '[stage.hot]\nverb = "where"\npredicate = \'text has "ERROR"\'\n\n'
        '[stage.numbers]\nverb = "summarize"\nexpression = "count()"\n',
        encoding="utf-8",
    )
    code, out, _err = run_cli(["triage"], stdin="ERROR a\nfine\nERROR b\n")
    assert code == 0
    assert out.strip() == '{"count":2}'


def test_builtins_always_win(verbs_dir: Path, run_cli: RunCli) -> None:
    (verbs_dir / "where.sem").write_text(
        'verb = "summarize"\nexpression = "count()"\n', encoding="utf-8"
    )
    code, out, _err = run_cli(["where", 'text has "x"'], stdin="x\ny\n")
    assert code == 0
    assert out == "x\n"  # the built-in where ran, not the shadow


def test_custom_verbs_appear_in_help(verbs_dir: Path, run_cli: RunCli) -> None:
    (verbs_dir / "triage.sem").write_text(
        'verb = "summarize"\nexpression = "count()"\n', encoding="utf-8"
    )
    code, out, _err = run_cli(["--help"], stdin="")
    assert code == 0
    assert "triage" in out


def test_broken_entry_point_warns_and_skips(
    monkeypatch: pytest.MonkeyPatch, run_cli: RunCli
) -> None:

    class BrokenPoint:
        name = "redact"

        def load(self) -> object:
            raise RuntimeError("plugin bug")

    def fake_entry_points(*, group: str) -> list[BrokenPoint]:
        assert group == "sempipe.verbs"
        return [BrokenPoint()]

    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    code, _out, err = run_cli(["redact"], stdin="")
    assert code != 0  # unknown command in the end
    assert "failed to load" in err


def test_entry_point_command_loads(monkeypatch: pytest.MonkeyPatch, run_cli: RunCli) -> None:
    @click.command(name="shout")
    def shout_command() -> None:
        click.echo("LOUD")

    class GoodPoint:
        name = "shout"

        def load(self) -> click.Command:
            return shout_command

    def fake_entry_points(*, group: str) -> list[GoodPoint]:
        return [GoodPoint()]

    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    code, out, _err = run_cli(["shout"], stdin="")
    assert code == 0
    assert out == "LOUD\n"
