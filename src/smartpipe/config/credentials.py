"""The credential stores (plan/decisions.md D19 + the auth-login wave).

Two small files, both mode 0600, both removable with ``smartpipe auth logout``:

- ``auth.json`` beside the config (``~/.config/smartpipe``): ChatGPT *login
  tokens* only — the original D19 store, unchanged.
- ``auth.json`` in the data dir (``~/.local/share/smartpipe``): API keys that
  ``smartpipe auth login`` stored, as typed per-provider records
  ``{"type": "api", "key": ...}`` (opencode's shape). Separate files keep an
  OpenAI key and a ChatGPT login coexisting under the same provider name.

Resolution order everywhere: flag > environment > stored key > nothing — an
environment variable ALWAYS wins over a stored key (``overlay_stored_keys``).
Unknown provider entries are preserved on rewrite (forward compatibility,
same spirit as the config store).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from smartpipe.config.paths import human_path
from smartpipe.core.errors import SetupFault
from smartpipe.core.jsontools import as_record, as_str

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "KEY_ENVS",
    "OAuthCredential",
    "credentials_path",
    "key_source",
    "keys_path",
    "load_api_key",
    "load_oauth",
    "mask_key",
    "overlay_stored_keys",
    "remove_api_key",
    "remove_oauth",
    "save_api_key",
    "save_oauth",
    "stored_api_keys",
]

_MODE = 0o600

# provider → the environment variables that carry its key, first one canonical.
# Any of them set (non-blank) means the environment owns that provider.
KEY_ENVS: Mapping[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "jina": ("JINA_API_KEY",),
}


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
    return _remove_entry(path, provider)


# --- the API-key store (data-dir auth.json) ----------------------------------------------


def keys_path(env: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    """Where ``auth login`` keeps API keys: the XDG *data* dir — user secrets
    that outlive a cache but are not configuration (opencode's location)."""
    import sys

    resolved_env = os.environ if env is None else env
    resolved_platform = sys.platform if platform is None else platform
    if resolved_platform == "win32":
        local = resolved_env.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
    else:
        xdg = resolved_env.get("XDG_DATA_HOME", "").strip()
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "smartpipe" / "auth.json"


def load_api_key(path: Path, provider: str) -> str | None:
    entry = as_record(_read_raw(path).get(provider))
    if entry is None or entry.get("type") != "api":
        return None
    return as_str(entry.get("key"))


def save_api_key(path: Path, provider: str, key: str) -> None:
    data = _read_raw(path)
    data[provider] = {"type": "api", "key": key}
    _write_raw(path, data)


def remove_api_key(path: Path, provider: str) -> bool:
    """Delete one provider's stored key; True if something was removed."""
    return _remove_entry(path, provider)


def stored_api_keys(path: Path) -> dict[str, str]:
    """Every well-formed ``{"type": "api"}`` record — provider → key."""
    keys: dict[str, str] = {}
    for provider, value in _read_raw(path).items():
        entry = as_record(value)
        if entry is None or entry.get("type") != "api":
            continue
        key = as_str(entry.get("key"))
        if key:
            keys[provider] = key
    return keys


def overlay_stored_keys(environ: Mapping[str, str], stored: Mapping[str, str]) -> dict[str, str]:
    """The resolution order, materialized: start from the environment and fill
    each provider's canonical key variable from the store ONLY when none of
    that provider's variables are set — the environment always wins."""
    merged = dict(environ)
    for provider, key in stored.items():
        env_vars = KEY_ENVS.get(provider)
        if env_vars is None:
            continue  # a store from a future smartpipe — leave it alone
        if any(merged.get(var, "").strip() for var in env_vars):
            continue
        merged[env_vars[0]] = key
    return merged


def key_source(
    environ: Mapping[str, str], stored: Mapping[str, str], provider: str
) -> Literal["env", "stored"] | None:
    """Which layer a provider's key comes from right now — for ``auth list``."""
    env_vars = KEY_ENVS.get(provider, ())
    if any(environ.get(var, "").strip() for var in env_vars):
        return "env"
    if provider in stored:
        return "stored"
    return None


def mask_key(key: str) -> str:
    """``sk-...9f2`` — enough to recognize a key, never enough to use one."""
    if len(key) < 10:
        return "***"
    return f"{key[:3]}...{key[-3:]}"


def _remove_entry(path: Path, provider: str) -> bool:
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
        if hasattr(os, "fchmod"):  # POSIX: 0600 from the first byte
            os.fchmod(fd, _MODE)
        else:  # Windows has no fd-chmod; ACLs scope the profile dir instead
            os.chmod(tmp, _MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp, path)  # atomic on POSIX and Windows (same volume)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):  # replace preserves the temp's mode, but be sure
        os.chmod(path, _MODE)
