"""Turn a file into text (spec §3.1, D08). Text kinds read directly; documents and
audio go through the lazily-imported markitdown bridge; images carry their bytes to
a vision model instead of being parsed.

Failure modes are typed: ``MissingExtra`` (an optional dependency isn't installed —
the reader shows a one-time install screen and skips), ``ItemError`` (this file
couldn't be parsed — skip with a warning).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from smartpipe.core.errors import ItemError
from smartpipe.core.jsontools import as_items, as_record
from smartpipe.models.base import AudioData, ImageData, VideoData
from smartpipe.parsing.detect import FileKind, route

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = [
    "EmbeddedImage",
    "EmbeddedMedia",
    "Extracted",
    "ImageData",
    "MissingExtra",
    "VideoParts",
    "embedded_images",
    "extract",
    "ffmpeg_exe",
    "pdf_page_texts",
    "slice_audio",
    "slice_video",
    "transcribe_audio",
    "video_to_parts",
    "whisper_size",
]

_IMAGE_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class MissingExtra(Exception):
    """An optional dependency needed to parse this file isn't installed."""

    def __init__(self, extra: str, guidance: str) -> None:
        super().__init__(guidance)
        self.extra = extra
        self.guidance = guidance


@dataclass(frozen=True, slots=True)
class Extracted:
    text: str
    image: ImageData | None = None
    warning: str | None = None


def extract(path: Path, kind: FileKind) -> Extracted:
    match route(kind):
        case "text":
            return _read_text(path)
        case "doc":
            return Extracted(text=_via_markitdown(path, extra="files", noun="documents"))
        case "audio":
            return Extracted(text=_via_markitdown(path, extra="audio", noun="audio"))
        case "video":
            raise ItemError(
                "video reaches text extraction unconverted — this is a smartpipe bug"
            )  # readers hand video BYTES to the verbs; conversion is per-verb (D27)
        case "image":
            return Extracted(text="", image=_read_image(path))
        case "skip":
            raise ItemError("unsupported format")
        case _ as unreachable:  # pragma: no cover
            assert_never(unreachable)


def _read_text(path: Path) -> Extracted:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    if "�" in text and b"\xef\xbf\xbd" not in raw:
        return Extracted(text=text, warning="not valid UTF-8; some bytes were replaced")
    return Extracted(text=text)


def _read_image(path: Path) -> ImageData:
    mime = _IMAGE_MIME.get(path.suffix.lower(), "image/png")
    return ImageData(data=path.read_bytes(), mime=mime)


def pdf_page_texts(path: Path) -> list[str]:
    """Per-page text of a PDF (D26 rich split). Needs the [files] extra."""
    try:
        from pdfminer.high_level import extract_text
        from pdfminer.pdfpage import PDFPage
    except ImportError as exc:
        raise MissingExtra(
            "files",
            "error: parsing documents needs an optional dependency\n"
            "  install it with:  pip install 'smartpipe[files]'",
        ) from exc
    try:
        with path.open("rb") as handle:
            count = sum(1 for _ in PDFPage.get_pages(handle))
        return [extract_text(str(path), page_numbers={number}) or "" for number in range(count)]
    except MissingExtra:  # pragma: no cover — nothing above raises it here
        raise
    except Exception as exc:
        raise ItemError(f"{path.name} couldn't be parsed as a PDF ({exc})") from exc


def slice_audio(audio: AudioData, *, seconds: int) -> list[AudioData]:
    """Duration slices of an audio payload (D27): wav natively, ffmpeg otherwise.

    Slicing is lossless re-segmentation — every slice is a valid standalone file
    of the same kind, so each can ride the native-hearing wire on its own.
    """
    if audio.mime in ("audio/wav", "audio/x-wav"):
        return _slice_wav(audio, seconds=seconds)
    return _slice_via_ffmpeg(audio, seconds=seconds)


def _slice_wav(audio: AudioData, *, seconds: int) -> list[AudioData]:
    import io
    import wave

    try:
        with wave.open(io.BytesIO(audio.data)) as reader:
            params = reader.getparams()
            frames_per_slice = params.framerate * seconds
            slices: list[AudioData] = []
            while True:
                frames = reader.readframes(frames_per_slice)
                if not frames:
                    break
                buffer = io.BytesIO()
                with wave.open(buffer, "wb") as writer:
                    writer.setnchannels(params.nchannels)
                    writer.setsampwidth(params.sampwidth)
                    writer.setframerate(params.framerate)
                    writer.writeframes(frames)
                slices.append(AudioData(data=buffer.getvalue(), mime="audio/wav"))
        return slices or [audio]
    except ItemError:  # pragma: no cover — nothing above raises it
        raise
    except Exception as exc:
        raise ItemError(f"audio couldn't be sliced ({exc})") from exc


def _slice_via_ffmpeg(audio: AudioData, *, seconds: int) -> list[AudioData]:
    import shutil

    if shutil.which("ffmpeg") is None:
        raise ItemError(
            "slicing this format needs ffmpeg on PATH (wav slices natively)\n"
            "  Install ffmpeg, or convert first: ffmpeg -i in.mp3 out.wav"
        )
    import contextlib
    import os
    import subprocess
    import tempfile

    workdir = tempfile.mkdtemp(prefix="smartpipe-slice-")
    source = os.path.join(workdir, "source")
    pattern = os.path.join(workdir, "slice-%04d.wav")
    try:
        with open(source, "wb") as handle:
            handle.write(audio.data)
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-i",
                source,
                "-f",
                "segment",
                "-segment_time",
                str(seconds),
                "-c:a",
                "pcm_s16le",
                pattern,
            ],
            check=True,
            capture_output=True,
        )
        names = sorted(name for name in os.listdir(workdir) if name.startswith("slice-"))
        return [
            AudioData(data=open(os.path.join(workdir, name), "rb").read(), mime="audio/wav")
            for name in names
        ] or [audio]
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip().splitlines()
        raise ItemError(
            f"ffmpeg couldn't slice it ({detail[-1] if detail else 'unknown'})"
        ) from exc
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(workdir)


_MEDIA_FLOOR_BYTES = 4_096  # icons, bullets, rules — decoration, not content
_OFFICE_MEDIA_DIRS = {".docx": "word/media/", ".pptx": "ppt/media/", ".xlsx": "xl/media/"}
_IMAGE_MIME_BY_NAME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@dataclass(frozen=True, slots=True)
class EmbeddedImage:
    image: ImageData
    where: str  # "p.7 img.2" (PDF) / "img.3" (office zip)


@dataclass(frozen=True, slots=True)
class EmbeddedMedia:
    images: tuple[EmbeddedImage, ...]
    dropped_small: int  # under the floor — counted, disclosed once


def embedded_images(path: Path) -> EmbeddedMedia:
    """Images embedded inside a document (D29): office zips via the stdlib,
    PDFs by walking XObjects for JPEG (DCTDecode) streams — passed through
    byte-identical, never re-encoded."""
    suffix = path.suffix.lower()
    if suffix in _OFFICE_MEDIA_DIRS:
        return _office_zip_images(path, _OFFICE_MEDIA_DIRS[suffix])
    if suffix == ".pdf":
        return _pdf_images(path)
    raise ItemError(f"{path.name} isn't a document with embedded media (pdf/docx/pptx/xlsx)")


def _office_zip_images(path: Path, media_dir: str) -> EmbeddedMedia:
    import zipfile
    from pathlib import Path

    images: list[EmbeddedImage] = []
    dropped = 0
    try:
        with zipfile.ZipFile(path) as archive:
            names = sorted(n for n in archive.namelist() if n.startswith(media_dir))
            for position, name in enumerate(names, start=1):
                mime = _IMAGE_MIME_BY_NAME.get(Path(name).suffix.lower())
                if mime is None:
                    continue  # emf/wmf and friends — no model reads them
                payload = archive.read(name)
                if len(payload) < _MEDIA_FLOOR_BYTES:
                    dropped += 1
                    continue
                images.append(EmbeddedImage(ImageData(payload, mime), f"img.{position}"))
    except ItemError:  # pragma: no cover — nothing above raises it
        raise
    except Exception as exc:
        raise ItemError(f"{path.name} couldn't be opened as an office document ({exc})") from exc
    return EmbeddedMedia(tuple(images), dropped)


def _pdf_images(path: Path) -> EmbeddedMedia:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise MissingExtra(
            "files",
            "error: parsing documents needs an optional dependency\n"
            "  install it with:  pip install 'smartpipe[files]'",
        ) from exc
    images: list[EmbeddedImage] = []
    dropped = 0
    try:
        reader = PdfReader(str(path))
        for page_number, page in enumerate(reader.pages, start=1):
            xobjects = as_record(_pdf_lookup(_pdf_lookup(page, "/Resources"), "/XObject"))
            if xobjects is None:
                continue
            position = 0
            for key in sorted(xobjects):
                stream = _pdf_resolve(xobjects.get(key))
                if _pdf_lookup(stream, "/Subtype") != "/Image":
                    continue
                filters = as_items(_pdf_lookup(stream, "/Filter"))
                names = (
                    [str(entry) for entry in filters]
                    if filters is not None
                    else [str(_pdf_lookup(stream, "/Filter"))]
                )
                if "/DCTDecode" not in names:
                    continue  # only JPEG streams pass through without re-encoding
                position += 1
                payload = getattr(stream, "_data", b"")
                if not isinstance(payload, bytes):
                    continue
                if len(payload) < _MEDIA_FLOOR_BYTES:
                    dropped += 1
                    continue
                images.append(
                    EmbeddedImage(
                        ImageData(payload, "image/jpeg"), f"p.{page_number} img.{position}"
                    )
                )
    except MissingExtra:
        raise
    except Exception as exc:
        raise ItemError(f"{path.name} couldn't be scanned for images ({exc})") from exc
    return EmbeddedMedia(tuple(images), dropped)


def _pdf_resolve(value: object) -> object:
    """Follow an IndirectObject reference; anything else passes through."""
    resolver = getattr(value, "get_object", None)
    return resolver() if callable(resolver) else value


def _pdf_lookup(mapping: object, key: str) -> object:
    """Duck lookup on pypdf's dict-like objects, reference-chased, None-safe."""
    resolved = _pdf_resolve(mapping)
    record = as_record(resolved)
    if record is not None:
        return _pdf_resolve(record.get(key))
    getter = getattr(resolved, "get", None)
    if not callable(getter):
        return None
    looked: object = getter(key)
    return _pdf_resolve(looked)


_VIDEO_NEEDS_FFMPEG = (
    "working with video needs ffmpeg\n"
    "  install the extra:  pip install 'smartpipe[video]'   (bundles a static ffmpeg)\n"
    "  or put ffmpeg on PATH"
)


def ffmpeg_exe() -> str:
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except ImportError:
        import shutil

        exe = shutil.which("ffmpeg")
        if exe is None:
            raise ItemError(_VIDEO_NEEDS_FFMPEG) from None
        return exe


def _ffprobe_duration(exe: str, source: str) -> float:
    """Parse "Duration: HH:MM:SS.cc" from ffmpeg's banner (no ffprobe needed)."""
    import re
    import subprocess

    result = subprocess.run([exe, "-i", source], capture_output=True, check=False)
    match = re.search(rb"Duration: (\d+):(\d+):(\d+\.?\d*)", result.stderr)
    if match is None:
        raise ItemError("ffmpeg couldn't read the video's duration")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


@dataclass(frozen=True, slots=True)
class VideoParts:
    frames: tuple[ImageData, ...]
    track: AudioData | None  # None when the video is silent


def video_to_parts(
    video: VideoData, *, max_frames: int = 24, every_seconds: float | None = None
) -> VideoParts:
    """Frames + the audio track (D27/D36/D43). Default: 1 frame per second up
    to ``max_frames``, evenly spread past the cap. ``every_seconds`` is a
    DENSITY GUARANTEE — one frame per period, and the default cap lifts
    (callers pass their own ``max_frames`` to keep a budget; the smaller wins).

    Free and local (ffmpeg). Blocking — callers run it in a thread.
    """
    import contextlib
    import os
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    exe = ffmpeg_exe()
    workdir = tempfile.mkdtemp(prefix="smartpipe-video-")
    source = os.path.join(workdir, "source")
    try:
        with open(source, "wb") as handle:
            handle.write(video.data)
        duration = _ffprobe_duration(exe, source)
        if every_seconds is not None:
            # D43: the density guarantee — one frame per period, cap honored
            rate = 1.0 / every_seconds
            if duration > 0:
                wanted = max(1, math.ceil(duration / every_seconds))
                max_frames = min(max_frames, wanted) if max_frames else wanted
        else:
            # 1 fps for clips up to the cap; longer clips spread the cap evenly (D36)
            rate = 1.0 if 0 < duration <= max_frames else max(max_frames / duration, 0.01)
        subprocess.run(
            [
                exe,
                "-loglevel",
                "error",
                "-i",
                source,
                "-vf",
                f"fps={rate}",
                "-frames:v",
                str(max_frames),
                os.path.join(workdir, "frame-%03d.jpg"),
            ],
            check=True,
            capture_output=True,
        )
        names = sorted(n for n in os.listdir(workdir) if n.startswith("frame-"))
        images = tuple(
            ImageData(Path(os.path.join(workdir, name)).read_bytes(), "image/jpeg")
            for name in names
        )
        track_path = os.path.join(workdir, "track.wav")
        probe = subprocess.run(
            [
                exe,
                "-loglevel",
                "error",
                "-i",
                source,
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                track_path,
            ],
            check=False,
            capture_output=True,
        )
        track = None
        if probe.returncode == 0 and os.path.exists(track_path):
            payload = Path(track_path).read_bytes()
            if len(payload) > 44:  # more than a bare wav header — a real track
                track = AudioData(payload, "audio/wav")
        if not images:
            raise ItemError("ffmpeg produced no frames from this video")
        return VideoParts(frames=images, track=track)
    except ItemError:
        raise
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip().splitlines()
        raise ItemError(
            f"ffmpeg couldn't read this video ({detail[-1] if detail else 'unknown'})"
        ) from exc
    except Exception as exc:
        raise ItemError(f"video couldn't be converted ({exc})") from exc
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(workdir)


def slice_video(video: VideoData, *, seconds: int) -> list[VideoData]:
    """Duration slices of a video, stream-copied (fast, lossless) via ffmpeg."""
    import contextlib
    import os
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    exe = ffmpeg_exe()
    workdir = tempfile.mkdtemp(prefix="smartpipe-vslice-")
    source = os.path.join(workdir, "source.mp4")
    pattern = os.path.join(workdir, "slice-%04d.mp4")
    try:
        with open(source, "wb") as handle:
            handle.write(video.data)
        subprocess.run(
            [
                exe,
                "-loglevel",
                "error",
                "-i",
                source,
                "-f",
                "segment",
                "-segment_time",
                str(seconds),
                "-c",
                "copy",
                "-reset_timestamps",
                "1",
                pattern,
            ],
            check=True,
            capture_output=True,
        )
        names = sorted(n for n in os.listdir(workdir) if n.startswith("slice-"))
        return [
            VideoData(Path(os.path.join(workdir, name)).read_bytes(), "video/mp4") for name in names
        ] or [video]
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip().splitlines()
        raise ItemError(
            f"ffmpeg couldn't slice this video ({detail[-1] if detail else 'unknown'})"
        ) from exc
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(workdir)


def whisper_size(environ: Mapping[str, str]) -> str:
    """The local whisper variant: ``SMARTPIPE_WHISPER_MODEL`` or the tiny default."""
    return environ.get("SMARTPIPE_WHISPER_MODEL", "tiny")


_WHISPER_CACHE: dict[str, object] = {}  # one loaded model per size, per process


def transcribe_audio(audio: AudioData) -> str:
    """In-memory audio → transcript, locally, via faster-whisper (D20 rung 2).

    The audio bytes never leave the machine; the first use of a model size
    downloads its weights once (~75 MB for tiny). Blocking — callers run it in
    a thread. ``MissingExtra`` propagates so the verb layer can name both fixes.
    """
    import io
    import os

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise MissingExtra(  # whisper ships in core (D44); only a broken env lands here
            "audio", "local transcription is unavailable — reinstall smartpipe"
        ) from exc

    size = whisper_size(os.environ)
    model = _WHISPER_CACHE.get(size)
    if model is None:
        from smartpipe.io import diagnostics

        diagnostics.note(f"loading local whisper ({size}) — first use downloads the model")
        model = WhisperModel(size, device="cpu", compute_type="int8")
        _WHISPER_CACHE[size] = model
    assert isinstance(model, WhisperModel)
    try:
        segments, _info = model.transcribe(io.BytesIO(audio.data))
        return " ".join(segment.text.strip() for segment in segments).strip()
    except MissingExtra:  # pragma: no cover — nothing below raises it
        raise
    except Exception as exc:
        raise ItemError(f"audio couldn't be transcribed ({exc})") from exc


def _via_markitdown(path: Path, *, extra: str, noun: str) -> str:
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        raise MissingExtra(
            extra,
            f"error: parsing {noun} needs an optional dependency\n"
            f"  install it with:  pip install 'smartpipe[{extra}]'",
        ) from exc
    try:
        result = MarkItDown().convert(str(path))
    except Exception as exc:  # markitdown raises many types; any of them is a parse failure
        raise ItemError("parse error") from exc
    return result.text_content
