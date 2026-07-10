"""The ``sort`` verb: order JSONL by a field (D38/10, KQL ``sort by``).

Free, whole-set (inherently), stable, passthrough-verbatim. Missing-field
rows always land last — in both directions — and are disclosed. A column
whose every value is an ISO date/datetime orders temporally (item 56 —
mixed date/datetime columns, offsets honored); any other column keeps the
number/string bands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode
from smartpipe.engine.fieldpath import MISSING, lookup
from smartpipe.engine.temporal import temporal_key
from smartpipe.io import diagnostics
from smartpipe.io.items import item_from_line

if TYPE_CHECKING:
    from typing import TextIO

__all__ = ["SortRequest", "run_sort"]


@dataclass(frozen=True, slots=True)
class SortRequest:
    by: str
    descending: bool = False


def run_sort(request: SortRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    entries: list[tuple[object, str]] = []
    missing: list[str] = []
    for index, line in enumerate(stdin):
        if not line.strip():
            continue
        item = item_from_line(line, index)
        # item 63: --by takes a field path; an exact flat column wins first
        found = lookup(item.data, request.by) if item.data is not None else MISSING
        value = None if found is MISSING else found
        if value is None:
            missing.append(item.raw)
            continue
        entries.append((value, item.raw))
    keyed = _keyed(entries, descending=request.descending)
    keyed.sort(key=lambda pair: pair[0])  # stable: ties keep input order
    for _sort_key, raw in keyed:
        stdout.write(raw + "\n")
    for raw in missing:  # always last, regardless of direction
        stdout.write(raw + "\n")
    if missing:
        diagnostics.note(f"sort: {len(missing):,} rows missing '{request.by}' placed last")
    return ExitCode.OK


def _keyed(
    entries: list[tuple[object, str]], *, descending: bool
) -> list[tuple[tuple[int, float, str], str]]:
    """Sort keys for the whole column: all-temporal columns order by epoch
    (item 56); anything mixed falls back to the per-value type bands."""
    epochs = [key for value, _raw in entries if (key := temporal_key(value)) is not None]
    if entries and len(epochs) == len(entries):
        return [
            ((0, -epoch if descending else epoch, ""), raw)
            for epoch, (_value, raw) in zip(epochs, entries, strict=True)
        ]
    return [(_key(value, descending=descending), raw) for value, raw in entries]


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
