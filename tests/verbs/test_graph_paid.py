"""The graph verb's paid half (wave G2): full extraction, hybrid naming,
adopted pipe-in edges — FakeChat end to end, the wordings pinned."""

from __future__ import annotations

import asyncio
import io
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import (
    ExitCode,
    RetryableError,
    SetupFault,
    SourceCounts,
    TooManyFailures,
    TransportError,
    UsageFault,
)
from smartpipe.engine.runner import FailurePolicy
from smartpipe.io import manifest
from smartpipe.models.base import ModelRef
from smartpipe.models.budget import CallBudget, budgeted_chat
from smartpipe.verbs.graph import GraphRequest, run_graph
from smartpipe.verbs.graphfull import CONFIRM_PARTIAL, extraction_prompt
from tests.verbs.test_graph import FakeEmbedder, FakeFinder

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from smartpipe.models.base import ChatModel, CompletionRequest
    from smartpipe.models.resilience import WiredChat


_CANARY_MARK = "Alice pays Bob for the shipment."  # mirrors graphfull._CANARY_SNIPPET


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

    @property
    def extraction_calls(self) -> list[CompletionRequest]:
        """The corpus calls the run actually spent — the A2 schema-canary probe
        (a fixed synthetic snippet, sent once before ingestion) filtered out so
        counts and unpacks reflect real work."""
        return [call for call in self.calls if _CANARY_MARK not in call.user]


@dataclass
class PaidContext:
    """The full graph seam: the free half's fakes plus a scriptable chat wire."""

    chat: ChatModel
    finder: FakeFinder
    embedder: FakeEmbedder
    backup: ChatModel | None = None  # A4: the configured --fallback-model target
    breaker_limit: int = 5  # kept == failure_policy.transport_limit (the breaker invariant)
    concurrency_value: int = 1  # sequential: deterministic call order under test
    halt_min_sample: int = 20  # lower it to trip the >50 % halt on a few chunks
    finder_labels: tuple[str, ...] = ()
    chat_resolutions: int = field(default=0)

    def entity_finder(self, labels: Sequence[str]) -> FakeFinder:
        self.finder_labels = tuple(labels)
        return self.finder

    async def fold_embedder(self, flag: str | None = None) -> FakeEmbedder:
        del flag
        return self.embedder

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        # Retained for any construction that still peeks at the plain wire; the
        # migrated paid modes run on resilient_chat_model instead (A4).
        self.chat_resolutions += 1
        return self.chat

    def fallback_ref(self, flag: str | None = None) -> ModelRef | None:
        return self.backup.ref if self.backup is not None else None

    async def fallback_chat_model(self, ref: object) -> ChatModel:
        assert self.backup is not None
        return self.backup

    def batching(self) -> None:
        return None

    async def resilient_chat_model(
        self, flag: str | None = None, fallback_flag: str | None = None
    ) -> WiredChat:
        # Compose THIS fake's fallback wiring into the WiredChat seam the migrated
        # paid modes run on — mirrors test_map.FakeContext.resilient_chat_model.
        from tests.helpers.wiring import build_wired

        self.chat_resolutions += 1
        ref = self.fallback_ref(fallback_flag)
        if ref is None:
            return build_wired(
                self.chat,
                concurrency=self.concurrency_value,
                breaker_limit=self.breaker_limit,
                batching=None,
            )

        async def fallback() -> ChatModel:
            return await self.fallback_chat_model(ref)

        return build_wired(
            self.chat,
            concurrency=self.concurrency_value,
            breaker_limit=self.breaker_limit,
            fallback_factory=fallback,
            fallback_ref=ref,
            batching=None,
        )

    def document_parser(self, flag: str | None = None) -> None:
        return None

    def concurrency(self, flag: int | None = None) -> int:
        return self.concurrency_value

    def failure_policy(self, provider: str) -> FailurePolicy:
        from smartpipe.cli import screens

        return FailurePolicy(
            transport_limit=self.breaker_limit,
            transport_screen=screens.provider_down(provider, self.breaker_limit),
            min_sample=self.halt_min_sample,
        )


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
    (call,) = chat.extraction_calls
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
    (call,) = chat.extraction_calls
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


# --- A1: a fail-fast halt salvages what was already extracted ------------------------


async def test_full_mode_halt_salvages_the_extracted_edges() -> None:
    """The >50 % failure halt must still write the edges from the chunks that DID
    succeed before it tripped — run B discarded 7 good extractions and 943 paid
    OCR pages to this exact gap. The run still exits ALL_FAILED, not empty-handed."""
    chat = FakeChat(
        by_content={"Ann pays Bob": triples(("Ann", "pays", "Bob")), "junk": "not json"}
    )
    context = _context(chat)
    context.halt_min_sample = 2  # trip the ratio on three chunks, not twenty
    out = io.StringIO()
    with pytest.raises(TooManyFailures) as excinfo:
        await run_graph(
            GraphRequest(focus="pays"),
            context,
            stdin=io.StringIO("Ann pays Bob\njunk one\njunk two\n"),
            stdout=out,
            stop=None,
            clock=lambda: 0.0,
            ask=None,
            budget=None,
        )
    edges = _edges(out.getvalue())
    assert any((e["source"], e["target"]) == ("Ann", "Bob") for e in edges)  # salvaged
    # file-unit accounting for the manifest: one good source, two failed
    assert excinfo.value.source_counts == SourceCounts(succeeded=1, skipped=2, failed=2)


async def test_full_mode_halt_reraises_file_unit_not_chunk_unit_counts() -> None:
    """The halt's manifest denominator counts SOURCE FILES, not extraction chunks:
    one over-split file that fails is one skipped source, never its chunk count."""
    chat = FakeChat(
        by_content={"Ann pays Bob": triples(("Ann", "pays", "Bob")), "static": "not json"}
    )
    context = _context(chat)
    context.halt_min_sample = 2
    big_junk = "static " * 1500  # ~2,600 est. tokens → two chunks, both invalid
    out = io.StringIO()
    with pytest.raises(TooManyFailures) as excinfo:
        await run_graph(
            GraphRequest(focus="pays"),
            context,
            stdin=io.StringIO(f"Ann pays Bob\n{big_junk}\n"),
            stdout=out,
            stop=None,
            clock=lambda: 0.0,
            ask=None,
            budget=None,
        )
    # one good file + one failed (two-chunk) file: file-unit (1,1,1), NOT chunk (1,2,2)
    assert excinfo.value.source_counts == SourceCounts(succeeded=1, skipped=1, failed=1)
    assert any((e["source"], e["target"]) == ("Ann", "Bob") for e in _edges(out.getvalue()))


async def test_full_mode_halt_settles_the_manifest_at_all_failed(tmp_path: object) -> None:
    """A1's central claim, exercised end to end THROUGH ``settled`` — the seam it was
    built for. Prior halt tests only read ``excinfo.value.source_counts``; this drives
    the halt through the CLI boundary and asserts the manifest it writes records
    FILE-unit counts at exit ``all_failed``, with the salvaged edge already on stdout."""
    from pathlib import Path

    from smartpipe.cli.manifest_option import settled
    from smartpipe.io import source_accounting

    assert isinstance(tmp_path, Path)
    target = tmp_path / "halt.json"
    source_accounting.reset()  # the composition root arms this once per run
    manifest.reset()
    manifest.begin(target, verb="graph", argv=("graph",))
    chat = FakeChat(
        by_content={"Ann pays Bob": triples(("Ann", "pays", "Bob")), "junk": "not json"}
    )
    context = _context(chat)
    context.halt_min_sample = 2  # trip the ratio on three chunks
    out = io.StringIO()
    work = run_graph(
        GraphRequest(focus="pays"),
        context,
        stdin=io.StringIO("Ann pays Bob\njunk one\njunk two\n"),
        stdout=out,
        stop=None,
        clock=lambda: 0.0,
        ask=None,
        budget=None,
    )
    with pytest.raises(TooManyFailures) as excinfo:
        await settled(work, None)  # the boundary writes the manifest, then re-raises

    assert excinfo.value.source_counts == SourceCounts(succeeded=1, skipped=2, failed=2)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["run"]["exit_status"] == "all_failed"
    assert document["run"]["exit_code"] == int(ExitCode.ALL_FAILED)
    # file-unit books, NOT the runner's chunk-unit halt display (2 of 3 items)
    assert document["items"] == {"in": 3, "succeeded": 1, "skipped": 2, "failed": 2}
    assert any((e["source"], e["target"]) == ("Ann", "Bob") for e in _edges(out.getvalue()))


async def test_hybrid_naming_halt_keeps_the_co_occurrence_graph_and_exits_partial(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A naming fail-fast leaves the free co-occurrence graph fully intact, so the
    run salvages it as co-occurs and exits PARTIAL — never ALL_FAILED: the sources
    all succeeded, only the naming enhancement stopped early."""
    chat = FakeChat(default='{"relation": ""}')  # every naming call names nothing → fails
    context = _context(chat)
    context.halt_min_sample = 2
    out = io.StringIO()
    code = await run_graph(
        GraphRequest(focus="who", name_top=2),
        context,
        stdin=io.StringIO("Ann met Bob\nAnn met Bob\nAnn met Acme\n"),
        stdout=out,
        stop=None,
        clock=lambda: 0.0,
        ask=None,
        budget=None,
    )
    assert code is ExitCode.PARTIAL
    assert [(e["source"], e["target"], e["relation"]) for e in _edges(out.getvalue())] == [
        ("Ann", "Bob", "co-occurs"),
        ("Acme", "Ann", "co-occurs"),
    ]
    assert "naming stopped early" in capsys.readouterr().err


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
    # belt 2, canary charges 1 → 1 left; the note reports the REMAINING, not the
    # raw belt (GLM SHOULD-FIX 1), so the one-short shortfall is visible.
    assert (
        "note: ~3 extraction calls across 3 files; 1 left in the belt — the graph will be partial"
    ) in err
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


async def test_tty_confirm_decline_spends_only_the_canary_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # GLM review NIT 3: the canary fires BEFORE the partial-graph prompt, so a
    # decline spends the one probe (not literally nothing) — the name says so.
    stop = asyncio.Event()
    budget = CallBudget(limit=2, stop=stop)  # +1 belt unit for the A2 schema canary
    chat = FakeChat()
    context = _context(budgeted_chat(chat, budget))
    asked: list[str] = []

    def decline(question: str) -> bool:
        asked.append(question)
        return False

    code, out = await _run(
        GraphRequest(focus="pays"),
        context,
        "Ann one\nBob two\nCat three\n",  # 3 chunks > belt 2 → belt_short → the prompt
        stop=stop,
        ask=decline,
        budget=budget,
    )
    assert code is ExitCode.OK
    assert out == ""  # nothing extracted, nothing written
    assert asked == ["proceed with a partial graph? [y/N]"]
    assert asked == [CONFIRM_PARTIAL]
    assert chat.extraction_calls == []  # declined at the plan: only the canary probe fired
    assert "the graph will be partial" in capsys.readouterr().err


async def test_piped_run_never_prompts_and_the_belt_governs() -> None:
    stop = asyncio.Event()
    budget = CallBudget(limit=2, stop=stop)  # +1 belt unit for the A2 schema canary
    chat = FakeChat([triples(("Ann", "pays", "Bob"))])
    context = _context(budgeted_chat(chat, budget))
    # ask=None + a StringIO stdin (isatty False) = the piped path: no prompt
    code, out = await _run(
        GraphRequest(focus="pays"), context, "Ann one\nBob two\n", stop=stop, budget=budget
    )
    assert code is ExitCode.PARTIAL
    assert len(chat.extraction_calls) == 1  # the belt, not a prompt, capped the run
    assert len(_edges(out)) == 1


async def test_belt_census_wording_and_partial_graph(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop = asyncio.Event()
    budget = CallBudget(limit=2, stop=stop)  # +1 belt unit for the A2 schema canary
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


async def test_full_manifest_counts_sources_not_extraction_chunks(tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    target = tmp_path / "graph.json"
    manifest.reset()
    manifest.begin(target, verb="graph", argv=("graph",))
    long_text = " ".join(f"word{n}" for n in range(3_000))
    code, _out = await _run(
        GraphRequest(focus="pays"),
        _context(FakeChat(default=triples(("Ann", "pays", "Bob")))),
        f"{long_text}\n",
    )
    manifest.finish(code)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 1, "succeeded": 1, "skipped": 0, "failed": 0}


async def test_belt_remainder_is_skipped_but_not_failed_in_manifest(tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    target = tmp_path / "graph-belt.json"
    manifest.reset()
    manifest.begin(target, verb="graph", argv=("graph",))
    stop = asyncio.Event()
    budget = CallBudget(limit=2, stop=stop)  # +1 belt unit for the A2 schema canary
    chat = FakeChat([triples(("Ann", "pays", "Bob"))])
    context = _context(budgeted_chat(chat, budget))
    context.concurrency_value = 3  # admit the remainder so it becomes typed Unsent
    code, _out = await _run(
        GraphRequest(focus="pays"),
        context,
        "one Ann Bob\ntwo Ann Bob\nthree Ann Bob\n",
        stop=stop,
        budget=budget,
    )
    manifest.finish(code)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 3, "succeeded": 1, "skipped": 2, "failed": 0}


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
    (call,) = chat.extraction_calls
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
    budget = CallBudget(limit=2, stop=stop)  # +1 belt unit for the A2 schema canary
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


async def test_adopt_drained_stop_exits_partial_not_ok(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """B1 review: a drained Ctrl-C during adopt's fold must exit PARTIAL, never OK —
    like full/hybrid/fast mode already do. run_adopt used to return OK unconditionally,
    so an interrupted adopt run looked clean."""
    rows = (
        '{"subject": "Ann", "relation": "pays", "object": "Bob"}\n'
        '{"subject": "Cid", "relation": "pays", "object": "Dan"}\n'
    )
    stop = asyncio.Event()

    def should_stop() -> bool:
        stop.set()  # the fold's first cooperative check trips the run-level drain
        return True

    out = io.StringIO()
    code = await run_graph(
        GraphRequest(),
        _context(FakeChat()),
        stdin=io.StringIO(rows),
        stdout=out,
        stop=stop,
        should_stop=should_stop,
        clock=lambda: 0.0,
    )
    assert code is ExitCode.PARTIAL  # NOT OK — the drained fold is a partial (was the bug)
    assert "interrupted" in capsys.readouterr().err


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
    assert len(chat.extraction_calls) > 1  # one call per token chunk
    (edge,) = _edges(out)
    assert edge["weight"] == len(chat.extraction_calls)  # every chunk asserted the triple
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
    (call,) = chat.extraction_calls
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
    (call,) = chat.extraction_calls
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
    assert chat.extraction_calls == []  # empty stdin: at most the canary probe, no extraction


async def test_full_mode_interrupt_drains_and_reports(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop = asyncio.Event()

    class StoppingChat(FakeChat):
        async def complete(self, request: CompletionRequest) -> str:
            if _CANARY_MARK not in request.user:
                stop.set()  # a Ctrl-C mid-run (not the canary): intake halts, work drains
            return await super().complete(request)

    chat = StoppingChat(default=triples(("Ann", "pays", "Bob")))
    code, out = await _run(
        GraphRequest(focus="pays"), _context(chat), "one Ann\ntwo Ann\nthree Ann\n", stop=stop
    )
    assert code is ExitCode.PARTIAL  # two consumed source rows were never attempted
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
            if _CANARY_MARK not in request.user:
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
    (call,) = chat.extraction_calls
    assert "source: Ann" in call.user
    assert "they appear together in:" not in call.user
    (edge,) = _edges(out)
    assert edge["relation"] == "pays"


async def test_hybrid_naming_windows_cap_at_three() -> None:
    chat = FakeChat(default='{"relation": "pays"}')
    corpus = "".join(f"Ann met Bob v{n}\n" for n in range(5))
    await _run(GraphRequest(name_top=1), _context(chat), corpus)
    (call,) = chat.extraction_calls
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
    assert chat.extraction_calls == []  # whitespace chunks to nothing: no extraction call


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
    assert len(chat.extraction_calls) == 2  # the text chunk and the figure chunk
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
    assert len(chat.extraction_calls) == 2  # two ten-minute slices, one call each
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


# --- A2: the schema canary refuses total incapacity BEFORE any ingestion spend -----


async def test_full_mode_canary_refuses_before_spend_when_the_model_fails_the_schema() -> None:
    # the canary snippet comes back wrong-shaped twice (initial + the one repair
    # rung map_one grants) → the model cannot hold the extraction schema, so the
    # run refuses at SETUP before a single real chunk (or paid OCR page) is sent.
    chat = FakeChat(by_content={"Alice pays Bob": "not json at all"})
    context = _context(chat)
    with pytest.raises(SetupFault) as excinfo:
        await _run(GraphRequest(focus="who pays whom"), context, "Ann pays Bob\n")
    message = str(excinfo.value)
    assert "ollama/fake" in message
    assert "cannot hold" in message
    # the real corpus never reached the wire — only the fixed canary snippet did
    assert all("Ann pays Bob" not in call.user for call in chat.calls)
    assert any("Alice pays Bob" in call.user for call in chat.calls)


async def test_full_mode_canary_passes_then_extracts_normally() -> None:
    chat = FakeChat(
        by_content={
            "Alice pays Bob": triples(("Alice", "pays", "Bob")),
            "Ann pays Bob": triples(("Ann", "pays", "Bob")),
        }
    )
    code, out = await _run(GraphRequest(focus="who pays whom"), _context(chat), "Ann pays Bob\n")
    assert code == ExitCode.OK
    assert any((e["source"], e["target"]) == ("Ann", "Bob") for e in _edges(out))
    assert any("Alice pays Bob" in call.user for call in chat.calls)  # the probe fired


async def test_full_mode_canary_does_not_relabel_an_availability_fault_as_incapacity() -> None:
    # a transient wire fault at canary time is NOT a schema verdict: it must
    # propagate as itself, never masquerade as "cannot hold the schema".
    class DownChat(FakeChat):
        async def complete(self, request: CompletionRequest) -> str:
            self.calls.append(request)
            raise RetryableError("rate limited")

    with pytest.raises(RetryableError):
        await _run(GraphRequest(focus="who pays whom"), _context(DownChat()), "Ann pays Bob\n")


async def test_hybrid_mode_canary_refuses_before_the_naming_loop_spends() -> None:
    # hybrid names its strongest edges through a single-field schema; if the
    # model cannot hold even that, refuse before the naming loop spends. The
    # canary is guarded by there being edges to name, so an empty corpus is free.
    chat = FakeChat(by_content={"Alice pays Bob": "not json"})
    context = _context(chat)
    with pytest.raises(SetupFault):
        await _run(GraphRequest(name_top=5), context, "Ann pays Bob and Bob pays Ann\n")


async def test_hybrid_canary_probes_the_naming_surface_not_a_bare_sentence() -> None:
    # GLM review NIT 5: the hybrid probe must exercise the SAME payload the
    # naming loop drives (source:/target: plus a co-occurrence window), not a
    # bare sentence — a model that emits relation JSON for free text is not
    # proof it can name from a pair window.
    chat = FakeChat(default='{"relation": "pays"}')
    await _run(GraphRequest(name_top=1), _context(chat), "Ann pays Bob and Bob pays Ann\n")
    probe = next(call for call in chat.calls if _CANARY_MARK in call.user)
    assert "source:" in probe.user
    assert "target:" in probe.user
    assert "they appear together in:" in probe.user


async def test_a_belt_sized_to_the_chunk_count_is_one_short_from_the_probe(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # GLM review SHOULD-FIX 1: the canary spends one belt unit, so a belt equal
    # to the chunk count leaves room for one fewer extraction. The plan must SAY
    # "partial" up front — counting the probe against the belt — not silently
    # drop the last chunk after promising a full graph. And the number it reports
    # is the REMAINING (2 left of a belt-3), not the raw belt — so a reader who
    # bumps the belt raises it PAST the chunk count, not merely up to it.
    stop = asyncio.Event()
    budget = CallBudget(limit=3, stop=stop)  # 3 chunks, but the probe eats one unit
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
    assert "2 left in the belt — the graph will be partial" in err
    assert code is ExitCode.PARTIAL


async def test_belt_of_one_does_its_one_real_call_and_skips_the_probe() -> None:
    # GLM review SHOULD-FIX 2: a belt that cannot afford the probe PLUS a real
    # call must not burn its only unit on the canary. The probe protects work,
    # and a belt of one has at most one call to protect — so skip it and spend
    # the unit on the real extraction (pre-A2 behaviour).
    stop = asyncio.Event()
    budget = CallBudget(limit=1, stop=stop)
    chat = FakeChat([triples(("Ann", "pays", "Bob"))])
    context = _context(budgeted_chat(chat, budget))
    code, out = await _run(
        GraphRequest(focus="pays"), context, "Ann pays Bob\n", stop=stop, budget=budget
    )
    assert code is ExitCode.OK
    assert all(_CANARY_MARK not in call.user for call in chat.calls)  # the probe never fired
    assert len(chat.extraction_calls) == 1  # the one belt unit went to real work
    assert len(_edges(out)) == 1


async def test_empty_input_at_belt_of_one_spends_nothing() -> None:
    # GLM review SHOULD-FIX 2, the cited case: `echo -n "" | graph --max-calls 1`
    # must not spend its only unit probing an empty stream. With the belt unable
    # to afford probe + work, the canary is skipped and nothing is spent.
    stop = asyncio.Event()
    budget = CallBudget(limit=1, stop=stop)
    chat = FakeChat()
    context = _context(budgeted_chat(chat, budget))
    code, out = await _run(GraphRequest(focus="pays"), context, "", stop=stop, budget=budget)
    assert code is ExitCode.OK
    assert out == ""
    assert chat.calls == []  # not even the probe fired


# --- A4: --fallback-model failover into both paid modes (item 11) --------------------


class CanaryThenDown(FakeChat):
    """Passes the A2 schema canary (a fixed synthetic snippet), then dies on the
    wire for every REAL chunk — a primary that proved the schema but went down."""

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        if _CANARY_MARK in request.user:
            return triples(("Alice", "pays", "Bob"))  # the canary passes
        raise TransportError("ollama error 503: overloaded")  # every real chunk fails


async def test_full_mode_failover_switches_to_the_backup(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A4: the primary clears the canary, then every real extraction call dies on
    # the wire. The breaker trips and swaps WHOLESALE to the configured fallback,
    # replaying the held window onto it — the same seam map/extend/filter/join use.
    backup = FakeChat([triples(("Ann", "pays", "Bob"))])
    backup.ref = ModelRef("openai", "gpt-4o-mini")
    primary = CanaryThenDown()
    context = PaidContext(
        chat=primary,
        finder=FakeFinder(PEOPLE),
        embedder=FakeEmbedder({}),
        backup=backup,
        breaker_limit=2,
        concurrency_value=4,  # a held window before the trip, like map's switch test
    )
    code, out = await _run(
        GraphRequest(focus="who pays whom"),
        context,
        "one Ann Bob\ntwo Ann Bob\nthree Ann Bob\nfour Ann Bob\nfive Ann Bob\nsix Ann Bob\n",
    )
    assert code is ExitCode.OK  # the held window re-ran on the backup — nothing lost
    edges = _edges(out)
    assert any((e["source"], e["target"]) == ("Ann", "Bob") for e in edges)  # backup answered
    err = capsys.readouterr().err
    assert "switching to openai/gpt-4o-mini for the rest of the run" in err
    assert "answers: openai/gpt-4o-mini" in err  # the receipt keeps the swap visible
    # the "nothing lost" claim is load-bearing: the primary answered only its
    # breaker window, the backup answered every held + remaining chunk, and no
    # chunk fell through to a skip (mirrors test_map's failover-switch assertions).
    assert len(primary.extraction_calls) == 2  # the breaker window, then never again
    assert len(backup.calls) == 6  # the held window replayed onto the backup + the rest
    assert "skipped" not in err


async def test_full_mode_failover_on_a_dead_backup_dies_loudly() -> None:
    # A4: the primary clears the canary then dies, and the configured fallback is
    # ALSO down. One window on the primary, one on the backup, then honest death on
    # the provider-down screen — never a silent empty graph.
    class AlwaysDown(FakeChat):
        async def complete(self, request: CompletionRequest) -> str:
            self.calls.append(request)
            raise TransportError("openai error 503: overloaded")

    backup = AlwaysDown()
    backup.ref = ModelRef("openai", "gpt-4o-mini")
    primary = CanaryThenDown()
    context = PaidContext(
        chat=primary,
        finder=FakeFinder(PEOPLE),
        embedder=FakeEmbedder({}),
        backup=backup,
        breaker_limit=2,
        concurrency_value=1,  # sequential: a deterministic two-window death
    )
    with pytest.raises(SetupFault, match="looks down"):
        await _run(
            GraphRequest(focus="who pays whom"),
            context,
            "one Ann\ntwo Ann\nthree Ann\nfour Ann\nfive Ann\nsix Ann\n",
        )
    # both wires were actually tried before the honest death: the primary ran its
    # breaker window, the swap landed on the backup, and the backup's own failures
    # tripped it too — not a silent give-up (mirrors test_map's dead-backup counts).
    assert len(primary.extraction_calls) == 2  # the breaker window on the primary
    assert len(backup.calls) == 2  # the held window replayed onto the dead backup


async def test_full_mode_ocr_breaker_during_read_stops_at_setup_not_a_bug(
    tmp_path: object,
) -> None:
    """A5.1 (completion): when the configured OCR wire's breaker concludes it is
    DOWN during the read phase of a paid graph run, the fault stops the run as a
    SetupFault (which ``die`` maps to SETUP, exit 2) — NOT the internal-BUG screen
    (exit 70) that a raw item error escaping run_full's read loop would produce.
    This is the graph-verb end of the exit-70 defect the reader conversion closes."""
    from pathlib import Path

    from smartpipe.core.errors import CircuitOpenTransport
    from smartpipe.io.inputs import InputSpec
    from tests.io.test_ocr_ingest import RaisingParser

    assert isinstance(tmp_path, Path)
    (tmp_path / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    class _OcrDown(PaidContext):
        def document_parser(self, flag: str | None = None) -> RaisingParser:  # type: ignore[override]
            return RaisingParser(CircuitOpenTransport("ocr wire down", trip_id=1))

    context = _OcrDown(
        chat=FakeChat([triples(("Ann", "pays", "Bob"))]),  # clears the schema canary
        finder=FakeFinder(PEOPLE),
        embedder=FakeEmbedder({}),
    )
    with pytest.raises(SetupFault, match="circuit opened"):
        await _run(
            GraphRequest(
                focus="who pays whom",
                ocr_model_flag="mistral/mistral-ocr-latest",
                input=InputSpec(patterns=(str(tmp_path / "scan.png"),), from_files=False),
            ),
            context,
        )
