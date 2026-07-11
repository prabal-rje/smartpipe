"""Anthropic (Claude) adapter via the official SDK.

Project policy (plan/decisions.md D04): Claude calls go through the ``anthropic``
SDK, not a hand-written protocol. The dependency remains lazy-imported for the
startup budget. Credentials and the underlying HTTP client are injected by the
composition root, so stored keys and transport policy have one authority; the
SDK owns only its bounded retry behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from smartpipe.cli import screens
from smartpipe.core.errors import (
    ItemError,
    RetryableError,
    SchemaRejected,
    SetupFault,
    TransportError,
)
from smartpipe.core.jsontools import as_str, record_at
from smartpipe.io import metering
from smartpipe.models.base import AudioData, ImageData
from smartpipe.models.retry import RetryPolicy

if TYPE_CHECKING:
    import httpx

    from smartpipe.models.base import CompletionRequest, ModelRef

__all__ = [
    "AnthropicChatModel",
    "build_anthropic_chat_model",
    "build_kwargs",
    "load_anthropic_client",
]

_TIMEOUT_SECONDS = 120.0


def load_anthropic_client(
    model: str,
    *,
    api_key: str,
    http_client: httpx.AsyncClient,
    retry: RetryPolicy,
) -> Any:
    try:
        import anthropic
    except ImportError as exc:
        raise SetupFault(screens.missing_anthropic_extra(model)) from exc
    if not api_key:
        raise SetupFault(_key_screen(model))
    try:
        return anthropic.AsyncAnthropic(
            api_key=api_key,
            http_client=http_client,
            timeout=_TIMEOUT_SECONDS,
            # The SDK names retries after the first try; our policy names all attempts.
            max_retries=retry.attempts - 1,
        )
    except anthropic.AnthropicError as exc:
        raise SetupFault(_key_screen(model)) from exc


def _key_screen(model: str) -> str:
    return screens.missing_api_key(model, "Anthropic", "ANTHROPIC_API_KEY", "sk-ant-...")


def build_anthropic_chat_model(
    ref: ModelRef,
    *,
    api_key: str,
    http_client: httpx.AsyncClient,
    retry: RetryPolicy,
) -> AnthropicChatModel:
    return AnthropicChatModel(
        ref=ref,
        client=load_anthropic_client(
            ref.name,
            api_key=api_key,
            http_client=http_client,
            retry=retry,
        ),
    )


@dataclass(frozen=True, slots=True)
class AnthropicChatModel:
    ref: ModelRef
    client: Any  # anthropic.AsyncAnthropic — untyped here to keep the SDK a soft dependency

    def preflight(self, request: CompletionRequest) -> None:
        _validate_media(request)

    async def complete(self, request: CompletionRequest) -> str:
        import anthropic

        self.preflight(request)
        kwargs = build_kwargs(self.ref.name, request)
        try:
            metering.add_request_media(request.media)
            message = await self.client.messages.create(**kwargs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise SetupFault(_key_screen(self.ref.name)) from exc
        except anthropic.APIConnectionError as exc:
            raise TransportError(f"request to the Anthropic API failed: {exc}") from exc
        except anthropic.APIStatusError as exc:
            detail = _status_detail(exc)
            lowered = detail.lower()
            if (
                exc.status_code == 400
                and "output_config" in kwargs
                and ("schema" in lowered or "format" in lowered)
            ):
                raise SchemaRejected(screens.schema_rejected("api.anthropic.com", detail)) from exc
            if exc.status_code == 429:
                raise RetryableError(f"anthropic error {exc.status_code}: {detail}") from exc
            if exc.status_code >= 500:  # the wire, not the content — the breaker counts these
                raise TransportError(f"anthropic error {exc.status_code}: {detail}") from exc
            raise ItemError(f"anthropic error {exc.status_code}: {detail}") from exc
        return _reply_text(self.ref.name, message)


def build_kwargs(name: str, request: CompletionRequest) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "model": name,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,  # reproducible by default (D36)
        "messages": [{"role": "user", "content": _user_content(request)}],
    }
    if request.system is not None:
        kwargs["system"] = request.system
    if request.json_schema is not None:
        kwargs["output_config"] = {
            "format": {"type": "json_schema", "schema": dict(request.json_schema)}
        }
    return kwargs


def _user_content(request: CompletionRequest) -> str | list[dict[str, object]]:
    """Plain string normally; image blocks (image first) when media rides along."""
    _validate_media(request)
    if not request.media:
        return request.user
    import base64

    blocks: list[dict[str, object]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": part.mime,
                "data": base64.b64encode(part.data).decode(),
            },
        }
        for part in request.media
        if isinstance(part, ImageData)
    ]
    blocks.append({"type": "text", "text": request.user})
    return blocks


def _validate_media(request: CompletionRequest) -> None:
    if any(isinstance(part, AudioData) for part in request.media):
        raise ItemError(
            "this model can't hear audio — try an audio model "
            "(voxtral, gemini) — smartpipe transcribes locally otherwise"
        )
    from smartpipe.models.base import VideoData

    if any(isinstance(part, VideoData) for part in request.media):
        raise ItemError(
            "this endpoint can't watch video — map converts video to "
            "frames + audio automatically; use map, or split --by seconds"
        )


def _reply_text(name: str, message: Any) -> str:
    if getattr(message, "stop_reason", None) == "refusal":
        raise ItemError(f"the model '{name}' declined this item")
    blocks = getattr(message, "content", []) or []
    text = "".join(block.text for block in blocks if getattr(block, "type", None) == "text")
    if not text:
        raise ItemError(f"the model '{name}' returned an empty reply")
    usage = getattr(message, "usage", None)
    tokens_in = getattr(usage, "input_tokens", None)
    tokens_out = getattr(usage, "output_tokens", None)
    metering.add_tokens(
        tokens_in=tokens_in if isinstance(tokens_in, int) else 0,
        tokens_out=tokens_out if isinstance(tokens_out, int) else 0,
    )
    return text


def _status_detail(exc: Any) -> str:
    error = record_at(getattr(exc, "body", None), "error")
    message = as_str(error.get("message")) if error is not None else None
    if message is not None:
        return message
    fallback = getattr(exc, "message", None)
    return fallback if isinstance(fallback, str) else str(exc)
