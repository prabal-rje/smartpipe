"""Cross-surface source accounting: every whole-set early path uses one funnel."""

from __future__ import annotations

import asyncio
import base64
import io
import json
from typing import TYPE_CHECKING, Literal

import pytest

from smartpipe.core.errors import ExitCode, ItemError
from smartpipe.engine.graphkg import EntitySpan
from smartpipe.engine.runner import FailurePolicy
from smartpipe.io import manifest
from smartpipe.io.writers import OutputFormat, RenderMode, WriterConfig, make_writer
from smartpipe.models.base import CompletionRequest, ModelRef
from smartpipe.models.budget import CallBudget, budgeted_embed
from smartpipe.verbs.cluster import ClusterRequest, run_cluster
from smartpipe.verbs.diff import DiffRequest, run_diff
from smartpipe.verbs.distinct import DistinctRequest, run_distinct
from smartpipe.verbs.graph import GraphRequest, run_graph
from smartpipe.verbs.outliers import OutliersRequest, run_outliers
from smartpipe.verbs.reduce import ReduceRequest, run_reduce

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from typing import TextIO

    from smartpipe.io.writers import ResultWriter
    from smartpipe.models.base import EmbeddingModel


class _FailedEmbedding:
    ref = ModelRef("ollama", "failed-embed")

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        raise ItemError(f"could not embed {len(texts)} rows")


class _FailedChat:
    ref = ModelRef("ollama", "failed-chat")

    async def complete(self, request: CompletionRequest) -> str:
        del request
        raise ItemError("model declined")


class _Finder:
    def find(self, text: str) -> tuple[EntitySpan, ...]:
        del text
        return ()


class _Context:
    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel:
        del flag
        return _FailedEmbedding()

    async def chat_model(self, flag: str | None = None) -> _FailedChat:
        del flag
        return _FailedChat()

    async def context_window(self, ref: ModelRef) -> int | None:
        del ref
        return None

    def concurrency(self, flag: int | None = None) -> int:
        del flag
        return 1

    def failure_policy(self, provider: str) -> FailurePolicy:
        del provider
        return FailurePolicy()

    def document_parser(self, flag: str | None = None) -> None:
        del flag
        return None

    def remote_transcriber(self, chat_ref: ModelRef | None = None) -> None:
        del chat_ref
        return None

    def entity_finder(self, labels: Sequence[str]) -> _Finder:
        del labels
        return _Finder()

    def fold_embedder(self) -> _FailedEmbedding:
        return _FailedEmbedding()

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


class _SuccessfulEmbedding:
    ref = ModelRef("ollama", "successful-embed")

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple((float(position + 1), 1.0) for position, _text in enumerate(texts))


class _BeltContext(_Context):
    def __init__(self, stop: asyncio.Event) -> None:
        self.model = budgeted_embed(
            _SuccessfulEmbedding(),
            CallBudget(limit=1, stop=stop),
        )

    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel:
        del flag
        return self.model


Surface = Literal["cluster", "distinct", "outliers", "reduce", "graph"]


async def _all_skipped(surface: Surface, stdout: io.StringIO) -> tuple[ExitCode, int, int]:
    context = _Context()
    match surface:
        case "cluster":
            code = await run_cluster(
                ClusterRequest(), context, stdin=io.StringIO("one\ntwo\nthree\n"), stdout=stdout
            )
            return code, 3, 3
        case "distinct":
            code = await run_distinct(
                DistinctRequest(), context, stdin=io.StringIO("one\ntwo\nthree\n"), stdout=stdout
            )
            return code, 3, 3
        case "outliers":
            code = await run_outliers(
                OutliersRequest(count=1),
                context,
                stdin=io.StringIO("one\ntwo\nthree\n"),
                stdout=stdout,
            )
            return code, 3, 3
        case "reduce":
            request = ReduceRequest(
                prompt="summarize",
                schema_path=None,
                group_by=None,
                model_flag=None,
                concurrency_flag=None,
                verbose=False,
            )
            code = await run_reduce(
                request, context, stdin=io.StringIO("one\ntwo\n"), stdout=stdout
            )
            return code, 2, 2
        case "graph":
            pixel = base64.b64encode(b"px").decode("ascii")
            row = json.dumps({"__media": {"kind": "image", "mime": "image/png", "data_b64": pixel}})
            code = await run_graph(
                GraphRequest(fast=True),
                context,
                stdin=io.StringIO(f"{row}\n{row}\n"),
                stdout=stdout,
                clock=lambda: 0.0,
            )
            return code, 2, 0


@pytest.mark.parametrize("surface", ["cluster", "distinct", "outliers", "reduce", "graph"])
async def test_all_skipped_whole_set_surfaces_exit_three_and_manifest_source_units(
    surface: Surface, tmp_path: Path
) -> None:
    manifest.reset()
    target = tmp_path / f"{surface}.json"
    manifest.begin(target, verb=surface, argv=(surface,))
    code, input_count, failed = await _all_skipped(surface, io.StringIO())
    manifest.finish(code)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert code is ExitCode.ALL_FAILED
    assert document["items"] == {
        "in": input_count,
        "succeeded": 0,
        "skipped": input_count,
        "failed": failed,
    }


@pytest.mark.parametrize("surface", ["cluster", "distinct", "outliers", "diff"])
async def test_belt_exclusions_are_skipped_but_not_failed_across_whole_set_surfaces(
    surface: str,
    tmp_path: Path,
) -> None:
    stop = asyncio.Event()
    context = _BeltContext(stop)
    target = tmp_path / f"{surface}-belt.json"
    manifest.reset()
    manifest.begin(target, verb=surface, argv=(surface,))
    lines = "".join(f"row {position}\n" for position in range(64))
    stdout = io.StringIO()

    if surface == "cluster":
        code = await run_cluster(
            ClusterRequest(), context, stdin=io.StringIO(lines + "tail\n"), stdout=stdout, stop=stop
        )
        succeeded = 64
    elif surface == "distinct":
        code = await run_distinct(
            DistinctRequest(),
            context,
            stdin=io.StringIO(lines + "tail\n"),
            stdout=stdout,
            stop=stop,
        )
        succeeded = 64
    elif surface == "outliers":
        code = await run_outliers(
            OutliersRequest(count=1),
            context,
            stdin=io.StringIO(lines + "tail\n"),
            stdout=stdout,
            stop=stop,
        )
        succeeded = 64
    else:
        right = tmp_path / "right.txt"
        right.write_text("right row\n", encoding="utf-8")
        code = await run_diff(
            DiffRequest(right=right),
            context,
            stdin=io.StringIO(lines),
            stdout=stdout,
            stop=stop,
        )
        succeeded = 0  # the right side was unsent, so no usable comparison exists

    manifest.finish(code)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {
        "in": 65,
        "succeeded": succeeded,
        "skipped": 65 - succeeded,
        "failed": 0,
    }
