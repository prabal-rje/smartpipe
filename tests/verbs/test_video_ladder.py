"""The poor man's video (D27): frames + track, per-row disclosure, the map ladder."""

from __future__ import annotations

import io
import subprocess
from typing import TYPE_CHECKING

import pytest

from sempipe.core.errors import ExitCode, ItemError
from sempipe.io.diagnostics import DegradationLog
from sempipe.io.writers import OutputFormat, RenderMode, ResultWriter, WriterConfig, make_writer
from sempipe.models.base import AudioData, CompletionRequest, ImageData, ModelRef, VideoData
from sempipe.verbs.common import ensure_text
from sempipe.verbs.map import MapRequest, run_map

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO


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
    from sempipe.parsing.extract import video_to_parts

    parts = video_to_parts(VideoData(clip.read_bytes(), "video/mp4"), frames=4)
    assert 1 <= len(parts.frames) <= 4
    assert all(frame.mime == "image/jpeg" for frame in parts.frames)
    assert all(frame.data.startswith(b"\xff\xd8") for frame in parts.frames)  # real JPEGs
    assert parts.track is not None
    assert parts.track.data[:4] == b"RIFF"  # a real wav


def test_silent_video_has_no_track(tmp_path: Path) -> None:
    from sempipe.parsing.extract import video_to_parts

    path = tmp_path / "silent.mp4"
    _make_test_video(path, silent=True)
    parts = video_to_parts(VideoData(path.read_bytes(), "video/mp4"))
    assert parts.frames
    assert parts.track is None


def test_slice_video_reassembles_by_count(clip: Path) -> None:
    from sempipe.parsing.extract import slice_video

    slices = slice_video(VideoData(clip.read_bytes(), "video/mp4"), seconds=1)
    assert len(slices) >= 2  # a 2s clip at 1s segments
    assert all(part.data[4:8] == b"ftyp" for part in slices)  # each is a real mp4


async def test_ensure_text_transcribes_the_track_with_a_row_note(
    clip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import replace

    from sempipe.io.items import item_from_file

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
        return "watched it"


class SeesOnly:
    """Vision yes, audio no — like Claude or a local vision model."""

    def __init__(self) -> None:
        self.ref = ModelRef("anthropic", "claude-haiku-4-5")
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        if any(isinstance(part, AudioData) for part in request.media):
            raise ItemError("this model can't hear audio — …")
        return "saw frames and read the transcript"


class Ctx:
    def __init__(self, model: SeesAndHears | SeesOnly) -> None:
        self.model = model

    async def chat_model(self, flag: str | None = None) -> SeesAndHears | SeesOnly:
        return self.model

    async def context_window(self, ref: object) -> int | None:
        return None

    def concurrency(self, flag: int | None = None) -> int:
        return 1

    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextIO,
        fields: tuple[str, ...] | None = None,
    ) -> ResultWriter:
        return make_writer(WriterConfig(mode=RenderMode.TEXT, color=False, width=80), stdout)


class _TtyStdin(io.StringIO):
    def isatty(self) -> bool:
        return True


def _request(clip: Path) -> MapRequest:
    from sempipe.io.inputs import InputSpec

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
    media = model.calls[0].media
    assert any(isinstance(part, ImageData) for part in media)  # the frames
    assert any(isinstance(part, AudioData) for part in media)  # the heard track
    err = capsys.readouterr().err
    assert "video → frames+audio" in err  # the row disclosure


async def test_deaf_model_falls_to_frames_plus_transcript(
    clip: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from sempipe.parsing import extract

    def fake_transcribe(audio: AudioData) -> str:
        return "a constant tone hums"

    monkeypatch.setattr(extract, "transcribe_audio", fake_transcribe)
    model = SeesOnly()
    out = io.StringIO()
    code = await run_map(_request(clip), Ctx(model), stdin=_TtyStdin(), stdout=out)
    assert code is ExitCode.OK
    assert "saw frames and read the transcript" in out.getvalue()
    assert len(model.calls) == 2  # heard-attempt, then the transcript retry
    retry = model.calls[1]
    assert all(isinstance(part, ImageData) for part in retry.media)  # frames only
    assert "a constant tone hums" in retry.user  # the transcript rode the text
    err = capsys.readouterr().err
    assert "video audio → text" in err  # the second rung, row-noted
