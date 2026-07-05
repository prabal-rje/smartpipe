"""OpenAI-compatible cloud adapter (OpenAI itself, or any compatible endpoint
via ``SEMPIPE_OPENAI_BASE_URL`` — Groq, Mistral, OpenRouter, llama.cpp, …).

Key rule (plan/stages/stage-02): a missing API key fails *before* any request
leaves the machine, with the screen that names the fix.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from sempipe.cli import screens
from sempipe.core.errors import ItemError, SetupFault
from sempipe.core.jsontools import as_float_vector, as_items, as_record, as_str, record_at
from sempipe.engine.schema import is_strict_compatible
from sempipe.models.http_support import is_retryable_http, retry_after_seconds
from sempipe.models.retry import RetryPolicy, with_retries

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sempipe.models.base import CompletionRequest, ModelRef

__all__ = [
    "DEFAULT_BASE_URL",
    "OpenAIChatModel",
    "OpenAIEmbeddingModel",
    "require_api_key",
    "resolve_base_url",
]

DEFAULT_BASE_URL = "https://api.openai.com"


def resolve_base_url(env: Mapping[str, str]) -> str:
    return env.get("SEMPIPE_OPENAI_BASE_URL", "").strip().rstrip("/") or DEFAULT_BASE_URL


def require_api_key(env: Mapping[str, str], model: str) -> str:
    key = env.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise SetupFault(screens.missing_api_key(model, "OpenAI", "OPENAI_API_KEY", "sk-..."))
    return key


@dataclass(frozen=True, slots=True)
class OpenAIChatModel:
    ref: ModelRef
    client: httpx.AsyncClient
    base_url: str
    api_key: str
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    async def complete(self, request: CompletionRequest) -> str:
        messages = [
            *(
                [{"role": "system", "content": request.system}]
                if request.system is not None
                else []
            ),
            {"role": "user", "content": _user_content(request)},
        ]
        payload: dict[str, object] = {"model": self.ref.name, "messages": messages}
        if request.json_schema is not None:
            schema = dict(request.json_schema)
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "sempipe_output",
                    "schema": schema,
                    # claiming strict for a schema with optional fields draws a 400;
                    # non-strict stays schema-guided, validate_and_coerce is the backstop
                    "strict": is_strict_compatible(schema),
                },
            }
        data = await _post(self, "/v1/chat/completions", payload)
        record = as_record(data)
        choices = as_items(record.get("choices")) if record is not None else None
        first = record_at(choices[0], "message") if choices else None
        content = as_str(first.get("content")) if first is not None else None
        if content is None:
            raise ItemError(f"{self.ref.provider} returned an unexpected reply shape")
        return content


@dataclass(frozen=True, slots=True)
class OpenAIEmbeddingModel:
    ref: ModelRef
    client: httpx.AsyncClient
    base_url: str
    api_key: str
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        payload: dict[str, object] = {"model": self.ref.name, "input": list(texts)}
        data = await _post(self, "/v1/embeddings", payload)
        record = as_record(data)
        rows = as_items(record.get("data")) if record is not None else None
        if rows is None:
            raise ItemError("embedding endpoint returned an unexpected shape")
        indexed: list[tuple[int, tuple[float, ...]]] = []
        for row in rows:
            entry = as_record(row)
            index = entry.get("index") if entry is not None else None
            vector = as_float_vector(entry.get("embedding")) if entry is not None else None
            if not isinstance(index, int) or vector is None:
                raise ItemError("embedding endpoint returned an unexpected shape")
            indexed.append((index, vector))
        return tuple(vector for _, vector in sorted(indexed))


async def _post(
    model: OpenAIChatModel | OpenAIEmbeddingModel, path: str, payload: Mapping[str, object]
) -> object:
    headers = {"Authorization": f"Bearer {model.api_key}"}

    async def attempt() -> object:
        response = await model.client.post(f"{model.base_url}{path}", json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    try:
        return await with_retries(
            model.retry, attempt, is_retryable=is_retryable_http, delay_hint=retry_after_seconds
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise SetupFault(
                f"error: the API key for '{model.ref.name}' was rejected "
                f"({exc.response.status_code})\n"
                "  The endpoint answered, but it didn't accept OPENAI_API_KEY.\n"
                "  Fix: check the key, or the endpoint in SEMPIPE_OPENAI_BASE_URL."
            ) from exc
        raise ItemError(
            f"{model.ref.provider} error {exc.response.status_code}: {_detail(exc.response)}"
        ) from exc
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        # ConnectTimeout is a TimeoutException, not a ConnectError — both mean
        # "couldn't establish a connection", so both map to the same screen.
        raise SetupFault(
            f"error: can't reach {model.base_url} ({exc})\n"
            f"  The model '{model.ref}' needs that endpoint.\n"
            "  Check your network, or SEMPIPE_OPENAI_BASE_URL if you pointed sempipe elsewhere."
        ) from exc
    except httpx.HTTPError as exc:
        raise ItemError(f"request to {model.base_url} failed: {exc}") from exc


def _detail(response: httpx.Response) -> str:
    try:
        record = as_record(response.json())
    except ValueError:
        record = None
    error = record_at(record, "error") if record is not None else None
    message = as_str(error.get("message")) if error is not None else None
    return message if message is not None else response.text[:200].strip() or "no detail"


def _user_content(request: CompletionRequest) -> str | list[dict[str, object]]:
    """Plain string normally; the content-array form when images ride along."""
    if not request.images:
        return request.user
    parts: list[dict[str, object]] = [{"type": "text", "text": request.user}]
    for image in request.images:
        data_uri = f"data:{image.mime};base64,{base64.b64encode(image.data).decode()}"
        parts.append({"type": "image_url", "image_url": {"url": data_uri}})
    return parts
