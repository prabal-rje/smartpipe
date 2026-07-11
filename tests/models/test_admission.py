"""One run-scoped actual-call boundary shared by every remote model role."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import CircuitOpenTransport, RetryableError, UnsentError
from smartpipe.models.admission import (
    OutboundCallPolicy,
    admitted_chat,
    admitted_embed,
    admitted_parser,
    admitted_transcriber,
)
from smartpipe.models.base import (
    AudioData,
    CompletionRequest,
    ImageData,
    ModelRef,
    supports_media_embedding,
)
from smartpipe.models.ocr import OcrPage

if TYPE_CHECKING:
    from collections.abc import Sequence


class _Tracker:
    def __init__(self) -> None:
        self.active = 0
        self.maximum = 0
        self.started = 0
        self.first = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self) -> None:
        self.active += 1
        self.started += 1
        self.maximum = max(self.maximum, self.active)
        self.first.set()
        try:
            await self.release.wait()
        finally:
            self.active -= 1


class _Chat:
    ref = ModelRef("openai", "chat")

    def __init__(self, tracker: _Tracker) -> None:
        self.tracker = tracker

    async def complete(self, request: CompletionRequest) -> str:
        await self.tracker.run()
        return request.user


class _Embed:
    ref = ModelRef("openai", "embed")

    def __init__(self, tracker: _Tracker) -> None:
        self.tracker = tracker

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        await self.tracker.run()
        return tuple((1.0,) for _ in texts)


class _Parser:
    ref = ModelRef("mistral", "ocr")

    def __init__(self, tracker: _Tracker) -> None:
        self.tracker = tracker

    async def parse_image(self, image: ImageData) -> str:
        await self.tracker.run()
        return image.mime

    async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
        await self.tracker.run()
        return (OcrPage(0, path.name),)


class _Transcriber:
    ref = ModelRef("openai", "whisper-1")

    def __init__(self, tracker: _Tracker) -> None:
        self.tracker = tracker

    async def transcribe(self, audio: AudioData) -> str:
        await self.tracker.run()
        return audio.mime


async def test_one_policy_limits_actual_calls_across_remote_role_types() -> None:
    tracker = _Tracker()
    policy = OutboundCallPolicy(concurrency=1)
    chat = admitted_chat(_Chat(tracker), policy)
    embed = admitted_embed(_Embed(tracker), policy)
    parser = admitted_parser(_Parser(tracker), policy)
    transcriber = admitted_transcriber(_Transcriber(tracker), policy)

    tasks = (
        asyncio.create_task(chat.complete(CompletionRequest(system=None, user="hello"))),
        asyncio.create_task(embed.embed(("hello",))),
        asyncio.create_task(parser.parse_image(ImageData(b"png", "image/png"))),
        asyncio.create_task(transcriber.transcribe(AudioData(b"wav", "audio/wav"))),
    )
    await tracker.first.wait()
    await asyncio.sleep(0)
    assert tracker.started == 1
    tracker.release.set()
    await asyncio.gather(*tasks)
    assert tracker.started == 4
    assert tracker.maximum == 1


async def test_exhausted_429_counts_at_the_actual_call_breaker() -> None:
    policy = OutboundCallPolicy(concurrency=1, breaker_limit=2)
    calls = 0

    async def limited() -> str:
        nonlocal calls
        calls += 1
        raise RetryableError("openai error 429: slow down")

    with pytest.raises(RetryableError):
        await policy.execute(ModelRef("openai", "chat"), limited)
    with pytest.raises(CircuitOpenTransport) as tripped:
        await policy.execute(ModelRef("openai", "chat"), limited)
    with pytest.raises(CircuitOpenTransport) as opened:
        await policy.execute(ModelRef("openai", "chat"), limited)

    assert opened.value.trip_id == tripped.value.trip_id
    assert calls == 2


async def test_an_unsent_budget_refusal_does_not_reset_the_actual_call_streak() -> None:
    policy = OutboundCallPolicy(concurrency=1, breaker_limit=2)
    ref = ModelRef("openai", "chat")

    async def retryable() -> str:
        raise RetryableError("openai error 429")

    async def unsent() -> str:
        raise UnsentError("call budget reached")

    with pytest.raises(RetryableError):
        await policy.execute(ref, retryable)
    with pytest.raises(UnsentError):
        await policy.execute(ref, unsent)
    with pytest.raises(CircuitOpenTransport):
        await policy.execute(ref, retryable)


async def test_media_embedding_capability_survives_admission() -> None:
    class _MediaEmbed:
        ref = ModelRef("jina", "clip")

        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            return tuple((1.0,) for _ in texts)

        async def embed_parts(
            self, parts: Sequence[str | ImageData]
        ) -> tuple[tuple[float, ...], ...]:
            return tuple((1.0,) for _ in parts)

    model = admitted_embed(_MediaEmbed(), OutboundCallPolicy())
    probe: object = model
    assert supports_media_embedding(probe)
    assert await probe.embed_parts((ImageData(b"png", "image/png"),)) == ((1.0,),)
