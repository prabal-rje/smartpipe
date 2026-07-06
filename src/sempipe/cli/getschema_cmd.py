"""``sempipe getschema`` — what fields does this stream even have?"""

from __future__ import annotations

import sys

import click

from sempipe.core.errors import ExitCode
from sempipe.verbs.getschema import GetSchemaRequest, run_getschema

__all__ = ["getschema_command"]


@click.command(name="getschema")
@click.option("--all", "scan_all", is_flag=True, help="Scan every row (default: first 10,000).")
def getschema_command(scan_all: bool) -> None:
    """Report the stream's fields, types, and coverage. Free — never calls a model.

    \b
    Examples:
      cat data.jsonl | sempipe getschema
      sempipe getschema --all < big.jsonl

    A table on a terminal, NDJSON when piped. Mixed types show as unions
    (string|number) — that's the dirt worth seeing. Plain-text input gets a
    one-line answer instead of an error. The footer suggests the next move.
    """
    code = run_getschema(GetSchemaRequest(scan_all=scan_all), stdin=sys.stdin, stdout=sys.stdout)
    if code is not ExitCode.OK:  # pragma: no cover — getschema always OKs
        raise SystemExit(int(code))
