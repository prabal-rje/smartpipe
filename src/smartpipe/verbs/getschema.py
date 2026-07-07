"""The ``getschema`` verb: what's in this stream (D38/09, KQL ``getschema``).

Everyone's first 30 seconds with a new file, answered for free: fields,
types, coverage, an example each — and what to try next.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode
from smartpipe.io import diagnostics, tty
from smartpipe.io.items import item_from_line

if TYPE_CHECKING:
    from typing import TextIO

__all__ = ["GetSchemaRequest", "run_getschema"]

_SCAN_CAP = 10_000
_EXAMPLE_WIDTH = 24


@dataclass(frozen=True, slots=True)
class GetSchemaRequest:
    scan_all: bool = False


@dataclass(slots=True)
class _FieldState:
    types: set[str] = field(default_factory=set[str])
    non_null: int = 0
    example: str | None = None


def run_getschema(request: GetSchemaRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    fields: dict[str, _FieldState] = {}
    scanned = 0
    plain_lengths: list[int] = []
    capped = False
    for index, line in enumerate(stdin):
        if not line.strip():
            continue
        if not request.scan_all and scanned >= _SCAN_CAP:
            capped = True
            break
        scanned += 1
        item = item_from_line(line, index)
        if item.data is None:
            plain_lengths.append(len(item.text))
            continue
        for name, value in item.data.items():
            state = fields.setdefault(name, _FieldState())
            state.types.add(_type_name(value))
            if value is not None:
                state.non_null += 1
                if state.example is None:
                    state.example = _render_example(value)

    if not fields:
        lengths = sorted(plain_lengths)
        median = lengths[len(lengths) // 2] if lengths else 0
        stdout.write(
            f"plain text lines (no fields) — {scanned:,} lines · median {median:,} chars\n"
        )
        diagnostics.note('try: smartpipe map "Extract {label}" · smartpipe cluster')
        return ExitCode.OK

    rows = [
        {
            "field": name,
            "type": "|".join(sorted(state.types - {"null"}) or ["null"]),
            "coverage": f"{round(100 * state.non_null / scanned)}%",
            "example": state.example if state.example is not None else "",
        }
        for name, state in fields.items()
    ]
    if tty.stdout_is_tty():  # pragma: no cover — piped in tests; the table is trivial
        widths = {
            key: max(len(key), *(len(str(row[key])) for row in rows))
            for key in ("field", "type", "coverage")
        }
        from smartpipe.cli.screens import heading

        header = (
            f"{'field'.ljust(widths['field'])}  {'type'.ljust(widths['type'])}  "
            f"{'coverage'.ljust(widths['coverage'])}  example"
        )
        stdout.write(heading(header) + "\n")
        for row in rows:
            stdout.write(
                f"{str(row['field']).ljust(widths['field'])}  "
                f"{str(row['type']).ljust(widths['type'])}  "
                f"{str(row['coverage']).ljust(widths['coverage'])}  {row['example']}\n"
            )
    else:
        for row in rows:
            stdout.write(json.dumps(row, separators=(",", ":")) + "\n")
    if capped:
        diagnostics.note(f"getschema: first {_SCAN_CAP:,} rows — --all scans everything")
    best = max(
        (name for name in fields if name != "text"),
        key=lambda name: fields[name].non_null,
        default=None,
    )
    if best is not None:
        diagnostics.note(f"try: smartpipe chart {best} · smartpipe where '{best} …'")
    return ExitCode.OK


def _type_name(value: object) -> str:
    match value:
        case None:
            return "null"
        case bool():
            return "boolean"
        case int():
            return "integer"
        case float():
            return "number"
        case str():
            return "string"
        case list():
            return "array"
        case _:
            return "object"


def _render_example(value: object) -> str:
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(rendered) > _EXAMPLE_WIDTH:
        return rendered[: _EXAMPLE_WIDTH - 1] + "…"
    return rendered
