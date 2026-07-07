"""The D33 converter: LLM rungs, cost fence, whisper fallback."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from smartpipe.core.errors import ItemError
from smartpipe.io.diagnostics import DegradationLog
from smartpipe.models.base import AudioData, CompletionRequest, ImageData, ModelRef
from smartpipe.verbs.convert import IMAGE_NEEDS_CAPTION, make_converter

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
    from smartpipe.parsing import extract

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
    from smartpipe.parsing import extract

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


class WatchesForHalves:
    """gemini-native shape: accepts video + a response schema, returns both halves."""

    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "watcher")  # local → free rung
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        return '{"visual": "colored bars on a screen", "transcript": "revenue doubled"}'


async def test_video_halves_from_one_watching_call() -> None:
    from smartpipe.models.base import VideoData

    log = DegradationLog()
    converter = make_converter(WatchesForHalves(), allow_paid=False, log=log)
    visual, speech = await converter.video_halves(
        VideoData(b"\x00\x00\x00 ftypfake", "video/mp4"), "demo.mp4"
    )
    assert visual == "colored bars on a screen"
    assert speech == "revenue doubled"


async def test_video_vector_is_the_fair_average_of_both_halves() -> None:
    from dataclasses import replace as dc_replace

    from smartpipe.io.items import item_from_file
    from smartpipe.models.base import VideoData
    from smartpipe.verbs.convert import embed_video_halves

    class HalfEmbed:
        def __init__(self) -> None:
            self.ref = ModelRef("openai", "text-embedding-3-small")
            self.seen: list[str] = []

        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            self.seen.extend(texts)
            return tuple((1.0, 0.0) if "bars" in t else (0.0, 1.0) for t in texts)

    log = DegradationLog()
    converter = make_converter(WatchesForHalves(), allow_paid=False, log=log)
    video = VideoData(b"\x00\x00\x00 ftypfake", "video/mp4")
    item = dc_replace(item_from_file("", "demo.mp4", 0), media=(video,))
    embedder = HalfEmbed()
    converted, vector = await embed_video_halves(embedder, item, video, converter)
    assert len(embedder.seen) == 2  # both halves embedded separately
    assert vector == (0.5, 0.5)  # the fair 50/50 mean — neither half drowns
    assert "colored bars" in converted.text and "revenue doubled" in converted.text


# --- the stt-model rung (D39/05) ----------------------------------------------------


class VerbatimStt:
    def __init__(self, reply: str = "the verbatim words") -> None:
        self.ref = ModelRef("openai", "whisper-1")
        self.reply = reply
        self.calls = 0

    async def transcribe(self, audio: AudioData) -> str:
        self.calls += 1
        if not self.reply:
            raise ItemError("stt error 500: down")
        return self.reply


async def test_configured_stt_runs_before_the_llm_rung() -> None:
    log = DegradationLog()
    hears = Hears()  # the LLM that WOULD transcribe
    converter = make_converter(hears, allow_paid=True, log=log, stt=VerbatimStt())  # type: ignore[arg-type]
    text = await converter.audio_to_text(AudioData(b"x", "audio/wav"), "call.wav")
    assert text == "the verbatim words"
    assert hears.calls == []  # verbatim wins — the paraphrasing LLM never ran


async def test_stt_respects_the_consent_gate() -> None:
    log = DegradationLog()
    stt = VerbatimStt()
    converter = make_converter(None, allow_paid=False, log=log, stt=stt)  # type: ignore[arg-type]
    with pytest.raises(ItemError):  # no consent, no chat, no [audio] extra → the skip
        await converter.audio_to_text(AudioData(b"x", "audio/wav"), "call.wav")
    assert stt.calls == 0  # paid conversion never ran without consent


async def test_stt_failure_falls_down_the_ladder() -> None:
    log = DegradationLog()
    hears = Hears()
    converter = make_converter(hears, allow_paid=True, log=log, stt=VerbatimStt(reply=""))  # type: ignore[arg-type]
    text = await converter.audio_to_text(AudioData(b"x", "audio/wav"), "call.wav")
    assert text == "a steady 440 Hz tone"  # the wire hiccuped; the LLM rung caught it
