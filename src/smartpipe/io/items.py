"""The Item model: the unit every verb operates on.

Contract (plan/architecture.md "Core types"): ``raw`` preserves the input line
byte-for-byte (minus the trailing newline) so ``filter``/``top_k`` can honor the
passthrough-fidelity guarantee; ``data`` is set only when the line is a JSON
*object* (a JSONL record) — scalars and arrays are just text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeGuard

if TYPE_CHECKING:
    from smartpipe.models.base import MediaData

__all__ = [
    "KNOWN_META",
    "Item",
    "ItemSource",
    "content_text",
    "describe_source",
    "item_from_file",
    "item_from_line",
    "item_record",
    "media_parts",
    "media_record",
    "project_content",
    "source_record",
]

_BOM = "﻿"

# The reserved double-underscore namespace (wave 2, item 12): tool metadata.
# KNOWN fields are adopted on ingestion (round-tripping); unknown `__` fields
# warn once per name and carry through untouched — user data owns at most one
# leading underscore, so a stray `__x` is worth a heads-up, never a hard error.
KNOWN_META = frozenset(
    {
        "__source",
        "__sources",
        "__media",
        "__score",
        "__rank",
        "__snapshot",
        "__distance",
        "__invalid",
        "__error",
        "__raw",
        "__embedder",
    }
)

_warned_meta: set[str] = set()  # once per field name per process, like the degradation cap


@dataclass(frozen=True, slots=True)
class ItemSource:
    kind: Literal["stdin", "file"]
    name: str  # "-" for stdin, else the path (or an adopted human label)
    index: int  # 0-based line number (stdin) or file/segment ordinal
    cut: str = "lines"  # how the item was cut: lines|jsonl|csv|file|tokens|pages|minutes|seconds
    path: str | None = None  # the origin path when `name` carries a human label
    label: str | None = None  # adopted human wording ("report.pdf §3/12")


def source_record(source: ItemSource) -> dict[str, object]:
    """The ``__source`` spine record (item 13): how the item was cut travels
    with it — ``{path, as, line|page|segment}`` plus an optional human label."""
    record: dict[str, object] = {"path": source.path or source.name, "as": source.cut}
    match source.cut:
        case "lines" | "jsonl" | "csv":
            # csv indexes are PHYSICAL lines (header = 1) so grep/sed agree,
            # even when a quoted cell spans lines (then: the row's first line)
            record["line"] = source.index + 1
        case "pages":
            record["page"] = source.index + 1
        case "file":
            pass  # a whole file has no inner position
        case _:  # tokens / minutes / seconds / future cuts
            record["segment"] = source.index + 1
    if source.label is not None:
        record["label"] = source.label
    return record


@dataclass(frozen=True, slots=True)
class Item:
    raw: str  # the line/file EXACTLY as read (newline stripped)
    text: str  # model-facing content (== raw for lines; extracted text for files)
    data: Mapping[str, object] | None  # parsed object if the line was a JSON object
    source: ItemSource
    media: tuple[MediaData, ...] = ()  # media parts (D32) — text plus any number of figures/clips


def item_from_line(line: str, index: int) -> Item:
    raw = line.removesuffix("\n").removesuffix("\r")
    if index == 0:
        raw = raw.removeprefix(_BOM)
    data = _sniff_json_object(raw)
    _warn_unknown_meta(data)
    media = _sniff_media(data)
    text = raw
    if media and data is not None:
        # a media-carrying record's text is its "text" field, not the raw JSON
        carried = data.get("text")
        text = carried if isinstance(carried, str) else ""
    return Item(
        raw=raw,
        text=text,
        data=data,
        source=_named_source(data, index),
        media=media,
    )


def item_from_file(text: str, path: str, index: int) -> Item:
    """A whole file is one item: its extracted text, with no JSON sniffing (a
    document's text isn't a JSONL line). ``filter``/``top_k`` emit its path."""
    return Item(
        raw=text,
        text=text,
        data=None,
        source=ItemSource(kind="file", name=path, index=index, cut="file"),
    )


def _named_source(data: Mapping[str, object] | None, index: int) -> ItemSource:
    """The line's provenance: an incoming ``__source`` record is ADOPTED (the
    round-trip half of item 13 — split's cut survives the pipe); otherwise the
    line is a fresh lines/jsonl cut at this position."""
    if data is None:
        return ItemSource(kind="stdin", name="-", index=index, cut="lines")
    from smartpipe.core.jsontools import as_record

    carried = as_record(data.get("__source"))
    if carried is None:
        legacy = data.get("source")
        if isinstance(legacy, str) and "vector" in data:
            # a pre-1.5 embed row ({"text", "vector", "source"}) — read the old
            # field for one release; `vector` fences off ordinary user data
            return ItemSource(kind="stdin", name=legacy, index=index, cut="jsonl", path=legacy)
        return ItemSource(kind="stdin", name="-", index=index, cut="jsonl")
    path = carried.get("path")
    cut = carried.get("as")
    label = carried.get("label")
    position = next(
        (
            value
            for key in ("line", "page", "segment")
            if isinstance(value := carried.get(key), int)
        ),
        None,
    )
    named_path = path if isinstance(path, str) else "-"
    named_label = label if isinstance(label, str) else None
    return ItemSource(
        kind="stdin",
        name=named_label or named_path,
        index=position - 1 if position is not None else index,
        cut=cut if isinstance(cut, str) else "jsonl",
        path=named_path,
        label=named_label,
    )


def _warn_unknown_meta(data: Mapping[str, object] | None) -> None:
    """Unknown `__` fields warn once per name and carry through (item 12)."""
    if data is None:
        return
    from smartpipe.io import diagnostics

    for key in data:
        if key.startswith("__") and key not in KNOWN_META and key not in _warned_meta:
            _warned_meta.add(key)
            diagnostics.warn(
                f"unknown {key!r} field carried through "
                "(double-underscore fields are reserved for smartpipe metadata)"
            )


def _sniff_media(data: Mapping[str, object] | None) -> tuple[MediaData, ...]:
    """``split`` ships media under the ``__media`` spine field (item 12): one
    ``{kind, mime, data_b64}`` object, or a list of them for multi-part page
    items — rebuild the bytes so the next verb can hear or see them (D27/D32)."""
    if data is None:
        return ()
    from smartpipe.core.jsontools import as_items, as_record

    carried = data.get("__media")
    if carried is None:
        return ()
    entries = as_items(carried)
    if entries is not None:
        return tuple(
            part for entry in entries if (part := _one_media(as_record(entry))) is not None
        )
    single = _one_media(as_record(carried))
    return (single,) if single is not None else ()


_MEDIA_KINDS = ("audio", "image", "video")


def _one_media(data: Mapping[str, object] | None) -> MediaData | None:
    if data is None:
        return None
    mime = data.get("mime")
    kind = data.get("kind")
    encoded = data.get("data_b64")
    if not (isinstance(mime, str) and isinstance(encoded, str) and kind in _MEDIA_KINDS):
        return None
    import base64
    import binascii

    from smartpipe.models.base import (  # runtime construction
        AudioData,
        ImageData,
        VideoData,
    )

    build = {"audio": AudioData, "image": ImageData, "video": VideoData}[str(kind)]
    try:
        return build(base64.b64decode(encoded, validate=True), mime)
    except (binascii.Error, ValueError):
        return None  # not ours — treat as a plain JSON line


def media_parts(record: Mapping[str, object]) -> tuple[MediaData, ...]:
    """The decoded ``__media`` parts of a record — the reading half of the
    spine transport (``media_record`` writes it). Empty when the record
    carries none, or when the payload isn't ours."""
    return _sniff_media(record)


def media_record(part: MediaData) -> dict[str, object]:
    """One media part as its ``__media`` spine object: {kind, mime, data_b64}."""
    import base64

    kind = type(part).__name__.removesuffix("Data").lower()
    return {
        "kind": kind,
        "mime": part.mime,
        "data_b64": base64.b64encode(part.data).decode("ascii"),
    }


def item_record(item: Item) -> dict[str, object]:
    """The record an item IS (laws 1-2): its own fields, or ``{"text": …}`` for
    plain text, with media and provenance riding the ``__`` spine."""
    record: dict[str, object] = dict(item.data) if item.data is not None else {"text": item.text}
    if item.media and "__media" not in record:
        parts = [media_record(part) for part in item.media]
        record["__media"] = parts[0] if len(parts) == 1 else parts
    record["__source"] = source_record(item.source)
    return record


def content_text(item: Item) -> str:
    """The item's meaningful text — the text-projection rule every verb shares.

    Plain items ARE their text. A record's meaning is its content fields: a
    pure ``{"text": …}`` record projects to that string (so a reader-fed text
    row embeds identically to the raw line — deliverable 4's pin); a record
    that never carried a ``__`` spine keeps its raw line byte-identical
    (today's behavior); a spined record re-serializes its content fields with
    the spine stripped — tool metadata must never reach a model.
    """
    if item.data is None:
        return item.text
    content = {key: value for key, value in item.data.items() if not key.startswith("__")}
    carried = content.get("text")
    if set(content) == {"text"} and isinstance(carried, str):
        return carried
    if len(content) == len(item.data):
        return item.raw  # no spine rode along — nothing to strip
    return json.dumps(content, ensure_ascii=False)


def project_content(item: Item) -> Item:
    """The item with its text projected by ``content_text`` — a no-op for
    plain items and for media records (whose text is already the projection)."""
    if item.data is None or item.media:
        return item
    from dataclasses import replace

    projected = content_text(item)
    return item if projected == item.text else replace(item, text=projected)


def describe_source(source: ItemSource) -> str:
    """Human wording for warnings — 1-based lines, plain filenames; a split
    stage's provenance (``call.wav §00:10-00:20``) survives the pipe."""
    if source.kind == "stdin" and source.name == "-":
        return f"line {source.index + 1}"
    if source.kind == "stdin":
        return source.name
    return source.name


def _sniff_json_object(raw: str) -> Mapping[str, object] | None:
    candidate = raw.lstrip()
    if not candidate.startswith("{"):
        return None
    try:
        parsed: object = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not _is_json_object(parsed):  # pragma: no cover — a parsed "{…}" is always an object
        return None
    return dict(parsed)


def _is_json_object(value: object) -> TypeGuard[Mapping[str, object]]:
    """Sound claim: ``json.loads`` produces ``str`` keys by contract."""
    return isinstance(value, dict)
