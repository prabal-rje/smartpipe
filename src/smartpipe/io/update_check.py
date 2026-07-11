"""The npm-style update check: notify-next-run, zero latency, silent failures.

Two halves, both called from ``cli/root.main`` and both wrapped so they can
NEVER break a run (this is the one place expected exceptions are deliberately
swallowed — a version nag must not cost anyone a pipeline):

- ``begin_background_check`` — at the start of a run, when every gate holds
  (stderr TTY, not CI, kill switches off, cache older than a day), a daemon
  thread GETs PyPI with a hard 2 s timeout and rewrites the cache atomically.
  The run never waits for it; if the process exits first, the next long run
  finishes the job.
- ``emit_update_notice`` — at the end of a run, the *cached* answer (usually
  from a previous run) becomes one stderr note when it names a version newer
  than the one executing.

Gates and staleness are pure functions (env/clock as parameters, the
``io/tty.supports_color`` pattern); the wrappers read real process state.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.config.paths import config_path
from smartpipe.core.jsontools import as_record, as_str
from smartpipe.core.versions import is_newer
from smartpipe.io import tty

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

__all__ = [
    "CACHE_TTL_SECONDS",
    "CachedCheck",
    "begin_background_check",
    "cache_path",
    "check_allowed",
    "emit_update_notice",
    "fetch_latest_version",
    "is_fresh",
    "read_cache",
    "start_background_check",
    "write_cache",
]

CACHE_TTL_SECONDS = 86_400.0  # one day — the npm cadence
_PYPI_URL = "https://pypi.org/pypi/smartpipe-cli/json"
_CI_VARS = ("CI", "GITHUB_ACTIONS")


@dataclass(frozen=True, slots=True)
class CachedCheck:
    version: str  # PyPI's info.version — the latest STABLE release
    checked_at: float  # epoch seconds


def cache_path(env: Mapping[str, str] | None = None) -> Path:
    """Next to the config file (D09): ``~/.config/smartpipe/update-check.json``."""
    return config_path(env).parent / "update-check.json"


def read_cache(path: Path) -> CachedCheck | None:
    """The cached answer, or ``None`` for missing/corrupt — never an error."""
    try:
        record = as_record(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
    if record is None:
        return None
    version = as_str(record.get("version"))
    checked_at = record.get("checked_at")
    if version is None or isinstance(checked_at, bool) or not isinstance(checked_at, int | float):
        return None
    return CachedCheck(version=version, checked_at=float(checked_at))


def write_cache(path: Path, check: CachedCheck) -> None:
    """Atomic, like the config store: a reader can never see a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"version": check.version, "checked_at": check.checked_at}))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def is_fresh(cached: CachedCheck | None, now: float) -> bool:
    return cached is not None and now - cached.checked_at < CACHE_TTL_SECONDS


def check_allowed(env: Mapping[str, str], *, is_tty: bool) -> bool:
    """Every kill switch, for the check AND the notice: stderr must be a TTY,
    CI never nags, ``SMARTPIPE_NO_UPDATE_CHECK`` wins, then the config key.
    ``--local-only`` (item 65d) silences it too as a conservative posture;
    this data-free PyPI ping is allowed by the local-execution contract."""
    from smartpipe.core.fence import local_only

    if local_only(env):
        return False
    if not is_tty:
        return False
    if env.get("SMARTPIPE_NO_UPDATE_CHECK", "").strip():
        return False
    if any(env.get(var, "").strip() for var in _CI_VARS):
        return False
    return _config_allows(env)


def _config_allows(env: Mapping[str, str]) -> bool:
    """``update-check = false`` disables; an unreadable config also means no —
    a broken file must surface through the command that needs it, never here."""
    from smartpipe.config.store import load_config

    try:
        return load_config(config_path(env)).update_check is not False
    except Exception:
        return False


def start_background_check(
    env: Mapping[str, str],
    *,
    is_tty: bool,
    now: Callable[[], float],
    fetch: Callable[[], str | None],
) -> threading.Thread | None:
    """Spawn the daemon refresh when the gates hold and the cache is stale."""
    if not check_allowed(env, is_tty=is_tty):
        return None
    path = cache_path(env)
    if is_fresh(read_cache(path), now()):
        return None

    def refresh() -> None:
        try:
            version = fetch()
            if version is not None:
                write_cache(path, CachedCheck(version=version, checked_at=now()))
        except Exception:  # nothing to tell: a failed check must never surface
            pass

    thread = threading.Thread(target=refresh, name="smartpipe-update-check", daemon=True)
    thread.start()
    return thread


def begin_background_check(
    env: Mapping[str, str] | None = None,
    *,
    is_tty: bool | None = None,
    now: Callable[[], float] | None = None,
    fetch: Callable[[], str | None] | None = None,
) -> threading.Thread | None:
    """The root-command hook: real process state in, and NOTHING ever raised."""
    try:
        return start_background_check(
            os.environ if env is None else env,
            is_tty=tty.stderr_is_tty() if is_tty is None else is_tty,
            now=time.time if now is None else now,
            fetch=(lambda: fetch_latest_version(_PYPI_URL)) if fetch is None else fetch,
        )
    except Exception:
        return None


def emit_update_notice(
    current_version: str,
    env: Mapping[str, str] | None = None,
    *,
    is_tty: bool | None = None,
    emit: Callable[[str], None] | None = None,
) -> None:
    """The end-of-run hook: one stderr note when the cache names a newer
    stable release. Same gates as the check; NOTHING ever raised."""
    from smartpipe.cli.screens import update_available
    from smartpipe.io import diagnostics

    try:
        resolved_env = os.environ if env is None else env
        resolved_tty = tty.stderr_is_tty() if is_tty is None else is_tty
        if not check_allowed(resolved_env, is_tty=resolved_tty):
            return
        cached = read_cache(cache_path(resolved_env))
        if cached is None or not is_newer(cached.version, current_version):
            return
        (diagnostics.note if emit is None else emit)(
            update_available(cached.version, current_version)
        )
    except Exception:  # a version nag must never break a run
        pass


def fetch_latest_version(url: str, timeout: float = 2.0) -> str | None:
    """GET PyPI's JSON and read ``info.version`` (the latest stable — PyPI
    never puts pre-releases there). urllib stays function-local: this runs on
    a background thread and must not tax the <150 ms startup budget."""
    from urllib.request import urlopen

    try:
        with urlopen(url, timeout=timeout) as response:
            payload: object = json.load(response)
    except Exception:
        return None
    info = as_record(payload)
    if info is None:
        return None
    record = as_record(info.get("info"))
    return None if record is None else as_str(record.get("version"))
