"""The graph verb's --fast half (wave G1): free by construction, pinned here."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, ItemError, UsageFault
from smartpipe.engine.graphkg import EntitySpan
from smartpipe.io.inputs import InputSpec
from smartpipe.verbs.graph import DEFAULT_ENTITIES, GraphRequest, parse_entities, run_graph

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from smartpipe.models.base import ModelRef


class FakeFinder:
    """Marks any configured name it sees in the text, with char offsets."""

    def __init__(self, known: dict[str, str], *, poison: str | None = None) -> None:
        self.known = known  # name -> label
        self.poison = poison
        self.calls: list[str] = []

    def find(self, text: str) -> tuple[EntitySpan, ...]:
        self.calls.append(text)
        if self.poison is not None and self.poison in text:
            raise ItemError("the local NER model returned an unexpected shape")
        found = [
            EntitySpan(
                name=name, label=label, start=text.index(name), end=text.index(name) + len(name)
            )
            for name, label in self.known.items()
            if name in text
        ]
        return tuple(sorted(found, key=lambda span: span.start))


class FakeEmbedder:
    """Unit vectors by table — names sharing a vector fold together; names
    outside the table get mutually orthogonal one-hots (they never fold)."""

    def __init__(self, table: dict[str, tuple[float, ...]], *, broken: bool = False) -> None:
        self.table = table
        self.broken = broken
        self.batches: list[list[str]] = []
        self._assigned: dict[str, int] = {}

    @property
    def ref(self) -> ModelRef:
        from smartpipe.models.base import parse_model_ref

        return parse_model_ref("local/nomic-embed-text-v1.5")

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if self.broken:
            raise ItemError("local embedding failed (onnx says no)")
        self.batches.append(list(texts))
        return tuple(self._vector(text) for text in texts)

    def _vector(self, text: str) -> tuple[float, ...]:
        if text in self.table:
            return self.table[text]
        slot = self._assigned.setdefault(text, len(self._assigned))
        one_hot = [0.0] * 64
        one_hot[slot] = 1.0
        return tuple(one_hot)


@dataclass
class FakeContext:
    finder: FakeFinder
    embedder: FakeEmbedder
    finder_labels: tuple[str, ...] = ()
    chat_calls: int = field(default=0)

    def entity_finder(self, labels: Sequence[str]) -> FakeFinder:
        self.finder_labels = tuple(labels)
        return self.finder

    def fold_embedder(self) -> FakeEmbedder:
        return self.embedder

    async def chat_model(self, flag: str | None = None) -> object:
        self.chat_calls += 1
        raise AssertionError("graph --fast constructed a chat model — the free pin is broken")

    async def embedding_model(self, flag: str | None = None) -> object:
        self.chat_calls += 1
        raise AssertionError("graph --fast resolved the configured embedder — must stay local")


def _context(
    known: dict[str, str] | None = None,
    *,
    vectors: dict[str, tuple[float, ...]] | None = None,
    poison: str | None = None,
    broken_embedder: bool = False,
) -> FakeContext:
    return FakeContext(
        finder=FakeFinder(known or {}, poison=poison),
        embedder=FakeEmbedder(vectors or {}, broken=broken_embedder),
    )


async def _run(
    request: GraphRequest, context: FakeContext, stdin_text: str = ""
) -> tuple[ExitCode, str]:
    out = io.StringIO()
    code = await run_graph(
        request, context, stdin=io.StringIO(stdin_text), stdout=out, clock=lambda: 0.0
    )
    return code, out.getvalue()


PEOPLE = {"Ann": "person", "Bob": "person", "Acme": "company"}


# --- flag validation ----------------------------------------------------------


async def test_graph_without_fast_refuses_loudly() -> None:
    with pytest.raises(UsageFault, match="--fast"):
        await _run(GraphRequest(fast=False), _context())


async def test_min_weight_and_top_validate() -> None:
    with pytest.raises(UsageFault, match="--min-weight"):
        await _run(GraphRequest(fast=True, min_weight=0), _context())
    with pytest.raises(UsageFault, match="--top"):
        await _run(GraphRequest(fast=True, top=0), _context())


async def test_save_extension_refuses_before_any_work() -> None:
    context = _context(PEOPLE)
    with pytest.raises(UsageFault, match="--save"):
        await _run(GraphRequest(fast=True, save="graph.xlsx"), context, "Ann met Bob\n")
    assert context.finder.calls == []  # the refusal landed before the grind


def test_parse_entities_defaults_and_dedupes() -> None:
    assert parse_entities(None) == DEFAULT_ENTITIES
    assert parse_entities("person, vessel , person") == ("person", "vessel")
    with pytest.raises(UsageFault, match="--entities"):
        parse_entities(" , ")


async def test_entities_dial_reaches_the_finder() -> None:
    context = _context(PEOPLE)
    await _run(GraphRequest(fast=True, entities="person, vessel"), context, "Ann met Bob\n")
    assert context.finder_labels == ("person", "vessel")


# --- the free co-occurrence path ------------------------------------------------


async def test_chunk_windows_emit_weighted_jsonl_edges() -> None:
    code, out = await _run(
        GraphRequest(fast=True), _context(PEOPLE), "Ann met Bob\nBob saw Ann\nAnn alone\n"
    )
    assert code is ExitCode.OK
    edges = [json.loads(line) for line in out.splitlines()]
    assert edges == [
        {
            "source": "Ann",
            "relation": "co-occurs",
            "target": "Bob",
            "weight": 2,
            "sources": [
                {"path": "-", "as": "lines", "line": 1},
                {"path": "-", "as": "lines", "line": 2},
            ],
        }
    ]


async def test_receipt_wording_is_pinned(capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = await _run(
        GraphRequest(fast=True), _context(PEOPLE), "Ann met Bob at Acme\nBob left Acme\n"
    )
    assert code is ExitCode.OK
    err = capsys.readouterr().err
    assert "note: graph: 3 entities (0 folded) · 3 edges (0 pruned) · 0 tok" in err


async def test_min_weight_prunes_and_the_receipt_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, out = await _run(
        GraphRequest(fast=True, min_weight=2),
        _context(PEOPLE),
        "Ann met Bob\nBob saw Ann\nAnn met Acme\nBob near Acme\n",
    )
    edges = [json.loads(line) for line in out.splitlines()]
    assert [(e["source"], e["target"]) for e in edges] == [("Ann", "Bob")]
    assert "3 entities (0 folded) · 1 edges (2 pruned) · 0 tok" in capsys.readouterr().err


async def test_sentence_window_separates_sentences() -> None:
    _, out = await _run(
        GraphRequest(fast=True, window="sentence"),
        _context(PEOPLE),
        "Ann met Bob. Acme closed.\n",
    )
    edges = [json.loads(line) for line in out.splitlines()]
    assert [(e["source"], e["target"]) for e in edges] == [("Ann", "Bob")]


async def test_document_window_spans_the_whole_file(tmp_path: Path) -> None:
    corpus = tmp_path / "notes.txt"
    corpus.write_text("Ann here\nBob there\n", encoding="utf-8")
    request = GraphRequest(
        fast=True,
        window="document",
        input=InputSpec(patterns=(str(corpus),), from_files=False, as_mode="lines"),
    )
    _, out = await _run(request, _context(PEOPLE))
    edges = [json.loads(line) for line in out.splitlines()]
    assert [(e["source"], e["target"], e["weight"]) for e in edges] == [("Ann", "Bob", 1)]
    assert edges[0]["sources"] == [{"path": str(corpus), "as": "file"}]


async def test_empty_input_is_ok_and_silent() -> None:
    code, out = await _run(GraphRequest(fast=True), _context(PEOPLE), "")
    assert code is ExitCode.OK
    assert out == ""


# --- folding ---------------------------------------------------------------------


async def test_entity_folding_notes_and_folds_onto_the_most_frequent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    known = {"Acme Corp": "company", "Acme Corporation": "company", "Bob": "person"}
    vectors = {
        "Acme Corp": (1.0, 0.0, 0.0),
        "Acme Corporation": (1.0, 0.0, 0.0),
        "Bob": (0.0, 1.0, 0.0),
    }
    _, out = await _run(
        GraphRequest(fast=True),
        _context(known, vectors=vectors),
        "Acme Corp hired Bob\nAcme Corp grew\nAcme Corporation filed\nBob at Acme Corporation\n",
    )
    err = capsys.readouterr().err
    assert "note: 2 entity names folded into 1 node" in err
    assert "3 entities (2 folded)" in err
    edges = [json.loads(line) for line in out.splitlines()]
    assert [(e["source"], e["target"], e["weight"]) for e in edges] == [("Acme Corp", "Bob", 2)]


async def test_broken_embedder_degrades_to_no_folding(
    capsys: pytest.CaptureFixture[str],
) -> None:
    known = {"Acme Corp": "company", "Acme Corporation": "company"}
    code, out = await _run(
        GraphRequest(fast=True),
        _context(known, broken_embedder=True),
        "Acme Corp and Acme Corporation\n",
    )
    assert code is ExitCode.OK
    err = capsys.readouterr().err
    assert "entity folding skipped" in err
    assert "2 entities (0 folded)" in err
    edges = [json.loads(line) for line in out.splitlines()]
    assert [(e["source"], e["target"]) for e in edges] == [("Acme Corp", "Acme Corporation")]


async def test_single_entity_never_wakes_the_embedder() -> None:
    context = _context({"Ann": "person"})
    await _run(GraphRequest(fast=True), context, "Ann alone\n")
    assert context.embedder.batches == []


# --- the free ladder and its census ------------------------------------------------


async def test_image_only_items_get_one_census_note(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import base64

    pixel = base64.b64encode(b"px").decode()
    record = json.dumps({"__media": {"kind": "image", "mime": "image/png", "data_b64": pixel}})
    code, out = await _run(
        GraphRequest(fast=True),
        _context(PEOPLE),
        f"{record}\n{record}\nAnn met Bob\n",
    )
    assert code is ExitCode.PARTIAL
    err = capsys.readouterr().err
    assert (
        "note: 2 files skipped — no free text (images/scans); the full mode or ocr-model reads them"
    ) in err
    assert len([json.loads(line) for line in out.splitlines()]) == 1


async def test_finder_failures_skip_that_item_loudly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = await _run(
        GraphRequest(fast=True),
        _context(PEOPLE, poison="radioactive"),
        "Ann met Bob\nradioactive line\n",
    )
    assert code is ExitCode.PARTIAL
    assert "skipped: line 2" in capsys.readouterr().err
    assert len(out.splitlines()) == 1


# --- the zero-model-call pin --------------------------------------------------------


async def test_fast_never_touches_chat_or_the_configured_embedder() -> None:
    context = _context(PEOPLE)
    code, _ = await _run(GraphRequest(fast=True), context, "Ann met Bob at Acme\nBob saw Ann\n")
    assert code is ExitCode.OK
    assert context.chat_calls == 0  # would have raised AssertionError if asked


# --- projected-time honesty ----------------------------------------------------------


async def test_slow_pace_projection_notes_once(capsys: pytest.CaptureFixture[str]) -> None:
    ticks = iter([0.0] + [100.0] * 200)
    out = io.StringIO()
    lines = "".join(f"Ann {n}\n" for n in range(40))
    code = await run_graph(
        GraphRequest(fast=True),
        _context(PEOPLE),
        stdin=io.StringIO(lines),
        stdout=out,
        clock=lambda: next(ticks),
    )
    assert code is ExitCode.OK
    err = capsys.readouterr().err
    assert (
        "note: ~40 windows at this machine's pace — roughly 3 min (progress below; Ctrl-C is safe)"
    ) in err


async def test_quick_pace_stays_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    ticks = iter([0.0] + [1.0] * 200)
    out = io.StringIO()
    lines = "".join(f"Ann {n}\n" for n in range(40))
    await run_graph(
        GraphRequest(fast=True),
        _context(PEOPLE),
        stdin=io.StringIO(lines),
        stdout=out,
        clock=lambda: next(ticks),
    )
    assert "at this machine's pace" not in capsys.readouterr().err


# --- --save ---------------------------------------------------------------------------


def _save_request(save: str, top: int | None = None) -> GraphRequest:
    return GraphRequest(fast=True, save=save, top=top)


CORPUS = "Ann met Bob at Acme\nBob saw Ann\nAcme hired Ann\n"


async def test_save_graphml_dot_html_write_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("g.graphml", "g.dot", "g.html", "g.mmd"):
        path = tmp_path / name
        await _run(_save_request(str(path)), _context(PEOPLE), CORPUS)
        assert path.exists()
        assert f"graph saved: {path}" in capsys.readouterr().err


async def test_save_csv_writes_the_pair_side_by_side(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await _run(_save_request(str(tmp_path / "g.csv")), _context(PEOPLE), CORPUS)
    nodes = (tmp_path / "g.nodes.csv").read_text(encoding="utf-8")
    edges = (tmp_path / "g.edges.csv").read_text(encoding="utf-8")
    assert nodes.startswith("id,label,count\n")
    assert edges.startswith("source,target,relation,weight\n")
    assert "graph saved:" in capsys.readouterr().err


async def test_save_vault_writes_notes_and_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = tmp_path / "vault"
    await _run(_save_request(f"{vault}/"), _context(PEOPLE), CORPUS)
    assert (vault / "index.md").exists()
    assert (vault / "Ann.md").exists()
    assert "(4 notes)" in capsys.readouterr().err


async def test_top_caps_display_formats_and_mermaid_notes_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mmd = tmp_path / "g.mmd"
    await _run(_save_request(str(mmd), top=2), _context(PEOPLE), CORPUS)
    text = mmd.read_text(encoding="utf-8")
    assert "Bob" not in text  # the smallest hub fell off
    assert "mermaid capped to the 2 biggest hubs of 3 nodes" in capsys.readouterr().err

    html = tmp_path / "g.html"
    await _run(_save_request(str(html), top=1), _context(PEOPLE), CORPUS)
    assert '"id": "Bob"' not in html.read_text(encoding="utf-8")
