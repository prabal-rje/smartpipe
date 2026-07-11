"""``smartpipe distinct`` — fold near-duplicates; keep the first of each."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from smartpipe.cli.completions import complete_embed_models
from smartpipe.cli.input_options import (
    input_options,
    input_spec,
    ocr_model_option,
    positional_paths,
)
from smartpipe.cli.interrupts import graceful_interrupts
from smartpipe.cli.manifest_option import begin_manifest, manifest_option, settled
from smartpipe.core.errors import ExitCode
from smartpipe.verbs.distinct import DistinctRequest, run_distinct

__all__ = ["distinct_command"]


@click.command(name="distinct")
@click.argument("paths", nargs=-1, required=False)
@click.option("--show-groups", is_flag=True, help="Emit group records instead (audit the folds).")
@click.option(
    "--threshold",
    type=float,
    default=0.90,
    show_default=True,
    help="Cosine similarity at which two items are the same thing.",
)
@click.option(
    "--embed-model",
    "model_flag",
    shell_complete=complete_embed_models,
    help="Embedding model for this run.",
)
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel model calls.")
@click.option(
    "--max-calls",
    "max_calls",
    type=int,
    help="Stop after N billable units (model calls; dedicated OCR pages).",
)
@manifest_option
@click.option(
    "--allow-captions",
    "allow_captions",
    is_flag=True,
    help="Let a CLOUD model convert images/audio to text (paid; local models do it free).",
)
@ocr_model_option
@input_options
@click.option(
    "--exact",
    "exact",
    is_flag=True,
    help="Fold byte-identical items only — the hash rung, zero model calls.",
)
def distinct_command(
    exact: bool,
    manifest_path: Path | None,
    show_groups: bool,
    threshold: float,
    model_flag: str | None,
    concurrency_flag: int | None,
    max_calls: int | None,
    allow_captions: bool,
    ocr_model_flag: str | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
    as_mode: str | None,
    strict_rows: bool,
    paths: tuple[str, ...],
) -> None:
    """Fold near-duplicate items — the same thing worded differently is one item.

    \b
    Examples:
      cat tickets.txt | smartpipe distinct > unique.txt
      cat alerts.jsonl | smartpipe distinct --show-groups   # audit what folded
      smartpipe distinct < candidates.jsonl > train-clean.jsonl

    Exact duplicates fold for free (no model calls); the rest are embedded
    once and grouped by meaning. First occurrence wins; output keeps input
    order and bytes. The receipt on stderr says exactly what was folded.

    Run distinct BEFORE map/filter: every duplicate you fold is a model call
    you don't pay for downstream.
    """
    request = DistinctRequest(
        exact=exact,
        show_groups=show_groups,
        threshold=threshold,
        model_flag=model_flag,
        concurrency_flag=concurrency_flag,
        allow_captions=allow_captions,
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
    request: DistinctRequest, max_calls: int | None, manifest_path: Path | None
) -> ExitCode:
    from dataclasses import replace

    from smartpipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        if not request.allow_captions and container.config.allow_captions:
            request = replace(request, allow_captions=True)  # profile consent (D35)
        begin_manifest(manifest_path, verb="distinct")
        return await settled(
            run_distinct(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop),
            container.budget,
        )
