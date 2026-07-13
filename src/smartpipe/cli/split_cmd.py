"""``smartpipe split`` — break oversized items into chunk items. No model calls
(a configured ocr-model at ingestion is the one exception - item 48)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from smartpipe.cli.input_options import (
    input_options,
    input_spec,
    ocr_model_option,
    positional_paths,
)
from smartpipe.cli.interrupts import graceful_interrupts
from smartpipe.cli.manifest_option import begin_manifest, manifest_option, settled
from smartpipe.core.errors import ExitCode
from smartpipe.verbs.split import SplitRequest, run_split

__all__ = ["split_command"]


@click.command(name="split")
@click.argument("paths", nargs=-1, required=False)
@click.option(
    "--by",
    "by_flag",
    metavar="UNIT[:N]",
    help="Split unit: tokens, pages, minutes, seconds. e.g. --by pages, --by minutes:10",
)
@click.option(
    "--media",
    "media",
    is_flag=True,
    help="Extract images embedded in PDFs/DOCX/PPTX/XLSX as items (icons dropped).",
)
@click.option(
    "--max-tokens",
    "max_tokens",
    type=int,
    help="Shorthand for --by tokens:N (default 2000).",
)
@ocr_model_option
@click.option(
    "--max-calls",
    "max_calls",
    type=int,
    help="Stop after N billable units (model calls; dedicated OCR pages).",
)
@manifest_option
@input_options
def split_command(
    by_flag: str | None,
    media: bool,
    max_tokens: int | None,
    ocr_model_flag: str | None,
    max_calls: int | None,
    manifest_path: Path | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
    as_mode: str | None,
    strict_rows: bool,
    paths: tuple[str, ...],
) -> None:
    """Break oversized items into budget-sized chunks. Free — no model calls
    UNLESS an ocr-model is configured (then PDFs and images parse through it,
    disclosed per row; --max-calls caps that spend).

    \b
    Examples:
      smartpipe split '10k-filings/*.pdf' | smartpipe map "list the risk factors {risk}"
      smartpipe split --by pages:5 report.pdf | smartpipe map "summarize these pages"
      smartpipe split --by minutes:10 call.mp3 | smartpipe map "what was agreed?"

    Each chunk is a JSON record: {"text": …, "source": "report.pdf §3/12"} —
    paragraph-boundary aware, and the chunks of a document concatenate back to
    its exact text. Recombine downstream with reduce.
    """
    request = SplitRequest(
        max_tokens_flag=max_tokens,
        by_flag=by_flag,
        media=media,
        ocr_model_flag=ocr_model_flag,
        input=input_spec(
            positional_paths(paths, in_patterns),
            from_files=from_files,
            as_mode=as_mode,
            strict_rows=strict_rows,
        ),
    )
    code = asyncio.run(_run(request, max_calls, manifest_path))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(
    request: SplitRequest, max_calls: int | None, manifest_path: Path | None
) -> ExitCode:
    from smartpipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        begin_manifest(manifest_path, verb="split")
        return await settled(
            run_split(
                request,
                container,
                stdin=sys.stdin,
                stdout=sys.stdout,
                stop=stop,
                budget=container.budget,
            ),
            container.budget,
        )
