"""The local-first default provider (plan/architecture.md adapter table).

Failure philosophy: a refused connection or a missing model fails *fast* with a
screen that names the fix (a local daemon being down is a setup problem, not a
transient blip); 429/5xx/timeouts are retried, then skip just that item.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from smartpipe.cli import screens
from smartpipe.core.errors import ItemError, SetupFault, TransportError
from smartpipe.core.jsontools import as_float_vector, as_items, as_record, as_str, record_at
from smartpipe.io import metering
from smartpipe.models.base import AudioData, ImageData, VideoData
from smartpipe.models.http_support import is_retryable_http, retry_after_seconds
from smartpipe.models.retry import RetryPolicy, with_retries

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from smartpipe.models.base import CompletionRequest, ModelRef

_NO_VISION = "model can't read images — try a vision model, e.g. --model ollama/qwen3-vl"

__all__ = [
    "DEFAULT_HOST",
    "OllamaChatModel",
    "OllamaEmbeddingModel",
    "ollama_model_names",
    "resolve_host",
]

DEFAULT_HOST = "http://localhost:11434"


def resolve_host(env: Mapping[str, str]) -> str:
    host = env.get("OLLAMA_HOST", "").strip() or DEFAULT_HOST
    if "://" not in host:  # OLLAMA_HOST convention allows bare host:port
        host = f"http://{host}"
    return host.rstrip("/")


@dataclass(frozen=True, slots=True)
class OllamaChatModel:
    ref: ModelRef
    client: httpx.AsyncClient
    host: str
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    async def complete(self, request: CompletionRequest) -> str:
        messages: list[dict[str, object]] = []
        if request.system is not None:
            messages.append({"role": "system", "content": request.system})
        user: dict[str, object] = {"role": "user", "content": request.user}
        if any(isinstance(part, VideoData) for part in request.media):
            raise ItemError(
                "this model can't watch video — map converts it to frames + audio "
                "automatically (gemini watches natively)"
            )
        if any(isinstance(part, AudioData) for part in request.media):
            # ollama's chat API carries images only — fail before any bytes leave (D20 §2)
            raise ItemError(
                "this model can't hear audio — try an audio model "
                "(voxtral, gemini) — smartpipe transcribes locally otherwise"
            )
        images = [part for part in request.media if isinstance(part, ImageData)]
        if images:
            user["images"] = [base64.b64encode(image.data).decode() for image in images]
        messages.append(user)
        options: dict[str, object] = {
            "num_predict": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.presence_penalty is not None:
            options["presence_penalty"] = request.presence_penalty
        if request.frequency_penalty is not None:
            options["frequency_penalty"] = request.frequency_penalty
        payload: dict[str, object] = {
            "model": self.ref.name,
            "stream": False,
            "messages": messages,
            "options": options,  # tiny local models ramble unbounded otherwise (D35)
        }
        if request.json_schema is not None:
            payload["format"] = dict(request.json_schema)
        metering.add_request_media(request.media)
        try:
            data = await _post(self, "/api/chat", payload)
        except ItemError as exc:
            # _post maps a 400 to ItemError; with images in flight that almost
            # always means "this model has no vision" — say so, name a fix.
            if request.media and "error 400" in str(exc):
                raise ItemError(_NO_VISION) from exc
            raise
        message = record_at(data, "message")
        content = as_str(message.get("content")) if message is not None else None
        if content is None:
            raise ItemError("ollama returned an unexpected reply shape")
        _meter_usage(data)
        return content


@dataclass(frozen=True, slots=True)
class OllamaEmbeddingModel:
    ref: ModelRef
    client: httpx.AsyncClient
    host: str
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        payload: dict[str, object] = {"model": self.ref.name, "input": list(texts)}
        data = await _post(self, "/api/embed", payload)
        record = as_record(data)
        rows = as_items(record.get("embeddings")) if record is not None else None
        if rows is None:
            raise ItemError("ollama returned an unexpected embeddings shape")
        vectors: list[tuple[float, ...]] = []
        for row in rows:
            vector = as_float_vector(row)
            if vector is None:
                raise ItemError("ollama returned an unexpected embeddings shape")
            vectors.append(vector)
        return tuple(vectors)


async def ollama_model_names(client: httpx.AsyncClient, host: str) -> tuple[str, ...] | None:
    """Installed model names, or None when no Ollama is listening (a probe, never fatal)."""
    try:
        response = await client.get(f"{host}/api/tags", timeout=2.0)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    models = as_record(response.json())
    entries = as_items(models.get("models")) if models is not None else None
    if entries is None:
        return None
    names: list[str] = []
    for entry in entries:
        record = as_record(entry)
        name = as_str(record.get("name")) if record is not None else None
        if name is not None:
            names.append(name)
    return tuple(names)


async def _post(
    model: OllamaChatModel | OllamaEmbeddingModel, path: str, payload: Mapping[str, object]
) -> object:
    async def attempt() -> object:
        try:
            response = await model.client.post(f"{model.host}{path}", json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # A local daemon that's down or wedged is a setup problem: fail fast
            # with the fix screen rather than retrying (ConnectTimeout is a
            # TimeoutException, so it must be named explicitly here).
            timed_out = isinstance(exc, httpx.ConnectTimeout)
            reason = "connection timed out" if timed_out else "connection refused"
            raise SetupFault(
                screens.ollama_unreachable(model.host, str(model.ref), reason)
            ) from exc
        if response.status_code == 404:
            detail = _error_detail(response)
            raise SetupFault(screens.ollama_model_missing(model.ref.name, model.host, detail))
        response.raise_for_status()
        return response.json()

    try:
        return await with_retries(
            model.retry, attempt, is_retryable=is_retryable_http, delay_hint=retry_after_seconds
        )
    except httpx.HTTPStatusError as exc:
        detail = _error_detail(exc.response)
        status = exc.response.status_code
        if status >= 500:  # the wire, not the content — the breaker counts these
            raise TransportError(f"ollama error {status}: {detail}") from exc
        raise ItemError(f"ollama error {status}: {detail}") from exc
    except httpx.HTTPError as exc:  # read/write timeouts, protocol errors — transport
        raise TransportError(f"ollama request failed: {exc}") from exc


def _error_detail(response: httpx.Response) -> str:
    try:
        record = as_record(response.json())
    except ValueError:
        record = None
    detail = as_str(record.get("error")) if record is not None else None
    return detail if detail is not None else response.text[:200].strip() or "no detail"


def _meter_usage(data: object) -> None:
    from smartpipe.core.jsontools import as_record

    record = as_record(data)
    if record is None:
        return
    tokens_in = record.get("prompt_eval_count")
    tokens_out = record.get("eval_count")
    metering.add_tokens(
        tokens_in=tokens_in if isinstance(tokens_in, int) else 0,
        tokens_out=tokens_out if isinstance(tokens_out, int) else 0,
    )
