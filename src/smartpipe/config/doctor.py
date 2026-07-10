"""``smartpipe doctor`` report — the pure half (D18).

Check *gathering* is the CLI's job (it touches env, disk, and the local Ollama
probe); this module owns the result type, the rendering, and the exit rule so the
exact screen is golden-testable. Doctor never makes a paid model call — its whole
point is telling you the run will work *before* anything costs money.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from smartpipe.core.errors import ExitCode
from smartpipe.io.richui import Cell, UiStyle, render_grid

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["CheckResult", "doctor_exit_code", "render_report"]

_MARKS: dict[str, str] = {"ok": "✓", "fail": "✗", "skip": "–"}  # noqa: RUF001 — the en-dash skip mark is the ux.md pin


@dataclass(frozen=True, slots=True)
class CheckResult:
    section: str  # "config", "ollama", "chat", "embed", "keys", "login", "extras", "terminal"
    status: Literal["ok", "fail", "skip"]
    detail: str  # the rest of the line, including any "— fix: …"


def render_report(results: Sequence[CheckResult], *, color: bool) -> str:
    """One line per check: padded section, mark, detail (the ux.md doctor screen)."""
    section_width = max((len(result.section) for result in results), default=0) + 2
    styles: dict[str, UiStyle] = {
        "ok": UiStyle.GOOD,
        "fail": UiStyle.BAD,
        "skip": UiStyle.DIM,
    }
    rows = tuple(
        (
            Cell(result.section, UiStyle.DIM),
            Cell(_MARKS[result.status], styles[result.status]),
            Cell(f" {result.detail}"),
        )
        for result in results
    )
    return render_grid(
        rows,
        color=color,
        column_gap=0,
        column_widths=(section_width, 1, None),
    )


def doctor_exit_code(results: Sequence[CheckResult]) -> ExitCode:
    """0 all green (skips are fine); 1 if anything needs fixing."""
    if any(result.status == "fail" for result in results):
        return ExitCode.PARTIAL
    return ExitCode.OK
