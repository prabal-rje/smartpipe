"""Gemini's native ``:generateContent`` wire (D34) — the only endpoint that
watches video.

Chat rides here (the compat shim carries text/images/audio but not video);
embeddings stay on the compat wire. Same taxonomy as every adapter: 401/403 →
key screen, 404 → model-missing at first sight (D18), schema 400 → the schema
screen, 429/5xx retried with ``Retry-After`` honored. Our JSON Schema subset is
translated to Gemini's response schema dialect; ``validate_and_coerce`` stays
the client-side backstop.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, assert_never

import httpx

from sempipe.cli import screens
from sempipe.core.errors import ItemError, SetupFault
from sempipe.core.jsontools import as_items, as_record, as_str
from sempipe.io import metering
from sempipe.models.base import AudioData, ImageData, VideoData
from sempipe.models.http_support import is_retryable_http, retry_after_seconds
from sempipe.models.openai_compat import GEMINI_WIRE, resolve_base_url
from sempipe.models.retry import RetryPolicy, with_retries

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sempipe.models.base import CompletionRequest, ModelRef

__all__ = ["GeminiNativeChatModel", "native_base_url", "to_gemini_schema"]


def native_base_url(env: Mapping[str, str]) -> str:
    """The native root sits one path segment above the OpenAI-compat root."""
    return resolve_base_url(env, GEMINI_WIRE).removesuffix("/openai")


@dataclass(frozen=True, slots=True)
class GeminiNativeChatModel:
    ref: ModelRef
    client: httpx.AsyncClient
    base_url: str  # the native root (…/v1beta)
    api_key: str
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    async def complete(self, request: CompletionRequest) -> str:
        payload: dict[str, object] = {"contents": [{"role": "user", "parts": _parts(request)}]}
        if request.system is not None:
            payload["systemInstruction"] = {"parts": [{"text": request.system}]}
        # NB: gemini-2.5 rejects presence/frequency penalties outright
        # ("Penalty is not enabled") — the anti-rambling fields are ollama-only (D35)
        config: dict[str, object] = {
            "maxOutputTokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.json_schema is not None:
            config["responseMimeType"] = "application/json"
            config["responseSchema"] = to_gemini_schema(request.json_schema)
        payload["generationConfig"] = config
        metering.add_request_media(request.media)
        data = await self._post(payload)
        record = as_record(data)
        candidates = as_items(record.get("candidates")) if record is not None else None
        content = as_record(candidates[0]) if candidates else None
        inner = as_record(content.get("content")) if content is not None else None
        parts = as_items(inner.get("parts")) if inner is not None else None
        if parts is None:
            raise ItemError("gemini returned an unexpected reply shape")
        texts = [
            text
            for part in parts
            if (entry := as_record(part)) is not None
            and (text := as_str(entry.get("text"))) is not None
        ]
        if not texts:
            raise ItemError("gemini returned an unexpected reply shape")
        usage = as_record(record.get("usageMetadata")) if record is not None else None
        if usage is not None:
            tokens_in = usage.get("promptTokenCount")
            tokens_out = usage.get("candidatesTokenCount")
            metering.add_tokens(
                tokens_in=tokens_in if isinstance(tokens_in, int) else 0,
                tokens_out=tokens_out if isinstance(tokens_out, int) else 0,
            )
        return "".join(texts)

    async def _post(self, payload: Mapping[str, object]) -> object:
        url = f"{self.base_url}/models/{self.ref.name}:generateContent"
        headers = {"x-goog-api-key": self.api_key}

        async def attempt() -> object:
            response = await self.client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

        try:
            return await with_retries(
                self.retry, attempt, is_retryable=is_retryable_http, delay_hint=retry_after_seconds
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise SetupFault(
                    f"error: the API key for '{self.ref.name}' was rejected ({status})\n"
                    f"  The endpoint answered, but it didn't accept {GEMINI_WIRE.key_env}.\n"
                    f"  Fix: check the key, or the endpoint in {GEMINI_WIRE.base_url_env}."
                ) from exc
            detail = _detail(exc.response)
            # D18: failures that doom every item identically stop at the first
            if status == 404:
                raise SetupFault(
                    screens.cloud_model_missing(self.ref.name, _host(self.base_url))
                ) from exc
            if status == 400 and ("responseSchema" in detail or "response_schema" in detail):
                raise SetupFault(screens.schema_rejected(_host(self.base_url), detail)) from exc
            raise ItemError(f"gemini error {status}: {detail}") from exc
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise SetupFault(
                f"error: can't reach {self.base_url} ({exc})\n"
                f"  The model '{self.ref}' needs that endpoint.\n"
                f"  Check your network, or {GEMINI_WIRE.base_url_env} if you pointed "
                "sempipe elsewhere."
            ) from exc
        except httpx.HTTPError as exc:
            raise ItemError(f"request to {self.base_url} failed: {exc}") from exc


def _parts(request: CompletionRequest) -> list[dict[str, object]]:
    parts: list[dict[str, object]] = [{"text": request.user}]
    for part in request.media:
        match part:
            case ImageData() | AudioData() | VideoData():
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": part.mime,
                            "data": base64.b64encode(part.data).decode("ascii"),
                        }
                    }
                )
            case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                assert_never(unreachable)
    return parts


_SCHEMA_KEYS = ("type", "properties", "required", "items", "enum", "description")


def to_gemini_schema(schema: Mapping[str, object]) -> dict[str, object]:
    """Our JSON Schema subset → Gemini's response-schema dialect: the shared
    keys carry over (types uppercased), everything else is dropped —
    ``validate_and_coerce`` remains the real guarantee."""
    translated: dict[str, object] = {}
    for key in _SCHEMA_KEYS:
        value = schema.get(key)
        if value is None:
            continue
        if key == "type" and isinstance(value, str):
            translated["type"] = value.upper()
        elif key == "properties":
            record = as_record(value)
            if record is not None:
                translated["properties"] = {
                    name: to_gemini_schema(narrowed)
                    for name in record
                    if (narrowed := as_record(record.get(name))) is not None
                }
        elif key == "items":
            child = as_record(value)
            if child is not None:
                translated["items"] = to_gemini_schema(child)
        else:
            translated[key] = value
    return translated


def _detail(response: httpx.Response) -> str:
    record = as_record(_safe_json(response))
    error = as_record(record.get("error")) if record is not None else None
    message = as_str(error.get("message")) if error is not None else None
    return message or response.text[:200]


def _safe_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _host(base_url: str) -> str:
    return httpx.URL(base_url).host or base_url
