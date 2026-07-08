"""``smartpipe extend`` — your record, plus columns."""

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
    positional_paths,
    resolve_prompt,
)
from smartpipe.cli.interrupts import graceful_interrupts, settle_budget
from smartpipe.core.errors import ExitCode
from smartpipe.io.writers import OutputFormat
from smartpipe.verbs.extend import ExtendRequest, run_extend

__all__ = ["extend_command"]


@click.command(name="extend")
@click.argument("prompt", required=False)
@click.argument("paths", nargs=-1, required=False)
@click.option(
    "--prompt-file",
    "prompt_file",
    type=click.Path(path_type=Path),
    help="Prompt from a file (@file works too).",
)
@click.option(
    "--schema",
    "schema_path",
    type=click.Path(path_type=Path),
    help="Shape the ADDED fields with a JSON Schema.",
)
@click.option(
    "--schema-from",
    "schema_dsl",
    metavar="DSL",
    help='Added fields from a mini-DSL: "vendor string; total number >= 0".',
)
@click.option("--tally", "tally_field", metavar="FIELD", help="Count FIELD's values (stderr).")
@click.option(
    "--explode",
    "explode_field",
    metavar="FIELD",
    help="One row per element of a list-valued FIELD (original fields ride along).",
)
@click.option(
    "--model", "model_flag", shell_complete=complete_chat_models, help="Model for this run."
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
    "--full",
    "full",
    is_flag=True,
    help="Terminal preview: show whole values (no truncation).",
)
@click.option(
    "--bare",
    "bare",
    is_flag=True,
    help="Strip __ metadata fields from record output (for > redirections).",
)
@click.option(
    "--fallback-model",
    "fallback_flag",
    shell_complete=complete_chat_models,
    help="Chat model to switch to if the primary looks down (circuit breaker).",
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
def extend_command(
    prompt: str | None,
    frame_every: float | None,
    max_frames: int | None,
    prompt_file: Path | None,
    schema_path: Path | None,
    schema_dsl: str | None,
    tally_field: str | None,
    explode_field: str | None,
    model_flag: str | None,
    fallback_flag: str | None,
    bare: bool,
    full: bool,
    output: str,
    dry_run: bool,
    keep_invalid: bool,
    concurrency_flag: int | None,
    max_calls: int | None,
    fields: tuple[str, ...] | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
    as_mode: str | None,
    strict_rows: bool,
    paths: tuple[str, ...],
) -> None:
    """Add extracted fields to each record — everything it had survives.

    \b
    Before:  {"id": 812, "body": "app crashes when saving"}
    $ cat tickets.jsonl | smartpipe extend "Add {sentiment enum(pos, neg, neutral), product string}"
    After:   {"id": 812, "body": "app crashes when saving", "sentiment": "neg", "product": "app"}

    Same prompt language as map (typed braces, --schema, --schema-from), but
    the output is your record PLUS the new columns — drop it into the middle
    of an existing pipeline. Plain text lines become {"text": ..., ...}.
    Existing fields with the same name are overwritten (noted on stderr) so
    re-running enrichment stays idempotent.
    """
    request = ExtendRequest(
        prompt=resolve_prompt(prompt, prompt_file),
        schema_path=schema_path,
        schema_dsl=schema_dsl,
        tally_field=tally_field,
        explode_field=explode_field,
        frame_every=frame_every,
        max_frames=max_frames,
        model_flag=model_flag,
        fallback_flag=fallback_flag,
        bare=bare,
        full=full,
        output=OutputFormat(output),
        dry_run=dry_run,
        keep_invalid=keep_invalid,
        concurrency_flag=concurrency_flag,
        fields=fields,
        input=input_spec(
            positional_paths(paths, in_patterns), from_files=from_files, as_mode=as_mode
        ),
    )
    code = asyncio.run(_run(request, max_calls))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: ExtendRequest, max_calls: int | None) -> ExitCode:
    from smartpipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        code = await run_extend(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
        return settle_budget(container.budget, code)
