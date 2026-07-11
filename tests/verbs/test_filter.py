from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smartpipe.core.errors import ExitCode, ItemError, UsageFault
from smartpipe.engine.runner import FailurePolicy
from smartpipe.io.writers import OutputFormat, RenderMode, WriterConfig, make_writer
from smartpipe.models.base import CompletionRequest, ModelRef
from smartpipe.verbs.filter import FilterRequest, run_filter

if TYPE_CHECKING:
    from collections.abc import Callable

    from smartpipe.engine.coalesce import BatchSettings
    from smartpipe.io.writers import ResultWriter, TextSink
    from smartpipe.models.base import ChatModel


class FakeChat:
    """Judges via an injected verdict function keyed on the item text in the prompt."""

    def __init__(self, verdict: Callable[[str], str]) -> None:
        self.verdict = verdict
        self.ref = ModelRef("ollama", "fake")
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        reply = self.verdict(request.user)
        if reply == "__RAISE_ITEM__":
            raise ItemError("model declined")
        return reply


class FakeContext:
    def __init__(self, model: FakeChat) -> None:
        self.model = model

    async def chat_model(self, flag: str | None = None) -> FakeChat:
        return self.model

    async def context_window(self, ref: object) -> int | None:
        return None  # the static table stands in these tests

    def fallback_ref(self, flag: str | None = None) -> None:
        return None  # no failover configured in these tests

    async def fallback_chat_model(self, ref: object) -> ChatModel:
        raise AssertionError("fallback never resolved without a configured ref")

    def concurrency(self, flag: int | None = None) -> int:
        return 1  # deterministic order for assertions

    def failure_policy(self, provider: str) -> FailurePolicy:
        from smartpipe.cli import screens

        return FailurePolicy(
            transport_limit=5,
            transport_screen=screens.provider_down(provider, 5),
        )

    def batching(self) -> BatchSettings | None:
        return None  # batching off: these tests pin the solo path byte-for-byte

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None

    def document_parser(self, flag: str | None = None) -> None:
        return None

    def writer(
        self, output_flag: OutputFormat, *, structured: bool, stdout: TextSink
    ) -> ResultWriter:
        return make_writer(WriterConfig(mode=RenderMode.TEXT, color=False, width=80), stdout)


def _request(condition: str, *, invert: bool = False) -> FilterRequest:
    return FilterRequest(condition=condition, invert=invert, model_flag=None, concurrency_flag=None)


async def _run(
    condition: str, stdin: str, verdict: Callable[[str], str], *, invert: bool = False
) -> tuple[ExitCode, str, FakeChat]:
    model = FakeChat(verdict)
    out = io.StringIO()
    code = await run_filter(
        _request(condition, invert=invert),
        FakeContext(model),
        stdin=io.StringIO(stdin),
        stdout=out,
    )
    return code, out.getvalue(), model


def _match_if(needle: str) -> Callable[[str], str]:
    return lambda user: '{"match": true}' if needle in user else '{"match": false}'


# --- semantic grep ------------------------------------------------------------


async def test_keeps_matching_lines_in_order() -> None:
    # the needle "bug" must appear only in the item text, not the condition
    code, out, _model = await _run(
        "reports a defect", "login bug\nall good\ncrash bug\n", _match_if("bug")
    )
    assert code == ExitCode.OK
    assert out == "login bug\ncrash bug\n"


async def test_zero_matches_is_success() -> None:
    code, out, _model = await _run("x", "a\nb\n", _match_if("nothing"))
    assert code == ExitCode.OK
    assert out == ""


async def test_not_inverts() -> None:
    _code, out, _model = await _run("spam", "buy now\nhi mom\n", _match_if("buy"), invert=True)
    assert out == "hi mom\n"


async def test_output_is_byte_identical_to_matched_input() -> None:
    quirky = '{ "a" :1}'  # odd spacing must survive unchanged
    _code, out, _model = await _run("x", quirky + "\n", lambda _u: '{"match": true}')
    assert out == quirky + "\n"


# --- field interpolation ------------------------------------------------------


async def test_field_substitution_reaches_the_model() -> None:
    stdin = '{"priority": "high", "title": "Login"}\n'
    _code, _out, model = await _run("{priority} is wrong for {title}", stdin, _match_if("nope"))
    assert "high is wrong for Login" in model.calls[0].user


async def test_missing_field_skips_that_item() -> None:
    stdin = '{"priority": "high"}\n{"other": 1}\n'
    code, out, _model = await _run("{priority} bad", stdin, _match_if("high"))
    # first item matches, second is skipped (no 'priority')
    assert code == ExitCode.PARTIAL
    assert out == '{"priority": "high"}\n'


# --- fail-fast screens --------------------------------------------------------


async def test_comma_group_is_usage_fault() -> None:
    with pytest.raises(UsageFault, match="only work in 'map'"):
        await _run("{a, b}", "x\n", _match_if("x"))


async def test_braces_on_all_plain_input_fails_fast() -> None:
    with pytest.raises(UsageFault, match="isn't JSON"):
        await _run("{priority} bad", "plain text\nmore text\n", _match_if("x"))


# --- repair & skips -----------------------------------------------------------


async def test_unparseable_verdict_repairs_then_recovers() -> None:
    calls = {"n": 0}

    def verdict(_user: str) -> str:
        calls["n"] += 1
        return "maybe?" if calls["n"] == 1 else '{"match": true}'

    code, out, model = await _run("x", "keep me\n", verdict)
    assert code == ExitCode.OK
    assert out == "keep me\n"
    assert len(model.calls) == 2  # original + one repair


async def test_verdict_bad_twice_skips_item() -> None:
    code, out, _model = await _run("x", "a\n", lambda _u: "never json")
    assert code == ExitCode.ALL_FAILED  # the only item was skipped
    assert out == ""


async def test_one_skip_among_judged_is_partial() -> None:
    # item "a" is judged (no match), item "b" errors twice → skipped
    def verdict(user: str) -> str:
        return "never json" if "\nb" in user else '{"match": false}'

    code, out, _model = await _run("x", "a\nb\n", verdict)
    assert code == ExitCode.PARTIAL
    assert out == ""


# --- property: passthrough purity ---------------------------------------------


@given(lines=st.lists(st.text(alphabet=st.characters(exclude_characters="\n\r")), max_size=10))
def test_kept_output_is_always_a_verbatim_subset(lines: list[str]) -> None:
    import asyncio

    stdin = "".join(f"{line}\n" for line in lines)
    code, out, _model = asyncio.run(_run("x", stdin, lambda _u: '{"match": true}'))
    assert code == ExitCode.OK
    assert out == stdin  # match-all reproduces the input exactly


# --- interrupt paths (unit twins of the signals e2e) ----------------------------


async def test_interrupted_empty_stream_exits_130(capsys: pytest.CaptureFixture[str]) -> None:
    import asyncio

    stop = asyncio.Event()
    stop.set()  # interrupted before anything arrived
    model = FakeChat(_match_if("x"))
    out = io.StringIO()
    code = await run_filter(
        _request("x"), FakeContext(model), stdin=io.StringIO(""), stdout=out, stop=stop
    )
    assert code == ExitCode.INTERRUPTED
    assert "0 processed · 0 skipped" in capsys.readouterr().err


async def test_interrupted_after_results_keeps_outcome_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    stop = asyncio.Event()
    model = FakeChat(_match_if("keep"))

    class StopAfterFirst(FakeContext):
        def writer(self, output_flag: OutputFormat, *, structured: bool, stdout: TextSink):  # type: ignore[override]
            inner = super().writer(output_flag, structured=structured, stdout=stdout)

            class W:
                def write_text(self, line: str) -> None:
                    inner.write_text(line)
                    stop.set()  # interrupt lands right after the first emission

                def write_record(self, record: object) -> None:  # pragma: no cover
                    raise AssertionError

                def write_passthrough(self, item: object) -> None:
                    inner.write_passthrough(item)  # type: ignore[arg-type]
                    stop.set()

                def flush(self) -> None:
                    inner.flush()

            return W()

    out = io.StringIO()
    code = await run_filter(
        _request("x"),
        StopAfterFirst(model),
        stdin=io.StringIO("keep me\nkeep too\nnever seen\n"),
        stdout=out,
        stop=stop,
    )
    assert code == ExitCode.OK  # everything that ran succeeded
    assert "done: interrupted" in capsys.readouterr().err
    assert out.getvalue().startswith("keep me\n")


# --- positional files keep their rows (field-test catch, 2026-07-08) ----------


async def test_as_jsonl_file_rows_pass_through_not_the_filename(tmp_path: Path) -> None:
    """filter '...' --as jsonl tickets.jsonl emitted the FILENAME once per
    matching row (the grep -l path-back contract leaked onto row cuts).
    Rows must pass through; only whole-file items get paths back."""
    from smartpipe.io.inputs import InputSpec

    corpus = tmp_path / "tickets.jsonl"
    corpus.write_text('{"id": 1, "text": "login bug"}\n{"id": 2, "text": "all good"}\n')
    model = FakeChat(_match_if("bug"))
    out = io.StringIO()
    request = FilterRequest(
        condition="reports a defect",
        invert=False,
        model_flag=None,
        concurrency_flag=None,
        input=InputSpec(patterns=(str(corpus),), from_files=False, as_mode="jsonl"),
    )
    code = await run_filter(request, FakeContext(model), stdin=io.StringIO(""), stdout=out)
    assert code == ExitCode.OK
    assert out.getvalue() == '{"id": 1, "text": "login bug"}\n'


async def test_whole_file_match_still_returns_the_path(tmp_path: Path) -> None:
    from smartpipe.io.inputs import InputSpec

    doc = tmp_path / "resume.txt"
    doc.write_text("ten years of bug hunting\n")
    model = FakeChat(_match_if("bug"))
    out = io.StringIO()
    request = FilterRequest(
        condition="reports a defect",
        invert=False,
        model_flag=None,
        concurrency_flag=None,
        input=InputSpec(patterns=(str(doc),), from_files=False, as_mode=None),
    )
    code = await run_filter(request, FakeContext(model), stdin=io.StringIO(""), stdout=out)
    assert code == ExitCode.OK
    assert out.getvalue() == f"{doc}\n"


# --- the <input> framing (item 57) ---------------------------------------------


async def test_judge_prompt_frames_a_record_as_an_input_block() -> None:
    _code, _out, model = await _run("is urgent", '{"id": 1, "body": "crash"}\n', _match_if("crash"))
    assert model.calls[0].user == "Condition: is urgent\n\n<input>\nid: 1\nbody: crash\n</input>"


async def test_judge_prompt_frames_plain_text_as_an_input_block() -> None:
    _code, _out, model = await _run("is urgent", "system down\n", _match_if("down"))
    assert model.calls[0].user == "Condition: is urgent\n\n<input>\nsystem down\n</input>"
