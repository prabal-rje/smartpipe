"""The graph verb's paid half (wave G2): full extraction, hybrid naming,
adopted pipe-in edges — FakeChat end to end, the wordings pinned."""

from __future__ import annotations

import asyncio
import io
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.models.base import ModelRef
from smartpipe.models.budget import CallBudget, budgeted_chat
from smartpipe.verbs.graph import GraphRequest, run_graph
from smartpipe.verbs.graphfull import CONFIRM_PARTIAL, extraction_prompt
from tests.verbs.test_graph import FakeEmbedder, FakeFinder

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from smartpipe.models.base import ChatModel, CompletionRequest


class FakeChat:
    """Scriptable ChatModel: replies keyed by a substring of the user message
    (concurrency-safe), with a sequential list as the fallback."""

    def __init__(
        self,
        replies: Sequence[str] = (),
        *,
        by_content: Mapping[str, str] | None = None,
        default: str = '{"triples": []}',
    ) -> None:
        self.replies = list(replies)
        self.by_content = dict(by_content or {})
        self.default = default
        self.ref = ModelRef("ollama", "fake")
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        for needle, reply in self.by_content.items():
            if needle in request.user:
                return reply
        if self.replies:
            return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        return self.default


@dataclass
class PaidContext:
    """The full graph seam: the free half's fakes plus a scriptable chat wire."""

    chat: ChatModel
    finder: FakeFinder
    embedder: FakeEmbedder
    concurrency_value: int = 1  # sequential: deterministic call order under test
    finder_labels: tuple[str, ...] = ()
    chat_resolutions: int = field(default=0)

    def entity_finder(self, labels: Sequence[str]) -> FakeFinder:
        self.finder_labels = tuple(labels)
        return self.finder

    def fold_embedder(self) -> FakeEmbedder:
        return self.embedder

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        self.chat_resolutions += 1
        return self.chat

    def document_parser(self, flag: str | None = None) -> None:
        return None

    def concurrency(self, flag: int | None = None) -> int:
        return self.concurrency_value


PEOPLE = {"Ann": "person", "Bob": "person", "Acme": "company"}


def _context(
    chat: ChatModel,
    *,
    known: dict[str, str] | None = None,
    vectors: dict[str, tuple[float, ...]] | None = None,
) -> PaidContext:
    return PaidContext(
        chat=chat,
        finder=FakeFinder(known or PEOPLE),
        embedder=FakeEmbedder(vectors or {}),
    )


async def _run(
    request: GraphRequest,
    context: PaidContext,
    stdin_text: str = "",
    *,
    stop: asyncio.Event | None = None,
    ask: Callable[[str], bool] | None = None,
    budget: CallBudget | None = None,
) -> tuple[ExitCode, str]:
    out = io.StringIO()
    code = await run_graph(
        request,
        context,
        stdin=io.StringIO(stdin_text),
        stdout=out,
        stop=stop,
        clock=lambda: 0.0,
        ask=ask,
        budget=budget,
    )
    return code, out.getvalue()


def _edges(out: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in out.splitlines()]


def _source_records(edge: dict[str, object]) -> list[dict[str, object]]:
    """The edge's ``sources`` list, shape-narrowed the untrusted-JSON way."""
    from smartpipe.core.jsontools import as_items, as_record

    return [
        dict(record)
        for entry in as_items(edge.get("sources")) or ()
        if (record := as_record(entry)) is not None
    ]


def _source_labels(edge: dict[str, object]) -> list[str]:
    return [
        label for record in _source_records(edge) if isinstance(label := record.get("label"), str)
    ]


def triples(*rows: tuple[str, str, str]) -> str:
    return json.dumps({"triples": [{"subject": s, "relation": r, "object": o} for s, r, o in rows]})


# --- FULL mode: the focus-prompt extraction path -----------------------------------


async def test_full_mode_extracts_folds_and_serializes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    chat = FakeChat(
        by_content={
            "Acme Corp pays": triples(("Acme Corp", "pays", "Bob")),
            "Acme Corporation pays": triples(("Acme Corporation", "pays", "Bob")),
        }
    )
    vectors = {"Acme Corp": (1.0, 0.0), "Acme Corporation": (1.0, 0.0), "Bob": (0.0, 1.0)}
    code, out = await _run(
        GraphRequest(focus="who pays whom"),
        _context(chat, vectors=vectors),
        "Acme Corp pays Bob\nAcme Corporation pays Bob\n",
    )
    assert code is ExitCode.OK
    assert _edges(out) == [
        {
            "source": "Acme Corp",
            "relation": "pays",
            "target": "Bob",
            "weight": 2,  # two chunks assert the canonical triple
            "sources": [
                {"path": "-", "as": "lines", "line": 1},
                {"path": "-", "as": "lines", "line": 2},
            ],
        }
    ]
    err = capsys.readouterr().err
    assert "note: ~2 extraction calls across 2 files" in err
    assert "note: graph: 3 entities (2 folded) · 1 edges · 0 tok" in err


async def test_full_mode_instruction_carries_the_focus_preamble() -> None:
    chat = FakeChat()
    await _run(GraphRequest(focus="who pays whom"), _context(chat), "Ann met Bob\n")
    (call,) = chat.calls
    assert call.user.startswith("who pays whom\n\nExtract triples:")
    assert "<input>\nAnn met Bob\n</input>" in call.user


async def test_enum_ontology_reaches_the_schema() -> None:
    from smartpipe.core.jsontools import as_record

    chat = FakeChat()
    await _run(
        GraphRequest(focus="who pays whom", entities="person, company", relations="pays, owns"),
        _context(chat),
        "Ann met Bob\n",
    )
    (call,) = chat.calls
    assert call.json_schema is not None
    outer = as_record(as_record(call.json_schema.get("properties")).get("triples"))  # type: ignore[union-attr]
    inner = as_record(as_record(outer.get("items")).get("properties"))  # type: ignore[union-attr]
    assert inner is not None
    assert as_record(inner.get("relation")) == {"enum": ["pays", "owns"]}
    assert as_record(inner.get("subject_type")) == {"enum": ["person", "company"]}
    assert as_record(inner.get("object_type")) == {"enum": ["person", "company"]}


def test_extraction_prompt_is_the_object_list_braces_path() -> None:
    assert extraction_prompt("who pays whom", None, None) == (
        "who pays whom\n\nExtract {triples {subject string, relation string, "
        "object string}[]}: every relationship this item asserts, "
        "with short canonical entity names."
    )
    typed = extraction_prompt("deals", ("person", "company"), ("pays", "owns"))
    assert "{triples {subject string, subject_type enum(person, company), " in typed
    assert "relation enum(pays, owns), object string, " in typed
    assert "object_type enum(person, company)}[]}" in typed


async def test_full_mode_dedupes_repeated_triples_within_one_chunk() -> None:
    reply = triples(("Ann", "pays", "Bob"), ("Ann", "pays", "Bob"))
    code, out = await _run(
        GraphRequest(focus="pays"), _context(FakeChat([reply])), "Ann pays Bob twice\n"
    )
    assert code is ExitCode.OK
    (edge,) = _edges(out)
    assert edge["weight"] == 1  # weight counts chunks asserting, not repetitions


async def test_full_mode_skips_invalid_replies_and_exits_partial(
    capsys: pytest.CaptureFixture[str],
) -> None:
    chat = FakeChat(
        by_content={
            "Ann pays Bob": triples(("Ann", "pays", "Bob")),
            "static": "not json at all",
        }
    )
    code, out = await _run(GraphRequest(focus="pays"), _context(chat), "Ann pays Bob\nstatic\n")
    assert code is ExitCode.PARTIAL
    assert len(_edges(out)) == 1
    assert "skipped: line 2" in capsys.readouterr().err


# --- the pre-flight cost plan (pinned wordings, ledger 66f) --------------------------


async def test_preflight_plan_names_the_short_belt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop = asyncio.Event()
    budget = CallBudget(limit=2, stop=stop)
    chat = FakeChat([triples(("Ann", "pays", "Bob"))])
    context = _context(budgeted_chat(chat, budget))
    code, _ = await _run(
        GraphRequest(focus="pays"),
        context,
        "one Ann Bob\ntwo Ann Bob\nthree Ann Bob\n",
        stop=stop,
        budget=budget,
    )
    err = capsys.readouterr().err
    assert "note: ~3 extraction calls across 3 files; belt is 2 — the graph will be partial" in err
    assert code is ExitCode.PARTIAL


async def test_preflight_nudges_when_beltless_past_one_hundred_calls(
    capsys: pytest.CaptureFixture[str],
) -> None:
    corpus = "".join(f"Ann row {n}\n" for n in range(101))
    code, _ = await _run(GraphRequest(focus="pays"), _context(FakeChat()), corpus)
    assert code is ExitCode.OK
    assert "note: ~101 extraction calls across 101 files — no belt set" in capsys.readouterr().err


async def test_preflight_stays_plain_when_the_belt_covers_the_need(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop = asyncio.Event()
    budget = CallBudget(limit=5, stop=stop)
    chat = budgeted_chat(FakeChat(), budget)
    code, _ = await _run(
        GraphRequest(focus="pays"), _context(chat), "Ann\n", stop=stop, budget=budget
    )
    assert code is ExitCode.OK
    err = capsys.readouterr().err
    assert "note: ~1 extraction calls across 1 file\n" in err
    assert "belt is" not in err
    assert "no belt set" not in err


async def test_tty_confirm_decline_spends_nothing_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop = asyncio.Event()
    budget = CallBudget(limit=1, stop=stop)
    chat = FakeChat()
    context = _context(budgeted_chat(chat, budget))
    asked: list[str] = []

    def decline(question: str) -> bool:
        asked.append(question)
        return False

    code, out = await _run(
        GraphRequest(focus="pays"),
        context,
        "Ann one\nBob two\n",
        stop=stop,
        ask=decline,
        budget=budget,
    )
    assert code is ExitCode.OK
    assert out == ""  # nothing extracted, nothing written
    assert asked == ["proceed with a partial graph? [y/N]"]
    assert asked == [CONFIRM_PARTIAL]
    assert chat.calls == []  # declined at the plan: zero spend
    assert "the graph will be partial" in capsys.readouterr().err


async def test_piped_run_never_prompts_and_the_belt_governs() -> None:
    stop = asyncio.Event()
    budget = CallBudget(limit=1, stop=stop)
    chat = FakeChat([triples(("Ann", "pays", "Bob"))])
    context = _context(budgeted_chat(chat, budget))
    # ask=None + a StringIO stdin (isatty False) = the piped path: no prompt
    code, out = await _run(
        GraphRequest(focus="pays"), context, "Ann one\nBob two\n", stop=stop, budget=budget
    )
    assert code is ExitCode.PARTIAL
    assert len(chat.calls) == 1  # the belt, not a prompt, capped the run
    assert len(_edges(out)) == 1


async def test_belt_census_wording_and_partial_graph(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop = asyncio.Event()
    budget = CallBudget(limit=1, stop=stop)
    chat = FakeChat([triples(("Ann", "pays", "Bob"))])
    context = _context(budgeted_chat(chat, budget))
    code, out = await _run(
        GraphRequest(focus="pays"),
        context,
        "one Ann Bob\ntwo Ann Bob\nthree Ann Bob\n",
        stop=stop,
        budget=budget,
    )
    assert code is ExitCode.PARTIAL  # never exit 0 on a partial
    (edge,) = _edges(out)  # the graph is built from what WAS extracted
    assert (edge["source"], edge["target"]) == ("Ann", "Bob")
    err = capsys.readouterr().err
    assert (
        "note: belt hit — 1 of 3 chunks extracted; the graph is partial "
        "(rerun raises the belt; cache makes it cheap)"
    ) in err


async def test_full_receipt_embeds_the_run_meter_when_tokens_flowed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from smartpipe.io import metering

    chat = FakeChat([triples(("Ann", "pays", "Bob"))])
    metering.add_tokens(tokens_in=48_000, tokens_out=12_000)
    code, _ = await _run(GraphRequest(focus="pays"), _context(chat), "Ann pays Bob\n")
    assert code is ExitCode.OK
    err = capsys.readouterr().err
    assert "note: graph: 2 entities (0 folded) · 1 edges · run: ↑48.0k ↓12.0k tok" in err


# --- HYBRID: --name-top N ------------------------------------------------------------


async def test_hybrid_names_the_strongest_edges_and_replaces_co_occurs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    chat = FakeChat(
        by_content={"target: Bob": '{"relation": "pays"}'}, default='{"relation": "knows"}'
    )
    code, out = await _run(
        GraphRequest(focus="who pays whom", name_top=5),
        _context(chat),
        "Ann pays Bob\nBob thanks Ann\n",
    )
    assert code is ExitCode.OK
    (edge,) = _edges(out)
    assert (edge["source"], edge["relation"], edge["target"], edge["weight"]) == (
        "Ann",
        "pays",
        "Bob",
        2,
    )
    err = capsys.readouterr().err
    assert "note: graph: 2 entities (0 folded) · 1 edges · 1 named · 0 tok" in err


async def test_hybrid_naming_call_carries_pair_windows_focus_and_enum() -> None:
    chat = FakeChat(default='{"relation": "pays"}')
    await _run(
        GraphRequest(focus="who pays whom", name_top=1, relations="pays, owns"),
        _context(chat),
        "Ann pays Bob\nBob thanks Ann\n",
    )
    (call,) = chat.calls
    assert "source: Ann" in call.user
    assert "target: Bob" in call.user
    assert "they appear together in:" in call.user
    assert "[1] Ann pays Bob" in call.user
    assert "[2] Bob thanks Ann" in call.user
    assert "Focus: who pays whom" in call.user
    assert call.json_schema is not None
    from smartpipe.core.jsontools import as_record

    relation = as_record(as_record(call.json_schema.get("properties")).get("relation"))  # type: ignore[union-attr]
    assert relation == {"enum": ["pays", "owns"]}


async def test_hybrid_composes_without_fast_and_with_it() -> None:
    for fast in (False, True):
        chat = FakeChat(default='{"relation": "pays"}')
        code, out = await _run(
            GraphRequest(fast=fast, name_top=1), _context(chat), "Ann pays Bob\n"
        )
        assert code is ExitCode.OK
        (edge,) = _edges(out)
        assert edge["relation"] == "pays"


async def test_hybrid_belt_shortfall_keeps_co_occurs_and_discloses(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop = asyncio.Event()
    budget = CallBudget(limit=1, stop=stop)
    chat = FakeChat(default='{"relation": "pays"}')
    context = _context(budgeted_chat(chat, budget))
    code, out = await _run(
        GraphRequest(focus="who pays whom", name_top=2),
        context,
        "Ann met Bob\nAnn met Bob\nAnn met Acme\n",
        stop=stop,
        budget=budget,
    )
    assert code is ExitCode.PARTIAL
    edges = _edges(out)
    assert [(e["source"], e["target"], e["relation"]) for e in edges] == [
        ("Ann", "Bob", "pays"),  # named before the belt hit
        ("Acme", "Ann", "co-occurs"),  # the unnamed remainder keeps its label
    ]
    err = capsys.readouterr().err
    assert "note: named 1 of 2 (belt); 1 strongest remain co-occurs" in err
    assert "· 2 edges · 1 named · 0 tok" in err


async def test_hybrid_on_empty_input_is_ok_and_silent() -> None:
    chat = FakeChat()
    code, out = await _run(GraphRequest(name_top=3), _context(chat), "")
    assert code is ExitCode.OK
    assert out == ""
    assert chat.calls == []


# --- ADOPT: edge-shaped records on stdin ---------------------------------------------


async def test_adopt_subject_relation_object_rows_fold_and_serialize(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = (
        '{"subject": "Ann", "relation": "pays", "object": "Bob"}\n'
        '{"subject": "Ann", "relation": "pays", "object": "Bob"}\n'
    )
    chat = FakeChat()
    code, out = await _run(GraphRequest(), _context(chat), rows)
    assert code is ExitCode.OK
    assert chat.calls == []  # adoption skips extraction entirely
    (edge,) = _edges(out)
    assert edge == {
        "source": "Ann",
        "relation": "pays",
        "target": "Bob",
        "weight": 2,
        "sources": [
            {"path": "-", "as": "jsonl", "line": 1},
            {"path": "-", "as": "jsonl", "line": 2},
        ],
    }
    assert "note: graph: 2 entities (0 folded) · 1 edges (0 pruned) · 0 tok" in (
        capsys.readouterr().err
    )


async def test_adopt_source_target_rows_keep_weight_relation_and_provenance() -> None:
    row = json.dumps(
        {
            "source": "Ann",
            "target": "Bob",
            "weight": 3,
            "sources": [{"path": "a.txt", "as": "lines", "line": 4}],
        }
    )
    named = json.dumps({"source": "Ann", "relation": "owes", "target": "Cid"})
    code, out = await _run(GraphRequest(), _context(FakeChat()), f"{row}\n{named}\n")
    assert code is ExitCode.OK
    edges = _edges(out)
    assert edges[0] == {
        "source": "Ann",
        "relation": "co-occurs",  # no relation on the row: the neutral label
        "target": "Bob",
        "weight": 3,  # adopted, not recounted
        "sources": [{"path": "a.txt", "as": "lines", "line": 4}],
    }
    assert (edges[1]["source"], edges[1]["relation"], edges[1]["target"]) == (
        "Ann",
        "owes",
        "Cid",
    )


async def test_adopt_canonicalizes_near_duplicate_names() -> None:
    vectors = {
        "Acme Corp": (1.0, 0.0),
        "Acme Corporation": (1.0, 0.0),
        "Ann": (0.0, 1.0),
    }
    rows = (
        '{"source": "Acme Corp", "target": "Ann"}\n'
        '{"source": "Acme Corporation", "target": "Ann"}\n'
    )
    code, out = await _run(GraphRequest(), _context(FakeChat(), vectors=vectors), rows)
    assert code is ExitCode.OK
    (edge,) = _edges(out)
    assert (edge["source"], edge["target"], edge["weight"]) == ("Acme Corp", "Ann", 2)


async def test_mixed_stdin_refuses_before_any_output() -> None:
    rows = '{"source": "Ann", "target": "Bob"}\nplain text line\n'
    with pytest.raises(UsageFault, match="line 2 isn't an edge record") as caught:
        await _run(GraphRequest(), _context(FakeChat()), rows)
    assert "--fast" in str(caught.value)


# --- the refusal matrix ---------------------------------------------------------------


async def test_bare_graph_on_non_edge_stdin_names_the_three_forms() -> None:
    with pytest.raises(UsageFault) as caught:
        await _run(GraphRequest(), _context(FakeChat()), "hello\n")
    words = str(caught.value)
    assert "--fast" in words
    assert "focus prompt" in words
    assert "edge records on stdin" in words


async def test_relations_without_a_model_read_mode_refuses() -> None:
    with pytest.raises(UsageFault, match="--relations"):
        await _run(GraphRequest(fast=True, relations="pays"), _context(FakeChat()), "Ann\n")


async def test_name_top_validates() -> None:
    with pytest.raises(UsageFault, match="--name-top"):
        await _run(GraphRequest(name_top=0), _context(FakeChat()), "Ann\n")


async def test_adopt_at_a_bare_terminal_names_the_three_forms() -> None:
    with pytest.raises(UsageFault, match="three forms"):
        out = io.StringIO()
        await run_graph(
            GraphRequest(), _context(FakeChat()), stdin=_TtyIn(""), stdout=out, clock=lambda: 0.0
        )


async def test_adopt_triple_missing_its_relation_refuses() -> None:
    with pytest.raises(UsageFault, match="isn't an edge record"):
        await _run(GraphRequest(), _context(FakeChat()), '{"subject": "Ann", "object": "Bob"}\n')


async def test_adopt_source_without_target_refuses() -> None:
    with pytest.raises(UsageFault, match="isn't an edge record"):
        await _run(GraphRequest(), _context(FakeChat()), '{"source": "Ann"}\n')


# --- chunking: split's units reused ----------------------------------------------------


async def test_full_mode_splits_oversized_text_and_labels_the_chunks() -> None:
    long_text = " ".join(f"word{n}" for n in range(3000))  # well past 2 000 tokens
    chat = FakeChat(default=triples(("Ann", "pays", "Bob")))
    code, out = await _run(GraphRequest(focus="pays"), _context(chat), f"{long_text}\n")
    assert code is ExitCode.OK
    assert len(chat.calls) > 1  # one call per token chunk
    (edge,) = _edges(out)
    assert edge["weight"] == len(chat.calls)  # every chunk asserted the triple
    first = _source_records(edge)[0]
    assert first["as"] == "tokens"
    assert first["segment"] == 1
    assert str(first["label"]).startswith("line 1 §1/")


async def test_full_mode_routes_image_records_through_the_vision_ladder() -> None:
    import base64

    pixel = base64.b64encode(b"px").decode()
    record = json.dumps({"__media": {"kind": "image", "mime": "image/png", "data_b64": pixel}})
    chat = FakeChat(default=triples(("Ann", "pays", "Bob")))
    code, out = await _run(GraphRequest(focus="pays"), _context(chat), f"{record}\n")
    assert code is ExitCode.OK
    (call,) = chat.calls
    assert len(call.media) == 1  # the figure rode the request natively
    assert len(_edges(out)) == 1


async def test_full_mode_slices_audio_and_sends_it_natively() -> None:
    import base64
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(8000)
        clip.writeframes(b"\x00\x00" * 800)
    encoded = base64.b64encode(buffer.getvalue()).decode()
    record = json.dumps({"__media": {"kind": "audio", "mime": "audio/wav", "data_b64": encoded}})
    chat = FakeChat(default=triples(("Ann", "pays", "Bob")))
    code, out = await _run(GraphRequest(focus="pays"), _context(chat), f"{record}\n")
    assert code is ExitCode.OK
    (call,) = chat.calls
    assert len(call.media) == 1  # one sub-10-minute slice, heard natively
    assert len(_edges(out)) == 1


async def test_full_mode_ignores_blank_triple_fields() -> None:
    reply = json.dumps(
        {
            "triples": [
                {"subject": " ", "relation": "pays", "object": "Bob"},
                {"subject": "Ann", "relation": "", "object": "Bob"},
            ]
        }
    )
    code, out = await _run(GraphRequest(focus="pays"), _context(FakeChat([reply])), "Ann Bob\n")
    assert code is ExitCode.OK
    assert _edges(out) == []  # schema-valid but blank: nothing survived narrowing


def test_chunk_assertions_narrow_untrusted_shapes() -> None:
    from smartpipe.engine.graphkg import SpineRef
    from smartpipe.verbs.graphfull import chunk_assertions

    ref = SpineRef(path="a.txt", cut="lines", position=1)
    result: dict[str, object] = {
        "triples": [
            1,  # not a record
            {"subject": "Ann"},  # missing fields
            {"subject": "Ann", "relation": "pays", "object": "Bob"},
        ]
    }
    (assertion,) = chunk_assertions(result, ref)
    assert (assertion.source, assertion.relation, assertion.target) == ("Ann", "pays", "Bob")
    assert chunk_assertions({}, ref) == []  # no triples field at all


async def test_full_mode_on_empty_input_is_ok_and_silent() -> None:
    chat = FakeChat()
    code, out = await _run(GraphRequest(focus="pays"), _context(chat), "")
    assert code is ExitCode.OK
    assert out == ""
    assert chat.calls == []


async def test_full_mode_interrupt_drains_and_reports(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop = asyncio.Event()

    class StoppingChat(FakeChat):
        async def complete(self, request: CompletionRequest) -> str:
            stop.set()  # a Ctrl-C mid-run: intake halts, in-flight work drains
            return await super().complete(request)

    chat = StoppingChat(default=triples(("Ann", "pays", "Bob")))
    code, out = await _run(
        GraphRequest(focus="pays"), _context(chat), "one Ann\ntwo Ann\nthree Ann\n", stop=stop
    )
    assert code is ExitCode.OK  # 1 processed, 0 skipped: the drain summary tells the rest
    assert len(_edges(out)) == 1
    assert "done: interrupted — 1 processed · 0 skipped" in capsys.readouterr().err


# --- hybrid degradation ------------------------------------------------------------------


async def test_hybrid_empty_relation_reply_keeps_co_occurs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    chat = FakeChat(default='{"relation": ""}')
    code, out = await _run(GraphRequest(focus="pays", name_top=1), _context(chat), "Ann met Bob\n")
    assert code is ExitCode.PARTIAL  # a naming skip is a skip — disclosed, exit 1
    (edge,) = _edges(out)
    assert edge["relation"] == "co-occurs"
    err = capsys.readouterr().err
    assert "the model named no relation" in err
    assert "· 1 edges · 0 named · 0 tok" in err


async def test_hybrid_interrupt_drains_and_reports(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop = asyncio.Event()

    class StoppingChat(FakeChat):
        async def complete(self, request: CompletionRequest) -> str:
            stop.set()
            return await super().complete(request)

    chat = StoppingChat(default='{"relation": "pays"}')
    code, out = await _run(
        GraphRequest(name_top=2),
        _context(chat),
        "Ann met Bob\nAnn met Bob\nAnn met Acme\n",
        stop=stop,
    )
    assert code is ExitCode.OK  # every item was read; the drain summary tells the rest
    edges = _edges(out)
    assert [e["relation"] for e in edges] == ["pays", "co-occurs"]
    assert "done: interrupted" in capsys.readouterr().err


async def test_hybrid_document_window_names_without_snippets() -> None:
    # stdin lines share one document: a document-window edge whose pair never
    # shares a chunk — the naming call carries the pair alone, no passages
    chat = FakeChat(default='{"relation": "pays"}')
    code, out = await _run(
        GraphRequest(name_top=1, window="document"), _context(chat), "Ann here\nBob there\n"
    )
    assert code is ExitCode.OK
    (call,) = chat.calls
    assert "source: Ann" in call.user
    assert "they appear together in:" not in call.user
    (edge,) = _edges(out)
    assert edge["relation"] == "pays"


async def test_hybrid_naming_windows_cap_at_three() -> None:
    chat = FakeChat(default='{"relation": "pays"}')
    corpus = "".join(f"Ann met Bob v{n}\n" for n in range(5))
    await _run(GraphRequest(name_top=1), _context(chat), corpus)
    (call,) = chat.calls
    assert "[3] Ann met Bob v2" in call.user
    assert "[4]" not in call.user


async def test_hybrid_with_no_edges_never_wakes_the_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    chat = FakeChat()
    context = _context(chat)
    code, out = await _run(GraphRequest(name_top=3), context, "Ann alone\n")
    assert code is ExitCode.OK
    assert out == ""
    assert context.chat_resolutions == 0  # nothing to name: zero calls, zero resolution
    assert "· 0 edges · 0 named · 0 tok" in capsys.readouterr().err


# --- odds and ends -------------------------------------------------------------------------


async def test_full_hybrid_and_adopt_share_the_save_path(tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    full = tmp_path / "full.graphml"
    chat = FakeChat(default=triples(("Ann", "pays", "Bob")))
    code, _ = await _run(GraphRequest(focus="pays", save=str(full)), _context(chat), "Ann Bob\n")
    assert code is ExitCode.OK
    assert '<data key="d2">pays</data>' in full.read_text(encoding="utf-8")

    hybrid = tmp_path / "hybrid.graphml"
    namer = FakeChat(default='{"relation": "pays"}')
    code, _ = await _run(
        GraphRequest(name_top=1, save=str(hybrid)), _context(namer), "Ann met Bob\n"
    )
    assert code is ExitCode.OK
    assert '<data key="d2">pays</data>' in hybrid.read_text(encoding="utf-8")

    adopted = tmp_path / "adopted.graphml"
    code, _ = await _run(
        GraphRequest(save=str(adopted)),
        _context(FakeChat()),
        '{"source": "Ann", "relation": "owes", "target": "Bob"}\n',
    )
    assert code is ExitCode.OK
    assert '<data key="d2">owes</data>' in adopted.read_text(encoding="utf-8")


async def test_full_mode_whitespace_only_input_is_ok_and_silent() -> None:
    chat = FakeChat()
    code, out = await _run(GraphRequest(focus="pays"), _context(chat), "   \n")
    assert code is ExitCode.OK
    assert out == ""
    assert chat.calls == []  # nothing worth an extraction call


async def test_adopt_ignores_a_boolean_weight() -> None:
    row = '{"source": "Ann", "target": "Bob", "weight": true}\n'
    code, out = await _run(GraphRequest(), _context(FakeChat()), row)
    assert code is ExitCode.OK
    (edge,) = _edges(out)
    assert edge["weight"] == 1  # JSON true is not a count


async def test_full_mode_labels_figures_beside_text() -> None:
    import base64

    pixel = base64.b64encode(b"px").decode()
    record = json.dumps(
        {
            "text": "Ann pays Bob",
            "__media": {"kind": "image", "mime": "image/png", "data_b64": pixel},
        }
    )
    chat = FakeChat(default=triples(("Ann", "pays", "Bob")))
    code, out = await _run(GraphRequest(focus="pays"), _context(chat), f"{record}\n")
    assert code is ExitCode.OK
    assert len(chat.calls) == 2  # the text chunk and the figure chunk
    (edge,) = _edges(out)
    assert any("img.1" in label for label in _source_labels(edge))


async def test_full_mode_slices_long_audio_into_labeled_minutes() -> None:
    import base64
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(1)  # 1 frame per second: 601 frames = a 601-second clip
        clip.writeframes(b"\x00\x00" * 601)
    encoded = base64.b64encode(buffer.getvalue()).decode()
    record = json.dumps({"__media": {"kind": "audio", "mime": "audio/wav", "data_b64": encoded}})
    chat = FakeChat(default=triples(("Ann", "pays", "Bob")))
    code, out = await _run(GraphRequest(focus="pays"), _context(chat), f"{record}\n")
    assert code is ExitCode.OK
    assert len(chat.calls) == 2  # two ten-minute slices, one call each
    (edge,) = _edges(out)
    labels = _source_labels(edge)
    assert any("§00:00-10:00" in label for label in labels)
    assert any("§10:00-20:00" in label for label in labels)


# --- the TTY asker -----------------------------------------------------------------------


class _TtyIn(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_tty_asker_prompts_on_stderr_and_reads_the_answer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from smartpipe.io import tty
    from smartpipe.verbs.graphfull import tty_asker

    monkeypatch.setattr(tty, "stderr_is_tty", lambda: True)
    accept = tty_asker(_TtyIn("y\n"))
    assert accept is not None
    assert accept(CONFIRM_PARTIAL) is True
    decline = tty_asker(_TtyIn("nope\n"))
    assert decline is not None
    assert decline(CONFIRM_PARTIAL) is False
    assert CONFIRM_PARTIAL in capsys.readouterr().err


def test_tty_asker_is_absent_when_stdin_is_piped() -> None:
    from smartpipe.verbs.graphfull import tty_asker

    assert tty_asker(io.StringIO("data\n")) is None
