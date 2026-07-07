"""Full-stack reduce: real CLI → container → Ollama chat adapter, HTTP mocked."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.conftest import RunCli

if TYPE_CHECKING:
    import respx

CHAT = "http://localhost:11434/api/chat"


@pytest.fixture(autouse=True)
def local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": content}})


def test_reduce_synthesizes_one_result(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply("One executive summary."))
    code, out, _err = run_cli(["reduce", "Summarize these notes"], stdin="note 1\nnote 2\nnote 3\n")
    assert code == 0
    assert out == "One executive summary.\n"


def test_reduce_group_by(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).side_effect = [_reply("A summary"), _reply("B summary")]
    stdin = (
        '{"team": "alpha", "msg": "shipped"}\n'
        '{"team": "beta", "msg": "delayed"}\n'
        '{"team": "alpha", "msg": "tested"}\n'
    )
    code, out, _err = run_cli(["reduce", "Summarize", "--group-by", "team"], stdin=stdin)
    assert code == 0
    records = [json.loads(line) for line in out.splitlines()]
    assert {r["group"] for r in records} == {"alpha", "beta"}


def test_reduce_braces_without_group_by_is_usage_error(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply("x"))
    code, _out, err = run_cli(["reduce", "Summarize {product}"], stdin="a\n")
    assert code == 64
    assert "--group-by" in err
    assert route.call_count == 0
