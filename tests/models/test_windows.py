"""D26 layer 1: the window probe — one metadata GET, never fatal."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from sempipe.models.base import parse_model_ref
from sempipe.models.windows import probe_context_window

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import respx

from sempipe.models.http_support import make_client


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as c:
        yield c


async def test_ollama_reports_the_arch_prefixed_length(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post("http://localhost:11434/api/show").mock(
        return_value=httpx.Response(
            200, json={"model_info": {"qwen3.context_length": 40960, "qwen3.head_count": 32}}
        )
    )
    window = await probe_context_window(parse_model_ref("ollama/qwen3:8b"), client=client, env={})
    assert window == 40960


async def test_mistral_reports_max_context_length(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://api.mistral.ai/v1/models/mistral-small-latest").mock(
        return_value=httpx.Response(
            200, json={"id": "mistral-small-latest", "max_context_length": 131072}
        )
    )
    window = await probe_context_window(
        parse_model_ref("mistral-small-latest"), client=client, env={"MISTRAL_API_KEY": "k"}
    )
    assert window == 131072


async def test_openrouter_scans_the_model_list(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "other/model", "context_length": 4096},
                    {"id": "deepseek/deepseek-chat", "context_length": 65536},
                ]
            },
        )
    )
    window = await probe_context_window(
        parse_model_ref("openrouter/deepseek/deepseek-chat"), client=client, env={}
    )
    assert window == 65536


async def test_gemini_native_endpoint(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash").mock(
        return_value=httpx.Response(200, json={"inputTokenLimit": 1048576})
    )
    window = await probe_context_window(
        parse_model_ref("gemini-2.5-flash"), client=client, env={"GEMINI_API_KEY": "g"}
    )
    assert window == 1048576


async def test_openai_and_anthropic_publish_nothing(client: httpx.AsyncClient) -> None:
    assert await probe_context_window(parse_model_ref("gpt-4o-mini"), client=client, env={}) is None
    assert (
        await probe_context_window(parse_model_ref("claude-haiku-4-5"), client=client, env={})
        is None
    )


async def test_a_failed_probe_is_never_fatal(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post("http://localhost:11434/api/show").mock(
        return_value=httpx.Response(500, text="boom")
    )
    window = await probe_context_window(parse_model_ref("ollama/qwen3:8b"), client=client, env={})
    assert window is None
