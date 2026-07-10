from __future__ import annotations

import sys
import types
from importlib.util import find_spec
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ItemError
from smartpipe.parsing.detect import FileKind
from smartpipe.parsing.extract import ImageData, MissingExtra, extract

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
    assert "unavailable" in excinfo.value.guidance  # core on <=3.13; wheel-gap wording on 3.14


# --- the environment fence (item 78: magika load_dotenv's the cwd's .env) ------


def test_environ_fence_reverts_additions_and_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from smartpipe.parsing.envfence import environ_fence

    monkeypatch.setenv("SMARTPIPE_FENCE_KEEP", "original")
    monkeypatch.delenv("SMARTPIPE_FENCE_ADDED", raising=False)
    with environ_fence():
        os.environ["SMARTPIPE_FENCE_ADDED"] = "sneaky"
        os.environ["SMARTPIPE_FENCE_KEEP"] = "clobbered"
    assert "SMARTPIPE_FENCE_ADDED" not in os.environ
    assert os.environ["SMARTPIPE_FENCE_KEEP"] == "original"


@pytest.mark.skipif(find_spec("markitdown") is None, reason="no markitdown wheel (3.14)")
def test_real_import_never_ingests_a_dotenv_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """markitdown's import chain (magika) calls load_dotenv(find_dotenv()) —
    a .env in the working tree must never leak into smartpipe's process env
    (env-always-wins is a CONSENTED precedence; a file on disk is not consent).
    Root cause of the item-78 auth-list flake."""
    import contextlib
    import os

    (tmp_path / ".env").write_text("SMARTPIPE_PLANTED_DOTENV_KEY=leaked\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SMARTPIPE_PLANTED_DOTENV_KEY", raising=False)
    # purge the cached modules so the import side effect genuinely re-runs
    for name in [n for n in sys.modules if n == "magika" or n.startswith("magika.")]:
        monkeypatch.delitem(sys.modules, name)
    for name in [n for n in sys.modules if n == "markitdown" or n.startswith("markitdown.")]:
        monkeypatch.delitem(sys.modules, name)
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"%PDF-not-really")
    with contextlib.suppress(ItemError):  # parse outcome is irrelevant; the import matters
        extract(junk, FileKind.PDF)
    assert "SMARTPIPE_PLANTED_DOTENV_KEY" not in os.environ


def test_audio_names_the_audio_extra(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "markitdown", None)
    f = tmp_path / "clip.mp3"
    f.write_bytes(b"ID3")
    with pytest.raises(MissingExtra) as excinfo:
        extract(f, FileKind.AUDIO)
    assert excinfo.value.extra == "audio"


@pytest.mark.skipif(
    find_spec("faster_whisper") is None,
    reason="whisper wheels absent on this python (3.14 until upstream ships)",
)
def test_transcribe_audio_runs_the_real_pipeline_on_junk_bytes() -> None:
    # the [audio] extra is installed in dev: junk bytes exercise the real
    # faster-whisper decode path and fail as a per-item error, never a crash
    from smartpipe.core.errors import ItemError
    from smartpipe.models.base import AudioData
    from smartpipe.parsing.extract import transcribe_audio

    with pytest.raises(ItemError, match="audio couldn't be transcribed"):
        transcribe_audio(AudioData(data=b"not really audio", mime="audio/wav"))


def test_whisper_size_env_override() -> None:
    from smartpipe.parsing.extract import whisper_size

    assert whisper_size({}) == "tiny"
    assert whisper_size({"SMARTPIPE_WHISPER_MODEL": "small"}) == "small"


def test_text_files_normalize_crlf(tmp_path: Path) -> None:
    # a Windows-authored file must yield clean text on every platform
    crlf = tmp_path / "win.txt"
    crlf.write_bytes(b"line one\r\nline two\r")
    extracted = extract(crlf, FileKind.TEXT)
    assert extracted.text == "line one\nline two\n"
