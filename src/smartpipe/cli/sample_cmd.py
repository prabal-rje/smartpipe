"""``smartpipe sample`` — the same N random rows, every run."""

from __future__ import annotations

import sys

import click

from smartpipe.core.errors import ExitCode
from smartpipe.verbs.sample import SampleRequest, run_sample

__all__ = ["sample_command"]


@click.command(name="sample")
@click.argument("count", type=int)
@click.option(
    "--seed", type=int, default=0, show_default=True, help="Vary the (still reproducible) sample."
)
def sample_command(count: int, seed: int) -> None:
    """Keep N random rows — seeded, reproducible. Free — never calls a model.

    \b
    Examples:
      cat huge.jsonl | smartpipe sample 20 | smartpipe map "Extract {label}"
      cat evals.jsonl | smartpipe sample 50 --seed 7 > eval-subset.jsonl

    Deterministic BY DEFAULT: the same input gives the same sample with no
    flags, so prompt comparisons compare prompts (and the sample is citable).
    Unlike --max-calls (which takes the head of the stream), sample is
    representative. Output keeps input order.
    """
    code = run_sample(SampleRequest(count=count, seed=seed), stdin=sys.stdin, stdout=sys.stdout)
    if code is not ExitCode.OK:  # pragma: no cover — sample always OKs
        raise SystemExit(int(code))
