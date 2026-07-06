"""``sempipe extend`` — your record, plus columns."""

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
from sempipe.verbs.extend import ExtendRequest, run_extend

__all__ = ["extend_command"]


@click.command(name="extend")
@click.argument("prompt", required=False)
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
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel model calls.")
@click.option("--max-calls", "max_calls", type=int, help="Stop after N model calls (cost cap).")
@fields_option
@input_options
def extend_command(
    prompt: str | None,
    prompt_file: Path | None,
    schema_path: Path | None,
    schema_dsl: str | None,
    tally_field: str | None,
    explode_field: str | None,
    model_flag: str | None,
    output: str,
    concurrency_flag: int | None,
    max_calls: int | None,
    fields: tuple[str, ...] | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
) -> None:
    """Add extracted fields to each record — everything it had survives.

    \b
    Before:  {"id": 812, "body": "app crashes when saving"}
    $ cat tickets.jsonl | sempipe extend "Add {sentiment enum(pos, neg, neutral), product string}"
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
        model_flag=model_flag,
        output=OutputFormat(output),
        concurrency_flag=concurrency_flag,
        fields=fields,
        input=input_spec(in_patterns, from_files=from_files),
    )
    code = asyncio.run(_run(request, max_calls))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: ExtendRequest, max_calls: int | None) -> ExitCode:
    from sempipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        code = await run_extend(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
        return settle_budget(container.budget, code)
