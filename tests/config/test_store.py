from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from smartpipe.config.paths import config_path
from smartpipe.config.store import Config, load_config, save_config
from smartpipe.core.errors import SetupFault

# --- paths --------------------------------------------------------------------


def test_config_path_honors_xdg(tmp_path: Path) -> None:
    path = config_path(env={"XDG_CONFIG_HOME": str(tmp_path)}, platform="darwin")
    assert path == tmp_path / "smartpipe" / "config.toml"


def test_config_path_defaults_to_dot_config_on_unix() -> None:
    path = config_path(env={}, platform="linux")
    assert path == Path.home() / ".config" / "smartpipe" / "config.toml"


def test_config_path_uses_appdata_on_windows(tmp_path: Path) -> None:
    path = config_path(env={"APPDATA": str(tmp_path)}, platform="win32")
    assert path == tmp_path / "smartpipe" / "config.toml"


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
    assert "smartpipe config" in message  # the fix is in the message


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


# --- atomic writes + unknown-key preservation (DEFER-1, workstream 07) -------------


def test_save_preserves_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'model = "x"\nfuture-flag = true\n\n[some.table]\nnested = 1\n', encoding="utf-8"
    )
    save_config(path, Config(model="y"))
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert raw["model"] == "y"
    assert raw["future-flag"] is True  # a key we don't know survives verbatim
    assert raw["some"]["table"]["nested"] == 1


def test_save_removes_none_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "x"\nembed-model = "old"\n', encoding="utf-8")
    save_config(path, Config(model="x", embed_model=None))  # None = unset (pinned)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "embed-model" not in raw


def test_save_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.toml"
    replaces: list[tuple[str, str]] = []
    real_replace = os.replace

    def recording_replace(src: str | Path, dst: str | Path) -> None:
        replaces.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr("os.replace", recording_replace)
    save_config(path, Config(model="x"))
    assert len(replaces) == 1
    src, dst = replaces[0]
    assert Path(src).parent == path.parent  # same directory = same filesystem
    assert dst == str(path)  # the target is written ONLY via os.replace
    assert list(tmp_path.glob("*.tmp")) == []  # no residue after success


def test_save_cleans_temp_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.toml"

    def explode(document: object) -> str:
        raise RuntimeError("serializer down")

    monkeypatch.setattr("tomli_w.dumps", explode)
    with pytest.raises(RuntimeError):
        save_config(path, Config(model="x"))
    assert list(tmp_path.glob("*.tmp")) == []  # nothing left behind


def test_comments_are_lost_is_documented(tmp_path: Path) -> None:
    """Comments do not survive a rewrite — admitted in docs/reference/cli.md
    ("unknown keys are preserved, comments are not"); tomli-w cannot round-trip
    them and tomlkit stays outside the dependency budget. This test anchors the
    limitation so it can never become undocumented folklore."""
    path = tmp_path / "config.toml"
    path.write_text('# my note\nmodel = "x"\n', encoding="utf-8")
    save_config(path, Config(model="y"))
    assert "# my note" not in path.read_text(encoding="utf-8")


def test_save_refuses_to_overwrite_a_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("model =\n", encoding="utf-8")  # broken TOML = evidence
    with pytest.raises(SetupFault):
        save_config(path, Config(model="y"))
    assert path.read_text(encoding="utf-8") == "model =\n"  # untouched
