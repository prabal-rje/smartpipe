from __future__ import annotations

from pathlib import Path

import pytest

from sempipe.config.paths import config_path
from sempipe.config.store import Config, load_config, save_config
from sempipe.core.errors import SetupFault

# --- paths --------------------------------------------------------------------


def test_config_path_honors_xdg(tmp_path: Path) -> None:
    path = config_path(env={"XDG_CONFIG_HOME": str(tmp_path)}, platform="darwin")
    assert path == tmp_path / "sempipe" / "config.toml"


def test_config_path_defaults_to_dot_config_on_unix() -> None:
    path = config_path(env={}, platform="linux")
    assert path == Path.home() / ".config" / "sempipe" / "config.toml"


def test_config_path_uses_appdata_on_windows(tmp_path: Path) -> None:
    path = config_path(env={"APPDATA": str(tmp_path)}, platform="win32")
    assert path == tmp_path / "sempipe" / "config.toml"


# --- store --------------------------------------------------------------------


def test_missing_file_is_the_default_config(tmp_path: Path) -> None:
    assert load_config(tmp_path / "nope.toml") == Config()


def test_round_trip(tmp_path: Path) -> None:
    config = Config(
        model="ollama/qwen3:8b",
        embed_model="nomic-embed-text",
        concurrency=8,
        output="json",
    )
    path = tmp_path / "deep" / "config.toml"  # parent dirs created on save
    save_config(path, config)
    assert load_config(path) == config


def test_file_uses_dashed_keys_and_omits_unset_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(path, Config(embed_model="nomic-embed-text"))
    text = path.read_text(encoding="utf-8")
    assert 'embed-model = "nomic-embed-text"' in text
    assert "model =" not in text.replace("embed-model =", "")
    assert "concurrency" not in text


def test_partial_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-4o-mini"\n', encoding="utf-8")
    assert load_config(path) == Config(model="gpt-4o-mini")


def test_unknown_keys_are_ignored_for_forward_compat(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "x"\nfrom_the_future = 1\n', encoding="utf-8")
    assert load_config(path).model == "x"


def test_broken_toml_is_a_setup_fault_with_location_and_fix(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("model =\n", encoding="utf-8")
    with pytest.raises(SetupFault) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert message.startswith("error: config file has a syntax error")
    assert "line 1" in message
    assert "sempipe config" in message  # the fix is in the message


def test_wrong_value_type_is_a_setup_fault_naming_the_key(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('concurrency = "four"\n', encoding="utf-8")
    with pytest.raises(SetupFault) as excinfo:
        load_config(path)
    assert "concurrency" in str(excinfo.value)


def test_bool_is_not_a_valid_concurrency(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("concurrency = true\n", encoding="utf-8")
    with pytest.raises(SetupFault):
        load_config(path)
