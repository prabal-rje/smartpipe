"""D26 v2 per-verb oversize handling: auto-chunk + combine (map/extend), merge
(structured), ANY-true chunk judge (filter), --whole refusal, disclosure notes,
--max-calls interaction, and the media-aware gate."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode
from smartpipe.io.writers import OutputFormat, RenderMode, ResultWriter, WriterConfig, make_writer
from smartpipe.models.base import CompletionRequest, ModelRef
from smartpipe.verbs.extend import ExtendRequest, run_extend
from smartpipe.verbs.filter import FilterRequest, run_filter
from smartpipe.verbs.map import MapRequest, run_map

if TYPE_CHECKING:
    from collections.abc import Sequence

    from smartpipe.engine.coalesce import BatchSettings
    from smartpipe.io.writers import TextSink
    from smartpipe.models.base import ChatModel

# past the openai table budget (128k * 0.6 - 500 ≈ 76.3k tokens): the gate engages
BIG = "word " * 70_000  # ~87.5k estimated tokens


class Chat:
    """Scriptable ChatModel: replies keyed by call index (last repeats)."""

    def __init__(self, replies: Sequence[str] = ("ok",), *, ref: ModelRef | None = None) -> None:
        self.ref = ref if ref is not None else ModelRef("openai", "gpt-4o-mini")
        self.replies = list(replies)
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


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
    """Context double; ``window`` is what the provider probe reports."""

    def __init__(self, model: ChatModel, window: int | None = 4_000) -> None:
        self.model = model
        self.window = window
        self.probes = 0

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        return self.model

    async def context_window(self, ref: object) -> int | None:
        self.probes += 1
        return self.window

    def fallback_ref(self, flag: str | None = None) -> None:
        return None  # no failover configured in these tests

    async def fallback_chat_model(self, ref: object) -> ChatModel:
        raise AssertionError("fallback never resolved without a configured ref")

    def concurrency(self, flag: int | None = None) -> int:
        return 1

    def batching(self) -> BatchSettings | None:
        return None  # batching off: these tests pin the solo path byte-for-byte

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None

    def document_parser(self, flag: str | None = None) -> None:
        return None

    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextSink,
        fields: tuple[str, ...] | None = None,
        bare: bool = False,
        full: bool = False,
    ) -> ResultWriter:
        mode = RenderMode.NDJSON if structured else RenderMode.TEXT
        return make_writer(WriterConfig(mode=mode, color=False, width=80, fields=fields), stdout)


def _map_request(prompt: str = "summarize", **kw: object) -> MapRequest:
    defaults: dict[str, object] = {
        "prompt": prompt,
        "schema_path": None,
        "model_flag": None,
        "output": OutputFormat.AUTO,
        "concurrency_flag": None,
    }
    defaults.update(kw)
    return MapRequest(**defaults)  # type: ignore[arg-type]


# --- plain map/extend: chunk fan-out + ONE combine call ---------------------------


async def test_plain_map_chunks_and_combines_an_oversized_item(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = Chat(["part one", "part two", "THE COMBINED ANSWER"])
    out = io.StringIO()
    code = await run_map(
        _map_request(),
        Ctx(model, window=None),  # the probe finds nothing — the table stands
        stdin=io.StringIO(BIG + "\n"),
        stdout=out,
    )
    assert code is ExitCode.OK
    assert out.getvalue() == "THE COMBINED ANSWER\n"  # ONE result for the item
    assert len(model.calls) == 3  # 2 chunk calls + 1 combine call
    # the chunk calls carry the SAME instruction; the combine sees the partials
    assert model.calls[0].user.startswith("summarize\n\n")
    assert model.calls[1].user.startswith("summarize\n\n")
    combine = model.calls[2]
    assert "Partial answers, in order:" in combine.user
    assert "part one" in combine.user and "part two" in combine.user
    # disclosure BEFORE spend, pinned format (the golden note)
    err = capsys.readouterr().err
    assert "note: line 1 ~87,500 tokens over budget - 2 chunks + 1 combine call" in err


async def test_the_combine_recurses_when_partials_overflow() -> None:
    # ollama table budget: 8000 * 0.6 - 500 = 4300 tokens (probe finds nothing)
    model = Chat(
        ["y" * 8_000, "y" * 8_000, "y" * 8_000, "z" * 400, "FINAL"],
        ref=ModelRef("ollama", "fake"),
    )
    text = "word " * 8_000  # 40k chars ≈ 10k tokens → 3 chunks of ≤ 4300
    out = io.StringIO()
    code = await run_map(
        _map_request(), Ctx(model, window=None), stdin=io.StringIO(text + "\n"), stdout=out
    )
    assert code is ExitCode.OK
    assert out.getvalue() == "FINAL\n"
    # 3 chunk calls; the 3 partials (2000 tokens each) overflow 4300 → one
    # intermediate combine folds two of them (the reduce-tree shape), the lone
    # third passes through unfolded, then the final combine lands
    assert len(model.calls) == 5
    assert "Partial answers" in model.calls[3].user
    assert "Partial answers" in model.calls[4].user


async def test_map_probe_can_widen_and_allow() -> None:
    model = Chat(["summary"])
    out = io.StringIO()
    context = Ctx(model, window=1_000_000)  # the probe discovers a huge window
    code = await run_map(_map_request(), context, stdin=io.StringIO(BIG + "\n"), stdout=out)
    assert code is ExitCode.OK
    assert len(model.calls) == 1  # widened past the table, the call proceeded
    assert context.probes == 1  # asked exactly once


# --- braces/schema: extract per chunk + ONE merge call ----------------------------


async def test_structured_map_extracts_per_chunk_then_merges_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from smartpipe.engine.prompts import MAP_MERGE_SYSTEM

    model = Chat(['{"v": "a"}', '{"v": "b"}', '{"v": "merged"}'])
    out = io.StringIO()
    code = await run_map(
        _map_request("Extract {v}"),
        Ctx(model, window=None),
        stdin=io.StringIO(BIG + "\n"),
        stdout=out,
    )
    assert code is ExitCode.OK
    assert json.loads(out.getvalue()) == {
        "v": "merged",
        "__source": {"path": "-", "as": "lines", "line": 1},
    }
    assert len(model.calls) == 3  # 2 chunk extractions + 1 merge
    merge = model.calls[2]
    assert merge.system == MAP_MERGE_SYSTEM
    assert merge.json_schema == model.calls[0].json_schema  # the SAME schema
    assert '{"v": "a"}' in merge.user and '{"v": "b"}' in merge.user
    err = capsys.readouterr().err
    assert "note: line 1 ~87,500 tokens over budget - 2 chunks + 1 merge call" in err


async def test_extend_merges_chunked_extractions_onto_the_base_record() -> None:
    model = Chat(['{"label": "a"}', '{"label": "b"}', '{"label": "merged"}'])
    out = io.StringIO()
    text = "word " * 8_000  # ≈ 10k tokens past the ollama 4300 budget → 3 chunks
    model.ref = ModelRef("ollama", "fake")
    code = await run_extend(
        ExtendRequest(
            prompt="Add {label}",
            schema_path=None,
            model_flag=None,
            output=OutputFormat.AUTO,
            concurrency_flag=None,
        ),
        Ctx(model, window=None),
        stdin=io.StringIO(text + "\n"),
        stdout=out,
    )
    assert code is ExitCode.OK
    row = json.loads(out.getvalue())
    assert row["label"] == "merged"  # the merged extraction, not a partial
    assert row["text"] == text  # the base record survives (extend's law)
    assert len(model.calls) == 4  # 3 chunk extractions + 1 merge


# --- --whole: the old refusal, verbatim -------------------------------------------


async def test_whole_map_refuses_an_over_window_item_with_the_split_recipe(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = Chat()
    out = io.StringIO()
    code = await run_map(
        _map_request(whole=True),
        Ctx(model, window=None),
        stdin=io.StringIO(BIG + "\n"),
        stdout=out,
    )
    assert code is ExitCode.ALL_FAILED
    assert model.calls == []  # refused BEFORE any spend
    err = capsys.readouterr().err
    assert "token budget — split it first" in err
    assert "smartpipe split" in err


async def test_whole_filter_refuses_instead_of_chunk_judging(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = ChunkJudge()
    out = io.StringIO()
    code = await run_filter(
        FilterRequest(
            condition="mentions the needle",
            invert=False,
            model_flag=None,
            concurrency_flag=None,
            whole=True,
        ),
        Ctx(model, window=None),
        stdin=io.StringIO(BIG + "\n"),
        stdout=out,
    )
    assert code is ExitCode.ALL_FAILED
    assert model.calls == []
    assert "token budget — split it first" in capsys.readouterr().err


# --- filter: ANY-true chunk judgment, early exit, disclosed ------------------------


async def test_filter_judges_chunks_and_any_match_keeps_the_item(
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    assert len(model.calls) == 2  # judged chunk-wise, stopped at the match
    assert "NEEDLE" in model.calls[-1]  # short-circuited at the matching chunk
    err = capsys.readouterr().err
    # both disclosures, pinned format (the golden notes)
    assert "note: line 1 ~87,505 tokens over budget - 2 chunks, any-true judge" in err
    assert "note: line 1: matched in chunk 2/2" in err


async def test_filter_early_exit_skips_the_remaining_chunks() -> None:
    model = ChunkJudge()
    out = io.StringIO()
    line = "the NEEDLE leads " + "word " * 70_000  # the match sits in chunk 1
    code = await run_filter(
        FilterRequest(
            condition="mentions the needle", invert=False, model_flag=None, concurrency_flag=None
        ),
        Ctx(model, window=None),
        stdin=io.StringIO(line + "\n"),
        stdout=out,
    )
    assert code is ExitCode.OK
    assert len(model.calls) == 1  # chunk 2 was never paid for


# --- --max-calls counts every chunk call -------------------------------------------


async def test_call_budget_counts_chunk_calls_and_stops_mid_item(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    from smartpipe.models.budget import CallBudget, budgeted_chat

    inner = Chat(["part one", "part two", "never reached"])
    budget = CallBudget(limit=2, stop=asyncio.Event())
    model = budgeted_chat(inner, budget)  # the container's wrapper, for real
    out = io.StringIO()
    code = await run_map(
        _map_request(),
        Ctx(model, window=None),
        stdin=io.StringIO(BIG + "\n"),
        stdout=out,
    )
    assert code is ExitCode.ALL_FAILED  # the one item could not finish
    assert len(inner.calls) == 2  # the cap held MID-ITEM: the combine never ran
    assert "call budget reached (--max-calls 2)" in capsys.readouterr().err


# --- the media-aware gate ----------------------------------------------------------


async def test_window_gate_counts_media_alongside_text() -> None:
    import struct

    from smartpipe.models.base import ImageData
    from smartpipe.verbs.common import WindowGate

    async def no_window() -> int | None:
        return None

    gate = WindowGate(provider="ollama", model_name="fake", overhead=500, window=no_window)
    assert await gate.budget_for_oversized("tiny text") is None  # text alone fits
    png = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", 9000, 9000)
        + b"\x08\x06\x00\x00\x00"
    )
    over = await gate.budget_for_oversized("tiny text", (ImageData(png, "image/png"),))
    assert over is not None  # the same text + a 81-megapixel image overflows
    assert over.estimate > over.budget
    assert over.media_tokens > 0  # the media share travels with the verdict


async def test_media_alone_past_the_window_is_a_per_item_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bytes can't be text-chunked: media alone over the budget keeps the
    refusal (whose recipe — split — is exactly the fix)."""
    import struct

    from smartpipe.engine.prompts import MapPlan
    from smartpipe.io.items import Item, ItemSource
    from smartpipe.models.base import ImageData
    from smartpipe.verbs.common import Oversize
    from smartpipe.verbs.oversize import transform_oversized

    png = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", 9000, 9000)
        + b"\x08\x06\x00\x00\x00"
    )
    item = Item(
        raw="x",
        text="x",
        data=None,
        source=ItemSource(kind="stdin", name="-", index=0),
        media=(ImageData(png, "image/png"),),
    )
    model = Chat()
    from smartpipe.core.errors import ItemError

    with pytest.raises(ItemError, match="token budget — split it first"):
        await transform_oversized(
            model,  # type: ignore[arg-type]
            MapPlan("plain", None, "system"),
            "describe",
            item,
            Oversize(estimate=135_002, budget=4_300, media_tokens=135_001),
        )
    assert model.calls == []  # refused before any spend


# --- repair + --keep-invalid on the chunked ladder ---------------------------------


async def test_chunk_extraction_gets_the_standard_single_repair() -> None:
    model = Chat(["not json", '{"v": "repaired"}', '{"v": "b"}', '{"v": "merged"}'])
    out = io.StringIO()
    code = await run_map(
        _map_request("Extract {v}"),
        Ctx(model, window=None),
        stdin=io.StringIO(BIG + "\n"),
        stdout=out,
    )
    assert code is ExitCode.OK
    assert json.loads(out.getvalue()) == {
        "v": "merged",
        "__source": {"path": "-", "as": "lines", "line": 1},
    }
    assert len(model.calls) == 4  # chunk 1 + its repair, chunk 2, merge
    assert "That was invalid" in model.calls[1].user  # the repair prompt


async def test_failed_merge_with_keep_invalid_becomes_a_marker_row() -> None:
    model = Chat(['{"v": "a"}', '{"v": "b"}', "bad merge", "still bad"])
    out = io.StringIO()
    code = await run_map(
        _map_request("Extract {v}", keep_invalid=True),
        Ctx(model, window=None),
        stdin=io.StringIO(BIG + "\n"),
        stdout=out,
    )
    assert code is ExitCode.OK  # a kept row is a result, not a failure
    row = json.loads(out.getvalue())
    assert row["__invalid"] is True
    assert row["__raw"] == "still bad"
    assert len(model.calls) == 4  # 2 chunks, the merge, and its one repair


async def test_failed_merge_without_keep_invalid_skips_the_item(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = Chat(['{"v": "a"}', '{"v": "b"}', "bad merge", "still bad"])
    out = io.StringIO()
    code = await run_map(
        _map_request("Extract {v}"),
        Ctx(model, window=None),
        stdin=io.StringIO(BIG + "\n"),
        stdout=out,
    )
    assert code is ExitCode.ALL_FAILED
    assert out.getvalue() == ""
    assert "skipped: line 1" in capsys.readouterr().err


async def test_no_matching_chunk_drops_the_item() -> None:
    model = ChunkJudge()
    out = io.StringIO()
    code = await run_filter(
        FilterRequest(
            condition="mentions the needle", invert=False, model_flag=None, concurrency_flag=None
        ),
        Ctx(model, window=None),
        stdin=io.StringIO(BIG + "\n"),  # no NEEDLE anywhere
        stdout=out,
    )
    assert code is ExitCode.OK  # zero matches is success
    assert out.getvalue() == ""
    assert len(model.calls) == 2  # every chunk was judged before giving up


async def test_combine_stops_folding_when_no_level_can_shrink() -> None:
    """Partials that each fit but pair-wise overflow: singleton groups mean no
    progress — go straight to the final combine rather than looping."""
    model = Chat(
        ["y" * 12_000, "y" * 12_000, "y" * 12_000, "FINAL"],
        ref=ModelRef("ollama", "fake"),
    )
    text = "word " * 8_000  # 3 chunks under the 4300 ollama budget
    out = io.StringIO()
    code = await run_map(
        _map_request(), Ctx(model, window=None), stdin=io.StringIO(text + "\n"), stdout=out
    )
    assert code is ExitCode.OK
    assert out.getvalue() == "FINAL\n"
    # partials are 3000 tokens each: any two overflow 4300 → singleton groups
    # → one final combine, not an endless folding loop
    assert len(model.calls) == 4


# --- bisect-on-context-400 (item 3) -------------------------------------------------


class FlakyChat(Chat):
    """Raises a context-length 400 on scripted call ordinals, replies otherwise
    (replies consumed only by successful calls)."""

    def __init__(
        self,
        replies: Sequence[str],
        *,
        overflow_calls: frozenset[int] = frozenset(),
        always_overflow: bool = False,
        ref: ModelRef | None = None,
    ) -> None:
        super().__init__(replies, ref=ref)
        self.overflow_calls = overflow_calls
        self.always_overflow = always_overflow
        self.answered = 0

    async def complete(self, request: CompletionRequest) -> str:
        from smartpipe.core.errors import ItemError

        self.calls.append(request)
        if self.always_overflow or len(self.calls) in self.overflow_calls:
            raise ItemError("openai error 400: context_length_exceeded")
        reply = self.replies[min(self.answered, len(self.replies) - 1)]
        self.answered += 1
        return reply


MACHINE_CUT_ROW = json.dumps(
    {
        "text": "word " * 400,  # ~500 tokens — well within every table budget
        "__source": {"path": "report.pdf", "as": "tokens", "segment": 3},
    }
)


async def test_auto_chunk_that_still_overflows_bisects_and_retries(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 2 auto-chunks; the FIRST chunk call draws a 400 → its halves retry
    model = FlakyChat(["half one", "half two", "part two", "FINAL"], overflow_calls=frozenset({1}))
    out = io.StringIO()
    code = await run_map(
        _map_request(), Ctx(model, window=None), stdin=io.StringIO(BIG + "\n"), stdout=out
    )
    assert code is ExitCode.OK
    assert out.getvalue() == "FINAL\n"
    assert len(model.calls) == 5  # chunk1 (400) + 2 halves + chunk2 + combine
    err = capsys.readouterr().err
    assert err.count("note: line 1 chunk re-split: provider rejected the estimate") == 1


async def test_machine_cut_item_bisects_on_a_context_400(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # the item FITS the estimated budget, but the wire says otherwise — and it
    # is machine-cut (as: tokens), so smartpipe halves it instead of skipping
    model = FlakyChat(["a", "b", "COMBINED"], overflow_calls=frozenset({1}))
    out = io.StringIO()
    code = await run_map(
        _map_request(),
        Ctx(model, window=None),
        stdin=io.StringIO(MACHINE_CUT_ROW + "\n"),
        stdout=out,
    )
    assert code is ExitCode.OK
    row = json.loads(out.getvalue())
    assert row["result"] == "COMBINED"  # records in, records out — spine intact
    assert len(model.calls) == 4  # the 400, 2 halves, 1 combine
    err = capsys.readouterr().err
    assert "note: report.pdf chunk re-split: provider rejected the estimate" in err


async def test_user_cut_item_never_bisects(capsys: pytest.CaptureFixture[str]) -> None:
    model = FlakyChat([], always_overflow=True)
    out = io.StringIO()
    code = await run_map(
        _map_request(),
        Ctx(model, window=None),
        stdin=io.StringIO("an ordinary user line\n"),  # as: lines — the USER's cut
        stdout=out,
    )
    assert code is ExitCode.ALL_FAILED
    assert len(model.calls) == 1  # one honest failure, zero re-splits
    err = capsys.readouterr().err
    assert "chunk re-split" not in err
    assert "skipped: line 1" in err


async def test_bisection_depth_is_bounded(capsys: pytest.CaptureFixture[str]) -> None:
    model = FlakyChat([], always_overflow=True)
    out = io.StringIO()
    code = await run_map(
        _map_request(),
        Ctx(model, window=None),
        stdin=io.StringIO(MACHINE_CUT_ROW + "\n"),
        stdout=out,
    )
    assert code is ExitCode.ALL_FAILED  # bounded retreat, then an honest skip
    assert len(model.calls) <= 40  # 1 + two chunks' bounded binary trees, never unbounded
    err = capsys.readouterr().err
    assert err.count("chunk re-split: provider rejected the estimate") == 1  # once per row
    assert "skipped: report.pdf" in err


async def test_machine_cut_filter_judges_halves_after_a_400(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FlakyJudge:
        """400 on the first (whole-item) call; then judges by needle."""

        def __init__(self) -> None:
            self.ref = ModelRef("openai", "gpt-4o-mini")
            self.calls: list[str] = []

        async def complete(self, request: CompletionRequest) -> str:
            from smartpipe.core.errors import ItemError

            self.calls.append(request.user)
            if len(self.calls) == 1:
                raise ItemError("This model's maximum context length is 8192 tokens")
            verdict = "true" if "NEEDLE" in request.user else "false"
            return f'{{"match": {verdict}}}'

    row = json.dumps(
        {
            "text": "filler prose " * 150 + "the NEEDLE sits at the end",
            "__source": {"path": "report.pdf", "as": "tokens", "segment": 2},
        }
    )
    model = FlakyJudge()
    out = io.StringIO()
    code = await run_filter(
        FilterRequest(
            condition="mentions the needle", invert=False, model_flag=None, concurrency_flag=None
        ),
        Ctx(model, window=None),
        stdin=io.StringIO(row + "\n"),
        stdout=out,
    )
    assert code is ExitCode.OK
    assert out.getvalue() == row + "\n"  # the WHOLE row survives, byte-verbatim
    assert len(model.calls) == 3  # the 400, then two halves (needle in the second)
    err = capsys.readouterr().err
    assert "note: report.pdf chunk re-split: provider rejected the estimate" in err
    assert "matched in chunk 2/2" in err


async def test_whole_disables_the_resplit(capsys: pytest.CaptureFixture[str]) -> None:
    model = FlakyChat([], always_overflow=True)
    out = io.StringIO()
    code = await run_map(
        _map_request(whole=True),
        Ctx(model, window=None),
        stdin=io.StringIO(MACHINE_CUT_ROW + "\n"),
        stdout=out,
    )
    assert code is ExitCode.ALL_FAILED
    assert len(model.calls) == 1  # --whole: process whole or per-item error
    assert "chunk re-split" not in capsys.readouterr().err


# --- the disclosure formats (the golden pins) --------------------------------------


def test_note_formats_are_pinned() -> None:
    from smartpipe.verbs.oversize import judge_note, matched_note, transform_note

    assert (
        transform_note("report.pdf", 48_200, 7, structured=False)
        == "report.pdf ~48,200 tokens over budget - 7 chunks + 1 combine call"
    )
    assert (
        transform_note("report.pdf", 48_200, 7, structured=True)
        == "report.pdf ~48,200 tokens over budget - 7 chunks + 1 merge call"
    )
    assert (
        judge_note("report.pdf", 48_200, 7)
        == "report.pdf ~48,200 tokens over budget - 7 chunks, any-true judge"
    )
    assert matched_note("report.pdf", 3, 7) == "report.pdf: matched in chunk 3/7"
    from smartpipe.verbs.oversize import resplit_note

    assert resplit_note("report.pdf") == "report.pdf chunk re-split: provider rejected the estimate"
