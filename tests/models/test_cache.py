"""Result caching (D38/15): key sensitivity, hit short-circuits, honest misses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sempipe.models.base import CompletionRequest, ImageData, ModelRef
from sempipe.models.cache import CachingChatModel, cache_key

if TYPE_CHECKING:
    from pathlib import Path

REF = ModelRef("openai", "gpt-5.4-mini")


def _request(**kwargs: object) -> CompletionRequest:
    return CompletionRequest(system="s", user="u", **kwargs)  # type: ignore[arg-type]


def test_key_is_stable_and_sensitive() -> None:
    base = cache_key(REF, _request())
    assert base == cache_key(REF, _request())  # stable
    assert base != cache_key(ModelRef("openai", "other"), _request())  # model flips it
    different = CompletionRequest(system="s", user="different")
    assert base != cache_key(REF, different)
    assert base != cache_key(REF, _request(max_tokens=64))
    assert base != cache_key(REF, _request(media=(ImageData(b"px", "image/png"),)))


class CountingModel:
    def __init__(self) -> None:
        self.ref = REF
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> str:
        self.calls += 1
        return f"reply-{self.calls}"


async def test_hit_short_circuits_the_inner_model(tmp_path: Path) -> None:
    inner = CountingModel()
    cached = CachingChatModel(inner, tmp_path)
    first = await cached.complete(_request())
    second = await cached.complete(_request())
    assert (first, second) == ("reply-1", "reply-1")  # the stored reply, verbatim
    assert inner.calls == 1  # the second call never reached the wire
    assert (cached.hits, cached.misses) == (1, 1)


async def test_hits_do_not_consume_the_call_budget(tmp_path: Path) -> None:
    from sempipe.models.budget import CallBudget, budgeted_chat

    inner = CountingModel()
    budget = CallBudget(limit=1, stop=None)
    cached = CachingChatModel(budgeted_chat(inner, budget), tmp_path)
    await cached.complete(_request())  # spends the single budgeted call
    reply = await cached.complete(_request())  # a hit — must NOT trip the budget
    assert reply == "reply-1"
    assert inner.calls == 1


async def test_corrupt_entry_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    inner = CountingModel()
    cached = CachingChatModel(inner, tmp_path)
    key = cache_key(REF, _request())
    target = tmp_path / key[:2] / f"{key}.json"
    target.parent.mkdir(parents=True)
    target.write_text("not json{", encoding="utf-8")
    reply = await cached.complete(_request())
    assert reply == "reply-1"  # re-fetched and re-stored
    assert await cached.complete(_request()) == "reply-1"  # now a clean hit
