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

from smartpipe.cli.interrupts import register_hard_exit_cleanup, settle_budget
from smartpipe.core.errors import ExitCode, LateSetupFault, SourceCounts, TooManyFailures

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
    # B6: a hard exit (second Ctrl-C / watchdog escalation) runs os._exit, which
    # skips settled()'s abandon — register the temp unlink so no 0-byte
    # *.manifest.tmp leaks. Idempotent once the run finishes normally.
    register_hard_exit_cleanup(manifest.discard_reservation)


async def settled(work: Awaitable[ExitCode], budget: CallBudget | None) -> ExitCode:
    """Await the verb, settle the belt, write the manifest, return the code."""
    from smartpipe.io import manifest, source_accounting

    try:
        code = await work
    except LateSetupFault as fault:
        combined = source_accounting.settle(fault.source_counts) or fault.source_counts
        manifest.replace_counts(combined)
        manifest.finish(ExitCode.SETUP)
        raise LateSetupFault(str(fault), source_counts=combined) from None
    except TooManyFailures as halt:
        counts = halt.source_counts
        if counts is None:
            # Backward compatibility for direct callers that still carry only
            # the legacy display totals plus an optional consumed-input count.
            cancelled = halt.consumed - halt.total
            done = halt.total - halt.failed
            skipped = halt.failed + cancelled
            failed = halt.failed
        else:
            done = counts.succeeded
            skipped = counts.skipped
            failed = counts.failed
        base = SourceCounts(succeeded=done, skipped=skipped, failed=failed)
        combined = source_accounting.settle(base) or base
        manifest.replace_counts(combined)
        manifest.finish(ExitCode.ALL_FAILED)
        raise TooManyFailures(
            halt.failed,
            halt.total,
            halt.last_reason,
            source_counts=combined,
        ) from None
    except BaseException:
        source_accounting.discard()
        manifest.abandon()
        raise
    dropped = source_accounting.pending_ingestion()
    combined = source_accounting.settle()
    if combined is not None:
        manifest.replace_counts(combined)
        if dropped.total:
            code = _combined_exit(code, combined)
    code = settle_budget(budget, code)
    manifest.finish(code)
    return code


def _combined_exit(code: ExitCode, counts: SourceCounts) -> ExitCode:
    """Reconcile the verb's status with sources rejected before item creation."""
    if code in (ExitCode.ALL_FAILED, ExitCode.INTERRUPTED):
        return code
    if not counts.succeeded:
        return ExitCode.ALL_FAILED
    return ExitCode.PARTIAL
