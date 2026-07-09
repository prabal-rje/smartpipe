"""engine/graphout — graph serializers, byte-deterministic, golden-pinned."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.engine.graphkg import GraphEdge, GraphNode, SpineRef
from smartpipe.engine.graphout import (
    SaveFormat,
    save_format,
    to_dot,
    to_edges_csv,
    to_graphml,
    to_html,
    to_mermaid,
    to_nodes_csv,
    to_obsidian,
)

GOLDEN = Path(__file__).parent.parent / "golden" / "graph"

NODES = (
    GraphNode(name="Acme Corp", label="company", count=7),
    GraphNode(name="Maria Schneider", label="person", count=5),
    GraphNode(name="Zurich", label="location", count=2),
)
EDGES = (
    GraphEdge(
        source="Acme Corp",
        target="Maria Schneider",
        relation="co-occurs",
        weight=4,
        sources=(
            SpineRef(path="notes.txt", cut="lines", position=12),
            SpineRef(path="report.pdf", cut="pages", position=3),
        ),
    ),
    GraphEdge(
        source="Acme Corp",
        target="Zurich",
        relation="co-occurs",
        weight=2,
        sources=(
            SpineRef(path="call.wav", cut="minutes", position=2, label="call.wav §00:10-00:20"),
        ),
        hidden_sources=1,
    ),
    GraphEdge(
        source="Maria Schneider",
        target="Zurich",
        relation="co-occurs",
        weight=1,
        sources=(SpineRef(path="notes.txt", cut="lines", position=30),),
    ),
)


def _check_golden(name: str, rendered: str) -> None:
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    assert rendered == path.read_text(encoding="utf-8"), (
        f"graph serializer output '{name}' drifted from its golden; if intended: make golden"
    )


# --- dispatch ----------------------------------------------------------------


def test_save_format_dispatches_by_extension() -> None:
    assert save_format("out.graphml") is SaveFormat.GRAPHML
    assert save_format("out.dot") is SaveFormat.DOT
    assert save_format("out.gv") is SaveFormat.DOT
    assert save_format("out.mmd") is SaveFormat.MERMAID
    assert save_format("out.mermaid") is SaveFormat.MERMAID
    assert save_format("out.csv") is SaveFormat.CSV
    assert save_format("out.html") is SaveFormat.HTML
    assert save_format("out.htm") is SaveFormat.HTML
    assert save_format("OUT.GRAPHML") is SaveFormat.GRAPHML


def test_save_format_trailing_slash_is_an_obsidian_vault() -> None:
    assert save_format("vault/") is SaveFormat.VAULT


def test_save_format_unknown_extension_names_the_menu() -> None:
    with pytest.raises(UsageFault, match=r"\.graphml.*\.html"):
        save_format("out.xlsx")


# --- graphml -----------------------------------------------------------------


def test_graphml_matches_golden() -> None:
    _check_golden("tiny.graphml", to_graphml(NODES, EDGES))


def test_graphml_is_strictly_parseable_gephi_shaped() -> None:
    root = ET.fromstring(to_graphml(NODES, EDGES))  # our own output — a strictness gate
    ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
    assert root.tag == "{http://graphml.graphdrawing.org/xmlns}graphml"
    keys = {key.get("attr.name") for key in root.findall("g:key", ns)}
    assert keys == {"label", "count", "relation", "weight", "sources"}
    graph = root.find("g:graph", ns)
    assert graph is not None
    assert graph.get("edgedefault") == "undirected"
    nodes = graph.findall("g:node", ns)
    assert [node.get("id") for node in nodes] == ["Acme Corp", "Maria Schneider", "Zurich"]
    edges = graph.findall("g:edge", ns)
    assert len(edges) == 3
    first = edges[0]
    assert (first.get("source"), first.get("target")) == ("Acme Corp", "Maria Schneider")
    attr_name = {key.get("id"): key.get("attr.name") for key in root.findall("g:key", ns)}
    data = {attr_name[entry.get("key")]: entry.text for entry in first.findall("g:data", ns)}
    assert data["weight"] == "4"
    assert data["relation"] == "co-occurs"
    assert data["sources"] == "notes.txt:12 · report.pdf p.3"


def test_graphml_escapes_markup_in_names() -> None:
    spiky = (GraphNode(name='A<&"B', label="x", count=1),)
    root = ET.fromstring(to_graphml(spiky, ()))  # our own output
    node = root.find(
        "{http://graphml.graphdrawing.org/xmlns}graph/{http://graphml.graphdrawing.org/xmlns}node"
    )
    assert node is not None
    assert node.get("id") == 'A<&"B'


# --- dot ---------------------------------------------------------------------


def test_dot_matches_golden() -> None:
    _check_golden("tiny.dot", to_dot(NODES, EDGES))


def test_dot_is_syntactically_balanced_graphviz() -> None:
    rendered = to_dot(NODES, EDGES)
    assert rendered.startswith("graph smartpipe {\n")
    assert rendered.rstrip().endswith("}")
    assert rendered.count("{") == rendered.count("}")
    assert rendered.count(" -- ") == 3


def test_dot_buckets_weight_into_penwidth_one_to_five() -> None:
    rendered = to_dot(NODES, EDGES)
    assert "penwidth=5" in rendered  # weight 4 (the max) gets the widest line
    assert "penwidth=1" in rendered  # weight 1 (the min) gets the thinnest


def test_dot_single_weight_graph_keeps_penwidth_one() -> None:
    lone = (
        GraphEdge(
            source="A",
            target="B",
            relation="co-occurs",
            weight=9,
            sources=(SpineRef(path="a.txt", cut="lines", position=1),),
        ),
    )
    rendered = to_dot((GraphNode("A", "x", 1), GraphNode("B", "x", 1)), lone)
    assert "penwidth=1" in rendered


def test_dot_tooltips_carry_citations_and_escape_quotes() -> None:
    rendered = to_dot(NODES, EDGES)
    assert 'tooltip="co-occurs ×2 — call.wav §00:10-00:20 · +1 more"' in rendered  # noqa: RUF001
    spiky = (GraphNode(name='say "hi"', label="x", count=1),)
    assert '"say \\"hi\\""' in to_dot(spiky, ())


# --- mermaid -----------------------------------------------------------------


def test_mermaid_matches_golden() -> None:
    _check_golden("tiny.mmd", to_mermaid(NODES, EDGES, cap=40).text)


def test_mermaid_caps_to_the_biggest_hubs() -> None:
    capped = to_mermaid(NODES, EDGES, cap=2)
    assert capped.shown == 2
    assert capped.total == 3
    assert "Zurich" not in capped.text  # the smallest node fell off
    assert "Maria Schneider" in capped.text
    lines = capped.text.splitlines()
    assert lines[0] == "graph LR"
    assert sum(1 for line in lines if "---" in line) == 1  # only the edge among kept nodes


def test_mermaid_uncapped_shows_everything() -> None:
    full = to_mermaid(NODES, EDGES, cap=40)
    assert full.shown == full.total == 3
    assert full.text.count("---") == 3


def test_mermaid_escapes_quotes_in_labels() -> None:
    spiky = (GraphNode(name='say "hi"', label="x", count=1),)
    assert "#quot;" in to_mermaid(spiky, (), cap=40).text


# --- csv ---------------------------------------------------------------------


def test_nodes_csv_matches_golden() -> None:
    _check_golden("tiny.nodes.csv", to_nodes_csv(NODES))


def test_edges_csv_matches_golden() -> None:
    _check_golden("tiny.edges.csv", to_edges_csv(EDGES))


def test_csv_quotes_commas_in_names() -> None:
    spiky = (GraphNode(name="Acme, Inc.", label="company", count=1),)
    assert '"Acme, Inc."' in to_nodes_csv(spiky)


# --- html --------------------------------------------------------------------


def test_html_matches_golden() -> None:
    _check_golden("tiny.html", to_html(NODES, EDGES))


def test_html_is_self_contained_with_an_honest_cdn_note() -> None:
    rendered = to_html(NODES, EDGES)
    assert rendered.count("<script") == rendered.count("</script>")
    header = "\n".join(rendered.splitlines()[:8])
    assert "vis-network" in header  # the file header comment discloses the CDN honestly
    assert "CDN" in header
    assert "#0c0e12" in rendered  # the dark ground
    assert "#22d3ee" in rendered  # the cyan accent
    assert 'id="search"' in rendered
    assert 'id="weight"' in rendered  # the live weight slider
    assert "could not load" in rendered  # the graceful CDN-unreachable message
    assert "Maria Schneider" in rendered  # data embedded as JSON


def test_html_embedded_json_cannot_break_out_of_its_script_tag() -> None:
    spiky = (GraphNode(name="</script><b>x", label="y", count=1),)
    rendered = to_html(spiky, ())
    assert "</script><b>x" not in rendered
    assert "<\\/script><b>x" in rendered


# --- obsidian vault ----------------------------------------------------------


def test_obsidian_vault_matches_goldens() -> None:
    vault = to_obsidian(NODES, EDGES)
    assert sorted(vault) == [
        "Acme Corp.md",
        "Maria Schneider.md",
        "Zurich.md",
        "index.md",
    ]
    for name, content in vault.items():
        _check_golden(f"vault/{name}", content)


def test_obsidian_filenames_are_sanitized_and_collision_free() -> None:
    clashing = (
        GraphNode(name="a/b", label="x", count=2),
        GraphNode(name="a\\b", label="x", count=1),
    )
    vault = to_obsidian(clashing, ())
    assert sorted(vault) == ["a-b 2.md", "a-b.md", "index.md"]
    assert "[[a-b]]" in vault["index.md"]
    assert "[[a-b 2]]" in vault["index.md"]
