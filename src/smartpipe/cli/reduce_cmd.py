"""``smartpipe reduce`` — synthesize many items into one."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from smartpipe.cli.completions import complete_chat_models
from smartpipe.cli.input_options import (
    fields_option,
    input_options,
    input_spec,
    resolve_prompt,
)
from smartpipe.cli.interrupts import graceful_interrupts, settle_budget
from smartpipe.core.errors import ExitCode
from smartpipe.verbs.reduce import ReduceRequest, run_reduce

__all__ = ["reduce_command"]


@click.command(name="reduce")
@click.argument("prompt", required=False)
@click.option(
    "--prompt-file",
    "prompt_file",
    type=click.Path(path_type=Path),
    help="Read the prompt from a file (the @file shorthand does the same).",
)
@click.option(
    "--schema-from",
    "schema_dsl",
    metavar="DSL",
    help='Build the schema from a short DSL: "vendor string; total number >= 0".',
)
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
@click.option("--max-calls", "max_calls", type=int, help="Stop after N model calls (cost cap).")
@click.option("--verbose", is_flag=True, help="Show the chunking tree on stderr.")
@click.option("--window", type=int, help="Stream mode: reduce every N lines (tumbling).")
@click.option("--every", type=int, help="With --window: slide, reducing after every M lines.")
@fields_option
@click.option(
    "--allow-captions",
    "allow_captions",
    is_flag=True,
    help="Let a CLOUD model convert images/audio to text (paid; local models do it free).",
)
@input_options
def reduce_command(
    prompt: str | None,
    prompt_file: Path | None,
    schema_path: Path | None,
    schema_dsl: str | None,
    group_by: str | None,
    model_flag: str | None,
    concurrency_flag: int | None,
    max_calls: int | None,
    allow_captions: bool,
    verbose: bool,
    window: int | None,
    every: int | None,
    fields: tuple[str, ...] | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
    as_mode: str | None,
) -> None:
    """Synthesize all input items into a single result.

    \b
    Examples:
      smartpipe reduce "Write a one-page executive summary" --in 'notes/*.md'
      cat reports.jsonl | smartpipe reduce "Write a root-cause analysis" --schema rca.json
      cat feedback.jsonl | smartpipe reduce "Summarize sentiment" --group-by product

    When the input is too large for the model, smartpipe chunks it and recursively
    summarizes — automatically. Add --verbose to see the chunking tree.
    """
    request = ReduceRequest(
        allow_captions=allow_captions,
        prompt=resolve_prompt(prompt, prompt_file),
        schema_path=schema_path,
        schema_dsl=schema_dsl,
        group_by=group_by,
        model_flag=model_flag,
        concurrency_flag=concurrency_flag,
        verbose=verbose,
        window=window,
        every=every,
        input=input_spec(in_patterns, from_files=from_files, as_mode=as_mode),
        fields=fields,
    )
    code = asyncio.run(_run(request, max_calls))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: ReduceRequest, max_calls: int | None) -> ExitCode:
    from smartpipe.container import build_container

    if request.window is None:  # whole-set mode: ^C exits immediately; budget is fatal (D18)
        async with build_container(os.environ, max_calls=max_calls) as container:
            if not request.allow_captions and container.config.allow_captions:
                from dataclasses import replace as _replace

                request = _replace(request, allow_captions=True)  # profile consent (D35)
            return await run_reduce(request, container, stdin=sys.stdin, stdout=sys.stdout)
    async with (  # stream mode drains + flushes partial
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        if not request.allow_captions and container.config.allow_captions:
            from dataclasses import replace as _replace

            request = _replace(request, allow_captions=True)  # profile consent (D35)
        code = await run_reduce(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
        return settle_budget(container.budget, code)
