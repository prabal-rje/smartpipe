"""Bar-chart rendering for ``sempipe chart`` — pure, no plotting dependency.

Terminal bars from block characters; ``--save`` writes a hand-rolled SVG (text,
so it costs no dependency and converts to anything). Counts in, pictures out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["CHARTS_EXTRA_SCREEN", "render_bars", "render_svg"]

_BLOCK = "▇"
_BAR_WIDTH = 40  # cells for the longest bar; the rest scale


def render_bars(counts: Sequence[tuple[str, int]], *, width: int = _BAR_WIDTH) -> str:
    """Horizontal unicode bars, widest value = ``width`` cells, labels aligned."""
    if not counts:
        return "(nothing to chart)"
    label_width = max(len(label) for label, _ in counts)
    top = max(count for _, count in counts) or 1
    lines: list[str] = []
    for label, count in counts:
        cells = max(1, round(count / top * width)) if count else 0
        lines.append(f"{label.ljust(label_width)}  {_BLOCK * cells} {count:,}")
    return "\n".join(lines)


_ROW_HEIGHT = 28
_LABEL_SPACE = 180
_CHART_WIDTH = 640

CHARTS_EXTRA_SCREEN = (
    "error: saving charts needs an optional dependency\n"
    "  install it with:  pip install 'sempipe[charts]'"
)


def render_svg(counts: Sequence[tuple[str, int]], *, title: str | None = None) -> str:
    """An SVG bar chart via svgwrite (the ``[charts]`` extra) — a library, not
    bespoke markup (owner's call: no hand-rolled SVG to maintain)."""
    try:
        import svgwrite
    except ImportError as exc:
        from sempipe.core.errors import SetupFault

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
