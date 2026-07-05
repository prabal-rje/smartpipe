"""The Item model: the unit every verb operates on.

Contract (plan/architecture.md "Core types"): ``raw`` preserves the input line
byte-for-byte (minus the trailing newline) so ``filter``/``top_k`` can honor the
passthrough-fidelity guarantee; ``data`` is set only when the line is a JSON
*object* (an NDJSON record) — scalars and arrays are just text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeGuard

if TYPE_CHECKING:
    from collections.abc import Mapping

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


def item_from_line(line: str, index: int) -> Item:
    raw = line.removesuffix("\n").removesuffix("\r")
    if index == 0:
        raw = raw.removeprefix(_BOM)
    return Item(
        raw=raw,
        text=raw,
        data=_sniff_json_object(raw),
        source=ItemSource(kind="stdin", name="-", index=index),
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


def describe_source(source: ItemSource) -> str:
    """Human wording for warnings — 1-based lines, plain filenames."""
    if source.kind == "stdin":
        return f"line {source.index + 1}"
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
