"""The ``agree`` verb (item 65b): inter-rater agreement between two label files.

Free - zero model calls, zero config. Reads two JSONL files, aligns rows (by
``--on`` key, or by row order), extracts the ``--label`` value from each side,
and emits observed agreement, Cohen's kappa, Krippendorff's alpha (nominal),
and the confusion matrix as structured records. All the math lives in the
pure ``engine/agreement`` module; this shell only reads files, writes records,
and discloses exclusions on stderr.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.engine.agreement import AgreementStats, Comparison, LabelFile, compare_labels
from smartpipe.io import diagnostics, tty
from smartpipe.io.tty import ColorMode
from smartpipe.io.writers import OutputFormat, WriterConfig, make_writer, resolve_format

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path
    from typing import TextIO

__all__ = ["AgreeRequest", "run_agree"]

_AGREE_FIELDS = (
    "n",
    "observed_agreement",
    "cohen_kappa",
    "krippendorff_alpha",
    "label_a",
    "label_b",
    "count",
)


@dataclass(frozen=True, slots=True)
class AgreeRequest:
    file_a: Path
    file_b: Path
    on: str | None = None  # --on FIELD: align by key; None = row order
    label: str = "label"  # --label FIELD: the compared value
    output: OutputFormat = OutputFormat.AUTO


def run_agree(request: AgreeRequest, *, stdout: TextIO) -> ExitCode:
    a = _read_label_file(request.file_a)
    b = _read_label_file(request.file_b)
    comparison = compare_labels(a, b, on=request.on, label=request.label)
    mode = resolve_format(
        request.output, os.environ, stdout_tty=tty.stdout_is_tty(), structured=True
    )
    writer = make_writer(
        WriterConfig(
            mode=mode,
            color=tty.stdout_supports_color(ColorMode.AUTO),
            width=tty.terminal_width(),
            fields=_AGREE_FIELDS,
        ),
        stdout,
    )
    stats = comparison.stats
    writer.write_record(_summary_record(stats))
    for label_a, label_b, count in stats.matrix:
        writer.write_record(_cell_record(label_a, label_b, count))
    writer.flush()
    _disclose(comparison, stats, a.name, b.name, request)
    return ExitCode.OK


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _summary_record(stats: AgreementStats) -> dict[str, object]:
    return {
        "n": stats.n,
        "observed_agreement": _rounded(stats.observed),
        "cohen_kappa": _rounded(stats.kappa),
        "krippendorff_alpha": _rounded(stats.alpha),
        "label_a": None,
        "label_b": None,
        "count": None,
    }


def _cell_record(label_a: str, label_b: str, count: int) -> dict[str, object]:
    return {
        "n": None,
        "observed_agreement": None,
        "cohen_kappa": None,
        "krippendorff_alpha": None,
        "label_a": label_a,
        "label_b": label_b,
        "count": count,
    }


def _disclose(
    comparison: Comparison, stats: AgreementStats, a: str, b: str, request: AgreeRequest
) -> None:
    """The stderr ledger: what was excluded and why, one note per concern."""
    if comparison.only_a or comparison.only_b:
        keys = "key" if comparison.only_a == 1 else "keys"
        diagnostics.note(
            f"agree: excluded from the stats - {comparison.only_a} {keys} only in {a}, "
            f"{comparison.only_b} only in {b}"
        )
    if comparison.missing_key_a or comparison.missing_key_b:
        diagnostics.note(
            f"agree: rows lacking '{request.on}' excluded - "
            f"{comparison.missing_key_a} in {a}, {comparison.missing_key_b} in {b}"
        )
    if comparison.unlabeled_a or comparison.unlabeled_b:
        diagnostics.note(
            f"agree: unlabeled rows excluded (missing/null '{request.label}') - "
            f"{comparison.unlabeled_a} in {a}, {comparison.unlabeled_b} in {b}"
        )
    if stats.kappa is None:
        diagnostics.note(
            "agree: kappa and alpha are undefined - only one label class observed "
            "(observed agreement is still exact)"
        )


def _read_label_file(path: Path) -> LabelFile:
    if not path.is_file():
        raise UsageFault(
            f"agree: no such file: {path}\n"
            "  agree compares two label files: smartpipe agree A.jsonl B.jsonl --on id"
        )
    return LabelFile(name=str(path), records=_records(path))


def _records(path: Path) -> Iterator[Mapping[str, object]]:
    """Strict JSONL, decoded one physical line at a time for exact faults."""
    line_number = 0
    try:
        with path.open("rb") as handle:
            for line_number, raw in enumerate(handle, start=1):
                if not raw.strip():
                    continue
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise UsageFault(
                        f"agree: invalid UTF-8 in {path}, line {line_number}\n"
                        "  Save the label file as UTF-8, then rerun agree."
                    ) from exc
                if line_number == 1:
                    line = line.removeprefix("\ufeff")
                try:
                    parsed: object = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise UsageFault(
                        f"agree: invalid record in {path}, line {line_number} ({exc.msg})\n"
                        "  Each nonblank line must be one JSON object."
                    ) from exc
                from smartpipe.core.jsontools import as_record

                record = as_record(parsed)
                if record is None:
                    raise UsageFault(
                        f"agree: invalid record in {path}, line {line_number} "
                        "(expected a JSON object)\n"
                        "  Each nonblank line must be one JSON object."
                    )
                yield record
    except OSError as exc:
        location = f", line {line_number}" if line_number else ""
        raise UsageFault(
            f"agree: could not read {path}{location} ({exc})\n"
            "  Check that the file is readable, then rerun agree."
        ) from exc
