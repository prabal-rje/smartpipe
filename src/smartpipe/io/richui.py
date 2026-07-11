"""Lazy Rich rendering for human-facing terminal screens.

The value types in this module are dependency-free and safe on the CLI's hot
import path. Rich itself is imported only inside the rendering functions, so a
command pays for it only when it actually draws a human-facing screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from io import StringIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import Console
    from rich.text import Text

__all__ = ["Cell", "UiStyle", "render_grid", "render_text"]

_UNWRAPPED_WIDTH = 10_000  # these screens were historically unwrapped string contracts


class UiStyle(StrEnum):
    """The small, shared visual vocabulary for smartpipe's CLI screens."""

    DIM = "dim"
    GOOD = "green"
    BAD = "red"


@dataclass(frozen=True, slots=True)
class Cell:
    """One text span or grid cell with optional semantic styling."""

    text: str
    style: UiStyle | None = None


def render_text(spans: Sequence[Cell], *, color: bool) -> str:
    """Render adjacent styled spans without adding a newline."""
    from rich.text import Text

    text = Text()
    for span in spans:
        text.append(span.text, style=_style(span, color=color))
    buffer = StringIO()
    _console(buffer, color=color).print(text, end="", soft_wrap=True)
    return buffer.getvalue()


def render_grid(
    rows: Sequence[Sequence[Cell]],
    *,
    color: bool,
    column_gap: int = 2,
    column_widths: Sequence[int | None] | None = None,
) -> str:
    """Render a borderless grid whose columns use terminal display width."""
    if not rows:
        return ""
    column_count = len(rows[0])
    if not all(len(row) == column_count for row in rows):
        raise AssertionError("grid rows must have the same number of cells")
    if column_widths is not None and len(column_widths) != column_count:
        raise AssertionError("column widths must match the number of cells")

    from rich.table import Table

    table = Table.grid(padding=(0, column_gap), pad_edge=False)
    widths = column_widths or (None,) * column_count
    for width in widths:
        table.add_column(width=width, no_wrap=True)
    for row in rows:
        table.add_row(*(_cell_text(cell, color=color) for cell in row))
    buffer = StringIO()
    _console(buffer, color=color).print(table, end="")
    return "\n".join(line.rstrip() for line in buffer.getvalue().splitlines())


def _style(cell: Cell, *, color: bool) -> str | None:
    if not color or cell.style is None:
        return None
    return cell.style.value


def _cell_text(cell: Cell, *, color: bool) -> Text:
    from rich.text import Text

    style = _style(cell, color=color)
    if style is None:
        return Text(cell.text, no_wrap=True)
    return Text(cell.text, style=style, no_wrap=True)


def _console(buffer: StringIO, *, color: bool) -> Console:
    from rich.console import Console

    return Console(
        file=buffer,
        color_system="standard" if color else None,
        force_terminal=color,
        force_jupyter=False,
        force_interactive=False,
        no_color=not color,
        width=_UNWRAPPED_WIDTH,
        # Rich intentionally forces TERM=dumb consoles to 80x25 unless both
        # dimensions are explicit. These render to an in-memory buffer and are
        # contractually unwrapped, so pin the otherwise irrelevant height too.
        height=25,
        highlight=False,
        markup=False,
        emoji=False,
    )
