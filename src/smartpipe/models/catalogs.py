"""Live model catalogs — one authenticated GET per provider (picker phase 2).

Every failure returns None and the picker degrades to typed input: a catalog
is a convenience, never a gate. Requests carry nothing but the key header,
run on short timeouts, and reuse the container's shared client. Ollama has no
fetcher here — its tags arrive with detection (``ollama_model_names``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from smartpipe.config.picker import (
    parse_anthropic_catalog,
    parse_gemini_catalog,
    parse_gemini_embed_catalog,
    parse_mistral_catalog,
    parse_mistral_embed_catalog,
    parse_openai_catalog,
    parse_openai_embed_catalog,
    parse_openrouter_catalog,
)
from smartpipe.models.openai_compat import (
    MISTRAL_WIRE,
    OPENROUTER_WIRE,
    resolve_base_url,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["CATALOG_TIMEOUT", "fetch_catalog", "fetch_embed_catalog"]

CATALOG_TIMEOUT = 4.0  # seconds — a slow catalog degrades to typed input, never blocks
_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"


async def fetch_catalog(
    provider: str, env: Mapping[str, str], client: httpx.AsyncClient
) -> tuple[str, ...] | None:
    """The provider's chat-model names, or None (no key, no wire, or any failure)."""
    match provider:
        case "openai":
            return await _openai(env, client)
        case "gemini":
            return await _gemini(env, client)
        case "anthropic":
            return await _anthropic(env, client)
        case "mistral":
            return await _mistral(env, client)
        case "openrouter":
            return await _openrouter(env, client)
        case _:
            return None


async def fetch_embed_catalog(
    provider: str, env: Mapping[str, str], client: httpx.AsyncClient
) -> tuple[str, ...] | None:
    """The provider's embedding-model names, or None. Providers without a
    fetchable embed catalog (jina, local, ollama) are the caller's curated
    lists - no wire exists for them here."""
    match provider:
        case "openai":
            payload = await _openai_models_payload(env, client)
            return parse_openai_embed_catalog(payload) if payload is not None else None
        case "gemini":
            payload = await _gemini_models_payload(env, client)
            return parse_gemini_embed_catalog(payload) if payload is not None else None
        case "mistral":
            payload = await _mistral_models_payload(env, client)
            return parse_mistral_embed_catalog(payload) if payload is not None else None
        case _:
            return None


async def _openai(env: Mapping[str, str], client: httpx.AsyncClient) -> tuple[str, ...] | None:
    payload = await _openai_models_payload(env, client)
    return parse_openai_catalog(payload) if payload is not None else None


async def _openai_models_payload(env: Mapping[str, str], client: httpx.AsyncClient) -> object:
    key = env.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None  # the ChatGPT-login wire has no /models endpoint
    return await _get_json(
        client,
        f"{resolve_base_url(env)}/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )


async def _gemini(env: Mapping[str, str], client: httpx.AsyncClient) -> tuple[str, ...] | None:
    payload = await _gemini_models_payload(env, client)
    return parse_gemini_catalog(payload) if payload is not None else None


async def _gemini_models_payload(env: Mapping[str, str], client: httpx.AsyncClient) -> object:
    key = env.get("GEMINI_API_KEY", "").strip() or env.get("GOOGLE_API_KEY", "").strip()
    if not key:
        return None
    from smartpipe.models.gemini_native import native_base_url

    return await _get_json(
        client,
        f"{native_base_url(env)}/models",
        headers={"x-goog-api-key": key},  # a header, never the URL — keys don't log
        params={"pageSize": "1000"},
    )


async def _anthropic(env: Mapping[str, str], client: httpx.AsyncClient) -> tuple[str, ...] | None:
    key = env.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    payload = await _get_json(
        client,
        f"{_ANTHROPIC_BASE_URL}/v1/models",
        headers={"x-api-key": key, "anthropic-version": _ANTHROPIC_VERSION},
        params={"limit": "100"},
    )
    return parse_anthropic_catalog(payload) if payload is not None else None


async def _mistral(env: Mapping[str, str], client: httpx.AsyncClient) -> tuple[str, ...] | None:
    payload = await _mistral_models_payload(env, client)
    return parse_mistral_catalog(payload) if payload is not None else None


async def _mistral_models_payload(env: Mapping[str, str], client: httpx.AsyncClient) -> object:
    key = env.get("MISTRAL_API_KEY", "").strip()
    if not key:
        return None
    return await _get_json(
        client,
        f"{resolve_base_url(env, MISTRAL_WIRE)}/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )


async def _openrouter(env: Mapping[str, str], client: httpx.AsyncClient) -> tuple[str, ...] | None:
    key = env.get("OPENROUTER_API_KEY", "").strip()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    payload = await _get_json(
        client,
        f"{resolve_base_url(env, OPENROUTER_WIRE)}/v1/models",  # the catalog itself is public
        headers=headers,
    )
    return parse_openrouter_catalog(payload) if payload is not None else None


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Mapping[str, str],
    params: Mapping[str, str] | None = None,
) -> object | None:
    try:
        response = await client.get(
            url, headers=dict(headers), params=dict(params or {}), timeout=CATALOG_TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):  # any wire or body trouble: degrade, never die
        return None
