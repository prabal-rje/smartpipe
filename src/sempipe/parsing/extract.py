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
    from pathlib import Path

__all__ = ["Extracted", "ImageData", "MissingExtra", "extract", "transcribe_audio"]

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


_SUFFIX_BY_MIME = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
}


def transcribe_audio(audio: AudioData) -> str:
    """In-memory audio → transcript via the ``[audio]`` extra (D20 rung 2).

    Blocking (markitdown is sync) — callers run it in a thread. ``MissingExtra``
    propagates so the verb layer can name both fixes.
    """
    import contextlib
    import os
    import tempfile

    suffix = _SUFFIX_BY_MIME.get(audio.mime, ".mp3")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(audio.data)
        name = handle.name
    try:
        from pathlib import Path

        return _via_markitdown(Path(name), extra="audio", noun="audio")
    finally:
        with contextlib.suppress(OSError):
            os.unlink(name)


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
