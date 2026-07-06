"""Detect a file's kind (spec §3.1, stage-07 contract) — pure, never raises.

Extension first, magic bytes as a backstop. Detection only *classifies*; extraction
(and any missing-dependency handling) lives in ``extract``.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["FileKind", "Route", "audio_mime", "detect_kind", "route"]

Route = Literal["text", "doc", "audio", "image", "skip"]


class FileKind(Enum):
    TEXT = "text"
    MARKDOWN = "markdown"
    CSV = "csv"
    JSON = "json"
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    HTML = "html"
    EPUB = "epub"
    AUDIO = "audio"
    IMAGE = "image"
    UNKNOWN_BINARY = "unknown-binary"


_BY_EXTENSION: dict[str, FileKind] = {
    ".txt": FileKind.TEXT,
    ".text": FileKind.TEXT,
    ".log": FileKind.TEXT,
    ".md": FileKind.MARKDOWN,
    ".markdown": FileKind.MARKDOWN,
    ".csv": FileKind.CSV,
    ".tsv": FileKind.CSV,
    ".json": FileKind.JSON,
    ".jsonl": FileKind.JSON,
    ".ndjson": FileKind.JSON,
    ".pdf": FileKind.PDF,
    ".docx": FileKind.DOCX,
    ".xlsx": FileKind.XLSX,
    ".pptx": FileKind.PPTX,
    ".html": FileKind.HTML,
    ".htm": FileKind.HTML,
    ".epub": FileKind.EPUB,
    ".mp3": FileKind.AUDIO,
    ".wav": FileKind.AUDIO,
    ".flac": FileKind.AUDIO,
    ".m4a": FileKind.AUDIO,
    ".ogg": FileKind.AUDIO,
    ".png": FileKind.IMAGE,
    ".jpg": FileKind.IMAGE,
    ".jpeg": FileKind.IMAGE,
    ".gif": FileKind.IMAGE,
    ".webp": FileKind.IMAGE,
}

_ROUTES: dict[FileKind, Route] = {
    FileKind.TEXT: "text",
    FileKind.MARKDOWN: "text",
    FileKind.CSV: "text",
    FileKind.JSON: "text",
    FileKind.PDF: "doc",
    FileKind.DOCX: "doc",
    FileKind.XLSX: "doc",
    FileKind.PPTX: "doc",
    FileKind.HTML: "doc",
    FileKind.EPUB: "doc",
    FileKind.AUDIO: "audio",
    FileKind.IMAGE: "image",
    FileKind.UNKNOWN_BINARY: "skip",
}


_AUDIO_MIME_BY_SUFFIX = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
}


def audio_mime(path: Path) -> str:
    """The wire mime for an audio file — suffix-driven, mp3 as the safe default."""
    return _AUDIO_MIME_BY_SUFFIX.get(path.suffix.lower(), "audio/mpeg")


def route(kind: FileKind) -> Route:
    return _ROUTES[kind]


def detect_kind(path: Path, head: bytes) -> FileKind:
    by_ext = _BY_EXTENSION.get(path.suffix.lower())
    if by_ext is not None:
        return by_ext
    return _sniff(head)


def _sniff(head: bytes) -> FileKind:
    if head.startswith(b"%PDF"):
        return FileKind.PDF
    if head.startswith(b"PK\x03\x04"):
        return _sniff_zip(head)
    if _is_audio(head):
        return FileKind.AUDIO
    if _is_image(head):
        return FileKind.IMAGE
    if _is_utf8(head):
        return FileKind.TEXT
    return FileKind.UNKNOWN_BINARY


def _sniff_zip(head: bytes) -> FileKind:
    # OOXML/EPUB are zips; the inner directory names appear near the front
    if b"word/" in head:
        return FileKind.DOCX
    if b"xl/" in head:
        return FileKind.XLSX
    if b"ppt/" in head:
        return FileKind.PPTX
    if b"epub" in head or b"mimetype" in head:
        return FileKind.EPUB
    return FileKind.UNKNOWN_BINARY


def _is_audio(head: bytes) -> bool:
    if head.startswith((b"ID3", b"fLaC", b"OggS")):
        return True
    if head.startswith(b"\xff\xfb") or head.startswith(b"\xff\xf3"):  # MP3 frame sync
        return True
    return head.startswith(b"RIFF") and head[8:12] == b"WAVE"


def _is_image(head: bytes) -> bool:
    if head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")):
        return True
    return head.startswith(b"RIFF") and head[8:12] == b"WEBP"


def _is_utf8(head: bytes) -> bool:
    if b"\x00" in head:
        return False  # NUL bytes never appear in text; a strong binary signal
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # tolerate an invalid byte only in the last 3 positions — a multibyte char
        # truncated at the 8 KiB sniff boundary, not mid-stream binary
        return exc.start >= len(head) - 3
    return True
