"""Live catalog fetchers: one GET per provider, every failure degrades to None."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.models.catalogs import fetch_catalog

if TYPE_CHECKING:
    import respx


@pytest.fixture
async def client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


async def test_openai_catalog_needs_a_key_before_any_request(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("https://api.openai.com/v1/models")
    assert await fetch_catalog("openai", {}, client) is None
    assert route.call_count == 0  # ChatGPT-login-only setups have no /models wire


async def test_openai_catalog_happy_path(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "gpt-5.4-mini"}, {"id": "whisper-1"}]}
        )
    )
    names = await fetch_catalog("openai", {"OPENAI_API_KEY": "sk-test"}, client)
    assert names == ("gpt-5.4-mini",)


async def test_openai_restricted_key_degrades_to_none(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(403, json={"error": "missing scope api.model.read"})
    )
    assert await fetch_catalog("openai", {"OPENAI_API_KEY": "sk-test"}, client) is None


async def test_timeout_degrades_to_none(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://api.openai.com/v1/models").mock(
        side_effect=httpx.ConnectTimeout("slow")
    )
    assert await fetch_catalog("openai", {"OPENAI_API_KEY": "sk-test"}, client) is None


async def test_unparseable_body_degrades_to_none(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(200, text="<html>maintenance</html>")
    )
    assert await fetch_catalog("openai", {"OPENAI_API_KEY": "sk-test"}, client) is None


async def test_gemini_catalog_uses_the_native_wire_and_header_key(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"pageSize": "1000"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-2.5-flash",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ]
            },
        )
    )
    names = await fetch_catalog("gemini", {"GEMINI_API_KEY": "g-test"}, client)
    assert names == ("gemini-2.5-flash",)
    assert route.calls.last.request.headers["x-goog-api-key"] == "g-test"
    assert "g-test" not in str(route.calls.last.request.url)  # the key rides a header, not the URL


async def test_gemini_accepts_the_google_key_alias(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"pageSize": "1000"},
    ).mock(return_value=httpx.Response(200, json={"models": []}))
    assert await fetch_catalog("gemini", {"GOOGLE_API_KEY": "g2"}, client) == ()
    assert route.calls.last.request.headers["x-goog-api-key"] == "g2"


async def test_anthropic_catalog_sends_version_header(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("https://api.anthropic.com/v1/models", params={"limit": "100"}).mock(
        return_value=httpx.Response(200, json={"data": [{"id": "claude-sonnet-5"}]})
    )
    names = await fetch_catalog("anthropic", {"ANTHROPIC_API_KEY": "sk-ant"}, client)
    assert names == ("claude-sonnet-5",)
    request = route.calls.last.request
    assert request.headers["x-api-key"] == "sk-ant"
    assert request.headers["anthropic-version"] == "2023-06-01"


async def test_mistral_catalog_respects_the_wire_base_url(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("https://mistral.example/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"id": "mistral-small-latest", "capabilities": {"completion_chat": True}}]
            },
        )
    )
    env = {"MISTRAL_API_KEY": "m", "SMARTPIPE_MISTRAL_BASE_URL": "https://mistral.example"}
    assert await fetch_catalog("mistral", env, client) == ("mistral-small-latest",)


async def test_openrouter_catalog_is_public_and_vision_filtered(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "x-ai/grok-4.5",
                        "architecture": {"input_modalities": ["text", "image"]},
                    },
                    {"id": "textonly/model", "architecture": {"input_modalities": ["text"]}},
                ]
            },
        )
    )
    assert await fetch_catalog("openrouter", {}, client) == ("x-ai/grok-4.5",)
    assert "authorization" not in route.calls.last.request.headers  # no key needed, none sent


async def test_openrouter_sends_the_key_when_present(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await fetch_catalog("openrouter", {"OPENROUTER_API_KEY": "sk-or"}, client)
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-or"


async def test_unknown_provider_has_no_catalog(client: httpx.AsyncClient) -> None:
    assert await fetch_catalog("ollama", {}, client) is None  # tags come from detection
    assert await fetch_catalog("acme", {}, client) is None
