from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, SetupFault, UsageFault
from smartpipe.models.base import ChatModel, ModelRef
from smartpipe.verbs.top_k import TopKRequest, run_top_k

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class FakeEmbed:
    """Maps a text to a 2-D vector via a lookup, so cosine order is controllable."""

    def __init__(self, table: Mapping[str, tuple[float, float]]) -> None:
        self.ref = ModelRef("ollama", "fake-embed")
        self.table = table

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self.table[text] for text in texts)


class FakeContext:
    def __init__(self, model: FakeEmbed) -> None:
        self.model = model

    async def embedding_model(self, flag: str | None = None) -> FakeEmbed:
        return self.model

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        from smartpipe.core.errors import SetupFault

        raise SetupFault("no chat configured")

    def concurrency(self, flag: int | None = None) -> int:
        return 2

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None


def _request(near: str, k: int | None = None, threshold: float | None = None) -> TopKRequest:
    return TopKRequest(near=near, k=k, threshold=threshold, model_flag=None, concurrency_flag=None)


async def _run(
    request: TopKRequest, stdin: str, table: Mapping[str, tuple[float, float]]
) -> tuple[ExitCode, str]:
    out = io.StringIO()
    code = await run_top_k(
        request, FakeContext(FakeEmbed(table)), stdin=io.StringIO(stdin), stdout=out
    )
    return code, out.getvalue()


async def test_requires_k_or_threshold() -> None:
    with pytest.raises(UsageFault, match="needs a number"):
        await _run(_request("q"), "a\n", {"q": (1.0, 0.0), "a": (1.0, 0.0)})


async def test_ranks_and_limits_to_k_with_scores() -> None:
    table = {
        "distributed systems": (1.0, 0.0),
        "kubernetes at scale": (0.9, 0.1),  # closest
        "baking sourdough": (0.0, 1.0),  # orthogonal
        "database sharding": (0.7, 0.3),  # middling
    }
    code, out = await _run(
        _request("distributed systems", k=2),
        "kubernetes at scale\nbaking sourdough\ndatabase sharding\n",
        table,
    )
    assert code == ExitCode.OK
    lines = out.splitlines()
    assert len(lines) == 2
    # plain text items → "text\tscore", ranked best first
    assert lines[0].startswith("kubernetes at scale\t")
    assert lines[1].startswith("database sharding\t")
    # score is in [0,1], best is higher
    s0 = float(lines[0].split("\t")[1])
    s1 = float(lines[1].split("\t")[1])
    assert s0 > s1


async def test_threshold_filters() -> None:
    table = {"q": (1.0, 0.0), "hot": (1.0, 0.0), "cold": (-1.0, 0.0)}
    code, out = await _run(_request("q", threshold=0.9), "hot\ncold\n", table)
    assert code == ExitCode.OK
    assert out.splitlines() == ["hot\t1.0"]  # cold scores 0.0, below threshold


async def test_json_items_gain_a_score_field() -> None:
    table = {"q": (1.0, 0.0), "alice": (1.0, 0.0)}
    stdin = '{"name": "alice", "role": "eng"}\n'
    # the fake embeds the item TEXT; for a JSON line, item.text == the raw JSON string
    table[stdin.strip()] = (1.0, 0.0)
    code, out = await _run(_request("q", k=1), stdin, table)
    assert code == ExitCode.OK
    record = json.loads(out.strip())
    assert record["name"] == "alice"
    assert record["_score"] == 1.0


async def test_precomputed_vector_skips_reembedding() -> None:
    # an embed-output record carries its own vector; top_k must not re-embed it
    stdin = '{"text": "doc a", "vector": [1.0, 0.0], "source": "a.md"}\n'
    table = {"q": (1.0, 0.0)}  # note: no entry for the item text → would KeyError if re-embedded
    code, out = await _run(_request("q", k=1), stdin, table)
    assert code == ExitCode.OK
    record = json.loads(out.strip())
    assert record["text"] == "doc a"
    assert record["_score"] == 1.0
    assert "vector" not in record  # the plumbing vector is dropped from output


async def test_dimension_mismatch_is_setup_fault() -> None:
    # query is 2-D, the corpus vector is 3-D → different embedding models
    stdin = '{"text": "doc", "vector": [1.0, 0.0, 0.0]}\n'
    with pytest.raises(SetupFault, match="different models"):
        await _run(_request("q", k=1), stdin, {"q": (1.0, 0.0)})


async def test_empty_input_is_ok() -> None:
    code, out = await _run(_request("q", k=5), "", {"q": (1.0, 0.0)})
    assert code == ExitCode.OK
    assert out == ""
