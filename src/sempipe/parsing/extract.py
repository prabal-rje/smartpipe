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

__all__ = ["Extracted", "ImageData", "MissingExtra", "extract", "transcribe_audio", "whisper_size"]

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
