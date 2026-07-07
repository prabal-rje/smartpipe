"""The cluster verb: N embeddings + K labels, deterministic, honest folds."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode
from smartpipe.models.base import CompletionRequest, ModelRef
from smartpipe.verbs.cluster import ClusterRequest, run_cluster

if TYPE_CHECKING:
    from collections.abc import Sequence

VECTORS: dict[str, tuple[float, ...]] = {
    "payment dies on iphone": (1.0, 0.0),
    "cart dies at checkout": (0.99, 0.141),
    "checkout button broken": (0.995, 0.0999),
    "love the dark mode": (0.0, 1.0),
    "dark theme is great": (0.05, 0.999),
}


class FakeEmbedding:
    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake-embed")

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(VECTORS[text] for text in texts)


class NamesClusters:
    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake-chat")
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.calls.append(request)
        label = "checkout failures" if "checkout" in request.user else "dark mode praise"
        return json.dumps({"label": label})


class FakeContext:
    def __init__(self, chat: NamesClusters | None) -> None:
        self.chat = chat

    async def chat_model(self, flag: str | None = None) -> NamesClusters:
        if self.chat is None:
            raise RuntimeError("no chat configured")
        return self.chat

    async def embedding_model(self, flag: str | None = None) -> FakeEmbedding:
        return FakeEmbedding()

    def concurrency(self, flag: int | None = None) -> int:
        return 2

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None


STDIN = (
    "payment dies on iphone\ncart dies at checkout\ncheckout button broken\n"
    "love the dark mode\ndark theme is great\n"
)


async def _run(
    chat: NamesClusters | None, **kwargs: object
) -> tuple[ExitCode, list[dict[str, object]], str]:
    import contextlib

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = await run_cluster(
            ClusterRequest(**kwargs),  # type: ignore[arg-type]
            FakeContext(chat),
            stdin=io.StringIO(STDIN),
            stdout=out,
        )
    return code, [json.loads(line) for line in out.getvalue().splitlines()], err.getvalue()


async def test_rows_are_sized_shared_labeled_largest_first() -> None:
    chat = NamesClusters()
    code, rows, err = await _run(chat)
    assert code is ExitCode.OK
    assert [row["cluster"] for row in rows] == ["checkout failures", "dark mode praise"]
    assert [row["size"] for row in rows] == [3, 2]
    assert rows[0]["share"] == 0.6
    assert len(chat.calls) == 2  # one label call per cluster, never N
    assert "one label call per cluster" in err  # the preview line, before spend


async def test_examples_are_members_of_the_cluster() -> None:
    _code, rows, _err = await _run(NamesClusters())
    examples = rows[0]["examples"]
    assert isinstance(examples, list)
    quotes = [str(example) for example in examples]  # type: ignore[union-attr]
    assert 1 <= len(quotes) <= 3
    assert all("dark" not in quote for quote in quotes)


async def test_k_forces_a_single_cluster() -> None:
    _code, rows, _err = await _run(NamesClusters(), k=1)
    assert len(rows) == 1
    assert rows[0]["size"] == 5


async def test_top_folds_the_tail_honestly() -> None:
    _code, rows, _err = await _run(NamesClusters(), top=1)
    assert rows[-1]["cluster"] == "(other)"
    assert rows[-1]["size"] == 2


async def test_explode_members_labels_every_input_row() -> None:
    _code, rows, _err = await _run(NamesClusters(), explode="members")
    assert len(rows) == 5
    assert rows[0] == {"text": "payment dies on iphone", "cluster": "checkout failures"}
    assert rows[3]["cluster"] == "dark mode praise"


async def test_without_chat_clusters_are_numbered_and_noted() -> None:
    _code, rows, err = await _run(None)
    assert [row["cluster"] for row in rows] == ["cluster 1", "cluster 2"]
    assert "no chat model" in err
