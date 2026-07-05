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
    monkeypatch.setenv("SEMPIPE_MODEL", "ollama/qwen3:8b")


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
    assert out == "name,role\r\nAda,eng\r\nBob,design\r\n"


def test_map_output_tsv(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).mock(return_value=_extract(name="Ada", role="eng"))
    code, out, _err = run_cli(["map", "Extract {name, role}", "--output", "tsv"], stdin="card\n")
    assert code == 0
    assert out == "name\trole\r\nAda\teng\r\n"


def test_csv_on_plain_text_is_usage_error(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_extract(x="y"))
    code, _out, err = run_cli(["map", "shout it", "--output", "csv"], stdin="hi\n")
    assert code == 64
    assert "needs structured output" in err
    assert route.call_count == 0  # failed before any model call
