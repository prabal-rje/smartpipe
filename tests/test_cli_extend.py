"""Full-stack ``extend`` flag plumbing: the real CLI entry point → real
AppContainer → real Ollama adapter, HTTP mocked. The merge semantics live in
tests/verbs/test_extend.py; this proves the flags reach the verb.
"""

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
    monkeypatch.delenv("SMARTPIPE_OUTPUT", raising=False)


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


def test_extend_merges_end_to_end(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply('{"sentiment": "neg"}'))
    code, out, _err = run_cli(["extend", "Add {sentiment}"], stdin='{"id": 1}\n')
    assert code == 0
    assert json.loads(out) == {"id": 1, "sentiment": "neg"}


def test_keep_invalid_reaches_the_verb(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).side_effect = [_reply("nope"), _reply("still nope")]
    code, out, err = run_cli(["extend", "Add {sentiment}", "--keep-invalid"], stdin='{"id": 1}\n')
    assert code == 0
    row = json.loads(out)
    assert row["id"] == 1  # the base record survives
    assert row["_invalid"] is True
    assert row["_raw"] == "still nope"
    assert "sentiment" not in row
    assert "skipped" not in err
