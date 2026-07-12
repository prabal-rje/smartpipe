"""Real-terminal smoke for the C2 first-paint rule (#36/#37): on a live PTY the
zero-state frames the fake-stream tests pin must actually reach a terminal.

Hermetic by construction (no model, no network, no NER load): graph's ADOPT
mode drives both runs. The self-edge JSONL corpus makes a clean green run whose
one distinct name keeps the fold below its two-name floor — the preflight-built
local embedder is never asked to embed (construction is lazy; no weights load).
The plain-text corpus exercises the D3 plain-glob read bar (total = files
NAMED) up to its zero state, then refuses with the three-forms screen before
any phase that would need a model.
"""

from __future__ import annotations

import errno
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pseudo-terminal test")

_TIMEOUT = 30.0  # interpreter start + the adopt run; generous for cold CI


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
            "NO_COLOR": "1",
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
    ):
        env.pop(name, None)
    return env


def _run_graph_on_a_pty(tmp_path: Path, corpus: Path) -> tuple[int, str]:
    repo = Path(__file__).resolve().parents[1]
    master, slave = os.openpty()
    process = subprocess.Popen(
        [sys.executable, "-m", "smartpipe", "graph", "--in", str(corpus)],
        cwd=repo,
        env=_isolated_env(tmp_path),
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
    )
    os.close(slave)
    try:
        code, output = _read_to_exit(process, master)
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=_TIMEOUT)
    return code, output.decode(errors="replace")


def _read_to_exit(process: subprocess.Popen[bytes], master: int) -> tuple[int, bytes]:
    output = bytearray()
    deadline = time.monotonic() + _TIMEOUT
    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            pytest.fail(f"graph did not exit:\n{output.decode(errors='replace')}")
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
    return code, bytes(output)


def test_graph_adopt_paints_the_read_zero_state_on_a_real_terminal(tmp_path: Path) -> None:
    """The plan's hermetic corpus: one self-edge JSONL record, exit 0. The jsonl
    row-cut keeps the total unknown, so the read bar's D1 zero state is the
    ``Processing [0] 0.0/s`` count line — pinned here against a REAL terminal."""
    corpus = tmp_path / "edges.jsonl"
    corpus.write_text('{"source": "ann", "target": "ann"}\n', encoding="utf-8")
    code, transcript = _run_graph_on_a_pty(tmp_path, corpus)
    assert code == 0, transcript
    assert "[read]" in transcript
    assert "Processing [0] 0.0/s" in transcript  # the zero state hit the terminal


def test_graph_read_bar_paints_its_zero_state_bar_on_a_real_terminal(tmp_path: Path) -> None:
    """The determinate twin (D1 + D3): a plain-text file rides the lazy glob
    branch, so the read bar knows total = files NAMED and paints ``0% · 0/1``
    at start. The row then refuses as not-an-edge (the three-forms screen,
    exit 64) — hermetically BEFORE any phase that would want a model."""
    corpus = tmp_path / "notes.txt"
    corpus.write_text("just prose, not an edge record\n", encoding="utf-8")
    code, transcript = _run_graph_on_a_pty(tmp_path, corpus)
    assert code == 64, transcript
    assert "0% · 0/1" in transcript  # the determinate zero state hit the terminal
    assert "graph needs one of its three forms" in transcript  # the refusal stayed loud
