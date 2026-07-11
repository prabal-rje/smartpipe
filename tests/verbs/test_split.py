"""The split verb (D26 layer 3): free, provenance-carrying, exact reassembly."""

from __future__ import annotations

import asyncio
import io
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode
from smartpipe.io.writers import OutputFormat, RenderMode, ResultWriter, WriterConfig, make_writer
from smartpipe.verbs.split import SplitRequest, run_split
from tests.helpers.pdf import minimal_pdf
from tests.io.test_ocr_ingest import FakeParser, RaisingParser

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO


class FakeContext:
    def document_parser(self, flag: str | None = None) -> None:
        return None

    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextIO,
        fields: tuple[str, ...] | None = None,
    ) -> ResultWriter:
        return make_writer(WriterConfig(mode=RenderMode.NDJSON, color=False, width=80), stdout)


class _TtyStdin(io.StringIO):
    def isatty(self) -> bool:
        return True


async def test_small_file_passes_through_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smartpipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    (tmp_path / "note.md").write_text("short and sweet", encoding="utf-8")
    out = io.StringIO()
    code = await run_split(
        SplitRequest(input=InputSpec(patterns=("*.md",), from_files=False)),
        FakeContext(),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.OK
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert records == [
        {
            "text": "short and sweet",
            "__source": {"path": "note.md", "as": "tokens", "segment": 1, "label": "note.md"},
        }
    ]


async def test_big_file_becomes_provenance_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smartpipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    paragraphs = "\n\n".join(f"paragraph {i} " + "x" * 100 for i in range(20))
    (tmp_path / "big.md").write_bytes(
        paragraphs.encode()
    )  # exact bytes - write_text CRLFs on windows
    out = io.StringIO()
    code = await run_split(
        SplitRequest(
            max_tokens_flag=100,
            input=InputSpec(patterns=("*.md",), from_files=False),
        ),
        FakeContext(),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.OK
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(records) > 1
    assert records[0]["__source"]["label"] == f"big.md §1/{len(records)}"
    assert records[0]["__source"]["as"] == "tokens"
    assert records[0]["__source"]["segment"] == 1
    assert "".join(r["text"] for r in records) == paragraphs  # exact reassembly


async def test_stdin_lines_split_too() -> None:
    out = io.StringIO()
    code = await run_split(
        SplitRequest(max_tokens_flag=2),
        FakeContext(),
        stdin=io.StringIO("a" * 40 + "\n"),
        stdout=out,
    )
    assert code is ExitCode.OK
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(records) == 5  # 40 chars / (2 tokens * 4 chars)
    assert all(r["__source"]["label"].startswith("line 1 §") for r in records)


async def test_by_pages_yields_page_spans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from smartpipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    (tmp_path / "r.pdf").write_bytes(minimal_pdf(["alpha page", "beta page", "gamma page"]))
    out = io.StringIO()
    code = await run_split(
        SplitRequest(by_flag="pages:2", input=InputSpec(patterns=("*.pdf",), from_files=False)),
        FakeContext(),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.OK
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["__source"]["label"] for r in records] == ["r.pdf p.1-2", "r.pdf p.3"]
    assert [r["__source"]["page"] for r in records] == [1, 3]
    assert "alpha page" in records[0]["text"] and "beta page" in records[0]["text"]
    assert "gamma page" in records[1]["text"]


async def test_by_seconds_slices_audio_with_clock_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import math
    import struct
    import wave

    from smartpipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    with wave.open(str(tmp_path / "call.wav"), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * t / 8000)))
                for t in range(8000 * 5)  # five seconds
            )
        )
    out = io.StringIO()
    code = await run_split(
        SplitRequest(by_flag="seconds:2", input=InputSpec(patterns=("*.wav",), from_files=False)),
        FakeContext(),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.OK
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["__source"]["label"] for r in records] == [
        "call.wav §00:00-00:02",
        "call.wav §00:02-00:04",
        "call.wav §00:04-00:06",
    ]


async def test_by_pages_on_docx_is_a_loud_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from smartpipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    (tmp_path / "r.docx").write_bytes(b"PK\x03\x04fake")
    out = io.StringIO()
    code = await run_split(
        SplitRequest(by_flag="pages", input=InputSpec(patterns=("*.docx",), from_files=False)),
        FakeContext(),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.ALL_FAILED
    assert "has no fixed pages; use --by tokens" in capsys.readouterr().err


async def test_sliced_audio_round_trips_into_hearable_items() -> None:
    # the pipeline promise: split --by seconds | map can HEAR each slice
    import base64

    from smartpipe.io.items import item_from_line
    from smartpipe.models.base import AudioData

    payload = base64.b64encode(b"RIFFfakewav").decode("ascii")
    line = (
        '{"__media": {"kind": "audio", "mime": "audio/wav", "data_b64": "'
        + payload
        + '"}, "__source": {"path": "call.wav", "as": "seconds", "segment": 1,'
        + ' "label": "call.wav §00:00-00:02"}}\n'
    )
    item = item_from_line(line, 0)
    assert len(item.media) == 1 and isinstance(item.media[0], AudioData)
    assert item.media[0].data == b"RIFFfakewav"
    assert item.media[0].mime == "audio/wav"
    from smartpipe.io.items import describe_source

    assert describe_source(item.source) == "call.wav §00:00-00:02"


def _docx_with_media(path: Path, images: dict[str, bytes]) -> None:
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
        for name, payload in images.items():
            archive.writestr(f"word/media/{name}", payload)


def _pdf_with_jpeg(path: Path, jpeg: bytes) -> None:
    """A hand-rolled one-page PDF with one DCTDecode image XObject."""
    stream = (
        b"<< /Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceRGB"
        b" /BitsPerComponent 8 /Filter /DCTDecode /Length "
        + str(len(jpeg)).encode()
        + b" >>\nstream\n"
        + jpeg
        + b"\nendstream"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        b" /Resources << /XObject << /Im1 4 0 R >> >> >>",
        stream,
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(out))


async def test_media_extracts_docx_images_and_drops_icons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import base64

    from smartpipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    real = b"\x89PNG\r\n\x1a\n" + b"x" * 9_000  # past the floor
    icon = b"\x89PNG\r\n\x1a\n" + b"y" * 100  # decoration
    _docx_with_media(tmp_path / "deck.docx", {"image1.png": real, "image2.png": icon})
    out = io.StringIO()
    code = await run_split(
        SplitRequest(media=True, input=InputSpec(patterns=("*.docx",), from_files=False)),
        FakeContext(),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.OK
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(records) == 1
    assert records[0]["__source"]["label"] == "deck.docx img.1"
    assert records[0]["__source"]["as"] == "file"
    assert records[0]["__media"]["mime"] == "image/png"
    assert records[0]["__media"]["kind"] == "image"
    assert base64.b64decode(records[0]["__media"]["data_b64"]) == real  # byte-identical
    assert "skipped 1 embedded image under 4 KB" in capsys.readouterr().err


async def test_media_extracts_pdf_jpegs_with_page_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64

    from smartpipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    jpeg = b"\xff\xd8\xff\xe0" + b"j" * 8_000 + b"\xff\xd9"  # passthrough, never decoded
    _pdf_with_jpeg(tmp_path / "report.pdf", jpeg)
    out = io.StringIO()
    code = await run_split(
        SplitRequest(media=True, input=InputSpec(patterns=("*.pdf",), from_files=False)),
        FakeContext(),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.OK
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["__source"]["label"] for r in records] == ["report.pdf p.1 img.1"]
    assert base64.b64decode(records[0]["__media"]["data_b64"]) == jpeg


async def test_media_image_items_round_trip_into_vision_items() -> None:
    import base64

    from smartpipe.io.items import item_from_line
    from smartpipe.models.base import ImageData

    line = (
        '{"__media": {"kind": "image", "mime": "image/png", "data_b64": "'
        + base64.b64encode(b"\x89PNGfake").decode("ascii")
        + '"}, "source": "deck.docx img.1"}\n'
    )
    item = item_from_line(line, 0)
    assert len(item.media) == 1 and isinstance(item.media[0], ImageData)
    assert item.media[0].data == b"\x89PNGfake"


async def test_pages_media_fuses_text_and_figures_per_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64

    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import item_from_line
    from smartpipe.models.base import ImageData

    monkeypatch.chdir(tmp_path)
    jpeg = b"\xff\xd8\xff\xe0" + b"j" * 8_000 + b"\xff\xd9"
    _pdf_with_jpeg(tmp_path / "r.pdf", jpeg)  # one page, one figure, page text absent
    out = io.StringIO()
    code = await run_split(
        SplitRequest(
            by_flag="pages", media=True, input=InputSpec(patterns=("*.pdf",), from_files=False)
        ),
        FakeContext(),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.OK
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["__source"]["label"] == "r.pdf"
    assert len(record["__media"]) == 1
    assert base64.b64decode(record["__media"][0]["data_b64"]) == jpeg
    # and the record round-trips into ONE multimodal item downstream
    item = item_from_line(json.dumps(record) + "\n", 0)
    assert len(item.media) == 1 and isinstance(item.media[0], ImageData)
    assert item.media[0].data == jpeg


async def test_doc_items_carry_figures_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.readers import resolve_items
    from smartpipe.models.base import ImageData

    monkeypatch.chdir(tmp_path)
    real = b"\x89PNG\r\n\x1a\n" + b"x" * 9_000
    _docx_with_media(tmp_path / "deck.docx", {"image1.png": real})
    from smartpipe.io import readers
    from smartpipe.parsing.extract import Extracted

    def fake_extract(path: object, kind: object) -> Extracted:
        # 64+ chars: a REAL text layer — the plain figure note, not the scan route
        return Extracted(text="the deck text " * 8)

    monkeypatch.setattr(readers, "extract", fake_extract)
    items_iter, _total = resolve_items(
        InputSpec(patterns=("*.docx",), from_files=False), _TtyStdin()
    )
    items = [item async for item in items_iter]
    assert len(items) == 1
    figures = [part for part in items[0].media if isinstance(part, ImageData)]
    assert len(figures) == 1 and figures[0].data == real  # D32: attached by default
    assert "deck.docx: 1 figure attached" in capsys.readouterr().err


# --- the ocr-model role (item 48): token path, --by pages, and the fallback -----------


class _OcrContext(FakeContext):
    def __init__(self, parser: FakeParser) -> None:
        self.parser = parser

    def document_parser(self, flag: str | None = None) -> FakeParser:  # type: ignore[override]
        return self.parser


async def test_ocr_role_parses_scans_on_the_token_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from smartpipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    (tmp_path / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    parser = FakeParser(image_text="SCANNED MD")
    out = io.StringIO()
    code = await run_split(
        SplitRequest(input=InputSpec(patterns=("*.png",), from_files=False)),
        _OcrContext(parser),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.OK
    assert len(parser.image_calls) == 1
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert records[0]["text"] == "SCANNED MD"
    assert "parsed by mistral/mistral-ocr-latest" in capsys.readouterr().err


async def test_by_pages_parses_through_the_role_and_keeps_the_grouping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from smartpipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    (tmp_path / "r.pdf").write_bytes(b"%PDF-1.4 tiny")  # the parser reads it, not pypdf
    parser = FakeParser(pages=3)
    out = io.StringIO()
    code = await run_split(
        SplitRequest(by_flag="pages:2", input=InputSpec(patterns=("*.pdf",), from_files=False)),
        _OcrContext(parser),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.OK
    assert len(parser.pdf_calls) == 1
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["__source"]["label"] for r in records] == ["r.pdf p.1-2", "r.pdf p.3"]
    assert [r["__source"]["page"] for r in records] == [1, 3]
    assert records[0]["text"] == "page 1 md\n\npage 2 md"  # cut exactly like local pages
    assert records[1]["text"] == "page 3 md"
    err = capsys.readouterr().err
    assert "degraded: r.pdf p.1 document → markdown" in err  # disclosed per page


async def test_media_branch_never_constructs_the_ocr_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smartpipe.io.inputs import InputSpec

    class GuardContext(FakeContext):
        def document_parser(self, flag: str | None = None) -> None:
            raise AssertionError("split --media never consults ocr-model")

    monkeypatch.chdir(tmp_path)
    _docx_with_media(tmp_path / "deck.docx", {})
    code = await run_split(
        SplitRequest(media=True, input=InputSpec(patterns=("*.docx",), from_files=False)),
        GuardContext(),
        stdin=_TtyStdin(),
        stdout=io.StringIO(),
    )

    assert code is ExitCode.OK


async def test_pre_stopped_pages_run_never_constructs_or_calls_the_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smartpipe.io.inputs import InputSpec

    class GuardContext(FakeContext):
        def document_parser(self, flag: str | None = None) -> None:
            raise AssertionError("stopped intake must not set up OCR")

    monkeypatch.chdir(tmp_path)
    (tmp_path / "r.pdf").write_bytes(minimal_pdf(["one"]))
    stop = asyncio.Event()
    stop.set()
    await run_split(
        SplitRequest(by_flag="pages", input=InputSpec(patterns=("*.pdf",), from_files=False)),
        GuardContext(),
        stdin=_TtyStdin(),
        stdout=io.StringIO(),
        stop=stop,
    )


async def test_by_pages_falls_back_to_local_extraction_when_the_parse_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from smartpipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    (tmp_path / "r.pdf").write_bytes(minimal_pdf(["alpha page", "beta page"]))
    out = io.StringIO()
    code = await run_split(
        SplitRequest(by_flag="pages", input=InputSpec(patterns=("*.pdf",), from_files=False)),
        _OcrContext(FakeParser(fail=True)),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.OK
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert "alpha page" in records[0]["text"]  # the local ladder took over
    err = capsys.readouterr().err
    assert "ocr failed: r.pdf" in err and "falling back" in err


async def test_by_pages_rate_limit_degrades_with_the_honest_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A5.1: an isolated exhausted 429 ladder still falls back to local page text,
    but the note is the honest 'rate-limited', not 'ocr failed', not the wire body."""
    from smartpipe.core.errors import RetryableError
    from smartpipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    (tmp_path / "r.pdf").write_bytes(minimal_pdf(["alpha page", "beta page"]))
    out = io.StringIO()
    code = await run_split(
        SplitRequest(by_flag="pages", input=InputSpec(patterns=("*.pdf",), from_files=False)),
        _OcrContext(RaisingParser(RetryableError("429 Too Many Requests"))),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.OK
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert "alpha page" in records[0]["text"]  # the local ladder took over
    err = capsys.readouterr().err
    assert "ocr rate-limited: r.pdf — falling back to local extraction" in err
    assert "ocr failed" not in err and "429" not in err


async def test_by_pages_systemic_breaker_is_not_masqueraded_as_a_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A5.1: when the run-scoped breaker concludes the OCR wire is down, the fault
    is NOT relabeled as a per-file fallback: no 'falling back' line, no degraded
    local garbage, and the run stops (every source failed, exit 3)."""
    from smartpipe.core.errors import CircuitOpenTransport
    from smartpipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    (tmp_path / "r.pdf").write_bytes(minimal_pdf(["alpha page", "beta page"]))
    out = io.StringIO()
    code = await run_split(
        SplitRequest(by_flag="pages", input=InputSpec(patterns=("*.pdf",), from_files=False)),
        _OcrContext(RaisingParser(CircuitOpenTransport("ocr wire down", trip_id=1))),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.ALL_FAILED  # the run stops with the truth
    assert out.getvalue() == ""  # it did NOT degrade to local garbage
    err = capsys.readouterr().err
    assert "falling back" not in err  # never masquerades as a per-file fallback
    assert "skipped: r.pdf" in err  # the file is dropped and counted, not degraded
