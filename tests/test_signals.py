"""Unix death contracts: SIGPIPE (141, silent) and Ctrl-C drain semantics.

These are real-subprocess tests — signals can't be faithfully simulated in-process.
Synchronization is by events (server arrivals, output lines), never bare sleeps.
"""

from __future__ import annotations

import json
import os
import signal as signal_module
import subprocess
import sys
import time

import pytest

from tests.helpers.paced import PacedOllama

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")

SEMPIPE = f"{sys.executable} -m sempipe"


def test_downstream_close_is_silent_141() -> None:
    # seq floods far past the 64 KiB pipe buffer; head exits after one line, so
    # sempipe's next flushed write hits a closed pipe → it must die like grep: 141,
    # nothing on stderr, never the BUG screen.
    script = f"seq 100000 | {SEMPIPE} echo | head -1; echo code=${{PIPESTATUS[1]}} >&2"
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    assert proc.stdout == "1\n"
    assert "code=141" in proc.stderr
    assert "BUG" not in proc.stderr
    assert "Traceback" not in proc.stderr
    assert "internal error" not in proc.stderr


# --- Ctrl-C drain matrix (ux.md §12) — paced server, no sleeps-as-sync ---------


def _match_all(_body: dict[str, object]) -> str:
    return '{"match": true}'


def _garbage_for_bad(body: dict[str, object]) -> str:
    messages = body.get("messages")
    text = json.dumps(messages)
    return "garbage" if "bad-item" in text else '{"match": true}'


def _spawn_filter(
    server: PacedOllama, stdin: str, *, extra_env: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    env = {
        **os.environ,
        "OLLAMA_HOST": server.url,
        "SEMPIPE_MODEL": "ollama/qwen3:8b",
        **(extra_env or {}),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "sempipe", "filter", "keep?", "--concurrency", "2"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdin is not None
    proc.stdin.write(stdin)
    proc.stdin.close()
    return proc


def test_first_sigint_drains_in_flight_and_stops_intake() -> None:
    with PacedOllama(_match_all) as server:
        proc = _spawn_filter(server, "a\nb\nc\n")
        server.wait_for_arrivals(2)  # a, b in flight; c waiting for a slot
        proc.send_signal(signal_module.SIGINT)
        server.release(2)  # in-flight work completes during the drain
        out, err = proc.communicate(timeout=30)
        assert proc.returncode == 0  # everything that ran succeeded
        assert out == "a\nb\n"  # drained, in order
        assert "done: interrupted — 2 processed · 0 skipped" in err
        assert server.arrived == 2  # c never started: intake stopped


def test_interrupt_preserves_partial_exit_code() -> None:
    with PacedOllama(_garbage_for_bad) as server:
        proc = _spawn_filter(server, "ok-item\nbad-item\n")
        server.wait_for_arrivals(2)
        proc.send_signal(signal_module.SIGINT)
        server.release(2)  # ok matches; bad's verdict is garbage → repair request
        server.wait_for_arrivals(3)
        server.release(1)  # repair garbage again → bad-item is skipped
        out, err = proc.communicate(timeout=30)
        assert proc.returncode == 1  # PARTIAL survives the interrupt
        assert out == "ok-item\n"
        assert "done: interrupted — 1 processed · 1 skipped" in err


def test_second_sigint_exits_immediately() -> None:
    with PacedOllama(_match_all) as server:
        proc = _spawn_filter(server, "a\n")
        server.wait_for_arrivals(1)
        proc.send_signal(signal_module.SIGINT)
        time.sleep(0.3)  # let the first handler run (not sync-critical: worst case
        proc.send_signal(signal_module.SIGINT)  # both count; second still hard-exits)
        out, err = proc.communicate(timeout=15)
        assert proc.returncode == 130
        assert out == ""
        assert "BUG" not in err and "Traceback" not in err


def test_drain_timeout_caps_the_wait() -> None:
    with PacedOllama(_match_all) as server:
        proc = _spawn_filter(server, "a\n", extra_env={"SEMPIPE_DRAIN_SECONDS": "1"})
        server.wait_for_arrivals(1)
        proc.send_signal(signal_module.SIGINT)  # never released → watchdog fires at ~1 s
        out, err = proc.communicate(timeout=20)
        assert proc.returncode == 130
        assert out == ""
        assert "done: interrupted — drain timed out" in err
        assert "Traceback" not in err


def test_broken_pipe_error_fallback_exits_141_quietly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exception path (Windows / flush edge): quiet SystemExit(141), no screen."""
    from sempipe.cli import root

    monkeypatch.setattr(sys, "argv", ["sempipe", "cite"])

    def burst(*_args: object, **_kwargs: object) -> None:
        raise BrokenPipeError

    monkeypatch.setattr("click.echo", burst)
    with pytest.raises(SystemExit) as excinfo:
        root.main()
    assert excinfo.value.code == 141
    assert capsys.readouterr().err == ""
