"""The ``sort`` verb: order NDJSON by a field (D38/10, KQL ``sort by``).

Free, whole-set (inherently), stable, passthrough-verbatim. Missing-field
rows always land last — in both directions — and are disclosed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sempipe.core.errors import ExitCode
from sempipe.io import diagnostics
from sempipe.io.items import item_from_line

if TYPE_CHECKING:
    from typing import TextIO

__all__ = ["SortRequest", "run_sort"]


@dataclass(frozen=True, slots=True)
class SortRequest:
    by: str
    descending: bool = False


def run_sort(request: SortRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    keyed: list[tuple[tuple[int, float, str], str]] = []
    missing: list[str] = []
    for index, line in enumerate(stdin):
        if not line.strip():
            continue
        item = item_from_line(line, index)
        value = item.data.get(request.by) if item.data is not None else None
        if value is None:
            missing.append(item.raw)
            continue
        keyed.append((_key(value, descending=request.descending), item.raw))
    keyed.sort(key=lambda pair: pair[0])  # stable: ties keep input order
    for _sort_key, raw in keyed:
        stdout.write(raw + "\n")
    for raw in missing:  # always last, regardless of direction
        stdout.write(raw + "\n")
    if missing:
        diagnostics.note(f"sort: {len(missing):,} rows missing '{request.by}' placed last")
    return ExitCode.OK


def _key(value: object, *, descending: bool) -> tuple[int, float, str]:
    """Numbers first (numerically), then strings (lexically), then the rest
    (stringified). Descending flips within each band — bands never mix."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        text = value if isinstance(value, str) else str(value)
        band = 1 if isinstance(value, str) else 2
        return (band, 0.0, _flip_text(text) if descending else text)
    number = float(value)
    return (0, -number if descending else number, "")


def _flip_text(text: str) -> str:
    # descending lexicographic via per-character complement (stable, pure)
    return "".join(chr(0x10FFFF - ord(char)) for char in text)
