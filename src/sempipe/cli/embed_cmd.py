"""``sempipe embed`` — convert items to vector embeddings."""

from __future__ import annotations

import asyncio
import os
import sys

import click

from sempipe.cli.completions import complete_embed_models
from sempipe.cli.input_options import fields_option, input_options, input_spec
from sempipe.cli.interrupts import graceful_interrupts, settle_budget
from sempipe.core.errors import ExitCode
from sempipe.verbs.embed import EmbedRequest, run_embed

__all__ = ["embed_command"]


@click.command(name="embed")
@click.option(
    "--embed-model",
    "model_flag",
    shell_complete=complete_embed_models,
    help="Embedding model (e.g. nomic-embed-text).",
)
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel model calls.")
@click.option("--max-calls", "max_calls", type=int, help="Stop after N model calls (cost cap).")
@fields_option
@click.option(
    "--allow-captions",
    "allow_captions",
    is_flag=True,
    help="Let a CLOUD model convert images/audio to text (paid; local models do it free).",
)
@input_options
def embed_command(
    model_flag: str | None,
    concurrency_flag: int | None,
    max_calls: int | None,
    allow_captions: bool,
    fields: tuple[str, ...] | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
) -> None:
    """Convert each item to a vector embedding (NDJSON out).

    \b
    Examples:
      cat docs/*.md | sempipe embed > corpus.embeddings
      sempipe embed --in 'docs/*.pdf' > corpus.embeddings

    This is the only command that never touches a chat model — it uses the
    embedding model, and exists to feed 'top_k'.
    """
    request = EmbedRequest(
        allow_captions=allow_captions,
        model_flag=model_flag,
        concurrency_flag=concurrency_flag,
        input=input_spec(in_patterns, from_files=from_files),
        fields=fields,
    )
    code = asyncio.run(_run(request, max_calls))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: EmbedRequest, max_calls: int | None) -> ExitCode:
    from sempipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        if not request.allow_captions and container.config.allow_captions:
            from dataclasses import replace as _replace

            request = _replace(request, allow_captions=True)  # profile consent (D35)
        code = await run_embed(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
        return settle_budget(container.budget, code)
