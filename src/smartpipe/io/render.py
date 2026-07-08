"""YAML-ish block rendering — records for human EYES (wave 2, item 19).

One in-house renderer (no new dependency, a deliberately tame subset of YAML's
look) shared by the TTY preview writer and the ``readable`` verb: nested maps
indent, lists render as ``- ``, multi-line strings as block scalars (``|``).
DISPLAY ONLY — piped record output stays JSONL and never truncates. The ``__``
spine renders dimmed at the bottom of each block; ``__media`` never dumps
base64 — it renders as ``image/png (48 KB)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from smartpipe.core.jsontools import as_items, as_record

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["render_block"]

_DIM = "\x1b[2m"
_RESET = "\x1b[0m"
_STRING_CAP = 400  # characters shown before "… (+N chars)"
_LIST_CAP = 10  # items shown before "… (+N items)"


def render_block(record: Mapping[str, object], *, color: bool, full: bool) -> str:
    """One record as an indented block: payload fields first, the ``__`` spine
    dimmed at the bottom (shown, not hidden — the owner default)."""
    body = [(key, value) for key, value in record.items() if not key.startswith("__")]
    spine = [(key, value) for key, value in record.items() if key.startswith("__")]
    lines: list[str] = []
    for key, value in body:
        lines.extend(_field(key, value, indent=0, color=color, full=full, dim=False))
    for key, value in spine:
        lines.extend(_field(key, value, indent=0, color=color, full=full, dim=True))
    return "\n".join(lines)


def _field(
    key: str, value: object, *, indent: int, color: bool, full: bool, dim: bool
) -> list[str]:
    pad = " " * indent
    if key == "__media":
        return [_line(pad, key, _media_summary(value), color=color, dim=True)]
    if isinstance(value, str) and "\n" in value:
        text = _clip_string(value, color=color, full=full)
        head = _line(pad, key, "|", color=color, dim=dim)
        return [
            head,
            *(
                _dimmed(f"{pad}  {row}", color) if dim else f"{pad}  {row}"
                for row in text.split("\n")
            ),
        ]
    nested = as_record(value)
    if nested is not None:
        head = _line(pad, key, "", color=color, dim=dim)
        children = [
            row
            for child_key, child_value in nested.items()
            for row in _field(
                child_key, child_value, indent=indent + 2, color=color, full=full, dim=dim
            )
        ]
        return [head, *children]
    entries = as_items(value)
    if entries is not None:
        return _list_field(key, entries, pad=pad, indent=indent, color=color, full=full, dim=dim)
    return [_line(pad, key, _scalar(value, color=color, full=full), color=color, dim=dim)]


def _list_field(
    key: str,
    value: Sequence[object],
    *,
    pad: str,
    indent: int,
    color: bool,
    full: bool,
    dim: bool,
) -> list[str]:
    lines = [_line(pad, key, "", color=color, dim=dim)]
    shown = value if full or len(value) <= _LIST_CAP else value[:_LIST_CAP]
    for element in shown:
        nested = as_record(element)
        if nested is not None:
            inner = [
                row
                for child_key, child_value in nested.items()
                for row in _field(
                    child_key, child_value, indent=indent + 4, color=color, full=full, dim=dim
                )
            ]
            if inner:  # "- " replaces the first element line's leading indent
                lines.append(f"{pad}  - " + inner[0].lstrip(" "))
                lines.extend(inner[1:])
            continue
        row = f"{pad}  - {_scalar(element, color=color, full=full)}"
        lines.append(_dimmed(row, color) if dim else row)
    hidden = len(value) - len(shown)
    if hidden:
        lines.append(_dimmed(f"{pad}  … (+{hidden} items)", color))
    return lines


def _line(pad: str, key: str, rendered: str, *, color: bool, dim: bool) -> str:
    label = f"{key}:"
    if color:
        label = f"{_DIM}{label}{_RESET}"
    text = f"{pad}{label} {rendered}".rstrip()
    return _dimmed(text, color) if dim else text


def _dimmed(text: str, color: bool) -> str:
    return f"{_DIM}{text}{_RESET}" if color else text


def _scalar(value: object, *, color: bool, full: bool) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _clip_string(value, color=color, full=full)
    if isinstance(value, int | float):
        return str(value)
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _clip_string(value: str, *, color: bool, full: bool) -> str:
    if full or len(value) <= _STRING_CAP:
        return value
    hidden = len(value) - _STRING_CAP
    return f"{value[:_STRING_CAP]}{_dimmed(f'… (+{hidden} chars)', color)}"


def _media_summary(value: object) -> str:
    """``__media`` never dumps base64: mime + decoded size, per part."""
    from smartpipe.core.jsontools import as_items, as_record

    entries = as_items(value)
    parts = [as_record(entry) for entry in entries] if entries is not None else [as_record(value)]
    summaries: list[str] = []
    for part in parts:
        if part is None:
            continue
        mime = part.get("mime")
        encoded = part.get("data_b64")
        size = len(encoded) * 3 // 4 if isinstance(encoded, str) else 0
        summaries.append(f"{mime if isinstance(mime, str) else 'media'} ({_human_size(size)})")
    return " · ".join(summaries) if summaries else "media"


def _human_size(size: int) -> str:
    if size >= 1_048_576:
        return f"{size / 1_048_576:.1f} MB"
    if size >= 1024:
        return f"{size // 1024} KB"
    return f"{size} B"
