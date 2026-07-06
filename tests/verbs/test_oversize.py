"""D26 per-verb oversize ladders: refuse (map), any-chunk (filter), gate probe."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest

from sempipe.core.errors import ExitCode
from sempipe.io.writers import OutputFormat, RenderMode, ResultWriter, WriterConfig, make_writer
from sempipe.models.base import CompletionRequest, ModelRef
from sempipe.verbs.filter import FilterRequest, run_filter
from sempipe.verbs.map import MapRequest, run_map

if TYPE_CHECKING:
    from typing import TextIO

# past the openai table budget (128k * 0.6 - 500 ≈ 76.3k tokens): the gate engages
BIG = "word " * 70_000  # ~87.5k estimated tokens


class Chat:
    def __init__(self, reply: str = "ok") -> None:
        self.ref = ModelRef("openai", "gpt-4o-mini")
        self.reply = reply
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        return self.reply


class ChunkJudge:
    """Matches only when the needle chunk is in the judged text."""

    def __init__(self) -> None:
        self.ref = ModelRef("openai", "gpt-4o-mini")
        self.calls: list[str] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request.user)
        verdict = "true" if "NEEDLE" in request.user else "false"
        return f'{{"match": {verdict}}}'


class Ctx:
    """Reports a tiny 4k window, like a small local model would."""

    def __init__(self, model: Chat | ChunkJudge, window: int | None = 4_000) -> None:
        self.model = model
        self.window = window
        self.probes = 0

    async def chat_model(self, flag: str | None = None) -> Chat | ChunkJudge:
        return self.model

    async def context_window(self, ref: object) -> int | None:
        self.probes += 1
        return self.window

    def concurrency(self, flag: int | None = None) -> int:
        return 1

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
        return make_writer(WriterConfig(mode=RenderMode.TEXT, color=False, width=80), stdout)


async def test_map_refuses_an_over_window_item_with_the_split_recipe(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = Chat()
    out = io.StringIO()
    code = await run_map(
        MapRequest(
            prompt="summarize",
            schema_path=None,
            model_flag=None,
            output=OutputFormat.TEXT,
            concurrency_flag=None,
        ),
        Ctx(model, window=None),  # the probe finds nothing — the table stands
        stdin=io.StringIO(BIG + "\n"),
        stdout=out,
    )
    assert code is ExitCode.ALL_FAILED
    assert model.calls == []  # refused BEFORE any spend
    err = capsys.readouterr().err
    assert "token budget — split it first" in err
    assert "sempipe split" in err


async def test_map_probe_can_widen_and_allow() -> None:
    model = Chat("summary")
    out = io.StringIO()
    context = Ctx(model, window=1_000_000)  # the probe discovers a huge window
    code = await run_map(
        MapRequest(
            prompt="summarize",
            schema_path=None,
            model_flag=None,
            output=OutputFormat.TEXT,
            concurrency_flag=None,
        ),
        context,
        stdin=io.StringIO(BIG + "\n"),
        stdout=out,
    )
    assert code is ExitCode.OK
    assert len(model.calls) == 1  # widened past the table, the call proceeded
    assert context.probes == 1  # asked exactly once


async def test_filter_judges_chunks_and_any_match_keeps_the_item() -> None:
    model = ChunkJudge()
    out = io.StringIO()
    line = "word " * 70_000 + "the NEEDLE is here"
    code = await run_filter(
        FilterRequest(
            condition="mentions the needle", invert=False, model_flag=None, concurrency_flag=None
        ),
        Ctx(model, window=None),  # table budget → two chunks for ~87k tokens
        stdin=io.StringIO(line + "\n"),
        stdout=out,
    )
    assert code is ExitCode.OK
    assert out.getvalue() == line + "\n"  # the WHOLE item, byte-verbatim
    assert len(model.calls) > 1  # it really judged multiple chunks
    assert "NEEDLE" in model.calls[-1]  # short-circuited at the matching chunk
