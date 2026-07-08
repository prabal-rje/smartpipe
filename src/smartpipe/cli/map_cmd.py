"""``smartpipe map`` — the CLI surface: flags in, verb out."""

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
from smartpipe.io.writers import OutputFormat
from smartpipe.verbs.map import MapRequest, run_map

__all__ = ["map_command"]


@click.command(name="map")
@click.argument("prompt", required=False)
@click.option(
    "--prompt-file",
    "prompt_file",
    type=click.Path(path_type=Path),
    help="Prompt from a file (@file works too).",
)
@click.option(
    "--explode",
    "explode_field",
    metavar="FIELD",
    help="One row per element of a list-valued FIELD.",
)
@click.option(
    "--tally",
    "tally_field",
    metavar="FIELD",
    help="Count FIELD's values (live tally on stderr).",
)
@click.option(
    "--schema-from",
    "schema_dsl",
    metavar="DSL",
    help='Schema from a mini-DSL: "vendor string; total number >= 0".',
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
@click.option(
    "--frame-every",
    "frame_every",
    type=float,
    metavar="SECONDS",
    help="Video density guarantee: one frame per period (lifts the 24-frame cap).",
)
@click.option(
    "--max-frames",
    "max_frames",
    type=int,
    help="Video frame budget (default 24; the smaller of the two flags wins).",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Print the composed first request (system, schema, item) and exit — no model call.",
)
@click.option(
    "--keep-invalid",
    "keep_invalid",
    is_flag=True,
    help='Failed extractions become {"__invalid": true, "__error": …, "__raw": …} rows, not skips.',
)
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel model calls.")
@click.option("--max-calls", "max_calls", type=int, help="Stop after N model calls (cost cap).")
@fields_option
@input_options
def map_command(
    prompt: str | None,
    frame_every: float | None,
    max_frames: int | None,
    prompt_file: Path | None,
    schema_path: Path | None,
    schema_dsl: str | None,
    tally_field: str | None,
    explode_field: str | None,
    model_flag: str | None,
    output: str,
    dry_run: bool,
    keep_invalid: bool,
    concurrency_flag: int | None,
    max_calls: int | None,
    fields: tuple[str, ...] | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
) -> None:
    """Transform each input item with a prompt. One item in, one result out.

    \b
    Examples:
      echo "hello" | smartpipe map "translate to Spanish"
      cat reviews.jsonl | smartpipe map "Extract {product, sentiment}"
      smartpipe map "Summarize this document" --in 'reports/*.pdf'
      smartpipe map "What does the caller want?" --in 'calls/*.mp3'

    You usually need NO flags: braces in the prompt name the JSON fields you
    want back; plain prompts return plain text; and media is first-class —
    images, audio, video, and the figures inside PDFs go to the model natively
    when it supports them, converted (and disclosed) when it doesn't.

    Everything else is opt-in refinement: schemas when braces aren't enough,
    --tally/--explode/--fields to shape output, --max-calls to cap spend.
    """
    request = MapRequest(
        prompt=resolve_prompt(prompt, prompt_file),
        schema_path=schema_path,
        schema_dsl=schema_dsl,
        tally_field=tally_field,
        explode_field=explode_field,
        frame_every=frame_every,
        max_frames=max_frames,
        model_flag=model_flag,
        output=OutputFormat(output),
        dry_run=dry_run,
        keep_invalid=keep_invalid,
        concurrency_flag=concurrency_flag,
        input=input_spec(in_patterns, from_files=from_files),
        fields=fields,
    )
    code = asyncio.run(_run(request, max_calls))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: MapRequest, max_calls: int | None) -> ExitCode:
    from smartpipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        code = await run_map(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
        return settle_budget(container.budget, code)
