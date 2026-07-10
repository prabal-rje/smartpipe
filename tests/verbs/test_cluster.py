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
    from pathlib import Path

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

    def document_parser(self, flag: str | None = None) -> None:
        return None

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
    # item 64: summary rows carry a summary spine sized like the cluster
    assert [row["__source"] for row in rows] == [
        {"as": "cluster", "count": 3},
        {"as": "cluster", "count": 2},
    ]
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
    assert rows[-1]["__source"] == {"as": "cluster", "count": 2}  # the fold row too


async def test_explode_members_labels_every_input_row() -> None:
    _code, rows, err = await _run(NamesClusters(), explode="members")
    assert len(rows) == 5
    assert rows[0] == {"text": "payment dies on iphone", "cluster": "checkout failures"}
    assert rows[3]["cluster"] == "dark mode praise"
    assert "overwrites" not in err  # no input row carried a cluster field — stay silent


class SubstringEmbedding:
    """Looks vectors up by substring, so raw-JSON item text still resolves."""

    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake-embed")

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        def lookup(text: str) -> tuple[float, ...]:
            for key, vector in VECTORS.items():
                if key in text:
                    return vector
            raise KeyError(text)

        return tuple(lookup(text) for text in texts)


class RecordContext(FakeContext):
    async def embedding_model(self, flag: str | None = None) -> SubstringEmbedding:  # type: ignore[override]
        return SubstringEmbedding()


async def test_explode_warns_once_when_input_rows_already_carry_cluster() -> None:
    """Item 76: the field name stays (it IS the requested data) — the silence dies."""
    import contextlib

    rows_in = [
        {"text": "payment dies on iphone", "cluster": "stale"},
        {"text": "cart dies at checkout", "cluster": "stale"},
        {"text": "love the dark mode"},
    ]
    stdin = "\n".join(json.dumps(row) for row in rows_in) + "\n"
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = await run_cluster(
            ClusterRequest(explode="members"),
            RecordContext(NamesClusters()),
            stdin=io.StringIO(stdin),
            stdout=out,
        )
    assert code is ExitCode.OK
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert all(row["cluster"] != "stale" for row in rows)  # overwritten with the label
    stderr = err.getvalue()
    assert stderr.count("cluster --explode overwrites an existing 'cluster' field on 2 rows") == 1


async def test_without_chat_clusters_are_numbered_and_noted() -> None:
    _code, rows, err = await _run(None)
    assert [row["cluster"] for row in rows] == ["cluster 1", "cluster 2"]
    assert "no chat model" in err


async def test_explode_rows_keep_their_carried_spine() -> None:
    """--explode rows already carry their item's spine (item 64: verify,
    don't duplicate - the record's own __source rides through untouched)."""

    class _AnyEmbedding:
        ref = ModelRef("ollama", "fake-embed")

        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            return tuple((1.0, 0.0) for _ in texts)

    class _Context(FakeContext):
        async def embedding_model(self, flag: str | None = None) -> _AnyEmbedding:  # type: ignore[override]
            return _AnyEmbedding()

    spine = {"path": "notes.txt", "as": "lines", "line": 7}
    line = json.dumps({"text": "payment dies on iphone", "__source": spine})
    out = io.StringIO()
    import contextlib

    with contextlib.redirect_stderr(io.StringIO()):
        code = await run_cluster(
            ClusterRequest(explode="members"),
            _Context(NamesClusters()),
            stdin=io.StringIO(line + "\ncart dies at checkout\n"),
            stdout=out,
        )
    assert code is ExitCode.OK
    rows = [json.loads(row) for row in out.getvalue().splitlines()]
    assert rows[0]["__source"] == spine  # carried, not duplicated
    assert "__source" not in rows[1]  # a plain line has no spine to carry


# --- the ocr-model role at ingestion (item 48) ---------------------------------------


async def test_ocr_role_parses_pattern_scans_at_ingestion(tmp_path: Path) -> None:
    import contextlib

    from smartpipe.io.inputs import InputSpec
    from tests.io.test_ocr_ingest import FakeParser

    parser = FakeParser(image_text="love the dark mode")

    class OcrContext(FakeContext):
        def document_parser(self, flag: str | None = None) -> FakeParser:  # type: ignore[override]
            return parser

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    (tmp_path / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = await run_cluster(
            ClusterRequest(
                input=InputSpec(patterns=(str(tmp_path / "scan.png"),), from_files=False)
            ),
            OcrContext(NamesClusters()),
            stdin=_Tty(),
            stdout=out,
        )
    assert code is ExitCode.OK
    assert len(parser.image_calls) == 1  # the scan parsed through the role
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert rows[0]["examples"] == ["love the dark mode"]
    assert "parsed by mistral/mistral-ocr-latest" in err.getvalue()  # disclosed per row
