"""Offline smoke tests for packaging/install/install.sh.

The installer runs package managers on stranger machines - the branch logic
(brew first, uv otherwise, the version pin, idempotent reruns, the curl/wget
fallback, the platform notes, the PATH hint) is pinned here against FAKE
tools on a stripped PATH. Installer fakes create the smartpipe binary the way
real installs do, so "fresh install" and "already installed" stay distinct.
Nothing real installs, nothing talks to the network; POSIX-only, skipped
where /bin/sh doesn't exist.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "packaging" / "install" / "install.sh"
ASTRAL_URL = "https://astral.sh/uv/install.sh"

BREW_INSTALL_CONDITION = '[ "$1" = install ]'
UV_INSTALL_CONDITION = '[ "$1" = tool ] && [ "$2" = install ]'

# A fake uv that already manages smartpipe-cli. Mirrors real uv on a rerun:
# `tool list` names the tool, and a plain `tool install` without --force does
# NOT upgrade (verified on uv 0.9.26: it no-ops with "already installed";
# older uv errors) - so the fake refuses, forcing `tool upgrade` / `--force`.
UV_MANAGES_SMARTPIPE = """\
if [ "$1" = tool ] && [ "$2" = list ]; then printf 'smartpipe-cli v1.3.0\\n- smartpipe\\n'; fi
if [ "$1" = tool ] && [ "$2" = install ]; then
    case "$*" in
    *--force*) : ;;
    *) printf 'error: smartpipe-cli is already installed\\n' >&2; exit 2 ;;
    esac
fi
"""

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None, reason="needs a POSIX sh"
)


def _log(tmp_path: Path) -> Path:
    log = tmp_path / "calls.log"
    log.touch()
    return log


def _fake_tool_at(directory: Path, name: str, log: Path, body: str = "") -> Path:
    """A fake tool that logs every call; `body` adds behaviour after the log line."""
    directory.mkdir(parents=True, exist_ok=True)
    tool = directory / name
    tool.write_text(f'#!/bin/sh\nprintf \'{name} %s\\n\' "$*" >> "{log}"\n{body}', encoding="utf-8")
    tool.chmod(0o755)
    return tool


def _fake_tool(tmp_path: Path, name: str, body: str = "") -> Path:
    return _fake_tool_at(tmp_path / "bin", name, _log(tmp_path), body)


def _installs_smartpipe(tmp_path: Path, condition: str) -> str:
    """Fake-installer behaviour: on `condition`, drop smartpipe onto PATH."""
    payload = _fake_tool_at(tmp_path / "payload", "smartpipe", _log(tmp_path))
    bin_dir = tmp_path / "bin"
    return f'if {condition}; then cp "{payload}" "{bin_dir}/smartpipe"; fi\n'


def _restricted_path(tmp_path: Path, *real_tools: str) -> str:
    """A PATH of only the fake bin dir plus symlinks to the named real tools."""
    sys_dir = tmp_path / "sysbin"
    sys_dir.mkdir(exist_ok=True)
    for name in real_tools:
        real = shutil.which(name)
        assert real is not None, f"test host lacks {name}"
        (sys_dir / name).symlink_to(real)
    return f"{tmp_path / 'bin'}:{sys_dir}"


def _run(
    tmp_path: Path,
    *tools: str,
    env_extra: dict[str, str] | None = None,
    path: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    (tmp_path / "bin").mkdir(exist_ok=True)
    log = _log(tmp_path)
    for name in tools:
        _fake_tool(tmp_path, name)
    env = {
        "PATH": path if path is not None else f"{tmp_path / 'bin'}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        **(env_extra or {}),
    }
    proc = subprocess.run(
        ["sh", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=30, check=False
    )
    return proc, log.read_text(encoding="utf-8")


def test_brew_wins_when_present(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "brew", _installs_smartpipe(tmp_path, BREW_INSTALL_CONDITION))
    proc, calls = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "brew install prabal-rje/tap/smartpipe" in calls
    assert "smartpipe --version" in calls  # the loud verify ran
    assert "get started: smartpipe config" in proc.stdout


def test_uv_when_no_brew(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "uv", _installs_smartpipe(tmp_path, UV_INSTALL_CONDITION))
    proc, calls = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "uv tool install smartpipe-cli" in calls
    assert "brew" not in calls


def test_version_pin_applies_to_uv(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "uv", _installs_smartpipe(tmp_path, UV_INSTALL_CONDITION))
    proc, calls = _run(tmp_path, env_extra={"SMARTPIPE_VERSION": "9.9.9"})
    assert proc.returncode == 0, proc.stderr
    assert "uv tool install smartpipe-cli==9.9.9" in calls


def test_brew_notes_that_the_pin_does_not_apply(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "brew", _installs_smartpipe(tmp_path, BREW_INSTALL_CONDITION))
    proc, calls = _run(tmp_path, env_extra={"SMARTPIPE_VERSION": "9.9.9"})
    assert proc.returncode == 0, proc.stderr
    assert "SMARTPIPE_VERSION only pins uv installs" in proc.stdout
    assert "brew install prabal-rje/tap/smartpipe" in calls


def test_path_hint_when_the_binary_is_missing(tmp_path: Path) -> None:
    proc, _calls = _run(tmp_path, "uv")  # uv "installs" but smartpipe never lands on PATH
    assert proc.returncode == 0, proc.stderr  # a PATH gap is a hint, not a failure
    assert "not on PATH" in proc.stdout
    assert "uv tool update-shell" in proc.stdout


def test_a_failing_manager_fails_the_script(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "brew", "exit 7\n")
    proc, _calls = _run(tmp_path)
    assert proc.returncode == 7  # set -e: never claim success over a failed install


def test_rerun_brew_managed_upgrades_in_place(tmp_path: Path) -> None:
    cellar_bin = tmp_path / "Cellar" / "smartpipe" / "1.3.0" / "bin"
    _fake_tool_at(cellar_bin, "smartpipe", _log(tmp_path))
    _fake_tool(tmp_path, "brew")
    proc, calls = _run(tmp_path, path=f"{cellar_bin}:{tmp_path / 'bin'}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    assert "already installed - upgrading" in proc.stdout
    assert "brew upgrade prabal-rje/tap/smartpipe" in calls
    assert "brew install" not in calls


def test_rerun_survives_a_failing_brew_upgrade(tmp_path: Path) -> None:
    cellar_bin = tmp_path / "Cellar" / "smartpipe" / "1.3.0" / "bin"
    _fake_tool_at(cellar_bin, "smartpipe", _log(tmp_path))
    _fake_tool(tmp_path, "brew", 'if [ "$1" = upgrade ]; then exit 1; fi\n')
    proc, calls = _run(tmp_path, path=f"{cellar_bin}:{tmp_path / 'bin'}:/usr/bin:/bin")
    assert "brew upgrade prabal-rje/tap/smartpipe" in calls
    assert proc.returncode == 0, proc.stderr  # an upgrade hiccup must not fail the rerun


def test_rerun_uv_managed_upgrades(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "smartpipe")
    _fake_tool(tmp_path, "uv", UV_MANAGES_SMARTPIPE)
    proc, calls = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "already installed - upgrading" in proc.stdout
    assert "uv tool upgrade smartpipe-cli" in calls


def test_rerun_uv_managed_with_pin_forces_that_version(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "smartpipe")
    _fake_tool(tmp_path, "uv", UV_MANAGES_SMARTPIPE)
    proc, calls = _run(tmp_path, env_extra={"SMARTPIPE_VERSION": "9.9.9"})
    assert proc.returncode == 0, proc.stderr
    assert "uv tool install --force smartpipe-cli==9.9.9" in calls


def test_rerun_from_another_installer_defers_and_exits_zero(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "smartpipe")  # pipx/pip style: on PATH, but no brew and no uv
    proc, calls = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "already installed via" in proc.stdout
    assert str(tmp_path / "bin" / "smartpipe") in proc.stdout
    assert "upgrade" in proc.stdout  # points at the owning installer's upgrade
    assert calls == ""  # nothing was installed, upgraded, or run


def test_uv_bootstrap_falls_back_to_wget(tmp_path: Path) -> None:
    log = _log(tmp_path)
    uv_payload = _fake_tool_at(tmp_path / "payload", "uv", log)
    fetched = tmp_path / "fetched-uv-installer.sh"  # what "wget ... | sh" streams
    fetched.write_text(
        f'mkdir -p "{tmp_path}/.local/bin"\ncp "{uv_payload}" "{tmp_path}/.local/bin/uv"\n',
        encoding="utf-8",
    )
    _fake_tool(tmp_path, "wget", f'cat "{fetched}"\n')
    proc, calls = _run(tmp_path, path=_restricted_path(tmp_path, "sh", "cat", "cp", "mkdir"))
    assert proc.returncode == 0, proc.stderr
    assert f"wget -qO- {ASTRAL_URL}" in calls
    assert "uv tool install smartpipe-cli" in calls


def test_uv_bootstrap_without_curl_or_wget_names_both(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, path=_restricted_path(tmp_path, "sh"))
    assert proc.returncode == 1
    assert "curl" in proc.stderr
    assert "wget" in proc.stderr
    assert "uv tool install" not in calls


def test_musl_warns_before_installing_and_continues(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "uv", _installs_smartpipe(tmp_path, UV_INSTALL_CONDITION))
    # musl's ldd prints its banner to stderr and exits 1
    _fake_tool(tmp_path, "ldd", 'printf "musl libc (x86_64)\\nVersion 1.2.5\\n" >&2\nexit 1\n')
    proc, calls = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "musl" in proc.stdout
    assert "onnxruntime" in proc.stdout
    assert proc.stdout.index("musl") < proc.stdout.index("installing with uv")
    assert "uv tool install smartpipe-cli" in calls  # warned, then continued anyway


def test_rosetta_note_on_translated_mac(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "brew", _installs_smartpipe(tmp_path, BREW_INSTALL_CONDITION))
    _fake_tool(tmp_path, "uname", 'case "$1" in -s) echo Darwin ;; -m) echo x86_64 ;; esac\n')
    _fake_tool(tmp_path, "sysctl", "echo 1\n")
    proc, calls = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "Rosetta" in proc.stdout
    assert "arch -arm64 zsh" in proc.stdout
    assert "brew install prabal-rje/tap/smartpipe" in calls  # a note, never a block


def test_github_actions_appends_local_bin_to_github_path(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "uv", _installs_smartpipe(tmp_path, UV_INSTALL_CONDITION))
    gh_path = tmp_path / "github_path"
    proc, _calls = _run(tmp_path, env_extra={"GITHUB_ACTIONS": "true", "GITHUB_PATH": str(gh_path)})
    assert proc.returncode == 0, proc.stderr
    assert f"{tmp_path}/.local/bin" in gh_path.read_text(encoding="utf-8")
    assert "GITHUB_PATH" in proc.stdout  # the note
