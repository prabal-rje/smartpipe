from __future__ import annotations

from pathlib import Path

import pytest

from smartpipe.cli.config_cmd import run_interactive_setup
from smartpipe.config.store import Config, load_config
from tests.conftest import RunCli


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("SMARTPIPE_MODEL", raising=False)
    monkeypatch.delenv("SMARTPIPE_EMBED_MODEL", raising=False)
    return tmp_path / "smartpipe" / "config.toml"


# --- config model / embed-model -----------------------------------------------


def test_set_model_writes_canonical_and_confirms(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "model", "gpt-5.4-mini"])
    assert code == 0
    assert out.strip() == "model set to openai/gpt-5.4-mini"
    assert load_config(config_home).model == "openai/gpt-5.4-mini"


def test_set_embed_model(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "embed-model", "nomic-embed-text"])
    assert code == 0
    assert out.strip() == "embed-model set to ollama/nomic-embed-text"
    assert load_config(config_home).embed_model == "ollama/nomic-embed-text"


def test_set_model_preserves_other_fields(run_cli: RunCli, config_home: Path) -> None:
    run_cli(["config", "embed-model", "nomic-embed-text"])
    run_cli(["config", "model", "ollama/qwen3:8b"])
    config = load_config(config_home)
    assert config.model == "ollama/qwen3:8b"
    assert config.embed_model == "ollama/nomic-embed-text"


def test_set_bad_model_is_a_usage_error(run_cli: RunCli, config_home: Path) -> None:
    code, _out, err = run_cli(["config", "model", "   "])
    assert code == 64
    assert "no model given" in err


# --- config show --------------------------------------------------------------


def test_show_reports_defaults(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "show"])
    assert code == 0
    lines = out.splitlines()
    assert lines[0].startswith("model")
    assert "(auto-detect)" in lines[0]
    assert "(default)" in lines[0]
    assert lines[-1].startswith("config file")


def test_show_reflects_a_saved_model(run_cli: RunCli, config_home: Path) -> None:
    run_cli(["config", "model", "ollama/qwen3:8b"])
    _code, out, _err = run_cli(["config", "show"])
    model_line = out.splitlines()[0]
    assert "ollama/qwen3:8b" in model_line
    assert "(config file)" in model_line


def test_show_env_origin(
    run_cli: RunCli, config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "gpt-5.4-mini")
    _code, out, _err = run_cli(["config", "show"])
    assert "(env)" in out.splitlines()[0]


# --- bare config (non-TTY) ----------------------------------------------------


def test_bare_config_without_tty_is_setup_fault(run_cli: RunCli, config_home: Path) -> None:
    code, _out, err = run_cli(["config"], stdin="")
    assert code == 2
    assert "interactive and needs a terminal" in err
    assert "smartpipe config model ollama/qwen3:8b" in err


# --- interactive setup (unit, injected I/O) -----------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.saved: Config | None = None
        self.said: list[str] = []

    def say(self, message: str) -> None:
        self.said.append(message)

    def save(self, config: Config) -> None:
        self.saved = config


async def _probe(*names: str) -> tuple[str, ...] | None:
    return names if names else None


async def test_interactive_with_ollama_saves_detected_defaults() -> None:
    rec = _Recorder()
    answers = {
        "Default model?": "ollama/qwen3:8b",
        "Embedding model?": "ollama/nomic-embed-text",
    }

    result = await run_interactive_setup(
        current=Config(),
        probe=lambda: _probe("nomic-embed-text", "qwen3:8b"),
        ask=lambda question, default: answers.get(question, default),
        confirm=lambda _question: True,
        say=rec.say,
        save=rec.save,
    )
    assert result.model == "ollama/qwen3:8b"
    assert result.embed_model == "ollama/nomic-embed-text"
    assert rec.saved == result
    assert any("found Ollama (2 models)" in line for line in rec.said)


async def test_interactive_default_answers_use_first_chat_and_embed() -> None:
    rec = _Recorder()
    seen_defaults: dict[str, str] = {}

    def ask(question: str, default: str) -> str:
        seen_defaults[question] = default
        return default  # user just hits Enter

    await run_interactive_setup(
        current=Config(),
        probe=lambda: _probe("nomic-embed-text", "llama3.2", "qwen3:8b"),
        ask=ask,
        confirm=lambda _q: True,
        say=rec.say,
        save=rec.save,
    )
    assert seen_defaults["Default model?"] == "ollama/llama3.2"  # first non-embed
    assert seen_defaults["Embedding model?"] == "ollama/nomic-embed-text"


async def test_interactive_without_ollama_offers_cloud() -> None:
    rec = _Recorder()

    result = await run_interactive_setup(
        current=Config(),
        probe=lambda: _probe(),
        ask=lambda _question, default: default,
        confirm=lambda _q: True,
        say=rec.say,
        save=rec.save,
    )
    assert result.model == "openai/gpt-5.4-mini"
    assert any("no local chat model found" in line for line in rec.said)


async def test_interactive_with_only_embed_models_does_not_propose_embed_as_chat() -> None:
    # regression (adversarial review): if Ollama has ONLY embedding models,
    # the chat default must not be an embedding model — fall to the cloud prompt.
    rec = _Recorder()
    seen_defaults: dict[str, str] = {}

    def ask(question: str, default: str) -> str:
        seen_defaults[question] = default
        return default

    result = await run_interactive_setup(
        current=Config(),
        probe=lambda: _probe("nomic-embed-text", "mxbai-embed-large"),
        ask=ask,
        confirm=lambda _q: True,
        say=rec.say,
        save=rec.save,
    )
    assert result.model == "openai/gpt-5.4-mini"  # not an embedding model
    assert seen_defaults["Embedding model?"] == "ollama/nomic-embed-text"


async def test_interactive_decline_does_not_save() -> None:
    rec = _Recorder()
    await run_interactive_setup(
        current=Config(),
        probe=lambda: _probe("qwen3:8b"),
        ask=lambda _question, default: default,
        confirm=lambda _q: False,
        say=rec.say,
        save=rec.save,
    )
    assert rec.saved is None
    assert any("Not saved" in line for line in rec.said)


def test_set_embed_model_mistral(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "embed-model", "mistral-embed"])
    assert code == 0
    assert out.strip() == "embed-model set to mistral/mistral-embed"
    assert load_config(config_home).embed_model == "mistral/mistral-embed"


def test_profile_switch_and_list(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    code, _out, err = run_cli(["config", "profile", "local"])
    assert code == 0
    assert "profile 'local' active — model: ollama/gemma-4-e2b" in err
    code, out, _err = run_cli(["config", "profile"])
    assert code == 0
    assert "* local" in out
    assert "  openai" in out


def test_profile_unknown_name_lists_known(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    code, _out, err = run_cli(["config", "profile", "yolo"])
    assert code == 2
    assert "profile 'yolo' doesn't exist" in err


async def test_wizard_offers_profiles_first_on_a_fresh_setup(tmp_path: Path) -> None:
    from smartpipe.cli.config_cmd import run_interactive_setup
    from smartpipe.config.store import Config

    said: list[str] = []
    saved: list[Config] = []

    async def probe() -> tuple[str, ...] | None:
        raise AssertionError("picking a preset must not probe anything")

    result = await run_interactive_setup(
        current=Config(),
        probe=probe,
        ask=lambda question, default: "3",  # local
        confirm=lambda question: True,
        say=said.append,
        save=saved.append,
    )
    assert result.profile == "local"
    assert saved and saved[0].profile == "local"
    assert any("gemma-4-e2b" in line for line in said)
    assert any("smartpipe doctor" in line for line in said)
