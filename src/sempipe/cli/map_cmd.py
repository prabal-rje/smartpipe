"""``sempipe map`` — the CLI surface: flags in, verb out."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from sempipe.cli.input_options import fields_option, input_options, input_spec
from sempipe.cli.interrupts import graceful_interrupts
from sempipe.container import build_container
from sempipe.core.errors import ExitCode
from sempipe.io.writers import OutputFormat
from sempipe.verbs.map import MapRequest, run_map

__all__ = ["map_command"]


@click.command(name="map")
@click.argument("prompt")
@click.option(
    "--schema",
    "schema_path",
    type=click.Path(path_type=Path),
    help="Enforce a JSON Schema on the output.",
)
@click.option(
    "--model",
    "model_flag",
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
@fields_option
@input_options
def map_command(
    prompt: str,
    schema_path: Path | None,
    model_flag: str | None,
    output: str,
    concurrency_flag: int | None,
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
        prompt=prompt,
        schema_path=schema_path,
        model_flag=model_flag,
        output=OutputFormat(output),
        concurrency_flag=concurrency_flag,
        input=input_spec(in_patterns, from_files=from_files),
        fields=fields,
    )
    code = asyncio.run(_run(request))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: MapRequest) -> ExitCode:
    async with build_container(os.environ) as container, graceful_interrupts() as stop:
        return await run_map(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
