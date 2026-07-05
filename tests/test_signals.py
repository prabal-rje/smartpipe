"""Unix death contracts: SIGPIPE (141, silent) and Ctrl-C drain semantics.

These are real-subprocess tests — signals can't be faithfully simulated in-process.
Synchronization is by events (server arrivals, output lines), never bare sleeps.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")

SEMPIPE = f"{sys.executable} -m sempipe"


def test_downstream_close_is_silent_141() -> None:
    # seq floods far past the 64 KiB pipe buffer; head exits after one line, so
    # sempipe's next flushed write hits a closed pipe → it must die like grep: 141,
    # nothing on stderr, never the BUG screen.
    script = f"seq 100000 | {SEMPIPE} echo | head -1; echo code=${{PIPESTATUS[1]}} >&2"
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=60
    )
    assert proc.stdout == "1\n"
    assert "code=141" in proc.stderr
    assert "BUG" not in proc.stderr
    assert "Traceback" not in proc.stderr
    assert "internal error" not in proc.stderr


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
