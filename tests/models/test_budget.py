"""``--max-calls`` (D18): a hard ceiling on model calls, drained gracefully.

Per-item verbs get a tripped ``stop`` event (the Ctrl-C drain machinery); whole-set
verbs (no stop) get the fatal screen — a partial collection is nothing usable.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ItemError, SetupFault
from smartpipe.models.base import CompletionRequest, ModelRef
from smartpipe.models.budget import CallBudget, budgeted_chat, budgeted_embed

if TYPE_CHECKING:
    from collections.abc import Sequence


class FakeChat:
    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake")
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> str:
        self.calls += 1
        return "ok"


class FakeEmbed:
    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake-embed")
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        return tuple((1.0,) for _ in texts)


REQUEST = CompletionRequest(system=None, user="x")


async def test_budget_trips_stop_at_the_limit_and_skips_after() -> None:
    stop = asyncio.Event()
    budget = CallBudget(limit=2, stop=stop)
    inner = FakeChat()
    model = budgeted_chat(inner, budget)
    assert model.ref == inner.ref  # the wrapper is invisible to callers
    await model.complete(REQUEST)
    assert not stop.is_set()
    await model.complete(REQUEST)
    assert stop.is_set()  # the limit call still ran, intake stops now
    assert budget.exhausted
    with pytest.raises(ItemError, match="call budget"):
        await model.complete(REQUEST)  # a racing in-flight worker: skip, not crash
    assert inner.calls == 2  # the raced call never reached the wire


async def test_whole_set_budget_exhaustion_is_fatal() -> None:
    budget = CallBudget(limit=1, stop=None)  # whole-set verbs run without a stop event
    model = budgeted_embed(FakeEmbed(), budget)
    await model.embed(["a"])
    with pytest.raises(SetupFault, match="call budget reached mid-collection"):
        await model.embed(["b"])


async def test_charges_count_calls_not_texts() -> None:
    budget = CallBudget(limit=2, stop=asyncio.Event())
    model = budgeted_embed(FakeEmbed(), budget)
    await model.embed(["a", "b", "c"])  # one batched call = one charge
    assert budget.calls == 1


def test_limit_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be >= 1"):
        CallBudget(limit=0, stop=None)
