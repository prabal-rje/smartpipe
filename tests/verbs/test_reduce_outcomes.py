"""Reduce preserves attempted-failure semantics for unsent sources."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, UnsentError
from smartpipe.io import manifest
from smartpipe.io.writers import OutputFormat, RenderMode, WriterConfig, make_writer
from smartpipe.models.base import CompletionRequest, ModelRef
from smartpipe.verbs.reduce import ReduceRequest, run_reduce

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    from smartpipe.io.writers import ResultWriter


class _UnsentChat:
    ref = ModelRef("ollama", "unsent")

    async def complete(self, request: CompletionRequest) -> str:
        del request
        raise UnsentError("run stopping - not sent")


class _Context:
    async def chat_model(self, flag: str | None = None) -> _UnsentChat:
        del flag
        return _UnsentChat()

    async def context_window(self, ref: ModelRef) -> None:
        del ref

    def concurrency(self, flag: int | None = None) -> int:
        del flag
        return 1

    def document_parser(self, flag: str | None = None) -> None:
        del flag

    def remote_transcriber(self, chat_ref: ModelRef | None = None) -> None:
        del chat_ref

    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextIO,
        fields: tuple[str, ...] | None = None,
    ) -> ResultWriter:
        del output_flag
        mode = RenderMode.NDJSON if structured else RenderMode.TEXT
        return make_writer(WriterConfig(mode=mode, color=False, width=80, fields=fields), stdout)


@pytest.mark.parametrize("window", (None, 2))
async def test_unsent_reduce_sources_are_skipped_but_not_failed(
    window: int | None,
    tmp_path: Path,
) -> None:
    target = tmp_path / "reduce.json"
    manifest.reset()
    manifest.begin(target, verb="reduce", argv=("reduce",))
    request = ReduceRequest(
        prompt="summarize",
        schema_path=None,
        group_by=None,
        model_flag=None,
        concurrency_flag=None,
        verbose=False,
        window=window,
    )

    code = await run_reduce(
        request,
        _Context(),
        stdin=io.StringIO("one\ntwo\n"),
        stdout=io.StringIO(),
    )
    manifest.finish(code)

    assert code is ExitCode.ALL_FAILED
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 2, "succeeded": 0, "skipped": 2, "failed": 0}
