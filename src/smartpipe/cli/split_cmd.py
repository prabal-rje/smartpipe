"""``smartpipe split`` — break oversized items into chunk items. No model calls."""

from __future__ import annotations

import asyncio
import os
import sys

import click

from smartpipe.cli.input_options import input_options, input_spec
from smartpipe.cli.interrupts import graceful_interrupts
from smartpipe.core.errors import ExitCode
from smartpipe.verbs.split import SplitRequest, run_split

__all__ = ["split_command"]


@click.command(name="split")
@click.option(
    "--by",
    "by_flag",
    metavar="UNIT[:N]",
    help="Split unit: tokens, pages, minutes, seconds. e.g. --by pages, --by minutes:10",
)
@click.option(
    "--media",
    "media",
    is_flag=True,
    help="Extract images embedded in PDFs/DOCX/PPTX/XLSX as items (icons dropped).",
)
@click.option(
    "--max-tokens",
    "max_tokens",
    type=int,
    help="Shorthand for --by tokens:N (default 2000).",
)
@input_options
def split_command(
    by_flag: str | None,
    media: bool,
    max_tokens: int | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
) -> None:
    """Break oversized items into budget-sized chunks. Free — no model calls.

    \b
    Examples:
      smartpipe split --in '10k-filings/*.pdf' | smartpipe map "list the risk factors {risk}"
      smartpipe split --by pages:5 --in report.pdf | smartpipe map "summarize these pages"
      smartpipe split --by minutes:10 --in call.mp3 | smartpipe map "what was agreed?"

    Each chunk is a JSON record: {"text": …, "source": "report.pdf §3/12"} —
    paragraph-boundary aware, and the chunks of a document concatenate back to
    its exact text. Recombine downstream with reduce.
    """
    request = SplitRequest(
        max_tokens_flag=max_tokens,
        by_flag=by_flag,
        media=media,
        input=input_spec(in_patterns, from_files=from_files),
    )
    code = asyncio.run(_run(request))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: SplitRequest) -> ExitCode:
    from smartpipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ) as container,
    ):
        return await run_split(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
