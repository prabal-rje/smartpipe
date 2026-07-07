"""``smartpipe sort`` — order records by a field, without the jq incantation."""

from __future__ import annotations

import sys

import click

from sempipe.core.errors import ExitCode
from sempipe.verbs.sortverb import SortRequest, run_sort

__all__ = ["sort_command"]


@click.command(name="sort")
@click.option("--by", "by", required=True, metavar="FIELD", help="The field to order by.")
@click.option("--desc", "descending", is_flag=True, help="Largest / Z-first.")
def sort_command(by: str, descending: bool) -> None:
    """Order records by a field. Free — never calls a model. Reads the whole input.

    \b
    Examples:
      cat scored.ndjson | smartpipe sort --by _score --desc | head -5
      smartpipe map "…{confidence number}" --in 'docs/*.pdf' | smartpipe sort --by confidence --desc

    Numbers sort numerically, strings lexically (numbers first when mixed);
    rows missing the field always land LAST, in both directions, with a
    note. Stable: ties keep input order. Rows pass through byte-for-byte.
    (There is no `take` — `head` already counts NDJSON rows.)
    """
    code = run_sort(SortRequest(by=by, descending=descending), stdin=sys.stdin, stdout=sys.stdout)
    if code is not ExitCode.OK:  # pragma: no cover — sort always OKs
        raise SystemExit(int(code))
