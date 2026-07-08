"""Full-stack ``join`` through the real CLI and container (D21), HTTP mocked."""

from __future__ import annotations

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
def local_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.setenv("SMARTPIPE_EMBED_MODEL", "ollama/nomic-embed-text")


def _embeddings(*vectors: list[float]) -> httpx.Response:
    return httpx.Response(200, json={"embeddings": list(vectors)})


def _verdict(match: bool) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": json.dumps({"match": match})}})


def test_join_end_to_end(run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path) -> None:
    right = tmp_path / "products.jsonl"
    right.write_text('{"name": "LaserJet 9"}\n{"name": "Espresso One"}\n', encoding="utf-8")
    respx_mock.post(EMBED).side_effect = [
        _embeddings([1.0, 0.0], [0.0, 1.0]),  # the right side, one chunked call
        _embeddings([0.9, 0.1]),  # left line
    ]
    respx_mock.post(CHAT).mock(return_value=_verdict(True))
    code, out, _err = run_cli(
        [
            "join",
            "ticket {left.text} concerns {right.name}",
            "--right",
            str(right),
            "--k",
            "1",
            "--concurrency",
            "1",
        ],
        stdin="printer smoking\n",
    )
    assert code == 0
    record = json.loads(out.splitlines()[0])
    assert record["left"] == {"text": "printer smoking"}
    assert record["right"] == {"name": "LaserJet 9"}
    assert 0.0 <= record["__score"] <= 1.0


def test_bare_brace_is_exit_64_before_any_call(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    right = tmp_path / "r.jsonl"
    right.write_text("x\n", encoding="utf-8")
    embed_route = respx_mock.post(EMBED).mock(return_value=_embeddings([1.0]))
    code, _out, err = run_cli(
        ["join", "ticket {text} concerns {right.name}", "--right", str(right)], stdin="x\n"
    )
    assert code == 64
    assert "{text} is ambiguous in join" in err
    assert embed_route.call_count == 0  # grammar fails before the first embed


def test_right_dash_is_exit_64(run_cli: RunCli, tmp_path: Path) -> None:
    code, _out, err = run_cli(["join", "x {left.text} y {right.text}", "--right", "-"], stdin="x\n")
    assert code == 64
    assert "stdin is join's left side" in err


def test_missing_right_flag_is_exit_64(run_cli: RunCli) -> None:
    code, _out, err = run_cli(["join", "x {left.text} y {right.text}"], stdin="x\n")
    assert code == 64
    assert "--right" in err


def test_k_zero_is_exit_64(run_cli: RunCli, tmp_path: Path) -> None:
    right = tmp_path / "r.jsonl"
    right.write_text("x\n", encoding="utf-8")
    code, _out, err = run_cli(
        ["join", "x {left.text} y {right.text}", "--right", str(right), "--k", "0"],
        stdin="x\n",
    )
    assert code == 64
    assert "--k must be >= 1, got 0" in err
