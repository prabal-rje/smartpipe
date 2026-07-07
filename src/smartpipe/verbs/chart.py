"""The ``chart`` verb: NDJSON in, a bar chart out. Free — no model calls.

The chart IS the result, so it goes to stdout; ``--save`` additionally writes a
dependency-free SVG. Counts a field across records (or tallies whole lines),
which makes it the natural tail for the tools upstream:

    … | smartpipe map "Extract {label}" | smartpipe chart label
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.engine.chart import render_bars, render_svg, render_svg_panels
from smartpipe.io import diagnostics
from smartpipe.io.items import item_from_line

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
    by_time: str | None = None  # --by-time FIELD:BUCKET — chronological bars (D38/13)


class ChartContext(Protocol):
    """chart needs nothing from the container — the Protocol keeps the shape."""


def run_chart(request: ChartRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    if request.facets and request.field is not None:
        raise UsageFault("--facet replaces the FIELD argument — pass one or the other")
    if request.by_time is not None and (request.facets or request.field is not None):
        raise UsageFault("--by-time replaces FIELD/--facet — pass one of the three")
    if request.by_time is not None:
        return _run_by_time(request, stdin=stdin, stdout=stdout)
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


def _run_by_time(request: ChartRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    from smartpipe.engine.timebin import bucket_label, parse_bucket, parse_timestamp

    assert request.by_time is not None
    field, colon, bucket_text = request.by_time.partition(":")
    if not colon or not field.strip():
        raise UsageFault(
            "--by-time takes FIELD:BUCKET — e.g. --by-time ts:1h\n"
            "  Buckets: 1m · 5m · 15m · 1h · 6h · 1d"
        )
    bucket = parse_bucket(bucket_text)
    field = field.strip()
    counts: Counter[int] = Counter()
    unparseable = 0
    for index, line in enumerate(stdin):
        if not line.strip():
            continue
        item = item_from_line(line, index)
        value = item.data.get(field) if item.data is not None else None
        epoch = parse_timestamp(value)
        if epoch is None:
            unparseable += 1
            continue
        counts[int(epoch // bucket) * bucket] += 1
    if not counts:
        stdout.write("(nothing to chart)\n")
    else:
        # chronological, zero-filled: gaps in a time series are signal
        first, last = min(counts), max(counts)
        rows = [
            (bucket_label(float(moment), bucket), counts.get(moment, 0))
            for moment in range(first, last + bucket, bucket)
        ]
        stdout.write(render_bars(rows) + "\n")
        if request.save is not None:
            request.save.write_text(
                render_svg(rows, title=request.title or field), encoding="utf-8"
            )
            diagnostics.note(f"chart saved: {request.save} (SVG — opens anywhere, converts to png)")
    if unparseable:
        diagnostics.note(
            f"{unparseable:,} rows with unparseable '{field}' — "
            "ISO-8601 or epoch only; preprocess with jq/date"
        )
    return ExitCode.OK
