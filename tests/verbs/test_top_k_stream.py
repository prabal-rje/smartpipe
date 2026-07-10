"""top_k --stream: the rolling leaderboard — board math, snapshot protocol, screens."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.engine.ranking import board_insert
from smartpipe.io.inputs import InputSpec
from smartpipe.io.leaderboard import render_frame
from smartpipe.models.base import ChatModel, ModelRef
from smartpipe.verbs.top_k import TopKRequest, run_top_k

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


# --- pure board math -------------------------------------------------------------


def test_board_insert_ranks_and_caps() -> None:
    board, changed = board_insert((), 0.5, 1, k=2)
    assert board == ((0.5, 1),) and changed
    board, changed = board_insert(board, 0.9, 2, k=2)
    assert board == ((0.9, 2), (0.5, 1)) and changed  # better score ranks first
    board, changed = board_insert(board, 0.1, 3, k=2)
    assert board == ((0.9, 2), (0.5, 1)) and not changed  # below the floor: no change
    board, changed = board_insert(board, 0.7, 4, k=2)
    assert board == ((0.9, 2), (0.7, 4)) and changed  # evicts the floor


def test_board_ties_prefer_earlier_arrival() -> None:
    board, _ = board_insert((), 0.5, 1, k=2)
    board, changed = board_insert(board, 0.5, 2, k=1)
    assert board == ((0.5, 1),) and not changed  # the incumbent wins the tie


@given(
    scores=st.lists(st.floats(min_value=0, max_value=1, allow_nan=False), max_size=30),
    k=st.integers(min_value=1, max_value=5),
)
def test_board_invariants(scores: list[float], k: int) -> None:
    board: tuple[tuple[float, int], ...] = ()
    for arrival, score in enumerate(scores, start=1):
        board, _ = board_insert(board, score, arrival, k)
        assert len(board) <= k
        assert list(board) == sorted(board, key=lambda e: (-e[0], e[1]))  # always ranked
    # the final board is exactly the true top-k
    expected = sorted(((s, i) for i, s in enumerate(scores, start=1)), key=lambda e: (-e[0], e[1]))[
        :k
    ]
    assert list(board) == expected


# --- frame renderer ---------------------------------------------------------------


def test_render_frame_golden_and_truncation() -> None:
    rows = [(0.913, "short"), (0.5, "x" * 100)]
    lines = render_frame(rows, width=20)
    assert lines[0] == "0.91  short"
    assert lines[1] == "0.50  " + "x" * 13 + "…"
    assert all(len(line) <= 20 for line in lines)


# --- stream mode ------------------------------------------------------------------


class FakeEmbed:
    def __init__(self, table: Mapping[str, tuple[float, float]]) -> None:
        self.ref = ModelRef("ollama", "fake-embed")
        self.table = table

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self.table[t] for t in texts)


class FakeContext:
    def __init__(self, model: FakeEmbed) -> None:
        self.model = model

    async def embedding_model(self, flag: str | None = None) -> FakeEmbed:
        return self.model

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        from smartpipe.core.errors import SetupFault

        raise SetupFault("no chat configured")

    def concurrency(self, flag: int | None = None) -> int:
        return 1  # deterministic arrival order for the snapshot transcript

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None

    def document_parser(self, flag: str | None = None) -> None:
        return None

    async def media_embedding_model(self, flag: str | None = None) -> None:
        return None


def _request(k: int | None = 2, **kw: object) -> TopKRequest:
    defaults: dict[str, object] = {
        "near": "q",
        "k": k,
        "threshold": None,
        "model_flag": None,
        "concurrency_flag": None,
        "stream": True,
    }
    defaults.update(kw)
    return TopKRequest(**defaults)  # type: ignore[arg-type]


async def _run(
    request: TopKRequest, stdin: str, table: Mapping[str, tuple[float, float]]
) -> tuple[ExitCode, list[dict[str, object]]]:
    out = io.StringIO()
    code = await run_top_k(
        request, FakeContext(FakeEmbed(table)), stdin=io.StringIO(stdin), stdout=out
    )
    return code, [json.loads(line) for line in out.getvalue().splitlines()]


async def test_snapshot_transcript_is_pinned() -> None:
    # arrivals: mid(0.75) → snapshot 1; best(1.0) → snapshot 2; worst — below floor,
    # NO snapshot; better(0.9) evicts mid → snapshot 3. K=2, concurrency 1.
    table = {
        "q": (1.0, 0.0),
        "mid": (0.5, 0.5),
        "best": (1.0, 0.0),
        "worst": (-1.0, 0.0),
        "better": (0.8, 0.2),
    }
    code, records = await _run(_request(), "mid\nbest\nworst\nbetter\n", table)
    assert code == ExitCode.OK
    snapshots = [r["__snapshot"] for r in records if "__snapshot" in r]
    assert snapshots == [1, 2, 3]  # "worst" produced no snapshot — no change, no output
    # the final snapshot is best-first with ranks
    final = records[-2:]
    assert final[0]["text"] == "best" and final[0]["__rank"] == 1
    assert final[1]["text"] == "better" and final[1]["__rank"] == 2
    scores = [r["__score"] for r in final]
    assert all(isinstance(s, float) and 0 <= s <= 1 for s in scores)


async def test_threshold_gates_membership() -> None:
    table = {"q": (1.0, 0.0), "hot": (1.0, 0.0), "cold": (0.0, 1.0)}
    code, records = await _run(_request(threshold=0.9), "cold\nhot\n", table)
    assert code == ExitCode.OK
    texts = [r["text"] for r in records if "text" in r]
    assert texts == ["hot"]  # cold never entered the board


async def test_stream_without_k_is_usage_error() -> None:
    with pytest.raises(UsageFault, match="needs K"):
        await _run(_request(k=None), "a\n", {"q": (1.0, 0.0)})


async def test_stream_with_file_inputs_is_usage_error() -> None:
    request = _request(input=InputSpec(patterns=("*.txt",), from_files=False))
    with pytest.raises(UsageFault, match="can't combine with --in"):
        await _run(request, "a\n", {"q": (1.0, 0.0)})


async def test_dimension_mismatch_skips_and_continues() -> None:
    # a precomputed 3-D vector against a 2-D query: skip that record, keep going
    stdin = '{"text": "bad", "vector": [1.0, 0.0, 0.0]}\ngood\n'
    table = {"q": (1.0, 0.0), "good": (1.0, 0.0)}
    code, records = await _run(_request(), stdin, table)
    assert code == ExitCode.PARTIAL  # one skipped, one scored
    texts = [r["text"] for r in records if "text" in r]
    assert texts == ["good"]


async def test_tty_mode_paints_the_board_instead_of_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("smartpipe.verbs.top_k.tty.stdout_is_tty", lambda: True)
    monkeypatch.setattr("smartpipe.verbs.top_k.tty.terminal_width", lambda: 40)
    table = {"q": (1.0, 0.0), "hot": (1.0, 0.0)}
    out = io.StringIO()
    code = await run_top_k(
        _request(), FakeContext(FakeEmbed(table)), stdin=io.StringIO("hot\n"), stdout=out
    )
    assert code == ExitCode.OK
    painted = out.getvalue()
    assert "hot" in painted and "1.00" in painted  # the board block, not NDJSON
    assert "__snapshot" not in painted


async def test_interrupted_stream_reports_and_exits_130(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    stop = asyncio.Event()
    stop.set()  # interrupted before anything arrived
    out = io.StringIO()
    code = await run_top_k(
        _request(),
        FakeContext(FakeEmbed({"q": (1.0, 0.0)})),
        stdin=io.StringIO("never-read\n"),
        stdout=out,
        stop=stop,
    )
    assert code == ExitCode.INTERRUPTED
    assert "done: interrupted" in capsys.readouterr().err
