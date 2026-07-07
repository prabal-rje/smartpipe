"""The Item model: the unit every verb operates on.

Contract (plan/architecture.md "Core types"): ``raw`` preserves the input line
byte-for-byte (minus the trailing newline) so ``filter``/``top_k`` can honor the
passthrough-fidelity guarantee; ``data`` is set only when the line is a JSON
*object* (an NDJSON record) — scalars and arrays are just text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeGuard

if TYPE_CHECKING:
    from smartpipe.models.base import MediaData

__all__ = ["Item", "ItemSource", "describe_source", "item_from_file", "item_from_line"]

_BOM = "﻿"


@dataclass(frozen=True, slots=True)
class ItemSource:
    kind: Literal["stdin", "file"]
    name: str  # "-" for stdin, else the path as given
    index: int  # 0-based line number (stdin) or file ordinal


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
    document's text isn't an NDJSON line). ``filter``/``top_k`` emit its path."""
    return Item(
        raw=text,
        text=text,
        data=None,
        source=ItemSource(kind="file", name=path, index=index),
    )


def _named_source(data: Mapping[str, object] | None, index: int) -> ItemSource:
    # split emits {"source": "call.mp3 §00:10-00:20"} — keep that provenance
    name = data.get("source") if data is not None else None
    return ItemSource(kind="stdin", name=name if isinstance(name, str) else "-", index=index)


def _sniff_media(data: Mapping[str, object] | None) -> tuple[MediaData, ...]:
    """``split`` ships media as base64 NDJSON (audio/video slices, figures, and
    multi-part page items) — rebuild the bytes so the next verb can hear or see
    them (D27/D32)."""
    if data is None:
        return ()
    from smartpipe.core.jsontools import as_items, as_record

    entries = as_items(data.get("parts"))
    if entries is not None:
        return tuple(
            part for entry in entries if (part := _one_media(as_record(entry))) is not None
        )
    single = _one_media(data)
    return (single,) if single is not None else ()


def _one_media(data: Mapping[str, object] | None) -> MediaData | None:
    if data is None:
        return None
    mime = data.get("mime")
    if not isinstance(mime, str):
        return None
    import base64
    import binascii

    from smartpipe.models.base import (  # runtime construction
        AudioData,
        ImageData,
        VideoData,
    )

    for key, build in (
        ("audio_b64", AudioData),
        ("image_b64", ImageData),
        ("video_b64", VideoData),
    ):
        encoded = data.get(key)
        if not isinstance(encoded, str):
            continue
        try:
            return build(base64.b64decode(encoded, validate=True), mime)
        except (binascii.Error, ValueError):
            return None  # not ours — treat as a plain JSON line
    return None


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
