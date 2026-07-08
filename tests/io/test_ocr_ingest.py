"""The ocr-model role at ingestion (item 40): routing, spine, and fallback."""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ItemError
from smartpipe.io import diagnostics
from smartpipe.io.inputs import InputSpec
from smartpipe.io.items import Item, source_record
from smartpipe.io.readers import OcrIngest, ocr_route, resolve_items
from smartpipe.models.base import ImageData, ModelRef
from smartpipe.models.ocr import OcrPage
from smartpipe.parsing.detect import FileKind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class FakeParser:
    ref = ModelRef("mistral", "mistral-ocr-latest")

    def __init__(self, *, image_text: str = "IMAGE MD", pages: int = 2, fail: bool = False) -> None:
        self.image_text = image_text
        self.pages = pages
        self.fail = fail
        self.image_calls: list[ImageData] = []
        self.pdf_calls: list[Path] = []

    async def parse_image(self, image: ImageData) -> str:
        if self.fail:
            raise ItemError("wire down")
        self.image_calls.append(image)
        return self.image_text

    async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
        if self.fail:
            raise ItemError("wire down")
        self.pdf_calls.append(path)
        return tuple(OcrPage(index, f"page {index + 1} md") for index in range(self.pages))


class _TtyStdin(io.StringIO):
    def isatty(self) -> bool:
        return True


async def _drain(items: AsyncIterator[Item]) -> list[Item]:
    return [item async for item in items]


def _ingest(parser: FakeParser) -> OcrIngest:
    return OcrIngest(parser=parser, log=diagnostics.DegradationLog())


def test_ocr_route_is_pdf_and_image_crates_only() -> None:
    assert ocr_route(FileKind.PDF, None) == "pdf"
    assert ocr_route(FileKind.IMAGE, None) == "image"
    assert ocr_route(FileKind.PDF, "file") == "pdf"
    assert ocr_route(FileKind.PDF, "lines") is None  # text cuts refuse docs earlier
    assert ocr_route(FileKind.PDF, "jsonl") is None
    assert ocr_route(FileKind.DOCX, None) is None  # markitdown keeps every other kind
    assert ocr_route(FileKind.TEXT, None) is None
    assert ocr_route(FileKind.AUDIO, None) is None


async def test_image_file_becomes_markdown_text(tmp_path: Path) -> None:
    picture = tmp_path / "page.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    parser = FakeParser(image_text="# Scanned page")
    items, total = resolve_items(
        InputSpec(patterns=(str(picture),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(parser),
    )
    assert total is None  # page counts are unknown before parsing
    loaded = await _drain(items)
    assert len(loaded) == 1
    assert loaded[0].text == "# Scanned page"
    assert loaded[0].media == ()  # the parse consumed the pixels
    assert parser.image_calls and parser.image_calls[0].mime == "image/png"


async def test_pdf_cuts_one_item_per_page_in_the_spine(tmp_path: Path) -> None:
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 tiny")
    items, _total = resolve_items(
        InputSpec(patterns=(str(pdf),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(FakeParser(pages=2)),
    )
    loaded = await _drain(items)
    assert [item.text for item in loaded] == ["page 1 md", "page 2 md"]
    assert source_record(loaded[0].source) == {
        "path": str(pdf),
        "as": "pages",
        "page": 1,
        "label": "report.pdf p.1",
    }
    assert source_record(loaded[1].source)["page"] == 2  # mirrors split --by pages


async def test_single_page_pdf_keeps_the_plain_name(tmp_path: Path) -> None:
    pdf = tmp_path / "one.pdf"
    pdf.write_bytes(b"%PDF-1.4 tiny")
    items, _total = resolve_items(
        InputSpec(patterns=(str(pdf),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(FakeParser(pages=1)),
    )
    loaded = await _drain(items)
    assert source_record(loaded[0].source)["label"] == "one.pdf"


async def test_ocr_failure_falls_back_to_the_ladder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    picture = tmp_path / "photo.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    items, _total = resolve_items(
        InputSpec(patterns=(str(picture),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(FakeParser(fail=True)),
    )
    loaded = await _drain(items)
    assert len(loaded) == 1
    assert loaded[0].media  # today's path: the image rides as media
    err = capsys.readouterr().err
    assert "ocr failed: photo.png" in err
    assert "falling back" in err


async def test_each_parsed_row_is_disclosed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 tiny")
    items, _total = resolve_items(
        InputSpec(patterns=(str(pdf),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(FakeParser(pages=2)),
    )
    await _drain(items)
    err = capsys.readouterr().err
    voice = "degraded: scan.pdf p.1 document → markdown (parsed by mistral/mistral-ocr-latest)"
    assert voice in err
    assert "scan.pdf p.2" in err


async def test_text_only_corpus_keeps_its_known_total(tmp_path: Path) -> None:
    """A configured role must not degrade text ingestion: no eligible file,
    no OCR path — the finite total (and embed's batching) stays."""
    notes = tmp_path / "notes.txt"
    notes.write_text("hello\n", encoding="utf-8")
    parser = FakeParser()
    items, total = resolve_items(
        InputSpec(patterns=(str(notes),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(parser),
    )
    assert total == 1
    loaded = await _drain(items)
    assert loaded[0].text == "hello\n"  # whole-file crates keep their bytes
    assert parser.image_calls == [] and parser.pdf_calls == []


async def test_mixed_corpus_ocrs_only_the_eligible_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("plain\n", encoding="utf-8")
    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    parser = FakeParser(image_text="OCR MD")
    items, total = resolve_items(
        InputSpec(patterns=(str(tmp_path / "*"),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(parser),
    )
    assert total is None
    loaded = await _drain(items)
    assert [item.text for item in loaded] == ["plain\n", "OCR MD"]


async def test_from_files_routes_named_files_through_the_role(tmp_path: Path) -> None:
    picture = tmp_path / "slide.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    items, _total = resolve_items(
        InputSpec(patterns=(), from_files=True),
        io.StringIO(f"{picture}\n"),
        ocr=_ingest(FakeParser(image_text="SLIDE MD")),
    )
    loaded = await _drain(items)
    assert [item.text for item in loaded] == ["SLIDE MD"]
