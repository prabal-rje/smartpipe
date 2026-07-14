from __future__ import annotations

import io
import os
import socket
import stat
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smartpipe.io import tty
from smartpipe.io.tty import ColorMode, supports_color

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("is_tty", "env", "expected"),
    [
        (True, {}, True),
        (False, {}, False),
        (True, {"NO_COLOR": "1"}, False),
        (True, {"NO_COLOR": ""}, False),  # any presence disables, per the convention
        (True, {"TERM": "dumb"}, False),
        (True, {"TERM": "xterm-256color"}, True),
        (False, {"TERM": "xterm-256color"}, False),
    ],
)
def test_auto_mode_truth_table(is_tty: bool, env: dict[str, str], expected: bool) -> None:
    assert supports_color(is_tty, mode=ColorMode.AUTO, env=env) is expected


@given(
    is_tty=st.booleans(),
    env=st.dictionaries(st.text(min_size=1), st.text(), max_size=5),
)
def test_always_and_never_are_constant(is_tty: bool, env: dict[str, str]) -> None:
    assert supports_color(is_tty, mode=ColorMode.ALWAYS, env=env) is True
    assert supports_color(is_tty, mode=ColorMode.NEVER, env=env) is False


@pytest.mark.skipif(sys.platform == "win32", reason="tests the off-windows path")
def test_enable_windows_vt_is_a_noop_success_off_windows() -> None:
    from smartpipe.io.tty import enable_windows_vt

    assert enable_windows_vt() is True


@pytest.mark.parametrize(
    ("is_tty", "mode", "rdev", "null_rdev", "expected_name"),
    [
        (True, None, None, None, "TERMINAL"),
        (False, stat.S_IFREG, None, None, "REGULAR_FILE"),
        (False, stat.S_IFCHR, 7, 7, "NULL_DEVICE"),
        (False, stat.S_IFIFO, None, None, "FIFO"),
        (False, stat.S_IFSOCK, None, None, "SOCKET"),
        (False, stat.S_IFCHR, 8, 7, "UNKNOWN"),
        (False, stat.S_IFBLK, 7, 7, "UNKNOWN"),
        (False, None, None, None, "UNKNOWN"),
    ],
)
def test_output_endpoint_truth_table(
    is_tty: bool,
    mode: int | None,
    rdev: int | None,
    null_rdev: int | None,
    expected_name: str,
) -> None:
    endpoint = tty.classify_output_endpoint(
        is_tty,
        mode=mode,
        rdev=rdev,
        null_rdev=null_rdev,
    )
    assert endpoint is getattr(tty.OutputEndpoint, expected_name)


@pytest.mark.parametrize(
    ("endpoint_name", "expected"),
    [
        ("TERMINAL", True),
        ("REGULAR_FILE", True),
        ("NULL_DEVICE", True),
        ("FIFO", False),
        ("SOCKET", False),
        ("UNKNOWN", False),
    ],
)
def test_output_progress_safety(endpoint_name: str, expected: bool) -> None:
    endpoint = getattr(tty.OutputEndpoint, endpoint_name)
    assert tty.output_allows_progress(endpoint) is expected


def test_real_regular_file_allows_progress(tmp_path: Path) -> None:
    with (tmp_path / "result.jsonl").open("w", encoding="utf-8") as stream:
        endpoint = tty.output_endpoint(stream)
    assert endpoint is tty.OutputEndpoint.REGULAR_FILE
    assert tty.output_allows_progress(endpoint) is True


def test_real_pipe_write_end_suppresses_progress() -> None:
    read_fd, write_fd = os.pipe()
    try:
        with os.fdopen(write_fd, "w", encoding="utf-8") as stream:
            write_fd = -1
            endpoint = tty.output_endpoint(stream)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
    assert endpoint is tty.OutputEndpoint.FIFO
    assert tty.output_allows_progress(endpoint) is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX socket descriptor classification")
def test_real_socket_backed_stream_suppresses_progress() -> None:
    left, right = socket.socketpair()
    try:
        with left.makefile("w", encoding="utf-8") as stream:
            endpoint = tty.output_endpoint(stream)
    finally:
        left.close()
        right.close()
    assert endpoint is tty.OutputEndpoint.SOCKET
    assert tty.output_allows_progress(endpoint) is False


def test_real_null_device_allows_progress() -> None:
    with open(os.devnull, "w", encoding="utf-8") as stream:
        reports_tty = stream.isatty()
        endpoint = tty.output_endpoint(stream)
    # The Windows CRT reports NUL as a TTY; the classifier deliberately trusts it.
    expected = tty.OutputEndpoint.TERMINAL if reports_tty else tty.OutputEndpoint.NULL_DEVICE
    assert endpoint is expected
    assert tty.output_allows_progress(endpoint) is True


class _FakeTtyWithoutDescriptor(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        raise AssertionError("a positive isatty result must not inspect the descriptor")


class _InvalidDescriptor(io.StringIO):
    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return -1


class _IsattyFailure(io.StringIO):
    def __init__(self, descriptor: int) -> None:
        super().__init__()
        self._descriptor = descriptor

    def isatty(self) -> bool:
        raise ValueError("isatty unavailable")

    def fileno(self) -> int:
        return self._descriptor


class _UnexpectedIsattyFailure(io.StringIO):
    def isatty(self) -> bool:
        raise RuntimeError("programming error")


@dataclass(frozen=True, slots=True)
class _StatWithoutRdev:
    st_mode: int


def test_fake_tty_without_fileno_is_terminal() -> None:
    assert tty.output_endpoint(_FakeTtyWithoutDescriptor()) is tty.OutputEndpoint.TERMINAL


def test_stdout_progress_gate_uses_the_current_rebound_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdout", _FakeTtyWithoutDescriptor())
    assert tty.stdout_allows_progress() is True
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert tty.stdout_allows_progress() is False


def test_unclassifiable_streams_fail_closed() -> None:
    closed = io.StringIO()
    closed.close()
    assert tty.output_endpoint(io.StringIO()) is tty.OutputEndpoint.UNKNOWN
    assert tty.output_endpoint(closed) is tty.OutputEndpoint.UNKNOWN
    assert tty.output_endpoint(_InvalidDescriptor()) is tty.OutputEndpoint.UNKNOWN


def test_isatty_failure_still_classifies_the_descriptor(tmp_path: Path) -> None:
    with (tmp_path / "result.jsonl").open("w", encoding="utf-8") as stream:
        endpoint = tty.output_endpoint(_IsattyFailure(stream.fileno()))
    assert endpoint is tty.OutputEndpoint.REGULAR_FILE


def test_fstat_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_fstat(_descriptor: int) -> os.stat_result:
        raise OSError("descriptor vanished")

    monkeypatch.setattr(os, "fstat", fail_fstat)
    assert tty.output_endpoint(_InvalidDescriptor()) is tty.OutputEndpoint.UNKNOWN


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (stat.S_IFREG, tty.OutputEndpoint.REGULAR_FILE),
        (stat.S_IFCHR, tty.OutputEndpoint.UNKNOWN),
    ],
)
def test_missing_rdev_metadata_is_portable(
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    expected: tty.OutputEndpoint,
) -> None:
    def fstat_without_rdev(_descriptor: int) -> _StatWithoutRdev:
        return _StatWithoutRdev(st_mode=mode)

    monkeypatch.setattr(os, "fstat", fstat_without_rdev)
    assert tty.output_endpoint(_InvalidDescriptor()) is expected


def test_unexpected_isatty_failure_is_not_swallowed() -> None:
    with pytest.raises(RuntimeError, match="programming error"):
        tty.output_endpoint(_UnexpectedIsattyFailure())
