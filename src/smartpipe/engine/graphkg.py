"""Knowledge-graph math for the ``graph`` verb (wave G1) — pure, exhaustively tested.

Entities co-occur inside a window (sentence, chunk, or document); windows fold
into weighted undirected edges with capped spine-ref provenance; near-duplicate
entity names fold onto the most frequent surface form through the same
leader-cluster machinery ``distinct`` uses. Nothing here does I/O — the NER
model and the embedder arrive as data (spans, vectors) or ``Protocol`` seams.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from itertools import combinations
from typing import TYPE_CHECKING, Protocol, assert_never

from smartpipe.core.errors import UsageFault
from smartpipe.engine.clustering import leader_clusters

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

__all__ = [
    "FOLD_THRESHOLD",
    "EdgeAssertion",
    "EntityFinder",
    "EntitySpan",
    "EntityWindow",
    "GraphEdge",
    "GraphNode",
    "ItemEntities",
    "SpineRef",
    "SurfaceCount",
    "Window",
    "assertion_surface_counts",
    "build_nodes",
    "fold_assertions",
    "fold_edges",
    "fold_stats",
    "fold_surfaces",
    "human_ref",
    "name_edges",
    "parse_window",
    "prune_edges",
    "sentence_bounds",
    "spine_from_record",
    "spine_record",
    "surface_counts",
    "windows",
]

FOLD_THRESHOLD = 0.90  # distinct's near-duplicate bar — entity names are short texts
_SOURCE_CAP = 20  # provenance refs kept per edge; the rest fold into a `+N more` count


class Window(Enum):
    """The co-occurrence dial: what counts as "together"."""

    SENTENCE = "sentence"
    CHUNK = "chunk"  # the item itself — the default
    DOCUMENT = "document"


def parse_window(raw: str) -> Window:
    """The CLI dial value as a ``Window`` — loud on anything else."""
    try:
        return Window(raw)
    except ValueError as exc:
        choices = ", ".join(mode.value for mode in Window)
        raise UsageFault(f"--window takes one of: {choices} (got {raw!r})") from exc


@dataclass(frozen=True, slots=True)
class EntitySpan:
    """One entity mention: its surface form, user-named label, char offsets."""

    name: str
    label: str
    start: int
    end: int


class EntityFinder(Protocol):
    """The NER seam: implementations live in ``models/``; tests inject fakes."""

    def find(self, text: str) -> tuple[EntitySpan, ...]: ...
    def load(self, *, quiet: bool = False) -> None:
        """Trigger the one-time model load up front so the download/session-init
        shows a caller-owned status instead of a silent block. ``quiet`` asks the
        implementation to suppress its own third-party progress chatter because
        the caller owns the terminal row."""
        ...


@dataclass(frozen=True, slots=True)
class SpineRef:
    """Provenance the engine can carry without importing ``io``: the same
    fields the ``__source`` spine record has (item 13)."""

    path: str
    cut: str = "lines"
    position: int | None = None  # 1-based line/page/segment
    label: str | None = None  # adopted human wording ("call.wav §00:10-00:20")


@dataclass(frozen=True, slots=True)
class ItemEntities:
    """One read item after NER: where it came from, which document it belongs
    to, its free text, and the entity mentions found in it."""

    ref: SpineRef
    doc: str
    text: str
    spans: tuple[EntitySpan, ...]


@dataclass(frozen=True, slots=True)
class EntityWindow:
    """One co-occurrence window: its provenance ref and the distinct surface
    names present, in first-appearance order."""

    ref: SpineRef
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GraphNode:
    name: str
    label: str
    count: int  # total mentions across the corpus (folded surfaces included)


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: str
    target: str
    relation: str
    weight: int  # windows containing both endpoints
    sources: tuple[SpineRef, ...]  # capped provenance (first-seen order)
    hidden_sources: int = 0  # distinct refs beyond the cap — the `+N more` count


@dataclass(frozen=True, slots=True)
class SurfaceCount:
    """One distinct (surface name, label) pair with its mention count."""

    name: str
    label: str
    count: int


# sentence ends: terminator (+ closing quotes/brackets) then whitespace — or a
# paragraph break. The trailing whitespace stays with the sentence so bounds
# partition the text and every span start falls inside exactly one window.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])[\"'”’)\]]*\s+|\n{2,}")  # noqa: RUF001 — real curly quotes


def sentence_bounds(text: str) -> tuple[tuple[int, int], ...]:
    """Half-open char ranges partitioning ``text`` into sentences."""
    bounds: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_BREAK.finditer(text):
        # both break patterns consume ≥1 char, so every match advances `start`
        bounds.append((start, match.end()))
        start = match.end()
    if start < len(text):
        bounds.append((start, len(text)))
    return tuple(bounds)


def windows(items: Sequence[ItemEntities], mode: Window) -> tuple[EntityWindow, ...]:
    """Every co-occurrence window in the corpus, per the dial. Windows without
    entities carry no information and are dropped."""
    match mode:
        case Window.CHUNK:
            grouped = [(item.ref, _distinct_names(item.spans)) for item in items]
        case Window.SENTENCE:
            grouped = [
                (item.ref, _distinct_names(within))
                for item in items
                for within in _sentence_groups(item)
            ]
        case Window.DOCUMENT:
            grouped = _document_groups(items)
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)
    return tuple(EntityWindow(ref=ref, names=names) for ref, names in grouped if names)


def _distinct_names(spans: Sequence[EntitySpan]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(span.name for span in spans))


def _sentence_groups(item: ItemEntities) -> list[tuple[EntitySpan, ...]]:
    return [
        tuple(span for span in item.spans if start <= span.start < end)
        for start, end in sentence_bounds(item.text)
    ]


def _document_groups(items: Sequence[ItemEntities]) -> list[tuple[SpineRef, tuple[str, ...]]]:
    names_by_doc: dict[str, dict[str, None]] = {}
    for item in items:
        seen = names_by_doc.setdefault(item.doc, {})
        for span in item.spans:
            seen.setdefault(span.name)
    return [(SpineRef(path=doc, cut="file"), tuple(seen)) for doc, seen in names_by_doc.items()]


def surface_counts(items: Sequence[ItemEntities]) -> tuple[SurfaceCount, ...]:
    """Distinct (name, label) pairs with mention counts, first-appearance order."""
    counts: dict[tuple[str, str], int] = {}
    for item in items:
        for span in item.spans:
            key = (span.name, span.label)
            counts[key] = counts.get(key, 0) + 1
    return tuple(
        SurfaceCount(name=name, label=label, count=count) for (name, label), count in counts.items()
    )


def fold_surfaces(
    counts: Sequence[SurfaceCount],
    vectors: Mapping[str, tuple[float, ...]],
    *,
    threshold: float = FOLD_THRESHOLD,
    should_stop: Callable[[], bool] | None = None,
    progress: Callable[[], None] | None = None,
) -> dict[str, str]:
    """Surface name → canonical name, label-scoped, two rungs.

    Rung 1 is free: names equal under ``casefold`` collapse onto the most
    frequent spelling. Rung 2 reuses ``distinct``'s leader-cluster machinery:
    representatives with a vector fold onto the most frequent member of their
    cluster. Names without a vector (or when ``vectors`` is empty — the
    embedder was unavailable) keep their own node.

    ``should_stop``/``progress`` (B1) are injected effects the pure fold reports
    through: on a stop request the remaining label groups are left unclustered —
    each keeps its own node, the same graceful degradation an absent embedder
    gives — so the returned map is always a clean partial, never a crash.
    """
    representative: dict[str, str] = {}
    representatives: list[SurfaceCount] = []
    groups: dict[tuple[str, str], list[SurfaceCount]] = {}
    for surface in counts:
        groups.setdefault((surface.label, surface.name.casefold()), []).append(surface)
    for (label, _), members in groups.items():
        leader = max(members, key=lambda member: member.count)  # ties keep the first seen
        representatives.append(
            SurfaceCount(name=leader.name, label=label, count=sum(m.count for m in members))
        )
        for member in members:
            representative[member.name] = leader.name

    canonical: dict[str, str] = {}
    by_label: dict[str, list[SurfaceCount]] = {}
    for surface in representatives:
        by_label.setdefault(surface.label, []).append(surface)
    for members in by_label.values():
        if should_stop is not None and should_stop():
            break  # leave the rest unclustered — each keeps its own node (clean partial)
        embedded = sorted(
            (member for member in members if member.name in vectors),
            key=lambda member: -member.count,  # stable: ties keep first-appearance order
        )
        clusters = leader_clusters(
            [tuple(vectors[member.name]) for member in embedded], threshold=threshold
        )
        for cluster in clusters:
            leader = embedded[cluster[0]].name
            for position in cluster:
                canonical[embedded[position].name] = leader
        if progress is not None:
            progress()

    return {surface: canonical.get(leader, leader) for surface, leader in representative.items()}


def fold_stats(canonical: Mapping[str, str]) -> tuple[int, int]:
    """(names folded, nodes they folded into): surfaces in multi-member fold
    groups, and the count of those groups — the disclosure numbers."""
    members: dict[str, int] = {}
    for leader in canonical.values():
        members[leader] = members.get(leader, 0) + 1
    folded_groups = [size for size in members.values() if size > 1]
    return sum(folded_groups), len(folded_groups)


def build_nodes(
    counts: Sequence[SurfaceCount], canonical: Mapping[str, str]
) -> tuple[GraphNode, ...]:
    """One node per canonical name: mentions summed, majority label, sorted."""
    totals: dict[str, int] = {}
    label_votes: dict[str, dict[str, int]] = {}
    for surface in counts:
        name = canonical.get(surface.name, surface.name)
        totals[name] = totals.get(name, 0) + surface.count
        votes = label_votes.setdefault(name, {})
        votes[surface.label] = votes.get(surface.label, 0) + surface.count
    return tuple(
        GraphNode(
            name=name,
            label=max(label_votes[name].items(), key=lambda vote: vote[1])[0],
            count=total,
        )
        for name, total in sorted(totals.items())
    )


def fold_edges(
    entity_windows: Sequence[EntityWindow],
    canonical: Mapping[str, str],
    *,
    relation: str = "co-occurs",
    cap: int = _SOURCE_CAP,
    should_stop: Callable[[], bool] | None = None,
    progress: Callable[[], None] | None = None,
) -> tuple[GraphEdge, ...]:
    """Undirected co-occurrence edges: canonical order-independent pairs,
    weight = windows containing both, provenance deduped and capped.

    ``should_stop``/``progress`` (B1) are injected effects the pure fold reports
    through: this trio is quadratic in the entities per window and can dominate a
    large-corpus run, so it is checked once per window. A stop request breaks
    cleanly and folds every edge seen so far into the result (the caller salvages,
    mirroring A1)."""
    weights: dict[tuple[str, str], int] = {}
    kept_refs: dict[tuple[str, str], list[SpineRef]] = {}
    seen_refs: dict[tuple[str, str], set[SpineRef]] = {}
    hidden: dict[tuple[str, str], int] = {}
    for window in entity_windows:
        if should_stop is not None and should_stop():
            break  # salvage what folded so far — the caller writes the partial graph
        present = dict.fromkeys(canonical.get(name, name) for name in window.names)
        for pair in combinations(sorted(present), 2):
            weights[pair] = weights.get(pair, 0) + 1
            seen = seen_refs.setdefault(pair, set())
            if window.ref in seen:
                continue
            seen.add(window.ref)
            kept = kept_refs.setdefault(pair, [])
            if len(kept) < cap:
                kept.append(window.ref)
            else:
                hidden[pair] = hidden.get(pair, 0) + 1
        if progress is not None:
            progress()
    edges = [
        GraphEdge(
            source=source,
            target=target,
            relation=relation,
            weight=weight,
            sources=tuple(kept_refs[source, target]),
            hidden_sources=hidden.get((source, target), 0),
        )
        for (source, target), weight in weights.items()
    ]
    return tuple(sorted(edges, key=lambda edge: (-edge.weight, edge.source, edge.target)))


def prune_edges(edges: Sequence[GraphEdge], min_weight: int) -> tuple[tuple[GraphEdge, ...], int]:
    """Edges at or above the dial, plus how many fell below it (the receipt)."""
    kept = tuple(edge for edge in edges if edge.weight >= min_weight)
    return kept, len(edges) - len(kept)


@dataclass(frozen=True, slots=True)
class EdgeAssertion:
    """One asserted directed edge (wave G2): a full-mode extraction triple —
    (subject, relation, object) as (source, relation, target) — or an adopted
    pipe-in edge record, with its provenance refs and an adoptable weight."""

    refs: tuple[SpineRef, ...]
    source: str
    relation: str
    target: str
    source_label: str = "entity"
    target_label: str = "entity"
    weight: int = 1


def assertion_surface_counts(assertions: Sequence[EdgeAssertion]) -> tuple[SurfaceCount, ...]:
    """Distinct (name, label) pairs across both endpoints, weighted mention
    counts, first-appearance order — the assertion twin of ``surface_counts``."""
    counts: dict[tuple[str, str], int] = {}
    for assertion in assertions:
        for name, label in (
            (assertion.source, assertion.source_label),
            (assertion.target, assertion.target_label),
        ):
            key = (name, label)
            counts[key] = counts.get(key, 0) + assertion.weight
    return tuple(
        SurfaceCount(name=name, label=label, count=count) for (name, label), count in counts.items()
    )


def fold_assertions(
    assertions: Sequence[EdgeAssertion],
    canonical: Mapping[str, str],
    *,
    cap: int = _SOURCE_CAP,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[GraphEdge, ...]:
    """Directed, relation-keyed edges: weights sum per canonical
    (source, relation, target) fold key, provenance refs dedupe and cap,
    heaviest first. A pair folding onto one node is a self-loop — dropped.

    ``should_stop`` (B1 review) is the same injected stop the other two trio
    members poll: this one runs under ``to_thread`` too, so without it a Ctrl-C
    here could only escape via the watchdog hard-exit. A stop request breaks
    cleanly and folds every assertion seen so far into the result (clean partial)."""
    weights: dict[tuple[str, str, str], int] = {}
    kept_refs: dict[tuple[str, str, str], list[SpineRef]] = {}
    seen_refs: dict[tuple[str, str, str], set[SpineRef]] = {}
    hidden: dict[tuple[str, str, str], int] = {}
    for assertion in assertions:
        if should_stop is not None and should_stop():
            break  # salvage what folded so far — the caller writes the partial graph
        source = canonical.get(assertion.source, assertion.source)
        target = canonical.get(assertion.target, assertion.target)
        if source == target:
            continue
        key = (source, assertion.relation, target)
        weights[key] = weights.get(key, 0) + assertion.weight
        seen = seen_refs.setdefault(key, set())
        for ref in assertion.refs:
            if ref in seen:
                continue
            seen.add(ref)
            kept = kept_refs.setdefault(key, [])
            if len(kept) < cap:
                kept.append(ref)
            else:
                hidden[key] = hidden.get(key, 0) + 1
    edges = [
        GraphEdge(
            source=source,
            target=target,
            relation=relation,
            weight=weight,
            sources=tuple(kept_refs[source, relation, target]),
            hidden_sources=hidden.get((source, relation, target), 0),
        )
        for (source, relation, target), weight in weights.items()
    ]
    return tuple(
        sorted(edges, key=lambda edge: (-edge.weight, edge.source, edge.relation, edge.target))
    )


def name_edges(
    edges: Sequence[GraphEdge], names: Mapping[tuple[str, str], str]
) -> tuple[GraphEdge, ...]:
    """Hybrid naming (wave G2): a named relation replaces its edge's label in
    place — same fold key (source, target), same weight, same provenance."""
    from dataclasses import replace

    return tuple(
        replace(edge, relation=names.get((edge.source, edge.target), edge.relation))
        for edge in edges
    )


def human_ref(ref: SpineRef) -> str:
    """The ref as citation wording: an adopted label verbatim, else path+locator."""
    if ref.label is not None:
        return ref.label
    if ref.position is None:
        return ref.path
    match ref.cut:
        case "lines" | "jsonl" | "csv":
            return f"{ref.path}:{ref.position}"
        case "pages":
            return f"{ref.path} p.{ref.position}"
        case "file":
            return ref.path
        case _:  # tokens / minutes / seconds / future cuts
            return f"{ref.path} §{ref.position}"


def spine_record(ref: SpineRef) -> dict[str, object]:
    """The ref as a ``__source``-shaped record — what the JSONL edges carry."""
    record: dict[str, object] = {"path": ref.path, "as": ref.cut}
    if ref.position is not None:
        match ref.cut:
            case "lines" | "jsonl" | "csv":
                record["line"] = ref.position
            case "pages":
                record["page"] = ref.position
            case "file":
                pass
            case _:
                record["segment"] = ref.position
    if ref.label is not None:
        record["label"] = ref.label
    return record


def spine_from_record(record: Mapping[str, object]) -> SpineRef | None:
    """The reading half of ``spine_record`` (wave G2): an adopted edge row's
    ``sources`` entries become refs again. Untrusted JSON — anything that
    isn't ref-shaped is ``None``, never a guess."""
    path = record.get("path")
    if not isinstance(path, str) or not path:
        return None
    cut = record.get("as")
    label = record.get("label")
    position = next(
        (value for key in ("line", "page", "segment") if isinstance(value := record.get(key), int)),
        None,
    )
    return SpineRef(
        path=path,
        cut=cut if isinstance(cut, str) else "lines",
        position=position,
        label=label if isinstance(label, str) else None,
    )
