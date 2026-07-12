"""Graph batching (#21): full/hybrid text chunks pack into fewer wire calls.

Transplants the map-verb contract (tests/verbs/test_batching.py) onto graph:
identical stdout batching-on or -off, media chunks and the schema canary stay
solo, one packed flight charges one belt unit, and the plan note softens its
claims only while coalescing. The fakes answer packed AND solo requests, so
the same corpus runs both postures.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode
from smartpipe.engine.coalesce import BatchSettings, max_group
from smartpipe.engine.prompts import parse_prompt, plan_map
from smartpipe.models.base import CompletionRequest
from smartpipe.models.budget import CallBudget, budgeted_chat
from smartpipe.verbs.graph import GraphRequest, run_graph
from smartpipe.verbs.graphfull import extraction_prompt
from tests.verbs.test_batching import PackedCapable
from tests.verbs.test_graph import FakeEmbedder, FakeFinder

# local mirrors (pyright strict refuses cross-module private imports): the
# packed-block shape from tests/verbs/test_batching.py and the canary snippet
# from graphfull._CANARY_SNIPPET (same mirror as test_graph_paid.CANARY_MARK)
BLOCK = re.compile(r'<input id="(r\d+)">\n(.*?)\n</input>', re.DOTALL)
CANARY_MARK = "Alice pays Bob for the shipment."

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from smartpipe.engine.runner import FailurePolicy
    from smartpipe.models.base import ChatModel, EmbeddingModel
    from smartpipe.models.resilience import WiredChat


def _triples(_body: str, _request: CompletionRequest) -> object:
    return {"triples": [{"subject": "Ann", "relation": "pays", "object": "Bob"}]}


def _names(_body: str, _request: CompletionRequest) -> object:
    return {"relation": "pays"}


@dataclass
class BatchGraphContext:
    """The paid graph seam with a REAL coalescer: build_wired composes the
    breaker + gate + outer coalescer exactly as the container does."""

    chat: ChatModel
    finder: FakeFinder
    embedder: EmbeddingModel
    settings: BatchSettings | None
    breaker_limit: int = 5
    concurrency_value: int = 4
    finder_labels: tuple[str, ...] = field(default_factory=tuple)

    def entity_finder(self, labels: Sequence[str]) -> FakeFinder:
        self.finder_labels = tuple(labels)
        return self.finder

    async def fold_embedder(self, flag: str | None = None) -> EmbeddingModel:
        del flag
        return self.embedder

    def batching(self) -> BatchSettings | None:
        return self.settings

    def remote_transcriber(
        self, chat_ref: object | None = None, *, flag: str | None = None
    ) -> None:
        del chat_ref, flag  # C4 protocol member: batching tests never resolve stt
        return None

    async def resilient_chat_model(
        self, flag: str | None = None, fallback_flag: str | None = None
    ) -> WiredChat:
        from tests.helpers.wiring import build_wired

        del flag, fallback_flag
        return build_wired(
            self.chat,
            concurrency=self.concurrency_value,
            breaker_limit=self.breaker_limit,
            batching=self.settings,
        )

    def document_parser(self, flag: str | None = None) -> None:
        return None

    def concurrency(self, flag: int | None = None) -> int:
        return self.concurrency_value

    def failure_policy(self, provider: str) -> FailurePolicy:
        from smartpipe.cli import screens
        from smartpipe.engine.runner import FailurePolicy

        return FailurePolicy(
            transport_limit=self.breaker_limit,
            transport_screen=screens.provider_down(provider, self.breaker_limit),
        )


def _context(
    chat: ChatModel,
    *,
    size: int | None = 4,
    known: dict[str, str] | None = None,
) -> BatchGraphContext:
    # Full groups only (item count divides by size): dispatch is always the
    # synchronous size-cap path, never the window timer (the Windows sub-tick
    # trap pinned in tests/verbs/test_batching.py).
    settings = None if size is None else BatchSettings(size=size, window_seconds=60.0)
    return BatchGraphContext(
        chat=chat,
        finder=FakeFinder(known or {}),
        embedder=FakeEmbedder({}),
        settings=settings,
    )


async def _run(
    request: GraphRequest,
    context: BatchGraphContext,
    stdin_text: str = "",
    *,
    budget: CallBudget | None = None,
    ask: Callable[[str], bool] | None = None,
    stop: asyncio.Event | None = None,
) -> tuple[ExitCode, str]:
    out = io.StringIO()
    code = await run_graph(
        request,
        context,
        stdin=io.StringIO(stdin_text),
        stdout=out,
        stop=stop,
        clock=lambda: 0.0,
        budget=budget,
        ask=ask,
    )
    return code, out.getvalue()


def _packed_calls(fake: PackedCapable) -> list[CompletionRequest]:
    return [call for call in fake.calls if BLOCK.findall(call.user)]


def _corpus_calls(fake: PackedCapable) -> list[CompletionRequest]:
    """Wire calls minus the A2 schema canary (solo, fired pre-ingestion)."""
    return [call for call in fake.calls if CANARY_MARK not in call.user]


FOUR_ROWS = "Ann pays Bob alpha\nAnn pays Bob beta\nAnn pays Bob gamma\nAnn pays Bob delta\n"


# --- FULL mode packs its text chunks -------------------------------------------------


async def test_full_four_text_chunks_fly_in_one_packed_call() -> None:
    fake = PackedCapable(_triples)
    code, batched_out = await _run(
        GraphRequest(focus="who pays whom"), _context(fake, size=4), FOUR_ROWS
    )
    assert code is ExitCode.OK
    packed = _packed_calls(fake)
    assert len(packed) == 1  # EXACTLY one packed wire call carries all four chunks
    (call,) = packed
    assert [label for label, _body in BLOCK.findall(call.user)] == ["r1", "r2", "r3", "r4"]
    assert len(_corpus_calls(fake)) == 1  # canary aside, ONE wire call total

    solo = PackedCapable(_triples)
    off_code, solo_out = await _run(
        GraphRequest(focus="who pays whom"), _context(solo, size=None), FOUR_ROWS
    )
    assert off_code is ExitCode.OK
    assert len(_packed_calls(solo)) == 0
    assert len(_corpus_calls(solo)) == 4  # one solo call per chunk, as today
    assert batched_out == solo_out  # identical stdout, batching on or off


async def test_canary_stays_solo_while_chunks_pack() -> None:
    fake = PackedCapable(_triples)
    await _run(GraphRequest(focus="who pays whom"), _context(fake, size=4), FOUR_ROWS)
    canaries = [call for call in fake.calls if CANARY_MARK in call.user]
    assert len(canaries) == 1  # the probe fired once…
    assert not BLOCK.findall(canaries[0].user)  # …and never rode a packed call


async def test_one_packed_flight_charges_one_belt_unit() -> None:
    fake = PackedCapable(_triples)
    budget = CallBudget(limit=10, stop=None)
    context = _context(budgeted_chat(fake, budget), size=4)
    code, out = await _run(GraphRequest(focus="who pays whom"), context, FOUR_ROWS, budget=budget)
    assert code is ExitCode.OK
    assert len(out.splitlines()) == 1  # the folded edge landed
    # the canary charged one unit, the packed flight of four chunks charged ONE
    assert budget.calls == 2


async def test_media_chunks_ride_solo_while_text_packs(tmp_path: Path) -> None:
    from smartpipe.io.inputs import InputSpec

    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\nAAAA")
    (tmp_path / "b.png").write_bytes(b"\x89PNG\r\n\x1a\nBBBB")
    fake = PackedCapable(_triples)
    context = _context(fake, size=4)
    code, _out = await _run(
        GraphRequest(
            focus="who pays whom",
            input=InputSpec(patterns=(str(tmp_path / "*.png"),), from_files=False),
        ),
        context,
    )
    assert code is ExitCode.OK
    media_calls = [call for call in _corpus_calls(fake) if call.media]
    assert len(media_calls) == 2  # one solo call per figure chunk
    assert all(not BLOCK.findall(call.user) for call in media_calls)  # never packed
    assert all(call.batch is None for call in media_calls)  # no coalesce hint attached


# --- the plan note says true things while coalescing ---------------------------------


async def test_plan_note_wears_the_batching_parenthetical_only_when_coalescing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _run(GraphRequest(focus="who pays whom"), _context(PackedCapable(_triples)), FOUR_ROWS)
    err = capsys.readouterr().err
    assert (
        "~4 extraction calls across 4 files "
        "(batching may pack text chunks into fewer wire calls)" in err
    )

    await _run(
        GraphRequest(focus="who pays whom"),
        _context(PackedCapable(_triples), size=None),
        FOUR_ROWS,
    )
    err = capsys.readouterr().err
    assert "~4 extraction calls across 4 files" in err
    assert "(batching may pack" not in err  # batching off keeps today's string byte-identical


async def test_belt_short_tail_softens_to_may_be_partial_while_coalescing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop = asyncio.Event()
    budget = CallBudget(limit=3, stop=stop)
    fake = PackedCapable(_triples)
    code, _out = await _run(
        GraphRequest(focus="who pays whom"),
        _context(budgeted_chat(fake, budget), size=4),
        FOUR_ROWS,
        budget=budget,
        ask=lambda _prompt: True,
        stop=stop,
    )
    err = capsys.readouterr().err
    assert (
        "(batching may pack text chunks into fewer wire calls); "
        "2 left in the belt — the graph may be partial" in err
    )
    # the packed flight covered all four chunks on a 2-call remainder: honest "may"
    assert code is ExitCode.OK

    solo_stop = asyncio.Event()
    solo_budget = CallBudget(limit=3, stop=solo_stop)
    solo = PackedCapable(_triples)
    await _run(
        GraphRequest(focus="who pays whom"),
        _context(budgeted_chat(solo, solo_budget), size=None),
        FOUR_ROWS,
        budget=solo_budget,
        ask=lambda _prompt: True,
        stop=solo_stop,
    )
    err = capsys.readouterr().err
    assert "2 left in the belt — the graph will be partial" in err  # today's string, untouched


# --- HYBRID packs its naming asks -----------------------------------------------------


async def test_hybrid_twelve_naming_asks_fly_in_one_packed_call(
    capsys: pytest.CaptureFixture[str],
) -> None:
    known: dict[str, str] = dict.fromkeys(("Ann", "Bob", "Cid", "Dee", "Eve", "Fay"), "person")
    fake = PackedCapable(_names)
    context = _context(fake, size=12, known=known)
    code, out = await _run(
        GraphRequest(name_top=12),
        context,
        "Ann Bob Cid Dee Eve Fay meet weekly\n",
    )
    assert code is ExitCode.OK
    packed = _packed_calls(fake)
    assert len(packed) == 1  # twelve naming asks in ONE packed call
    labels = [label for label, _body in BLOCK.findall(packed[0].user)]
    assert labels == [f"r{n}" for n in range(1, 13)]
    assert len(_corpus_calls(fake)) == 1
    named = [json.loads(line) for line in out.splitlines()][:12]
    assert all(edge["relation"] == "pays" for edge in named)
    assert "· 12 named ·" in capsys.readouterr().err


async def test_hybrid_stdout_is_byte_identical_batching_on_or_off() -> None:
    known: dict[str, str] = dict.fromkeys(("Ann", "Bob", "Cid", "Dee", "Eve", "Fay"), "person")
    line = "Ann Bob Cid Dee Eve Fay meet weekly\n"
    _code, batched_out = await _run(
        GraphRequest(name_top=12), _context(PackedCapable(_names), size=12, known=known), line
    )
    solo = PackedCapable(_names)
    _code, solo_out = await _run(
        GraphRequest(name_top=12), _context(solo, size=None, known=known), line
    )
    assert len(_packed_calls(solo)) == 0
    assert len(_corpus_calls(solo)) == 12
    assert batched_out == solo_out


# --- the group math, pinned -----------------------------------------------------------


def test_group_math_full_ten_entities_six_naming_twelve() -> None:
    """K sanity pin: the full schema's 4 recursive props admit 10 per pack,
    --entities widens it to 6 props → 6, hybrid naming's single prop → 12."""
    plain = plan_map(
        parse_prompt(extraction_prompt("who pays whom", None, None), allow_descriptions=True),
        schema=None,
    )
    assert max_group(plain.schema) == 10
    labeled = plan_map(
        parse_prompt(
            extraction_prompt("who pays whom", ("person", "company"), None),
            allow_descriptions=True,
        ),
        schema=None,
    )
    assert max_group(labeled.schema) == 6
    from smartpipe.engine.schema import shorthand_to_schema

    naming = shorthand_to_schema(["relation"], types={"relation": {"type": "string"}})
    assert max_group(naming) == 12
