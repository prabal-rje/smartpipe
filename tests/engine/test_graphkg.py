"""engine/graphkg — pure knowledge-graph math (wave G1). 100% coverage required."""

from __future__ import annotations

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.engine.graphkg import (
    EntitySpan,
    EntityWindow,
    GraphEdge,
    ItemEntities,
    SpineRef,
    SurfaceCount,
    Window,
    build_nodes,
    fold_edges,
    fold_stats,
    fold_surfaces,
    human_ref,
    parse_window,
    prune_edges,
    sentence_bounds,
    spine_record,
    surface_counts,
    windows,
)


def _ref(path: str = "notes.txt", line: int = 1) -> SpineRef:
    return SpineRef(path=path, cut="lines", position=line)


def _span(name: str, label: str = "person", start: int = 0) -> EntitySpan:
    return EntitySpan(name=name, label=label, start=start, end=start + len(name))


def _item(
    spans: tuple[EntitySpan, ...],
    *,
    text: str = "",
    path: str = "notes.txt",
    line: int = 1,
    doc: str | None = None,
) -> ItemEntities:
    return ItemEntities(ref=_ref(path, line), doc=doc or path, text=text, spans=spans)


# --- sentence_bounds ---------------------------------------------------------


def test_sentence_bounds_empty_text_has_no_sentences() -> None:
    assert sentence_bounds("") == ()


def test_sentence_bounds_single_sentence_without_terminator() -> None:
    assert sentence_bounds("no punctuation here") == ((0, 19),)


def test_sentence_bounds_partitions_text_at_terminators() -> None:
    text = "One. Two! Three?"
    bounds = sentence_bounds(text)
    assert bounds == ((0, 5), (5, 10), (10, 16))
    assert "".join(text[a:b] for a, b in bounds) == text


def test_sentence_bounds_closing_quotes_stay_with_their_sentence() -> None:
    text = 'He said "go." Then left.'
    bounds = sentence_bounds(text)
    assert text[bounds[0][0] : bounds[0][1]] == 'He said "go." '
    assert text[bounds[1][0] : bounds[1][1]] == "Then left."


def test_sentence_bounds_paragraph_break_is_a_boundary() -> None:
    text = "alpha beta\n\ngamma"
    assert sentence_bounds(text) == ((0, 12), (12, 17))


# --- windows -----------------------------------------------------------------


def test_chunk_windows_one_window_per_item_names_deduped_in_order() -> None:
    item = _item((_span("Bob"), _span("Ann", start=10), _span("Bob", start=20)))
    assert windows([item], Window.CHUNK) == (EntityWindow(ref=item.ref, names=("Bob", "Ann")),)


def test_chunk_windows_skip_items_without_entities() -> None:
    assert windows([_item(())], Window.CHUNK) == ()


def test_sentence_windows_split_entities_by_sentence() -> None:
    text = "Bob met Ann. Cid slept."
    item = _item(
        (_span("Bob"), _span("Ann", start=8), _span("Cid", start=13)),
        text=text,
    )
    assert windows([item], Window.SENTENCE) == (
        EntityWindow(ref=item.ref, names=("Bob", "Ann")),
        EntityWindow(ref=item.ref, names=("Cid",)),
    )


def test_document_windows_group_items_by_document() -> None:
    one = _item((_span("Bob"),), path="a.txt", line=1, doc="a.txt")
    two = _item((_span("Ann"),), path="a.txt", line=2, doc="a.txt")
    other = _item((_span("Cid"),), path="b.txt", line=1, doc="b.txt")
    assert windows([one, two, other], Window.DOCUMENT) == (
        EntityWindow(ref=SpineRef(path="a.txt", cut="file"), names=("Bob", "Ann")),
        EntityWindow(ref=SpineRef(path="b.txt", cut="file"), names=("Cid",)),
    )


def test_parse_window_accepts_the_three_dial_values() -> None:
    assert parse_window("sentence") is Window.SENTENCE
    assert parse_window("chunk") is Window.CHUNK
    assert parse_window("document") is Window.DOCUMENT


def test_parse_window_refuses_unknown_values() -> None:
    with pytest.raises(UsageFault, match="--window"):
        parse_window("paragraph")


# --- surface_counts / folding ------------------------------------------------


def test_surface_counts_tally_mentions_in_first_appearance_order() -> None:
    items = [
        _item((_span("Ann"), _span("Bob"), _span("Ann", start=9))),
        _item((_span("Bob"),), line=2),
    ]
    assert surface_counts(items) == (
        SurfaceCount(name="Ann", label="person", count=2),
        SurfaceCount(name="Bob", label="person", count=2),
    )


def test_surface_counts_keep_same_name_with_different_labels_apart() -> None:
    items = [_item((_span("Apple", "company"), _span("Apple", "fruit", start=9)))]
    assert surface_counts(items) == (
        SurfaceCount(name="Apple", label="company", count=1),
        SurfaceCount(name="Apple", label="fruit", count=1),
    )


def test_fold_surfaces_casefold_rung_folds_to_most_frequent_surface() -> None:
    counts = (
        SurfaceCount("acme corp", "company", 1),
        SurfaceCount("Acme Corp", "company", 3),
    )
    assert fold_surfaces(counts, {}) == {"acme corp": "Acme Corp", "Acme Corp": "Acme Corp"}


def test_fold_surfaces_casefold_rung_is_label_scoped() -> None:
    counts = (
        SurfaceCount("Apple", "company", 1),
        SurfaceCount("apple", "fruit", 1),
    )
    folded = fold_surfaces(counts, {})
    assert folded == {"Apple": "Apple", "apple": "apple"}


def test_fold_surfaces_embedding_rung_folds_similar_names_to_most_frequent() -> None:
    counts = (
        SurfaceCount("Acme Corporation", "company", 2),
        SurfaceCount("Acme Corp", "company", 5),
        SurfaceCount("Zenith", "company", 1),
    )
    vectors = {
        "Acme Corporation": (1.0, 0.0),
        "Acme Corp": (1.0, 0.0),
        "Zenith": (0.0, 1.0),
    }
    folded = fold_surfaces(counts, vectors)
    assert folded["Acme Corporation"] == "Acme Corp"
    assert folded["Acme Corp"] == "Acme Corp"
    assert folded["Zenith"] == "Zenith"


def test_fold_surfaces_embedding_rung_is_label_scoped() -> None:
    counts = (
        SurfaceCount("Jordan", "person", 2),
        SurfaceCount("Jordan", "location", 1),
    )
    vectors = {"Jordan": (1.0, 0.0)}
    folded = fold_surfaces(counts, vectors)
    assert folded == {"Jordan": "Jordan"}  # label-scoped keys collapse per surface


def test_fold_surfaces_names_without_vectors_keep_their_own_node() -> None:
    counts = (
        SurfaceCount("Acme Corp", "company", 5),
        SurfaceCount("Acme Corporation", "company", 2),
    )
    folded = fold_surfaces(counts, {"Acme Corp": (1.0, 0.0)})
    assert folded["Acme Corporation"] == "Acme Corporation"


def test_fold_surfaces_empty_counts_fold_to_nothing() -> None:
    assert fold_surfaces((), {}) == {}


def test_fold_surfaces_chains_casefold_then_embedding() -> None:
    counts = (
        SurfaceCount("ACME CORP", "company", 1),
        SurfaceCount("Acme Corp", "company", 4),
        SurfaceCount("Acme Corporation", "company", 2),
    )
    vectors = {"Acme Corp": (1.0, 0.0), "Acme Corporation": (1.0, 0.0)}
    folded = fold_surfaces(counts, vectors)
    assert folded == {
        "ACME CORP": "Acme Corp",
        "Acme Corp": "Acme Corp",
        "Acme Corporation": "Acme Corp",
    }


def test_fold_stats_count_folded_names_and_their_nodes() -> None:
    canonical = {
        "ACME CORP": "Acme Corp",
        "Acme Corp": "Acme Corp",
        "Acme Corporation": "Acme Corp",
        "Bob": "Bob",
        "Robert": "Bob",
        "Zenith": "Zenith",
    }
    assert fold_stats(canonical) == (5, 2)


def test_fold_stats_without_folds_is_zero() -> None:
    assert fold_stats({"Ann": "Ann"}) == (0, 0)


# --- nodes -------------------------------------------------------------------


def test_build_nodes_sums_mentions_onto_canonical_names_sorted() -> None:
    counts = (
        SurfaceCount("Zenith", "company", 1),
        SurfaceCount("Acme Corp", "company", 5),
        SurfaceCount("ACME CORP", "company", 2),
    )
    canonical = {"Zenith": "Zenith", "Acme Corp": "Acme Corp", "ACME CORP": "Acme Corp"}
    nodes = build_nodes(counts, canonical)
    assert [(n.name, n.label, n.count) for n in nodes] == [
        ("Acme Corp", "company", 7),
        ("Zenith", "company", 1),
    ]


def test_build_nodes_majority_label_wins() -> None:
    counts = (
        SurfaceCount("Jordan", "location", 1),
        SurfaceCount("Jordan", "person", 3),
    )
    canonical = {"Jordan": "Jordan"}
    nodes = build_nodes(counts, canonical)
    assert [(n.name, n.label, n.count) for n in nodes] == [("Jordan", "person", 4)]


# --- edges -------------------------------------------------------------------


def test_fold_edges_weight_counts_windows_containing_both() -> None:
    shared = (
        EntityWindow(ref=_ref(line=1), names=("Bob", "Ann")),
        EntityWindow(ref=_ref(line=2), names=("Ann", "Bob")),
        EntityWindow(ref=_ref(line=3), names=("Bob",)),
    )
    edges = fold_edges(shared, {"Bob": "Bob", "Ann": "Ann"})
    assert edges == (
        GraphEdge(
            source="Ann",
            target="Bob",
            relation="co-occurs",
            weight=2,
            sources=(_ref(line=1), _ref(line=2)),
        ),
    )


def test_fold_edges_apply_canonical_names_and_skip_self_edges() -> None:
    fold = {"Acme Corp": "Acme", "Acme": "Acme", "Bob": "Bob"}
    grouped = (EntityWindow(ref=_ref(), names=("Acme Corp", "Acme", "Bob")),)
    edges = fold_edges(grouped, fold)
    assert [(e.source, e.target, e.weight) for e in edges] == [("Acme", "Bob", 1)]


def test_fold_edges_sources_dedupe_within_an_edge() -> None:
    same_ref = (
        EntityWindow(ref=_ref(line=7), names=("Bob", "Ann")),
        EntityWindow(ref=_ref(line=7), names=("Bob", "Ann")),
    )
    edges = fold_edges(same_ref, {"Bob": "Bob", "Ann": "Ann"})
    assert edges[0].weight == 2
    assert edges[0].sources == (_ref(line=7),)
    assert edges[0].hidden_sources == 0


def test_fold_edges_cap_sources_and_count_the_hidden_rest() -> None:
    spread = tuple(EntityWindow(ref=_ref(line=line), names=("Bob", "Ann")) for line in range(1, 26))
    edges = fold_edges(spread, {"Bob": "Bob", "Ann": "Ann"}, cap=20)
    assert edges[0].weight == 25
    assert len(edges[0].sources) == 20
    assert edges[0].hidden_sources == 5


def test_fold_edges_sorted_by_weight_then_names() -> None:
    grouped = (
        EntityWindow(ref=_ref(line=1), names=("A", "B")),
        EntityWindow(ref=_ref(line=2), names=("A", "B")),
        EntityWindow(ref=_ref(line=3), names=("A", "C")),
        EntityWindow(ref=_ref(line=4), names=("B", "C")),
    )
    identity = {name: name for name in "ABC"}
    edges = fold_edges(grouped, identity)
    assert [(e.source, e.target, e.weight) for e in edges] == [
        ("A", "B", 2),
        ("A", "C", 1),
        ("B", "C", 1),
    ]


def test_prune_edges_drop_below_min_weight_and_count_them() -> None:
    grouped = (
        EntityWindow(ref=_ref(line=1), names=("A", "B")),
        EntityWindow(ref=_ref(line=2), names=("A", "B")),
        EntityWindow(ref=_ref(line=3), names=("A", "C")),
    )
    edges = fold_edges(grouped, {name: name for name in "ABC"})
    kept, pruned = prune_edges(edges, min_weight=2)
    assert [(e.source, e.target) for e in kept] == [("A", "B")]
    assert pruned == 1


def test_prune_edges_min_weight_one_keeps_everything() -> None:
    edges = fold_edges((EntityWindow(ref=_ref(), names=("A", "B")),), {"A": "A", "B": "B"})
    kept, pruned = prune_edges(edges, min_weight=1)
    assert kept == edges
    assert pruned == 0


# --- refs --------------------------------------------------------------------


def test_human_ref_wording_by_cut() -> None:
    assert human_ref(SpineRef(path="a.txt", cut="lines", position=12)) == "a.txt:12"
    assert human_ref(SpineRef(path="a.jsonl", cut="jsonl", position=3)) == "a.jsonl:3"
    assert human_ref(SpineRef(path="a.csv", cut="csv", position=2)) == "a.csv:2"
    assert human_ref(SpineRef(path="r.pdf", cut="pages", position=3)) == "r.pdf p.3"
    assert human_ref(SpineRef(path="r.pdf", cut="file")) == "r.pdf"
    assert human_ref(SpineRef(path="call.wav", cut="minutes", position=2)) == "call.wav §2"


def test_human_ref_adopted_label_wins() -> None:
    ref = SpineRef(path="call.wav", cut="minutes", position=2, label="call.wav §00:10-00:20")
    assert human_ref(ref) == "call.wav §00:10-00:20"


def test_human_ref_without_position_is_the_path() -> None:
    assert human_ref(SpineRef(path="a.txt", cut="lines")) == "a.txt"


def test_human_ref_whole_file_ignores_a_stray_position() -> None:
    assert human_ref(SpineRef(path="r.pdf", cut="file", position=2)) == "r.pdf"


def test_spine_record_mirrors_the_source_spine() -> None:
    assert spine_record(SpineRef(path="a.txt", cut="lines", position=12)) == {
        "path": "a.txt",
        "as": "lines",
        "line": 12,
    }
    assert spine_record(SpineRef(path="r.pdf", cut="pages", position=3)) == {
        "path": "r.pdf",
        "as": "pages",
        "page": 3,
    }
    assert spine_record(SpineRef(path="r.pdf", cut="file")) == {"path": "r.pdf", "as": "file"}
    assert spine_record(SpineRef(path="r.pdf", cut="file", position=2)) == {
        "path": "r.pdf",
        "as": "file",
    }
    assert spine_record(SpineRef(path="c.wav", cut="minutes", position=2)) == {
        "path": "c.wav",
        "as": "minutes",
        "segment": 2,
    }


def test_spine_record_carries_the_adopted_label() -> None:
    ref = SpineRef(path="c.wav", cut="minutes", position=2, label="c.wav §00:10-00:20")
    assert spine_record(ref) == {
        "path": "c.wav",
        "as": "minutes",
        "segment": 2,
        "label": "c.wav §00:10-00:20",
    }


def test_spine_record_without_position_omits_the_locator() -> None:
    assert spine_record(SpineRef(path="a.txt", cut="lines")) == {"path": "a.txt", "as": "lines"}
