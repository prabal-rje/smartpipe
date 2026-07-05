"""Anthropic (Claude) adapter via the official SDK — an optional extra.

Project policy (plan/decisions.md D04): Claude calls go through the ``anthropic``
SDK, not raw HTTP. The SDK is lazy-imported so the core install stays SDK-free; a
``claude-*`` model without the extra produces the actionable missing-extra screen.
The SDK owns its own retries and credential resolution (``ANTHROPIC_API_KEY``,
``ANTHROPIC_AUTH_TOKEN``, ``ant`` profiles) — we never pre-check the env var, and a
rejected key surfaces as the key screen at request time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sempipe.cli import screens
from sempipe.core.errors import ItemError, SetupFault
from sempipe.core.jsontools import as_str, record_at

if TYPE_CHECKING:
    from sempipe.models.base import CompletionRequest, ModelRef

__all__ = [
    "AnthropicChatModel",
    "build_anthropic_chat_model",
    "build_kwargs",
    "load_anthropic_client",
]

_TIMEOUT_SECONDS = 120.0
# The SDK's built-in retries already honor Retry-After on 429 (unlike our raw-httpx
# adapters, which route the header through retry.with_retries' delay_hint).
_MAX_RETRIES = 3


def load_anthropic_client(model: str) -> Any:
    try:
        import anthropic
    except ImportError as exc:
        raise SetupFault(screens.missing_anthropic_extra(model)) from exc
    try:
        return anthropic.AsyncAnthropic(timeout=_TIMEOUT_SECONDS, max_retries=_MAX_RETRIES)
    except anthropic.AnthropicError as exc:  # e.g. no credentials resolvable at all
        raise SetupFault(_key_screen(model)) from exc


def _key_screen(model: str) -> str:
    return screens.missing_api_key(model, "Anthropic", "ANTHROPIC_API_KEY", "sk-ant-...")


def build_anthropic_chat_model(ref: ModelRef) -> AnthropicChatModel:
    return AnthropicChatModel(ref=ref, client=load_anthropic_client(ref.name))


@dataclass(frozen=True, slots=True)
class AnthropicChatModel:
    ref: ModelRef
    client: Any  # anthropic.AsyncAnthropic — untyped here to keep the SDK a soft dependency

    async def complete(self, request: CompletionRequest) -> str:
        import anthropic

        kwargs = build_kwargs(self.ref.name, request)
        try:
            message = await self.client.messages.create(**kwargs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise SetupFault(_key_screen(self.ref.name)) from exc
        except anthropic.APIConnectionError as exc:
            raise SetupFault(
                f"error: can't reach the Anthropic API ({exc})\n"
                f"  The model '{self.ref}' needs api.anthropic.com.\n"
                "  Check your network connection and try again."
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ItemError(f"anthropic error {exc.status_code}: {_status_detail(exc)}") from exc
        return _reply_text(self.ref.name, message)


def build_kwargs(name: str, request: CompletionRequest) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "model": name,
        "max_tokens": request.max_tokens,
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
    """Plain string normally; image blocks (image first) when images ride along."""
    if not request.images:
        return request.user
    import base64

    blocks: list[dict[str, object]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image.mime,
                "data": base64.b64encode(image.data).decode(),
            },
        }
        for image in request.images
    ]
    blocks.append({"type": "text", "text": request.user})
    return blocks


def _reply_text(name: str, message: Any) -> str:
    if getattr(message, "stop_reason", None) == "refusal":
        raise ItemError(f"the model '{name}' declined this item")
    blocks = getattr(message, "content", []) or []
    text = "".join(block.text for block in blocks if getattr(block, "type", None) == "text")
    if not text:
        raise ItemError(f"the model '{name}' returned an empty reply")
    return text


def _status_detail(exc: Any) -> str:
    error = record_at(getattr(exc, "body", None), "error")
    message = as_str(error.get("message")) if error is not None else None
    if message is not None:
        return message
    fallback = getattr(exc, "message", None)
    return fallback if isinstance(fallback, str) else str(exc)
