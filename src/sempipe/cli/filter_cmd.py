"""``sempipe filter`` — keep items matching a semantic condition."""

from __future__ import annotations

import asyncio
import os
import sys

import click

from sempipe.cli.input_options import input_options, input_spec
from sempipe.cli.interrupts import graceful_interrupts
from sempipe.container import build_container
from sempipe.core.errors import ExitCode
from sempipe.verbs.filter import FilterRequest, run_filter

__all__ = ["filter_command"]


@click.command(name="filter")
@click.argument("condition")
@click.option("--not", "invert", is_flag=True, help="Keep items that do NOT match (like grep -v).")
@click.option("--model", "model_flag", help="Model for this run.")
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel model calls.")
@input_options
def filter_command(
    condition: str,
    invert: bool,
    model_flag: str | None,
    concurrency_flag: int | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
) -> None:
    """Keep items matching a plain-English condition. Semantic grep.

    \b
    Examples:
      cat reviews.txt | sempipe filter "the reviewer is sarcastic"
      cat tickets.jsonl | sempipe filter "{priority} is wrong given {description}"
      sempipe filter "mentions a security issue" --in 'logs/*.txt'

    Output is the matching input items, unchanged and in order (in file mode, the
    matching filenames). Zero matches is a successful (exit 0) empty result.
    """
    request = FilterRequest(
        condition=condition,
        invert=invert,
        model_flag=model_flag,
        concurrency_flag=concurrency_flag,
        input=input_spec(in_patterns, from_files=from_files),
    )
    code = asyncio.run(_run(request))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: FilterRequest) -> ExitCode:
    async with build_container(os.environ) as container, graceful_interrupts() as stop:
        return await run_filter(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
