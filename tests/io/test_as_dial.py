"""The --as ingestion dial (wave 2, item 15): file | lines | jsonl."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.io.inputs import InputSpec
from smartpipe.io.readers import resolve_items

if TYPE_CHECKING:
    from pathlib import Path

    from smartpipe.io.items import Item


async def _drain(spec: InputSpec, stdin_text: str = "") -> list[Item]:
    items, _total = resolve_items(spec, io.StringIO(stdin_text))
    return [item async for item in items]


def _spec(*patterns: str, as_mode: str | None = None) -> InputSpec:
    return InputSpec(patterns=patterns, from_files=False, as_mode=as_mode)


# --- stdin ----------------------------------------------------------------------


async def test_stdin_as_lines_keeps_json_looking_lines_as_text() -> None:
    items = await _drain(_spec(as_mode="lines"), '{"a": 1}\nplain\n')
    assert [item.data for item in items] == [None, None]  # never sniffed
    assert items[0].raw == '{"a": 1}'
    assert [item.source.cut for item in items] == ["lines", "lines"]


async def test_stdin_as_jsonl_is_strict_and_names_the_line() -> None:
    with pytest.raises(UsageFault, match="stdin line 2 isn't a JSON object"):
        await _drain(_spec(as_mode="jsonl"), '{"a": 1}\nnot json\n')


async def test_stdin_as_file_slurps_the_whole_pipe_into_one_item() -> None:
    items = await _drain(_spec(as_mode="file"), "first\nsecond\nthird\n")
    assert len(items) == 1
    assert items[0].text == "first\nsecond\nthird"
    assert items[0].source.cut == "file"


async def test_stdin_auto_still_sniffs_per_line() -> None:
    items = await _drain(_spec(), '{"a": 1}\nplain\n')
    assert items[0].data == {"a": 1}
    assert items[1].data is None


# --- named paths ------------------------------------------------------------------


async def test_jsonl_extension_defaults_to_records(tmp_path: Path) -> None:
    data = tmp_path / "rows.jsonl"
    data.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
    items = await _drain(_spec(str(data)))
    assert [item.data for item in items] == [{"a": 1}, {"a": 2}]
    assert items[0].source.cut == "jsonl"
    assert items[0].source.path == str(data)


async def test_jsonl_extension_bad_row_is_loud_and_names_the_file(tmp_path: Path) -> None:
    data = tmp_path / "rows.jsonl"
    data.write_text('{"a": 1}\noops\n', encoding="utf-8")
    with pytest.raises(UsageFault, match=r"line 2 isn't a JSON object"):
        await _drain(_spec(str(data)))


async def test_text_file_defaults_to_one_crate(tmp_path: Path) -> None:
    doc = tmp_path / "notes.txt"
    doc.write_text("one\ntwo\n", encoding="utf-8")
    items = await _drain(_spec(str(doc)))
    assert len(items) == 1
    assert items[0].source.cut == "file"


async def test_as_lines_cuts_a_text_file_into_rows(tmp_path: Path) -> None:
    doc = tmp_path / "notes.txt"
    doc.write_text('one\n{"x": 2}\n', encoding="utf-8")
    items = await _drain(_spec(str(doc), as_mode="lines"))
    assert [item.raw for item in items] == ["one", '{"x": 2}']
    assert all(item.data is None for item in items)
    assert items[1].source.path == str(doc)


async def test_as_lines_refuses_media_files(tmp_path: Path) -> None:
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    with pytest.raises(UsageFault, match=r"images .* have no finer granularity"):
        await _drain(_spec(str(photo), as_mode="lines"))


async def test_as_jsonl_refuses_documents_and_points_at_split(tmp_path: Path) -> None:
    doc = tmp_path / "report.pdf"
    doc.write_bytes(b"%PDF-1.4 fake body")
    with pytest.raises(UsageFault, match=r"split --by pages"):
        await _drain(_spec(str(doc), as_mode="jsonl"))


async def test_explicit_as_names_every_offender_class(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 64)
    (tmp_path / "notes.txt").write_text("fine\n", encoding="utf-8")
    with pytest.raises(UsageFault) as caught:
        await _drain(_spec(str(tmp_path / "*"), as_mode="lines"))
    message = str(caught.value)
    assert "2 matched files can't be cut into lines" in message
    assert "a.png +1 more" in message  # first example + count, never the full dump


async def test_as_file_is_universal_for_media(tmp_path: Path) -> None:
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    items = await _drain(_spec(str(photo), as_mode="file"))
    assert len(items) == 1
    assert items[0].media  # the crate carries its image


async def test_binary_stdin_with_as_lines_refuses(tmp_path: Path) -> None:
    # simulate via a real pipe: a PNG redirected to stdin
    import os

    read_fd, write_fd = os.pipe()
    with os.fdopen(write_fd, "wb") as writer:
        writer.write(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    stdin = os.fdopen(read_fd, "r")
    try:
        items, _ = resolve_items(_spec(as_mode="lines"), stdin)
        with pytest.raises(UsageFault, match="no finer granularity"):
            _ = [item async for item in items]
    finally:
        stdin.close()
