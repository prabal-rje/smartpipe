"""``smartpipe filter`` — keep items matching a semantic condition."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from smartpipe.cli.input_options import input_options, input_spec, resolve_prompt
from smartpipe.cli.interrupts import graceful_interrupts, settle_budget
from smartpipe.core.errors import ExitCode
from smartpipe.verbs.filter import FilterRequest, run_filter

__all__ = ["filter_command"]


@click.command(name="filter")
@click.argument("condition", required=False)
@click.option(
    "--prompt-file",
    "prompt_file",
    type=click.Path(path_type=Path),
    help="Read the condition from a file (the @file shorthand does the same).",
)
@click.option("--not", "invert", is_flag=True, help="Keep items that do NOT match (like grep -v).")
@click.option("--model", "model_flag", help="Model for this run.")
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel model calls.")
@click.option("--max-calls", "max_calls", type=int, help="Stop after N model calls (cost cap).")
@click.option(
    "--allow-captions",
    "allow_captions",
    is_flag=True,
    help="Let a CLOUD model convert images/audio to text (paid; local models do it free).",
)
@input_options
def filter_command(
    condition: str | None,
    prompt_file: Path | None,
    invert: bool,
    model_flag: str | None,
    concurrency_flag: int | None,
    max_calls: int | None,
    allow_captions: bool,
    in_patterns: tuple[str, ...],
    from_files: bool,
) -> None:
    """Keep items matching a plain-English condition. Semantic grep.

    \b
    Examples:
      cat reviews.txt | smartpipe filter "the reviewer is sarcastic"
      cat tickets.jsonl | smartpipe filter "{priority} is wrong given {description}"
      smartpipe filter "mentions a security issue" --in 'logs/*.txt'

    Output is the matching input items, unchanged and in order (in file mode, the
    matching filenames). Zero matches is a successful (exit 0) empty result.
    Deterministic condition (field == value, text has "word")? where is free.
    """
    request = FilterRequest(
        allow_captions=allow_captions,
        condition=resolve_prompt(condition, prompt_file),
        invert=invert,
        model_flag=model_flag,
        concurrency_flag=concurrency_flag,
        input=input_spec(in_patterns, from_files=from_files),
    )
    code = asyncio.run(_run(request, max_calls))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: FilterRequest, max_calls: int | None) -> ExitCode:
    from smartpipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        if not request.allow_captions and container.config.allow_captions:
            from dataclasses import replace as _replace

            request = _replace(request, allow_captions=True)  # profile consent (D35)
        code = await run_filter(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
        return settle_budget(container.budget, code)
