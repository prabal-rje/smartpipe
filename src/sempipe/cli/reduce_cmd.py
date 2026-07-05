"""``sempipe reduce`` — synthesize many items into one."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from sempipe.cli.completions import complete_chat_models
from sempipe.cli.input_options import fields_option, input_options, input_spec
from sempipe.cli.interrupts import graceful_interrupts
from sempipe.container import build_container
from sempipe.core.errors import ExitCode
from sempipe.verbs.reduce import ReduceRequest, run_reduce

__all__ = ["reduce_command"]


@click.command(name="reduce")
@click.argument("prompt")
@click.option(
    "--schema",
    "schema_path",
    type=click.Path(path_type=Path),
    help="Shape the final result with a JSON Schema.",
)
@click.option("--group-by", "group_by", help="Reduce per group (by an input JSON field).")
@click.option(
    "--model", "model_flag", shell_complete=complete_chat_models, help="Model for this run."
)
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel model calls.")
@click.option("--verbose", is_flag=True, help="Show the chunking tree on stderr.")
@click.option("--window", type=int, help="Stream mode: reduce every N lines (tumbling).")
@click.option("--every", type=int, help="With --window: slide, reducing after every M lines.")
@fields_option
@input_options
def reduce_command(
    prompt: str,
    schema_path: Path | None,
    group_by: str | None,
    model_flag: str | None,
    concurrency_flag: int | None,
    verbose: bool,
    window: int | None,
    every: int | None,
    fields: tuple[str, ...] | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
) -> None:
    """Synthesize all input items into a single result.

    \b
    Examples:
      sempipe reduce "Write a one-page executive summary" --in 'notes/*.md'
      cat reports.jsonl | sempipe reduce "Write a root-cause analysis" --schema rca.json
      cat feedback.jsonl | sempipe reduce "Summarize sentiment" --group-by product

    When the input is too large for the model, sempipe chunks it and recursively
    summarizes — automatically. Add --verbose to see the chunking tree.
    """
    request = ReduceRequest(
        prompt=prompt,
        schema_path=schema_path,
        group_by=group_by,
        model_flag=model_flag,
        concurrency_flag=concurrency_flag,
        verbose=verbose,
        window=window,
        every=every,
        input=input_spec(in_patterns, from_files=from_files),
        fields=fields,
    )
    code = asyncio.run(_run(request))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: ReduceRequest) -> ExitCode:
    async with build_container(os.environ) as container:
        if request.window is None:  # whole-set mode: ^C exits immediately (ux.md §12)
            return await run_reduce(request, container, stdin=sys.stdin, stdout=sys.stdout)
        async with graceful_interrupts() as stop:  # stream mode drains + flushes partial
            return await run_reduce(
                request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop
            )
