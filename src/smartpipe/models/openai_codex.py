"""The ChatGPT-plan chat adapter (plan/decisions.md D19).

A login token doesn't speak the platform ``/v1/chat/completions``; it speaks the
**Responses API** at ``chatgpt.com/backend-api/codex/responses`` — streamed SSE,
Codex model family, ``ChatGPT-Account-Id`` + ``originator`` headers. Transcribed
from opencode's working wire (context/opencode). Tokens self-refresh (single-flight,
60 s skew) and rotations persist to the credential store; a 401 gets one refresh
and one retry before the "login expired" screen.

No embeddings exist on this wire — the container says so instead of pretending.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from smartpipe.config.credentials import OAuthCredential, load_oauth, save_oauth
from smartpipe.core.errors import (
    ItemError,
    RetryableError,
    SchemaRejected,
    SetupFault,
    TransportError,
)
from smartpipe.core.jsontools import as_items, as_record, as_str
from smartpipe.io import metering
from smartpipe.models.http_support import is_retryable_http, retry_after_seconds
from smartpipe.models.openai_oauth import refresh_tokens
from smartpipe.models.retry import RetryPolicy, with_retries

if TYPE_CHECKING:
    from pathlib import Path

    from smartpipe.models.base import CompletionRequest, ModelRef

__all__ = ["CODEX_ENDPOINT", "LOGIN_EXPIRED", "CodexChatModel", "accumulate_sse", "build_payload"]

CODEX_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
_SKEW_MS = 60_000  # refresh when within a minute of expiry — no mid-call surprises

LOGIN_EXPIRED = (
    "error: the ChatGPT login has expired and couldn't be refreshed\n  Fix: smartpipe auth login"
)


@dataclass(slots=True)
class CodexChatModel:
    """Mutable by design (documented Spinner-style exception): the credential
    rotates underneath us and the session id is per-run state."""

    ref: ModelRef
    client: httpx.AsyncClient
    store_path: Path
    credential: OAuthCredential
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def preflight(self, request: CompletionRequest) -> None:
        _validate_media(request)

    async def complete(self, request: CompletionRequest) -> str:
        self.preflight(request)
        await self._ensure_fresh()
        response = await self._post(request)
        if response.status_code == 401:  # one refresh, one retry, then be honest
            await self._force_refresh()
            response = await self._post(request)
            if response.status_code == 401:
                raise SetupFault(LOGIN_EXPIRED)
        if response.status_code == 404:  # D18: dooms every item — stop at the first
            from smartpipe.cli import screens

            raise SetupFault(screens.cloud_model_missing(self.ref.name, "the ChatGPT wire"))
        if response.status_code != 200:
            detail = _detail(response)
            lowered = detail.lower()
            if response.status_code == 400 and (
                "response_format" in lowered or "json_schema" in lowered
            ):
                from smartpipe.cli import screens

                raise SchemaRejected(screens.schema_rejected("the ChatGPT wire", detail))
            if response.status_code == 429:
                raise RetryableError(f"chatgpt wire error {response.status_code}: {detail}")
            if response.status_code >= 500:  # the wire, not the content
                raise TransportError(f"chatgpt wire error {response.status_code}: {detail}")
            raise ItemError(f"chatgpt wire error {response.status_code}: {detail}")
        text = accumulate_sse(response.text)
        if not text:
            raise ItemError("the model returned an empty reply")
        return text

    async def _post(self, request: CompletionRequest) -> httpx.Response:
        headers = {
            "authorization": f"Bearer {self.credential.access}",
            "originator": "smartpipe",
            "User-Agent": _user_agent(),
            "session-id": self.session_id,
            "Accept": "text/event-stream",
        }
        if self.credential.account_id is not None:
            headers["ChatGPT-Account-Id"] = self.credential.account_id
        payload = build_payload(self.ref.name, request)

        async def attempt() -> httpx.Response:
            response = await self.client.post(CODEX_ENDPOINT, json=payload, headers=headers)
            if response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()
            return response

        try:
            return await with_retries(
                self.retry,
                attempt,
                is_retryable=is_retryable_http,
                delay_hint=retry_after_seconds,
            )
        except httpx.HTTPStatusError as exc:
            detail = _detail(exc.response)
            status = exc.response.status_code
            if status == 429:
                raise RetryableError(f"chatgpt wire error {status}: {detail}") from exc
            raise TransportError(f"chatgpt wire error {status}: {detail}") from exc
        except httpx.HTTPError as exc:
            raise TransportError(f"request to the ChatGPT wire failed: {exc}") from exc

    async def _ensure_fresh(self) -> None:
        if self.credential.expires_ms - _SKEW_MS > time.time() * 1000:
            return
        await self._force_refresh()

    async def _force_refresh(self) -> None:
        async with self._refresh_lock:  # single-flight across concurrent workers
            stored = load_oauth(self.store_path, "openai")
            if stored is not None and stored.access != self.credential.access:
                self.credential = stored  # another worker/process already rotated
                if self.credential.expires_ms - _SKEW_MS > time.time() * 1000:
                    return
            try:
                rotated = await refresh_tokens(self.client, self.credential.refresh)
            except SetupFault as exc:
                raise SetupFault(LOGIN_EXPIRED) from exc
            self.credential = rotated
            save_oauth(self.store_path, "openai", rotated)


def build_payload(model: str, request: CompletionRequest) -> dict[str, object]:
    _validate_media(request)
    content: list[dict[str, object]] = [{"type": "input_text", "text": request.user}]
    from smartpipe.models.base import ImageData

    for part in request.media:
        if isinstance(part, ImageData):
            data_uri = f"data:{part.mime};base64,{base64.b64encode(part.data).decode()}"
            content.append({"type": "input_image", "image_url": data_uri})
    payload: dict[str, object] = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "stream": True,  # the codex wire streams; we accumulate to a final string
        "store": False,  # smartpipe is stateless — nothing parked server-side either
    }
    if request.system is not None:
        payload["instructions"] = request.system
    if request.json_schema is not None:
        from smartpipe.engine.schema import is_strict_compatible

        schema = dict(request.json_schema)
        payload["text"] = {
            "format": {
                "type": "json_schema",
                "name": "smartpipe_output",
                "schema": schema,
                # same 400 hazard as the platform wire: only claim strict when true
                "strict": is_strict_compatible(schema),
            }
        }
    return payload


def _validate_media(request: CompletionRequest) -> None:
    from smartpipe.models.base import AudioData, VideoData

    if any(isinstance(part, AudioData) for part in request.media):
        raise ItemError(
            "this model can't hear audio — try an audio model "
            "(voxtral, gemini) — smartpipe transcribes locally otherwise"
        )
    if any(isinstance(part, VideoData) for part in request.media):
        raise ItemError(
            "this endpoint can't watch video — map converts video to "
            "frames + audio automatically; use map, or split --by seconds"
        )


def accumulate_sse(body: str) -> str:
    """Fold the SSE stream to the final text: sum ``response.output_text.delta``
    events, preferring the terminal ``response.completed`` payload when present."""
    deltas: list[str] = []
    completed: str | None = None
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[len("data:") :].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            parsed: object = json.loads(raw)
        except json.JSONDecodeError:
            continue
        event = as_record(parsed)
        if event is None:
            continue
        kind = as_str(event.get("type"))
        if kind == "response.output_text.delta":
            delta = as_str(event.get("delta"))
            if delta is not None:
                deltas.append(delta)
        elif kind == "response.failed":
            raise ItemError(f"the model reported a failure: {_failure_detail(dict(event))}")
        elif kind == "response.completed":
            completed = _completed_text(event) or completed
            _meter_completed(event)
    return completed if completed is not None else "".join(deltas)


def _completed_text(event: dict[str, object] | object) -> str | None:
    record = as_record(event)
    response = as_record(record.get("response")) if record is not None else None
    output = as_items(response.get("output")) if response is not None else None
    if output is None:
        return None
    parts: list[str] = []
    for item in output:
        entry = as_record(item)
        if entry is None or entry.get("type") != "message":
            continue
        for chunk in as_items(entry.get("content")) or ():
            piece = as_record(chunk)
            if piece is None:
                continue
            text = as_str(piece.get("text"))
            if text is not None:
                parts.append(text)
    return "".join(parts) or None


def _failure_detail(event: dict[str, object]) -> str:
    response = as_record(event.get("response"))
    error = as_record(response.get("error")) if response is not None else None
    message = as_str(error.get("message")) if error is not None else None
    return message or "no detail"


def _detail(response: httpx.Response) -> str:
    text = response.text[:200].strip()
    return text or "no detail"


def _user_agent() -> str:
    from smartpipe import __version__

    return f"smartpipe/{__version__}"


def _meter_completed(event: object) -> None:
    record = as_record(event)
    response = as_record(record.get("response")) if record is not None else None
    usage = as_record(response.get("usage")) if response is not None else None
    if usage is None:
        return
    tokens_in = usage.get("input_tokens")
    tokens_out = usage.get("output_tokens")
    metering.add_tokens(
        tokens_in=tokens_in if isinstance(tokens_in, int) else 0,
        tokens_out=tokens_out if isinstance(tokens_out, int) else 0,
    )
