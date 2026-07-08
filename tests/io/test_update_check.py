"""The npm-style update check: cached, gated, silent on every failure.

The check must add zero latency and zero noise: a daemon thread refreshes a
cache file at most once a day, and the NEXT run prints one stderr note. Every
gate (TTY, CI, kill switches, config) and every failure path is pinned here.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

from smartpipe.config.paths import config_path
from smartpipe.io.update_check import (
    CACHE_TTL_SECONDS,
    CachedCheck,
    begin_background_check,
    cache_path,
    check_allowed,
    emit_update_notice,
    fetch_latest_version,
    is_fresh,
    read_cache,
    start_background_check,
    write_cache,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_NOW = 1_800_000_000.0


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {"XDG_CONFIG_HOME": str(tmp_path), "APPDATA": str(tmp_path), **extra}


class _HostileEnv(dict[str, str]):
    """A mapping whose reads explode — proves the hooks never break a run."""

    def get(self, *args: object, **kwargs: object) -> str:
        del args, kwargs
        raise RuntimeError("boom")


def _join(thread: threading.Thread | None) -> None:
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()


# --- cache file ---------------------------------------------------------------


def test_cache_lives_next_to_the_config_file(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert cache_path(env).parent == config_path(env).parent
    assert cache_path(env).name == "update-check.json"


def test_cache_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "update-check.json"
    write_cache(path, CachedCheck(version="1.5.0", checked_at=_NOW))
    assert read_cache(path) == CachedCheck(version="1.5.0", checked_at=_NOW)


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "er" / "update-check.json"
    write_cache(path, CachedCheck(version="1.5.0", checked_at=_NOW))
    assert read_cache(path) is not None


def test_read_missing_file_is_none(tmp_path: Path) -> None:
    assert read_cache(tmp_path / "absent.json") is None


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[1, 2]",
        '{"version": 5, "checked_at": 1.0}',
        '{"version": "1.5.0", "checked_at": "yesterday"}',
        '{"version": "1.5.0", "checked_at": true}',
        '{"version": "1.5.0"}',
    ],
)
def test_read_rejects_broken_shapes_silently(tmp_path: Path, body: str) -> None:
    path = tmp_path / "update-check.json"
    path.write_text(body, encoding="utf-8")
    assert read_cache(path) is None


# --- staleness ----------------------------------------------------------------


def test_is_fresh_within_a_day() -> None:
    cached = CachedCheck(version="1.5.0", checked_at=_NOW)
    assert is_fresh(cached, _NOW + CACHE_TTL_SECONDS - 1)
    assert not is_fresh(cached, _NOW + CACHE_TTL_SECONDS)
    assert not is_fresh(None, _NOW)


# --- gates ---------------------------------------------------------------------


def test_allowed_by_default_at_a_tty(tmp_path: Path) -> None:
    assert check_allowed(_env(tmp_path), is_tty=True)


def test_never_without_a_tty(tmp_path: Path) -> None:
    assert not check_allowed(_env(tmp_path), is_tty=False)


@pytest.mark.parametrize("blocker", ["SMARTPIPE_NO_UPDATE_CHECK", "CI", "GITHUB_ACTIONS"])
def test_env_kill_switches(tmp_path: Path, blocker: str) -> None:
    assert not check_allowed(_env(tmp_path, **{blocker: "1"}), is_tty=True)
    assert check_allowed(_env(tmp_path, **{blocker: ""}), is_tty=True)  # empty = unset


def test_config_key_disables(tmp_path: Path) -> None:
    env = _env(tmp_path)
    config_path(env).parent.mkdir(parents=True)
    config_path(env).write_text("update-check = false\n", encoding="utf-8")
    assert not check_allowed(env, is_tty=True)


def test_config_key_on_allows(tmp_path: Path) -> None:
    env = _env(tmp_path)
    config_path(env).parent.mkdir(parents=True)
    config_path(env).write_text("update-check = true\n", encoding="utf-8")
    assert check_allowed(env, is_tty=True)


def test_broken_config_means_no_check(tmp_path: Path) -> None:
    env = _env(tmp_path)
    config_path(env).parent.mkdir(parents=True)
    config_path(env).write_text("update-check = ] nonsense", encoding="utf-8")
    assert not check_allowed(env, is_tty=True)


# --- the background refresh -----------------------------------------------------


def test_refresh_writes_the_cache(tmp_path: Path) -> None:
    env = _env(tmp_path)
    thread = start_background_check(env, is_tty=True, now=lambda: _NOW, fetch=lambda: "9.9.9")
    _join(thread)
    assert read_cache(cache_path(env)) == CachedCheck(version="9.9.9", checked_at=_NOW)


def test_fresh_cache_means_no_thread(tmp_path: Path) -> None:
    env = _env(tmp_path)
    write_cache(cache_path(env), CachedCheck(version="9.9.9", checked_at=_NOW))

    def fetch() -> str:
        raise AssertionError("a fresh cache must not refetch")

    assert start_background_check(env, is_tty=True, now=lambda: _NOW + 60, fetch=fetch) is None


def test_stale_cache_refetches(tmp_path: Path) -> None:
    env = _env(tmp_path)
    write_cache(cache_path(env), CachedCheck(version="9.9.8", checked_at=_NOW))
    later = _NOW + CACHE_TTL_SECONDS + 1
    thread = start_background_check(env, is_tty=True, now=lambda: later, fetch=lambda: "9.9.9")
    _join(thread)
    assert read_cache(cache_path(env)) == CachedCheck(version="9.9.9", checked_at=later)


def test_gated_off_means_no_thread(tmp_path: Path) -> None:
    env = _env(tmp_path, SMARTPIPE_NO_UPDATE_CHECK="1")
    assert start_background_check(env, is_tty=True, now=lambda: _NOW, fetch=lambda: "9") is None


def test_failed_fetch_writes_nothing(tmp_path: Path) -> None:
    env = _env(tmp_path)
    thread = start_background_check(env, is_tty=True, now=lambda: _NOW, fetch=lambda: None)
    _join(thread)
    assert read_cache(cache_path(env)) is None


def test_raising_fetch_stays_silent(tmp_path: Path) -> None:
    env = _env(tmp_path)

    def fetch() -> str:
        raise RuntimeError("the wire died")

    thread = start_background_check(env, is_tty=True, now=lambda: _NOW, fetch=fetch)
    _join(thread)
    assert read_cache(cache_path(env)) is None


def test_begin_wrapper_never_raises() -> None:
    assert begin_background_check(_HostileEnv(), is_tty=True) is None


def test_begin_wrapper_uses_real_gates_under_pytest(tmp_path: Path) -> None:
    # stderr is captured here, so the TTY gate turns the whole thing off.
    assert begin_background_check(_env(tmp_path)) is None


# --- the notice ------------------------------------------------------------------


def _notice_lines(env: Mapping[str, str], current: str, *, is_tty: bool = True) -> list[str]:
    lines: list[str] = []
    emit_update_notice(current, env, is_tty=is_tty, emit=lines.append)
    return lines


def test_notice_names_both_versions_and_the_fix(tmp_path: Path) -> None:
    env = _env(tmp_path)
    write_cache(cache_path(env), CachedCheck(version="9.9.9", checked_at=_NOW))
    assert _notice_lines(env, "1.4.0") == [
        "smartpipe 9.9.9 is available (you have 1.4.0) — run: smartpipe update"
    ]


def test_no_notice_when_current_is_latest(tmp_path: Path) -> None:
    env = _env(tmp_path)
    write_cache(cache_path(env), CachedCheck(version="1.4.0", checked_at=_NOW))
    assert _notice_lines(env, "1.4.0") == []


def test_no_notice_for_an_rc_ahead_of_stable(tmp_path: Path) -> None:
    env = _env(tmp_path)
    write_cache(cache_path(env), CachedCheck(version="1.3.1", checked_at=_NOW))
    assert _notice_lines(env, "1.4.0rc1") == []


def test_stable_release_of_the_running_rc_notifies(tmp_path: Path) -> None:
    env = _env(tmp_path)
    write_cache(cache_path(env), CachedCheck(version="1.4.0", checked_at=_NOW))
    assert _notice_lines(env, "1.4.0rc1") == [
        "smartpipe 1.4.0 is available (you have 1.4.0rc1) — run: smartpipe update"
    ]


def test_no_notice_without_a_cache(tmp_path: Path) -> None:
    assert _notice_lines(_env(tmp_path), "1.4.0") == []


def test_no_notice_without_a_tty(tmp_path: Path) -> None:
    env = _env(tmp_path)
    write_cache(cache_path(env), CachedCheck(version="9.9.9", checked_at=_NOW))
    assert _notice_lines(env, "1.4.0", is_tty=False) == []


def test_no_notice_when_disabled(tmp_path: Path) -> None:
    env = _env(tmp_path, SMARTPIPE_NO_UPDATE_CHECK="1")
    write_cache(cache_path(env), CachedCheck(version="9.9.9", checked_at=_NOW))
    assert _notice_lines(env, "1.4.0") == []


def test_notice_never_raises() -> None:
    emit_update_notice("1.4.0", _HostileEnv(), is_tty=True, emit=lambda _line: None)


# --- the PyPI fetch ---------------------------------------------------------------


class _ServeFn(Protocol):
    def __call__(self, body: bytes) -> str: ...


@pytest.fixture
def serve() -> Iterator[_ServeFn]:
    """A local HTTP server serving one fixed body — the offline stand-in for PyPI."""
    import http.server

    class _Handler(http.server.BaseHTTPRequestHandler):
        body = b""

        def do_GET(self) -> None:  # the stdlib names it
            self.send_response(200)
            self.end_headers()
            self.wfile.write(type(self).body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — stdlib signature
            del format, args

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def _serve(body: bytes) -> str:
        _Handler.body = body
        return f"http://127.0.0.1:{server.server_port}/pypi/smartpipe-cli/json"

    yield _serve
    server.shutdown()


def test_fetch_reads_info_version(serve: _ServeFn) -> None:
    url = serve(json.dumps({"info": {"version": "1.2.3"}}).encode())
    assert fetch_latest_version(url) == "1.2.3"


def test_fetch_malformed_body_is_none(serve: _ServeFn) -> None:
    assert fetch_latest_version(serve(b"<html>not json</html>")) is None
    assert fetch_latest_version(serve(b'{"info": {"version": 5}}')) is None
    assert fetch_latest_version(serve(b'{"no_info": true}')) is None


def test_fetch_connection_failure_is_none() -> None:
    assert fetch_latest_version("http://127.0.0.1:9/pypi/x/json", timeout=0.2) is None
