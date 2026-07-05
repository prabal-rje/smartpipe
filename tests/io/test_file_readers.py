from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest

from sempipe.core.errors import UsageFault
from sempipe.io.inputs import InputSpec
from sempipe.io.readers import resolve_items

if TYPE_CHECKING:
    from pathlib import Path


async def _collect(spec: InputSpec, stdin: str = "") -> list[str]:
    items, _total = resolve_items(spec, io.StringIO(stdin))
    return [item.text async for item in items]


async def test_glob_reads_each_file_sorted(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("beta")
    (tmp_path / "a.txt").write_text("alpha")
    spec = InputSpec(patterns=(str(tmp_path / "*.txt"),), from_files=False)
    assert await _collect(spec) == ["alpha", "beta"]


async def test_file_item_source_is_the_path(tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("hello")
    spec = InputSpec(patterns=(str(f),), from_files=False)
    items_iter, _total = resolve_items(spec, io.StringIO(""))
    items = [item async for item in items_iter]
    assert items[0].source.kind == "file"
    assert items[0].source.name == str(f)


async def test_empty_glob_is_usage_fault(tmp_path: Path) -> None:
    spec = InputSpec(patterns=(str(tmp_path / "*.pdf"),), from_files=False)
    with pytest.raises(UsageFault, match="no files matched"):
        await _collect(spec)


async def test_from_files_reads_named_files(tmp_path: Path) -> None:
    f = tmp_path / "note.txt"
    f.write_text("content")
    spec = InputSpec(patterns=(), from_files=True)
    assert await _collect(spec, stdin=f"{f}\n") == ["content"]


async def test_from_files_skips_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "gone.txt"
    spec = InputSpec(patterns=(), from_files=True)
    assert await _collect(spec, stdin=f"{missing}\n") == []
    assert "cannot read" in capsys.readouterr().err


async def test_unknown_binary_file_is_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = tmp_path / "a.txt"
    good.write_text("readable")
    blob = tmp_path / "b.bin"
    blob.write_bytes(b"\x00\x01\x02\xff\x87")
    spec = InputSpec(patterns=(str(tmp_path / "*"),), from_files=False)
    assert await _collect(spec) == ["readable"]
    assert "unsupported format" in capsys.readouterr().err


async def test_default_is_stdin_lines() -> None:
    spec = InputSpec(patterns=(), from_files=False)
    assert await _collect(spec, stdin="one\ntwo\n") == ["one", "two"]


async def test_image_file_becomes_an_image_item(tmp_path: Path) -> None:
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    good = tmp_path / "note.txt"
    good.write_text("text")
    spec = InputSpec(patterns=(str(tmp_path / "*"),), from_files=False)
    items_iter, total = resolve_items(spec, io.StringIO(""))
    items = [item async for item in items_iter]
    assert total == 2
    by_name = {item.source.name.rsplit("/", 1)[-1]: item for item in items}
    photo = by_name["photo.png"]
    assert photo.image is not None  # bytes carried to the vision path
    assert photo.image.mime == "image/png"
    assert photo.text == ""  # nothing to "read" — the model sees the image
    assert by_name["note.txt"].image is None


async def test_missing_extra_warns_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "markitdown", None)  # force ImportError in the bridge
    for name in ("a.pdf", "b.pdf"):
        (tmp_path / name).write_bytes(b"%PDF-1.7")
    spec = InputSpec(patterns=(str(tmp_path / "*.pdf"),), from_files=False)
    assert await _collect(spec) == []  # both skipped
    err = capsys.readouterr().err
    assert err.count("sempipe[files]") == 1  # guidance shown once, not per file


async def test_corrupt_document_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys
    import types

    module = types.ModuleType("markitdown")

    class MarkItDown:
        def convert(self, _path: str) -> object:
            raise ValueError("corrupt")

    module.MarkItDown = MarkItDown  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "markitdown", module)
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-bad")
    spec = InputSpec(patterns=(str(tmp_path / "*.pdf"),), from_files=False)
    assert await _collect(spec) == []
    assert "parse error" in capsys.readouterr().err


async def test_utf8_replacement_warning_on_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "messy.txt"  # .txt → text by extension, invalid bytes replaced
    f.write_bytes(b"ok \xff\xfe done")
    spec = InputSpec(patterns=(str(f),), from_files=False)
    items_iter, _total = resolve_items(spec, io.StringIO(""))
    items = [item async for item in items_iter]
    assert len(items) == 1
    assert "some bytes were replaced" in capsys.readouterr().err
