from __future__ import annotations

import io

import pytest

from sempipe.core.errors import UsageFault
from sempipe.io.items import Item
from sempipe.io.readers import ensure_not_a_tty, stdin_items


async def _collect(stdin: io.StringIO) -> list[Item]:
    return [item async for item in stdin_items(stdin)]


async def test_yields_items_in_order_with_contiguous_indexes() -> None:
    items = await _collect(io.StringIO("a\nb\nc\n"))
    assert [item.raw for item in items] == ["a", "b", "c"]
    assert [item.source.index for item in items] == [0, 1, 2]


async def test_final_line_without_newline_is_an_item() -> None:
    items = await _collect(io.StringIO("a\nb"))
    assert [item.raw for item in items] == ["a", "b"]


async def test_empty_stdin_yields_nothing() -> None:
    assert await _collect(io.StringIO("")) == []


async def test_crlf_input() -> None:
    items = await _collect(io.StringIO("a\r\nb\r\n"))
    assert [item.raw for item in items] == ["a", "b"]


async def test_empty_lines_are_items() -> None:
    items = await _collect(io.StringIO("a\n\nb\n"))
    assert [item.raw for item in items] == ["a", "", "b"]


class _FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_tty_stdin_is_a_usage_fault() -> None:
    with pytest.raises(UsageFault) as excinfo:
        ensure_not_a_tty(_FakeTty())
    assert "pipe some input" in str(excinfo.value)


def test_piped_stdin_passes_the_guard() -> None:
    ensure_not_a_tty(io.StringIO("data\n"))  # must not raise


# --- scan routing disclosure (D39/03) ----------------------------------------------


def test_thin_text_with_figures_reads_as_a_scan() -> None:
    from sempipe.io.readers import figure_note

    note = figure_note("contract.pdf", 11, 8, 22)
    assert "thin text layer (11 chars)" in note
    assert "scanned?" in note
    assert "split --by pages --media" in note  # the actionable past-the-cap hint


def test_real_text_keeps_the_plainfigure_note() -> None:
    from sempipe.io.readers import figure_note

    note = figure_note("report.pdf", 5_000, 3, 0)
    assert note == "report.pdf: 3 figures attached"
    assert "scanned" not in note
