"""``sempipe chart`` — bars in the terminal, SVG on disk. No model calls."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from sempipe.core.errors import ExitCode
from sempipe.verbs.chart import ChartRequest, run_chart

__all__ = ["chart_command"]


@click.command(name="chart")
@click.argument("field", required=False)
@click.option("--top", type=int, help="How many bars (default 20).")
@click.option(
    "--save",
    type=click.Path(path_type=Path),
    help="Also write the chart as an SVG file (no extra dependencies).",
)
@click.option("--title", help="Title for the saved SVG.")
def chart_command(field: str | None, top: int | None, save: Path | None, title: str | None) -> None:
    """Draw a bar chart of a field's values (or of whole lines). Free.

    \b
    Examples:
      cat tickets.txt | sempipe map "Extract {label}" | sempipe chart label
      jq -r .status data.jsonl | sempipe chart
      … | sempipe chart label --save labels.svg --title "Ticket labels"

    Reads NDJSON records (counts FIELD) or plain lines (counts each line).
    The chart is the result — it goes to stdout; --save adds an SVG.
    """
    code = run_chart(
        ChartRequest(field=field, top=top, save=save, title=title),
        stdin=sys.stdin,
        stdout=sys.stdout,
    )
    if code is not ExitCode.OK:
        raise SystemExit(int(code))
