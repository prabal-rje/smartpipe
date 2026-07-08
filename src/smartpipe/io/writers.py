"""TTY-adaptive result writers — the only module that writes to stdout.

Two vocabularies on purpose: ``OutputFormat`` is what users say (``--output`` /
``SMARTPIPE_OUTPUT``); ``RenderMode`` is what a writer does. ``resolve_format``
maps one to the other using the TTY matrix in plan/ux.md — notably, AUTO on a
terminal renders structured results as a human view, while an *explicit*
``--output json`` forces NDJSON even there (spec §5.2).
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, assert_never

from smartpipe.core.errors import UsageFault
from smartpipe.core.jsontools import as_record
from smartpipe.io import diagnostics
from smartpipe.io.text import clip_to_width, display_width

if TYPE_CHECKING:
    from smartpipe.io.items import Item

_TRAILING_COLUMNS = ("_score", "_rank")  # ranking metadata sorts to the right of the sheet

__all__ = [
    "OutputFormat",
    "RenderMode",
    "ResultWriter",
    "TextSink",
    "WriterConfig",
    "make_writer",
    "resolve_format",
]


class TextSink(Protocol):
    """The slice of a text stream writers actually use — write and flush.

    A structural type so the progress arbiter can wrap stdout (pausing the
    status line around each result write) without impersonating a full TextIO.
    """

    def write(self, s: str, /) -> int: ...
    def flush(self) -> None: ...


_DIM = "\x1b[2m"
_RESET = "\x1b[0m"
_ELLIPSIS = "…"


class OutputFormat(StrEnum):
    AUTO = "auto"
    TEXT = "text"
    JSON = "json"
    CSV = "csv"
    TSV = "tsv"


class RenderMode(StrEnum):
    TEXT = "text"
    NDJSON = "ndjson"
    HUMAN = "human"
    CSV = "csv"
    TSV = "tsv"


@dataclass(frozen=True, slots=True)
class WriterConfig:
    mode: RenderMode
    color: bool
    width: int
    fields: tuple[str, ...] | None = None  # honored from stage 9


class ResultWriter(Protocol):
    def write_text(self, line: str) -> None: ...
    def write_record(self, record: Mapping[str, object]) -> None: ...
    def write_passthrough(self, item: Item) -> None: ...
    def flush(self) -> None: ...


def resolve_format(
    flag: OutputFormat,
    env: Mapping[str, str],
    *,
    stdout_tty: bool,
    structured: bool,
    fields: tuple[str, ...] | None = None,
) -> RenderMode:
    if fields is not None and not structured:
        raise UsageFault(
            "--fields selects columns from structured output\n"
            "  This run produces plain text — there are no named fields to pick from.\n"
            '  Add braces to the prompt (e.g. "Extract {name, email}") or pass --schema.'
        )
    requested = flag
    if requested is OutputFormat.AUTO:
        env_value = env.get("SMARTPIPE_OUTPUT", "")
        if env_value:
            try:
                requested = OutputFormat(env_value)
            except ValueError:
                raise UsageFault(
                    f"SMARTPIPE_OUTPUT={env_value!r} isn't a format smartpipe knows\n"
                    "  valid values: auto, text, json, csv, tsv"
                ) from None
    match requested:
        case OutputFormat.AUTO:
            if structured:
                return RenderMode.HUMAN if stdout_tty else RenderMode.NDJSON
            return RenderMode.TEXT
        case OutputFormat.TEXT:
            return RenderMode.TEXT
        case OutputFormat.JSON:
            return RenderMode.NDJSON
        case OutputFormat.CSV:
            _require_structured(requested, structured=structured)
            return RenderMode.CSV
        case OutputFormat.TSV:
            _require_structured(requested, structured=structured)
            return RenderMode.TSV
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)


def _require_structured(fmt: OutputFormat, *, structured: bool) -> None:
    if not structured:
        raise UsageFault(
            f"--output {fmt.value} needs structured output — a table needs named columns\n"
            '  add braces to the prompt (e.g. "Extract {name, email}") or pass --schema'
        )


def make_writer(config: WriterConfig, stdout: TextSink) -> ResultWriter:
    match config.mode:
        case RenderMode.TEXT:
            return _TextWriter(stream=stdout, fields=config.fields)
        case RenderMode.NDJSON:
            return _NdjsonWriter(stream=stdout, fields=config.fields)
        case RenderMode.HUMAN:
            return _HumanWriter(
                stream=stdout, color=config.color, width=config.width, fields=config.fields
            )
        case RenderMode.CSV:
            return _TableWriter(stream=stdout, delimiter=",", fields=config.fields)
        case RenderMode.TSV:
            return _TableWriter(stream=stdout, delimiter="\t", fields=config.fields)
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


_ABSENT = object()  # sentinel: "no such field", distinct from a genuine null
_RAW_PREVIEW_CELLS = 70  # --keep-invalid TTY line: this much of the raw reply


def _is_invalid_row(record: Mapping[str, object]) -> bool:
    """A --keep-invalid marker row — projection and block rendering both step
    aside for these: the row IS the failure report, not extracted data."""
    return record.get("_invalid") is True


def _lookup(record: Mapping[str, object], name: str) -> object:
    """Exact key first; else a dotted walk into nested objects (join's left.x/right.x)."""
    if name in record:
        return record[name]
    current: object = record
    for part in name.split("."):
        narrowed = as_record(current)
        if narrowed is None or part not in narrowed:
            return _ABSENT
        current = narrowed[part]
    return current


def _warn_missing(record: Mapping[str, object], fields: tuple[str, ...], warned: set[str]) -> None:
    """The one-time heads-up per requested-but-absent field (plan/ux.md, --fields)."""
    for name in fields:
        if _lookup(record, name) is _ABSENT and name not in warned:
            diagnostics.warn(f"--fields: no field {name!r} in the results; emitting null")
            warned.add(name)


def _project(
    record: Mapping[str, object], fields: tuple[str, ...], warned: set[str]
) -> dict[str, object]:
    """Select + order the requested columns; absent ones become null (shape stays stable)."""
    _warn_missing(record, fields, warned)
    projected = {name: _lookup(record, name) for name in fields}
    return {name: (None if value is _ABSENT else value) for name, value in projected.items()}


@dataclass(frozen=True, slots=True)
class _TextWriter:
    stream: TextSink
    fields: tuple[str, ...] | None = None  # top_k routes structured records through TEXT
    warned: set[str] = field(default_factory=set[str])

    def write_text(self, line: str) -> None:
        self.stream.write(f"{line}\n")
        self.stream.flush()

    def write_record(self, record: Mapping[str, object]) -> None:
        if self.fields is not None and not _is_invalid_row(record):
            record = _project(record, self.fields, self.warned)
        self.write_text(_compact_json(dict(record)))

    def write_passthrough(self, item: Item) -> None:
        self.write_text(item.raw)

    def flush(self) -> None:
        self.stream.flush()


@dataclass(frozen=True, slots=True)
class _NdjsonWriter:
    stream: TextSink
    fields: tuple[str, ...] | None = None
    warned: set[str] = field(default_factory=set[str])

    def write_text(self, line: str) -> None:
        self.write_record({"result": line})

    def write_record(self, record: Mapping[str, object]) -> None:
        if self.fields is not None and not _is_invalid_row(record):
            record = _project(record, self.fields, self.warned)
        self.stream.write(f"{_compact_json(dict(record))}\n")
        self.stream.flush()

    def write_passthrough(self, item: Item) -> None:
        self.stream.write(f"{item.raw}\n")
        self.stream.flush()

    def flush(self) -> None:
        self.stream.flush()


class _TableWriter:
    """CSV/TSV — a rectangle is the contract. Columns are fixed by ``--fields`` or the
    first record; later records fill missing cells empty and drop surprise keys with a
    one-time warning. Nested values become compact JSON; TSV strips tabs/newlines."""

    def __init__(self, *, stream: TextSink, delimiter: str, fields: tuple[str, ...] | None) -> None:
        self.stream = stream
        self.delimiter = delimiter
        self.fields = fields
        self.columns: tuple[str, ...] | None = None
        self.warned: set[str] = set()
        self.tsv_cleaned = False
        # excel dialect gives RFC 4180 quoting + CRLF; TSV mirrors it with a tab delimiter
        self.csv = csv.writer(stream, dialect="excel", delimiter=delimiter)

    def write_text(self, line: str) -> None:
        # csv is guarded to structured output, but stay valid if a plain result slips in
        self.write_record({"result": line})

    def write_record(self, record: Mapping[str, object]) -> None:
        if self.columns is None:
            self.columns = self._header(record)
            self.csv.writerow(self.columns)
        if self.fields is not None:
            # explicit projection: dropping extras is the point, absence gets the shared warning
            _warn_missing(record, self.fields, self.warned)
        else:
            for key in record:
                if key not in self.columns and key not in self.warned:
                    diagnostics.warn(
                        f"column {key!r} appeared after the header was fixed; "
                        "use --fields to pin columns"
                    )
                    self.warned.add(key)
        cells = (_lookup(record, column) for column in self.columns)
        self.csv.writerow([self._cell(None if value is _ABSENT else value) for value in cells])
        self.stream.flush()

    def write_passthrough(self, item: Item) -> None:  # pragma: no cover — csv is structured-only
        self.write_text(item.raw)

    def flush(self) -> None:
        self.stream.flush()

    def _header(self, record: Mapping[str, object]) -> tuple[str, ...]:
        if self.fields is not None:
            return self.fields
        body = [key for key in record if key not in _TRAILING_COLUMNS]
        trailing = [key for key in _TRAILING_COLUMNS if key in record]
        return (*body, *trailing)

    def _cell(self, value: object) -> str:
        text = _scalar(value)
        if self.delimiter == "\t" and any(ch in text for ch in "\t\n\r"):
            if not self.tsv_cleaned:
                diagnostics.warn("replaced tabs/newlines in TSV cells with spaces")
                self.tsv_cleaned = True
            text = text.replace("\t", " ").replace("\n", " ").replace("\r", " ")
        return text


def _scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return _compact_json(value)  # objects/arrays → compact JSON in one cell


@dataclass(frozen=True, slots=True)
class _HumanWriter:
    """Structured results as aligned key/value blocks — TTY reading, never parsing.

    Truncation to the terminal width happens here and only here: piped output
    (the other writers) is never truncated (spec §5.1).
    """

    stream: TextSink
    color: bool
    width: int
    fields: tuple[str, ...] | None = None
    warned: set[str] = field(default_factory=set[str])

    def write_text(self, line: str) -> None:
        self.stream.write(f"{line}\n")
        self.stream.flush()

    def write_record(self, record: Mapping[str, object]) -> None:
        if _is_invalid_row(record):
            self._write_invalid(record)
            return
        if self.fields is not None:
            record = _project(record, self.fields, self.warned)
        for key, value in record.items():
            # null shows as nothing — for humans an absent value reads best as blank
            rendered = (
                "" if value is None else value if isinstance(value, str) else _compact_json(value)
            )
            # budget in terminal cells, not code points (DEFER-2) — a Wide char is 2
            available = self.width - display_width(key) - 2
            if available >= 2 and display_width(rendered) > available:
                rendered = clip_to_width(rendered, available - 1) + _ELLIPSIS
            label = f"{_DIM}{key}:{_RESET}" if self.color else f"{key}:"
            self.stream.write(f"{label} {rendered}\n")
        self.stream.write("\n")
        self.stream.flush()

    def _write_invalid(self, record: Mapping[str, object]) -> None:
        """A --keep-invalid row at the terminal: one dim compact line — marker,
        the validator's complaint, the first ~70 cells of the raw reply. The
        full JSON row is a pipe thing; a human wants the gist, not the wreckage."""
        error = str(record.get("_error", ""))
        raw = " ".join(str(record.get("_raw", "")).split())  # flatten to one line
        if display_width(raw) > _RAW_PREVIEW_CELLS:
            raw = clip_to_width(raw, _RAW_PREVIEW_CELLS) + _ELLIPSIS
        line = f"✗ invalid: {error} · {raw}"
        if self.color:
            line = f"{_DIM}{line}{_RESET}"
        self.stream.write(f"{line}\n\n")
        self.stream.flush()

    def write_passthrough(self, item: Item) -> None:
        self.write_text(item.raw)

    def flush(self) -> None:
        self.stream.flush()
