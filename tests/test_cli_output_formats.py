"""Full-stack CSV/TSV output through the real CLI and container."""

from __future__ import annotations

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


def _extract(**fields: object) -> httpx.Response:
    import json

    return httpx.Response(200, json={"message": {"content": json.dumps(fields)}})


def test_map_output_csv(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).side_effect = [
        _extract(name="Ada", role="eng"),
        _extract(name="Bob", role="design"),
    ]
    code, out, _err = run_cli(
        ["map", "Extract {name, role}", "--output", "csv", "--concurrency", "1"],
        stdin="card one\ncard two\n",
    )
    assert code == 0
    assert out == (
        "name,role,__source\r\n"
        'Ada,eng,"{""path"":""-"",""as"":""lines"",""line"":1}"\r\n'
        'Bob,design,"{""path"":""-"",""as"":""lines"",""line"":2}"\r\n'
    )


def test_map_output_tsv(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).mock(return_value=_extract(name="Ada", role="eng"))
    code, out, _err = run_cli(["map", "Extract {name, role}", "--output", "tsv"], stdin="card\n")
    assert code == 0
    assert out == (
        'name\trole\t__source\r\nAda\teng\t"{""path"":""-"",""as"":""lines"",""line"":1}"\r\n'
    )


def test_csv_on_plain_text_is_usage_error(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_extract(x="y"))
    code, _out, err = run_cli(["map", "shout it", "--output", "csv"], stdin="hi\n")
    assert code == 64
    assert "needs structured output" in err
    assert route.call_count == 0  # failed before any model call


# --- --fields (workstream 04) ---------------------------------------------------


def test_map_fields_projects_tsv(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    # the stage-09 demo shape: extract three fields, keep two, cut(1) the second
    respx_mock.post(CHAT).side_effect = [
        _extract(name="Ada", email="ada@x.io", role="eng"),
        _extract(name="Bob", email="bob@x.io", role="design"),
    ]
    code, out, _err = run_cli(
        [
            "map",
            "Extract {name, email, role}",
            "--fields",
            "name,email",
            "--output",
            "tsv",
            "--concurrency",
            "1",
        ],
        stdin="card one\ncard two\n",
    )
    assert code == 0
    assert out == "name\temail\r\nAda\tada@x.io\r\nBob\tbob@x.io\r\n"


def test_fields_on_plain_map_is_usage_error(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_extract(x="y"))
    code, _out, err = run_cli(["map", "shout it", "--fields", "a"], stdin="hi\n")
    assert code == 64
    assert "--fields selects columns from structured output" in err
    assert route.call_count == 0


def test_empty_field_name_is_usage_error(run_cli: RunCli) -> None:
    code, _out, err = run_cli(["map", "Extract {a}", "--fields", "a,,b"], stdin="x\n")
    assert code == 64
    assert "--fields got an empty field name" in err


def test_duplicate_field_usage_error_names_the_offender(run_cli: RunCli) -> None:
    code, _out, err = run_cli(["map", "Extract {a}", "--fields", "a,a"], stdin="x\n")
    assert code == 64
    assert "--fields names 'a' more than once" in err


def test_filter_rejects_fields_flag(run_cli: RunCli) -> None:
    # filter output is a byte-faithful subset — it never grows the flag (plan/ux.md)
    code, _out, err = run_cli(["filter", "keeps", "--fields", "a"], stdin="x\n")
    assert code == 64
    assert "no such option" in err.lower()
