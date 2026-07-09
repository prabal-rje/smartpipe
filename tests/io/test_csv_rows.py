"""``--as csv`` ingestion (item 54): the streaming cutter, cell coercion,
physical line numbers on the ``__source`` spine, and the loud jsonl-style
refusals (ragged rows, missing/blank/duplicate headers).
"""

from __future__ import annotations

import csv
import io as stdlib_io
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.io.csvrows import CsvCutter, coerce_cell, csv_delimiter, csv_file_items

if TYPE_CHECKING:
    from pathlib import Path

    from smartpipe.io.items import Item


def _cut(text: str, *, origin: str | None = None, delimiter: str = ",") -> list[Item]:
    cutter = CsvCutter(origin=origin, delimiter=delimiter)
    items = [item for line in text.splitlines(keepends=True) for item in cutter.push(line)]
    return items + cutter.finish()


# --- the happy path ---------------------------------------------------------------


def test_header_names_fields_and_rows_become_records() -> None:
    items = _cut("name,age\nalice,31\nbob,52\n")
    assert [item.data for item in items] == [
        {"name": "alice", "age": 31},
        {"name": "bob", "age": 52},
    ]


def test_cell_coercion_int_then_float_then_string() -> None:
    (item,) = _cut("a,b,c,d,e\n7,-2.5,1e3,007x,\n")
    assert item.data == {"a": 7, "b": -2.5, "c": 1000.0, "d": "007x", "e": ""}


def test_coerce_cell_ladder() -> None:
    assert coerce_cell("42") == 42
    assert coerce_cell("-3") == -3
    assert coerce_cell("2.5") == 2.5
    assert coerce_cell(".5") == 0.5
    assert coerce_cell("1e-3") == 0.001
    assert coerce_cell("nan") == "nan"  # no NaN/Infinity — they have no JSON spelling
    assert coerce_cell("inf") == "inf"
    assert coerce_cell("") == ""
    assert coerce_cell("hello") == "hello"


def test_source_spine_carries_physical_line_numbers() -> None:
    items = _cut("h\nfirst\nsecond\n", origin="rows.csv")
    # header = line 1, so the first data row is PHYSICAL line 2 (grep agrees)
    assert [item.source.index + 1 for item in items] == [2, 3]
    assert items[0].source.cut == "csv"
    assert items[0].source.path == "rows.csv"


def test_quoted_cell_spanning_lines_keeps_the_rows_first_line() -> None:
    text = 'id,note\n1,"line one\nline two"\n2,plain\n'
    items = _cut(text)
    assert items[0].data == {"id": 1, "note": "line one\nline two"}
    assert items[0].source.index + 1 == 2  # the row STARTS at physical line 2
    assert items[1].source.index + 1 == 4  # the quoted cell consumed lines 2-3


def test_quoted_delimiter_stays_one_cell() -> None:
    (item,) = _cut('a,b\n"x,y",2\n')
    assert item.data == {"a": "x,y", "b": 2}


def test_raw_and_text_are_the_record_as_json() -> None:
    (item,) = _cut("a,b\n1,x\n")
    assert item.raw == '{"a": 1, "b": "x"}'
    assert item.text == item.raw


def test_blank_lines_are_skipped_but_counted() -> None:
    items = _cut("h\n\nvalue\n")
    assert [item.data for item in items] == [{"h": "value"}]
    assert items[0].source.index + 1 == 3  # the blank line 2 still counts


def test_bom_on_the_first_line_is_stripped() -> None:
    items = _cut("﻿name\nx\n")
    assert items[0].data == {"name": "x"}


def test_final_row_without_trailing_newline_still_lands() -> None:
    items = _cut("h\nlast")
    assert [item.data for item in items] == [{"h": "last"}]


# --- refusals (jsonl-style: loud, named, with the fix) ------------------------------


def test_ragged_row_names_file_line_and_counts() -> None:
    with pytest.raises(UsageFault) as caught:
        _cut("a,b,c\n1,2\n", origin="data.csv")
    message = str(caught.value)
    assert "--as csv: data.csv line 2 has 2 columns, expected 3" in message
    assert "line 1 names 3 columns" in message


def test_ragged_row_on_stdin_names_stdin() -> None:
    with pytest.raises(UsageFault, match=r"--as csv: stdin line 2 has 4 columns, expected 1"):
        _cut("a\nw,x,y,z\n")


def test_empty_input_refuses_missing_header() -> None:
    with pytest.raises(UsageFault, match=r"--as csv: stdin has no header row"):
        _cut("")


def test_blank_lines_only_refuse_missing_header() -> None:
    with pytest.raises(UsageFault, match="has no header row"):
        _cut("\n\n")


def test_empty_ok_tolerates_an_idle_chained_stream() -> None:
    # the files-then-stdin chain: nothing arriving on the pipe is ordinary
    cutter = CsvCutter(origin=None, delimiter=",", empty_ok=True)
    assert cutter.finish() == []


def test_blank_header_cell_refuses() -> None:
    with pytest.raises(UsageFault, match=r"line 1 has an empty column name"):
        _cut("a,,c\n1,2,3\n", origin="data.csv")


def test_duplicate_header_name_refuses() -> None:
    with pytest.raises(UsageFault, match=r"line 1 names 'id' twice"):
        _cut("id,id\n1,2\n")


# --- dialect by extension ------------------------------------------------------------


def test_tsv_delimiter_by_extension(tmp_path: Path) -> None:
    assert csv_delimiter(tmp_path / "rows.tsv") == "\t"
    assert csv_delimiter(tmp_path / "rows.TSV") == "\t"
    assert csv_delimiter(tmp_path / "rows.csv") == ","
    assert csv_delimiter(None) == ","  # stdin cuts on commas


def test_tab_cells_cut_with_tab_delimiter() -> None:
    (item,) = _cut("a\tb\n1\tx\n", delimiter="\t")
    assert item.data == {"a": 1, "b": "x"}


# --- file streaming --------------------------------------------------------------


def test_csv_file_items_streams_rows(tmp_path: Path) -> None:
    data = tmp_path / "rows.csv"
    data.write_text("a,b\n1,x\n2,y\n", encoding="utf-8")
    rows = csv_file_items(data)
    first = next(rows)  # a generator: rows arrive one at a time, never a slurped list
    assert first.data == {"a": 1, "b": "x"}
    assert first.source.path == str(data)
    assert [item.data for item in rows] == [{"a": 2, "b": "y"}]


def test_csv_file_items_tsv_cuts_on_tabs(tmp_path: Path) -> None:
    data = tmp_path / "rows.tsv"
    data.write_text("a\tb\n1\tx\n", encoding="utf-8")
    items = list(csv_file_items(data))
    assert items[0].data == {"a": 1, "b": "x"}


def test_csv_file_items_empty_file_refuses(tmp_path: Path) -> None:
    data = tmp_path / "empty.csv"
    data.write_text("", encoding="utf-8")
    with pytest.raises(UsageFault, match="has no header row"):
        list(csv_file_items(data))


def test_csv_file_items_unreadable_warns_and_skips(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.csv"
    assert list(csv_file_items(missing)) == []
    assert "skipped" in capsys.readouterr().err


def test_csv_file_items_crlf_line_endings(tmp_path: Path) -> None:
    data = tmp_path / "rows.csv"
    data.write_bytes(b"a,b\r\n1,x\r\n")
    items = list(csv_file_items(data))
    assert [item.data for item in items] == [{"a": 1, "b": "x"}]


# --- the csv → smartpipe → csv round trip -------------------------------------------


def test_csv_round_trip_through_the_table_writer(tmp_path: Path) -> None:
    original = 'name,age,note\nalice,31,"likes, commas"\nbob,52,plain\n'
    data = tmp_path / "people.csv"
    data.write_text(original, encoding="utf-8")
    items = list(csv_file_items(data))

    from smartpipe.io.writers import RenderMode, WriterConfig, make_writer

    sink = stdlib_io.StringIO()
    writer = make_writer(WriterConfig(mode=RenderMode.CSV, color=False, width=80, bare=True), sink)
    for item in items:
        assert item.data is not None
        writer.write_record(item.data)
    writer.flush()

    emitted = list(csv.reader(stdlib_io.StringIO(sink.getvalue())))
    assert emitted == list(csv.reader(stdlib_io.StringIO(original)))
