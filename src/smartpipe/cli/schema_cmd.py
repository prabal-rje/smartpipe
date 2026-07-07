"""``smartpipe schema`` — rung 4 of the ladder (D22): English → a validated schema file.

Exactly one drafting call plus at most one repair. The draft is validated against
the JSON-Schema meta-schema before stdout sees a byte: an invalid draft is exit 3
with the attempt on stderr and **stdout empty** — a broken schema silently piped
into the next run is the disaster case this command exists to prevent.
"""

from __future__ import annotations

import asyncio
import json
import os

import click

from smartpipe.cli.completions import complete_chat_models
from smartpipe.core.errors import ExitCode, ItemError
from smartpipe.engine.prompts import build_repair_request, build_schema_request
from smartpipe.engine.schema import parse_schema_draft
from smartpipe.io import diagnostics

__all__ = ["schema_command"]

_FAILED_SCREEN = (
    "error: the model couldn't produce a valid JSON Schema (after one repair)\n"
    "  Its last attempt is above on stderr. Try rephrasing, or write the file by hand:\n"
    "  docs/concepts/structured-output.md"
)


@click.command(name="schema")
@click.argument("description")
@click.option(
    "--model",
    "model_flag",
    shell_complete=complete_chat_models,
    help="Model to draft with (one call + at most one repair).",
)
def schema_command(description: str, model_flag: str | None) -> None:
    """Draft a JSON Schema from an English description, validated before output.

    \b
    Examples:
      smartpipe schema "invoice with vendor string, total number, status paid/unpaid" > invoice.json
      cat receipts.txt | smartpipe map "Extract the fields" --schema invoice.json

    The draft is checked against the JSON-Schema meta-schema; a failed draft
    exits 3 with NOTHING on stdout, so a broken schema can never slip into a pipe.
    """
    code = asyncio.run(_run(description, model_flag))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(description: str, model_flag: str | None) -> ExitCode:
    from smartpipe.container import build_container

    async with build_container(os.environ) as container:
        model = await container.chat_model(model_flag)
        request = build_schema_request(description)
        reply = await model.complete(request)
        try:
            draft = parse_schema_draft(reply)
        except ItemError as first_error:
            repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
            reply = await model.complete(repair)
            try:
                draft = parse_schema_draft(reply)
            except ItemError:
                diagnostics.warn(f"the model's last draft attempt:\n{reply}")
                diagnostics.report_error(_FAILED_SCREEN)
                return ExitCode.ALL_FAILED
    click.echo(json.dumps(draft, indent=2, ensure_ascii=False))
    return ExitCode.OK
