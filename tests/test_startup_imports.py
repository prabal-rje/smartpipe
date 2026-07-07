"""The startup budget as a deterministic gate: ``--help`` must stay light.

The <150 ms promise (plan/stages/stage-09) can't be timed reliably in CI, so the
gate is structural instead: the heavy stack must not be imported at all for
``--help``. New heavy import? Make it function-local, or justify it here.
"""

from __future__ import annotations

import subprocess
import sys

BANNED = {"httpx", "jsonschema", "anthropic", "markitdown"}


def test_help_never_imports_the_heavy_stack() -> None:
    proc = subprocess.run(
        [sys.executable, "-X", "importtime", "-m", "smartpipe", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0
    imported = {
        line.rsplit("|", 1)[-1].strip()
        for line in proc.stderr.splitlines()
        if line.startswith("import time:")
    }
    offenders = BANNED & imported
    assert not offenders, f"--help imported: {sorted(offenders)}"
