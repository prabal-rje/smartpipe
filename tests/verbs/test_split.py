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
