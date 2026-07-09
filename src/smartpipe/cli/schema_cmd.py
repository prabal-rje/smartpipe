"""``smartpipe schema`` — the schema ladder's free rungs, plus rung 4 (D22).

A braces or DSL expression compiles deterministically: zero model calls, zero
I/O beyond the arguments (and the ``--check`` file). A plain-English
description still drafts with a model — exactly one call plus at most one
repair, validated against the JSON-Schema meta-schema before stdout sees a
byte: an invalid draft is exit 3 with the attempt on stderr and **stdout
empty** — a broken schema silently piped into the next run is the disaster
case this command exists to prevent.

Bare ``smartpipe schema`` at a TTY opens the interactive workshop
(``cli/schema_workshop``); bare with piped stdin keeps the line-per-expression
quasi-REPL below.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from smartpipe.cli.completions import complete_chat_models
from smartpipe.core.errors import ExitCode, ItemError, UsageFault
from smartpipe.engine.prompts import (
    build_repair_request,
    build_schema_request,
    parse_prompt,
    plan_map,
)
from smartpipe.engine.schema import example_instance, parse_schema_draft
from smartpipe.engine.schema_dsl import dsl_to_schema, type_token
from smartpipe.io import diagnostics

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["schema_command"]

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_CHECK_FAILURE_CAP = 5  # first N failing rows verbatim, then the tally

_FAILED_SCREEN = (
    "error: the model couldn't produce a valid JSON Schema (after one repair)\n"
    "  Its last attempt is above on stderr. Try rephrasing, or write the file by hand:\n"
    "  docs/concepts/structured-output.md"
)

_DETERMINISTIC_ONLY = (
    "--check and --example need a deterministic expression (braces or the DSL)\n"
    "  A model-drafted schema can change between runs — never gate a dataset on one.\n"
    "  Example: smartpipe schema '{vendor string, total number}' --check data.jsonl"
)

_ONE_QUESTION = (
    "--check and --example are different questions — ask one\n"
    "  --check validates a file against the schema; --example shows one instance of it."
)

_NO_BRACE_GROUP = (
    "the expression has no {field} group to compile\n"
    "  {{ and }} are literal braces. Name fields: {vendor string, total number}"
)


@click.command(name="schema")
@click.argument("description", required=False)
@click.option(
    "--check",
    "check_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    metavar="FILE",
    help="Validate FILE's JSONL rows against the schema; exit 1 if any fail.",
)
@click.option(
    "--example",
    "example",
    is_flag=True,
    help="Print one synthetic instance that validates (deterministic).",
)
@click.option(
    "--model",
    "model_flag",
    shell_complete=complete_chat_models,
    help="Model to draft an English description with (one call + at most one repair).",
)
def schema_command(
    description: str | None,
    check_path: Path | None,
    example: bool,
    model_flag: str | None,
) -> None:
    """Turn a field description into a JSON Schema, and put it to work.

    \b
    Examples:
      smartpipe schema '{vendor string: legal name, total number}' > invoice.json
      smartpipe schema 'vendor string; total number >= 0' --check data.jsonl
      smartpipe schema '{status enum(todo, done)}' --example
      echo '{vendor, total}' | smartpipe schema            # lines in, schemas out
      smartpipe schema                                     # at a terminal: the workshop

    Braces and the --schema-from DSL compile deterministically — free, instant,
    no model call. A plain-English description drafts with a model instead
    (one call + at most one repair), checked against the JSON-Schema
    meta-schema; a failed draft exits 3 with NOTHING on stdout, so a broken
    schema can never slip into a pipe.
    """
    if check_path is not None and example:
        raise UsageFault(_ONE_QUESTION)
    if description is None:
        if check_path is not None or example:
            raise UsageFault(_DETERMINISTIC_ONLY)
        if sys.stdin.isatty():
            from smartpipe.cli.schema_workshop import workshop_entry

            raise SystemExit(int(workshop_entry()))
        raise SystemExit(int(_repl(sys.stdin)))
    if _is_expression(description):
        code = _run_free(_compile_expression(description), check_path, example=example)
    elif check_path is not None or example:
        raise UsageFault(_DETERMINISTIC_ONLY)
    else:
        code = asyncio.run(_draft(description, model_flag))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


def _is_expression(text: str) -> bool:
    """The deterministic-compile signal: braces anywhere, a semicolon-joined
    field list, or a leading ``ident type`` pair from the shared vocabulary.
    Everything else reads as English and goes to the drafting model."""
    stripped = text.strip()
    if "{" in stripped or ";" in stripped:
        return True
    name, _, rest = stripped.partition(" ")
    rest = rest.strip()
    if not _IDENT.match(name) or not rest:
        return False
    if rest.startswith("enum("):
        return True
    return type_token(rest.split(" ", 1)[0]) is not None


def _compile_expression(text: str) -> dict[str, object]:
    """Braces or DSL → schema; bad grammar dies on the existing UsageFault
    screens (D37/D22) — free, before anything could cost money."""
    if "{" in text:
        tokens = parse_prompt(text, allow_descriptions=True)
        plan = plan_map(tokens, schema=None)
        if plan.schema is None:  # only {{ }} escapes — nothing to compile
            raise UsageFault(_NO_BRACE_GROUP)
        return dict(plan.schema)
    return dsl_to_schema(text)


def _run_free(schema: dict[str, object], check_path: Path | None, *, example: bool) -> ExitCode:
    if check_path is not None:
        return _check_rows(schema, check_path)
    payload = example_instance(schema) if example else schema
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    return ExitCode.OK


def _check_rows(schema: dict[str, object], path: Path) -> ExitCode:
    """Validate a JSONL file: first failures verbatim (capped), one tally line,
    exit 0 only when every row passes."""
    total = 0
    failed = 0
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            total += 1
            message = _row_error(schema, line)
            if message is None:
                continue
            failed += 1
            if failed <= _CHECK_FAILURE_CAP:
                diagnostics.warn(f"row {number}: {message}")
            elif failed == _CHECK_FAILURE_CAP + 1:
                diagnostics.warn("more failures follow (suppressed; the tally lands at the end)")
    verdict = f"schema check: {total - failed} of {total} rows pass"
    diagnostics.note(verdict if failed == 0 else f"{verdict} ({failed} failed)")
    return ExitCode.OK if failed == 0 else ExitCode.PARTIAL


def _row_error(schema: dict[str, object], line: str) -> str | None:
    import jsonschema  # function-local: --help must not pay for the validator stack

    try:
        parsed: object = json.loads(line)
    except json.JSONDecodeError as exc:
        return f"not JSON ({exc.msg})"
    try:
        jsonschema.validate(parsed, schema)
    except jsonschema.ValidationError as exc:
        return exc.message
    return None


def _repl(lines: Iterable[str]) -> ExitCode:
    """Piped stdin, no argument: each line is a braces/DSL expression — the
    quasi-REPL. A bad line prints its screen and the run keeps going; any
    failure marks the exit code."""
    failures = 0
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            compiled = _compile_expression(text)
        except UsageFault as fault:
            diagnostics.report_error(str(fault))
            failures += 1
            continue
        click.echo(json.dumps(compiled, indent=2, ensure_ascii=False))
    return ExitCode.OK if failures == 0 else ExitCode.USAGE


async def _draft(description: str, model_flag: str | None) -> ExitCode:
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
