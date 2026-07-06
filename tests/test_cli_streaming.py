"""The streaming property, end to end: results appear while stdin is STILL OPEN —
impossible before stage-08 — and shutdown never hangs on the blocked reader.
"""

from __future__ import annotations

import io
import os
import queue
import signal
import subprocess
import sys
import threading
from typing import TYPE_CHECKING

import pytest

from sempipe.core.errors import ExitCode
from sempipe.io.writers import OutputFormat, RenderMode, WriterConfig, make_writer
from sempipe.models.base import CompletionRequest, ModelRef
from sempipe.verbs.map import MapRequest, run_map
from tests.helpers.paced import PacedOllama

if TYPE_CHECKING:
    from typing import TextIO

    from sempipe.io.writers import ResultWriter

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipes/signals")


def _match_all(_body: dict[str, object]) -> str:
    return '{"match": true}'


def test_filter_streams_and_shuts_down_cleanly() -> None:
    """One test, three guarantees: (1) output while the pipe is open, (2) intake
    stops on ^C with the reader BLOCKED on an open pipe, (3) exit is fast — the
    daemon pump can't hang shutdown."""
    with PacedOllama(_match_all, paced=False) as server:
        env = {**os.environ, "OLLAMA_HOST": server.url, "SEMPIPE_MODEL": "ollama/qwen3:8b"}
        proc = subprocess.Popen(
            [sys.executable, "-m", "sempipe", "filter", "keep?", "--concurrency", "2"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        assert proc.stdin is not None and proc.stdout is not None
        stdout = proc.stdout  # narrowed for the closure (pyright)
        lines: queue.Queue[str] = queue.Queue()
        threading.Thread(
            target=lambda: [lines.put(ln) for ln in iter(stdout.readline, "")],
            daemon=True,
        ).start()

        proc.stdin.write("first\n")
        proc.stdin.flush()
        assert lines.get(timeout=15) == "first\n"  # emitted with stdin STILL OPEN

        proc.stdin.write("second\n")
        proc.stdin.flush()
        assert lines.get(timeout=15) == "second\n"

        proc.send_signal(signal.SIGINT)  # reader is parked on the open pipe right now
        _out, err = proc.communicate(timeout=10)  # ≪ drain cap: nothing in flight
        assert proc.returncode == 0
        assert "done: interrupted — 2 processed · 0 skipped" in err
        assert "Traceback" not in err


async def test_map_emits_before_eof_in_process() -> None:
    """The in-process twin, at the verb layer: run_map yields results per line."""

    class EchoUpper:
        ref = ModelRef("ollama", "fake")

        async def complete(self, request: CompletionRequest) -> str:
            return request.user.rsplit("\n\n", 1)[-1].upper()

    class Ctx:
        async def chat_model(self, flag: str | None = None) -> EchoUpper:
            return EchoUpper()

        async def context_window(self, ref: object) -> int | None:
            return None  # the static table stands here

        def concurrency(self, flag: int | None = None) -> int:
            return 2

        def remote_transcriber(self) -> None:
            return None

        def writer(
            self,
            output_flag: OutputFormat,
            *,
            structured: bool,
            stdout: TextIO,
            fields: tuple[str, ...] | None = None,
        ) -> ResultWriter:
            config = WriterConfig(mode=RenderMode.TEXT, color=False, width=80, fields=fields)
            return make_writer(config, stdout)

    import asyncio

    r_fd, w_fd = os.pipe()
    reader = os.fdopen(r_fd, "r", encoding="utf-8")
    out = io.StringIO()
    request = MapRequest(
        prompt="shout",
        schema_path=None,
        model_flag=None,
        output=OutputFormat.AUTO,
        concurrency_flag=None,
    )
    task = asyncio.ensure_future(run_map(request, Ctx(), stdin=reader, stdout=out))
    try:
        os.write(w_fd, b"hello\n")
        for _ in range(200):  # bounded poll (≤2 s) for the incremental emission
            if out.getvalue() == "HELLO\n":
                break
            await asyncio.sleep(0.01)
        assert out.getvalue() == "HELLO\n"  # result written while the pipe is open
        os.write(w_fd, b"world\n")
        os.close(w_fd)
        assert await asyncio.wait_for(task, timeout=5) == ExitCode.OK
        assert out.getvalue() == "HELLO\nWORLD\n"  # order preserved
    finally:
        if not task.done():
            task.cancel()
        reader.close()


def test_binary_stdin_document_end_to_end() -> None:
    """The stage-07 demo, real markitdown: `sempipe map "Summarize" < report.pdf`."""
    pytest.importorskip("markitdown")

    def reply(_body: dict[str, object]) -> str:
        return "ONE-LINE SUMMARY"

    with PacedOllama(reply, paced=False) as server:
        env = {**os.environ, "OLLAMA_HOST": server.url, "SEMPIPE_MODEL": "ollama/qwen3:8b"}
        with open("tests/corpus/one-page.pdf", "rb") as pdf:
            proc = subprocess.run(
                [sys.executable, "-m", "sempipe", "map", "Summarize"],
                stdin=pdf,
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == "ONE-LINE SUMMARY\n"  # one document → one item → one result
