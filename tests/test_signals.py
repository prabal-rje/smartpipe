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
from pathlib import Path

import pytest

from tests.helpers.paced import PacedOllama

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")

SEMPIPE = f"{sys.executable} -m smartpipe"


def test_main_keeps_sigpipe_ignored() -> None:
    """Item 75: main() must NEVER re-arm SIG_DFL for SIGPIPE. Process-wide
    SIG_DFL turned stray EPIPEs — a provider socket, the event loop's
    self-pipe when the stdin pump's call_soon_threadsafe races loop teardown —
    into raw -13 deaths with NO downstream close (the rc3 1-in-12 flake).
    Python's default SIG_IGN stays: a closed stdout surfaces as
    BrokenPipeError, which main() converts to the pinned quiet 141."""
    probe = (
        "import signal, sys\n"
        "from smartpipe.cli import root\n"
        "sys.argv = ['smartpipe', 'echo']\n"
        "try:\n"
        "    root.main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "handler = signal.getsignal(signal.SIGPIPE)\n"
        "print('DFL' if handler is signal.SIG_DFL else 'IGN', file=sys.stderr)\n"
    )
    proc = subprocess.run(  # stdout is a real pipe — the exact armed state
        [sys.executable, "-c", probe],
        input="x\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.stderr.strip().endswith("IGN"), proc.stderr


def test_downstream_close_is_silent_141() -> None:
    # seq floods far past the 64 KiB pipe buffer; head exits after one line, so
    # smartpipe's next flushed write hits a closed pipe → it must die like grep: 141,
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
    server: PacedOllama,
    stdin: str,
    *,
    extra_env: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.Popen[str]:
    env = {
        **os.environ,
        "OLLAMA_HOST": server.url,
        "SMARTPIPE_MODEL": "ollama/qwen3:8b",
        # the drain matrix pins the SOLO wire, call by call; the batched drain
        # is covered by tests/verbs/test_batching.py::test_interrupt_drains…
        "SMARTPIPE_BATCH": "off",
        **(extra_env or {}),
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "smartpipe",
            "filter",
            "keep?",
            "--concurrency",
            "2",
            *(extra_args or []),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdin is not None
    proc.stdin.write(stdin)
    proc.stdin.close()
    # 3.11's communicate() flushes a still-set stdin even when closed (ValueError);
    # 3.12+ tolerates it. Clearing the handle makes communicate() skip the flush.
    proc.stdin = None
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
        proc = _spawn_filter(server, "a\n", extra_env={"SMARTPIPE_DRAIN_SECONDS": "1"})
        server.wait_for_arrivals(1)
        proc.send_signal(signal_module.SIGINT)  # never released → watchdog fires at ~1 s
        out, err = proc.communicate(timeout=20)
        assert proc.returncode == 130
        assert out == ""
        assert "done: interrupted — drain timed out" in err
        assert "Traceback" not in err


def test_first_sigint_acknowledges_the_drain_on_stderr() -> None:
    # B2: the first Ctrl-C writes a direct fd-2 acknowledgment (os.write) so the
    # user knows it registered even while the loop is busy draining.
    with PacedOllama(_match_all) as server:
        proc = _spawn_filter(server, "a\nb\n")
        server.wait_for_arrivals(2)
        proc.send_signal(signal_module.SIGINT)
        server.release(2)
        out, err = proc.communicate(timeout=30)
        assert proc.returncode == 0
        assert out == "a\nb\n"
        assert "draining" in err  # the acknowledgment landed on stderr, not stdout


def test_hard_exit_leaves_no_manifest_temp(tmp_path: Path) -> None:
    # B6: a second Ctrl-C hard-exits via os._exit, which bypasses the normal
    # abandon() unwind. The registered hard-exit cleanup must still unlink the
    # reserved 0-byte *.manifest.tmp so nothing leaks.
    manifest_path = tmp_path / "run.json"
    with PacedOllama(_match_all) as server:
        proc = _spawn_filter(server, "a\n", extra_args=["--manifest", str(manifest_path)])
        server.wait_for_arrivals(1)
        proc.send_signal(signal_module.SIGINT)
        time.sleep(0.3)  # let the first handler arm the drain (not sync-critical)
        proc.send_signal(signal_module.SIGINT)  # second press → os._exit(130) + cleanups
        out, err = proc.communicate(timeout=15)
        assert proc.returncode == 130
        assert out == ""
        assert list(tmp_path.glob("*.manifest.tmp")) == []  # no 0-byte temp leaked
        assert not manifest_path.exists()  # hard exit before finish → no manifest written
        assert "Traceback" not in err


_STUCK_FOLD_PROBE = (
    "import asyncio, os, time\n"
    "from smartpipe.cli.interrupts import graceful_interrupts\n"
    "async def main():\n"
    "    async with graceful_interrupts():\n"
    "        os.write(2, b'READY\\n')\n"
    "        await asyncio.to_thread(time.sleep, 30)\n"  # uncancellable: holds the executor join
    "asyncio.run(main())\n"
)


def test_watchdog_escalation_hard_exits_a_stuck_executor() -> None:
    # B2 escalation: after the drain cap the watchdog cancels the main task, but a
    # stuck to-thread call keeps asyncio.run's teardown JOINING it forever. An
    # off-loop daemon takes the hard-exit path itself after a short grace, so the
    # process dies 130 within cap+grace instead of hanging on the 30 s sleep.
    env = {
        **os.environ,
        "SMARTPIPE_DRAIN_SECONDS": "0.3",
        "SMARTPIPE_DRAIN_GRACE_SECONDS": "0.3",
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", _STUCK_FOLD_PROBE],
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stderr is not None
    assert "READY" in proc.stderr.readline()  # the stuck to-thread is running
    started = time.monotonic()
    proc.send_signal(signal_module.SIGINT)  # one press: drain → cap → cancel → escalation
    try:
        _out, err = proc.communicate(timeout=8)  # generous, but must NOT hang 30 s
    except subprocess.TimeoutExpired:  # pragma: no cover — the escalation failed to fire
        proc.kill()
        raise
    assert proc.returncode == 130
    assert time.monotonic() - started < 5  # cap + grace, nowhere near the 30 s sleep
    assert "exiting" in err  # the escalation's own hard-exit acknowledgment


def test_broken_pipe_error_fallback_exits_141_quietly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exception path (Windows / flush edge): quiet SystemExit(141), no screen."""
    from smartpipe.cli import root

    monkeypatch.setattr(sys, "argv", ["smartpipe", "cite"])

    def burst(*_args: object, **_kwargs: object) -> None:
        raise BrokenPipeError

    monkeypatch.setattr("click.echo", burst)
    with pytest.raises(SystemExit) as excinfo:
        root.main()
    assert excinfo.value.code == 141
    assert capsys.readouterr().err == ""
