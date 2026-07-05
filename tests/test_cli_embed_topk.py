"""Full-stack embed + top_k: real CLI → container → Ollama embedding adapter, HTTP mocked."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.conftest import RunCli

if TYPE_CHECKING:
    import respx

EMBED = "http://localhost:11434/api/embed"


@pytest.fixture(autouse=True)
def embed_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMPIPE_EMBED_MODEL", "ollama/nomic-embed-text")


def _embeddings(*vectors: list[float]) -> httpx.Response:
    return httpx.Response(200, json={"embeddings": list(vectors)})


def test_embed_emits_ndjson(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(EMBED).side_effect = [
        _embeddings([0.1, 0.2]),
        _embeddings([0.3, 0.4]),
    ]
    code, out, _err = run_cli(["embed", "--concurrency", "1"], stdin="alpha\nbeta\n")
    assert code == 0
    lines = out.splitlines()
    assert json.loads(lines[0]) == {"text": "alpha", "vector": [0.1, 0.2], "source": "-"}
    assert json.loads(lines[1]) == {"text": "beta", "vector": [0.3, 0.4], "source": "-"}


def test_top_k_ranks(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    # query embedded first, then each item (concurrency 1 → deterministic call order)
    respx_mock.post(EMBED).side_effect = [
        _embeddings([1.0, 0.0]),  # query "target"
        _embeddings([1.0, 0.0]),  # "match"  → identical → top
        _embeddings([0.0, 1.0]),  # "other"  → orthogonal
    ]
    code, out, _err = run_cli(
        ["top_k", "1", "--near", "target", "--concurrency", "1"],
        stdin="match\nother\n",
    )
    assert code == 0
    assert out.splitlines()[0].startswith("match\t")


def test_top_k_over_embed_pipeline(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    # feed a precomputed .embeddings file to top_k: only the query is embedded
    respx_mock.post(EMBED).mock(return_value=_embeddings([1.0, 0.0]))
    stdin = (
        '{"text": "close", "vector": [1.0, 0.0], "source": "a"}\n'
        '{"text": "far", "vector": [0.0, 1.0], "source": "b"}\n'
    )
    code, out, _err = run_cli(["top_k", "--near", "q", "--threshold", "0.9"], stdin=stdin)
    assert code == 0
    record = json.loads(out.strip())
    assert record["text"] == "close"
    assert record["_score"] == 1.0


def test_top_k_alias_top_dash_k(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(EMBED).side_effect = [_embeddings([1.0, 0.0]), _embeddings([1.0, 0.0])]
    code, _out, _err = run_cli(["top-k", "1", "--near", "q", "--concurrency", "1"], stdin="x\n")
    assert code == 0


def test_top_k_without_k_or_threshold_is_usage_error(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(EMBED).mock(return_value=_embeddings([1.0, 0.0]))
    code, _out, err = run_cli(["top_k", "--near", "q"], stdin="a\n")
    assert code == 64
    assert "needs a number" in err


def test_embed_partial_failure_exits_1(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    # first item embeds; second returns a malformed embeddings shape → skip → exit 1
    respx_mock.post(EMBED).side_effect = [
        _embeddings([0.1, 0.2]),
        httpx.Response(200, json={"embeddings": [["not", "numbers"]]}),
    ]
    code, out, err = run_cli(["embed", "--concurrency", "1"], stdin="ok\nbroken\n")
    assert code == 1
    assert json.loads(out.strip())["text"] == "ok"
    assert "skipped: line 2" in err


def test_top_k_skipped_embedding_exits_1(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    # query ok, item 1 ok, item 2 malformed → skipped; one ranked + one skipped → exit 1
    respx_mock.post(EMBED).side_effect = [
        _embeddings([1.0, 0.0]),  # query
        _embeddings([1.0, 0.0]),  # item "good"
        httpx.Response(200, json={"embeddings": [["bad"]]}),  # item "broken"
    ]
    code, out, err = run_cli(
        ["top_k", "5", "--near", "q", "--concurrency", "1"], stdin="good\nbroken\n"
    )
    assert code == 1
    assert out.splitlines()[0].startswith("good\t")
    assert "skipped: line 2" in err
