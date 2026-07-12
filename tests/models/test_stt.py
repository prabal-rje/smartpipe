"""The remote STT wire (D39/05)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.core.errors import RetryableError, SetupFault, TransportError
from smartpipe.models.base import AudioData, ModelRef
from smartpipe.models.retry import RetryPolicy
from smartpipe.models.stt import RemoteTranscriber

if TYPE_CHECKING:
    import respx

FAST = RetryPolicy(attempts=1, base_delay=0.0)
URL = "https://api.openai.com/v1/audio/transcriptions"


def _transcriber(client: httpx.AsyncClient) -> RemoteTranscriber:
    return RemoteTranscriber(
        ref=ModelRef("openai", "whisper-1"), client=client, api_key="sk-x", retry=FAST
    )


async def test_multipart_fields_and_verbatim_text(respx_mock: respx.MockRouter) -> None:
    from smartpipe.io import metering

    metering.reset()
    route = respx_mock.post(URL).mock(return_value=httpx.Response(200, text="hello world\n"))
    async with httpx.AsyncClient() as client:
        text = await _transcriber(client).transcribe(AudioData(b"RIFFdata", "audio/wav"))
    assert text == "hello world"
    body = route.calls.last.request.content
    assert b'name="model"' in body and b"whisper-1" in body
    assert b'filename="audio.wav"' in body and b"RIFFdata" in body
    assert b'name="response_format"' in body and b"text" in body
    assert metering.snapshot().conversions == 1


async def test_401_names_the_key(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(401, text="no"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(SetupFault, match="OPENAI_API_KEY"):
            await _transcriber(client).transcribe(AudioData(b"x", "audio/mpeg"))


@pytest.mark.parametrize(
    ("status", "fault"),
    ((429, RetryableError), (503, TransportError)),
)
async def test_exhausted_transient_faults_reach_shared_admission(
    respx_mock: respx.MockRouter,
    status: int,
    fault: type[RetryableError],
) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(status, text="try later"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(fault):
            await _transcriber(client).transcribe(AudioData(b"x", "audio/mpeg"))


async def test_converter_does_not_double_meter_remote_stt(
    respx_mock: respx.MockRouter,
) -> None:
    from smartpipe.io import metering
    from smartpipe.io.diagnostics import DegradationLog
    from smartpipe.models.base import CompletionRequest
    from smartpipe.verbs.convert import make_converter

    class UnusedChat:
        ref = ModelRef("openai", "gpt-5-mini")

        async def complete(self, request: CompletionRequest) -> str:
            raise AssertionError(f"STT should outrank chat: {request.user}")

    metering.reset()
    respx_mock.post(URL).mock(return_value=httpx.Response(200, text="verbatim"))
    async with httpx.AsyncClient() as client:
        converter = make_converter(
            UnusedChat(),
            allow_paid=True,
            log=DegradationLog(),
            stt=_transcriber(client),
        )
        assert await converter.audio_to_text(AudioData(b"x", "audio/mpeg"), "clip.mp3")

    assert metering.snapshot().conversions == 1


# --- the local wire (stt-model = "local") -------------------------------------------


async def test_local_transcriber_converts_missing_extra_to_a_recoverable_item_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smartpipe.core.errors import ItemError, is_recoverable_item_error
    from smartpipe.models.stt import LocalTranscriber
    from smartpipe.parsing import extract

    def unavailable(audio: AudioData, *, model_size: str | None = None) -> str:
        raise extract.MissingExtra("audio", "local transcription is unavailable — reinstall")

    monkeypatch.setattr(extract, "transcribe_audio", unavailable)
    transcriber = LocalTranscriber(ref=ModelRef("local", "whisper-tiny"))
    with pytest.raises(ItemError, match="reinstall") as caught:
        await transcriber.transcribe(AudioData(b"x", "audio/wav"))
    # PLAIN ItemError — recoverable, so the audio ladder falls through instead of dying
    assert type(caught.value) is ItemError
    assert is_recoverable_item_error(caught.value)


async def test_local_transcriber_returns_the_on_device_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smartpipe.models.stt import LocalTranscriber
    from smartpipe.parsing import extract

    def canned(audio: AudioData, *, model_size: str | None = None) -> str:
        return "the local words"

    monkeypatch.setattr(extract, "transcribe_audio", canned)
    transcriber = LocalTranscriber(ref=ModelRef("local", "whisper-tiny"))
    assert await transcriber.transcribe(AudioData(b"x", "audio/wav")) == "the local words"
