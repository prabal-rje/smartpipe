"""The golden-screen pin: plain-text transcripts under tests/golden/screens."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

__all__ = ["assert_golden"]

GOLDEN = Path(__file__).parent.parent / "golden" / "screens"


def assert_golden(name: str, rendered: str) -> None:
    rendered = _strip_ansi(rendered)  # goldens pin PLAIN text; styling is never contract (D42)
    path = GOLDEN / f"{name}.txt"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    if not path.exists():
        pytest.fail(f"golden '{name}' missing; create it with: make golden")
    assert rendered == path.read_text(encoding="utf-8"), (
        f"screen '{name}' drifted from its golden; if intended, run: make golden"
    )


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)
