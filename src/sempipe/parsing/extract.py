"""Turn a file into text (spec §3.1, D08). Text kinds read directly; documents and
audio go through the lazily-imported markitdown bridge; images carry their bytes to
a vision model instead of being parsed.

Failure modes are typed: ``MissingExtra`` (an optional dependency isn't installed —
the reader shows a one-time install screen and skips), ``ItemError`` (this file
couldn't be parsed — skip with a warning).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from sempipe.core.errors import ItemError
from sempipe.models.base import AudioData, ImageData
from sempipe.parsing.detect import FileKind, route

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = [
    "Extracted",
    "ImageData",
    "MissingExtra",
    "extract",
    "pdf_page_texts",
    "slice_audio",
    "transcribe_audio",
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
            "  install it with:  pip install 'sempipe[files]'",
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

    workdir = tempfile.mkdtemp(prefix="sempipe-slice-")
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


def whisper_size(environ: Mapping[str, str]) -> str:
    """The local whisper variant: ``SEMPIPE_WHISPER_MODEL`` or the tiny default."""
    return environ.get("SEMPIPE_WHISPER_MODEL", "tiny")


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
        raise MissingExtra(
            "audio", "transcribing audio needs: pip install 'sempipe[audio]'"
        ) from exc

    size = whisper_size(os.environ)
    model = _WHISPER_CACHE.get(size)
    if model is None:
        from sempipe.io import diagnostics

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
            f"  install it with:  pip install 'sempipe[{extra}]'",
        ) from exc
    try:
        result = MarkItDown().convert(str(path))
    except Exception as exc:  # markitdown raises many types; any of them is a parse failure
        raise ItemError("parse error") from exc
    return result.text_content
