"""OpenAI-compatible cloud adapter (OpenAI itself, or any compatible endpoint
via ``SMARTPIPE_OPENAI_BASE_URL`` — Groq, Mistral, OpenRouter, llama.cpp, …).

Key rule (plan/stages/stage-02): a missing API key fails *before* any request
leaves the machine, with the screen that names the fix.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, assert_never

import httpx

from smartpipe.cli import screens
from smartpipe.core.errors import ItemError, SetupFault
from smartpipe.core.jsontools import as_float_vector, as_items, as_record, as_str, record_at
from smartpipe.engine.schema import is_strict_compatible
from smartpipe.io import metering
from smartpipe.models.base import AudioData, ImageData, VideoData
from smartpipe.models.http_support import is_retryable_http, retry_after_seconds
from smartpipe.models.retry import RetryPolicy, with_retries

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from smartpipe.models.base import CompletionRequest, ModelRef

__all__ = [
    "DEFAULT_BASE_URL",
    "GEMINI_WIRE",
    "MISTRAL_WIRE",
    "OPENAI_WIRE",
    "OPENROUTER_WIRE",
    "OpenAIChatModel",
    "OpenAIEmbeddingModel",
    "WireConfig",
    "require_api_key",
    "resolve_base_url",
]


@dataclass(frozen=True, slots=True)
class WireConfig:
    """Everything provider-specific about an OpenAI-wire endpoint — the adapter
    itself is one; providers differ only in these strings."""

    provider: str
    display: str  # how screens name the provider ("OpenAI", "Mistral")
    default_base_url: str
    base_url_env: str
    key_env: str
    key_hint: str  # the copy-pasteable key shape in the missing-key screen
    key_note: str = "add it to your shell profile to persist"


OPENAI_WIRE = WireConfig(
    provider="openai",
    display="OpenAI",
    default_base_url="https://api.openai.com",
    base_url_env="SMARTPIPE_OPENAI_BASE_URL",
    key_env="OPENAI_API_KEY",
    key_hint="sk-...",
)

MISTRAL_WIRE = WireConfig(
    provider="mistral",
    display="Mistral",
    default_base_url="https://api.mistral.ai",
    base_url_env="SMARTPIPE_MISTRAL_BASE_URL",
    key_env="MISTRAL_API_KEY",
    key_hint="...",
    key_note="create one at console.mistral.ai",
)

GEMINI_WIRE = WireConfig(
    provider="gemini",
    display="Gemini",
    # live-scouted: the compat endpoint tolerates our /v1/... path shape
    default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    base_url_env="SMARTPIPE_GEMINI_BASE_URL",
    key_env="GEMINI_API_KEY",
    key_hint="...",
    key_note="create one at aistudio.google.com",
)

OPENROUTER_WIRE = WireConfig(
    provider="openrouter",
    display="OpenRouter",
    default_base_url="https://openrouter.ai/api",
    base_url_env="SMARTPIPE_OPENROUTER_BASE_URL",
    key_env="OPENROUTER_API_KEY",
    key_hint="sk-or-...",
    key_note="create one at openrouter.ai/keys",
)

DEFAULT_BASE_URL = OPENAI_WIRE.default_base_url


def resolve_base_url(env: Mapping[str, str], wire: WireConfig = OPENAI_WIRE) -> str:
    return env.get(wire.base_url_env, "").strip().rstrip("/") or wire.default_base_url


def require_api_key(env: Mapping[str, str], model: str, wire: WireConfig = OPENAI_WIRE) -> str:
    key = env.get(wire.key_env, "").strip()
    if not key:
        raise SetupFault(
            screens.missing_api_key(
                model, wire.display, wire.key_env, wire.key_hint, note=wire.key_note
            )
        )
    return key


@dataclass(frozen=True, slots=True)
class OpenAIChatModel:
    ref: ModelRef
    client: httpx.AsyncClient
    base_url: str
    api_key: str
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    wire: WireConfig = OPENAI_WIRE  # names the right env vars in error screens

    async def complete(self, request: CompletionRequest) -> str:
        messages = [
            *(
                [{"role": "system", "content": request.system}]
                if request.system is not None
                else []
            ),
            {"role": "user", "content": _user_content(request)},
        ]
        payload: dict[str, object] = {
            "model": self.ref.name,
            "messages": messages,
            "temperature": request.temperature,  # reproducible by default (D36)
        }
        if request.json_schema is not None:
            schema = dict(request.json_schema)
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "smartpipe_output",
                    "schema": schema,
                    # claiming strict for a schema with optional fields draws a 400;
                    # non-strict stays schema-guided, validate_and_coerce is the backstop
                    "strict": is_strict_compatible(schema),
                },
            }
        metering.add_request_media(request.media)
        try:
            data = await _post(self, "/v1/chat/completions", payload, has_media=bool(request.media))
        except ItemError as exc:
            # o-series models reject explicit temperature — strip and retry once
            # (capability by attempt, D36; no model-name sniffing)
            if "temperature" not in str(exc) or "temperature" not in payload:
                raise
            payload.pop("temperature")
            data = await _post(self, "/v1/chat/completions", payload, has_media=bool(request.media))
        record = as_record(data)
        choices = as_items(record.get("choices")) if record is not None else None
        first = record_at(choices[0], "message") if choices else None
        content = as_str(first.get("content")) if first is not None else None
        if content is None:
            raise ItemError(f"{self.ref.provider} returned an unexpected reply shape")
        _meter_chat_usage(record)
        return content


@dataclass(frozen=True, slots=True)
class OpenAIEmbeddingModel:
    ref: ModelRef
    client: httpx.AsyncClient
    base_url: str
    api_key: str
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    wire: WireConfig = OPENAI_WIRE

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        payload: dict[str, object] = {"model": self.ref.name, "input": list(texts)}
        data = await _post(self, "/v1/embeddings", payload)
        record = as_record(data)
        rows = as_items(record.get("data")) if record is not None else None
        if rows is None:
            raise ItemError("embedding endpoint returned an unexpected shape")
        indexed: list[tuple[int, tuple[float, ...]]] = []
        for position, row in enumerate(rows):
            entry = as_record(row)
            vector = as_float_vector(entry.get("embedding")) if entry is not None else None
            if entry is None or vector is None:
                raise ItemError("embedding endpoint returned an unexpected shape")
            index = entry.get("index")
            # live-caught: Gemini's compat endpoint omits "index" — arrival order then
            indexed.append((index if isinstance(index, int) else position, vector))
        _meter_chat_usage(record)
        return tuple(vector for _, vector in sorted(indexed))


async def _post(
    model: OpenAIChatModel | OpenAIEmbeddingModel,
    path: str,
    payload: Mapping[str, object],
    *,
    has_media: bool = False,
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
        status = exc.response.status_code
        if status in (401, 403):
            reason = _detail(exc.response)
            scoped = "scope" in reason.lower() or "permission" in reason.lower()
            if scoped and has_media:
                # a RESTRICTED key refusing a media request is a capability
                # statement, not a dead key (text works) — per-item, so the
                # ladders and skip machinery take it from here (D43c)
                raise ItemError(
                    f"this key can't send media ({reason[:80]}) — grant the "
                    "scope or use an unrestricted key"
                ) from exc
            hint = (
                "  This key is RESTRICTED — grant the missing scope (or use an\n"
                "  unrestricted project key) in your provider console."
                if scoped
                else f"  Fix: check the key, or the endpoint in {model.wire.base_url_env}."
            )
            raise SetupFault(
                f"error: the API key for '{model.ref.name}' was rejected "
                f"({status})\n"
                f"  The endpoint answered but didn't accept {model.wire.key_env}.\n"
                f"  It said: {reason[:160]}\n" + hint
            ) from exc
        detail = _detail(exc.response)
        # D18: failures that doom every item identically stop the run at the first
        if status == 404:
            raise SetupFault(screens.cloud_model_missing(model.ref.name, _host(model))) from exc
        if status == 400 and ("response_format" in detail or "json_schema" in detail):
            raise SetupFault(screens.schema_rejected(_host(model), detail)) from exc
        raise ItemError(f"{model.ref.provider} error {status}: {detail}") from exc
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        # ConnectTimeout is a TimeoutException, not a ConnectError — both mean
        # "couldn't establish a connection", so both map to the same screen.
        raise SetupFault(
            f"error: can't reach {model.base_url} ({exc})\n"
            f"  The model '{model.ref}' needs that endpoint.\n"
            f"  Check your network, or {model.wire.base_url_env} if you pointed\n"
            "  smartpipe elsewhere."
        ) from exc
    except httpx.HTTPError as exc:
        raise ItemError(f"request to {model.base_url} failed: {exc}") from exc


def _host(model: OpenAIChatModel | OpenAIEmbeddingModel) -> str:
    return model.base_url.removeprefix("https://").removeprefix("http://")


def _detail(response: httpx.Response) -> str:
    try:
        record = as_record(response.json())
    except ValueError:
        record = None
    error = record_at(record, "error") if record is not None else None
    message = as_str(error.get("message")) if error is not None else None
    return message if message is not None else response.text[:200].strip() or "no detail"


_AUDIO_FORMATS = {  # OpenAI-wire input_audio formats by mime; anything else fails free
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


def _user_content(request: CompletionRequest) -> str | list[dict[str, object]]:
    """Plain string normally; the content-array form when media rides along (D20 §3:
    a modality is one more renderer in this builder, never a new adapter)."""
    if not request.media:
        return request.user
    parts: list[dict[str, object]] = [{"type": "text", "text": request.user}]
    for part in request.media:
        match part:
            case ImageData():
                data_uri = f"data:{part.mime};base64,{base64.b64encode(part.data).decode()}"
                parts.append({"type": "image_url", "image_url": {"url": data_uri}})
            case AudioData():
                fmt = _AUDIO_FORMATS.get(part.mime)
                if fmt is None:
                    # never guess a format at a paid endpoint — fail before the spend
                    raise ItemError(
                        f"audio format {part.mime} isn't sendable — "
                        "wav or mp3 reach audio models natively; "
                        "other formats transcribe locally"
                    )
                encoded = base64.b64encode(part.data).decode()
                parts.append(
                    {"type": "input_audio", "input_audio": {"data": encoded, "format": fmt}}
                )
            case VideoData():
                raise ItemError(
                    "this endpoint can't watch video — map converts video to "
                    "frames + audio automatically; use map, or split --by seconds"
                )
            case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                assert_never(unreachable)
    return parts


def _meter_chat_usage(record: Mapping[str, object] | None) -> None:
    usage = as_record(record.get("usage")) if record is not None else None
    if usage is None:
        return
    tokens_in = usage.get("prompt_tokens")
    tokens_out = usage.get("completion_tokens")
    metering.add_tokens(
        tokens_in=tokens_in if isinstance(tokens_in, int) else 0,
        tokens_out=tokens_out if isinstance(tokens_out, int) else 0,
    )
