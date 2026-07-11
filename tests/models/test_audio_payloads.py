"""Audio wire shapes (D20, post-1.1/02): byte-verified where a wire exists,
zero-cost refusal where none does — capability by attempt, never a registry."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.core.errors import ItemError
from smartpipe.core.jsontools import as_items, as_record, as_str
from smartpipe.models.base import AudioData, CompletionRequest, ModelRef
from smartpipe.models.http_support import make_client
from smartpipe.models.ollama import OllamaChatModel
from smartpipe.models.openai_compat import MISTRAL_WIRE, OpenAIChatModel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import respx

WAV_BYTES = b"RIFF----WAVEfakeaudio"
AUDIO = AudioData(data=WAV_BYTES, mime="audio/wav")


def _hear_request() -> CompletionRequest:
    return CompletionRequest(system=None, user="What is said?", media=(AUDIO,))


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as c:
        yield c


@pytest.mark.parametrize(
    ("base", "ref", "wire"),
    [
        ("https://api.openai.com", ModelRef("openai", "gpt-4o-audio-preview"), None),
        ("https://api.mistral.ai", ModelRef("mistral", "voxtral-small-latest"), MISTRAL_WIRE),
    ],
    ids=["openai", "mistral"],
)
async def test_openai_wire_sends_input_audio_parts(
    base: str,
    ref: ModelRef,
    wire: object,
    client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(f"{base}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})
    )
    kwargs: dict[str, object] = {}
    if wire is not None:
        kwargs["wire"] = wire
    model = OpenAIChatModel(
        ref=ref,
        client=client,
        base_url=base,
        api_key="k",
        **kwargs,  # type: ignore[arg-type]
    )
    assert await model.complete(_hear_request()) == "hello"
    body = as_record(__import__("json").loads(route.calls.last.request.content))
    assert body is not None
    messages = as_items(body.get("messages"))
    assert messages
    user = as_record(messages[-1])
    assert user is not None
    parts = as_items(user.get("content"))
    assert parts is not None
    audio_part = as_record(parts[1])
    payload = as_record(audio_part.get("input_audio")) if audio_part is not None else None
    assert payload is not None and payload.get("format") == "wav"
    data = as_str(payload.get("data"))
    assert data is not None
    assert base64.b64decode(data) == WAV_BYTES  # byte-roundtrip, the house standard


async def test_unknown_audio_mime_fails_before_the_wire(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("https://api.openai.com/v1/chat/completions")
    exotic = CompletionRequest(
        system=None, user="x", media=(AudioData(data=b"x", mime="audio/flac"),)
    )
    model = OpenAIChatModel(
        ref=ModelRef("openai", "gpt-4o-audio-preview"),
        client=client,
        base_url="https://api.openai.com",
        api_key="k",
    )
    with pytest.raises(ItemError, match="audio format audio/flac isn't sendable"):
        model.preflight(exotic)
    with pytest.raises(ItemError, match="audio format audio/flac isn't sendable"):
        await model.complete(exotic)
    assert route.call_count == 0  # never guess a format at a paid endpoint


async def test_ollama_refuses_audio_before_any_bytes_leave(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("http://localhost:11434/api/chat")
    model = OllamaChatModel(
        ref=ModelRef("ollama", "qwen3:8b"), client=client, host="http://localhost:11434"
    )
    with pytest.raises(ItemError, match="can't hear audio"):
        await model.complete(_hear_request())
    assert route.call_count == 0


def test_anthropic_refuses_audio_in_the_builder() -> None:
    from smartpipe.models.anthropic_adapter import build_kwargs

    with pytest.raises(ItemError, match="can't hear audio"):
        build_kwargs("claude-haiku-4-5", _hear_request())


def test_codex_wire_refuses_audio_in_the_builder() -> None:
    from smartpipe.models.openai_codex import build_payload

    with pytest.raises(ItemError, match="can't hear audio"):
        build_payload("gpt-5.4", _hear_request())


def _watch_request() -> CompletionRequest:
    from smartpipe.models.base import VideoData

    video = VideoData(data=b"\x00\x00\x00\x18ftypmp42fakevideo", mime="video/mp4")
    return CompletionRequest(system=None, user="What happens?", media=(video,))


def test_codex_wire_refuses_video_in_the_builder() -> None:
    """Silently dropping the video sends a prompt-only request and gets a
    confident wrong answer — the ladder (map converts to frames+audio) only
    fires on a pre-send refusal, exactly like openai_compat's."""
    from smartpipe.models.openai_codex import build_payload

    with pytest.raises(ItemError, match="can't watch video"):
        build_payload("gpt-5.4", _watch_request())


def test_anthropic_refuses_video_in_the_builder() -> None:
    from smartpipe.models.anthropic_adapter import build_kwargs

    with pytest.raises(ItemError, match="can't watch video"):
        build_kwargs("claude-haiku-4-5", _watch_request())
