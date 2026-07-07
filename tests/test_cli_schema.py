"""``smartpipe schema`` (rung 4, D22): one call + one repair, stdout never lies."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.conftest import RunCli

if TYPE_CHECKING:
    import respx

CHAT = "http://localhost:11434/api/chat"

GOOD = json.dumps(
    {
        "type": "object",
        "properties": {"vendor": {"type": "string"}},
        "required": ["vendor"],
        "additionalProperties": False,
    }
)


@pytest.fixture(autouse=True)
def local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": content}})


def test_valid_draft_prints_pretty_schema(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply(GOOD))
    code, out, _err = run_cli(["schema", "an invoice with a vendor"])
    assert code == 0
    assert json.loads(out) == json.loads(GOOD)
    assert out.endswith("}\n")
    assert route.call_count == 1  # exactly one call on the happy path


def test_invalid_draft_gets_one_repair(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(CHAT)
    route.side_effect = [
        _reply('{"type": {"not": "a schema"}}'),  # meta-schema rejects this
        _reply(GOOD),
    ]
    code, out, _err = run_cli(["schema", "an invoice"])
    assert code == 0
    assert json.loads(out)["required"] == ["vendor"]
    assert route.call_count == 2


def test_double_failure_is_exit_3_with_empty_stdout(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply("not json at all"))
    code, out, err = run_cli(["schema", "an invoice"])
    assert code == 3
    assert out == ""  # the whole point: a broken schema never reaches the pipe
    assert "couldn't produce a valid JSON Schema" in err
    assert "not json at all" in err  # the attempt is shown for debugging
