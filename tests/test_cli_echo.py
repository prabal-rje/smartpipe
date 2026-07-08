from __future__ import annotations

import io

import pytest

from tests.conftest import RunCli


def test_echo_passes_input_through_byte_identically(run_cli: RunCli) -> None:
    payload = 'a\n{ "b" :1}\n'
    code, out, err = run_cli(["echo"], stdin=payload)
    assert code == 0
    assert out == payload
    assert err == "note: input: 1 records · 1 plain lines\n"  # the kind census (item 20)


def test_echo_forced_json_is_still_passthrough(run_cli: RunCli) -> None:
    payload = '{"x": 1}\n'
    code, out, _err = run_cli(["echo", "--output", "json"], stdin=payload)
    assert code == 0
    assert out == payload


def test_echo_is_hidden_from_help(run_cli: RunCli) -> None:
    _code, out, _err = run_cli(["--help"])
    assert "echo" not in out


class _FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_terminal_stdin_is_a_usage_error(run_cli: RunCli, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", _FakeTty())
    code, out, err = run_cli(["echo"])
    assert code == 64
    assert out == ""
    assert err.startswith("error: reading from a terminal")
    assert "pipe some input" in err
