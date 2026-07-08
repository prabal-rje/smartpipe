"""Reader mode + positional files (wave 2, item 16)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.conftest import RunCli

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_a_path_first_arg_makes_the_binary_the_reader(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("one\ntwo\n", encoding="utf-8")
    code, out, _err = run_cli(["notes.txt", "--as", "lines"], stdin="")
    assert code == 0
    rows = [json.loads(line) for line in out.splitlines()]
    assert [row["text"] for row in rows] == ["one", "two"]
    assert rows[0]["__source"] == {"path": "notes.txt", "as": "lines", "line": 1}


def test_reader_defaults_to_one_record_per_file(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("whole body\n", encoding="utf-8")
    code, out, _err = run_cli(["notes.txt"], stdin="")
    assert code == 0
    (row,) = [json.loads(line) for line in out.splitlines()]
    assert row["text"] == "whole body\n"  # a file crate keeps its bytes
    assert row["__source"] == {"path": "notes.txt", "as": "file"}


def test_a_glob_first_arg_makes_the_binary_the_reader(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented reader form `smartpipe 'logs/*.jsonl'` (read_cmd epilog,
    the-item.md, learn/6): a quoted glob routes to the reader, which expands
    the pattern itself (sorted, deduped, no-match = loud error — D43)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "a.jsonl").write_text('{"text": "one"}\n', encoding="utf-8")
    (tmp_path / "logs" / "b.jsonl").write_text('{"text": "two"}\n', encoding="utf-8")
    code, out, _err = run_cli(["logs/*.jsonl"], stdin="")
    assert code == 0
    rows = [json.loads(line) for line in out.splitlines()]
    assert [row["text"] for row in rows] == ["one", "two"]


def test_an_unmatched_glob_is_the_readers_loud_no_match_error(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    code, _out, err = run_cli(["logs/*.jsonl"], stdin="")
    assert code == 64
    assert "no files matched" in err


def test_a_spaced_prompt_with_glob_chars_keeps_the_dual_interpretation_error(
    run_cli: RunCli,
) -> None:
    code, _out, err = run_cli(["summarize this?"], stdin="")
    assert code == 64
    assert "no verb 'summarize this?', no file" in err


def test_unknown_arg_is_the_dual_interpretation_error(run_cli: RunCli) -> None:
    code, _out, err = run_cli(["definitely-not-a-verb-or-file"], stdin="")
    assert code == 64
    assert "no verb 'definitely-not-a-verb-or-file', no file" in err
    assert "quote your prompt" in err


def test_dot_slash_forces_the_file_even_when_it_shadows_a_verb(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "map").write_text("shadowed\n", encoding="utf-8")
    code, out, _err = run_cli(["./map"], stdin="")
    assert code == 0
    (row,) = [json.loads(line) for line in out.splitlines()]
    assert row["text"] == "shadowed\n"


def test_positional_files_feed_map_like_dash_dash_in(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    import respx

    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("alpha\n", encoding="utf-8")
    with respx.mock:
        respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(
                200, json={"message": {"role": "assistant", "content": "OK"}}
            )
        )
        code, out, _err = run_cli(["map", "summarize", "a.txt"], stdin="")
    assert code == 0
    assert out == "OK\n"


def test_two_missing_positionals_hint_at_quoting(run_cli: RunCli) -> None:
    code, _out, err = run_cli(["map", "summarize", "this", "please"], stdin="")
    assert code == 64
    assert "aren't files on disk" in err
    assert "needs quotes" in err


def test_the_reader_still_wins_for_existing_verb_named_files(run_cli: RunCli) -> None:
    # regression guard for the glob change: a bare verb name stays a verb
    code, _out, err = run_cli(["map"], stdin="")
    assert code == 64  # map without a prompt is a usage error, not a file read
    assert "no files matched" not in err
