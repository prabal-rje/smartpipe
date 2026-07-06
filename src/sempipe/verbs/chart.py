"""The ``chart`` verb: NDJSON in, a bar chart out. Free — no model calls.

The chart IS the result, so it goes to stdout; ``--save`` additionally writes a
dependency-free SVG. Counts a field across records (or tallies whole lines),
which makes it the natural tail for the tools upstream:

    … | sempipe map "Extract {label}" | sempipe chart label
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sempipe.core.errors import ExitCode, UsageFault
from sempipe.engine.chart import render_bars, render_svg, render_svg_panels
from sempipe.io import diagnostics
from sempipe.io.items import item_from_line

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

__all__ = ["ChartRequest", "run_chart"]

_DEFAULT_TOP = 20


@dataclass(frozen=True, slots=True)
class ChartRequest:
    field: str | None = None  # None: tally whole lines
    top: int | None = None
    save: Path | None = None
    title: str | None = None
    facets: tuple[str, ...] = ()  # --facet a,b,c: several panels, one pass


class ChartContext(Protocol):
    """chart needs nothing from the container — the Protocol keeps the shape."""


def run_chart(request: ChartRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    if request.facets and request.field is not None:
        raise UsageFault("--facet replaces the FIELD argument — pass one or the other")
    if request.facets:
        return _run_facets(request, stdin=stdin, stdout=stdout)
    counts: Counter[str] = Counter()
    for index, line in enumerate(stdin):
        if not line.strip():
            continue
        item = item_from_line(line, index)
        if request.field is not None:
            value = item.data.get(request.field) if item.data is not None else None
            counts["(missing)" if value is None else str(value)] += 1
        else:
            counts[item.text.strip()] += 1
    ranked = counts.most_common(request.top or _DEFAULT_TOP)
    dropped = len(counts) - len(ranked)
    stdout.write(render_bars(ranked) + "\n")
    if dropped > 0:
        diagnostics.note(f"{dropped} more values below the top {len(ranked)} (--top widens)")
    if request.save is not None:
        request.save.write_text(
            render_svg(ranked, title=request.title or request.field), encoding="utf-8"
        )
        diagnostics.note(f"chart saved: {request.save} (SVG — opens anywhere, converts to png)")
    return ExitCode.OK


def _run_facets(request: ChartRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    tallies: dict[str, Counter[str]] = {facet: Counter() for facet in request.facets}
    for index, line in enumerate(stdin):
        if not line.strip():
            continue
        item = item_from_line(line, index)
        for facet, counter in tallies.items():
            value = item.data.get(facet) if item.data is not None else None
            counter["(missing)" if value is None else str(value)] += 1
    limit = request.top or _DEFAULT_TOP
    panels: list[tuple[str, list[tuple[str, int]]]] = []
    for facet, counter in tallies.items():
        ranked = counter.most_common(limit)
        panels.append((facet, ranked))
        dropped = len(counter) - len(ranked)
        if dropped > 0:
            diagnostics.note(f"{facet}: {dropped} more values below the top {len(ranked)}")
    rule_width = 46
    sections = [
        f"── {facet} {'─' * max(1, rule_width - len(facet) - 4)}\n" + render_bars(ranked)
        for facet, ranked in panels
    ]
    stdout.write("\n".join(sections) + "\n")
    if request.save is not None:
        request.save.write_text(render_svg_panels(panels, title=request.title), encoding="utf-8")
        diagnostics.note(f"chart saved: {request.save} (SVG — opens anywhere, converts to png)")
    return ExitCode.OK
