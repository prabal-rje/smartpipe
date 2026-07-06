"""``sempipe map`` — the CLI surface: flags in, verb out."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from sempipe.cli.completions import complete_chat_models
from sempipe.cli.input_options import (
    fields_option,
    input_options,
    input_spec,
    resolve_prompt,
)
from sempipe.cli.interrupts import graceful_interrupts, settle_budget
from sempipe.core.errors import ExitCode
from sempipe.io.writers import OutputFormat
from sempipe.verbs.map import MapRequest, run_map

__all__ = ["map_command"]


@click.command(name="map")
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
    help="Enforce a JSON Schema on the output.",
)
@click.option(
    "--model",
    "model_flag",
    shell_complete=complete_chat_models,
    help="Model for this run (e.g. ollama/qwen3:8b, claude-opus-4-8).",
)
@click.option(
    "--output",
    type=click.Choice([fmt.value for fmt in OutputFormat]),
    default=OutputFormat.AUTO.value,
    show_default=True,
    help="Output format.",
)
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel model calls.")
@click.option("--max-calls", "max_calls", type=int, help="Stop after N model calls (cost cap).")
@fields_option
@input_options
def map_command(
    prompt: str | None,
    prompt_file: Path | None,
    schema_path: Path | None,
    schema_dsl: str | None,
    model_flag: str | None,
    output: str,
    concurrency_flag: int | None,
    max_calls: int | None,
    fields: tuple[str, ...] | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
) -> None:
    """Transform each input item with a prompt. One item in, one result out.

    \b
    Examples:
      echo "hello" | sempipe map "translate to Spanish"
      cat reviews.jsonl | sempipe map "Extract {product, sentiment}"
      sempipe map "Summarize this document" --in 'reports/*.pdf'

    Braces in the prompt name the output fields you want back (JSON).
    Plain prompts return plain text, one line per item.
    """
    request = MapRequest(
        prompt=resolve_prompt(prompt, prompt_file),
        schema_path=schema_path,
        schema_dsl=schema_dsl,
        model_flag=model_flag,
        output=OutputFormat(output),
        concurrency_flag=concurrency_flag,
        input=input_spec(in_patterns, from_files=from_files),
        fields=fields,
    )
    code = asyncio.run(_run(request, max_calls))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: MapRequest, max_calls: int | None) -> ExitCode:
    from sempipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        code = await run_map(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
        return settle_budget(container.budget, code)
