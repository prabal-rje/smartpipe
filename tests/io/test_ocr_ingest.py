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
from smartpipe.models.base import CompletionRequest, ImageData, ModelRef
from smartpipe.models.ocr import OcrBilling, OcrPage
from smartpipe.parsing.detect import FileKind
from tests.helpers.pdf import minimal_pdf

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class FakeParser:
    ref = ModelRef("mistral", "mistral-ocr-latest")
    billing = OcrBilling.PAGE

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


@pytest.mark.parametrize(
    "module",
    (
        "src/smartpipe/cli/read_cmd.py",
        "src/smartpipe/verbs/map.py",
        "src/smartpipe/verbs/extend.py",
        "src/smartpipe/verbs/filter.py",
        "src/smartpipe/verbs/embed.py",
        "src/smartpipe/verbs/top_k.py",
        "src/smartpipe/verbs/reduce.py",
        "src/smartpipe/verbs/join.py",
        "src/smartpipe/verbs/cluster.py",
        "src/smartpipe/verbs/diff.py",
        "src/smartpipe/verbs/distinct.py",
        "src/smartpipe/verbs/outliers.py",
        "src/smartpipe/verbs/split.py",
        "src/smartpipe/verbs/graphfull.py",
    ),
)
def test_every_ocr_callsite_resolves_the_parser_inside_a_lazy_factory(module: str) -> None:
    """A new verb cannot reintroduce eager key/client setup on text-only input."""
    import ast

    tree = ast.parse(Path(module).read_text(encoding="utf-8"), filename=module)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "document_parser"
    ]
    lazy_calls = {
        id(node)
        for expression in ast.walk(tree)
        if isinstance(expression, ast.Lambda)
        for node in ast.walk(expression)
        if isinstance(node, ast.Call)
    }
    assert calls, f"{module} is registered but no longer has an OCR setup call"
    assert all(id(call) in lazy_calls for call in calls)


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


async def test_empty_ocr_result_falls_back_instead_of_swallowing_the_item(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    picture = tmp_path / "blank.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    items, _total = resolve_items(
        InputSpec(patterns=(str(picture),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(FakeParser(image_text="  ")),
    )

    loaded = await _drain(items)

    assert len(loaded) == 1 and loaded[0].media
    assert "OCR model returned no text" in capsys.readouterr().err


async def test_all_blank_ocr_pdf_pages_fall_back_to_local_extraction(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smartpipe.io import readers
    from smartpipe.parsing.extract import Extracted

    class BlankParser(FakeParser):
        async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
            self.pdf_calls.append(path)
            return (OcrPage(0, "  "), OcrPage(1, "\n"))

    pdf = tmp_path / "blank.pdf"
    pdf.write_bytes(minimal_pdf(["local layer"]))

    def local_extract(_path: Path, _kind: FileKind) -> Extracted:
        return Extracted("LOCAL TEXT")

    def no_figures(_path: Path, _kind: FileKind, _text: str) -> tuple[ImageData, ...]:
        return ()

    monkeypatch.setattr(readers, "extract", local_extract)
    monkeypatch.setattr(readers, "_document_figures", no_figures)

    items, _total = resolve_items(
        InputSpec(patterns=(str(pdf),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(BlankParser()),
    )
    loaded = await _drain(items)

    assert [item.text for item in loaded] == ["LOCAL TEXT"]
    assert "OCR model returned no text" in capsys.readouterr().err


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


async def test_lazy_parser_is_not_constructed_for_text_only_input(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("hello\n", encoding="utf-8")
    parser = FakeParser()
    resolutions = 0

    def resolve() -> FakeParser:
        nonlocal resolutions
        resolutions += 1
        return parser

    ocr = OcrIngest.lazy(resolve, diagnostics.DegradationLog())
    items, total = resolve_items(
        InputSpec(patterns=(str(notes),), from_files=False), _TtyStdin(), ocr=ocr
    )
    loaded = await _drain(items)

    assert total == 1
    assert loaded[0].text == "hello\n"
    assert resolutions == 0


async def test_lazy_parser_constructs_once_when_an_image_arrives(tmp_path: Path) -> None:
    picture = tmp_path / "page.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    parser = FakeParser()
    resolutions = 0

    def resolve() -> FakeParser:
        nonlocal resolutions
        resolutions += 1
        return parser

    items, _total = resolve_items(
        InputSpec(patterns=(str(picture),), from_files=False),
        _TtyStdin(),
        ocr=OcrIngest.lazy(resolve, diagnostics.DegradationLog()),
    )
    await _drain(items)

    assert resolutions == 1


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


# --- the stdin-redirected pump routes (item 49c) -------------------------------------


async def test_stdin_redirected_pdf_parses_through_the_pump(tmp_path: Path) -> None:
    """``smartpipe embed < scan.pdf``: the daemon pump thread sniffs the PDF,
    spools it, and the spool parses through the role — one item per page."""
    from tests.io.test_binary_stdin import BytePipe, pathlib_read

    pipe = BytePipe()
    try:
        pipe.write(pathlib_read("tests/corpus/one-page.pdf"))
        pipe.close_write()
        items, total = resolve_items(
            InputSpec(patterns=(), from_files=False),
            pipe.reader,
            ocr=_ingest(FakeParser(pages=2)),
        )
        assert total is None
        loaded = await _drain(items)
    finally:
        pipe.close()
    assert [item.text for item in loaded] == ["page 1 md", "page 2 md"]
    assert source_record(loaded[0].source) == {
        "path": "<stdin>",
        "as": "pages",
        "page": 1,
        "label": "<stdin> p.1",
    }


async def test_stdin_redirected_pdf_pump_falls_back_when_the_parse_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed stdin parse keeps the spool and hands it to the local ladder."""
    import sys
    import types

    from tests.io.test_binary_stdin import BytePipe, pathlib_read

    module = types.ModuleType("markitdown")

    class _Result:
        text_content = "LOCAL TEXT"

    class MarkItDown:
        def convert(self, path: str) -> _Result:
            return _Result()

    module.MarkItDown = MarkItDown  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "markitdown", module)
    pipe = BytePipe()
    try:
        pipe.write(pathlib_read("tests/corpus/one-page.pdf"))
        pipe.close_write()
        items, _total = resolve_items(
            InputSpec(patterns=(), from_files=False),
            pipe.reader,
            ocr=_ingest(FakeParser(fail=True)),
        )
        loaded = await _drain(items)
    finally:
        pipe.close()
    assert [item.text for item in loaded] == ["LOCAL TEXT"]
    err = capsys.readouterr().err
    assert "ocr failed: <stdin>" in err and "falling back" in err


async def test_stdin_redirected_image_parses_through_the_pump() -> None:
    from tests.io.test_binary_stdin import BytePipe

    pipe = BytePipe()
    try:
        pipe.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        pipe.close_write()
        items, _total = resolve_items(
            InputSpec(patterns=(), from_files=False),
            pipe.reader,
            ocr=_ingest(FakeParser(image_text="PUMPED MD")),
        )
        loaded = await _drain(items)
    finally:
        pipe.close()
    assert len(loaded) == 1
    assert loaded[0].text == "PUMPED MD"
    assert loaded[0].media == ()  # the parse consumed the pixels


async def test_stdin_as_file_still_routes_an_image_through_ocr() -> None:
    """--as file changes granularity; it must not bypass the configured role."""
    from tests.io.test_binary_stdin import BytePipe

    parser = FakeParser(image_text="AS FILE MD")
    pipe = BytePipe()
    try:
        pipe.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        pipe.close_write()
        items, _total = resolve_items(
            InputSpec(patterns=(), from_files=False, as_mode="file"),
            pipe.reader,
            ocr=_ingest(parser),
        )
        loaded = await _drain(items)
    finally:
        pipe.close()

    assert [item.text for item in loaded] == ["AS FILE MD"]
    assert len(parser.image_calls) == 1


async def test_failed_image_ocr_is_not_retried_by_the_converter(tmp_path: Path) -> None:
    """Ingestion and conversion share one OCR-attempt owner for the same pixels."""
    from smartpipe.verbs.convert import make_converter

    class FailingParser(FakeParser):
        async def parse_image(self, image: ImageData) -> str:
            self.image_calls.append(image)
            raise ItemError("wire down")

    class CaptionChat:
        ref = ModelRef("ollama", "llava")

        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, request: CompletionRequest) -> str:
            self.calls += 1
            return "local caption"

    picture = tmp_path / "page.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
    parser = FailingParser()
    ocr = _ingest(parser)
    items, _total = resolve_items(
        InputSpec(patterns=(str(picture),), from_files=False), _TtyStdin(), ocr=ocr
    )
    loaded = await _drain(items)
    image = loaded[0].media[0]
    assert isinstance(image, ImageData)
    chat = CaptionChat()
    converter = make_converter(chat, allow_paid=False, log=diagnostics.DegradationLog(), ocr=ocr)

    assert await converter.image_to_text(image, str(picture)) == "local caption"
    assert len(parser.image_calls) == 1
    assert chat.calls == 1


async def test_identical_files_each_get_their_own_ingestion_attempt(tmp_path: Path) -> None:
    class FailingParser(FakeParser):
        async def parse_image(self, image: ImageData) -> str:
            self.image_calls.append(image)
            raise ItemError("wire down")

    payload = b"\x89PNG\r\n\x1a\n" + b"same" * 8
    (tmp_path / "a.png").write_bytes(payload)
    (tmp_path / "b.png").write_bytes(payload)
    parser = FailingParser()
    items, _total = resolve_items(
        InputSpec(patterns=(str(tmp_path / "*.png"),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(parser),
    )

    loaded = await _drain(items)

    assert len(loaded) == 2
    assert len(parser.image_calls) == 2


# --- the >20-pages preflight note lives in the shared machinery (item 48) -------------


async def test_preflight_note_fires_for_any_verbs_path_ingestion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for index in range(21):
        (tmp_path / f"s{index:02}.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    items, _total = resolve_items(
        InputSpec(patterns=(str(tmp_path / "*.png"),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(FakeParser()),
    )
    err = capsys.readouterr().err  # the note fires at resolve time, before any parse
    assert (
        "~21 billable pages will parse through mistral/mistral-ocr-latest - --max-calls caps them"
    ) in err
    await _drain(items)


async def test_preflight_stays_quiet_at_twenty_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for index in range(20):
        (tmp_path / f"s{index:02}.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    resolve_items(
        InputSpec(patterns=(str(tmp_path / "*.png"),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(FakeParser()),
    )
    assert "will parse through" not in capsys.readouterr().err


async def test_preflight_counts_pdf_pages_not_request_envelopes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(minimal_pdf([f"page {index}" for index in range(21)]))

    items, _total = resolve_items(
        InputSpec(patterns=(str(pdf),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(FakeParser(pages=21)),
    )
    err = capsys.readouterr().err
    assert "~21 billable pages will parse through mistral/mistral-ocr-latest" in err
    await _drain(items)


async def test_request_billed_vision_ocr_never_claims_billable_pages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class VisionParser(FakeParser):
        ref = ModelRef("ollama", "llava")
        billing = OcrBilling.MODEL_CALL

    for index in range(21):
        (tmp_path / f"v{index:02}.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    parser = VisionParser()
    items, _total = resolve_items(
        InputSpec(patterns=(str(tmp_path / "*.png"),), from_files=False),
        _TtyStdin(),
        ocr=_ingest(parser),
    )

    assert "billable pages" not in capsys.readouterr().err
    await _drain(items)
    assert len(parser.image_calls) == 21


# --- the finite-corpus gate for embed's two-pass batching (item 49b) ------------------


def test_ocr_finite_paths_is_true_for_a_files_only_ocr_corpus(tmp_path: Path) -> None:
    from smartpipe.io.readers import ocr_finite_paths

    (tmp_path / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (tmp_path / "notes.txt").write_text("plain\n", encoding="utf-8")
    spec = InputSpec(patterns=(str(tmp_path / "*"),), from_files=False)
    assert ocr_finite_paths(spec, _TtyStdin()) is True


def test_ocr_finite_paths_is_false_for_streams_csv_and_text_corpora(tmp_path: Path) -> None:
    from smartpipe.io.readers import ocr_finite_paths

    (tmp_path / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (tmp_path / "rows.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("plain\n", encoding="utf-8")
    scan = str(tmp_path / "scan.png")
    # a chained pipe keeps streaming semantics — no two-pass collection
    assert ocr_finite_paths(InputSpec(patterns=(scan,), from_files=False), io.StringIO("")) is False
    # --from-files streams names — finiteness is unknowable up front
    assert ocr_finite_paths(InputSpec(patterns=(), from_files=True), _TtyStdin()) is False
    # a csv in the mix streams row-at-a-time (item 54) — never materialized
    spec = InputSpec(patterns=(scan, str(tmp_path / "rows.csv")), from_files=False)
    assert ocr_finite_paths(spec, _TtyStdin()) is False
    # no OCR-eligible file: the ordinary finite path already batches
    text_only = InputSpec(patterns=(str(tmp_path / "notes.txt"),), from_files=False)
    assert ocr_finite_paths(text_only, _TtyStdin()) is False
