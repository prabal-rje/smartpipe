"""``sempipe filter`` — keep items matching a semantic condition."""

from __future__ import annotations

import asyncio
import os
import sys

import click

from sempipe.container import build_container
from sempipe.core.errors import ExitCode
from sempipe.verbs.filter import FilterRequest, run_filter

__all__ = ["filter_command"]


@click.command(name="filter")
@click.argument("condition")
@click.option("--not", "invert", is_flag=True, help="Keep items that do NOT match (like grep -v).")
@click.option("--model", "model_flag", help="Model for this run.")
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel model calls.")
def filter_command(
    condition: str, invert: bool, model_flag: str | None, concurrency_flag: int | None
) -> None:
    """Keep items matching a plain-English condition. Semantic grep.

    \b
    Examples:
      cat reviews.txt | sempipe filter "the reviewer is sarcastic"
      cat tickets.jsonl | sempipe filter "{priority} is wrong given {description}"
      cat emails.txt | sempipe filter --not "this is spam"

    Output is the matching input items, unchanged and in order. Zero matches is a
    successful (exit 0) empty result.
    """
    request = FilterRequest(
        condition=condition,
        invert=invert,
        model_flag=model_flag,
        concurrency_flag=concurrency_flag,
    )
    code = asyncio.run(_run(request))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: FilterRequest) -> ExitCode:
    async with build_container(os.environ) as container:
        return await run_filter(request, container, stdin=sys.stdin, stdout=sys.stdout)
