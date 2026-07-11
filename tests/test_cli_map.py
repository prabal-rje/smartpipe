"""Full-stack ``map`` tests: the real CLI entry point → real AppContainer →
real Ollama adapter, with only the HTTP endpoint mocked. Proves the wiring the
unit tests (which inject a fake context) deliberately bypass.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.engine.schema import BARE_PROPERTY
from tests.conftest import RunCli

if TYPE_CHECKING:
    from pathlib import Path

    import respx

CHAT = "http://localhost:11434/api/chat"


@pytest.fixture(autouse=True)
def local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.delenv("SMARTPIPE_OUTPUT", raising=False)


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
    assert out == '{"vendor":"Acme","total":5,"__source":{"path":"-","as":"lines","line":1}}\n'


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
    assert out == (
        '{"v":"one","__source":{"path":"-","as":"lines","line":1}}\n'
        '{"v":"three","__source":{"path":"-","as":"lines","line":3}}\n'
    )
    assert "skipped: line 2" in err


def test_keep_invalid_keeps_the_row_and_exits_clean(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(CHAT).side_effect = [_reply("not json"), _reply("still not json")]
    code, out, err = run_cli(["map", "Extract {v}", "--keep-invalid"], stdin="a\n")
    assert code == 0
    row = json.loads(out)
    assert row["__invalid"] is True
    assert row["__raw"] == "still not json"
    assert row["__error"]
    assert "skipped" not in err


def test_dry_run_flag_prints_the_request_without_a_call(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply("never"))
    code, out, _err = run_cli(["map", "Extract {v}", "--dry-run"], stdin="Acme $5\n")
    assert code == 0
    assert route.call_count == 0
    assert "--- system ---" in out and "Acme $5" in out


def test_dry_run_works_without_any_model_configured(
    run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    # composing the request needs no model — a dry run is free even pre-setup
    monkeypatch.delenv("SMARTPIPE_MODEL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent-config-dir")
    monkeypatch.setenv("APPDATA", "/nonexistent-config-dir")
    code, out, _err = run_cli(["map", "Extract {v}", "--dry-run"], stdin="x\n")
    assert code == 0
    assert "--- user ---" in out


def test_fallback_model_flag_switches_wholesale(
    run_cli: RunCli, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTPIPE_BREAKER", "2")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    respx_mock.post(CHAT).mock(return_value=httpx.Response(500, json={"error": "overloaded"}))
    respx_mock.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "B"}}]})
    )
    code, out, err = run_cli(
        ["map", "x", "--concurrency", "1", "--fallback-model", "gpt-4o-mini"],
        stdin="a\nb\nc\n",
    )
    assert code == 0
    assert out == "B\nB\nB\n"  # one combined stream — the window re-ran on the fallback
    assert "switching to openai/gpt-4o-mini for the rest of the run" in err
    receipt = "answers: openai/gpt-4o-mini ×3"  # noqa: RUF001
    assert receipt in err  # every answer came from the fallback


def test_fallback_model_refuses_an_embedder(run_cli: RunCli) -> None:
    code, _out, err = run_cli(["map", "x", "--fallback-model", "nomic-embed-text"], stdin="a\n")
    assert code == 64
    assert "chat models only" in err


def test_bad_grammar_is_usage_error_before_any_model_call(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply("x"))
    code, _out, err = run_cli(["map", "Extract {bad name!}"], stdin="a\n")
    assert code == 64
    assert "isn't a type" in err  # D37: ident + unknown token reads as a bad type now
    assert route.call_count == 0  # failed fast, never hit the model


def test_no_model_configured_is_setup_screen(
    run_cli: RunCli, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SMARTPIPE_MODEL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent-config-dir")
    monkeypatch.setenv("APPDATA", "/nonexistent-config-dir")  # the windows config root (D09)
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
    monkeypatch.setenv("SMARTPIPE_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    schema_path = tmp_path / "optional.json"
    schema_path.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"a": dict(BARE_PROPERTY), "b": dict(BARE_PROPERTY)},
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
    assert out == '{"a":1,"__source":{"path":"-","as":"lines","line":1}}\n'
    body = json.loads(route.calls.last.request.content)
    assert body["response_format"]["json_schema"]["strict"] is False


def test_doomed_404_run_stops_at_first_sight(
    run_cli: RunCli, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D18: before the guardrail this skipped item-by-item, burning a call per line
    monkeypatch.setenv("SMARTPIPE_MODEL", "gpt-4o-mini-typo")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    route = respx_mock.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(404, json={"error": {"message": "model not found"}})
    )
    code, _out, err = run_cli(["map", "translate", "--concurrency", "2"], stdin="one\ntwo\nthree\n")
    assert code == 2
    assert err.count("doesn't know the model") == 1  # one screen, not three skips
    assert route.call_count <= 2  # at most the in-flight workers, never all items


def test_max_calls_caps_the_run_and_never_exits_clean(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(CHAT).mock(
        return_value=httpx.Response(200, json={"message": {"content": "ok"}})
    )
    code, out, err = run_cli(
        ["map", "translate", "--concurrency", "1", "--max-calls", "2"],
        stdin="one\ntwo\nthree\nfour\nfive\n",
    )
    assert route.call_count == 2  # the hard ceiling held
    assert out == "ok\nok\n"  # completed work was emitted (drained, not discarded)
    assert code == 1  # a capped run is never a clean 0 (D18)
    assert "stopped by --max-calls (2 calls made)" in err


def test_max_calls_zero_is_a_usage_error(run_cli: RunCli) -> None:
    code, _out, err = run_cli(["map", "x", "--max-calls", "0"], stdin="hi\n")
    assert code == 64
    assert "--max-calls must be >= 1, got 0" in err


def test_max_calls_env_fallback_is_validated(
    run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTPIPE_MAX_CALLS", "nope")
    code, _out, err = run_cli(["map", "x"], stdin="hi\n")
    assert code == 64
    assert "SMARTPIPE_MAX_CALLS must be a whole number >= 1" in err


def test_schema_from_builds_and_enforces_the_schema(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(CHAT).mock(
        return_value=httpx.Response(
            200, json={"message": {"content": '{"vendor": "Acme", "total": 12.5}'}}
        )
    )
    code, out, _err = run_cli(
        ["map", "Extract the fields", "--schema-from", "vendor string; total number >= 0"],
        stdin="invoice text\n",
    )
    assert code == 0
    assert out == '{"vendor":"Acme","total":12.5,"__source":{"path":"-","as":"lines","line":1}}\n'
    body = json.loads(route.calls.last.request.content)
    sent = body["format"]  # the ollama wire carries the synthesized schema
    assert sent["properties"]["total"] == {"type": "number", "minimum": 0}


def test_schema_from_typo_fails_free(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(CHAT).mock(return_value=httpx.Response(200, json={}))
    code, _out, err = run_cli(
        ["map", "Extract", "--schema-from", "priority enun(low,high)"], stdin="x\n"
    )
    assert code == 64
    assert "unexpected 'enun(low,high)' for field 'priority'" in err
    assert route.call_count == 0  # deterministic: failed before any spend


def test_schema_from_with_schema_is_a_usage_error(run_cli: RunCli, tmp_path: Path) -> None:
    schema = tmp_path / "s.json"
    schema.write_text("{}", encoding="utf-8")
    code, _out, err = run_cli(
        ["map", "x", "--schema", str(schema), "--schema-from", "a string"], stdin="x\n"
    )
    assert code == 64
    assert "both shape the output — use one" in err


def test_tally_counts_the_field_on_stderr(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    replies = iter(['{"label": "bug"}', '{"label": "bug"}', '{"label": "feature"}'])

    def next_reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": next(replies)}})

    respx_mock.post(CHAT).mock(side_effect=next_reply)
    code, out, err = run_cli(["map", "Extract {label}", "--tally", "label"], stdin="a\nb\nc\n")
    assert code == 0
    assert out.count("\n") == 3  # stdout untouched: three NDJSON records
    assert "tally: bug 2 · feature 1" in err  # the pinned final line


def test_tally_without_structure_is_a_usage_error(run_cli: RunCli) -> None:
    code, _out, err = run_cli(["map", "summarize", "--tally", "label"], stdin="x\n")
    assert code == 64
    assert "--tally needs structured output" in err


def test_explode_emits_one_row_per_list_element(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(CHAT).mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": '{"vendor": "Acme", "risks": ["late", "fx", "supply"]}'}},
        )
    )
    code, out, _err = run_cli(
        ["map", "Extract {vendor, risks}", "--explode", "risks"], stdin="doc\n"
    )
    assert code == 0
    spine = '"__source":{"path":"-","as":"lines","line":1}'
    assert out.splitlines() == [
        '{"vendor":"Acme","risks":"late",' + spine + "}",
        '{"vendor":"Acme","risks":"fx",' + spine + "}",
        '{"vendor":"Acme","risks":"supply",' + spine + "}",
    ]


def test_explode_composes_with_tally(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).mock(
        return_value=httpx.Response(
            200, json={"message": {"content": '{"risks": ["late", "late", "fx"]}'}}
        )
    )
    code, _out, err = run_cli(
        ["map", "Extract {risks}", "--explode", "risks", "--tally", "risks"], stdin="doc\n"
    )
    assert code == 0
    assert "tally: late 2 · fx 1" in err  # counted per exploded row


def test_explode_without_structure_is_a_usage_error(run_cli: RunCli) -> None:
    code, _out, err = run_cli(["map", "summarize", "--explode", "risks"], stdin="x\n")
    assert code == 64
    assert "--explode needs structured output" in err


def test_bare_strips_the_spine_from_record_results(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply("short"))
    code, out, _err = run_cli(["map", "summarize", "--bare"], stdin='{"id": 7}\n')
    assert code == 0
    assert json.loads(out) == {"result": "short"}  # no __source under --bare


def test_object_list_explodes_one_row_per_inner_record(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    """Item 16 end to end: an object-list field extracts, coerces per inner
    record (the date canonicalizes), and --explode yields one row per object."""
    reply = (
        '{"triples": ['
        '{"subject": "acme", "relation": "acquired", "object": "globex"},'
        '{"subject": "globex", "relation": "founded", "object": "1989"}]}'
    )
    respx_mock.post(CHAT).mock(return_value=_reply(reply))
    code, out, _err = run_cli(
        ["map", "Extract {triples {subject, relation, object}[]}", "--explode", "triples"],
        stdin="doc\n",
    )
    assert code == 0
    spine = '"__source":{"path":"-","as":"lines","line":1}'
    assert out.splitlines() == [
        '{"triples":{"subject":"acme","relation":"acquired","object":"globex"},' + spine + "}",
        '{"triples":{"subject":"globex","relation":"founded","object":"1989"},' + spine + "}",
    ]


def test_object_list_inner_dates_canonicalize_end_to_end(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    reply = '{"events": [{"name": "kickoff", "when": "Jan 15, 2026"}]}'
    respx_mock.post(CHAT).mock(return_value=_reply(reply))
    code, out, _err = run_cli(["map", "List {events {name string, when date}[]}"], stdin="doc\n")
    assert code == 0
    row = json.loads(out)
    assert row["events"] == [{"name": "kickoff", "when": "2026-01-15"}]


def test_object_list_ceiling_is_a_usage_error_before_any_call(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply("{}"))
    code, _out, err = run_cli(["map", "Extract {a {b {c}[]}[]}"], stdin="doc\n")
    assert code == 64
    assert "object lists nest one level deep" in err
    assert "flatten the inner structure or extract in two passes" in err
    assert route.call_count == 0  # refused free, before a single model call


# --- request batching (item 62), end to end through the real container --------------


def test_batching_on_packs_items_and_discloses(
    run_cli: RunCli, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    import re

    monkeypatch.setenv("SMARTPIPE_BATCH", "on")  # the conftest pin holds everywhere else
    # Deterministic grouping (macOS-3.13 matrix flake, 2026-07-10): K=3 dispatches
    # synchronously on the third enqueue (map guarantees >= K workers when
    # coalescing), so the flight never depends on the window timer racing a loaded
    # runner. A straggler flushed by the timer flies SOLO - which this packed-only
    # mock cannot answer. The huge window is the belt: it must never fire first.
    monkeypatch.setenv("SMARTPIPE_BATCH_SIZE", "3")
    monkeypatch.setenv("SMARTPIPE_BATCH_WINDOW_MS", "60000")
    block = re.compile(r'<input id="(r\d+)">\n(.*?)\n</input>', re.DOTALL)
    calls: list[str] = []

    def packed_reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user = body["messages"][-1]["content"]
        calls.append(user)
        answers = {label: {"v": text.upper()} for label, text in block.findall(user)}
        return _reply(json.dumps(answers))

    respx_mock.post(CHAT).mock(side_effect=packed_reply)
    code, out, err = run_cli(["map", "Extract {v}"], stdin="a\nb\nc\n")
    assert code == 0
    assert len(calls) == 1  # three items, ONE model call
    rows = [json.loads(line) for line in out.splitlines()]
    assert [row["v"] for row in rows] == ["A", "B", "C"]  # fan-out kept input order
    assert "note: batching: 3 items in 1 packed call" in err  # §9: the disclosure, once
