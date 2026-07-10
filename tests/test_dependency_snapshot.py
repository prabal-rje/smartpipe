"""Install weight is a feature (plan/decisions.md D10).

A new transitive dependency in the *core* install fails this test until it is
consciously acknowledged. Update flow, after verifying the addition is justified:

    UPDATE_GOLDEN=1 uv run pytest tests/test_dependency_snapshot.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden" / "deps-core.txt"
REPO_ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_core_dependency_tree_is_frozen() -> None:
    out = subprocess.run(
        ["uv", "export", "--no-dev", "--no-emit-project", "--no-hashes"],
        check=True,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "NO_COLOR": "1"},  # newer uv colorizes; ANSI is not a dependency
    ).stdout
    names = sorted(
        {
            re.split(r"[=<>!;\[ ]", line, maxsplit=1)[0]
            for line in out.splitlines()
            if line and not line.startswith(("#", " ", "-"))
        }
    )
    snapshot = "".join(f"{name}\n" for name in names)
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(snapshot, encoding="utf-8")
    assert snapshot == GOLDEN.read_text(encoding="utf-8"), (
        "Core dependency set changed. If intentional, refresh with UPDATE_GOLDEN=1 "
        "(see this file's docstring); otherwise remove or re-justify the addition."
    )
