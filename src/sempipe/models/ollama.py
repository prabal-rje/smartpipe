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

from sempipe.cli import screens
from sempipe.core.errors import ItemError, SetupFault
from sempipe.core.jsontools import as_float_vector, as_items, as_record, as_str, record_at
from sempipe.models.http_support import is_retryable_http
from sempipe.models.retry import RetryPolicy, with_retries

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sempipe.models.base import CompletionRequest, ModelRef

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
        if request.images:
            user["images"] = [base64.b64encode(image.data).decode() for image in request.images]
        messages.append(user)
        payload: dict[str, object] = {
            "model": self.ref.name,
            "stream": False,
            "messages": messages,
        }
        if request.json_schema is not None:
            payload["format"] = dict(request.json_schema)
        try:
            data = await _post(self, "/api/chat", payload)
        except ItemError as exc:
            # _post maps a 400 to ItemError; with images in flight that almost
            # always means "this model has no vision" — say so, name a fix.
            if request.images and "error 400" in str(exc):
                raise ItemError(_NO_VISION) from exc
            raise
        message = record_at(data, "message")
        content = as_str(message.get("content")) if message is not None else None
        if content is None:
            raise ItemError("ollama returned an unexpected reply shape")
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
        return await with_retries(model.retry, attempt, is_retryable=is_retryable_http)
    except httpx.HTTPStatusError as exc:
        detail = _error_detail(exc.response)
        raise ItemError(f"ollama error {exc.response.status_code}: {detail}") from exc
    except httpx.HTTPError as exc:
        raise ItemError(f"ollama request failed: {exc}") from exc


def _error_detail(response: httpx.Response) -> str:
    try:
        record = as_record(response.json())
    except ValueError:
        record = None
    detail = as_str(record.get("error")) if record is not None else None
    return detail if detail is not None else response.text[:200].strip() or "no detail"
