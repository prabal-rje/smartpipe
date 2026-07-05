"""``sempipe embed`` — convert items to vector embeddings."""

from __future__ import annotations

import asyncio
import os
import sys

import click

from sempipe.container import build_container
from sempipe.core.errors import ExitCode
from sempipe.verbs.embed import EmbedRequest, run_embed

__all__ = ["embed_command"]


@click.command(name="embed")
@click.option("--embed-model", "model_flag", help="Embedding model (e.g. nomic-embed-text).")
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel model calls.")
def embed_command(model_flag: str | None, concurrency_flag: int | None) -> None:
    """Convert each item to a vector embedding (NDJSON out).

    \b
    Examples:
      cat docs/*.md | sempipe embed > corpus.embeddings
      echo "senior Python engineer" | sempipe embed

    This is the only command that never touches a chat model — it uses the
    embedding model, and exists to feed 'top_k'.
    """
    request = EmbedRequest(model_flag=model_flag, concurrency_flag=concurrency_flag)
    code = asyncio.run(_run(request))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: EmbedRequest) -> ExitCode:
    async with build_container(os.environ) as container:
        return await run_embed(request, container, stdin=sys.stdin, stdout=sys.stdout)
