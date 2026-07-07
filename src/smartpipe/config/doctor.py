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
from smartpipe.io.text import display_width

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = ["CheckResult", "doctor_exit_code", "render_report"]

_MARKS: dict[str, str] = {"ok": "✓", "fail": "✗", "skip": "–"}  # noqa: RUF001 — the en-dash skip mark is the ux.md pin


@dataclass(frozen=True, slots=True)
class CheckResult:
    section: str  # "config", "ollama", "chat", "embed", "keys", "login", "extras", "terminal"
    status: Literal["ok", "fail", "skip"]
    detail: str  # the rest of the line, including any "— fix: …"


def render_report(results: Sequence[CheckResult]) -> str:
    """One line per check: padded section, mark, detail (the ux.md doctor screen)."""
    from smartpipe.cli.screens import bad, good, tint

    width = max(display_width(result.section) for result in results) + 2

    def dim_mark(mark: str) -> str:
        return tint(mark, "2")

    paint: dict[str, Callable[[str], str]] = {"ok": good, "fail": bad, "skip": dim_mark}
    lines = (
        f"{tint(_pad(result.section, width), '2')}"
        f"{paint[result.status](_MARKS[result.status])} {result.detail}"
        for result in results
    )
    return "\n".join(lines)


def doctor_exit_code(results: Sequence[CheckResult]) -> ExitCode:
    """0 all green (skips are fine); 1 if anything needs fixing."""
    if any(result.status == "fail" for result in results):
        return ExitCode.PARTIAL
    return ExitCode.OK


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))
