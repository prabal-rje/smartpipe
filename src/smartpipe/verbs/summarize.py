"""The ``summarize`` verb: deterministic aggregation (D38/07, KQL verbatim).

Free — the number the analyst actually came for, without leaving for awk.
One pass, grouped state, KQL's own output naming.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode
from smartpipe.engine.aggregate import GroupState, finish, fold, group_key, parse_summarize
from smartpipe.io import diagnostics
from smartpipe.io.items import item_from_line
from smartpipe.io.writers import RenderMode, WriterConfig, make_writer

if TYPE_CHECKING:
    from typing import TextIO

__all__ = ["SummarizeRequest", "run_summarize"]


@dataclass(frozen=True, slots=True)
class SummarizeRequest:
    expression: str
    strict_rows: bool = False  # --strict-rows: a row lacking a by-field is an error (item 20)


def run_summarize(request: SummarizeRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    plan = parse_summarize(request.expression)  # UsageFault (64) before reading stdin
    groups: dict[tuple[object, ...], GroupState] = {}
    order: list[tuple[object, ...]] = []
    lacking: dict[str, int] = {}
    for index, line in enumerate(stdin):
        if not line.strip():
            continue
        item = item_from_line(line, index)
        record = item.data if item.data is not None else {"text": item.text}
        for name in plan.by_names:
            if name not in record:
                lacking[name] = lacking.get(name, 0) + 1
        key = group_key(plan, record)
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
    for field_name, count in lacking.items():
        line_note = f"summarize: {count:,} rows lacked '{field_name}' — grouped as null"
        if _strict(request.strict_rows):
            from smartpipe.core.errors import UsageFault

            raise UsageFault(
                f"{line_note}\n  --strict-rows demands the field on every row — "
                "filter first: smartpipe where '<field> != \"\"'"
            )
        diagnostics.note(line_note)
    return ExitCode.OK


def _strict(flag: bool) -> bool:
    import os

    return flag or bool(os.environ.get("SMARTPIPE_STRICT_ROWS", "").strip())
