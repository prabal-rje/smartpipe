"""``--manifest PATH`` (item 65a): the shared flag + run-end settling.

Every model verb wears the same option and the same two calls: ``begin_manifest``
arms the collector before any spend, ``settled`` awaits the verb, settles the
--max-calls belt, and writes the manifest on every exit path that produced
results - normal returns (ok/partial/all-failed/interrupted-after-drain) and
the >50% failure halt. Faults that abort before results (setup/usage) leave
no manifest: there was no run to record.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from smartpipe.cli.interrupts import settle_budget
from smartpipe.core.errors import ExitCode, TooManyFailures

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from smartpipe.models.budget import CallBudget

__all__ = ["begin_manifest", "manifest_option", "settled"]


manifest_option = click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(path_type=Path),
    help="Write a JSON run manifest (the citable methods-section record) to PATH at run end.",
)


def begin_manifest(path: Path | None, *, verb: str, prompt: str | None = None) -> None:
    """Arm the run's manifest collector (no-op when the flag wasn't given)."""
    if path is None:
        return
    from smartpipe.io import manifest

    manifest.begin(path, verb=verb, argv=tuple(sys.argv[1:]), prompt=prompt)


async def settled(work: Awaitable[ExitCode], budget: CallBudget | None) -> ExitCode:
    """Await the verb, settle the belt, write the manifest, return the code."""
    from smartpipe.io import manifest

    try:
        code = await work
    except TooManyFailures as halt:
        # results streamed before the halt - record what the halt knows
        manifest.record_counts(done=halt.total - halt.failed, skipped=halt.failed)
        manifest.finish(ExitCode.ALL_FAILED)
        raise
    code = settle_budget(budget, code)
    manifest.finish(code)
    return code
