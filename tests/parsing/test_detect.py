from __future__ import annotations

from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from sempipe.parsing.detect import FileKind, detect_kind, route

# minimal magic-byte samples — enough to exercise the sniffer without binary fixtures
PDF = b"%PDF-1.7\n..."
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8
WAV = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 8
FLAC = b"fLaC" + b"\x00" * 16
MP3_ID3 = b"ID3\x03\x00" + b"\x00" * 16
DOCX = b"PK\x03\x04" + b"\x00" * 26 + b"word/document.xml"
TEXT = b"hello, world\nsecond line\n"


# --- extension-first ----------------------------------------------------------


def test_extension_wins_over_content() -> None:
    # a .md file whose bytes look like text → MARKDOWN by extension
    assert detect_kind(Path("notes.md"), TEXT) == FileKind.MARKDOWN
    assert detect_kind(Path("data.csv"), TEXT) == FileKind.CSV
    assert detect_kind(Path("report.pdf"), PDF) == FileKind.PDF
    assert detect_kind(Path("deck.pptx"), DOCX) == FileKind.PPTX
    assert detect_kind(Path("clip.mp3"), MP3_ID3) == FileKind.AUDIO
    assert detect_kind(Path("photo.jpg"), JPEG) == FileKind.IMAGE


# --- magic-byte backstop (no / unknown extension) -----------------------------


def test_magic_bytes_when_no_extension() -> None:
    assert detect_kind(Path("mystery"), PDF) == FileKind.PDF
    assert detect_kind(Path("mystery"), PNG) == FileKind.IMAGE
    assert detect_kind(Path("mystery"), JPEG) == FileKind.IMAGE
    assert detect_kind(Path("mystery"), GIF) == FileKind.IMAGE
    assert detect_kind(Path("mystery"), WEBP) == FileKind.IMAGE
    assert detect_kind(Path("mystery"), WAV) == FileKind.AUDIO
    assert detect_kind(Path("mystery"), FLAC) == FileKind.AUDIO
    assert detect_kind(Path("mystery"), MP3_ID3) == FileKind.AUDIO
    assert detect_kind(Path("mystery"), DOCX) == FileKind.DOCX


def test_utf8_decodable_is_text() -> None:
    assert detect_kind(Path("mystery"), TEXT) == FileKind.TEXT


def test_ooxml_zips_by_inner_name() -> None:
    base = b"PK\x03\x04" + b"\x00" * 26
    assert detect_kind(Path("sheet"), base + b"xl/workbook.xml") == FileKind.XLSX
    assert detect_kind(Path("slides"), base + b"ppt/presentation.xml") == FileKind.PPTX


def test_mp3_frame_sync_variants() -> None:
    assert detect_kind(Path("clip"), b"\xff\xfb\x90\x00" + b"\x00" * 12) == FileKind.AUDIO
    assert detect_kind(Path("clip"), b"\xff\xf3\x90\x00" + b"\x00" * 12) == FileKind.AUDIO


def test_epub_zip_is_detected() -> None:
    epub = b"PK\x03\x04" + b"\x00" * 26 + b"mimetypeapplication/epub+zip"
    assert detect_kind(Path("book"), epub) == FileKind.EPUB


def test_unknown_zip_is_binary() -> None:
    # a zip that isn't OOXML/EPUB (no word//xl//ppt//epub markers near the front)
    zip_blob = b"PK\x03\x04" + b"\x00" * 26 + b"random/thing.dat"
    assert detect_kind(Path("archive"), zip_blob) == FileKind.UNKNOWN_BINARY


def test_truncated_multibyte_tail_is_text() -> None:
    # a valid UTF-8 body with a multibyte char cut off at the sniff boundary
    truncated = "hello wörld ".encode() + b"\xc3"  # dangling lead byte at the very end
    assert detect_kind(Path("mystery"), truncated) == FileKind.TEXT


def test_binary_with_invalid_byte_mid_stream_is_binary() -> None:
    mid = b"text \xff\xfe more text that continues well past the boundary here"
    assert detect_kind(Path("mystery"), mid) == FileKind.UNKNOWN_BINARY


def test_random_bytes_are_unknown_binary() -> None:
    assert detect_kind(Path("mystery"), b"\x00\x01\x02\xff\xfe\x87\x88") == FileKind.UNKNOWN_BINARY


def test_empty_file_is_text() -> None:
    assert detect_kind(Path("empty.txt"), b"") == FileKind.TEXT


# --- routing ------------------------------------------------------------------


def test_route_groups_kinds() -> None:
    assert route(FileKind.TEXT) == "text"
    assert route(FileKind.MARKDOWN) == "text"
    assert route(FileKind.PDF) == "doc"
    assert route(FileKind.DOCX) == "doc"
    assert route(FileKind.AUDIO) == "audio"
    assert route(FileKind.IMAGE) == "image"
    assert route(FileKind.UNKNOWN_BINARY) == "skip"


# --- property: never raises ---------------------------------------------------


@given(name=st.text(max_size=20), head=st.binary(max_size=64))
def test_detect_never_raises(name: str, head: bytes) -> None:
    kind = detect_kind(Path(name or "x"), head)
    assert isinstance(kind, FileKind)
