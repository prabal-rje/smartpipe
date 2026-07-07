"""The Jina embeddings wire (D39/04): text AND images in one space.

jina-clip-v2 is the first media-native embedder — mentioning it
(``--embed-model jina/jina-clip-v2``) switches image items away from the
caption pivot. Same endpoint shape as the compat wire, plus image entries.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from smartpipe.core.errors import ItemError, SetupFault
from smartpipe.core.jsontools import as_float_vector, as_items, as_record
from smartpipe.io import metering
from smartpipe.models.base import ImageData
from smartpipe.models.http_support import is_retryable_http, retry_after_seconds
from smartpipe.models.retry import RetryPolicy, with_retries

if TYPE_CHECKING:
    from collections.abc import Sequence

    from smartpipe.models.base import ModelRef

__all__ = ["JINA_BASE_URL", "JinaClipEmbeddingModel"]

JINA_BASE_URL = "https://api.jina.ai"


@dataclass(frozen=True, slots=True)
class JinaClipEmbeddingModel:
    ref: ModelRef
    client: httpx.AsyncClient
    api_key: str
    base_url: str = JINA_BASE_URL
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return await self.embed_parts(list(texts))

    async def embed_parts(self, parts: Sequence[str | ImageData]) -> tuple[tuple[float, ...], ...]:
        entries: list[dict[str, str]] = []
        for part in parts:
            if isinstance(part, ImageData):
                entries.append({"image": base64.b64encode(part.data).decode("ascii")})
            else:
                entries.append({"text": part})
        payload: dict[str, object] = {"model": self.ref.name, "input": entries}
        metering.add_request_media(tuple(part for part in parts if not isinstance(part, str)))
        data = await self._post("/v1/embeddings", payload)
        record = as_record(data)
        rows = as_items(record.get("data")) if record is not None else None
        if rows is None:
            raise ItemError("jina embedding endpoint returned an unexpected shape")
        usage = as_record(record.get("usage")) if record is not None else None
        if usage is not None:
            total = usage.get("total_tokens")
            metering.add_tokens(tokens_in=total if isinstance(total, int) else 0)
        indexed: list[tuple[int, tuple[float, ...]]] = []
        for position, row in enumerate(rows):
            entry = as_record(row)
            vector = as_float_vector(entry.get("embedding")) if entry is not None else None
            if entry is None or vector is None:
                raise ItemError("jina embedding endpoint returned an unexpected shape")
            index = entry.get("index")
            indexed.append((index if isinstance(index, int) else position, vector))
        return tuple(vector for _index, vector in sorted(indexed))

    async def _post(self, path: str, payload: dict[str, object]) -> object:
        headers = {"Authorization": f"Bearer {self.api_key}"}

        async def attempt() -> object:
            response = await self.client.post(
                f"{self.base_url}{path}", json=payload, headers=headers
            )
            response.raise_for_status()
            return response.json()

        try:
            return await with_retries(
                self.retry,
                attempt,
                is_retryable=is_retryable_http,
                delay_hint=retry_after_seconds,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise SetupFault(
                    "error: Jina rejected the API key\n"
                    "  Set JINA_API_KEY (https://jina.ai) and retry."
                ) from exc
            raise ItemError(f"jina error {status}: {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise ItemError(f"jina request failed ({exc})") from exc
