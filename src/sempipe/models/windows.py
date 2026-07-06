"""Dynamic context-window discovery (D26 layer 1).

Four of the six wires publish their window; asking costs one cheap metadata GET,
so the probe runs at most once per run, lazily, and only when the static table's
budget already looks too small. A failed probe is never fatal: the conservative
table stays the floor, and reduce's bisection is the backstop when everything lies.
OpenAI and Anthropic don't expose window size via API; ``SEMPIPE_CONTEXT_TOKENS``
covers them (and overrides everything else).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sempipe.core.jsontools import as_items, as_record

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx

    from sempipe.models.base import ModelRef

__all__ = ["probe_context_window"]

_PROBE_TIMEOUT_S = 5.0


async def probe_context_window(
    ref: ModelRef, *, client: httpx.AsyncClient, env: Mapping[str, str]
) -> int | None:
    """The provider's own answer to "how big is this model's window?", or None."""
    try:
        match ref.provider:
            case "ollama":
                return await _ollama(ref.name, client, env)
            case "mistral":
                return await _mistral(ref.name, client, env)
            case "openrouter":
                return await _openrouter(ref.name, client, env)
            case "gemini":
                return await _gemini(ref.name, client, env)
            case _:  # openai/anthropic publish no window via API
                return None
    except Exception:
        return None


async def _ollama(name: str, client: httpx.AsyncClient, env: Mapping[str, str]) -> int | None:
    from sempipe.models.ollama import resolve_host

    response = await client.post(
        f"{resolve_host(env)}/api/show", json={"model": name}, timeout=_PROBE_TIMEOUT_S
    )
    response.raise_for_status()
    record = as_record(response.json())
    info = as_record(record.get("model_info")) if record is not None else None
    if info is None:
        return None
    # the key is architecture-prefixed: "llama.context_length", "qwen3.context_length", …
    lengths = (value for key, value in info.items() if key.endswith(".context_length"))
    first = next(lengths, None)
    return first if isinstance(first, int) else None


async def _mistral(name: str, client: httpx.AsyncClient, env: Mapping[str, str]) -> int | None:
    from sempipe.models.openai_compat import MISTRAL_WIRE, resolve_base_url

    key = env.get(MISTRAL_WIRE.key_env, "").strip()
    if not key:
        return None
    response = await client.get(
        f"{resolve_base_url(env, MISTRAL_WIRE)}/v1/models/{name}",
        headers={"Authorization": f"Bearer {key}"},
        timeout=_PROBE_TIMEOUT_S,
    )
    response.raise_for_status()
    record = as_record(response.json())
    value = record.get("max_context_length") if record is not None else None
    return value if isinstance(value, int) else None


async def _openrouter(name: str, client: httpx.AsyncClient, env: Mapping[str, str]) -> int | None:
    from sempipe.models.openai_compat import OPENROUTER_WIRE, resolve_base_url

    response = await client.get(
        f"{resolve_base_url(env, OPENROUTER_WIRE)}/v1/models", timeout=_PROBE_TIMEOUT_S
    )
    response.raise_for_status()
    record = as_record(response.json())
    rows = as_items(record.get("data")) if record is not None else None
    if rows is None:
        return None
    for row in rows:
        entry = as_record(row)
        if entry is not None and entry.get("id") == name:
            value = entry.get("context_length")
            return value if isinstance(value, int) else None
    return None


async def _gemini(name: str, client: httpx.AsyncClient, env: Mapping[str, str]) -> int | None:
    from sempipe.models.openai_compat import GEMINI_WIRE, resolve_base_url

    key = env.get(GEMINI_WIRE.key_env, "").strip()
    if not key:
        return None
    # the native endpoint lives one path segment above the OpenAI-compat root
    native = resolve_base_url(env, GEMINI_WIRE).removesuffix("/openai")
    response = await client.get(
        f"{native}/models/{name}", headers={"x-goog-api-key": key}, timeout=_PROBE_TIMEOUT_S
    )
    response.raise_for_status()
    record = as_record(response.json())
    value = record.get("inputTokenLimit") if record is not None else None
    return value if isinstance(value, int) else None
