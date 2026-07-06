"""The ``summarize`` verb: deterministic aggregation (D38/07, KQL verbatim).

Free — the number the analyst actually came for, without leaving for awk.
One pass, grouped state, KQL's own output naming.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sempipe.core.errors import ExitCode
from sempipe.engine.aggregate import GroupState, finish, fold, parse_summarize
from sempipe.io import diagnostics
from sempipe.io.items import item_from_line
from sempipe.io.writers import RenderMode, WriterConfig, make_writer

if TYPE_CHECKING:
    from typing import TextIO

__all__ = ["SummarizeRequest", "run_summarize"]


@dataclass(frozen=True, slots=True)
class SummarizeRequest:
    expression: str


def run_summarize(request: SummarizeRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    plan = parse_summarize(request.expression)  # UsageFault (64) before reading stdin
    groups: dict[tuple[object, ...], GroupState] = {}
    order: list[tuple[object, ...]] = []
    for index, line in enumerate(stdin):
        if not line.strip():
            continue
        item = item_from_line(line, index)
        record = item.data if item.data is not None else {"text": item.text}
        key = tuple(record.get(field) for field in plan.by)
        state = groups.get(key)
        if state is None:
            state = GroupState()
            groups[key] = state
            order.append(key)
        fold(plan, state, record)

    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=None), stdout
    )
    ranked = sorted(order, key=lambda key: (-groups[key].count, str(key)))
    for key in ranked:
        writer.write_record(finish(plan, key, groups[key]))
    writer.flush()

    skipped: dict[str, int] = {}
    for state in groups.values():
        for field_name, count in state.skipped_non_numeric.items():
            skipped[field_name] = skipped.get(field_name, 0) + count
    for field_name, count in skipped.items():
        diagnostics.note(f"summarize: skipped {count:,} non-numeric value(s) of '{field_name}'")
    return ExitCode.OK
