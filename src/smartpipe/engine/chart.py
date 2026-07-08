"""Bar-chart rendering for ``smartpipe chart`` — counts in, pictures out.

Two terminal voices: piped or NO_COLOR output stays plain ASCII (aligned
labels, ``#`` bars, exact counts — downstream tools keep parsing it); a real
color TTY gets plotext canvases, cyan for distributions and green for time
series. ``--save`` renders through matplotlib — SVG or PNG by extension, in
the demo-video identity (near-black ground, one cyan accent, zinc monospace
text, no chartjunk). Both plotting imports stay function-local so startup
never pays for them, and the Agg backend is pinned before any pyplot import.
"""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

__all__ = [
    "CHARTS_BROKEN_SCREEN",
    "ChartFormat",
    "render_bars",
    "render_bars_tty",
    "render_figure",
    "render_figure_panels",
    "render_figure_timeseries",
    "render_timeseries_tty",
]

ChartFormat = Literal["png", "svg"]

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


# --- saved figures: matplotlib, the demo-video identity ---------------------------

CHARTS_BROKEN_SCREEN = (
    "error: saving charts needs matplotlib, which ships with smartpipe\n"
    "  a missing matplotlib is a broken install — reinstall smartpipe"
)

_BACKGROUND = "#0c0e12"  # near-black ground
_ACCENT = "#22d3ee"  # the one cyan — never rainbow-per-bar
_ACCENT_EDGE = "#67e8f9"  # lighter cyan edge: the soft glow
_TEXT = "#d4d4d8"  # zinc-300
_MUTED = "#a1a1aa"  # zinc-400
_SPINE = "#3f3f46"  # zinc-700
_GRID = "#27272a"  # zinc-800 — the one faint grid, on the value axis
_FIGURE_WIDTH = 8.0  # inches; fixed so output is deterministic
_FIGURE_DPI = 144

_RC_STYLE: Mapping[str, object] = {
    "figure.facecolor": _BACKGROUND,
    "axes.facecolor": _BACKGROUND,
    "savefig.facecolor": _BACKGROUND,
    "text.color": _TEXT,
    "axes.titlecolor": _TEXT,
    "axes.labelcolor": _MUTED,
    "xtick.color": _MUTED,
    "ytick.color": _MUTED,
    "axes.edgecolor": _SPINE,
    "font.family": "monospace",
    "svg.fonttype": "none",  # labels stay real, searchable text
    "svg.hashsalt": "smartpipe",  # same data, same bytes — no random ids
}


def render_figure(
    counts: Sequence[tuple[str, int]], *, title: str | None, fmt: ChartFormat
) -> bytes:
    """Ranked horizontal bars as SVG/PNG bytes — counts annotated at bar ends."""
    rows = list(counts) or [("(nothing)", 0)]

    def draw(figure: Figure) -> None:
        axes = figure.add_subplot()
        _draw_ranked_bars(axes, rows)
        if title:
            axes.set_title(title, loc="left", fontsize=13, pad=14)

    return _render_styled(draw, height=max(2.4, 0.5 * len(rows) + 1.4), fmt=fmt)


def render_figure_panels(
    panels: Sequence[tuple[str, Sequence[tuple[str, int]]]],
    *,
    title: str | None,
    fmt: ChartFormat,
) -> bytes:
    """One bar panel per facet, stacked in a single column (chart --facet)."""
    named = [(name, list(counts) or [("(nothing)", 0)]) for name, counts in panels]
    ratios = [len(rows) + 2 for _name, rows in named]

    def draw(figure: Figure) -> None:
        grid = figure.add_gridspec(nrows=len(named), ncols=1, height_ratios=ratios)
        for index, (name, rows) in enumerate(named):
            axes = figure.add_subplot(grid[index, 0])
            _draw_ranked_bars(axes, rows)
            axes.set_title(name, loc="left", fontsize=11, pad=10)
        if title:
            figure.suptitle(title, x=0.02, ha="left", fontsize=13)

    height = 0.5 * sum(ratios) + (0.6 if title else 0.2)
    return _render_styled(draw, height=height, fmt=fmt)


def render_figure_timeseries(
    rows: Sequence[tuple[str, int]], *, title: str | None, fmt: ChartFormat
) -> bytes:
    """Chronological vertical bars (chart --by-time) — empty buckets stay visible."""
    buckets = list(rows) or [("(nothing)", 0)]

    def draw(figure: Figure) -> None:
        axes = figure.add_subplot()
        values = [count for _, count in buckets]
        axes.bar(
            range(len(buckets)),
            values,
            color=_ACCENT,
            edgecolor=_ACCENT_EDGE,
            linewidth=1.2,
            width=0.7,
        )
        step = max(1, -(-len(buckets) // 8))
        positions = list(range(0, len(buckets), step))
        axes.set_xticks(positions, [buckets[position][0] for position in positions])
        _strip_chartjunk(axes, value_axis="y")

        if title:
            axes.set_title(title, loc="left", fontsize=13, pad=14)

    return _render_styled(draw, height=3.6, fmt=fmt)


def _draw_ranked_bars(axes: Axes, rows: Sequence[tuple[str, int]]) -> None:
    positions = list(range(len(rows)))
    values = [count for _, count in rows]
    bars = axes.barh(
        positions, values, color=_ACCENT, edgecolor=_ACCENT_EDGE, linewidth=1.2, height=0.62
    )
    axes.bar_label(bars, labels=[f"{value:,}" for value in values], padding=6, fontsize=9)
    axes.set_yticks(positions, [label for label, _ in rows])
    axes.invert_yaxis()  # rank 1 on top, like the terminal
    axes.set_xlim(0, (max(values) or 1) * 1.15)  # room for the count labels
    _strip_chartjunk(axes, value_axis="x")


def _strip_chartjunk(axes: Axes, *, value_axis: Literal["x", "y"]) -> None:
    from matplotlib.ticker import MaxNLocator

    axes.spines["top"].set_visible(False)
    axes.spines["right"].set_visible(False)
    axes.grid(axis=value_axis, color=_GRID, linewidth=0.8)
    axes.set_axisbelow(True)
    axes.tick_params(length=0)
    counted = axes.xaxis if value_axis == "x" else axes.yaxis
    counted.set_major_locator(MaxNLocator(integer=True))  # counts are integers


def _render_styled(draw: Callable[[Figure], None], *, height: float, fmt: ChartFormat) -> bytes:
    try:
        import matplotlib
    except ImportError as exc:
        from smartpipe.core.errors import SetupFault

        raise SetupFault(CHARTS_BROKEN_SCREEN) from exc

    matplotlib.use("Agg")  # pinned BEFORE any pyplot import — headless, deterministic
    from matplotlib import rc_context
    from matplotlib.figure import Figure

    with rc_context(_RC_STYLE):  # rc applies at artist creation, so draw inside it
        figure = Figure(figsize=(_FIGURE_WIDTH, height), dpi=_FIGURE_DPI, layout="constrained")
        draw(figure)
        buffer = io.BytesIO()
        metadata = {"Date": None} if fmt == "svg" else None  # no timestamps, ever
        figure.savefig(buffer, format=fmt, metadata=metadata)
    return buffer.getvalue()
