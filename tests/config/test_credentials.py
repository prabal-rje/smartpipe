from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from smartpipe.config.credentials import (
    OAuthCredential,
    key_source,
    keys_path,
    load_api_key,
    load_oauth,
    mask_key,
    overlay_stored_keys,
    remove_api_key,
    remove_oauth,
    save_api_key,
    save_oauth,
    stored_api_keys,
)
from smartpipe.core.errors import SetupFault

CRED = OAuthCredential(
    access="at-1", refresh="rt-1", expires_ms=1_700_000_000_000, account_id="acct"
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_roundtrip_and_0600_on_create(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    save_oauth(path, "openai", CRED)
    if sys.platform != "win32":  # Windows mode bits are advisory
        assert _mode(path) == 0o600  # tokens are secrets from the first byte
    assert load_oauth(path, "openai") == CRED


def test_rewrite_keeps_0600_and_unknown_entries(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"future-provider": {"type": "oauth", "x": 1}}))
    save_oauth(path, "openai", CRED)
    if sys.platform != "win32":
        assert _mode(path) == 0o600
    data = json.loads(path.read_text())
    assert "future-provider" in data  # forward compatibility — never clobbered


def test_atomic_no_tmp_residue(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    save_oauth(path, "openai", CRED)
    assert [p.name for p in tmp_path.iterdir()] == ["auth.json"]


def test_missing_and_malformed_entries_are_none(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    assert load_oauth(path, "openai") is None  # no file
    path.write_text(json.dumps({"openai": {"type": "oauth", "access": "a"}}))  # no refresh
    assert load_oauth(path, "openai") is None


def test_corrupt_store_is_a_setup_fault(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text("{ not json")
    with pytest.raises(SetupFault, match="login store is unreadable"):
        load_oauth(path, "openai")


def test_remove(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    save_oauth(path, "openai", CRED)
    assert remove_oauth(path, "openai") is True
    assert load_oauth(path, "openai") is None
    assert remove_oauth(path, "openai") is False  # idempotent


# --- the API-key store (XDG data dir; opencode's record shape) --------------------------


def test_keys_path_xdg_data_dir() -> None:
    assert keys_path({"XDG_DATA_HOME": "/x"}, "darwin") == Path("/x/smartpipe/auth.json")
    assert keys_path({}, "linux") == Path.home() / ".local" / "share" / "smartpipe" / "auth.json"


def test_keys_path_windows_local_appdata() -> None:
    assert keys_path({"LOCALAPPDATA": "C:/loc"}, "win32") == Path("C:/loc/smartpipe/auth.json")
    fallback = keys_path({}, "win32")
    assert fallback == Path.home() / "AppData" / "Local" / "smartpipe" / "auth.json"


def test_api_key_roundtrip_record_shape_and_0600(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    save_api_key(path, "mistral", "mk-secret-123")
    if sys.platform != "win32":
        assert _mode(path) == 0o600  # keys are secrets from the first byte
    assert load_api_key(path, "mistral") == "mk-secret-123"
    data = json.loads(path.read_text())
    assert data["mistral"] == {"type": "api", "key": "mk-secret-123"}  # opencode's shape


def test_api_key_ignores_oauth_records_and_vice_versa(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    save_oauth(path, "openai", CRED)
    assert load_api_key(path, "openai") is None  # a login is not a key
    save_api_key(path, "anthropic", "sk-ant-1")
    assert load_oauth(path, "anthropic") is None  # a key is not a login


def test_stored_api_keys_skips_malformed_entries(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps(
            {
                "mistral": {"type": "api", "key": "mk-1"},
                "openai": {"type": "oauth", "access": "a"},
                "broken": {"type": "api"},
                "junk": 7,
            }
        )
    )
    assert stored_api_keys(path) == {"mistral": "mk-1"}


def test_stored_api_keys_missing_file_is_empty(tmp_path: Path) -> None:
    assert stored_api_keys(tmp_path / "auth.json") == {}


def test_remove_api_key(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    save_api_key(path, "mistral", "mk-1")
    assert remove_api_key(path, "mistral") is True
    assert load_api_key(path, "mistral") is None
    assert remove_api_key(path, "mistral") is False  # idempotent


# --- resolution order: env ALWAYS wins over the stored key ------------------------------


def test_overlay_fills_only_missing_env_vars() -> None:
    merged = overlay_stored_keys({"PATH": "/bin"}, {"mistral": "mk-1", "openai": "sk-1"})
    assert merged["MISTRAL_API_KEY"] == "mk-1"
    assert merged["OPENAI_API_KEY"] == "sk-1"
    assert merged["PATH"] == "/bin"  # untouched passthrough


def test_overlay_env_always_wins() -> None:
    merged = overlay_stored_keys({"MISTRAL_API_KEY": "env-key"}, {"mistral": "stored-key"})
    assert merged["MISTRAL_API_KEY"] == "env-key"


def test_overlay_gemini_respects_either_google_var() -> None:
    merged = overlay_stored_keys({"GOOGLE_API_KEY": "g-env"}, {"gemini": "g-stored"})
    assert "GEMINI_API_KEY" not in merged  # GOOGLE_API_KEY already covers gemini
    filled = overlay_stored_keys({}, {"gemini": "g-stored"})
    assert filled["GEMINI_API_KEY"] == "g-stored"


def test_overlay_unknown_provider_is_ignored() -> None:
    assert "FUTURE_API_KEY" not in overlay_stored_keys({}, {"future": "x"})


def test_key_source_reports_the_live_layer() -> None:
    assert key_source({"MISTRAL_API_KEY": "e"}, {"mistral": "s"}, "mistral") == "env"
    assert key_source({}, {"mistral": "s"}, "mistral") == "stored"
    assert key_source({}, {}, "mistral") is None


def test_mask_key_never_reveals_the_middle() -> None:
    assert mask_key("sk-abcdefgh9f2") == "sk-...9f2"
    assert mask_key("short") == "***"
    assert "abcdefgh" not in mask_key("sk-abcdefgh9f2")
