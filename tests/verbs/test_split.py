"""The split verb (D26 layer 3): free, provenance-carrying, exact reassembly."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from sempipe.core.errors import ExitCode
from sempipe.io.writers import OutputFormat, RenderMode, ResultWriter, WriterConfig, make_writer
from sempipe.verbs.split import SplitRequest, run_split

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO


class FakeContext:
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
    from sempipe.io.inputs import InputSpec

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
    assert records == [{"text": "short and sweet", "source": "note.md"}]


async def test_big_file_becomes_provenance_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sempipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    paragraphs = "\n\n".join(f"paragraph {i} " + "x" * 100 for i in range(20))
    (tmp_path / "big.md").write_text(paragraphs, encoding="utf-8")
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
    assert records[0]["source"] == f"big.md §1/{len(records)}"
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
    assert all(r["source"].startswith("line 1 §") for r in records)


def _minimal_pdf(pages: list[str]) -> bytes:
    """A hand-rolled N-page PDF with one text line per page (no writer dep)."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for i, text in enumerate(pages):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {4 + i * 2} 0 R /Resources << /Font << /F1 "
            f"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>".encode()
        )
        objects.append(
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
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
    return bytes(out)


async def test_by_pages_yields_page_spans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sempipe.io.inputs import InputSpec

    monkeypatch.chdir(tmp_path)
    (tmp_path / "r.pdf").write_bytes(_minimal_pdf(["alpha page", "beta page", "gamma page"]))
    out = io.StringIO()
    code = await run_split(
        SplitRequest(by_flag="pages:2", input=InputSpec(patterns=("*.pdf",), from_files=False)),
        FakeContext(),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.OK
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["source"] for r in records] == ["r.pdf p.1-2", "r.pdf p.3"]
    assert "alpha page" in records[0]["text"] and "beta page" in records[0]["text"]
    assert "gamma page" in records[1]["text"]


async def test_by_seconds_slices_audio_with_clock_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import math
    import struct
    import wave

    from sempipe.io.inputs import InputSpec

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
    assert [r["source"] for r in records] == [
        "call.wav §00:00-00:02",
        "call.wav §00:02-00:04",
        "call.wav §00:04-00:06",
    ]


async def test_by_pages_on_docx_is_a_loud_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from sempipe.io.inputs import InputSpec

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

    from sempipe.io.items import item_from_line
    from sempipe.models.base import AudioData

    payload = base64.b64encode(b"RIFFfakewav").decode("ascii")
    line = (
        '{"audio_b64": "' + payload + '", "mime": "audio/wav", "source": "call.wav §00:00-00:02"}\n'
    )
    item = item_from_line(line, 0)
    assert isinstance(item.media, AudioData)
    assert item.media.data == b"RIFFfakewav"
    assert item.media.mime == "audio/wav"
    from sempipe.io.items import describe_source

    assert describe_source(item.source) == "call.wav §00:00-00:02"
