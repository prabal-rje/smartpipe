from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest

from sempipe.core.errors import ExitCode, ItemError, SetupFault
from sempipe.io.writers import (
    OutputFormat,
    RenderMode,
    WriterConfig,
    make_writer,
)
from sempipe.models.base import CompletionRequest, ModelRef
from sempipe.verbs.map import MapRequest, run_map

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TextIO

    from sempipe.io.writers import ResultWriter


# --- fakes --------------------------------------------------------------------


class FakeChat:
    """Scriptable ChatModel: replies keyed by call index (last repeats)."""

    def __init__(self, replies: Sequence[str], *, raise_setup_on_first: bool = False) -> None:
        self.replies = list(replies)
        self.raise_setup_on_first = raise_setup_on_first
        self.ref = ModelRef("ollama", "fake")
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        if self.raise_setup_on_first and len(self.calls) == 1:
            raise SetupFault("error: model unreachable")
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if reply == "__RAISE_ITEM__":
            raise ItemError("model declined")
        return reply


class FakeContext:
    def __init__(self, model: FakeChat, *, concurrency: int = 4) -> None:
        self.model = model
        self.concurrency_value = concurrency

    async def chat_model(self, flag: str | None = None) -> FakeChat:
        return self.model

    def concurrency(self, flag: int | None = None) -> int:
        return self.concurrency_value

    async def context_window(self, ref: object) -> int | None:
        return None  # the static table stands in these tests

    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextIO,
        fields: tuple[str, ...] | None = None,
    ) -> ResultWriter:
        mode = RenderMode.NDJSON if structured else RenderMode.TEXT
        return make_writer(WriterConfig(mode=mode, color=False, width=80, fields=fields), stdout)


def _request(prompt: str, **kw: object) -> MapRequest:
    return MapRequest(
        prompt=prompt,
        schema_path=kw.get("schema_path"),  # type: ignore[arg-type]
        model_flag=None,
        output=OutputFormat.AUTO,
        concurrency_flag=None,
    )


async def _run(prompt: str, stdin: str, replies: Sequence[str]) -> tuple[ExitCode, str, FakeChat]:
    model = FakeChat(replies=list(replies))
    context = FakeContext(model=model)
    out = io.StringIO()
    code = await run_map(_request(prompt), context, stdin=io.StringIO(stdin), stdout=out)
    return code, out.getvalue(), model


# --- plain mode ---------------------------------------------------------------


async def test_plain_transform_one_line() -> None:
    code, out, _model = await _run("translate to Spanish", "hello world\n", ["hola mundo"])
    assert code == ExitCode.OK
    assert out == "hola mundo\n"


async def test_plain_trims_only_trailing_whitespace() -> None:
    _code, out, _model = await _run("x", "a\n", ["  spaced out  \n"])
    assert out == "  spaced out\n"


async def test_plain_each_line_is_an_item() -> None:
    code, out, model = await _run("upper", "a\nb\nc\n", ["A", "B", "C"])
    assert code == ExitCode.OK
    assert out == "A\nB\nC\n"
    assert len(model.calls) == 3


async def test_plain_request_shape() -> None:
    _code, _out, model = await _run("translate", "hello\n", ["x"])
    assert model.calls[0].user == "translate\n\nhello"
    assert model.calls[0].json_schema is None


# --- structured mode ----------------------------------------------------------


async def test_shorthand_extraction_emits_ndjson() -> None:
    code, out, _model = await _run(
        "Extract {vendor, total}", "Acme $5\n", ['{"vendor": "Acme", "total": 5}']
    )
    assert code == ExitCode.OK
    assert out == '{"vendor":"Acme","total":5}\n'


async def test_structured_request_carries_schema() -> None:
    _code, _out, model = await _run("Extract {v}", "x\n", ['{"v": "y"}'])
    assert model.calls[0].json_schema == {
        "type": "object",
        "properties": {"v": {}},
        "required": ["v"],
        "additionalProperties": False,
    }


# --- repair retry -------------------------------------------------------------


async def test_repair_retry_recovers_bad_json() -> None:
    # first reply invalid, second valid → one repair, success
    code, out, model = await _run("Extract {v}", "x\n", ["not json at all", '{"v": "recovered"}'])
    assert code == ExitCode.OK
    assert out == '{"v":"recovered"}\n'
    assert len(model.calls) == 2
    assert "That was invalid" in model.calls[1].user  # repair prompt


async def test_second_failure_skips_the_item() -> None:
    code, out, model = await _run("Extract {v}", "x\n", ["bad once", "bad twice"])
    assert code == ExitCode.ALL_FAILED  # the only item failed
    assert out == ""
    assert len(model.calls) == 2  # original + one repair, then give up


# --- exit codes & skips -------------------------------------------------------


async def test_partial_success_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    model = FakeChat(replies=["ok", "__RAISE_ITEM__", "ok"])
    context = FakeContext(model=model)
    out = io.StringIO()
    code = await run_map(_request("x"), context, stdin=io.StringIO("a\nb\nc\n"), stdout=out)
    assert code == ExitCode.PARTIAL
    assert out.getvalue() == "ok\nok\n"  # the two successes, in order
    captured = capsys.readouterr()
    assert "skipped: line 2 (model declined)" in captured.err


async def test_empty_input_is_ok_and_silent() -> None:
    code, out, model = await _run("x", "", ["unused"])
    assert code == ExitCode.OK
    assert out == ""
    assert model.calls == []


# --- fatal errors propagate ---------------------------------------------------


async def test_setup_fault_from_model_propagates() -> None:
    model = FakeChat(replies=["x"], raise_setup_on_first=True)
    context = FakeContext(model=model)
    with pytest.raises(SetupFault, match="unreachable"):
        await run_map(_request("x"), context, stdin=io.StringIO("a\n"), stdout=io.StringIO())


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


async def test_terminal_stdin_is_a_usage_fault() -> None:
    from sempipe.core.errors import UsageFault

    model = FakeChat(replies=["x"])
    with pytest.raises(UsageFault, match="terminal"):
        await run_map(_request("x"), FakeContext(model=model), stdin=_Tty(), stdout=io.StringIO())
