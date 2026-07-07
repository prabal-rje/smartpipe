"""doctor --probe (D31): four tiny calls, one honest matrix."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from tests.conftest import RunCli

if TYPE_CHECKING:
    import respx

CHAT = "http://localhost:11434/api/chat"
EMBED = "http://localhost:11434/api/embed"


@pytest.fixture(autouse=True)
def local_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.setenv("SMARTPIPE_EMBED_MODEL", "nomic-embed-text")


def test_probe_charts_the_matrix(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        has_images = any("images" in message for message in body["messages"])
        content = "red" if has_images else "OK"
        return httpx.Response(200, json={"message": {"content": content}})

    respx_mock.post(CHAT).mock(side_effect=answer)
    respx_mock.post(EMBED).mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})
    )
    respx_mock.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
    )
    code, out, err = run_cli(["doctor", "--probe"])
    assert "probing modalities with 4 tiny calls" in err
    assert "text" in out and "image" in out and "audio" in out
    assert "✓ replied 'OK'" in out
    assert "✓ saw it — 'red'" in out
    assert "3-dim vector" in out
    # the ollama wire refuses audio pre-send — the matrix says so honestly
    assert "✗" in out and "can't hear audio" in out
    del code  # exit reflects the FREE checks; capability gaps don't flip it


def test_without_probe_no_model_calls(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    chat = respx_mock.post(CHAT)
    respx_mock.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
    )
    run_cli(["doctor"])
    assert chat.call_count == 0  # the D18 pin stands


def test_doctor_without_probe_shouts_about_it(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
    )
    _code, out, _err = run_cli(["doctor"])
    assert "verify SETUP, not ABILITY" in out
    assert "doctor --probe" in out
