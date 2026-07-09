"""``--as csv`` ingestion (item 54): the header row names the fields; every
data row becomes one record — streamed row-at-a-time through the stdlib
``csv`` machinery, mirroring the jsonl iterator shape (a 10 GB export must
never materialize).

Cell coercion ladder (documented in concepts/feeding-smartpipe.md): a cell
that is a whole number becomes an int, a decimal/scientific number becomes a
float, anything else — empty cells included — stays the string it was.

The ``__source`` spine carries ``{"path", "as": "csv", "line": N}`` where N is
the row's FIRST *physical* line number (header = line 1, first data row = 2),
so grep/sed line references keep matching even when a quoted cell spans lines.

Dialect by extension: ``.tsv`` cuts on tabs — including under an explicit
``--as csv`` — everything else (stdin included) cuts on commas. Full dialect
sniffing (``csv.Sniffer``) is deliberately out: extensions are predictable,
sniffers guess.
"""

from __future__ import annotations

import csv
import json
import re
from typing import TYPE_CHECKING

from smartpipe.core.errors import UsageFault
from smartpipe.io.items import Item, ItemSource

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = ["CsvCutter", "coerce_cell", "csv_delimiter", "csv_file_items"]

_BOM = "﻿"
# the same number shapes engine/schema coerces — NaN/Infinity stay strings on
# purpose (they have no JSON spelling, and json.dumps would emit invalid JSON)
_INT = re.compile(r"[+-]?\d+")
_FLOAT = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")


def coerce_cell(cell: str) -> object:
    """The per-cell ladder: int → float → string. Empty cells stay ``""``."""
    text = cell.strip()
    if _INT.fullmatch(text):
        return int(text)
    if _FLOAT.fullmatch(text):
        return float(text)
    return cell


def csv_delimiter(path: Path | None) -> str:
    """Dialect by extension: ``.tsv`` means tabs; everything else, commas."""
    if path is not None and path.suffix.lower() == ".tsv":
        return "\t"
    return ","


class CsvCutter:
    """Push-driven csv cutter shared by the file and stdin paths: feed
    physical lines (terminator included), receive Items. Stateful by design —
    an open quoted cell spans pushes, and the header outlives every row."""

    def __init__(self, *, origin: str | None, delimiter: str, empty_ok: bool = False) -> None:
        self._origin = origin
        self._delimiter = delimiter
        self._empty_ok = empty_ok  # a chained stdin tail may be empty; a csv source may not
        self._header: tuple[str, ...] | None = None
        self._header_line = 1
        self._pending: list[str] = []
        self._consumed = 0  # physical lines fully parsed into rows
        self._saw_line = False

    def push(self, line: str) -> list[Item]:
        if not self._saw_line:
            line = line.removeprefix(_BOM)  # the line-0 BOM, as item_from_line does
            self._saw_line = True
        self._pending.append(line)
        if sum(part.count('"') for part in self._pending) % 2:
            return []  # an open quoted cell — this record continues on the next line
        return self._cut()

    def finish(self) -> list[Item]:
        """EOF: flush any unterminated final record, refuse a header-less stream.
        A stream that was empty from the start refuses too, unless ``empty_ok``
        (the files-then-stdin chain, where an idle pipe is ordinary)."""
        items = self._cut() if self._pending else []
        if self._header is None and (self._saw_line or not self._empty_ok):
            raise UsageFault(
                f"--as csv: {self._where()} has no header row\n"
                "  csv needs a first line naming the columns; "
                "--as lines reads raw text instead."
            )
        return items

    def _where(self) -> str:
        return self._origin or "stdin"

    def _cut(self) -> list[Item]:
        pending, self._pending = self._pending, []
        start = self._consumed + 1
        self._consumed += len(pending)
        reader = csv.reader(pending, delimiter=self._delimiter)
        items: list[Item] = []
        offset = 0
        for row in reader:
            first_line = start + offset
            offset = reader.line_num
            if not row:
                continue  # a blank line is no record — skipped, still counted
            if self._header is None:
                self._header = self._checked_header(row, first_line)
                self._header_line = first_line
                continue
            items.append(self._item(row, first_line))
        return items

    def _checked_header(self, row: list[str], line: int) -> tuple[str, ...]:
        names = tuple(name.strip() for name in row)
        if any(not name for name in names):
            raise UsageFault(
                f"--as csv: {self._where()} line {line} has an empty column name\n"
                "  header names become the record fields, so every cell needs one."
            )
        seen: set[str] = set()
        for name in names:
            if name in seen:
                raise UsageFault(
                    f"--as csv: {self._where()} line {line} names {name!r} twice\n"
                    "  header names become the record fields, so each column needs its own."
                )
            seen.add(name)
        return names

    def _item(self, row: list[str], line: int) -> Item:
        header = self._header
        assert header is not None  # _cut() sets the header before any data row lands
        if len(row) != len(header):
            plural = "s" if len(row) != 1 else ""
            raise UsageFault(
                f"--as csv: {self._where()} line {line} has {len(row)} "
                f"column{plural}, expected {len(header)}\n"
                f"  every row must match the header (line {self._header_line} names "
                f"{len(header)} columns); --as lines reads raw text instead."
            )
        record: dict[str, object] = {
            name: coerce_cell(cell) for name, cell in zip(header, row, strict=True)
        }
        raw = json.dumps(record, ensure_ascii=False)
        source = ItemSource(
            kind="file" if self._origin is not None else "stdin",
            name=self._origin or "-",
            index=line - 1,  # source_record adds 1 back: `line` IS the physical line
            cut="csv",
            path=self._origin,
        )
        return Item(raw=raw, text=raw, data=record, source=source)


def csv_file_items(path: Path) -> Iterator[Item]:
    """One named file, streamed: rows become Items as the file is read.
    Unreadable files warn and skip (spec §6.3), like every other reader."""
    from smartpipe.io import diagnostics

    cutter = CsvCutter(origin=str(path), delimiter=csv_delimiter(path))
    try:
        with path.open(encoding="utf-8", errors="replace", newline="") as handle:
            for line in handle:
                yield from cutter.push(line)
    except OSError as exc:
        diagnostics.warn(f"skipped: {path} (cannot read: {exc.strerror or exc})")
        return
    yield from cutter.finish()
