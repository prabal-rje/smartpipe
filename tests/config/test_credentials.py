from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING

import pytest

from sempipe.config.credentials import OAuthCredential, load_oauth, remove_oauth, save_oauth
from sempipe.core.errors import SetupFault

if TYPE_CHECKING:
    from pathlib import Path

CRED = OAuthCredential(
    access="at-1", refresh="rt-1", expires_ms=1_700_000_000_000, account_id="acct"
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_roundtrip_and_0600_on_create(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    save_oauth(path, "openai", CRED)
    assert _mode(path) == 0o600  # tokens are secrets from the first byte
    assert load_oauth(path, "openai") == CRED


def test_rewrite_keeps_0600_and_unknown_entries(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"future-provider": {"type": "oauth", "x": 1}}))
    save_oauth(path, "openai", CRED)
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
