"""The D20 three-rung ladder for audio: native → transcribe → skip-with-both-fixes."""

from __future__ import annotations

import io
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, ItemError
from smartpipe.io.items import item_from_file
from smartpipe.io.writers import OutputFormat, RenderMode, ResultWriter, WriterConfig, make_writer
from smartpipe.models.base import AudioData, CompletionRequest, ImageData, ModelRef
from smartpipe.verbs.common import AUDIO_NEEDS_TEXT, ensure_text
from smartpipe.verbs.map import MapRequest, run_map

if TYPE_CHECKING:
    from pathlib import Path

    from smartpipe.engine.coalesce import BatchSettings
    from smartpipe.io.writers import TextSink
    from smartpipe.models.base import ChatModel

AUDIO = AudioData(data=b"RIFFfake", mime="audio/wav")


def _audio_item(name: str = "call.wav"):
    return replace(item_from_file("", name, 0), media=(AUDIO,))


# --- ensure_text (the non-map rung) ------------------------------------------------


async def test_image_message_is_byte_identical_to_stage_7() -> None:
    item = replace(item_from_file("", "x.png", 0), media=(ImageData(b"png", "image/png"),))
    with pytest.raises(ItemError, match="image items need map — this verb reads text"):
        await ensure_text(item)


async def test_audio_transcribes_via_the_injected_transcriber() -> None:
    def fake_transcriber(audio: AudioData) -> str:
        assert audio.mime == "audio/wav"
        return "the caller wants a refund"

    spoken = await ensure_text(_audio_item(), transcriber=fake_transcriber)
    assert spoken.text == "the caller wants a refund"
    assert spoken.media == ()


async def test_missing_extra_maps_to_the_two_fix_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smartpipe.parsing import extract
    from smartpipe.verbs.common import transcribe

    def no_extra(audio: AudioData) -> str:
        raise extract.MissingExtra("audio", "install it")

    monkeypatch.setattr(extract, "transcribe_audio", no_extra)
    with pytest.raises(ItemError) as excinfo:
        transcribe(AUDIO)
    assert str(excinfo.value) == AUDIO_NEEDS_TEXT


# --- the map ladder ------------------------------------------------------------------


class DeafChat:
    """Raises the capability error on audio requests; answers text ones."""

    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "deaf-model")
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        if request.media:
            raise ItemError("this model can't hear audio — …")
        # the payload's last content line, inside the <input> fence (item 57)
        return f"summary of: {request.user.splitlines()[-2]}"


class HearingChat:
    def __init__(self) -> None:
        self.ref = ModelRef("openai", "gpt-4o-audio-preview")
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        return "heard it natively"


class FakeContext:
    def __init__(self, model: DeafChat | HearingChat) -> None:
        self.model = model

    async def chat_model(self, flag: str | None = None):
        return self.model

    async def context_window(self, ref: object) -> int | None:
        return None  # the static table stands in these tests

    def fallback_ref(self, flag: str | None = None) -> None:
        return None  # no failover configured in these tests

    async def fallback_chat_model(self, ref: object) -> ChatModel:
        raise AssertionError("fallback never resolved without a configured ref")

    def concurrency(self, flag: int | None = None) -> int:
        return 1

    def batching(self) -> BatchSettings | None:
        return None  # batching off: these tests pin the solo path byte-for-byte

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None

    def document_parser(self, flag: str | None = None) -> None:
        return None

    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextSink,
        fields: tuple[str, ...] | None = None,
        bare: bool = False,
        full: bool = False,
    ) -> ResultWriter:
        return make_writer(WriterConfig(mode=RenderMode.TEXT, color=False, width=80), stdout)


class _TtyStdin(io.StringIO):
    def isatty(self) -> bool:
        return True


def _request(tmp_path: Path) -> MapRequest:
    from smartpipe.io.inputs import InputSpec

    (tmp_path / "call.wav").write_bytes(b"RIFF----WAVEfakeaudio")
    return MapRequest(
        prompt="what does the caller want?",
        schema_path=None,
        model_flag=None,
        output=OutputFormat.TEXT,
        concurrency_flag=None,
        input=InputSpec(patterns=(str(tmp_path / "*.wav"),), from_files=False),
    )


async def test_hearing_model_gets_the_bytes_natively(tmp_path: Path) -> None:
    model = HearingChat()
    out = io.StringIO()
    code = await run_map(_request(tmp_path), FakeContext(model), stdin=_TtyStdin(), stdout=out)
    assert code is ExitCode.OK
    assert out.getvalue() == "heard it natively\n"
    assert isinstance(model.calls[0].media[0], AudioData)  # the bytes rode the request


async def test_deaf_model_falls_back_to_transcription(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from smartpipe.parsing import extract

    def fake_transcribe(audio: AudioData) -> str:
        return "please cancel my subscription"

    monkeypatch.setattr(extract, "transcribe_audio", fake_transcribe)
    model = DeafChat()
    out = io.StringIO()
    code = await run_map(_request(tmp_path), FakeContext(model), stdin=_TtyStdin(), stdout=out)
    assert code is ExitCode.OK
    assert "please cancel my subscription" in out.getvalue()
    assert len(model.calls) == 2  # native attempt, then the transcript retry
    assert model.calls[1].media == ()
    err = capsys.readouterr().err
    assert err.count("⚠ degraded:") == 1  # the per-row disclosure (D27)
    assert "audio → text (whisper tiny)" in err
    assert "note: degraded: audio → text \N{MULTIPLICATION SIGN}1" in err  # pinned rollup


async def test_deaf_model_without_extra_skips_with_both_fixes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from smartpipe.parsing import extract

    def no_extra(audio: AudioData) -> str:
        raise extract.MissingExtra("audio", "install it")

    monkeypatch.setattr(extract, "transcribe_audio", no_extra)
    model = DeafChat()
    out = io.StringIO()
    code = await run_map(_request(tmp_path), FakeContext(model), stdin=_TtyStdin(), stdout=out)
    assert code is ExitCode.ALL_FAILED  # the one item skipped
    assert out.getvalue() == ""
    assert "can't hear audio" in capsys.readouterr().err  # the adapter's two-fix line


async def test_default_transcriber_end_to_end_on_junk_audio() -> None:
    # no monkeypatch: real faster-whisper rejects junk bytes as a per-item
    # error (the two-fix skip), never a crash (the ensure_text rung, fully real)
    with pytest.raises(ItemError):
        await ensure_text(_audio_item())
