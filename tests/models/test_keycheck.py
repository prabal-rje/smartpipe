"""Live key validation - one tiny authenticated GET per provider, respx-pinned."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.models.keycheck import KeyRejected, KeyUnchecked, KeyValid, check_api_key
from tests.helpers.wire import sent_header

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import respx


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as instance:
        yield instance


async def test_openai_valid_key(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    verdict = await check_api_key("openai", "sk-live", {}, client)
    assert verdict == KeyValid()
    assert sent_header(route, "authorization") == "Bearer sk-live"


async def test_openai_rejected_key(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.openai.com/v1/models").mock(return_value=httpx.Response(401))
    verdict = await check_api_key("openai", "sk-bad", {}, client)
    assert verdict == KeyRejected("HTTP 401")


async def test_gemini_key_travels_in_a_header(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(
        "https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": "1"}
    ).mock(return_value=httpx.Response(200, json={"models": []}))
    verdict = await check_api_key("gemini", "g-key", {}, client)
    assert verdict == KeyValid()
    assert sent_header(route, "x-goog-api-key") == "g-key"  # a header, never the URL


async def test_anthropic_versioned_models_get(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("https://api.anthropic.com/v1/models", params={"limit": "1"}).mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    assert await check_api_key("anthropic", "sk-ant", {}, client) == KeyValid()
    assert sent_header(route, "x-api-key") == "sk-ant"
    assert sent_header(route, "anthropic-version") == "2023-06-01"


async def test_mistral_models_get(client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://api.mistral.ai/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    assert await check_api_key("mistral", "mk", {}, client) == KeyValid()


async def test_openrouter_uses_the_authenticated_key_endpoint(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    # the models catalog is public - only /v1/key can tell a bad key from a good one
    route = respx_mock.get("https://openrouter.ai/api/v1/key").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    assert await check_api_key("openrouter", "sk-or", {}, client) == KeyValid()
    assert sent_header(route, "authorization") == "Bearer sk-or"


async def test_jina_has_no_free_check(client: httpx.AsyncClient) -> None:
    verdict = await check_api_key("jina", "jk", {}, client)
    assert isinstance(verdict, KeyUnchecked)
    assert "jina" in verdict.reason


async def test_unknown_provider_is_unchecked(client: httpx.AsyncClient) -> None:
    assert isinstance(await check_api_key("future", "x", {}, client), KeyUnchecked)


async def test_network_trouble_reads_as_rejection_with_the_reason(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://api.mistral.ai/v1/models").mock(side_effect=httpx.ConnectError("boom"))
    verdict = await check_api_key("mistral", "mk", {}, client)
    assert isinstance(verdict, KeyRejected)
    assert "reach" in verdict.detail  # the three-way prompt says why storing anyway is sane


async def test_base_url_overrides_are_honored(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("http://localhost:9/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    env = {"SMARTPIPE_OPENAI_BASE_URL": "http://localhost:9"}
    assert await check_api_key("openai", "sk", env, client) == KeyValid()
