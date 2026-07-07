"""``smartpipe where`` — the free filter. Runs before anything paid."""

from __future__ import annotations

import sys

import click

from sempipe.core.errors import ExitCode
from sempipe.verbs.where import WhereRequest, run_where

__all__ = ["where_command"]


@click.command(name="where")
@click.argument("predicate")
def where_command(predicate: str) -> None:
    """Keep rows matching a deterministic predicate. Free — never calls a model.

    \b
    Examples:
      tail -f app.log | smartpipe where 'text has "ERROR"'
      cat orders.jsonl | smartpipe where 'total > 1000'
      smartpipe where 'level == "error" and not text contains "retry"'

    \b
    Operators: has (word, case-insensitive) · contains · matches /re/
               == != > >= < <=   combined with and · or · not · ( )
    FIELD is a record field, or text for the whole line.

    Put where BEFORE filter/map: it cuts the corpus for free, so the paid
    stages only see what matters. Semantic condition? Use filter.
    """
    code = run_where(WhereRequest(predicate), stdin=sys.stdin, stdout=sys.stdout)
    if code is not ExitCode.OK:  # pragma: no cover — where currently always OKs
        raise SystemExit(int(code))
