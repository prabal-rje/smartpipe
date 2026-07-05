from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from sempipe.core.errors import ItemError, SetupFault
from sempipe.models.base import CompletionRequest, parse_model_ref
from sempipe.models.openai_compat import (
    OpenAIChatModel,
    OpenAIEmbeddingModel,
    require_api_key,
    resolve_base_url,
)
from sempipe.models.retry import RetryPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import respx

BASE = "https://api.openai.com"
FAST_RETRY = RetryPolicy(attempts=3, base_delay=0.0)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as instance:
        yield instance


def _chat(client: httpx.AsyncClient) -> OpenAIChatModel:
    return OpenAIChatModel(
        ref=parse_model_ref("gpt-4o-mini"),
        client=client,
        base_url=BASE,
        api_key="sk-test",
        retry=FAST_RETRY,
    )


def test_resolve_base_url() -> None:
    assert resolve_base_url({}) == "https://api.openai.com"
    assert (
        resolve_base_url({"SEMPIPE_OPENAI_BASE_URL": "https://api.groq.com/openai/"})
        == "https://api.groq.com/openai"
    )


def test_missing_key_fails_before_any_request() -> None:
    with pytest.raises(SetupFault) as excinfo:
        require_api_key({}, "gpt-4o-mini")
    message = str(excinfo.value)
    assert "OPENAI_API_KEY" in message
    assert "export OPENAI_API_KEY=" in message  # the fix line


def test_present_key_is_returned() -> None:
    assert require_api_key({"OPENAI_API_KEY": " sk-x "}, "m") == "sk-x"


async def test_chat_sends_bearer_and_exact_payload(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "hola"}}]}
        )
    )
    reply = await _chat(client).complete(CompletionRequest(system="sys", user="hello"))
    assert reply == "hola"
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer sk-test"
    assert json.loads(request.content) == {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ],
    }


async def test_response_format_present_iff_schema(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    )
    schema: dict[str, object] = {"type": "object"}
    await _chat(client).complete(CompletionRequest(system=None, user="x", json_schema=schema))
    sent = json.loads(route.calls.last.request.content)
    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "sempipe_output", "schema": schema, "strict": True},
    }

    await _chat(client).complete(CompletionRequest(system=None, user="x"))
    assert "response_format" not in json.loads(route.calls.last.request.content)


async def test_rejected_key_is_a_setup_fault(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    respx_mock.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    with pytest.raises(SetupFault, match="was rejected"):
        await _chat(client).complete(CompletionRequest(system=None, user="x"))


async def test_rate_limit_is_retried_then_recovers(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    route = respx_mock.post(f"{BASE}/v1/chat/completions")
    route.side_effect = [
        httpx.Response(429, json={"error": {"message": "slow down"}}),
        httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}),
    ]
    assert await _chat(client).complete(CompletionRequest(system=None, user="x")) == "ok"
    assert route.call_count == 2


async def test_server_error_exhausts_retries_then_skips_item(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": {"message": "boom"}})
    )
    with pytest.raises(ItemError, match="openai error 500: boom"):
        await _chat(client).complete(CompletionRequest(system=None, user="x"))
    assert route.call_count == 3


async def test_embeddings_sort_by_index(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    respx_mock.post(f"{BASE}/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.3, 0.4]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ]
            },
        )
    )
    model = OpenAIEmbeddingModel(
        ref=parse_model_ref("text-embedding-3-small"),
        client=client,
        base_url=BASE,
        api_key="sk-test",
        retry=FAST_RETRY,
    )
    assert await model.embed(["a", "b"]) == ((0.1, 0.2), (0.3, 0.4))
