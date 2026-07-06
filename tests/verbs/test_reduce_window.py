"""reduce --window: streamed windows, pinned record shape, partial flush, screens."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from sempipe.core.errors import ExitCode, ItemError, UsageFault
from sempipe.io.inputs import InputSpec
from sempipe.io.writers import OutputFormat, RenderMode, WriterConfig, make_writer
from sempipe.models.base import CompletionRequest, ModelRef
from sempipe.verbs.reduce import ReduceRequest, run_reduce

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TextIO

    from sempipe.io.writers import ResultWriter


class FakeChat:
    def __init__(self, reply: Callable[[CompletionRequest], str]) -> None:
        self.reply = reply
        self.ref = ModelRef("ollama", "fake")
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        out = self.reply(request)
        if out == "__RAISE__":
            raise ItemError("model declined")
        return out


class FakeContext:
    def __init__(self, model: FakeChat) -> None:
        self.model = model

    async def chat_model(self, flag: str | None = None) -> FakeChat:
        return self.model

    async def context_window(self, ref: object) -> int | None:
        return None  # the static table stands in these tests

    def concurrency(self, flag: int | None = None) -> int:
        return 4

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None

    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextIO,
        fields: tuple[str, ...] | None = None,
    ) -> ResultWriter:
        config = WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=fields)
        return make_writer(config, stdout)


def _request(prompt: str = "Summarize", **kw: object) -> ReduceRequest:
    defaults: dict[str, object] = {
        "prompt": prompt,
        "schema_path": None,
        "group_by": None,
        "model_flag": None,
        "concurrency_flag": None,
        "verbose": False,
    }
    defaults.update(kw)
    return ReduceRequest(**defaults)  # type: ignore[arg-type]


def _count_items(request: CompletionRequest) -> str:
    return f"S({request.user.count('[')})"  # items are numbered [1] [2] … in the prompt


async def _run(
    request: ReduceRequest, stdin: str, reply: Callable[[CompletionRequest], str]
) -> tuple[ExitCode, list[dict[str, object]], FakeChat]:
    model = FakeChat(reply)
    out = io.StringIO()
    code = await run_reduce(request, FakeContext(model), stdin=io.StringIO(stdin), stdout=out)
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    return code, records, model


async def test_tumbling_windows_with_partial_flush() -> None:
    code, records, _ = await _run(_request(window=2), "a\nb\nc\nd\ne\n", _count_items)
    assert code == ExitCode.OK
    assert records == [
        {"window_end": 2, "result": "S(2)"},
        {"window_end": 4, "result": "S(2)"},
        {"window_end": 5, "result": "S(1)", "partial": True},
    ]


async def test_sliding_windows() -> None:
    code, records, _ = await _run(_request(window=2, every=1), "a\nb\nc\n", _count_items)
    assert code == ExitCode.OK
    assert [r["window_end"] for r in records] == [2, 3]
    assert all("partial" not in r for r in records)  # nothing left after the last slide


async def test_failed_window_is_skipped_and_stream_continues() -> None:
    def reply(request: CompletionRequest) -> str:
        return "__RAISE__" if "bad" in request.user else "ok"

    code, records, _ = await _run(_request(window=1), "good\nbad\nfine\n", reply)
    assert code == ExitCode.PARTIAL
    assert [r["window_end"] for r in records] == [1, 3]  # window 2 skipped, run continued


async def test_every_without_window_is_usage_error() -> None:
    with pytest.raises(UsageFault, match="--every only makes sense with --window"):
        await _run(_request(every=5), "a\n", _count_items)


async def test_window_with_group_by_is_usage_error() -> None:
    with pytest.raises(UsageFault, match="can't combine with --group-by"):
        await _run(_request(window=2, group_by="team"), "a\n", _count_items)


async def test_window_with_file_inputs_is_usage_error() -> None:
    request = _request(window=2, input=InputSpec(patterns=("*.txt",), from_files=False))
    with pytest.raises(UsageFault, match="can't combine with --in"):
        await _run(request, "a\n", _count_items)


async def test_every_larger_than_window_is_usage_error() -> None:
    with pytest.raises(UsageFault, match="--every must be between"):
        await _run(_request(window=2, every=3), "a\n", _count_items)


async def test_interrupt_on_a_paused_stream_flushes_the_buffered_partial(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The real tail -f scenario: 3 lines arrived, the window is 2, line 'c' sits
    in the buffer while the stream is quiet. ^C must reduce-and-emit that partial —
    buffered lines are never silently discarded (stage-08 contract)."""
    import asyncio
    import os

    stop = asyncio.Event()
    model = FakeChat(_count_items)
    out = io.StringIO()
    r_fd, w_fd = os.pipe()
    reader = os.fdopen(r_fd, "r", encoding="utf-8")
    try:
        task = asyncio.ensure_future(
            run_reduce(_request(window=2), FakeContext(model), stdin=reader, stdout=out, stop=stop)
        )
        os.write(w_fd, b"a\nb\nc\n")  # window [a,b] emits; c is buffered, stream open
        for _ in range(300):  # bounded poll for the first window record
            if out.getvalue():
                break
            await asyncio.sleep(0.01)
        assert '"window_end":2' in out.getvalue().replace(" ", "")
        stop.set()  # ^C while the (open, quiet) stream has 'c' buffered
        code = await asyncio.wait_for(task, timeout=5)
    finally:
        os.close(w_fd)
        reader.close()
    records = [json.loads(line) for line in out.getvalue().splitlines()]
    assert records[-1]["partial"] is True  # 'c' was flushed, not discarded
    assert records[-1]["window_end"] == 3
    assert code == ExitCode.OK  # everything that ran succeeded
    assert "done: interrupted" in capsys.readouterr().err
