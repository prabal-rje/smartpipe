"""Live API-key validation for ``smartpipe auth login`` - one tiny GET, no spend.

Every provider gets exactly one authenticated catalog-style GET (openrouter's
catalog is public, so its dedicated ``/v1/key`` endpoint stands in). The verdict
is a discriminated union the flow dispatches with ``match``: a rejection is
never fatal - the provider may be having a bad minute, so the caller offers
retry / store anyway / skip. Key material never appears in errors or logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["CHECK_TIMEOUT", "KeyRejected", "KeyUnchecked", "KeyValid", "check_api_key"]

CHECK_TIMEOUT = 6.0  # seconds - a login prompt can afford a breath more than the picker
_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True, slots=True)
class KeyValid:
    """The provider answered the authenticated GET - the key works."""


@dataclass(frozen=True, slots=True)
class KeyRejected:
    """The check failed - a bad key, or a provider having a bad minute."""

    detail: str  # "HTTP 401", "couldn't reach api.mistral.ai (ConnectError)"


@dataclass(frozen=True, slots=True)
class KeyUnchecked:
    """No free validation wire exists - stored on trust, verified by use."""

    reason: str


KeyVerdict = KeyValid | KeyRejected | KeyUnchecked


async def check_api_key(
    provider: str, key: str, env: Mapping[str, str], client: httpx.AsyncClient
) -> KeyVerdict:
    """One authenticated GET against the provider, or ``KeyUnchecked``."""
    from smartpipe.models.openai_compat import (
        MISTRAL_WIRE,
        OPENROUTER_WIRE,
        resolve_base_url,
    )

    match provider:
        case "openai":
            url = f"{resolve_base_url(env)}/v1/models"
            return await _get(client, url, headers={"Authorization": f"Bearer {key}"})
        case "gemini":
            from smartpipe.models.gemini_native import native_base_url

            return await _get(
                client,
                f"{native_base_url(env)}/models",
                headers={"x-goog-api-key": key},  # a header, never the URL - keys don't log
                params={"pageSize": "1"},
            )
        case "anthropic":
            return await _get(
                client,
                f"{_ANTHROPIC_BASE_URL}/v1/models",
                headers={"x-api-key": key, "anthropic-version": _ANTHROPIC_VERSION},
                params={"limit": "1"},
            )
        case "mistral":
            url = f"{resolve_base_url(env, MISTRAL_WIRE)}/v1/models"
            return await _get(client, url, headers={"Authorization": f"Bearer {key}"})
        case "openrouter":
            # the models catalog is public - only the key-info endpoint authenticates
            url = f"{resolve_base_url(env, OPENROUTER_WIRE)}/v1/key"
            return await _get(client, url, headers={"Authorization": f"Bearer {key}"})
        case "jina":
            return KeyUnchecked("jina has no free check endpoint - the first embed verifies it")
        case _:
            return KeyUnchecked(f"no check wire for {provider!r}")


async def _get(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Mapping[str, str],
    params: Mapping[str, str] | None = None,
) -> KeyVerdict:
    try:
        response = await client.get(
            url, headers=dict(headers), params=dict(params or {}), timeout=CHECK_TIMEOUT
        )
    except httpx.HTTPError as exc:
        host = httpx.URL(url).host
        return KeyRejected(f"couldn't reach {host} ({type(exc).__name__})")
    if response.status_code < 400:
        return KeyValid()
    return KeyRejected(f"HTTP {response.status_code}")
