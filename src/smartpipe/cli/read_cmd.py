"""Reader mode (item 16): the binary IS the reader.

``smartpipe PATH…`` — a first argument that is no verb but exists on disk —
emits the files' items to stdout as JSONL records, cut per the ``--as`` dial,
each carrying its ``__`` spine (``__source``, ``__media``). Zero model calls
by default — this is ingestion made visible, the front half of the read/write
mirror. The ONE exception (item 48, owner: "do what the user configured"): a
configured ``ocr-model`` parses PDF/image crates exactly like the ingesting
verbs do, each use disclosed per row, cappable with ``--max-calls``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from smartpipe.cli.input_options import ocr_model_option
from smartpipe.cli.interrupts import graceful_interrupts
from smartpipe.cli.manifest_option import begin_manifest, manifest_option, settled
from smartpipe.core.errors import ExitCode
from smartpipe.io.inputs import InputSpec
from smartpipe.io.items import item_record

__all__ = ["read_command"]


@click.command(name="read", hidden=True)
@click.argument("paths", nargs=-1, required=True)
@click.option(
    "--bare",
    "bare",
    is_flag=True,
    help="Strip __ metadata fields from the emitted records.",
)
@click.option(
    "--as",
    "as_mode",
    type=click.Choice(["file", "lines", "jsonl", "csv"]),
    default=None,
    help="Cut granularity: file = one item per file; lines = text rows; "
    "jsonl = strict records; csv = header-named rows.",
)
@ocr_model_option
@click.option(
    "--max-calls",
    "max_calls",
    type=int,
    help="Stop after N billable units (model calls; dedicated OCR pages).",
)
@manifest_option
def read_command(
    paths: tuple[str, ...],
    as_mode: str | None,
    bare: bool,
    ocr_model_flag: str | None,
    max_calls: int | None,
    manifest_path: Path | None,
) -> None:
    """Emit the named files' items as JSONL records (reader mode).

    \b
    Examples:
      smartpipe report.pdf                     # one record: the whole document
      smartpipe notes.txt --as lines           # one record per line
      smartpipe 'logs/*.jsonl'                 # strict records, per row
      smartpipe export.csv                     # header-named records, per row

    Reading is free - zero model calls - UNLESS an ocr-model is configured
    (config, SMARTPIPE_OCR_MODEL, or --ocr-model): then PDFs and images parse
    through it, exactly as the ingesting verbs would, each use disclosed on
    stderr. --max-calls caps that spend.
    """
    spec = InputSpec(patterns=paths, from_files=False, as_mode=as_mode)
    code = asyncio.run(_run(spec, bare, ocr_model_flag, max_calls, manifest_path))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(
    spec: InputSpec,
    bare: bool,
    ocr_flag: str | None,
    max_calls: int | None,
    manifest_path: Path | None,
) -> ExitCode:
    import os

    from smartpipe.container import build_container
    from smartpipe.io import diagnostics, readers, source_accounting
    from smartpipe.io.writers import RenderMode, WriterConfig, make_writer
    from smartpipe.verbs.common import interrupted_exit_code, outcome_exit_code

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        begin_manifest(manifest_path, verb="read")

        async def consume() -> ExitCode:
            log = diagnostics.DegradationLog()
            ocr = readers.OcrIngest.lazy(lambda: container.document_parser(ocr_flag), log)
            # the >20-pages preflight note fires inside resolve_items (item 48) —
            # one machinery, every verb, reader mode included
            items, _total = readers.resolve_items(spec, sys.stdin, stop=stop, ocr=ocr)
            # records for machines, always — reader mode's whole output IS the record
            writer = make_writer(
                WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, bare=bare),
                sys.stdout,
            )
            produced = 0
            sources = source_accounting.SourceCounter()
            try:
                async for item in items:
                    writer.write_record(item_record(item))
                    produced += 1
                    sources.done(item.source)
            finally:
                writer.flush()
                log.finish()
            counts = sources.counts
            if stop.is_set():
                diagnostics.interrupted_summary(processed=produced, skipped=counts.skipped)
                return interrupted_exit_code(
                    done=counts.succeeded,
                    skipped=counts.skipped,
                    failed=counts.failed,
                )
            return outcome_exit_code(
                done=counts.succeeded,
                skipped=counts.skipped,
                failed=counts.failed,
            )

        return await settled(consume(), container.budget)
