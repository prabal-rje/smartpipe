"""The ``chart`` verb: JSONL in, a bar chart out. Free — no model calls.

The chart IS the result, so it goes to stdout; ``--save`` additionally writes
an SVG or PNG (matplotlib, format by extension). Counts a field across records
(or tallies whole lines), which makes it the natural tail for the tools
upstream:

    … | smartpipe map "Extract {label}" | smartpipe chart label
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.engine.chart import (
    ChartFormat,
    render_bars,
    render_bars_tty,
    render_figure,
    render_figure_panels,
    render_figure_timeseries,
    render_timeseries_tty,
)
from smartpipe.engine.fieldpath import MISSING, lookup
from smartpipe.io import diagnostics
from smartpipe.io.items import Item, item_from_line

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from typing import TextIO

__all__ = ["ChartRequest", "run_chart"]

_DEFAULT_TOP = 20


def _save_format(path: Path) -> ChartFormat:
    """The output format, named by the file extension — anything else is a fault."""
    match path.suffix.lower():
        case ".svg":
            return "svg"
        case ".png":
            return "png"
        case _:
            raise UsageFault(
                "--save writes SVG or PNG — name the format by extension\n"
                "  Try: --save labels.svg   or   --save labels.png\n"
                f"  (got: {path.name})"
            )


@dataclass(frozen=True, slots=True)
class ChartRequest:
    field: str | None = None  # None: tally whole lines
    top: int | None = None
    save: Path | None = None
    title: str | None = None
    facets: tuple[str, ...] = ()  # --facet a,b,c: several panels, one pass
    by_time: str | None = None  # --by-time FIELD:BUCKET — chronological bars (D38/13)
    # Decided once at the CLI edge (stdout TTY + NO_COLOR) and injected, so the
    # verb stays pure: True draws plotext canvases, False stays plain ASCII.
    color: bool = False
    width: int = 80  # terminal columns, for the plotext canvases


def _render_ranked(counts: Sequence[tuple[str, int]], request: ChartRequest) -> str:
    if request.color:
        return render_bars_tty(counts, width=request.width)
    return render_bars(counts)


class ChartContext(Protocol):
    """chart needs nothing from the container — the Protocol keeps the shape."""


def _field_value(item: Item, field: str) -> object | None:
    """The item's value at ``field`` — a flat column first, then a field path
    (item 63); ``None`` for a miss, exactly like the old flat ``.get``."""
    if item.data is None:
        return None
    found = lookup(item.data, field)
    return None if found is MISSING else found


def run_chart(request: ChartRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    if request.facets and request.field is not None:
        raise UsageFault("--facet replaces the FIELD argument — pass one or the other")
    if request.by_time is not None and (request.facets or request.field is not None):
        raise UsageFault("--by-time replaces FIELD/--facet — pass one of the three")
    if request.save is not None:
        _save_format(request.save)  # fail fast, before a single line is consumed
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
            value = _field_value(item, request.field)
            counts["(missing)" if value is None else str(value)] += 1
        else:
            counts[item.text.strip()] += 1
    ranked = counts.most_common(request.top or _DEFAULT_TOP)
    dropped = len(counts) - len(ranked)
    stdout.write(_render_ranked(ranked, request) + "\n")
    if dropped > 0:
        diagnostics.note(f"{dropped} more values below the top {len(ranked)} (--top widens)")
    if request.save is not None:
        payload = render_figure(
            ranked, title=request.title or request.field, fmt=_save_format(request.save)
        )
        request.save.write_bytes(payload)
        diagnostics.note(f"chart saved: {request.save}")
    return ExitCode.OK


def _run_facets(request: ChartRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    tallies: dict[str, Counter[str]] = {facet: Counter() for facet in request.facets}
    for index, line in enumerate(stdin):
        if not line.strip():
            continue
        item = item_from_line(line, index)
        for facet, counter in tallies.items():
            value = _field_value(item, facet)
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
    from smartpipe.cli.screens import heading

    sections = [
        heading(f"── {facet} {'─' * max(1, rule_width - len(facet) - 4)}")
        + "\n"
        + _render_ranked(ranked, request)
        for facet, ranked in panels
    ]
    stdout.write("\n".join(sections) + "\n")
    if request.save is not None:
        payload = render_figure_panels(panels, title=request.title, fmt=_save_format(request.save))
        request.save.write_bytes(payload)
        diagnostics.note(f"chart saved: {request.save}")
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
        epoch = parse_timestamp(_field_value(item, field))
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
        rendered = (
            render_timeseries_tty(rows, width=request.width) if request.color else render_bars(rows)
        )
        stdout.write(rendered + "\n")
        if request.save is not None:
            payload = render_figure_timeseries(
                rows, title=request.title or field, fmt=_save_format(request.save)
            )
            request.save.write_bytes(payload)
            diagnostics.note(f"chart saved: {request.save}")
    if unparseable:
        diagnostics.note(
            f"{unparseable:,} rows with unparseable '{field}' — "
            "ISO-8601 or epoch only; preprocess with jq/date"
        )
    return ExitCode.OK
