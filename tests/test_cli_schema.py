"""``smartpipe schema`` (rung 4, D22): one call + one repair, stdout never lies."""

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


# --- the free rungs: braces/DSL compile with zero model calls -------------------

DSL_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "vendor": {"type": "string"},
        "total": {"type": "number", "minimum": 0},
    },
    "required": ["vendor", "total"],
    "additionalProperties": False,
}


def test_brace_expression_compiles_free(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply(GOOD))
    code, out, err = run_cli(["schema", "{vendor string: legal name, total number}"])
    assert code == 0
    assert route.call_count == 0  # deterministic — never a model call
    schema = json.loads(out)
    assert schema["properties"]["vendor"] == {"type": "string", "description": "legal name"}
    assert schema["properties"]["total"] == {"type": "number"}
    assert err == ""


def test_dsl_expression_compiles_free_and_pretty(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply(GOOD))
    code, out, _err = run_cli(["schema", "vendor string; total number >= 0"])
    assert code == 0
    assert route.call_count == 0
    assert out == json.dumps(DSL_SCHEMA, indent=2) + "\n"  # pretty, 2-space, pinned shape


def test_single_field_dsl_is_recognized(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply(GOOD))
    code, out, _err = run_cli(["schema", "status enum(todo, done)"])
    assert code == 0
    assert route.call_count == 0
    assert json.loads(out)["properties"]["status"] == {"enum": ["todo", "done"]}


def test_bad_brace_grammar_is_the_existing_usage_screen(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply(GOOD))
    code, out, err = run_cli(["schema", "{status enum()}"])
    assert code == 64
    assert out == ""
    assert "enum needs at least one value" in err
    assert route.call_count == 0


def test_check_passing_file_is_exit_0(run_cli: RunCli, tmp_path: Path) -> None:
    data = tmp_path / "data.jsonl"
    data.write_text('{"vendor": "Acme", "total": 5}\n{"vendor": "Bar", "total": 0}\n')
    code, _out, err = run_cli(["schema", "vendor string; total number >= 0", "--check", str(data)])
    assert code == 0
    assert "schema check: 2 of 2 rows pass" in err


def test_check_reports_first_failures_and_exits_1(run_cli: RunCli, tmp_path: Path) -> None:
    rows = ['{"vendor": "ok", "total": 1}'] + ['{"vendor": 7, "total": -1}'] * 7 + ["not json"]
    data = tmp_path / "data.jsonl"
    data.write_text("\n".join(rows) + "\n")
    code, out, err = run_cli(["schema", "vendor string; total number >= 0", "--check", str(data)])
    assert code == 1
    assert out == ""  # --check is a verdict, not a schema dump
    assert "row 2:" in err and "row 6:" in err  # the first 5 failures, numbered
    assert "row 7:" not in err  # capped after 5
    assert "not JSON" in err or "row 9" not in err
    assert "schema check: 1 of 9 rows pass (8 failed)" in err


def test_example_prints_a_validating_instance(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    import jsonschema

    route = respx_mock.post(CHAT).mock(return_value=_reply(GOOD))
    code, out, _err = run_cli(["schema", "vendor string; total number >= 0", "--example"])
    assert code == 0
    assert route.call_count == 0
    instance = json.loads(out)
    jsonschema.validate(instance, DSL_SCHEMA)
    code2, out2, _err2 = run_cli(["schema", "vendor string; total number >= 0", "--example"])
    assert (code2, out2) == (code, out)  # deterministic, no randomness


def test_stdin_repl_compiles_each_line(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply(GOOD))
    code, out, err = run_cli(["schema"], stdin="{vendor}\n\nstatus enum(a, b)\n")
    assert code == 0
    assert route.call_count == 0
    blocks = out.split("}\n{")  # two pretty objects back to back
    assert len(blocks) == 2
    assert err == ""


def test_stdin_repl_reports_bad_lines_and_keeps_going(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply(GOOD))
    code, out, err = run_cli(["schema"], stdin="{status enum()}\n{vendor}\n")
    assert code == 64  # a bad line marks the run
    assert "enum needs at least one value" in err
    assert json.loads(out)["required"] == ["vendor"]  # the good line still compiled


def test_no_expression_at_a_tty_is_a_usage_screen(
    run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Tty:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", _Tty())
    code, out, err = run_cli(["schema"])
    assert code == 64
    assert out == ""
    assert "needs an expression" in err


def test_check_demands_a_deterministic_expression(run_cli: RunCli, tmp_path: Path) -> None:
    data = tmp_path / "data.jsonl"
    data.write_text("{}\n")
    code, _out, err = run_cli(["schema", "an invoice with a vendor", "--check", str(data)])
    assert code == 64
    assert "deterministic expression" in err


def test_check_and_example_together_refuse(run_cli: RunCli, tmp_path: Path) -> None:
    data = tmp_path / "data.jsonl"
    data.write_text("{}\n")
    code, _out, err = run_cli(["schema", "{v}", "--check", str(data), "--example"])
    assert code == 64
    assert "--check and --example" in err
