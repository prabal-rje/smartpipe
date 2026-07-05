"""TTY-adaptive result writers — the only module that writes to stdout.

Two vocabularies on purpose: ``OutputFormat`` is what users say (``--output`` /
``SEMPIPE_OUTPUT``); ``RenderMode`` is what a writer does. ``resolve_format``
maps one to the other using the TTY matrix in plan/ux.md — notably, AUTO on a
terminal renders structured results as a human view, while an *explicit*
``--output json`` forces NDJSON even there (spec §5.2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, assert_never

from sempipe.core.errors import UsageFault

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import TextIO

    from sempipe.io.items import Item

__all__ = [
    "OutputFormat",
    "RenderMode",
    "ResultWriter",
    "WriterConfig",
    "make_writer",
    "resolve_format",
]

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
) -> RenderMode:
    requested = flag
    if requested is OutputFormat.AUTO:
        env_value = env.get("SEMPIPE_OUTPUT", "")
        if env_value:
            try:
                requested = OutputFormat(env_value)
            except ValueError:
                raise UsageFault(
                    f"SEMPIPE_OUTPUT={env_value!r} isn't a format sempipe knows\n"
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
        case OutputFormat.CSV | OutputFormat.TSV:
            raise UsageFault("csv/tsv output arrives in v0.7")
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)


def make_writer(config: WriterConfig, stdout: TextIO) -> ResultWriter:
    match config.mode:
        case RenderMode.TEXT:
            return _TextWriter(stream=stdout)
        case RenderMode.NDJSON:
            return _NdjsonWriter(stream=stdout)
        case RenderMode.HUMAN:
            return _HumanWriter(stream=stdout, color=config.color, width=config.width)
        case RenderMode.CSV | RenderMode.TSV:
            raise UsageFault("csv/tsv output arrives in v0.7")
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class _TextWriter:
    stream: TextIO

    def write_text(self, line: str) -> None:
        self.stream.write(f"{line}\n")
        self.stream.flush()

    def write_record(self, record: Mapping[str, object]) -> None:
        self.write_text(_compact_json(dict(record)))

    def write_passthrough(self, item: Item) -> None:
        self.write_text(item.raw)

    def flush(self) -> None:
        self.stream.flush()


@dataclass(frozen=True, slots=True)
class _NdjsonWriter:
    stream: TextIO

    def write_text(self, line: str) -> None:
        self.write_record({"result": line})

    def write_record(self, record: Mapping[str, object]) -> None:
        self.stream.write(f"{_compact_json(dict(record))}\n")
        self.stream.flush()

    def write_passthrough(self, item: Item) -> None:
        self.stream.write(f"{item.raw}\n")
        self.stream.flush()

    def flush(self) -> None:
        self.stream.flush()


@dataclass(frozen=True, slots=True)
class _HumanWriter:
    """Structured results as aligned key/value blocks — TTY reading, never parsing.

    Truncation to the terminal width happens here and only here: piped output
    (the other writers) is never truncated (spec §5.1).
    """

    stream: TextIO
    color: bool
    width: int

    def write_text(self, line: str) -> None:
        self.stream.write(f"{line}\n")
        self.stream.flush()

    def write_record(self, record: Mapping[str, object]) -> None:
        for key, value in record.items():
            rendered = value if isinstance(value, str) else _compact_json(value)
            available = self.width - len(key) - 2
            if available >= 2 and len(rendered) > available:
                rendered = rendered[: available - 1] + _ELLIPSIS
            label = f"{_DIM}{key}:{_RESET}" if self.color else f"{key}:"
            self.stream.write(f"{label} {rendered}\n")
        self.stream.write("\n")
        self.stream.flush()

    def write_passthrough(self, item: Item) -> None:
        self.write_text(item.raw)

    def flush(self) -> None:
        self.stream.flush()
