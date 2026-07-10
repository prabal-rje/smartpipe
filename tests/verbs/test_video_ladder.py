"""The poor man's video (D27): frames + track, per-row disclosure, the map ladder."""

from __future__ import annotations

import io
import subprocess
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, ItemError
from smartpipe.io.diagnostics import DegradationLog
from smartpipe.io.writers import OutputFormat, RenderMode, ResultWriter, WriterConfig, make_writer
from smartpipe.models.base import AudioData, CompletionRequest, ImageData, ModelRef, VideoData
from smartpipe.verbs.common import ensure_text
from smartpipe.verbs.map import MapRequest, run_map

if TYPE_CHECKING:
    from pathlib import Path

    from smartpipe.engine.coalesce import BatchSettings
    from smartpipe.io.writers import TextSink
    from smartpipe.models.base import ChatModel


def _make_test_video(path: Path, *, seconds: int = 2, silent: bool = False) -> None:
    """A tiny real mp4 via the bundled ffmpeg: color bars + a 440 Hz tone."""
    from imageio_ffmpeg import get_ffmpeg_exe

    video_in = ["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=8"]
    audio_in = [] if silent else ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    subprocess.run(
        [
            get_ffmpeg_exe(),
            "-loglevel",
            "error",
            *video_in,
            *audio_in,
            "-pix_fmt",
            "yuv420p",
            "-g",
            "8",
            "-keyint_min",
            "8",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )  # keyframe every second: segment-copy can only cut at keyframes


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("video") / "clip.mp4"
    _make_test_video(path)
    return path


def test_video_to_parts_yields_frames_and_track(clip: Path) -> None:
    from smartpipe.parsing.extract import video_to_parts

    parts = video_to_parts(VideoData(clip.read_bytes(), "video/mp4"), max_frames=4)
    assert 1 <= len(parts.frames) <= 4  # 2s clip at 1 fps → ~2 frames
    assert all(frame.mime == "image/jpeg" for frame in parts.frames)
    assert all(frame.data.startswith(b"\xff\xd8") for frame in parts.frames)  # real JPEGs
    assert parts.track is not None
    assert parts.track.data[:4] == b"RIFF"  # a real wav


def test_silent_video_has_no_track(tmp_path: Path) -> None:
    from smartpipe.parsing.extract import video_to_parts

    path = tmp_path / "silent.mp4"
    _make_test_video(path, silent=True)
    parts = video_to_parts(VideoData(path.read_bytes(), "video/mp4"))
    assert parts.frames
    assert parts.track is None


def test_slice_video_reassembles_by_count(clip: Path) -> None:
    from smartpipe.parsing.extract import slice_video

    slices = slice_video(VideoData(clip.read_bytes(), "video/mp4"), seconds=1)
    assert len(slices) >= 2  # a 2s clip at 1s segments
    assert all(part.data[4:8] == b"ftyp" for part in slices)  # each is a real mp4


async def test_ensure_text_transcribes_the_track_with_a_row_note(
    clip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import replace

    from smartpipe.io.items import item_from_file

    item = replace(
        item_from_file("", str(clip), 0), media=(VideoData(clip.read_bytes(), "video/mp4"),)
    )

    def fake_transcriber(audio: AudioData) -> str:
        assert audio.mime == "audio/wav"
        return "a steady tone plays"

    log = DegradationLog()
    spoken = await ensure_text(item, transcriber=fake_transcriber, log=log)
    log.finish()
    assert spoken.text == "a steady tone plays"
    assert spoken.media == ()
    err = capsys.readouterr().err
    assert "degraded:" in err and "video → text" in err
    assert "frames dropped" in err


# --- the map ladder -------------------------------------------------------------


class SeesAndHears:
    def __init__(self) -> None:
        self.ref = ModelRef("openai", "gpt-4o-audio-preview")
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        if any(isinstance(part, VideoData) for part in request.media):
            raise ItemError("this model can't watch video — …")  # rung 0 refused, free
        return "watched it"


class Watches:
    """The gemini-native shape: accepts the raw video (D34 rung 0)."""

    def __init__(self) -> None:
        self.ref = ModelRef("gemini", "gemini-2.5-flash")
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        return "watched the actual video"


class SeesOnly:
    """Vision yes, audio no — like Claude or a local vision model."""

    def __init__(self) -> None:
        self.ref = ModelRef("anthropic", "claude-haiku-4-5")
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        if any(isinstance(part, VideoData) for part in request.media):
            raise ItemError("this model can't watch video — …")
        if any(isinstance(part, AudioData) for part in request.media):
            raise ItemError("this model can't hear audio — …")
        return "saw frames and read the transcript"


class Ctx:
    def __init__(self, model: SeesAndHears | SeesOnly | Watches) -> None:
        self.model = model

    async def chat_model(self, flag: str | None = None) -> SeesAndHears | SeesOnly | Watches:
        return self.model

    async def context_window(self, ref: object) -> int | None:
        return None

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


def _request(clip: Path) -> MapRequest:
    from smartpipe.io.inputs import InputSpec

    return MapRequest(
        prompt="what happens in this video?",
        schema_path=None,
        model_flag=None,
        output=OutputFormat.TEXT,
        concurrency_flag=None,
        input=InputSpec(patterns=(str(clip),), from_files=False),
    )


async def test_hearing_model_gets_frames_plus_track(
    clip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    model = SeesAndHears()
    out = io.StringIO()
    code = await run_map(_request(clip), Ctx(model), stdin=_TtyStdin(), stdout=out)
    assert code is ExitCode.OK
    assert out.getvalue() == "watched it\n"
    assert len(model.calls) == 2  # rung 0 (raw video, refused free), then frames+track
    media = model.calls[1].media
    assert any(isinstance(part, ImageData) for part in media)  # the frames
    assert any(isinstance(part, AudioData) for part in media)  # the heard track
    err = capsys.readouterr().err
    assert "video → frames+audio" in err  # the row disclosure


async def test_deaf_model_falls_to_frames_plus_transcript(
    clip: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from smartpipe.parsing import extract

    def fake_transcribe(audio: AudioData) -> str:
        return "a constant tone hums"

    monkeypatch.setattr(extract, "transcribe_audio", fake_transcribe)
    model = SeesOnly()
    out = io.StringIO()
    code = await run_map(_request(clip), Ctx(model), stdin=_TtyStdin(), stdout=out)
    assert code is ExitCode.OK
    assert "saw frames and read the transcript" in out.getvalue()
    assert len(model.calls) == 3  # raw video, frames+track, then frames+transcript
    retry = model.calls[2]
    assert all(isinstance(part, ImageData) for part in retry.media)  # frames only
    assert "a constant tone hums" in retry.user  # the transcript rode the text
    err = capsys.readouterr().err
    assert "video audio → text" in err  # the second rung, row-noted


async def test_watching_wire_gets_the_raw_video(clip: Path) -> None:
    """D34 rung 0: on a wire that watches (gemini native), the VIDEO itself rides."""
    model = Watches()
    out = io.StringIO()
    code = await run_map(_request(clip), Ctx(model), stdin=_TtyStdin(), stdout=out)
    assert code is ExitCode.OK
    assert out.getvalue() == "watched the actual video\n"
    assert len(model.calls) == 1  # no conversion, no second attempt
    assert isinstance(model.calls[0].media[0], VideoData)  # the bytes rode whole


def test_frame_every_is_a_density_guarantee(clip: Path) -> None:
    from smartpipe.parsing.extract import video_to_parts

    parts = video_to_parts(
        VideoData(clip.read_bytes(), "video/mp4"), max_frames=1000, every_seconds=0.5
    )
    assert 3 <= len(parts.frames) <= 5  # 2s at one frame per 0.5s ≈ 4


def test_max_frames_still_caps_the_density(clip: Path) -> None:
    from smartpipe.parsing.extract import video_to_parts

    parts = video_to_parts(
        VideoData(clip.read_bytes(), "video/mp4"), max_frames=2, every_seconds=0.25
    )
    assert len(parts.frames) <= 2  # the smaller of the two wins
