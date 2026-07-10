"""The ``agree`` verb (item 65b): inter-rater agreement between two label files.

Free - zero model calls, zero config. Reads two JSONL files, aligns rows (by
``--on`` key, or by row order), extracts the ``--label`` value from each side,
and emits observed agreement, Cohen's kappa, Krippendorff's alpha (nominal),
and the confusion matrix as structured records. All the math lives in the
pure ``engine/agreement`` module; this shell only reads files, writes records,
and discloses exclusions on stderr.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.engine.agreement import AgreementStats, Comparison, LabelFile, compare_labels
from smartpipe.io import diagnostics, tty
from smartpipe.io.items import item_from_line
from smartpipe.io.tty import ColorMode
from smartpipe.io.writers import OutputFormat, WriterConfig, make_writer, resolve_format

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

__all__ = ["AgreeRequest", "run_agree"]


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
        ),
        stdout,
    )
    stats = comparison.stats
    writer.write_record(
        {
            "n": stats.n,
            "observed_agreement": _rounded(stats.observed),
            "cohen_kappa": _rounded(stats.kappa),
            "krippendorff_alpha": _rounded(stats.alpha),
        }
    )
    for label_a, label_b, count in stats.matrix:
        writer.write_record({"label_a": label_a, "label_b": label_b, "count": count})
    writer.flush()
    _disclose(comparison, stats, a.name, b.name, request)
    return ExitCode.OK


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


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
    records = tuple(
        record
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines())
        if line.strip()
        for record in (_record_from(line, index),)
    )
    return LabelFile(name=str(path), records=records)


def _record_from(line: str, index: int) -> dict[str, object]:
    item = item_from_line(line, index)
    return dict(item.data) if item.data is not None else {"text": item.text}
