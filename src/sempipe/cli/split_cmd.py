"""``sempipe split`` — break oversized items into chunk items. No model calls."""

from __future__ import annotations

import asyncio
import os
import sys

import click

from sempipe.cli.input_options import input_options, input_spec
from sempipe.cli.interrupts import graceful_interrupts
from sempipe.core.errors import ExitCode
from sempipe.verbs.split import SplitRequest, run_split

__all__ = ["split_command"]


@click.command(name="split")
@click.option(
    "--max-tokens",
    "max_tokens",
    type=int,
    help="Chunk budget in tokens (default 2000 — comfortable for every provider).",
)
@input_options
def split_command(
    max_tokens: int | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
) -> None:
    """Break oversized items into budget-sized chunks. Free — no model calls.

    \b
    Examples:
      sempipe split --in '10k-filings/*.pdf' | sempipe map "list the risk factors {risk}"
      sempipe split --in big.md --max-tokens 4000 | sempipe filter "mentions pricing"

    Each chunk is a JSON record: {"text": …, "source": "report.pdf §3/12"} —
    paragraph-boundary aware, and the chunks of a document concatenate back to
    its exact text. Recombine downstream with reduce.
    """
    request = SplitRequest(
        max_tokens_flag=max_tokens,
        input=input_spec(in_patterns, from_files=from_files),
    )
    code = asyncio.run(_run(request))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: SplitRequest) -> ExitCode:
    from sempipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ) as container,
    ):
        return await run_split(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
