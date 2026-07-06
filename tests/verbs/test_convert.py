"""The D33 converter: LLM rungs, cost fence, whisper fallback."""

from __future__ import annotations

import pytest

from sempipe.core.errors import ItemError
from sempipe.io.diagnostics import DegradationLog
from sempipe.models.base import AudioData, CompletionRequest, ImageData, ModelRef
from sempipe.verbs.convert import IMAGE_NEEDS_CAPTION, make_converter

AUDIO = AudioData(b"RIFFfake", "audio/wav")
IMAGE = ImageData(b"\x89PNGfake", "image/png")


class Hears:
    def __init__(self, provider: str = "ollama") -> None:
        self.ref = ModelRef(provider, "omni")  # type: ignore[arg-type]
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        return "a steady 440 Hz tone"


class Deaf:
    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "text-only")
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        raise ItemError("this model can't hear audio — …")


async def test_local_model_converts_audio_automatically(
    capsys: pytest.CaptureFixture[str],
) -> None:
    log = DegradationLog()
    converter = make_converter(Hears("ollama"), allow_paid=False, log=log)
    text = await converter.audio_to_text(AUDIO, "call.wav")
    assert text == "a steady 440 Hz tone"
    assert "audio → text (heard by ollama/omni)" in capsys.readouterr().err


async def test_cloud_model_needs_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from sempipe.parsing import extract

    def fake_whisper(audio: AudioData) -> str:
        return "whispered instead"

    monkeypatch.setattr(extract, "transcribe_audio", fake_whisper)
    cloud = Hears("openai")
    log = DegradationLog()
    converter = make_converter(cloud, allow_paid=False, log=log)
    text = await converter.audio_to_text(AUDIO, "call.wav")
    assert text == "whispered instead"  # fell to whisper — no paid call
    assert cloud.calls == []  # the fence held
    with_flag = make_converter(cloud, allow_paid=True, log=log)
    assert await with_flag.audio_to_text(AUDIO, "call.wav") == "a steady 440 Hz tone"


async def test_deaf_model_falls_to_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    from sempipe.parsing import extract

    def fake_whisper(audio: AudioData) -> str:
        return "the words"

    monkeypatch.setattr(extract, "transcribe_audio", fake_whisper)
    log = DegradationLog()
    converter = make_converter(Deaf(), allow_paid=False, log=log)
    assert await converter.audio_to_text(AUDIO, "x.wav") == "the words"


async def test_images_have_no_free_non_llm_rung() -> None:
    log = DegradationLog()
    converter = make_converter(Hears("openai"), allow_paid=False, log=log)
    with pytest.raises(ItemError) as excinfo:
        await converter.image_to_text(IMAGE, "photo.png")
    assert str(excinfo.value) == IMAGE_NEEDS_CAPTION
    none_at_all = make_converter(None, allow_paid=True, log=log)
    with pytest.raises(ItemError):
        await none_at_all.image_to_text(IMAGE, "photo.png")


async def test_local_model_captions_images(capsys: pytest.CaptureFixture[str]) -> None:
    log = DegradationLog()
    converter = make_converter(Hears("ollama"), allow_paid=False, log=log)
    caption = await converter.image_to_text(IMAGE, "photo.png")
    assert caption == "a steady 440 Hz tone"  # the fake's reply — it was called
    assert "image → text (described by ollama/omni)" in capsys.readouterr().err
