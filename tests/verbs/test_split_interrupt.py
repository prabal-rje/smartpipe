"""The direct media branch honors the shared drain event."""

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode
from smartpipe.io.inputs import InputSpec
from smartpipe.verbs.split import SplitRequest, run_split

if TYPE_CHECKING:
    from pathlib import Path


class _NoIoContext:
    def document_parser(self, flag: str | None = None) -> None:
        del flag

    def writer(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("a pre-stopped media run must not open its writer")


async def test_pre_stopped_media_run_returns_interrupted_before_extraction(tmp_path: Path) -> None:
    source = tmp_path / "deck.pdf"
    source.write_bytes(b"%PDF-1.4")
    stop = asyncio.Event()
    stop.set()

    code = await run_split(
        SplitRequest(
            media=True,
            input=InputSpec(patterns=(str(source),), from_files=False),
        ),
        _NoIoContext(),  # type: ignore[arg-type]
        stdin=io.StringIO(),
        stdout=io.StringIO(),
        stop=stop,
    )

    assert code is ExitCode.INTERRUPTED
