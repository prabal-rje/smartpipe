"""Real-terminal regressions for the interactive config boundary."""

from __future__ import annotations

import errno
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from smartpipe.config.store import Config, load_config, save_config

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pseudo-terminal test")

_PROMPT = b"Pick ["
_TIMEOUT = 10.0


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "TERM": "dumb",
            "NO_COLOR": "1",
            "SHELL": "/bin/zsh",
            "SMARTPIPE_NO_UPDATE_CHECK": "1",
            "PYTHONPATH": os.pathsep.join(
                (str(repo / "src"), *(value for value in (env.get("PYTHONPATH"),) if value))
            ),
        }
    )
    for name in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
        "MISTRAL_API_KEY",
        "OPENROUTER_API_KEY",
        "JINA_API_KEY",
        "SMARTPIPE_MODEL",
        "SMARTPIPE_EMBED_MODEL",
        "SMARTPIPE_OCR_MODEL",
        "SMARTPIPE_STT_MODEL",
    ):
        env.pop(name, None)
    return env


def _spawn_config(tmp_path: Path) -> tuple[subprocess.Popen[bytes], int]:
    repo = Path(__file__).resolve().parents[1]
    master, slave = os.openpty()
    process = subprocess.Popen(
        [sys.executable, "-m", "smartpipe", "config"],
        cwd=repo,
        env=_isolated_env(tmp_path),
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
    )
    os.close(slave)
    return process, master


def _read_until(
    process: subprocess.Popen[bytes],
    master: int,
    output: bytearray,
    marker: bytes,
    *,
    start: int = 0,
) -> None:
    deadline = time.monotonic() + _TIMEOUT
    while marker not in output[start:]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            pytest.fail(f"timed out waiting for {marker!r}:\n{output.decode(errors='replace')}")
        ready, _, _ = select.select((master,), (), (), min(remaining, 0.1))
        if not ready:
            if process.poll() is not None:
                pytest.fail(f"config exited before {marker!r}:\n{output.decode(errors='replace')}")
            continue
        try:
            chunk = os.read(master, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        output.extend(chunk)
    if marker not in output[start:]:
        pytest.fail(f"missing {marker!r}:\n{output.decode(errors='replace')}")


def _read_to_exit(process: subprocess.Popen[bytes], master: int, output: bytearray) -> int:
    deadline = time.monotonic() + _TIMEOUT
    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            pytest.fail(f"config did not exit:\n{output.decode(errors='replace')}")
        ready, _, _ = select.select((master,), (), (), min(remaining, 0.1))
        if not ready:
            continue
        try:
            chunk = os.read(master, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                break
            raise
        output.extend(chunk)
    code = process.wait(timeout=_TIMEOUT)
    while True:
        ready, _, _ = select.select((master,), (), (), 0)
        if not ready:
            break
        try:
            chunk = os.read(master, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        output.extend(chunk)
    return code


def _close(process: subprocess.Popen[bytes], master: int) -> None:
    os.close(master)
    if process.poll() is None:
        process.kill()
        process.wait(timeout=_TIMEOUT)


def test_config_click_prompt_eof_exits_130_without_bug_screen(tmp_path: Path) -> None:
    process, master = _spawn_config(tmp_path)
    output = bytearray()
    try:
        _read_until(process, master, output, _PROMPT)
        os.write(master, b"\x04")
        code = _read_to_exit(process, master, output)
    finally:
        _close(process, master)

    transcript = output.decode(errors="replace")
    assert code == 130
    assert "internal error" not in transcript
    assert "this is a bug" not in transcript


def _seed_registry(tmp_path: Path) -> None:
    """Pin today's capability-registry cache so the subprocess never fetches
    models.dev — the OCR stage's vision rows (item 73c) stay deterministic."""
    from datetime import UTC, datetime

    from smartpipe.config.picker import RegistryCaps
    from smartpipe.config.state_cache import registry_path, store_registry

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    env = {"XDG_STATE_HOME": str(tmp_path / "state")}
    store_registry(registry_path(env, day), {"openai/o3": RegistryCaps(image=True, audio=False)})


def test_config_discard_exits_cleanly_without_a_later_prompt(tmp_path: Path) -> None:
    path = tmp_path / "config" / "smartpipe" / "config.toml"
    original = Config(model="ollama/qwen3:8b", ocr_model="ollama/llava")
    save_config(path, original)
    _seed_registry(tmp_path)
    process, master = _spawn_config(tmp_path)
    output = bytearray()
    try:
        _read_until(process, master, output, _PROMPT)
        start = len(output)
        os.write(master, b"\n")  # keep the current text model
        _read_until(process, master, output, b"Add a backup model", start=start)
        start = len(output)
        os.write(master, b"\n")  # no backup
        _read_until(process, master, output, _PROMPT, start=start)
        start = len(output)
        os.write(master, b"\n")  # keep the default local embedder
        _read_until(process, master, output, _PROMPT, start=start)
        start = len(output)
        # ocr ordinals (separators take no number): 1 keep · 2 mistral · 3 vision
        # chat · 4 openai/o3 (the seeded registry's one vision entry, item 73c) ·
        # 5 typed · 6 unset · 7 back
        os.write(master, b"6\n")  # draft: unset the current OCR model
        _read_until(process, master, output, _PROMPT, start=start)
        start = len(output)
        os.write(master, b"\n")  # speech-to-text: keep the auto ladder
        _read_until(process, master, output, _PROMPT, start=start)
        os.write(master, b"3\n")  # discard the draft (1 save · 2 back · 3 discard)
        code = _read_to_exit(process, master, output)
    finally:
        _close(process, master)

    transcript = output.decode(errors="replace")
    assert code == 0
    assert "openai/o3" in transcript  # the vision-capable catalog row was offered
    assert "Not saved." in transcript
    assert "Install zsh tab completion" not in transcript
    assert "internal error" not in transcript
    assert load_config(path) == original
