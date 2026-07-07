"""The local embedding wire (D44): pure Python, no server, no key.

fastembed runs nomic-embed-text v1.5 as ONNX on CPU. This is the DEFAULT
embedder: `smartpipe embed` works on a fresh install with nothing running.
The model (~130 MB) downloads once on first use, disclosed. Imports stay
inside methods — the startup budget never pays for onnxruntime. ONE engine
per model instance, loaded lazily and reused for every batch; the container
hands verbs a single instance per run, and weights cache on disk across runs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smartpipe.core.errors import ItemError
from smartpipe.io import diagnostics

if TYPE_CHECKING:
    from collections.abc import Sequence

    from smartpipe.models.base import ModelRef

__all__ = ["LOCAL_EMBED_MODEL", "LocalEmbeddingModel"]

LOCAL_EMBED_MODEL = "nomic-ai/nomic-embed-text-v1.5"

_announced = False  # the one-time download note, once per process


def _plain(vector: object) -> object:
    """numpy arrays expose tolist(); plain sequences pass through untouched."""
    lister = getattr(vector, "tolist", None)
    return lister() if callable(lister) else vector


@dataclass(slots=True)
class LocalEmbeddingModel:
    ref: ModelRef
    engine: object | None = field(default=None, repr=False)  # injected in tests; lazy-loaded live

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return await asyncio.to_thread(self._embed_blocking, list(texts))

    def _embed_blocking(self, texts: list[str]) -> tuple[tuple[float, ...], ...]:
        from smartpipe.core.jsontools import as_float_vector

        engine = self._load()
        try:
            produced: list[object] = list(engine.embed(texts))  # type: ignore[attr-defined]
        except Exception as exc:  # onnx failures become per-item errors
            raise ItemError(f"local embedding failed ({exc})") from exc
        vectors: list[tuple[float, ...]] = []
        for entry in produced:
            vector = as_float_vector(_plain(entry))
            if vector is None:
                raise ItemError("the local embedder returned an unexpected shape")
            vectors.append(vector)
        return tuple(vectors)

    def _load(self) -> object:
        if self.engine is not None:
            return self.engine  # loaded once, reused for every batch
        global _announced
        if not _announced:
            _announced = True
            diagnostics.note(
                "local embeddings: nomic-embed-text v1.5 on CPU (first use downloads ~130 MB, once)"
            )
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover — fastembed is a core dep
            raise ItemError("the local embedder is unavailable — reinstall smartpipe") from exc
        self.engine = TextEmbedding(model_name=LOCAL_EMBED_MODEL)
        return self.engine
