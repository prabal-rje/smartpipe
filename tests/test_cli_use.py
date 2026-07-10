"""``smartpipe use`` / ``smartpipe using`` (item 30): the setup door and the origins view."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from smartpipe.config.store import Config, load_config, save_config
from tests.conftest import RunCli


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
    for var in (
        "SMARTPIPE_MODEL",
        "SMARTPIPE_EMBED_MODEL",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
        "MISTRAL_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "smartpipe" / "config.toml"


# --- one-shot stamps ---------------------------------------------------------------


def test_use_provider_stamps_the_full_bundle(
    run_cli: RunCli, config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    code, out, _err = run_cli(["use", "gemini"])
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "  ✓ model gemini/gemini-3.1-flash-lite"
    assert lines[1] == "  ✓ embed-model gemini/gemini-embedding-001  (paired with gemini)"
    assert lines[2] == (
        "  ✓ allow-captions on"
        "  (cloud pick = consent for paid media conversions; disclosed per row)"
    )
    assert lines[-2] == "  Saved. Try it:"
    assert lines[-1] == '    echo "hello world" | smartpipe map "translate to Spanish"'
    saved = load_config(config_home)
    assert saved.model == "gemini/gemini-3.1-flash-lite"
    assert saved.embed_model == "gemini/gemini-embedding-001"
    assert saved.allow_captions is True


def test_use_writes_the_receipt_header(
    run_cli: RunCli, config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    run_cli(["use", "gemini"])
    first = config_home.read_text(encoding="utf-8").splitlines()[0]
    assert re.fullmatch(r"# stamped by: smartpipe use \(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z\)", first), (
        first
    )


def test_use_model_ref_stamps_model_plus_pair(
    run_cli: RunCli, config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    code, out, _err = run_cli(["use", "gpt-5.4-mini"])
    assert code == 0
    assert "✓ model openai/gpt-5.4-mini" in out
    assert "✓ embed-model openai/text-embedding-3-small" in out
    assert load_config(config_home).embed_model == "openai/text-embedding-3-small"


def test_use_rerun_refreshes_a_drifted_bundle(
    run_cli: RunCli, config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    run_cli(["use", "gemini"])
    save_config(config_home, Config(model="gemini/gemini-3.1-flash-lite", embed_model="ollama/x"))
    code, _out, _err = run_cli(["use", "gemini"])  # the drift cure
    assert code == 0
    assert load_config(config_home).embed_model == "gemini/gemini-embedding-001"


def test_use_preserves_unrelated_settings(
    run_cli: RunCli, config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_config(config_home, Config(cache=False, ocr_model="mistral/mistral-ocr-latest"))
    monkeypatch.setenv("MISTRAL_API_KEY", "mk")
    run_cli(["use", "mistral"])
    saved = load_config(config_home)
    assert saved.cache is False
    assert saved.ocr_model == "mistral/mistral-ocr-latest"


def test_use_without_a_key_refuses_and_stamps_nothing(run_cli: RunCli, config_home: Path) -> None:
    code, out, err = run_cli(["use", "gemini"])
    assert code == 2
    assert out == ""
    assert "gemini needs an API key" in err
    assert "smartpipe auth login gemini" in err
    assert "Then rerun: smartpipe use gemini" in err
    assert not config_home.exists()  # never a partial stamp


def test_use_embedding_ref_is_refused(run_cli: RunCli, config_home: Path) -> None:
    code, _out, err = run_cli(["use", "jina/jina-clip-v2"])
    assert code == 2
    assert "embedding model, not a chat model" in err


def test_use_ollama_with_nothing_listening_refuses(
    run_cli: RunCli, config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")  # the discard port — refused
    code, _out, err = run_cli(["use", "ollama"])
    assert code == 2
    assert "ollama serve" in err
    assert "Then rerun: smartpipe use ollama" in err


def test_bare_use_without_tty_is_a_setup_fault(run_cli: RunCli, config_home: Path) -> None:
    code, _out, err = run_cli(["use"], stdin="")
    assert code == 2
    assert "'smartpipe use' is interactive and needs a terminal" in err
    assert "smartpipe use gemini" in err  # the no-prompt path is in the screen


# --- the origins view ---------------------------------------------------------------


def test_using_shows_effective_settings_with_origins(
    run_cli: RunCli, config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    run_cli(["use", "gemini"])
    code, out, _err = run_cli(["using"])
    assert code == 0
    lines = out.splitlines()
    assert lines[0].startswith("model")
    assert "gemini/gemini-3.1-flash-lite" in lines[0]
    assert "(config file)" in lines[0]
    assert lines[-1].startswith("config file")


def test_config_show_is_an_alias_of_using(run_cli: RunCli, config_home: Path) -> None:
    _code, from_using, _err = run_cli(["using"])
    _code, from_show, _err = run_cli(["config", "show"])
    assert from_using == from_show


def test_using_warns_once_about_retired_profiles(run_cli: RunCli, config_home: Path) -> None:
    config_home.parent.mkdir(parents=True, exist_ok=True)
    config_home.write_text('profile = "openai"\nmodel = "gpt-4o"\n', encoding="utf-8")
    code, out, err = run_cli(["using"])
    assert code == 0
    assert "profiles were removed - run smartpipe use" in err
    assert "gpt-4o" in out  # flat keys still count


# --- the retired surfaces stay dead --------------------------------------------------


@pytest.mark.parametrize(
    "removed",
    [
        ["config", "model", "gpt-5.4-mini"],
        ["config", "embed-model", "nomic-embed-text"],
        ["config", "stt-model", "openai/whisper-1"],
        ["config", "ocr-model", "mistral-ocr-latest"],
        ["config", "media-embed-model", "jina/jina-clip-v2"],
        ["config", "profile", "local"],
    ],
)
def test_retired_config_subcommands_are_usage_errors(
    run_cli: RunCli, config_home: Path, removed: list[str]
) -> None:
    code, _out, _err = run_cli(removed, stdin="")
    assert code == 64
    assert not config_home.exists()
