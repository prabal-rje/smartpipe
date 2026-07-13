"""``smartpipe diff`` — what distinguishes two sets of items."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from smartpipe.cli.completions import complete_chat_models, complete_embed_models
from smartpipe.cli.input_options import ocr_model_option
from smartpipe.cli.interrupts import graceful_interrupts
from smartpipe.cli.manifest_option import begin_manifest, manifest_option, settled
from smartpipe.core.errors import ExitCode
from smartpipe.verbs.diff import DiffRequest, run_diff

__all__ = ["diff_command"]


@click.command(name="diff")
@click.option(
    "--right",
    "right",
    type=click.Path(path_type=Path),
    required=True,
    help="The comparison set (JSONL or plain lines). Left is stdin.",
)
@click.option("--top", type=int, help="Show at most N discriminating themes.")
@click.option("--all", "show_all", is_flag=True, help="Also show themes shared by both sides.")
@click.option(
    "--model",
    "model_flag",
    shell_complete=complete_chat_models,
    help="Chat model for theme labels.",
)
@click.option(
    "--embed-model",
    "embed_flag",
    shell_complete=complete_embed_models,
    help="Embedding model for grouping.",
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
def diff_command(
    right: Path,
    manifest_path: Path | None,
    top: int | None,
    show_all: bool,
    model_flag: str | None,
    embed_flag: str | None,
    concurrency_flag: int | None,
    max_calls: int | None,
    allow_captions: bool,
    ocr_model_flag: str | None,
) -> None:
    """Semantic diff of two item SETS — not a line diff.

    \b
    Examples:
      smartpipe diff --right errors-before.log < errors-during.log
      smartpipe diff --right outputs-v1.jsonl < outputs-v2.jsonl
      smartpipe diff --right v1-train.jsonl < v2-train.jsonl   # dataset drift

    Embeds both sides, groups the union by meaning, and reports the themes
    that over-index on one side — with both shares shown as evidence and
    examples from the dominant side. Balanced themes are omitted (a note
    says how many; --all shows them): the answer to "what's different"
    shouldn't bury you in what's the same.
    """
    request = DiffRequest(
        right=right,
        top=top,
        show_all=show_all,
        model_flag=model_flag,
        embed_flag=embed_flag,
        concurrency_flag=concurrency_flag,
        allow_captions=allow_captions,
        ocr_model_flag=ocr_model_flag,
    )
    code = asyncio.run(_run(request, max_calls, manifest_path))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: DiffRequest, max_calls: int | None, manifest_path: Path | None) -> ExitCode:
    from dataclasses import replace

    from smartpipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        if not request.allow_captions and container.config.allow_captions:
            request = replace(request, allow_captions=True)  # profile consent (D35)
        begin_manifest(manifest_path, verb="diff")
        return await settled(
            run_diff(
                request,
                container,
                stdin=sys.stdin,
                stdout=sys.stdout,
                stop=stop,
                budget=container.budget,
            ),
            container.budget,
        )
