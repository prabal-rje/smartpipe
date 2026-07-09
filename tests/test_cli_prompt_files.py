"""``@file`` prompts + ``--prompt-file`` (D23): both spellings, loud failures."""

from __future__ import annotations

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


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": content}})


def test_at_file_reads_the_prompt(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompt.md").write_text("translate to French\n", encoding="utf-8")
    route = respx_mock.post(CHAT).mock(return_value=_reply("bonjour"))
    code, out, _err = run_cli(["map", "@prompt.md"], stdin="hello\n")
    assert code == 0
    assert out == "bonjour\n"
    import json

    body = json.loads(route.calls.last.request.content)
    assert "translate to French" in body["messages"][-1]["content"]


def test_braces_inside_the_file_are_live_grammar(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    prompt = tmp_path / "extract.md"
    prompt.write_text("Extract {vendor: the supplier, total}\n", encoding="utf-8")
    route = respx_mock.post(CHAT).mock(return_value=_reply('{"vendor": "A", "total": 1}'))
    code, out, _err = run_cli(["map", f"@{prompt}"], stdin="invoice\n")
    assert code == 0
    assert out == '{"vendor":"A","total":1,"__source":{"path":"-","as":"lines","line":1}}\n'
    import json

    body = json.loads(route.calls.last.request.content)
    assert body["format"]["properties"]["vendor"] == {
        **BARE_PROPERTY,
        "description": "the supplier",
    }


def test_missing_file_is_the_pinned_error(run_cli: RunCli) -> None:
    code, _out, err = run_cli(["map", "@does_not_exist.md"], stdin="x\n")
    assert code == 64
    assert "prompt file not found: does_not_exist.md" in err


def test_empty_file_is_a_usage_error(run_cli: RunCli, tmp_path: Path) -> None:
    empty = tmp_path / "empty.md"
    empty.write_text("\n", encoding="utf-8")
    code, _out, err = run_cli(["map", f"@{empty}"], stdin="x\n")
    assert code == 64
    assert "prompt file is empty" in err


def test_double_at_escapes_a_literal(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply("ok"))
    code, _out, _err = run_cli(["map", "@@alice, summarize this"], stdin="x\n")
    assert code == 0
    import json

    body = json.loads(route.calls.last.request.content)
    assert "@alice, summarize this" in body["messages"][-1]["content"]


def test_non_leading_at_is_untouched(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply("ok"))
    code, _out, _err = run_cli(["map", "email @alice about x"], stdin="x\n")
    assert code == 0
    import json

    body = json.loads(route.calls.last.request.content)
    assert "email @alice about x" in body["messages"][-1]["content"]


def test_prompt_and_prompt_file_together_is_a_usage_error(run_cli: RunCli, tmp_path: Path) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("x\n", encoding="utf-8")
    code, _out, err = run_cli(["map", "inline prompt", "--prompt-file", str(prompt)], stdin="x\n")
    assert code == 64
    assert "both given — use one" in err


def test_no_prompt_at_all_is_a_usage_error(run_cli: RunCli) -> None:
    code, _out, err = run_cli(["map"], stdin="x\n")
    assert code == 64
    assert "no prompt given" in err


def test_filter_and_join_take_prompt_files(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    prompt = tmp_path / "cond.md"
    prompt.write_text("mentions a bug\n", encoding="utf-8")
    respx_mock.post(CHAT).mock(return_value=_reply('{"match": true}'))
    code, out, _err = run_cli(["filter", "--prompt-file", str(prompt)], stdin="crash!\n")
    assert code == 0
    assert out == "crash!\n"
