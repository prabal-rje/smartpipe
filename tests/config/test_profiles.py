"""Config profiles (D30): bundles of existing keys, env-selectable, presets shipped."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sempipe.config.store import BUILTIN_PROFILES, load_config, profile_names
from sempipe.core.errors import SetupFault

if TYPE_CHECKING:
    from pathlib import Path


def test_active_profile_supplies_the_base(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('profile = "openai"\n', encoding="utf-8")
    config = load_config(path)
    assert config.model == "gpt-4o-mini"  # the shipped preset
    assert config.embed_model == "text-embedding-3-small"
    assert config.profile == "openai"


def test_flat_keys_beat_the_profile(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('profile = "openai"\nmodel = "gpt-4o"\n', encoding="utf-8")
    config = load_config(path)
    assert config.model == "gpt-4o"  # direct set wins
    assert config.embed_model == "text-embedding-3-small"  # the profile still fills gaps


def test_user_defined_profile_overrides_the_preset(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'profile = "local"\n\n[profiles.local]\nmodel = "ollama/qwen3:8b"\n',
        encoding="utf-8",
    )
    assert load_config(path).model == "ollama/qwen3:8b"


def test_env_var_selects_a_one_off_profile(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('profile = "openai"\n', encoding="utf-8")
    config = load_config(path, {"SEMPIPE_PROFILE": "local"})
    assert config.model == "ollama/gemma-4-e2b"  # the multimodal local default


def test_unknown_profile_is_a_loud_setup_fault(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('profile = "yolo"\n', encoding="utf-8")
    with pytest.raises(SetupFault, match="profile 'yolo' doesn't exist"):
        load_config(path)


def test_profile_with_foreign_keys_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('profile = "work"\n\n[profiles.work]\napi-key = "sk-nope"\n', encoding="utf-8")
    with pytest.raises(SetupFault, match="unknown key 'api-key'"):
        load_config(path)  # secrets never live in profiles (D24/D30)


def test_profile_names_union_presets_and_defined(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[profiles.work]\nmodel = "gpt-4o"\n', encoding="utf-8")
    assert profile_names(path) == ("gemini", "local", "openai", "work")
    assert set(BUILTIN_PROFILES) == {"openai", "gemini", "local"}
