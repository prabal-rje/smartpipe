from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from sempipe.core.errors import ItemError, SetupFault
from sempipe.models.base import CompletionRequest, parse_model_ref
from sempipe.models.ollama import (
    OllamaChatModel,
    OllamaEmbeddingModel,
    ollama_model_names,
    resolve_host,
)
from sempipe.models.retry import RetryPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import respx

HOST = "http://localhost:11434"
FAST_RETRY = RetryPolicy(attempts=3, base_delay=0.0)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as instance:
        yield instance


def _chat(client: httpx.AsyncClient) -> OllamaChatModel:
    return OllamaChatModel(
        ref=parse_model_ref("ollama/qwen3:8b"), client=client, host=HOST, retry=FAST_RETRY
    )


def test_resolve_host() -> None:
    assert resolve_host({}) == "http://localhost:11434"
    assert resolve_host({"OLLAMA_HOST": "http://gpubox:11434/"}) == "http://gpubox:11434"
    assert resolve_host({"OLLAMA_HOST": "0.0.0.0:11434"}) == "http://0.0.0.0:11434"
    assert resolve_host({"OLLAMA_HOST": "  "}) == "http://localhost:11434"


async def test_chat_sends_the_exact_payload(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    route = respx_mock.post(f"{HOST}/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"role": "assistant", "content": "hola"}})
    )
    reply = await _chat(client).complete(CompletionRequest(system="sys", user="hello"))
    assert reply == "hola"
    assert json.loads(route.calls.last.request.content) == {
        "model": "qwen3:8b",
        "stream": False,
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ],
    }


async def test_format_field_present_iff_schema(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    route = respx_mock.post(f"{HOST}/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "{}"}})
    )
    schema: dict[str, object] = {"type": "object", "properties": {"a": {}}}
    await _chat(client).complete(CompletionRequest(system=None, user="x", json_schema=schema))
    assert json.loads(route.calls.last.request.content)["format"] == schema

    await _chat(client).complete(CompletionRequest(system=None, user="x"))
    assert "format" not in json.loads(route.calls.last.request.content)


async def test_connection_refused_is_the_unreachable_screen(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    respx_mock.post(f"{HOST}/api/chat").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(SetupFault) as excinfo:
        await _chat(client).complete(CompletionRequest(system=None, user="x"))
    message = str(excinfo.value)
    assert message.startswith("error: can't reach ollama at http://localhost:11434")
    assert "ollama serve" in message


async def test_missing_model_is_a_setup_fault_with_pull_hint(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    respx_mock.post(f"{HOST}/api/chat").mock(
        return_value=httpx.Response(404, json={"error": "model 'qwen3:8b' not found"})
    )
    with pytest.raises(SetupFault) as excinfo:
        await _chat(client).complete(CompletionRequest(system=None, user="x"))
    assert "ollama pull qwen3:8b" in str(excinfo.value)


async def test_server_errors_are_retried_then_skip_the_item(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    route = respx_mock.post(f"{HOST}/api/chat").mock(
        return_value=httpx.Response(500, json={"error": "overloaded"})
    )
    with pytest.raises(ItemError, match="ollama error 500"):
        await _chat(client).complete(CompletionRequest(system=None, user="x"))
    assert route.call_count == 3  # all attempts used


async def test_transient_error_recovers(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    route = respx_mock.post(f"{HOST}/api/chat")
    route.side_effect = [
        httpx.Response(429, json={"error": "slow down"}),
        httpx.Response(200, json={"message": {"content": "ok"}}),
    ]
    assert await _chat(client).complete(CompletionRequest(system=None, user="x")) == "ok"
    assert route.call_count == 2


async def test_unexpected_reply_shape_is_an_item_error(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    respx_mock.post(f"{HOST}/api/chat").mock(return_value=httpx.Response(200, json={"nope": 1}))
    with pytest.raises(ItemError, match="unexpected reply shape"):
        await _chat(client).complete(CompletionRequest(system=None, user="x"))


async def test_embed_batches_and_parses_vectors(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    route = respx_mock.post(f"{HOST}/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})
    )
    model = OllamaEmbeddingModel(
        ref=parse_model_ref("nomic-embed-text"), client=client, host=HOST, retry=FAST_RETRY
    )
    vectors = await model.embed(["a", "b"])
    assert vectors == ((0.1, 0.2), (0.3, 0.4))
    assert json.loads(route.calls.last.request.content) == {
        "model": "nomic-embed-text",
        "input": ["a", "b"],
    }


async def test_model_names_lists_tags(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    respx_mock.get(f"{HOST}/api/tags").mock(
        return_value=httpx.Response(
            200, json={"models": [{"name": "qwen3:8b"}, {"name": "nomic-embed-text"}]}
        )
    )
    assert await ollama_model_names(client, HOST) == ("qwen3:8b", "nomic-embed-text")


async def test_model_names_is_none_when_nothing_listens(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    respx_mock.get(f"{HOST}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    assert await ollama_model_names(client, HOST) is None
