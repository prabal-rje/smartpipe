"""``smartpipe summarize`` — the numbers, without leaving for awk."""

from __future__ import annotations

import sys

import click

from smartpipe.core.errors import ExitCode
from smartpipe.verbs.summarize import SummarizeRequest, run_summarize

__all__ = ["summarize_command"]


@click.command(name="summarize")
@click.argument("expression")
def summarize_command(expression: str) -> None:
    """Aggregate records deterministically. Free — never calls a model.

    \b
    Examples:
      cat orders.jsonl | smartpipe summarize 'count(), avg(total), p95(total) by region'
      cat evals.jsonl  | smartpipe summarize 'count() by pass'
      ... | smartpipe summarize 'dcount(user) by day'

    \b
    Aggregations: count() · sum(f) · avg(f) · min(f) · max(f)
                  p50(f) · p90(f) · p95(f) · p99(f) · dcount(f)

    KQL's own grammar and output naming (count, avg_total, p95_total).
    Groups sort largest first; a missing group field groups under null,
    visibly. Non-numeric values in numeric aggregations are skipped and
    counted on stderr — never a mid-stream crash.
    """
    code = run_summarize(SummarizeRequest(expression), stdin=sys.stdin, stdout=sys.stdout)
    if code is not ExitCode.OK:  # pragma: no cover — summarize always OKs
        raise SystemExit(int(code))
