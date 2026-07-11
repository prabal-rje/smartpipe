"""Result caching for chat completions (D38/15, KQL ``materialize``).

Iteration stops re-paying unchanged work. Sound because of D36: temperature
is 0.0 everywhere, so identical request → identical reply is the contract.
The cache wraps OUTSIDE the call budget — a hit costs nothing and must not
count against ``--max-calls`` (the belt caps spend, not answers).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from smartpipe.models.base import ChatModel, CompletionRequest, ModelRef

__all__ = ["CachingChatModel", "cache_key", "sweep"]


def cache_key(ref: ModelRef, request: CompletionRequest) -> str:
    """Anything that changes the reply changes the key."""
    payload: dict[str, object] = {
        "provider": ref.provider,
        "model": ref.name,
        "system": request.system,
        "user": request.user,
        "schema": request.json_schema,
        "temperature": request.temperature,
        "presence": request.presence_penalty,
        "frequency": request.frequency_penalty,
        "max_tokens": request.max_tokens,
        "media": [(part.mime, hashlib.sha256(part.data).hexdigest()) for part in request.media],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CachingChatModel:
    """ChatModel-shaped wrapper: hit → stored reply, miss → inner + store."""

    def __init__(self, inner: ChatModel, directory: Path) -> None:
        self.inner = inner
        self.directory = directory
        self.hits = 0
        self.misses = 0
        self._inflight: dict[str, asyncio.Task[str]] = {}

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def complete(self, request: CompletionRequest) -> str:
        key = cache_key(self.inner.ref, request)
        path = self.directory / key[:2] / f"{key}.json"
        stored = _read(path)
        if stored is not None:
            self.hits += 1
            return stored
        shared = self._inflight.get(key)
        if shared is not None:
            reply = await asyncio.shield(shared)
            self.hits += 1
            return reply
        task = asyncio.create_task(self._complete_miss(request, path))
        self._inflight[key] = task
        task.add_done_callback(lambda done: self._finish_inflight(key, done))
        return await asyncio.shield(task)

    async def _complete_miss(self, request: CompletionRequest, path: Path) -> str:
        reply = await self.inner.complete(request)
        self.misses += 1
        _write(path, reply)
        return reply

    def _finish_inflight(self, key: str, task: asyncio.Task[str]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled():
            _ = task.exception()  # retrieve failures even if every waiter was cancelled


def _read(path: Path) -> str | None:
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
        os.utime(path)  # a hit refreshes recency — the LRU truth (D39/02)
    except (OSError, json.JSONDecodeError):
        return None  # missing or corrupt — a miss, never a crash
    from smartpipe.core.jsontools import as_record

    record = as_record(parsed)
    if record is not None:
        reply = record.get("reply")
        if isinstance(reply, str):
            return reply
    return None


def _write(path: Path, reply: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(".tmp")
    scratch.write_text(json.dumps({"reply": reply}, ensure_ascii=False), encoding="utf-8")
    os.replace(scratch, path)  # atomic on POSIX — never a half-written entry


_DAY_SECONDS = 86_400


def sweep(directory: Path, *, ttl_days: int, max_mb: int, now: float) -> tuple[int, int]:
    """Expire entries past the TTL, then LRU-evict (oldest mtime first) until
    under the size cap. Returns (entries removed, bytes removed). Pure walk —
    the caller owns the once-a-day gating and error tolerance."""
    entries: list[tuple[float, int, Path]] = []
    for path in directory.rglob("*.json"):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((stat.st_mtime, stat.st_size, path))
    removed = 0
    freed = 0
    survivors: list[tuple[float, int, Path]] = []
    horizon = now - ttl_days * _DAY_SECONDS
    for mtime, size, path in entries:
        if mtime < horizon:
            try:
                path.unlink()
                removed += 1
                freed += size
            except OSError:
                survivors.append((mtime, size, path))
        else:
            survivors.append((mtime, size, path))
    survivors.sort()  # oldest first
    total = sum(size for _mtime, size, _path in survivors)
    cap = max_mb * 1_048_576
    for _mtime, size, path in survivors:
        if total <= cap:
            break
        try:
            path.unlink()
            removed += 1
            freed += size
            total -= size
        except OSError:
            continue
    return removed, freed
