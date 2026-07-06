from __future__ import annotations

import sys
import types
from typing import TYPE_CHECKING

import pytest

from sempipe.core.errors import ItemError
from sempipe.parsing.detect import FileKind
from sempipe.parsing.extract import ImageData, MissingExtra, extract

if TYPE_CHECKING:
    from pathlib import Path


# --- text ---------------------------------------------------------------------


def test_reads_utf8_text(tmp_path: Path) -> None:
    f = tmp_path / "note.txt"
    f.write_text("héllo wörld", encoding="utf-8")
    result = extract(f, FileKind.TEXT)
    assert result.text == "héllo wörld"
    assert result.image is None
    assert result.warning is None


def test_invalid_utf8_replaced_with_warning(tmp_path: Path) -> None:
    f = tmp_path / "bad.txt"
    f.write_bytes(b"ok \xff\xfe bytes")
    result = extract(f, FileKind.TEXT)
    assert "�" in result.text
    assert result.warning is not None


# --- image --------------------------------------------------------------------


def test_image_carries_bytes_not_text(tmp_path: Path) -> None:
    f = tmp_path / "pic.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\nDATA")
    result = extract(f, FileKind.IMAGE)
    assert result.text == ""
    assert result.image == ImageData(data=b"\x89PNG\r\n\x1a\nDATA", mime="image/png")


def test_jpeg_mime(tmp_path: Path) -> None:
    f = tmp_path / "pic.jpg"
    f.write_bytes(b"\xff\xd8\xff")
    result = extract(f, FileKind.IMAGE)
    assert result.image is not None
    assert result.image.mime == "image/jpeg"


# --- unknown ------------------------------------------------------------------


def test_unknown_binary_is_item_error(tmp_path: Path) -> None:
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x00\x01")
    with pytest.raises(ItemError, match="unsupported format"):
        extract(f, FileKind.UNKNOWN_BINARY)


# --- markitdown bridge (mocked) -----------------------------------------------


def _install_fake_markitdown(monkeypatch: pytest.MonkeyPatch, *, text: str | None) -> None:
    module = types.ModuleType("markitdown")

    class _Result:
        text_content = text or ""

    class MarkItDown:
        def convert(self, _path: str) -> _Result:
            if text is None:
                raise ValueError("corrupt")
            return _Result()

    module.MarkItDown = MarkItDown  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "markitdown", module)


def test_document_via_markitdown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_markitdown(monkeypatch, text="# Extracted heading\n\nbody")
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.7")
    result = extract(f, FileKind.PDF)
    assert result.text == "# Extracted heading\n\nbody"


def test_corrupt_document_is_item_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_fake_markitdown(monkeypatch, text=None)  # convert raises
    f = tmp_path / "broken.pdf"
    f.write_bytes(b"%PDF-bad")
    with pytest.raises(ItemError, match="parse error"):
        extract(f, FileKind.PDF)


def test_missing_markitdown_raises_missing_extra(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(sys.modules, "markitdown", None)  # force ImportError
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.7")
    with pytest.raises(MissingExtra) as excinfo:
        extract(f, FileKind.PDF)
    assert excinfo.value.extra == "files"
    assert "sempipe[files]" in excinfo.value.guidance


def test_audio_names_the_audio_extra(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "markitdown", None)
    f = tmp_path / "clip.mp3"
    f.write_bytes(b"ID3")
    with pytest.raises(MissingExtra) as excinfo:
        extract(f, FileKind.AUDIO)
    assert excinfo.value.extra == "audio"


def test_transcribe_audio_runs_the_real_pipeline_on_junk_bytes() -> None:
    # the [audio] extra is installed in dev: junk bytes exercise the real
    # temp-file path end to end — markitdown yields an empty transcript for
    # unparseable audio (a finding, pinned: empty, never a crash)
    from sempipe.models.base import AudioData
    from sempipe.parsing.extract import transcribe_audio

    transcript = transcribe_audio(AudioData(data=b"not really audio", mime="audio/wav"))
    assert isinstance(transcript, str)
