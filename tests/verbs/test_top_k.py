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

    def document_parser(self, flag: str | None = None) -> None:
        return None

    async def media_embedding_model(self, flag: str | None = None) -> None:
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
    assert record["__score"] == 1.0


async def test_precomputed_vector_skips_reembedding() -> None:
    # an embed-output record carries its own vector; top_k must not re-embed it
    stdin = '{"text": "doc a", "vector": [1.0, 0.0], "source": "a.md"}\n'
    table = {"q": (1.0, 0.0)}  # note: no entry for the item text → would KeyError if re-embedded
    code, out = await _run(_request("q", k=1), stdin, table)
    assert code == ExitCode.OK
    record = json.loads(out.strip())
    assert record["text"] == "doc a"
    assert record["__score"] == 1.0
    assert "vector" not in record  # the plumbing vector is dropped from output


async def test_matching_embedder_stamp_passes() -> None:
    stdin = '{"text": "doc", "vector": [1.0, 0.0], "__embedder": "ollama/fake-embed"}\n'
    code, out = await _run(_request("q", k=1), stdin, {"q": (1.0, 0.0)})
    assert code == ExitCode.OK
    assert json.loads(out.strip())["text"] == "doc"


async def test_mismatched_embedder_stamp_is_setup_fault() -> None:
    # same dimensions, different model — the stamp is the only honest witness
    stdin = '{"text": "doc", "vector": [1.0, 0.0], "__embedder": "openai/text-embedding-3-small"}\n'
    with pytest.raises(SetupFault) as caught:
        await _run(_request("q", k=1), stdin, {"q": (1.0, 0.0)})
    assert "openai/text-embedding-3-small" in str(caught.value)
    assert "ollama/fake-embed" in str(caught.value)


async def test_unstamped_corpus_works_with_one_note(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stdin = '{"text": "a", "vector": [1.0, 0.0]}\n{"text": "b", "vector": [0.0, 1.0]}\n'
    code, _out = await _run(_request("q", k=1), stdin, {"q": (1.0, 0.0)})
    assert code == ExitCode.OK
    err = capsys.readouterr().err
    assert err.count("__embedder") == 1  # one calm note, not one per row


async def test_reader_fed_record_embeds_its_text_not_the_wrapper() -> None:
    """Deliverable 4 pin for top_k: a spined record ranks by its text content."""
    stdin = '{"text": "kubernetes", "__source": {"path": "n.txt", "as": "lines", "line": 1}}\n'
    table = {"q": (1.0, 0.0), "kubernetes": (1.0, 0.0)}
    code, out = await _run(_request("q", k=1), stdin, table)  # KeyError if the wrapper embeds
    assert code == ExitCode.OK
    assert json.loads(out.strip())["text"] == "kubernetes"


async def test_duplicate_source_indexes_keep_their_own_scores() -> None:
    """Item 47: two page-cut records from different PDFs share page numbers —
    ranking must key on a run-scoped ordinal, never ``source.index``."""
    row_a = json.dumps({"text": "alpha", "__source": {"path": "a.pdf", "as": "pages", "page": 1}})
    row_b = json.dumps({"text": "beta", "__source": {"path": "b.pdf", "as": "pages", "page": 1}})
    table = {"q": (1.0, 0.0), "alpha": (1.0, 0.0), "beta": (0.0, 1.0)}
    code, out = await _run(_request("q", k=2), f"{row_a}\n{row_b}\n", table)
    assert code == ExitCode.OK
    records = [json.loads(line) for line in out.splitlines()]
    assert [r["text"] for r in records] == ["alpha", "beta"]  # each score emits ITS item
    assert records[0]["__score"] > records[1]["__score"]


async def test_duplicate_source_indexes_with_precomputed_vectors() -> None:
    """The precomputed-vector path shares the ranking dict — same collision."""

    def row(text: str, vector: list[float], path: str) -> str:
        return json.dumps(
            {"text": text, "vector": vector, "__source": {"path": path, "as": "pages", "page": 1}}
        )

    row_a = row("close", [1.0, 0.0], "a.pdf")
    row_b = row("far", [0.0, 1.0], "b.pdf")
    code, out = await _run(_request("q", k=2), f"{row_a}\n{row_b}\n", {"q": (1.0, 0.0)})
    assert code == ExitCode.OK
    records = [json.loads(line) for line in out.splitlines()]
    assert [r["text"] for r in records] == ["close", "far"]
    assert records[0]["__score"] > records[1]["__score"]


async def test_dimension_mismatch_is_setup_fault() -> None:
    # query is 2-D, the corpus vector is 3-D → different embedding models
    stdin = '{"text": "doc", "vector": [1.0, 0.0, 0.0]}\n'
    with pytest.raises(SetupFault, match="different models"):
        await _run(_request("q", k=1), stdin, {"q": (1.0, 0.0)})


async def test_empty_input_is_ok() -> None:
    code, out = await _run(_request("q", k=5), "", {"q": (1.0, 0.0)})
    assert code == ExitCode.OK
    assert out == ""


def test_emit_jsonl_cut_file_rows_stay_records() -> None:
    """Same leak as filter's: a row cut from a file (--as jsonl data.jsonl)
    must emit the record with __score, not 'data.jsonl<TAB>score' per row."""
    import io as _io

    from smartpipe.io.items import Item, ItemSource
    from smartpipe.io.writers import RenderMode, WriterConfig, make_writer
    from smartpipe.verbs import top_k as top_k_module

    out = _io.StringIO()
    writer = make_writer(WriterConfig(mode=RenderMode.NDJSON, color=False, width=80), out)
    item = Item(
        raw='{"id": 1, "text": "login bug"}',
        text="login bug",
        data={"id": 1, "text": "login bug"},
        source=ItemSource(kind="file", name="data.jsonl", index=0, cut="jsonl"),
    )
    top_k_module._emit(writer, item, 0.8765)  # pyright: ignore[reportPrivateUsage] — emission under test
    writer.flush()
    assert out.getvalue() == '{"id":1,"text":"login bug","__score":0.8765}\n'


def test_emit_whole_file_still_returns_the_path() -> None:
    import io as _io

    from smartpipe.io.items import Item, ItemSource
    from smartpipe.io.writers import RenderMode, WriterConfig, make_writer
    from smartpipe.verbs import top_k as top_k_module

    out = _io.StringIO()
    writer = make_writer(WriterConfig(mode=RenderMode.TEXT, color=False, width=80), out)
    item = Item(
        raw="ten years of bug hunting",
        text="ten years of bug hunting",
        data=None,
        source=ItemSource(kind="file", name="resume.txt", index=0, cut="file"),
    )
    top_k_module._emit(writer, item, 0.8765)  # pyright: ignore[reportPrivateUsage] — emission under test
    writer.flush()
    assert out.getvalue() == "resume.txt\t0.8765\n"
