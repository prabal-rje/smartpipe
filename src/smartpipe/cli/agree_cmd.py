"""``smartpipe agree`` - inter-rater agreement between two label files."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from smartpipe.core.errors import ExitCode
from smartpipe.io.writers import OutputFormat
from smartpipe.verbs.agree import AgreeRequest, run_agree

__all__ = ["agree_command"]


@click.command(name="agree")
@click.argument("file_a", metavar="A", type=click.Path(path_type=Path))
@click.argument("file_b", metavar="B", type=click.Path(path_type=Path))
@click.option(
    "--on",
    "on_field",
    metavar="FIELD",
    help="Align rows by this shared key (default: row order; unequal counts then fault).",
)
@click.option(
    "--label",
    "label_field",
    default="label",
    show_default=True,
    metavar="FIELD",
    help="The compared value on each row.",
)
@click.option(
    "--output",
    type=click.Choice([fmt.value for fmt in OutputFormat]),
    default=OutputFormat.AUTO.value,
    show_default=True,
    help="Output format.",
)
def agree_command(
    file_a: Path, file_b: Path, on_field: str | None, label_field: str, output: str
) -> None:
    """How much do two annotators agree? Free - never calls a model.

    \b
    Examples:
      smartpipe agree rater1.jsonl rater2.jsonl --on id
      smartpipe agree model.jsonl gold.jsonl --on id --label sentiment

    Compares the --label value of rows aligned by --on (or by row order).
    Output: one summary record - observed agreement, Cohen's kappa,
    Krippendorff's alpha (nominal) - then the confusion matrix as records.
    Rows missing the key or the label are excluded and counted on stderr.
    When only one label class appears, kappa and alpha are mathematically
    undefined: they come back null, with a note saying why.
    """
    request = AgreeRequest(
        file_a=file_a,
        file_b=file_b,
        on=on_field,
        label=label_field,
        output=OutputFormat(output),
    )
    code = run_agree(request, stdout=sys.stdout)
    if code is not ExitCode.OK:  # pragma: no cover - agree always OKs or faults
        raise SystemExit(int(code))
