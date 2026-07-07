"""The OAuth token store (plan/decisions.md D19) — ``auth.json`` beside the config.

The narrowest amendment to "nothing stored": one file, mode 0600, one purpose
(login tokens that must persist to refresh), removable with ``smartpipe auth
logout``. API keys never live here. Unknown provider entries are preserved on
rewrite (forward compatibility, same spirit as the config store).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from smartpipe.config.paths import human_path
from smartpipe.core.errors import SetupFault
from smartpipe.core.jsontools import as_record, as_str

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["OAuthCredential", "credentials_path", "load_oauth", "remove_oauth", "save_oauth"]

_MODE = 0o600


@dataclass(frozen=True, slots=True)
class OAuthCredential:
    access: str
    refresh: str
    expires_ms: int  # epoch milliseconds, matching the wire's convention
    account_id: str | None = None


def credentials_path(env: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    from smartpipe.config.paths import config_path

    return config_path(env, platform).parent / "auth.json"


def load_oauth(path: Path, provider: str) -> OAuthCredential | None:
    record = _read_raw(path).get(provider)
    entry = as_record(record)
    if entry is None or entry.get("type") != "oauth":
        return None
    access = as_str(entry.get("access"))
    refresh = as_str(entry.get("refresh"))
    expires = entry.get("expires")
    if access is None or refresh is None or not isinstance(expires, int):
        return None
    return OAuthCredential(
        access=access,
        refresh=refresh,
        expires_ms=expires,
        account_id=as_str(entry.get("account_id")),
    )


def save_oauth(path: Path, provider: str, credential: OAuthCredential) -> None:
    data = _read_raw(path)
    entry: dict[str, object] = {
        "type": "oauth",
        "access": credential.access,
        "refresh": credential.refresh,
        "expires": credential.expires_ms,
    }
    if credential.account_id is not None:
        entry["account_id"] = credential.account_id
    data[provider] = entry
    _write_raw(path, data)


def remove_oauth(path: Path, provider: str) -> bool:
    """Delete one provider's entry; True if something was removed."""
    data = _read_raw(path)
    if provider not in data:
        return False
    del data[provider]
    _write_raw(path, data)
    return True


def _read_raw(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SetupFault(
            f"error: the login store is unreadable\n"
            f"  {human_path(path)}: {exc}\n"
            "  Fix the file, or remove it and log in again: smartpipe auth login"
        ) from exc
    record = as_record(parsed)
    return dict(record) if record is not None else {}


def _write_raw(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.fchmod(fd, _MODE)  # 0600 from the first byte, not after the fact
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp, path)  # atomic on POSIX and Windows (same volume)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):  # replace preserves the temp's mode, but be sure
        os.chmod(path, _MODE)
