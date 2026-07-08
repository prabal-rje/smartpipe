"""The ``write`` verb (wave 2, item 17): the egress door — items → files.

The mirror of ingestion, driven by the ``__source`` spine: file-cut items (and
media items — reassembly is meaningless for bytes) each get their own file at
the template path, same-path collisions loudly refused; line/row/segment-cut
TEXT items append into their template path grouped by origin and ORDERED BY
their spine position, so concurrency upstream can never scramble reassembly.
``--as`` overrides the mirror; ``__`` fields are stripped unless
``--keep-meta``; the paths written go to stdout (one per line) so pipes
continue. Zero model calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.io import readers
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source

if TYPE_CHECKING:
    import asyncio
    from typing import TextIO

    from smartpipe.io.items import Item

__all__ = ["WriteRequest", "run_write"]

_RESERVED_VARS = ("path", "name", "stem", "ext", "index")


@dataclass(frozen=True, slots=True)
class WriteRequest:
    template: str  # 'out/{stem}.txt', 'by-lang/{lang}.jsonl', …
    keep_meta: bool = False  # retain __ fields in written rows
    field: str | None = None  # --field: write ONE field's value as raw text
    as_mode: str | None = None  # file|lines — overrides the __source mirror


async def run_write(
    request: WriteRequest,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    if request.as_mode not in (None, "file", "lines"):
        raise UsageFault(
            f"write --as takes file or lines, got {request.as_mode!r}\n"
            "  file = one file per item; lines = append rows into each target."
        )
    items_iter, _total = readers.resolve_items(STDIN, stdin, stop=stop)
    order: dict[str, None] = {}  # emit order: first touch wins
    singles: set[str] = set()  # one-file-per-item targets (collision guard)
    appends: dict[str, list[tuple[int, str]]] = {}  # target → (position, row)
    produced = 0
    async for item in items_iter:
        if stop is not None and stop.is_set():
            break
        target = _render_target(request.template, item)
        if _one_file_per_item(item, request.as_mode):
            if target in singles or target in appends:
                raise UsageFault(
                    f"write: {target!r} written twice — one file per item needs a "
                    "distinguishing template var\n"
                    "  Add {index} or {stem}: smartpipe write 'out/{stem}-{index}.png'"
                )
            _write_single(Path(target), _content(item, request))
            singles.add(target)
            order.setdefault(target)
        else:
            row = _row_text(item, request)
            appends.setdefault(target, []).append((item.source.index, row))
            order.setdefault(target)
        produced += 1
    for target, rows in appends.items():
        if target in singles:
            raise UsageFault(
                f"write: {target!r} takes both whole-file and appended items — "
                "give one of them its own template\n"
                "  Add {index} or {stem} so the paths can't collide."
            )
        rows.sort(key=lambda pair: pair[0])  # the spine position, never arrival order
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"{row}\n" for _position, row in rows), encoding="utf-8")
    for target in order:
        stdout.write(f"{target}\n")
    stdout.flush()
    return ExitCode.OK if produced else ExitCode.PARTIAL


def _one_file_per_item(item: Item, as_mode: str | None) -> bool:
    """The mirror rule: whole-file crates and media segments get their own
    file (reassembling bytes by append is meaningless); text rows append."""
    if as_mode == "file":
        return True
    if as_mode == "lines":
        return False
    return item.source.cut == "file" or bool(item.media)


def _render_target(template: str, item: Item) -> str:
    values = _template_vars(item)
    try:
        return template.format_map(values)
    except KeyError as exc:
        available = ", ".join(sorted(str(key) for key in values))
        raise UsageFault(
            f"write: {describe_source(item.source)} has no {exc.args[0]!r} for the template\n"
            f"  This row's template vars: {available}"
        ) from None


def _template_vars(item: Item) -> dict[str, object]:
    origin = item.source.path or item.source.name
    pure = PurePath(origin)
    fields: dict[str, object] = {}
    if item.data is not None:
        fields = {key: value for key, value in item.data.items() if not key.startswith("__")}
    # the reserved vars win — {name} is always the origin's basename
    fields.update(
        path=origin,
        name=pure.name,
        stem=pure.stem,
        ext=pure.suffix.lstrip("."),
        index=item.source.index + 1,
    )
    return fields


def _content(item: Item, request: WriteRequest) -> bytes | str:
    """A single file's whole content: media bytes, one field, raw text, or the
    record as one JSONL row."""
    if request.field is not None:
        return _field_text(item, request.field)
    if item.media and not _payload_fields(item):
        return item.media[0].data  # the crate IS the bytes
    return _row_text(item, request)


def _row_text(item: Item, request: WriteRequest) -> str:
    if request.field is not None:
        return _field_text(item, request.field)
    if item.data is None:
        return item.raw
    text_only = _text_only(item)
    if text_only is not None and not request.keep_meta:
        # law 5 at the write edge: a text-only record leaves as plain text —
        # the reader's lines round-trip byte-identically through the mirror
        return text_only
    return _record_row(item, keep_meta=request.keep_meta)


def _text_only(item: Item) -> str | None:
    """The record's text when text is ALL it carries (spine aside), else None."""
    if item.data is None:
        return None
    payload = {key for key in item.data if not key.startswith("__")}
    if payload != {"text"}:
        return None
    value = item.data.get("text")
    return value if isinstance(value, str) else None


def _payload_fields(item: Item) -> dict[str, object]:
    """Non-spine fields that carry actual data (an empty text tag doesn't)."""
    if item.data is None:
        return {}
    return {
        key: value
        for key, value in item.data.items()
        if not key.startswith("__") and not (key == "text" and value == "")
    }


def _record_row(item: Item, *, keep_meta: bool) -> str:
    assert item.data is not None
    record = (
        dict(item.data)
        if keep_meta
        else {key: value for key, value in item.data.items() if not key.startswith("__")}
    )
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def _field_text(item: Item, field: str) -> str:
    record = item.data if item.data is not None else {"text": item.text}
    value = record.get(field)
    if value is None:
        raise UsageFault(
            f"write --field {field}: {describe_source(item.source)} has no {field!r}\n"
            "  Every row must carry the field — filter or extend the stream first."
        )
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _write_single(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(f"{content}\n" if not content.endswith("\n") else content, encoding="utf-8")
