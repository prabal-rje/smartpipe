from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.core.errors import ItemError, SetupFault
from smartpipe.models.base import CompletionRequest, parse_model_ref
from smartpipe.models.openai_compat import (
    MISTRAL_WIRE,
    OpenAIChatModel,
    OpenAIEmbeddingModel,
    require_api_key,
    resolve_base_url,
)
from smartpipe.models.retry import RetryPolicy

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
        resolve_base_url({"SMARTPIPE_OPENAI_BASE_URL": "https://api.groq.com/openai/"})
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
        "temperature": 0.0,
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
    strict_schema: dict[str, object] = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    await _chat(client).complete(
        CompletionRequest(system=None, user="x", json_schema=strict_schema)
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "smartpipe_output", "schema": strict_schema, "strict": True},
    }

    # a schema strict mode would 400 on (optional field) must NOT claim strict
    open_schema: dict[str, object] = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    await _chat(client).complete(CompletionRequest(system=None, user="x", json_schema=open_schema))
    sent = json.loads(route.calls.last.request.content)
    assert sent["response_format"]["json_schema"]["strict"] is False

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


async def test_connect_timeout_retries_then_maps_to_unreachable_screen(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    # regression: a connect timeout must retry (transient) and then surface the
    # actionable "can't reach" screen, exactly like a refused connection — not a
    # generic item skip (ConnectTimeout != ConnectError).
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(
        side_effect=httpx.ConnectTimeout("timed out")
    )
    with pytest.raises(SetupFault, match="can't reach"):
        await _chat(client).complete(CompletionRequest(system=None, user="x"))
    assert route.call_count == 3  # retried before giving up


async def test_read_timeout_after_retries_is_an_item_skip(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    # a slow response (endpoint reachable) is a per-item problem, not setup.
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    with pytest.raises(ItemError, match="failed"):
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


# --- the same wire, Mistral-parametrized (workstream 10 Task 4) ---------------------

MISTRAL_BASE = "https://api.mistral.ai"


def _mistral_chat(client: httpx.AsyncClient, name: str = "mistral-small-latest") -> OpenAIChatModel:
    return OpenAIChatModel(
        ref=parse_model_ref(name),
        client=client,
        base_url=MISTRAL_BASE,
        api_key="mk-test",
        retry=FAST_RETRY,
        wire=MISTRAL_WIRE,
    )


async def test_mistral_chat_golden_shape(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    route = respx_mock.post(f"{MISTRAL_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "salut"}}]})
    )
    reply = await _mistral_chat(client).complete(CompletionRequest(system="sys", user="hello"))
    assert reply == "salut"
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer mk-test"
    assert json.loads(request.content) == {
        "model": "mistral-small-latest",
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ],
    }


async def test_mistral_structured_output_carries_the_strictness_logic(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    route = respx_mock.post(f"{MISTRAL_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    )
    open_schema: dict[str, object] = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    await _mistral_chat(client).complete(
        CompletionRequest(system=None, user="x", json_schema=open_schema)
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is False  # Task-1 logic, same wire


async def test_mistral_embed_via_v1_embeddings(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    route = respx_mock.post(f"{MISTRAL_BASE}/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 1024}]})
    )
    model = OpenAIEmbeddingModel(
        ref=parse_model_ref("mistral-embed"),
        client=client,
        base_url=MISTRAL_BASE,
        api_key="mk-test",
        retry=FAST_RETRY,
        wire=MISTRAL_WIRE,
    )
    vectors = await model.embed(["hello"])
    assert len(vectors[0]) == 1024
    assert json.loads(route.calls.last.request.content)["model"] == "mistral-embed"


async def test_mistral_429_honors_retry_after(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    # resilience comes free from with_retries/is_retryable_http — prove the wiring
    route = respx_mock.post(f"{MISTRAL_BASE}/v1/chat/completions")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"message": "slow"}}),
        httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}),
    ]
    assert await _mistral_chat(client).complete(CompletionRequest(system=None, user="x")) == "ok"
    assert route.call_count == 2


async def test_mistral_rejected_key_names_the_right_env_vars(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    respx_mock.post(f"{MISTRAL_BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    with pytest.raises(SetupFault) as excinfo:
        await _mistral_chat(client).complete(CompletionRequest(system=None, user="x"))
    message = str(excinfo.value)
    assert "MISTRAL_API_KEY" in message
    assert "SMARTPIPE_MISTRAL_BASE_URL" in message


def test_mistral_missing_key_screen_points_at_the_console() -> None:
    with pytest.raises(SetupFault) as excinfo:
        require_api_key({}, "mistral-large-latest", MISTRAL_WIRE)
    message = str(excinfo.value)
    assert "MISTRAL_API_KEY" in message
    assert "console.mistral.ai" in message


# --- D18: setup-class failures stop at first sight (workstream post-1.1/01) --------


async def test_cloud_404_is_fatal_at_first_sight(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    route = respx_mock.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            404, json={"error": {"message": "The model 'nope' does not exist"}}
        )
    )
    with pytest.raises(SetupFault, match="doesn't know the model"):
        await _chat(client).complete(CompletionRequest(system=None, user="x"))
    assert route.call_count == 1  # permanent 4xx never consumes retry budget


async def test_schema_rejection_400_is_fatal(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    respx_mock.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "Invalid 'response_format.json_schema': missing items"}},
        )
    )
    with pytest.raises(SetupFault, match="rejected the --schema"):
        await _chat(client).complete(CompletionRequest(system=None, user="x"))


async def test_other_400s_stay_item_errors(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    # a content-policy 400 on one item must not kill the run (spec §6.3)
    respx_mock.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "content flagged by moderation"}}
        )
    )
    with pytest.raises(ItemError, match="openai error 400"):
        await _chat(client).complete(CompletionRequest(system=None, user="x"))


async def test_embeddings_without_index_fall_back_to_arrival_order(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    # live-caught: Gemini's compat endpoint omits the "index" field
    respx_mock.post(f"{MISTRAL_BASE}/v1/embeddings").mock(
        return_value=httpx.Response(
            200, json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]}
        )
    )
    model = OpenAIEmbeddingModel(
        ref=parse_model_ref("mistral-embed"),
        client=client,
        base_url=MISTRAL_BASE,
        api_key="k",
        retry=FAST_RETRY,
    )
    assert await model.embed(["a", "b"]) == ((0.1, 0.2), (0.3, 0.4))


async def test_temperature_strips_and_retries_when_rejected(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    """o-series models 400 on explicit temperature — attempt, strip, retry once (D36)."""
    route = respx_mock.post("https://api.openai.com/v1/chat/completions")
    route.side_effect = [
        httpx.Response(
            400,
            json={"error": {"message": "Unsupported value: 'temperature' does not support 0.0"}},
        ),
        httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}),
    ]
    model = OpenAIChatModel(
        ref=parse_model_ref("o3-mini"),
        client=client,
        base_url="https://api.openai.com",
        api_key="k",
        retry=FAST_RETRY,
    )
    assert await model.complete(CompletionRequest(system=None, user="x")) == "ok"
    assert route.call_count == 2
    import json as jsonlib

    retried = jsonlib.loads(route.calls.last.request.content)
    assert "temperature" not in retried  # stripped on the second attempt
