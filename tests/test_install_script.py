"""Offline smoke tests for packaging/install/install.sh.

The installer runs package managers on stranger machines — the branch logic
(brew first, uv otherwise, the version pin, the PATH hint) is pinned here
against FAKE tools on a stripped PATH. Nothing real installs, nothing talks
to the network; POSIX-only, skipped where /bin/sh doesn't exist.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "packaging" / "install" / "install.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None, reason="needs a POSIX sh"
)


def _fake_tool(bin_dir: Path, name: str, log: Path) -> None:
    tool = bin_dir / name
    tool.write_text(f'#!/bin/sh\nprintf \'{name} %s\\n\' "$*" >> "{log}"\n', encoding="utf-8")
    tool.chmod(0o755)


def _run(
    tmp_path: Path, *tools: str, env_extra: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    log.touch()
    for name in tools:
        _fake_tool(bin_dir, name, log)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        **(env_extra or {}),
    }
    proc = subprocess.run(
        ["sh", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=30, check=False
    )
    return proc, log.read_text(encoding="utf-8")


def test_brew_wins_when_present(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, "brew", "smartpipe")
    assert proc.returncode == 0, proc.stderr
    assert "brew install prabal-rje/tap/smartpipe" in calls
    assert "smartpipe --version" in calls  # the loud verify ran
    assert "get started: smartpipe config" in proc.stdout


def test_uv_when_no_brew(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, "uv", "smartpipe")
    assert proc.returncode == 0, proc.stderr
    assert "uv tool install smartpipe-cli" in calls
    assert "brew" not in calls


def test_version_pin_applies_to_uv(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, "uv", "smartpipe", env_extra={"SMARTPIPE_VERSION": "9.9.9"})
    assert proc.returncode == 0, proc.stderr
    assert "uv tool install smartpipe-cli==9.9.9" in calls


def test_brew_notes_that_the_pin_does_not_apply(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, "brew", "smartpipe", env_extra={"SMARTPIPE_VERSION": "9.9.9"})
    assert proc.returncode == 0, proc.stderr
    assert "SMARTPIPE_VERSION only pins uv installs" in proc.stdout
    assert "brew install prabal-rje/tap/smartpipe" in calls


def test_path_hint_when_the_binary_is_missing(tmp_path: Path) -> None:
    proc, _calls = _run(tmp_path, "uv")  # no smartpipe fake: not on PATH
    assert proc.returncode == 0, proc.stderr  # a PATH gap is a hint, not a failure
    assert "not on PATH" in proc.stdout
    assert "uv tool update-shell" in proc.stdout


def test_a_failing_manager_fails_the_script(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    broken = bin_dir / "brew"
    broken.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    broken.chmod(0o755)
    proc, _calls = _run(tmp_path)
    assert proc.returncode == 7  # set -e: never claim success over a failed install
