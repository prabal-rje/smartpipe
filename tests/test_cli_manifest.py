"""Full-stack ``--manifest`` tests (item 65a): real CLI, mocked HTTP only.

The manifest is the citable methods-section artifact - these tests pin that
the file lands on every exit path that produced results, records the resolved
models/prompt/schema/counts/receipt, and never rides stdout.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.conftest import RunCli

if TYPE_CHECKING:
    from pathlib import Path

    import respx

CHAT = "http://localhost:11434/api/chat"
EMBED = "http://localhost:11434/api/embed"


@pytest.fixture(autouse=True)
def local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.delenv("SMARTPIPE_OUTPUT", raising=False)


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


def _embeddings(vector: list[float]) -> httpx.Response:
    return httpx.Response(200, json={"embeddings": [vector]})


def test_map_manifest_records_the_run(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply("hola"))
    target = tmp_path / "run.json"
    code, out, err = run_cli(
        ["map", "translate to Spanish", "--manifest", str(target)], stdin="hello\n"
    )
    assert code == 0
    assert out == "hola\n"  # stdout stays sacred - results only
    assert f"manifest: {target}" in err
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["verb"] == "map"
    assert document["argv"] == ["map", "translate to Spanish", "--manifest", str(target)]
    assert document["models"] == {"chat": "ollama/qwen3:8b"}
    assert document["prompt"] == {
        "text": "translate to Spanish",
        "sha256": hashlib.sha256(b"translate to Spanish").hexdigest(),
    }
    assert document["schema"] is None
    assert document["determinism"] == {"temperature": 0.0}
    assert document["items"] == {"in": 1, "succeeded": 1, "skipped": 0, "failed": 0}
    assert document["run"]["exit_code"] == 0
    assert document["run"]["exit_status"] == "ok"


def test_partial_run_manifest_keeps_the_honest_counts(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(CHAT).side_effect = [
        _reply('{"v": "one"}'),
        _reply("not json"),
        _reply("still not json"),
        _reply('{"v": "three"}'),
    ]
    target = tmp_path / "run.json"
    code, _out, _err = run_cli(
        ["map", "Extract {v}", "--concurrency", "1", "--manifest", str(target)],
        stdin="a\nb\nc\n",
    )
    assert code == 1
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 3, "succeeded": 2, "skipped": 1, "failed": 1}
    assert document["run"]["exit_status"] == "partial"
    assert document["schema"] is not None  # braces compiled to a schema - recorded


def test_belted_run_manifest_says_partial(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply("ok"))
    target = tmp_path / "run.json"
    code, _out, _err = run_cli(
        ["map", "x", "--max-calls", "1", "--concurrency", "1", "--manifest", str(target)],
        stdin="a\nb\nc\n",
    )
    assert code == 1  # a capped run never exits 0 (D18)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["run"]["exit_status"] == "partial"


def test_embed_manifest_records_the_embed_role(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTPIPE_EMBED_MODEL", "ollama/nomic-embed-text")
    respx_mock.post(EMBED).mock(return_value=_embeddings([1.0, 0.0]))
    target = tmp_path / "run.json"
    code, _out, _err = run_cli(["embed", "--manifest", str(target)], stdin="a\n")
    assert code == 0
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["verb"] == "embed"
    assert document["models"]["embed"] == "ollama/nomic-embed-text"
    # embed also RESOLVES a chat model for the conversion ladder (captions);
    # the manifest records resolutions, so the role appears here too
    assert document["models"]["chat"] == "ollama/qwen3:8b"
    assert document["prompt"] is None
    assert document["items"] == {"in": 1, "succeeded": 1, "skipped": 0, "failed": 0}


def test_missing_manifest_directory_faults_before_any_spend(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply("never"))
    code, out, err = run_cli(
        ["map", "x", "--manifest", str(tmp_path / "missing-dir" / "run.json")], stdin="a\n"
    )
    assert code == 64
    assert out == ""
    assert "does not exist" in err
    assert route.call_count == 0  # the fault landed before any model call


def test_setup_fault_leaves_no_manifest(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # no model configured, no ollama listening: exit 2 before any results -
    # there was no run to record
    monkeypatch.delenv("SMARTPIPE_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")  # nothing listens on port 9
    target = tmp_path / "run.json"
    code, _out, _err = run_cli(["map", "x", "--manifest", str(target)], stdin="a\n")
    assert code == 2
    assert not target.exists()


def test_a_second_run_overwrites_the_manifest(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply("first"))
    target = tmp_path / "run.json"
    assert run_cli(["map", "one", "--manifest", str(target)], stdin="a\n")[0] == 0
    assert run_cli(["map", "two", "--manifest", str(target)], stdin="a\nb\n")[0] == 0
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["prompt"]["text"] == "two"  # a record of THIS run
    assert document["items"]["in"] == 2
