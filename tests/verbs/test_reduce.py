from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, ItemError, UsageFault
from smartpipe.io.writers import OutputFormat, RenderMode, WriterConfig, make_writer
from smartpipe.models.base import CompletionRequest, ModelRef
from smartpipe.verbs.reduce import ReduceRequest, run_reduce

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TextIO

    from smartpipe.io.writers import ResultWriter
    from smartpipe.models.base import Provider


class FakeChat:
    """Replies via an injected function of the CompletionRequest. Provider drives budget."""

    def __init__(
        self, reply: Callable[[CompletionRequest], str], *, provider: Provider = "ollama"
    ) -> None:
        self.reply = reply
        self.ref = ModelRef(provider, "fake")
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
        return None  # table budget stands; the probe layer is exercised separately

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
        mode = RenderMode.NDJSON if structured else RenderMode.TEXT
        return make_writer(WriterConfig(mode=mode, color=False, width=80, fields=fields), stdout)


def _request(
    prompt: str,
    *,
    group_by: str | None = None,
    verbose: bool = False,
) -> ReduceRequest:
    return ReduceRequest(
        prompt=prompt,
        schema_path=None,
        group_by=group_by,
        model_flag=None,
        concurrency_flag=None,
        verbose=verbose,
    )


async def _run(
    request: ReduceRequest,
    stdin: str,
    reply: Callable[[CompletionRequest], str],
) -> tuple[ExitCode, str, FakeChat]:
    model = FakeChat(reply)
    out = io.StringIO()
    code = await run_reduce(request, FakeContext(model), stdin=io.StringIO(stdin), stdout=out)
    return code, out.getvalue(), model


# --- single-shot --------------------------------------------------------------


async def test_small_input_is_a_single_call() -> None:
    code, out, model = await _run(
        _request("Summarize"), "line one\nline two\n", lambda _r: "the summary"
    )
    assert code == ExitCode.OK
    assert out == "the summary\n"
    assert len(model.calls) == 1  # everything fit in one call


async def test_empty_input_is_ok() -> None:
    code, out, model = await _run(_request("Summarize"), "", lambda _r: "x")
    assert code == ExitCode.OK
    assert out == ""
    assert model.calls == []


# --- recursion ----------------------------------------------------------------


async def test_large_input_recurses_and_shows_tree(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # ollama budget ≈ 4800 - 300 = 4500 tokens ≈ 18000 chars. Make each item big.
    big = "x" * 8000  # ≈ 2000 tokens each; 6 items ≈ 12000 tokens → needs chunking
    stdin = "".join(f"{big}\n" for _ in range(6))

    def reply(request: CompletionRequest) -> str:
        # intermediate calls have the "condensing PART" system prompt → return small notes
        return "note" if "condensing PART" in (request.system or "") else "FINAL SUMMARY"

    code, out, model = await _run(_request("Summarize", verbose=True), stdin, reply)
    assert code == ExitCode.OK
    assert out == "FINAL SUMMARY\n"
    # more than one call: chunk reductions + a final synthesis
    assert len(model.calls) > 1
    err = capsys.readouterr().err
    assert "reduce:" in err and "→" in err


# --- structured ---------------------------------------------------------------


async def test_schema_final_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    schema: dict[str, object] = {
        "type": "object",
        "properties": {"headline": {"type": "string"}},
        "required": ["headline"],
        "additionalProperties": False,
    }

    def fake_load(_path: object) -> dict[str, object]:
        return schema

    monkeypatch.setattr("smartpipe.verbs.reduce.load_schema", fake_load)
    request = ReduceRequest(
        prompt="Summarize",
        schema_path=Path("x.json"),
        group_by=None,
        model_flag=None,
        concurrency_flag=None,
        verbose=False,
    )
    code, out, _model = await _run(request, "a\nb\n", lambda _r: '{"headline": "All good"}')
    assert code == ExitCode.OK
    assert json.loads(out.strip()) == {"headline": "All good"}


# --- group-by -----------------------------------------------------------------


async def test_group_by_reduces_each_group() -> None:
    stdin = (
        '{"product": "A", "note": "loved it"}\n'
        '{"product": "B", "note": "hated it"}\n'
        '{"product": "A", "note": "great"}\n'
    )

    # reply echoes how many items were in the group by counting the "[n]" markers
    def reply(request: CompletionRequest) -> str:
        return f"summary({request.user.count('[')} items)"

    code, out, _model = await _run(_request("Summarize", group_by="product"), stdin, reply)
    assert code == ExitCode.OK
    records = [json.loads(line) for line in out.splitlines()]
    assert records[0] == {"group": "A", "result": "summary(2 items)"}
    assert records[1] == {"group": "B", "result": "summary(1 items)"}


async def test_group_by_field_reaches_prompt() -> None:
    stdin = '{"product": "Widget", "note": "x"}\n'
    code, _out, model = await _run(
        _request("Summarize sentiment for {product}", group_by="product"), stdin, lambda _r: "ok"
    )
    assert code == ExitCode.OK
    assert "Summarize sentiment for Widget" in model.calls[0].user


async def test_group_by_skips_items_missing_the_field() -> None:
    stdin = '{"product": "A", "note": "x"}\n{"note": "orphan"}\n'
    code, _out, _model = await _run(_request("S", group_by="product"), stdin, lambda _r: "ok")
    assert code == ExitCode.PARTIAL  # one group produced, one item skipped


# --- errors -------------------------------------------------------------------


async def test_braces_without_group_by_is_usage_error() -> None:
    with pytest.raises(UsageFault, match="only work with --group-by"):
        await _run(_request("Summarize {x}"), "a\n", lambda _r: "ok")


async def test_comma_group_rejected() -> None:
    with pytest.raises(UsageFault, match="only work in 'map'"):
        await _run(_request("Summarize {a, b}", group_by="p"), '{"p":1}\n', lambda _r: "ok")


async def test_final_failure_exits_3() -> None:
    code, out, _model = await _run(_request("Summarize"), "a\n", lambda _r: "__RAISE__")
    assert code == ExitCode.ALL_FAILED
    assert out == ""


# --- chunk-level failures -----------------------------------------------------


def _two_big_items() -> str:
    # each ≈ 3000 tokens (< ~4500 budget alone, but two together force 2 chunks)
    return "A" * 12000 + "\n" + "B" * 12000 + "\n"


async def test_one_chunk_fails_is_partial(capsys: pytest.CaptureFixture[str]) -> None:
    def reply(request: CompletionRequest) -> str:
        if "condensing PART" in (request.system or ""):
            if "B" in request.user:  # the chunk holding item B declines
                return "__RAISE__"
            return "note A"
        return "FINAL"

    code, out, _model = await _run(_request("Summarize"), _two_big_items(), reply)
    assert code == ExitCode.PARTIAL
    assert out == "FINAL\n"
    assert "skipped: chunk over items" in capsys.readouterr().err


async def test_all_chunks_fail_exits_3() -> None:
    def reply(request: CompletionRequest) -> str:
        return "__RAISE__" if "condensing PART" in (request.system or "") else "FINAL"

    code, out, _model = await _run(_request("Summarize"), _two_big_items(), reply)
    assert code == ExitCode.ALL_FAILED
    assert out == ""


async def test_group_reduce_failure_is_partial(capsys: pytest.CaptureFixture[str]) -> None:
    stdin = '{"product": "A", "n": "x"}\n{"product": "B", "n": "y"}\n'

    def reply(request: CompletionRequest) -> str:
        return "__RAISE__" if "Summarize B" in request.user else "ok"

    code, out, _model = await _run(
        _request("Summarize {product}", group_by="product"), stdin, reply
    )
    assert code == ExitCode.PARTIAL
    assert json.loads(out.strip()) == {"group": "A", "result": "ok"}
    assert "reduce failed for group 'B'" in capsys.readouterr().err


async def test_schema_repair_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    schema: dict[str, object] = {
        "type": "object",
        "properties": {"headline": {"type": "string"}},
        "required": ["headline"],
        "additionalProperties": False,
    }

    def fake_load(_path: object) -> dict[str, object]:
        return schema

    monkeypatch.setattr("smartpipe.verbs.reduce.load_schema", fake_load)
    calls = {"n": 0}

    def reply(_request: CompletionRequest) -> str:
        calls["n"] += 1
        return "not json" if calls["n"] == 1 else '{"headline": "fixed"}'

    request = ReduceRequest(
        prompt="Summarize",
        schema_path=Path("x.json"),
        group_by=None,
        model_flag=None,
        concurrency_flag=None,
        verbose=False,
    )
    code, out, model = await _run(request, "a\n", reply)
    assert code == ExitCode.OK
    assert json.loads(out.strip()) == {"headline": "fixed"}
    assert len(model.calls) == 2  # original + one repair


class OverflowingChat:
    """Says 'context length exceeded' for any request over its true window,
    regardless of what the estimator believed."""

    def __init__(self, window_chars: int) -> None:
        self.ref = ModelRef("ollama", "tight")
        self.window_chars = window_chars
        self.calls: list[int] = []

    async def complete(self, request: CompletionRequest) -> str:
        size = len(request.user)
        self.calls.append(size)
        if size > self.window_chars:
            raise ItemError("this model's maximum context length is smaller than that")
        return "note"


async def test_bisection_recovers_when_the_estimate_lies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from smartpipe.verbs.reduce import Reducer

    model = OverflowingChat(window_chars=900)
    reducer = Reducer(model=model, budget=10_000, concurrency=1, verbose=False)
    # budget says these fit in ONE call; the wire disagrees until quartered
    texts = ["x" * 400 for _ in range(8)]
    result = await reducer.reduce("summarize", None, texts)
    assert result == "note"
    assert reducer.skipped == 0  # nothing lost — bisection, not skipping
    assert max(model.calls[-3:]) <= 900  # the final calls fit the true window
    err = capsys.readouterr().err
    assert err.count("splitting further and retrying") == 1  # the pinned once-note


async def test_single_item_overflow_surfaces_the_wire_error() -> None:
    # one item that alone exceeds the true window can't be bisected at item
    # boundaries — the wire's own message surfaces loudly (D26: split is the fix)
    from smartpipe.verbs.reduce import Reducer

    model = OverflowingChat(window_chars=100)
    reducer = Reducer(model=model, budget=10_000, concurrency=1, verbose=False)
    with pytest.raises(ItemError, match="maximum context length"):
        await reducer.reduce("summarize", None, ["y" * 5_000])
