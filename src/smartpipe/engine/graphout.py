"""Graph serializers for ``--save`` (wave G1) — pure, byte-deterministic.

Every writer takes the same ``(nodes, edges)`` pair and returns strings; the
verb owns the file I/O. Determinism is a feature (nodes sort by name, edges by
weight-then-names), so goldens pin every format. The HTML view is the one
place a network is honest about: it loads the vis-network renderer from a CDN
(disclosed in the file header) and degrades to a plain notice offline.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape, quoteattr

from smartpipe.core.errors import UsageFault
from smartpipe.engine.graphkg import human_ref

if TYPE_CHECKING:
    from collections.abc import Sequence

    from smartpipe.engine.graphkg import GraphEdge, GraphNode

__all__ = [
    "MERMAID_DEFAULT_CAP",
    "MermaidGraph",
    "SaveFormat",
    "save_format",
    "to_dot",
    "to_edges_csv",
    "to_graphml",
    "to_html",
    "to_mermaid",
    "to_nodes_csv",
    "to_obsidian",
]

MERMAID_DEFAULT_CAP = 40  # mermaid chokes on big graphs — hubs first, disclosed


class SaveFormat(Enum):
    GRAPHML = "graphml"
    DOT = "dot"
    MERMAID = "mermaid"
    CSV = "csv"
    HTML = "html"
    VAULT = "vault"


_SUFFIXES: dict[str, SaveFormat] = {
    ".graphml": SaveFormat.GRAPHML,
    ".dot": SaveFormat.DOT,
    ".gv": SaveFormat.DOT,
    ".mmd": SaveFormat.MERMAID,
    ".mermaid": SaveFormat.MERMAID,
    ".csv": SaveFormat.CSV,
    ".html": SaveFormat.HTML,
    ".htm": SaveFormat.HTML,
}


def save_format(raw: str) -> SaveFormat:
    """Dispatch ``--save`` by extension; a trailing ``/`` names an Obsidian vault."""
    if raw.endswith("/"):
        return SaveFormat.VAULT
    from pathlib import PurePath

    suffix = PurePath(raw).suffix.lower()
    fmt = _SUFFIXES.get(suffix)
    if fmt is None:
        raise UsageFault(
            "--save names the format by extension: "
            ".graphml, .dot, .mmd, .csv, or .html — or a directory (trailing /) "
            "for an Obsidian vault\n"
            f"  Got: {raw}"
        )
    return fmt


def _sorted_nodes(nodes: Sequence[GraphNode]) -> list[GraphNode]:
    return sorted(nodes, key=lambda node: node.name)


def _sorted_edges(edges: Sequence[GraphEdge]) -> list[GraphEdge]:
    return sorted(edges, key=lambda edge: (-edge.weight, edge.source, edge.target))


def _citations(edge: GraphEdge) -> str:
    """The provenance line every human-facing format shares: refs, then the cap."""
    refs = " · ".join(human_ref(ref) for ref in edge.sources)
    if edge.hidden_sources:
        refs += f" · +{edge.hidden_sources} more"
    return refs


# --- graphml (Gephi) ---------------------------------------------------------


def to_graphml(nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns"',
        '         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        '         xsi:schemaLocation="http://graphml.graphdrawing.org/xmlns'
        ' http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd">',
        '  <key id="d0" for="node" attr.name="label" attr.type="string"/>',
        '  <key id="d1" for="node" attr.name="count" attr.type="int"/>',
        '  <key id="d2" for="edge" attr.name="relation" attr.type="string"/>',
        '  <key id="d3" for="edge" attr.name="weight" attr.type="int"/>',
        '  <key id="d4" for="edge" attr.name="sources" attr.type="string"/>',
        '  <graph id="G" edgedefault="undirected">',
    ]
    for node in _sorted_nodes(nodes):
        lines += [
            f"    <node id={quoteattr(node.name)}>",
            f'      <data key="d0">{escape(node.label)}</data>',
            f'      <data key="d1">{node.count}</data>',
            "    </node>",
        ]
    for edge in _sorted_edges(edges):
        lines += [
            f"    <edge source={quoteattr(edge.source)} target={quoteattr(edge.target)}>",
            f'      <data key="d2">{escape(edge.relation)}</data>',
            f'      <data key="d3">{edge.weight}</data>',
            f'      <data key="d4">{escape(_citations(edge))}</data>',
            "    </edge>",
        ]
    lines += ["  </graph>", "</graphml>", ""]
    return "\n".join(lines)


# --- dot (Graphviz) ----------------------------------------------------------


def _dot_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _penwidth(weight: int, lightest: int, heaviest: int) -> int:
    """Weights bucketed onto penwidth 1-5; a flat graph stays hairline."""
    if heaviest == lightest:
        return 1
    return 1 + (4 * (weight - lightest)) // (heaviest - lightest)


def to_dot(nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]) -> str:
    ordered_edges = _sorted_edges(edges)
    weights = [edge.weight for edge in ordered_edges]
    lightest, heaviest = (min(weights), max(weights)) if weights else (0, 0)
    lines = [
        "graph smartpipe {",
        "  layout=neato;",
        "  overlap=false;",
        "  splines=true;",
        '  node [shape=ellipse, style=filled, fillcolor="#e8f7fb", color="#22809a",'
        ' fontname="Helvetica"];',
        '  edge [color="#8899aa"];',
    ]
    lines += [
        f"  {_dot_quote(node.name)} [label={_dot_quote(f'{node.name} ({node.count})')},"
        f" tooltip={_dot_quote(f'{node.label} · {node.count} mentions')}];"
        for node in _sorted_nodes(nodes)
    ]
    lines += [
        f"  {_dot_quote(edge.source)} -- {_dot_quote(edge.target)}"
        f" [penwidth={_penwidth(edge.weight, lightest, heaviest)},"
        f" tooltip={_dot_quote(f'{edge.relation} ×{edge.weight} — {_citations(edge)}')}];"  # noqa: RUF001 — the pinned count mark
        for edge in ordered_edges
    ]
    lines += ["}", ""]
    return "\n".join(lines)


# --- mermaid -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MermaidGraph:
    text: str
    shown: int
    total: int


def _mermaid_label(text: str) -> str:
    return text.replace('"', "#quot;")


def to_mermaid(nodes: Sequence[GraphNode], edges: Sequence[GraphEdge], *, cap: int) -> MermaidGraph:
    """``graph LR`` capped to the ``cap`` biggest hubs — mermaid chokes on
    huge graphs, so the cut is by mention count, disclosed via ``shown``/``total``."""
    hubs = sorted(nodes, key=lambda node: (-node.count, node.name))[:cap]
    kept = _sorted_nodes(hubs)
    identifier = {node.name: f"n{position}" for position, node in enumerate(kept)}
    lines = ["graph LR"]
    lines += [
        f'  {identifier[node.name]}["{_mermaid_label(f"{node.name} ({node.count})")}"]'
        for node in kept
    ]
    lines += [
        f"  {identifier[edge.source]} ---|{edge.weight}| {identifier[edge.target]}"
        for edge in _sorted_edges(edges)
        if edge.source in identifier and edge.target in identifier
    ]
    lines.append("")
    return MermaidGraph(text="\n".join(lines), shown=len(kept), total=len(nodes))


# --- csv (Neo4j/Kuzu-importable) ---------------------------------------------


def to_nodes_csv(nodes: Sequence[GraphNode]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["id", "label", "count"])
    writer.writerows([node.name, node.label, node.count] for node in _sorted_nodes(nodes))
    return out.getvalue()


def to_edges_csv(edges: Sequence[GraphEdge]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["source", "target", "relation", "weight"])
    writer.writerows(
        [edge.source, edge.target, edge.relation, edge.weight] for edge in _sorted_edges(edges)
    )
    return out.getvalue()


# --- obsidian vault ----------------------------------------------------------


_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|#^\[\]]')


def _page_names(nodes: Sequence[GraphNode]) -> dict[str, str]:
    """Entity name → vault page name: filesystem/wikilink-safe, collision-free."""
    pages: dict[str, str] = {}
    taken: set[str] = set()
    for node in _sorted_nodes(nodes):
        base = _UNSAFE_FILENAME.sub("-", node.name).strip(" .") or "entity"
        page = base
        ordinal = 2
        while page in taken or page == "index":
            page = f"{base} {ordinal}"
            ordinal += 1
        taken.add(page)
        pages[node.name] = page
    return pages


def to_obsidian(nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]) -> dict[str, str]:
    """The vault as ``filename → content``: one note per entity plus an index."""
    pages = _page_names(nodes)
    ordered_edges = _sorted_edges(edges)
    vault: dict[str, str] = {}
    for node in _sorted_nodes(nodes):
        lines = [
            "---",
            f"label: {node.label}",
            f"count: {node.count}",
            "---",
            "",
            f"# {node.name}",
            "",
        ]
        touching = [edge for edge in ordered_edges if node.name in (edge.source, edge.target)]
        for edge in touching:
            other = edge.target if edge.source == node.name else edge.source
            lines.append(
                f"- [[{pages.get(other, other)}]] — {edge.relation} ×{edge.weight}"  # noqa: RUF001 — the pinned count mark
            )
            lines += [f"  - {human_ref(ref)}" for ref in edge.sources]
            if edge.hidden_sources:
                lines.append(f"  - +{edge.hidden_sources} more")
        if not touching:
            lines.append("(no co-occurrences)")
        lines.append("")
        vault[f"{pages[node.name]}.md"] = "\n".join(lines)

    ranked = sorted(nodes, key=lambda node: (-node.count, node.name))
    index = [
        "# Graph index",
        "",
        f"{len(nodes)} entities · {len(edges)} edges",
        "",
    ]
    index += [
        f"- [[{pages[node.name]}]] — {node.label} ×{node.count}"  # noqa: RUF001 — the pinned count mark
        for node in ranked
    ]
    index.append("")
    vault["index.md"] = "\n".join(index)
    return vault


# --- html (the interactive view) ----------------------------------------------


def to_html(nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]) -> str:
    """One self-contained page: data embedded, vis-network from the CDN
    (disclosed), hover provenance cards, search, and a live weight filter."""
    payload = {
        "nodes": [
            {"id": node.name, "label": node.label, "count": node.count}
            for node in _sorted_nodes(nodes)
        ],
        "edges": [
            {
                "source": edge.source,
                "target": edge.target,
                "relation": edge.relation,
                "weight": edge.weight,
                "refs": [{"text": human_ref(ref), "path": ref.path} for ref in edge.sources],
                "hidden": edge.hidden_sources,
            }
            for edge in _sorted_edges(edges)
        ],
    }
    embedded = json.dumps(payload, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    heaviest = max((edge.weight for edge in edges), default=1)
    return (
        _HTML_TEMPLATE.replace("__GRAPH_JSON__", embedded)
        .replace("__NODE_COUNT__", str(len(nodes)))
        .replace("__EDGE_COUNT__", str(len(edges)))
        .replace("__MAX_WEIGHT__", str(max(heaviest, 1)))
    )


_HTML_TEMPLATE = """<!doctype html>
<!--
  smartpipe graph - interactive knowledge-graph view.
  Dependency note (honest): the ONLY external resource is the vis-network
  renderer, loaded from the unpkg CDN in the script tag below. The graph data
  itself is embedded in this file; offline, the page degrades to a plain notice.
-->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>smartpipe graph</title>
<style>
  :root { --ground: #0c0e12; --panel: #12161d; --line: #1f2732; --ink: #dbe4ee;
          --dim: #7d8b9d; --accent: #22d3ee; }
  * { box-sizing: border-box; margin: 0; }
  html, body { height: 100%; }
  body { background: var(--ground); color: var(--ink); overflow: hidden;
         font: 14px/1.45 ui-sans-serif, system-ui, "Segoe UI", sans-serif; }
  header { display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
           padding: 10px 16px; background: var(--panel);
           border-bottom: 1px solid var(--line); }
  header h1 { font-size: 15px; font-weight: 600; letter-spacing: .02em; }
  header h1::before { content: "◉ "; color: var(--accent); }
  .stat { color: var(--dim); font-variant-numeric: tabular-nums; }
  #search { flex: 1 1 180px; max-width: 320px; padding: 6px 10px;
            color: var(--ink); background: var(--ground);
            border: 1px solid var(--line); border-radius: 6px; outline: none; }
  #search:focus { border-color: var(--accent); }
  .weight { display: flex; align-items: center; gap: 8px; color: var(--dim); }
  .weight output { color: var(--accent); min-width: 1.5em; text-align: right;
                   font-variant-numeric: tabular-nums; }
  input[type=range] { accent-color: var(--accent); width: 140px; }
  #graph { position: absolute; inset: 49px 0 0 0; }
  #card { position: fixed; display: none; max-width: 380px; max-height: 60vh;
          overflow-y: auto; padding: 12px 14px; z-index: 10;
          background: var(--panel); border: 1px solid var(--line);
          border-top: 2px solid var(--accent); border-radius: 8px;
          box-shadow: 0 12px 32px rgba(0,0,0,.55); pointer-events: none; }
  #card h2 { font-size: 13px; font-weight: 600; margin-bottom: 2px; }
  #card .kind { color: var(--dim); font-size: 12px; margin-bottom: 8px; }
  #card ul { list-style: none; }
  #card li { padding: 2px 0; font-size: 12px; }
  #card li::before { content: "▸ "; color: var(--accent); }
  #card a { color: var(--ink); text-decoration: none;
            border-bottom: 1px dotted var(--dim); }
  #card .more { color: var(--dim); font-style: italic; }
  #fallback { position: absolute; inset: 49px 0 0 0; display: none;
              place-content: center; text-align: center; color: var(--dim);
              padding: 24px; }
  #fallback strong { color: var(--ink); }
</style>
</head>
<body>
<header>
  <h1>smartpipe graph</h1>
  <span class="stat">__NODE_COUNT__ entities · __EDGE_COUNT__ edges</span>
  <input id="search" type="search" placeholder="find an entity…" autocomplete="off">
  <label class="weight">weight ≥
    <input id="weight" type="range" min="1" max="__MAX_WEIGHT__" value="1" step="1">
    <output id="weight-value">1</output>
  </label>
</header>
<div id="graph"></div>
<div id="fallback">
  <p><strong>The vis-network renderer could not load</strong> (offline, or the CDN is
  unreachable).<br>The graph data is still embedded in this file — reopen with a
  connection,<br>or use the .graphml / .csv exports.</p>
</div>
<div id="card"></div>
<script id="graph-data" type="application/json">__GRAPH_JSON__</script>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<script>
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("graph-data").textContent);
  window.addEventListener("load", function () {
    if (typeof vis === "undefined") {
      document.getElementById("fallback").style.display = "grid";
      return;
    }
    var PALETTE = ["#22d3ee", "#a78bfa", "#34d399", "#fbbf24", "#fb7185",
                   "#60a5fa", "#f472b6", "#4ade80"];
    var labels = Array.from(new Set(DATA.nodes.map(function (n) { return n.label; }))).sort();
    var colorOf = {};
    labels.forEach(function (label, i) { colorOf[label] = PALETTE[i % PALETTE.length]; });

    var nodeSet = new vis.DataSet(DATA.nodes.map(function (n) {
      return { id: n.id, label: n.id, value: n.count,
               color: { background: colorOf[n.label], border: "#0c0e12",
                        highlight: { background: "#ffffff", border: colorOf[n.label] } } };
    }));
    var edgeSet = new vis.DataSet(DATA.edges.map(function (e, i) {
      return { id: i, from: e.source, to: e.target, value: e.weight, weight: e.weight };
    }));
    var minWeight = 1;
    var edgeView = new vis.DataView(edgeSet, {
      filter: function (e) { return e.weight >= minWeight; }
    });

    var network = new vis.Network(document.getElementById("graph"),
      { nodes: nodeSet, edges: edgeView },
      { nodes: { shape: "dot", scaling: { min: 8, max: 36 },
                 font: { color: "#dbe4ee", size: 13, face: "ui-sans-serif" } },
        edges: { color: { color: "#2a3646", highlight: "#22d3ee", hover: "#22d3ee" },
                 scaling: { min: 1, max: 6 }, smooth: { type: "continuous" } },
        physics: { barnesHut: { gravitationalConstant: -6000, springLength: 140 },
                   stabilization: { iterations: 120 } },
        interaction: { hover: true } });

    var card = document.getElementById("card");
    function esc(s) {
      return String(s).replace(/[&<>"]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
      });
    }
    function refLink(ref) {
      var href = ref.path.charAt(0) === "/" ? "file://" + encodeURI(ref.path)
                                            : encodeURI(ref.path);
      return '<li><a href="' + esc(href) + '">' + esc(ref.text) + "</a></li>";
    }
    function showCard(html, xy) {
      card.innerHTML = html;
      card.style.display = "block";
      var pad = 14;
      var x = Math.min(xy.x + pad, window.innerWidth - card.offsetWidth - pad);
      var y = Math.min(xy.y + pad, window.innerHeight - card.offsetHeight - pad);
      card.style.left = Math.max(pad, x) + "px";
      card.style.top = Math.max(pad, y) + "px";
    }
    function hideCard() { card.style.display = "none"; }

    network.on("hoverEdge", function (params) {
      var e = DATA.edges[params.edge];
      var html = "<h2>" + esc(e.source) + " ↔ " + esc(e.target) + "</h2>" +
        '<div class="kind">' + esc(e.relation) + " \\u00d7" + e.weight + "</div><ul>" +
        e.refs.map(refLink).join("") +
        (e.hidden ? '<li class="more">+' + e.hidden + " more</li>" : "") + "</ul>";
      showCard(html, params.pointer.DOM);
    });
    network.on("hoverNode", function (params) {
      var n = DATA.nodes.find(function (node) { return node.id === params.node; });
      if (!n) { return; }
      var html = "<h2>" + esc(n.id) + "</h2>" +
        '<div class="kind">' + esc(n.label) + " · " + n.count + " mentions</div>";
      showCard(html, params.pointer.DOM);
    });
    network.on("blurEdge", hideCard);
    network.on("blurNode", hideCard);
    network.on("dragStart", hideCard);

    document.getElementById("search").addEventListener("input", function (event) {
      var query = event.target.value.trim().toLowerCase();
      if (!query) {
        network.unselectAll();
        network.fit({ animation: true });
        return;
      }
      var matches = DATA.nodes
        .filter(function (n) { return n.id.toLowerCase().indexOf(query) !== -1; })
        .map(function (n) { return n.id; });
      network.selectNodes(matches);
      if (matches.length) {
        network.focus(matches[0], { scale: 1.4, animation: true });
      }
    });

    var slider = document.getElementById("weight");
    var readout = document.getElementById("weight-value");
    slider.addEventListener("input", function () {
      minWeight = Number(slider.value);
      readout.textContent = slider.value;
      edgeView.refresh();
    });
  });
})();
</script>
</body>
</html>
"""
