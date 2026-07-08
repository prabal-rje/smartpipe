"""``--max-calls`` (D18): a hard ceiling on model calls.

The budget wraps models at the composition root, so verbs stay ignorant. One
charge = one model call (a repair re-ask charges again; wire retries of the same
call, inside ``with_retries``, do not — the wrapper sits outside them).

Two exhaustion behaviors, pinned in ux.md: with a ``stop`` event (the per-item
verbs' drain machinery) the limit call still runs, intake stops, and any racing
in-flight worker skips its item; without one (whole-set verbs — a partial
collection is nothing usable) exhaustion is fatal with the fix screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import ItemError, SetupFault

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Sequence

    from smartpipe.models.base import (
        ChatModel,
        CompletionRequest,
        EmbeddingModel,
        ImageData,
        MediaEmbeddingModel,
        ModelRef,
    )

__all__ = ["CallBudget", "budgeted_chat", "budgeted_embed"]


@dataclass(slots=True)
class CallBudget:
    """Mutable by design (documented Spinner-style exception): the counter is
    per-run state shared by every model the container hands out."""

    limit: int
    stop: asyncio.Event | None  # per-item verbs trip intake; None = whole-set (fatal)
    calls: int = 0
    exhausted: bool = False

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError(f"--max-calls must be >= 1, got {self.limit}")

    def charge(self) -> None:
        if self.calls >= self.limit:
            self.exhausted = True
            if self.stop is None:
                raise SetupFault(
                    f"error: call budget reached mid-collection (--max-calls {self.limit})\n"
                    "  This verb needs every item before it can produce a result — a cap\n"
                    "  that stops early leaves nothing usable.\n"
                    "  Raise --max-calls, shrink the input, or drop the cap."
                )
            raise ItemError(f"call budget reached (--max-calls {self.limit})")
        self.calls += 1
        if self.calls >= self.limit:
            self.exhausted = True
            if self.stop is not None:
                self.stop.set()  # the limit call runs; nothing new starts


@dataclass(frozen=True, slots=True)
class _BudgetedChat:
    inner: ChatModel
    budget: CallBudget

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def complete(self, request: CompletionRequest) -> str:
        self.budget.charge()
        return await self.inner.complete(request)


@dataclass(frozen=True, slots=True)
class _BudgetedEmbed:
    inner: EmbeddingModel
    budget: CallBudget

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.budget.charge()
        return await self.inner.embed(texts)


@dataclass(frozen=True, slots=True)
class _BudgetedMediaEmbed:
    """The budget belt for a JOINT text+image embedder: the wrapper must keep
    ``embed_parts`` visible, or the belt would silently demote pixels to the
    caption pivot (capability follows the wrapper, item 40)."""

    inner: MediaEmbeddingModel
    budget: CallBudget

    @property
    def ref(self) -> ModelRef:
        return self.inner.ref

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.budget.charge()
        return await self.inner.embed_parts(list(texts))

    async def embed_parts(self, parts: Sequence[str | ImageData]) -> tuple[tuple[float, ...], ...]:
        self.budget.charge()
        return await self.inner.embed_parts(parts)


def budgeted_chat(inner: ChatModel, budget: CallBudget) -> ChatModel:
    return _BudgetedChat(inner, budget)


def budgeted_embed(inner: EmbeddingModel, budget: CallBudget) -> EmbeddingModel:
    from smartpipe.models.base import supports_media_embedding

    if supports_media_embedding(inner):
        return _BudgetedMediaEmbed(inner, budget)
    return _BudgetedEmbed(inner, budget)
