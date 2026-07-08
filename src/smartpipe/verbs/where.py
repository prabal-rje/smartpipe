"""The ``where`` verb: a free deterministic filter (D38/01, KQL ``where``).

The filter-early idiom given its missing operator: cut the corpus BEFORE any
paid stage touches it. Never calls a model; streams; passthrough-verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode
from smartpipe.engine.predicate import FieldTally, evaluate, parse_predicate
from smartpipe.io import diagnostics
from smartpipe.io.items import item_from_line

if TYPE_CHECKING:
    from typing import TextIO

__all__ = ["WhereRequest", "run_where"]

_ROLLUP_FIELDS = 3  # cap the closing disclosure at this many field names


@dataclass(frozen=True, slots=True)
class WhereRequest:
    predicate: str
    strict_rows: bool = False  # --strict-rows: field-less rows are an error (item 20)


def run_where(request: WhereRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    predicate = parse_predicate(request.predicate)  # UsageFault (64) before reading stdin
    tally = FieldTally()
    seen = 0
    matched = 0
    no_fields = 0
    for index, line in enumerate(stdin):
        if not line.strip():
            continue
        seen += 1
        item = item_from_line(line, index)
        if evaluate(predicate, item, tally):
            matched += 1
            stdout.write(item.raw + "\n")
        elif item.data is None:
            no_fields += 1  # a plain line the predicate couldn't hold (item 20)
    diagnostics.note(f"where: {matched:,} of {seen:,} matched")
    if no_fields:
        line_note = f"where: {no_fields:,} rows had no fields — treated as non-matching"
        if _strict(request.strict_rows):
            from smartpipe.core.errors import UsageFault

            raise UsageFault(f"{line_note}\n  --strict-rows demands records — feed JSONL rows.")
        diagnostics.note(line_note)
    for label, counter in (("missing", tally.missing), ("non-numeric", tally.non_numeric)):
        for field_name, count in counter.most_common(_ROLLUP_FIELDS):
            diagnostics.note(f"field '{field_name}' {label} on {count:,} rows")
    return ExitCode.OK  # zero matches is a valid result (filter's contract)


def _strict(flag: bool) -> bool:
    import os

    return flag or bool(os.environ.get("SMARTPIPE_STRICT_ROWS", "").strip())
