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

from sempipe.config.credentials import OAuthCredential, load_oauth, save_oauth
from sempipe.core.errors import ItemError, SetupFault
from sempipe.core.jsontools import as_items, as_record, as_str
from sempipe.models.openai_oauth import refresh_tokens

if TYPE_CHECKING:
    from pathlib import Path

    from sempipe.models.base import CompletionRequest, ModelRef

__all__ = ["CODEX_ENDPOINT", "LOGIN_EXPIRED", "CodexChatModel", "accumulate_sse", "build_payload"]

CODEX_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
_SKEW_MS = 60_000  # refresh when within a minute of expiry — no mid-call surprises

LOGIN_EXPIRED = (
    "error: the ChatGPT login has expired and couldn't be refreshed\n  Fix: sempipe auth login"
)


@dataclass(slots=True)
class CodexChatModel:
    """Mutable by design (documented Spinner-style exception): the credential
    rotates underneath us and the session id is per-run state."""

    ref: ModelRef
    client: httpx.AsyncClient
    store_path: Path
    credential: OAuthCredential
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def complete(self, request: CompletionRequest) -> str:
        await self._ensure_fresh()
        response = await self._post(request)
        if response.status_code == 401:  # one refresh, one retry, then be honest
            await self._force_refresh()
            response = await self._post(request)
            if response.status_code == 401:
                raise SetupFault(LOGIN_EXPIRED)
        if response.status_code != 200:
            raise ItemError(f"chatgpt wire error {response.status_code}: {_detail(response)}")
        text = accumulate_sse(response.text)
        if not text:
            raise ItemError("the model returned an empty reply")
        return text

    async def _post(self, request: CompletionRequest) -> httpx.Response:
        headers = {
            "authorization": f"Bearer {self.credential.access}",
            "originator": "sempipe",
            "User-Agent": _user_agent(),
            "session-id": self.session_id,
            "Accept": "text/event-stream",
        }
        if self.credential.account_id is not None:
            headers["ChatGPT-Account-Id"] = self.credential.account_id
        return await self.client.post(
            CODEX_ENDPOINT, json=build_payload(self.ref.name, request), headers=headers
        )

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
    content: list[dict[str, object]] = [{"type": "input_text", "text": request.user}]
    for image in request.images:
        data_uri = f"data:{image.mime};base64,{base64.b64encode(image.data).decode()}"
        content.append({"type": "input_image", "image_url": data_uri})
    payload: dict[str, object] = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "stream": True,  # the codex wire streams; we accumulate to a final string
        "store": False,  # sempipe is stateless — nothing parked server-side either
    }
    if request.system is not None:
        payload["instructions"] = request.system
    if request.json_schema is not None:
        payload["text"] = {
            "format": {
                "type": "json_schema",
                "name": "sempipe_output",
                "schema": dict(request.json_schema),
                "strict": True,
            }
        }
    return payload


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
    from sempipe import __version__

    return f"sempipe/{__version__}"
