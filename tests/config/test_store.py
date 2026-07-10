from __future__ import annotations

import os
import re
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


def test_media_previews_round_trips_as_a_dashed_boolean(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(path, Config(media_previews=False))
    assert "media-previews = false" in path.read_text(encoding="utf-8")
    assert load_config(path).media_previews is False


def test_media_previews_is_unset_by_default(tmp_path: Path) -> None:
    assert load_config(tmp_path / "nope.toml").media_previews is None


def test_media_previews_wrong_type_is_a_setup_fault(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('media-previews = "sometimes"\n', encoding="utf-8")
    with pytest.raises(SetupFault) as excinfo:
        load_config(path)
    assert "media-previews" in str(excinfo.value)


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


# --- update-check (the auto update check's persisted kill switch) ---------------


def test_update_check_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(path, Config(update_check=False))
    assert load_config(path).update_check is False
    save_config(path, Config(update_check=True))
    assert load_config(path).update_check is True
    save_config(path, Config(update_check=None))
    assert load_config(path).update_check is None
    assert "update-check" not in path.read_text(encoding="utf-8")  # None = unset


def test_update_check_wrong_type_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('update-check = "yes"\n', encoding="utf-8")
    with pytest.raises(SetupFault, match="update-check"):
        load_config(path)


def test_role_keys_round_trip(tmp_path: Path) -> None:
    """Item 40: the two new roles persist as dashed keys and load back."""
    from dataclasses import replace

    path = tmp_path / "config.toml"
    save_config(
        path,
        replace(
            Config(),
            ocr_model="mistral/mistral-ocr-latest",
            media_embed_model="jina/jina-clip-v2",
        ),
    )
    raw = path.read_text(encoding="utf-8")
    assert 'ocr-model = "mistral/mistral-ocr-latest"' in raw
    assert 'media-embed-model = "jina/jina-clip-v2"' in raw
    loaded = load_config(path)
    assert loaded.ocr_model == "mistral/mistral-ocr-latest"
    assert loaded.media_embed_model == "jina/jina-clip-v2"


def test_role_keys_wrong_type_is_loud(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("ocr-model = 3\n", encoding="utf-8")
    with pytest.raises(SetupFault, match="ocr-model"):
        load_config(path)


# --- profiles are retired (item 30): ignored on read, warned once, stripped on save -------


def test_profile_keys_are_ignored_with_one_warn(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'profile = "openai"\nmodel = "gpt-4o"\n\n[profiles.work]\nmodel = "x"\n',
        encoding="utf-8",
    )
    warned: list[str] = []
    config = load_config(path, warn=warned.append)
    assert config.model == "gpt-4o"  # flat keys still load
    assert warned == ["profiles were removed - run smartpipe use"]


def test_profile_keys_stay_silent_without_a_warn_channel(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('profile = "yolo"\n', encoding="utf-8")
    assert load_config(path) == Config()  # never a crash, even for unknown names


def test_profile_tables_with_foreign_keys_never_crash(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[profiles.work]\napi-key = "sk-nope"\n', encoding="utf-8")
    assert load_config(path) == Config()


def test_clean_files_never_warn(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-4o"\n', encoding="utf-8")
    warned: list[str] = []
    assert load_config(path, warn=warned.append).model == "gpt-4o"
    assert warned == []


def test_save_strips_the_retired_profile_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'profile = "openai"\nmodel = "gpt-4o"\n\n[profiles.work]\nmodel = "x"\n',
        encoding="utf-8",
    )
    save_config(path, load_config(path))
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "profile" not in raw and "profiles" not in raw  # the rewrite is the cure


# --- the receipt (item 30): every save documents which door wrote the file ---------------

_HEADER = re.compile(r"^# stamped by: (smartpipe [a-z ]+) \(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z\)$")


def test_stamped_save_writes_the_provenance_header(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(path, Config(model="gpt-4o"), stamped_by="smartpipe use")
    first = path.read_text(encoding="utf-8").splitlines()[0]
    matched = _HEADER.match(first)
    assert matched is not None and matched.group(1) == "smartpipe use"
    assert load_config(path).model == "gpt-4o"  # the header never breaks parsing


def test_unstamped_rewrite_preserves_the_existing_header(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(path, Config(model="gpt-4o"), stamped_by="smartpipe use")
    header = path.read_text(encoding="utf-8").splitlines()[0]
    save_config(path, Config(model="gpt-4o", cache=False))  # e.g. a posture toggle
    assert path.read_text(encoding="utf-8").splitlines()[0] == header


def test_a_new_stamp_replaces_the_old_header(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(path, Config(model="gpt-4o"), stamped_by="smartpipe use")
    save_config(path, Config(model="gpt-4o"), stamped_by="smartpipe config")
    text = path.read_text(encoding="utf-8")
    assert text.count("# stamped by:") == 1
    assert "smartpipe config" in text.splitlines()[0]


def test_loading_tolerates_a_hand_written_header(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('# stamped by: hand (yesterday)\nmodel = "gpt-4o"\n', encoding="utf-8")
    assert load_config(path).model == "gpt-4o"


# --- model-capabilities (declared chips for self-hosted models) --------------------------


def test_model_capabilities_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model-capabilities = ["image", "audio"]\n', encoding="utf-8")
    config = load_config(path)
    assert config.model_capabilities == ("image", "audio")
    save_config(path, config)
    assert load_config(path).model_capabilities == ("image", "audio")


def test_model_capabilities_unset_and_cleared(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    assert load_config(path).model_capabilities is None
    path.write_text('model-capabilities = ["image"]\n', encoding="utf-8")
    from dataclasses import replace as _replace

    save_config(path, _replace(load_config(path), model_capabilities=None))
    assert "model-capabilities" not in path.read_text(encoding="utf-8")  # None = unset


def test_model_capabilities_wrong_type_is_loud(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model-capabilities = "image"\n', encoding="utf-8")
    with pytest.raises(SetupFault, match="model-capabilities"):
        load_config(path)


def test_model_capabilities_unknown_word_is_loud(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model-capabilities = ["video"]\n', encoding="utf-8")
    with pytest.raises(SetupFault, match="unknown capability"):
        load_config(path)
