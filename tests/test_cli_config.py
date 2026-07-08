from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from smartpipe.cli.config_cmd import run_provider_picker
from smartpipe.config.picker import ProbeChip
from smartpipe.config.store import Config, load_config
from tests.conftest import RunCli

if TYPE_CHECKING:
    from collections.abc import Mapping

_NOW = 1_751_900_000.0  # a fixed clock — chips date against it


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
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


def test_media_previews_off_persists_the_kill_switch(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "media-previews", "off"])
    assert code == 0
    assert "media-previews off" in out
    assert load_config(config_home).media_previews is False


def test_media_previews_on_turns_it_back(run_cli: RunCli, config_home: Path) -> None:
    run_cli(["config", "media-previews", "off"])
    code, out, _err = run_cli(["config", "media-previews", "on"])
    assert code == 0
    assert "media-previews on" in out
    assert load_config(config_home).media_previews is True


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


# --- the provider-first picker (unit, injected I/O) -----------------------------


class _Recorder:
    def __init__(self) -> None:
        self.saved: Config | None = None
        self.said: list[str] = []

    def say(self, message: str) -> None:
        self.said.append(message)

    def save(self, config: Config) -> None:
        self.saved = config


class _Menu:
    """A scripted chooser that records every menu it was shown."""

    def __init__(self, picks: list[int | None]) -> None:
        self.picks = picks
        self.shown: list[tuple[str, tuple[str, ...], int]] = []

    def __call__(self, title: str, labels: tuple[str, ...], start: int) -> int | None:
        self.shown.append((title, labels, start))
        return self.picks.pop(0)


async def _run_picker(
    *,
    current: Config | None = None,
    env: Mapping[str, str] | None = None,
    tags: tuple[str, ...] | None = None,
    login: bool = False,
    catalogs: Mapping[str, tuple[str, ...] | None] | None = None,
    chips: Mapping[str, ProbeChip] | None = None,
    picks: list[int | None] | None = None,
    answers: dict[str, str] | None = None,
    confirms: dict[str, bool] | None = None,
) -> tuple[Config, _Recorder, _Menu]:
    rec = _Recorder()
    menu = _Menu(picks if picks is not None else [])
    held_answers = answers or {}
    held_confirms = confirms or {}

    async def probe() -> tuple[str, ...] | None:
        return tags

    async def fetch(provider: str) -> tuple[str, ...] | None:
        return (catalogs or {}).get(provider)

    def ask(question: str, default: str) -> str:
        return held_answers.get(question, default)

    def confirm(question: str, default: bool) -> bool:
        return held_confirms.get(question, default)

    result = await run_provider_picker(
        current=current if current is not None else Config(),
        env=env or {},
        probe=probe,
        login=lambda: login,
        fetch_catalog=fetch,
        chips=chips or {},
        now=_NOW,
        choose=menu,
        ask=ask,
        confirm=confirm,
        say=rec.say,
        save=rec.save,
    )
    return result, rec, menu


async def test_picker_ollama_pick_pairs_a_detected_local_embedder() -> None:
    result, rec, menu = await _run_picker(
        tags=("nomic-embed-text", "llava", "qwen3:8b"),
        picks=[0, 0],  # provider: ollama · model: llava (family-preferred start)
    )
    assert result.model == "ollama/llava"
    assert result.embed_model == "ollama/nomic-embed-text"  # detected tag wins
    assert rec.saved == result
    provider_title, provider_labels, _start = menu.shown[0]
    assert provider_title == "Pick a provider:"
    assert provider_labels[0].startswith("ollama")
    assert "3 local models" in provider_labels[0]
    model_title, model_labels_shown, model_start = menu.shown[1]
    assert model_title == "Pick a model (ollama):"
    assert model_labels_shown[0] == "ollama/llava"  # embedders never offered as chat
    assert model_labels_shown[-1].startswith("type a model name instead")
    assert model_start == 0  # llava is the family-preferred cursor start
    assert any("paired with ollama" in line for line in rec.said)
    assert rec.said[-1].strip().endswith('smartpipe map "translate to Spanish"')


async def test_picker_openai_catalog_pick_and_pairing() -> None:
    result, rec, _menu = await _run_picker(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini", "o4-mini")},
        picks=[0, 0],
    )
    assert result.model == "openai/gpt-5.4-mini"
    assert result.embed_model == "openai/text-embedding-3-small"
    assert rec.saved == result
    assert any("✓ model openai/gpt-5.4-mini" in line for line in rec.said)
    assert any("embed-model openai/text-embedding-3-small" in line for line in rec.said)


async def test_picker_never_overwrites_a_deliberate_embedder() -> None:
    result, rec, _menu = await _run_picker(
        current=Config(embed_model="jina/jina-clip-v2"),
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[0, 0],
    )
    assert result.embed_model == "jina/jina-clip-v2"  # user intent outranks the pairing
    assert not any("paired" in line for line in rec.said)


async def test_picker_repairs_a_previously_paired_embedder() -> None:
    result, _rec, _menu = await _run_picker(
        current=Config(embed_model="openai/text-embedding-3-small"),
        env={"GEMINI_API_KEY": "g"},
        catalogs={"gemini": ("gemini-3.1-flash-lite",)},
        picks=[0, 0],
    )
    assert result.model == "gemini/gemini-3.1-flash-lite"
    assert result.embed_model == "gemini/gemini-embedding-001"  # the old pair moves with us


async def test_picker_catalog_failure_degrades_to_typed_input() -> None:
    result, rec, menu = await _run_picker(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={},  # fetch returns None — the 403/timeout path
        picks=[0],
    )
    assert result.model == "openai/gpt-5.4-mini"  # the provider-prefixed example default
    assert len(menu.shown) == 1  # no model menu — typed input took over
    assert any("couldn't fetch the live catalog" in line for line in rec.said)
    assert any("openai/gpt-5.4-mini" in line and "OPENAI_API_KEY" in line for line in rec.said)


async def test_picker_type_it_entry_routes_to_typed_input() -> None:
    result, _rec, menu = await _run_picker(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[0, 1],  # the last menu entry is "type a model name instead…"
        answers={"Default model?": "o4-mini"},
    )
    assert result.model == "openai/o4-mini"
    assert menu.shown[1][1][-1].startswith("type a model name instead")


async def test_picker_typed_junk_twice_is_a_usage_fault() -> None:
    from smartpipe.core.errors import UsageFault

    with pytest.raises(UsageFault):
        await _run_picker(
            env={"OPENAI_API_KEY": "sk-x"},
            catalogs={},
            picks=[0],
            answers={
                "Default model?": " ",
                "Default model? (that wasn't a model ref: no model given — "
                "try: --model ollama/qwen3:8b)": " ",
            },
        )


async def test_picker_backup_question_loops_the_picker_once() -> None:
    result, rec, menu = await _run_picker(
        env={"OPENAI_API_KEY": "sk-x", "GEMINI_API_KEY": "g"},
        catalogs={"openai": ("gpt-5.4-mini",), "gemini": ("gemini-3.1-flash-lite",)},
        picks=[0, 0, 1, 0],  # provider · model · backup provider · backup model
        confirms={"Add a backup model for provider outages?": True},
    )
    assert result.model == "openai/gpt-5.4-mini"
    assert result.fallback_model == "gemini/gemini-3.1-flash-lite"
    assert menu.shown[2][0] == "Pick a backup provider:"
    assert any("fallback-model gemini/gemini-3.1-flash-lite" in line for line in rec.said)


async def test_picker_backup_defaults_to_no() -> None:
    result, _rec, menu = await _run_picker(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[0, 0],
    )
    assert result.fallback_model is None
    assert len(menu.shown) == 2  # the picker never looped


async def test_picker_backup_refuses_an_embedder() -> None:
    result, rec, _menu = await _run_picker(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[0, 0, 0, 1],  # backup goes through "type it instead"
        answers={"Backup model?": "text-embedding-3-small"},
        confirms={"Add a backup model for provider outages?": True},
    )
    assert result.fallback_model is None
    assert any("embeds" in line and "must chat" in line for line in rec.said)


async def test_picker_cancel_at_the_provider_menu_saves_nothing() -> None:
    current = Config(model="ollama/qwen3:8b")
    result, rec, _menu = await _run_picker(
        current=current, env={"OPENAI_API_KEY": "sk-x"}, picks=[None]
    )
    assert result == current
    assert rec.saved is None
    assert any("Not saved" in line for line in rec.said)


async def test_picker_decline_save_stamps_nothing_but_still_offers_completions() -> None:
    offered: list[bool] = []
    rec = _Recorder()
    menu = _Menu([0, 0])

    async def probe() -> tuple[str, ...] | None:
        return None

    async def fetch(_provider: str) -> tuple[str, ...] | None:
        return ("gpt-5.4-mini",)

    result = await run_provider_picker(
        current=Config(),
        env={"OPENAI_API_KEY": "sk-x"},
        probe=probe,
        login=lambda: False,
        fetch_catalog=fetch,
        chips={},
        now=_NOW,
        choose=menu,
        ask=lambda _q, default: default,
        confirm=lambda question, default: False if question == "Save to config?" else default,
        say=rec.say,
        save=rec.save,
        offer_completions=lambda: offered.append(True),
    )
    assert result.model == "openai/gpt-5.4-mini"  # returned, but…
    assert rec.saved is None  # …never written
    assert any("Not saved" in line for line in rec.said)
    assert offered == [True]


async def test_picker_completions_come_before_the_try_it_bait() -> None:
    rec = _Recorder()
    menu = _Menu([0, 0])

    async def probe() -> tuple[str, ...] | None:
        return ("llava",)

    async def fetch(_provider: str) -> tuple[str, ...] | None:
        return None

    await run_provider_picker(
        current=Config(),
        env={},
        probe=probe,
        login=lambda: False,
        fetch_catalog=fetch,
        chips={},
        now=_NOW,
        choose=menu,
        ask=lambda _q, default: default,
        confirm=lambda _q, default: default,
        say=rec.say,
        save=rec.save,
        offer_completions=lambda: rec.say("<completions offer>"),
    )
    # completions BEFORE the try-it invitation: a paste-me command printed while
    # questions remain baits the paste into the next prompt (owner-hit)
    offer_at = rec.said.index("<completions offer>")
    try_it_at = next(i for i, line in enumerate(rec.said) if "Try it" in line)
    assert offer_at < try_it_at


async def test_picker_no_providers_screen_prints_every_fix() -> None:
    result, rec, menu = await _run_picker(current=Config(), env={}, tags=None)
    assert result == Config()
    assert rec.saved is None
    assert menu.shown == []  # nothing to pick from — no menu
    transcript = "\n".join(rec.said)
    assert "No providers connected yet" in transcript
    assert "https://ollama.com" in transcript
    assert "export OPENAI_API_KEY=" in transcript
    assert "export GEMINI_API_KEY=" in transcript
    assert "export ANTHROPIC_API_KEY=" in transcript
    assert "export MISTRAL_API_KEY=" in transcript
    assert "export OPENROUTER_API_KEY=" in transcript
    assert "Connect one, then rerun: smartpipe config" in transcript


async def test_picker_lists_undetected_providers_dim_with_fixes() -> None:
    _result, rec, _menu = await _run_picker(
        env={"OPENAI_API_KEY": "sk-x", "JINA_API_KEY": "j"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[0, 0],
    )
    transcript = "\n".join(rec.said)
    assert "not connected (how to connect):" in transcript
    assert "export ANTHROPIC_API_KEY=" in transcript
    assert "embeddings only" in transcript  # the jina mention — never a chat choice


async def test_picker_chips_annotate_probed_catalog_entries() -> None:
    chips = {"openai/gpt-5.4-mini": ProbeChip(sees=True, hears=False, ts=_NOW - 3 * 86_400)}
    _result, _rec, menu = await _run_picker(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini", "o4-mini")},
        chips=chips,
        picks=[0, 0],
    )
    model_labels_shown = menu.shown[1][1]
    assert model_labels_shown[0] == "openai/gpt-5.4-mini  (sees — probed 3d ago)"
    assert model_labels_shown[1] == "openai/o4-mini"  # no cache = no chips, no claims


async def test_picker_openrouter_picks_canonicalize_nested_names() -> None:
    result, _rec, _menu = await _run_picker(
        env={"OPENROUTER_API_KEY": "sk-or"},
        catalogs={"openrouter": ("x-ai/grok-4.5",)},
        picks=[0, 0],
    )
    assert result.model == "openrouter/x-ai/grok-4.5"
    assert result.embed_model is None  # no ratified pairing for openrouter


async def test_picker_ollama_without_chat_models_degrades_to_typed() -> None:
    result, rec, _menu = await _run_picker(
        tags=("nomic-embed-text",),  # daemon up, but only embedders installed
        picks=[0],
    )
    assert result.model == "ollama/qwen3:8b"  # the typed example default
    assert any("ollama pull qwen3:8b" in line for line in rec.said)


def test_set_embed_model_mistral(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "embed-model", "mistral-embed"])
    assert code == 0
    assert out.strip() == "embed-model set to mistral/mistral-embed"
    assert load_config(config_home).embed_model == "mistral/mistral-embed"


def test_profile_switch_and_list(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
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
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
    code, _out, err = run_cli(["config", "profile", "yolo"])
    assert code == 2
    assert "profile 'yolo' doesn't exist" in err


# --- the wizard's completions offer ----------------------------------------------


def _offer(
    home: Path,
    shell: str,
    *,
    accept: bool = True,
    confirmed: list[str] | None = None,
) -> list[str]:
    from smartpipe.cli.config_cmd import offer_shell_completions

    said: list[str] = []

    def confirm(question: str) -> bool:
        if confirmed is not None:
            confirmed.append(question)
        return accept

    offer_shell_completions(env={"SHELL": shell}, home=home, confirm=confirm, say=said.append)
    return said


def test_completions_offer_writes_once_and_discloses(tmp_path: Path) -> None:
    said = _offer(tmp_path, "/bin/zsh")
    rc = tmp_path / ".zshrc"
    line = 'eval "$(_SMARTPIPE_COMPLETE=zsh_source smartpipe)"'
    assert rc.read_text(encoding="utf-8") == line + "\n"
    assert any(".zshrc" in message and line in message for message in said)  # disclosed


def test_completions_offer_is_idempotent(tmp_path: Path) -> None:
    _offer(tmp_path, "/bin/zsh")
    confirmed: list[str] = []
    said = _offer(tmp_path, "/bin/zsh", confirmed=confirmed)
    assert confirmed == []  # already installed: no nagging
    assert said == []
    rc_text = (tmp_path / ".zshrc").read_text(encoding="utf-8")
    assert rc_text.count("_SMARTPIPE_COMPLETE") == 1  # never appended twice


def test_completions_offer_decline_writes_nothing(tmp_path: Path) -> None:
    said = _offer(tmp_path, "/usr/bin/bash", accept=False)
    assert not (tmp_path / ".bashrc").exists()
    assert any("Skipped" in message for message in said)


def test_completions_offer_appends_after_existing_content(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("export PATH=$PATH:~/bin", encoding="utf-8")  # no trailing newline
    _offer(tmp_path, "/bin/bash")
    text = rc.read_text(encoding="utf-8")
    assert text.startswith("export PATH=$PATH:~/bin\n")
    assert text.endswith('eval "$(_SMARTPIPE_COMPLETE=bash_source smartpipe)"\n')


def test_completions_offer_unknown_shell_stays_silent(tmp_path: Path) -> None:
    confirmed: list[str] = []
    said = _offer(tmp_path, "/usr/bin/fish", confirmed=confirmed)
    assert said == [] and confirmed == []
    assert list(tmp_path.iterdir()) == []  # never touches files


# --- config update-check --------------------------------------------------------


def test_update_check_off_persists(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "update-check", "off"])
    assert code == 0
    assert "update-check off" in out
    assert load_config(config_home).update_check is False


def test_update_check_on_persists(run_cli: RunCli, config_home: Path) -> None:
    run_cli(["config", "update-check", "off"])
    code, out, _err = run_cli(["config", "update-check", "on"])
    assert code == 0
    assert "update-check on" in out
    assert load_config(config_home).update_check is True


def test_update_check_junk_value_is_usage_error(run_cli: RunCli, config_home: Path) -> None:
    code, _out, err = run_cli(["config", "update-check", "maybe"])
    assert code == 64
    assert "on" in err and "off" in err


def test_set_ocr_model(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "ocr-model", "mistral-ocr-latest"])
    assert code == 0
    assert "ocr-model set to mistral/mistral-ocr-latest" in out
    assert load_config(config_home).ocr_model == "mistral/mistral-ocr-latest"


def test_set_media_embed_model(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "media-embed-model", "jina/jina-clip-v2"])
    assert code == 0
    assert "media-embed-model set to jina/jina-clip-v2" in out
    assert load_config(config_home).media_embed_model == "jina/jina-clip-v2"
