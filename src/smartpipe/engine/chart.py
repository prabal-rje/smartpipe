"""Bar-chart rendering for ``smartpipe chart`` — counts in, pictures out.

Two terminal voices: piped or NO_COLOR output stays plain ASCII (aligned
labels, ``#`` bars, exact counts — downstream tools keep parsing it); a real
color TTY gets plotext canvases, cyan for distributions and green for time
series. The plotext import stays function-local so startup never pays for it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "CHARTS_EXTRA_SCREEN",
    "render_bars",
    "render_bars_tty",
    "render_svg",
    "render_svg_panels",
    "render_timeseries_tty",
]

_EMPTY = "(nothing to chart)"
_MARKER = "#"  # piped output is plain ASCII — parseable, greppable
_BAR_WIDTH = 40  # cells for the longest bar; the rest scale
# plotext's simple_bar hardcodes counts as `3.00`; strip the fake decimals from
# the value token at each line's end (labels sit at line start, never matched).
_SIMPLE_BAR_DECIMALS = re.compile(r"\.00(?=\x1b\[0m\x1b\[0m$)", re.MULTILINE)
_TIMESERIES_ROWS = 12  # canvas height: frame + bars + tick labels
_TIMESERIES_TICKS = 6  # at most this many time labels along the x axis


def render_bars(counts: Sequence[tuple[str, int]], *, width: int = _BAR_WIDTH) -> str:
    """Horizontal ASCII bars, widest value = ``width`` cells, labels aligned."""
    if not counts:
        return _EMPTY
    label_width = max(len(label) for label, _ in counts)
    top = max(count for _, count in counts) or 1
    lines: list[str] = []
    for label, count in counts:
        cells = max(1, round(count / top * width)) if count else 0
        lines.append(f"{label.ljust(label_width)}  {_MARKER * cells} {count:,}")
    return "\n".join(lines)


def render_bars_tty(counts: Sequence[tuple[str, int]], *, width: int) -> str:
    """Cyan plotext bars for a color TTY — labels, bars, and exact counts."""
    if not counts:
        return _EMPTY
    import plotext

    plotext.clear_figure()
    labels = [label for label, _ in counts]
    values = [count for _, count in counts]
    plotext.simple_bar(labels, values, width=width, color="cyan")
    return _SIMPLE_BAR_DECIMALS.sub("", plotext.build().rstrip("\n"))


def render_timeseries_tty(rows: Sequence[tuple[str, int]], *, width: int) -> str:
    """Green vertical plotext bars over time — gaps in the buckets stay visible."""
    if not rows:
        return _EMPTY
    import plotext

    plotext.clear_figure()
    plotext.theme("clear")
    values = [count for _, count in rows]
    plotext.bar(list(range(len(rows))), values, color="green", width=0.6)
    step = max(1, -(-len(rows) // _TIMESERIES_TICKS))
    positions = list(range(0, len(rows), step))
    plotext.xticks(positions, [rows[position][0] for position in positions])
    top = max(values)
    plotext.yticks(sorted({0, top // 2, top}))
    plotext.plot_size(width, _TIMESERIES_ROWS)
    return plotext.build().rstrip("\n")


_ROW_HEIGHT = 28
_LABEL_SPACE = 180
_CHART_WIDTH = 640

CHARTS_EXTRA_SCREEN = (
    "error: saving charts needs an optional dependency\n"
    "  svgwrite ships with smartpipe — reinstall smartpipe"
)


def render_svg(counts: Sequence[tuple[str, int]], *, title: str | None = None) -> str:
    """An SVG bar chart via svgwrite (the ``[charts]`` extra) — a library, not
    bespoke markup (owner's call: no hand-rolled SVG to maintain)."""
    try:
        import svgwrite
    except ImportError as exc:
        from smartpipe.core.errors import SetupFault

        raise SetupFault(CHARTS_EXTRA_SCREEN) from exc

    rows = list(counts) or [("(nothing)", 0)]
    top = max(count for _, count in rows) or 1
    header = 36 if title else 8
    height = header + len(rows) * _ROW_HEIGHT + 8
    drawing = svgwrite.Drawing(size=(_CHART_WIDTH, height))
    drawing.add(drawing.rect(insert=(0, 0), size=(_CHART_WIDTH, height), fill="white"))
    text_style = {"font_family": "system-ui, sans-serif", "font_size": "13px"}
    if title:
        drawing.add(
            drawing.text(
                title,
                insert=(12, 24),
                font_weight="bold",
                font_size="16px",
                font_family="system-ui, sans-serif",
            )
        )
    bar_space = _CHART_WIDTH - _LABEL_SPACE - 80
    for row, (label, count) in enumerate(rows):
        y = header + row * _ROW_HEIGHT
        bar = max(2, round(count / top * bar_space)) if count else 0
        drawing.add(
            drawing.text(label, insert=(_LABEL_SPACE - 8, y + 18), text_anchor="end", **text_style)
        )
        drawing.add(
            drawing.rect(insert=(_LABEL_SPACE, y + 6), size=(bar, 16), fill="#4477aa", rx=2)
        )
        drawing.add(
            drawing.text(f"{count:,}", insert=(_LABEL_SPACE + bar + 6, y + 18), **text_style)
        )
    return drawing.tostring()


def render_svg_panels(
    panels: Sequence[tuple[str, Sequence[tuple[str, int]]]], *, title: str | None = None
) -> str:
    """Several bar panels stacked in one SVG (chart --facet). Same dependency
    rules as render_svg: svgwrite behind the [charts] extra."""
    try:
        import svgwrite
    except ImportError as exc:
        from smartpipe.core.errors import SetupFault

        raise SetupFault(CHARTS_EXTRA_SCREEN) from exc

    header = 36 if title else 8
    heights = [24 + max(1, len(counts)) * _ROW_HEIGHT + 8 for _name, counts in panels]
    total_height = header + sum(heights)
    drawing = svgwrite.Drawing(size=(_CHART_WIDTH, total_height))
    drawing.add(drawing.rect(insert=(0, 0), size=(_CHART_WIDTH, total_height), fill="white"))
    text_style = {"font_family": "system-ui, sans-serif", "font_size": "13px"}
    if title:
        drawing.add(
            drawing.text(
                title,
                insert=(12, 24),
                font_weight="bold",
                font_size="16px",
                font_family="system-ui, sans-serif",
            )
        )
    offset = header
    bar_space = _CHART_WIDTH - _LABEL_SPACE - 80
    for (name, counts), panel_height in zip(panels, heights, strict=True):
        drawing.add(
            drawing.text(
                name,
                insert=(12, offset + 16),
                font_weight="bold",
                font_size="13px",
                font_family="system-ui, sans-serif",
            )
        )
        rows = list(counts) or [("(nothing)", 0)]
        top = max(count for _label, count in rows) or 1
        for row, (label, count) in enumerate(rows):
            y = offset + 24 + row * _ROW_HEIGHT
            bar = max(2, round(count / top * bar_space)) if count else 0
            drawing.add(
                drawing.text(
                    label, insert=(_LABEL_SPACE - 8, y + 18), text_anchor="end", **text_style
                )
            )
            drawing.add(
                drawing.rect(insert=(_LABEL_SPACE, y + 6), size=(bar, 16), fill="#4477aa", rx=2)
            )
            drawing.add(
                drawing.text(f"{count:,}", insert=(_LABEL_SPACE + bar + 6, y + 18), **text_style)
            )
        offset += panel_height
    return drawing.tostring()
