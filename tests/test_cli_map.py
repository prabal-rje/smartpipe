"""Full-stack ``map`` tests: the real CLI entry point → real AppContainer →
real Ollama adapter, with only the HTTP endpoint mocked. Proves the wiring the
unit tests (which inject a fake context) deliberately bypass.
"""

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


@pytest.fixture(autouse=True)
def local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.delenv("SEMPIPE_OUTPUT", raising=False)


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


def test_plain_map_end_to_end(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply("hola mundo"))
    code, out, err = run_cli(["map", "translate to Spanish"], stdin="hello world\n")
    assert code == 0
    assert out == "hola mundo\n"
    assert err == ""


def test_structured_map_emits_ndjson(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply('{"vendor": "Acme", "total": 5}'))
    code, out, _err = run_cli(["map", "Extract {vendor, total}"], stdin="Acme $5\n")
    assert code == 0
    assert out == '{"vendor":"Acme","total":5}\n'


def test_partial_failure_exits_1(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    # concurrency=1 makes the call order deterministic: item a → ok, item b →
    # invalid JSON twice (original + repair) → skip, item c → ok.
    respx_mock.post(CHAT).side_effect = [
        _reply('{"v": "one"}'),
        _reply("not json"),
        _reply("still not json"),
        _reply('{"v": "three"}'),
    ]
    code, out, err = run_cli(["map", "Extract {v}", "--concurrency", "1"], stdin="a\nb\nc\n")
    assert code == 1
    assert out == '{"v":"one"}\n{"v":"three"}\n'
    assert "skipped: line 2" in err


def test_bad_grammar_is_usage_error_before_any_model_call(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply("x"))
    code, _out, err = run_cli(["map", "Extract {bad name!}"], stdin="a\n")
    assert code == 64
    assert "invalid field group" in err
    assert route.call_count == 0  # failed fast, never hit the model


def test_no_model_configured_is_setup_screen(
    run_cli: RunCli, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SEMPIPE_MODEL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent-config-dir")
    respx_mock.get("http://localhost:11434/api/tags").mock(
        side_effect=httpx.ConnectError("refused")
    )
    code, _out, err = run_cli(["map", "translate"], stdin="hello\n")
    assert code == 2
    assert "no model configured" in err


def test_optional_field_schema_completes_on_the_openai_wire(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # workstream 10 Task 1: strict:true for an optional-field schema drew a 400
    # from OpenAI/Mistral and skipped every item for the wrong reason
    monkeypatch.setenv("SEMPIPE_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    schema_path = tmp_path / "optional.json"
    schema_path.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"a": {}, "b": {}},
                "required": ["a"],
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    route = respx_mock.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": '{"a": 1}'}}]})
    )
    code, out, _err = run_cli(["map", "Extract data", "--schema", str(schema_path)], stdin="x\n")
    assert code == 0
    assert out == '{"a":1}\n'
    body = json.loads(route.calls.last.request.content)
    assert body["response_format"]["json_schema"]["strict"] is False


def test_doomed_404_run_stops_at_first_sight(
    run_cli: RunCli, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D18: before the guardrail this skipped item-by-item, burning a call per line
    monkeypatch.setenv("SEMPIPE_MODEL", "gpt-4o-mini-typo")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    route = respx_mock.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(404, json={"error": {"message": "model not found"}})
    )
    code, _out, err = run_cli(["map", "translate", "--concurrency", "2"], stdin="one\ntwo\nthree\n")
    assert code == 2
    assert err.count("doesn't know the model") == 1  # one screen, not three skips
    assert route.call_count <= 2  # at most the in-flight workers, never all items
