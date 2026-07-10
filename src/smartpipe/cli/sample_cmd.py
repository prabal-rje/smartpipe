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
@click.option(
    "--by",
    "by_field",
    metavar="FIELD",
    help="Stratify: proportional allocation per FIELD value (missing = a null stratum).",
)
def sample_command(count: int, seed: int, by_field: str | None) -> None:
    """Keep N random rows — seeded, reproducible. Free — never calls a model.

    \b
    Examples:
      cat huge.jsonl | smartpipe sample 20 | smartpipe map "Extract {label}"
      cat evals.jsonl | smartpipe sample 50 --seed 7 > eval-subset.jsonl
      cat labeled.jsonl | smartpipe sample 100 --by label > eval-set.jsonl

    Deterministic BY DEFAULT: the same input gives the same sample with no
    flags, so prompt comparisons compare prompts (and the sample is citable).
    Unlike --max-calls (which takes the head of the stream), sample is
    representative. Output keeps input order.

    --by FIELD keeps the field's class balance: each value contributes rows
    in proportion to its share of the input (largest-remainder rounding, so
    the total is exactly N). Rows without the field sample as a null stratum.
    """
    code = run_sample(
        SampleRequest(count=count, seed=seed, by=by_field), stdin=sys.stdin, stdout=sys.stdout
    )
    if code is not ExitCode.OK:  # pragma: no cover — sample always OKs
        raise SystemExit(int(code))
