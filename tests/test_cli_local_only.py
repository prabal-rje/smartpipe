"""Full-stack ``--local-only`` tests (item 65d): local data execution.

With the fence armed, user input stays on the machine: cloud model wires
refuse at resolution time (exit 2, before spend), and a remote OLLAMA_HOST is
honestly refused. Supporting requests without user payload are allowed by the
product contract; the current update/catalog paths remain conservatively quiet.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.core.errors import SetupFault
from tests.conftest import RunCli

if TYPE_CHECKING:
    import respx

CHAT = "http://localhost:11434/api/chat"
EMBED = "http://localhost:11434/api/embed"


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


def test_cloud_chat_refuses_before_any_spend(
    run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")  # a key does not open the fence
    code, out, err = run_cli(["--local-only", "map", "x"], stdin="a\n")
    assert code == 2
    assert out == ""
    assert "--local-only forbids the cloud chat wire 'openai/gpt-4o-mini'" in err
    assert "input stays on this machine" in err
    assert "ollama" in err  # the local alternative is on the screen


def test_the_env_form_fences_without_the_flag(
    run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("SMARTPIPE_LOCAL_ONLY", "1")
    code, _out, err = run_cli(["map", "x"], stdin="a\n")
    assert code == 2
    assert "anthropic" in err


def test_local_ollama_passes_the_fence(
    run_cli: RunCli, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    respx_mock.post(CHAT).mock(return_value=_reply("hola"))
    code, out, _err = run_cli(["--local-only", "map", "translate"], stdin="hello\n")
    assert code == 0
    assert out == "hola\n"


def test_a_remote_ollama_host_is_data_leaving(
    run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.setenv("OLLAMA_HOST", "http://gpu-box:11434")
    code, _out, err = run_cli(["--local-only", "map", "x"], stdin="a\n")
    assert code == 2
    assert "OLLAMA_HOST" in err
    assert "IS data leaving" in err


def test_cloud_embedder_refuses_with_the_on_device_fix(
    run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTPIPE_EMBED_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    code, _out, err = run_cli(["--local-only", "embed"], stdin="a\n")
    assert code == 2
    assert "embedding wire" in err
    assert "unset embed-model" in err


def test_local_ollama_embedding_passes(
    run_cli: RunCli, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.setenv("SMARTPIPE_EMBED_MODEL", "ollama/nomic-embed-text")
    respx_mock.post(EMBED).mock(return_value=httpx.Response(200, json={"embeddings": [[1.0, 0.0]]}))
    code, out, _err = run_cli(["--local-only", "embed"], stdin="a\n")
    assert code == 0
    assert json.loads(out.splitlines()[0])["vector"] == [1.0, 0.0]


def test_the_flag_suppresses_the_update_check(
    run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smartpipe.io import update_check

    fired: list[bool] = []
    monkeypatch.setattr(update_check, "begin_background_check", lambda: fired.append(True))
    monkeypatch.setenv("SMARTPIPE_MODEL", "gpt-4o-mini")
    run_cli(["--local-only", "map", "x"], stdin="a\n")
    assert fired == []  # the pre-parse hook never ran under the flag


async def test_catalog_fetches_answer_none_without_a_request() -> None:
    from smartpipe.models.catalogs import fetch_catalog, fetch_embed_catalog, fetch_registry

    env = {"SMARTPIPE_LOCAL_ONLY": "1", "OPENAI_API_KEY": "sk-test"}
    async with httpx.AsyncClient() as client:  # no respx: a real request would explode
        assert await fetch_catalog("openai", env, client) is None
        assert await fetch_embed_catalog("openai", env, client) is None
        assert await fetch_registry(env, client) is None


def test_update_check_gate_is_fence_aware() -> None:
    from smartpipe.io.update_check import check_allowed

    assert check_allowed({"SMARTPIPE_LOCAL_ONLY": "1"}, is_tty=True) is False


def test_ollama_autodetect_probe_respects_the_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    # no configured model + remote OLLAMA_HOST: even the tags probe must not fire
    import asyncio

    from smartpipe.container import build_container

    async def scenario() -> None:
        env = {
            "SMARTPIPE_LOCAL_ONLY": "1",
            "OLLAMA_HOST": "http://gpu-box:11434",
            "XDG_CONFIG_HOME": "/nonexistent",
        }
        async with build_container(env) as container:
            with pytest.raises(SetupFault, match="OLLAMA_HOST"):
                await container.chat_model(None)

    asyncio.run(scenario())
