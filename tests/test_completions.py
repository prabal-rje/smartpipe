"""Shell completion: per-shell source smoke tests + the model-suggestion contract.

The smoke tests catch click-version regressions cheaply (the source scripts are
click's); the suggestion tests pin the plan's matrix — success / timeout /
connection-refused ⇒ suggestions / () / () — and the 150 ms never-hang budget is
enforced by construction (fault injection, no real sockets).
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.cli.completions import suggest_models

if TYPE_CHECKING:
    from pathlib import Path

    import respx

TAGS = "http://localhost:11434/api/tags"


# --- per-shell source scripts ---------------------------------------------------


@pytest.mark.parametrize(
    ("shell", "marker"),
    # markers per click 8.4's generated scripts (fish uses long-form flags)
    [("bash", "complete -o"), ("zsh", "#compdef"), ("fish", "--command smartpipe")],
)
def test_completion_source_emits_a_script(shell: str, marker: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "smartpipe"],
        env={**os.environ, "_SMARTPIPE_COMPLETE": f"{shell}_source"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0
    assert marker in proc.stdout


# --- the --as dial's completion menu (item 54) --------------------------------------


def test_as_dial_offers_csv_on_the_shared_input_options() -> None:
    # click.Choice IS the shell-completion source for --as — the menu is the contract
    import click

    from smartpipe.cli.map_cmd import map_command
    from smartpipe.cli.read_cmd import read_command

    for command in (map_command, read_command):
        param = next(entry for entry in command.params if entry.name == "as_mode")
        assert isinstance(param.type, click.Choice)
        assert list(param.type.choices) == ["file", "lines", "jsonl", "csv"]


# --- model-name suggestions -------------------------------------------------------


def _tags(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": name} for name in names]})


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    # both config roots — XDG everywhere, APPDATA on windows (D09)
    return {"XDG_CONFIG_HOME": str(tmp_path), "APPDATA": str(tmp_path), **extra}


def test_success_suggests_configured_then_ollama(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.get(TAGS).mock(return_value=_tags("qwen3:8b", "nomic-embed-text"))
    env = _env(tmp_path, SMARTPIPE_MODEL="gpt-4o-mini")
    assert suggest_models("", env, embed=False) == (
        "gpt-4o-mini",
        "ollama/qwen3:8b",
        "ollama/nomic-embed-text",
    )


def test_suggestions_filter_on_the_typed_prefix(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.get(TAGS).mock(return_value=_tags("qwen3:8b", "llama3:8b"))
    assert suggest_models("ollama/q", _env(tmp_path), embed=False) == ("ollama/qwen3:8b",)


def test_embed_flag_reads_the_embed_settings(respx_mock: respx.MockRouter, tmp_path: Path) -> None:
    respx_mock.get(TAGS).mock(return_value=_tags())
    env = _env(
        tmp_path, SMARTPIPE_MODEL="gpt-4o-mini", SMARTPIPE_EMBED_MODEL="text-embedding-3-small"
    )
    assert suggest_models("", env, embed=True) == ("text-embedding-3-small",)


def test_configured_value_comes_from_the_config_file(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    (tmp_path / "smartpipe").mkdir()
    (tmp_path / "smartpipe" / "config.toml").write_text('model = "claude-opus-4-8"\n')
    respx_mock.get(TAGS).mock(return_value=_tags())
    assert suggest_models("", _env(tmp_path), embed=False) == ("claude-opus-4-8",)


def test_timeout_yields_no_suggestions(respx_mock: respx.MockRouter, tmp_path: Path) -> None:
    respx_mock.get(TAGS).mock(side_effect=httpx.ConnectTimeout("too slow"))
    assert suggest_models("", _env(tmp_path), embed=False) == ()


def test_connection_refused_yields_no_suggestions(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.get(TAGS).mock(side_effect=httpx.ConnectError("refused"))
    assert suggest_models("", _env(tmp_path), embed=False) == ()


def test_broken_config_never_breaks_tab(respx_mock: respx.MockRouter, tmp_path: Path) -> None:
    (tmp_path / "smartpipe").mkdir()
    (tmp_path / "smartpipe" / "config.toml").write_text("model = not-even-toml")
    respx_mock.get(TAGS).mock(return_value=_tags("qwen3:8b"))
    assert suggest_models("", _env(tmp_path), embed=False) == ("ollama/qwen3:8b",)


# --- the stt-model suggestions (#20): curated wires, no probe, never a hang ---------


def test_stt_suggestions_offer_configured_then_the_curated_wires(tmp_path: Path) -> None:
    from smartpipe.cli.completions import suggest_stt_models

    env = _env(tmp_path, SMARTPIPE_STT_MODEL="openai/whisper-1")
    assert suggest_stt_models("", env) == (
        "openai/whisper-1",
        "local",
        "openai/gpt-4o-transcribe",
        "openai/gpt-4o-mini-transcribe",
    )


def test_stt_suggestions_read_the_config_file(tmp_path: Path) -> None:
    from smartpipe.cli.completions import suggest_stt_models

    (tmp_path / "smartpipe").mkdir()
    (tmp_path / "smartpipe" / "config.toml").write_text('stt-model = "openai/gpt-4o-transcribe"\n')
    assert suggest_stt_models("", _env(tmp_path))[0] == "openai/gpt-4o-transcribe"


def test_stt_suggestions_curated_order_matches_the_stage(tmp_path: Path) -> None:
    """Nothing configured: the curated order mirrors the wizard's stt stage —
    local, then the openai wires best-quality-first (owner ruling 2026-07-12)."""
    from smartpipe.cli.completions import suggest_stt_models

    assert suggest_stt_models("", _env(tmp_path)) == (
        "local",
        "openai/gpt-4o-transcribe",
        "openai/gpt-4o-mini-transcribe",
        "openai/whisper-1",
    )


def test_stt_suggestions_filter_on_the_typed_prefix(tmp_path: Path) -> None:
    from smartpipe.cli.completions import suggest_stt_models

    assert suggest_stt_models("lo", _env(tmp_path)) == ("local",)


def test_stt_broken_config_never_breaks_tab(tmp_path: Path) -> None:
    from smartpipe.cli.completions import suggest_stt_models

    (tmp_path / "smartpipe").mkdir()
    (tmp_path / "smartpipe" / "config.toml").write_text("stt-model = not-even-toml")
    assert "local" in suggest_stt_models("", _env(tmp_path))


# --- the click wiring --------------------------------------------------------------


def test_map_model_flag_completes_through_the_callback(
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from click.shell_completion import ShellComplete

    from smartpipe.cli.root import cli

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
    monkeypatch.delenv("SMARTPIPE_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    respx_mock.get(TAGS).mock(return_value=_tags("qwen3:8b"))
    completer = ShellComplete(cli, {}, "smartpipe", "_SMARTPIPE_COMPLETE")
    values = [item.value for item in completer.get_completions(["map", "hi", "--model"], "")]
    assert values == ["ollama/qwen3:8b"]


def test_use_target_completes_providers_then_live_models(
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from click.shell_completion import ShellComplete

    from smartpipe.cli.root import cli

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
    monkeypatch.delenv("SMARTPIPE_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    respx_mock.get(TAGS).mock(return_value=_tags("qwen3:8b"))
    completer = ShellComplete(cli, {}, "smartpipe", "_SMARTPIPE_COMPLETE")
    values = [item.value for item in completer.get_completions(["use"], "")]
    assert values[:6] == ["ollama", "openai", "gemini", "anthropic", "mistral", "openrouter"]
    assert "ollama/qwen3:8b" in values


def test_use_target_completion_narrows_on_the_prefix(
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from click.shell_completion import ShellComplete

    from smartpipe.cli.root import cli

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
    monkeypatch.delenv("SMARTPIPE_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    respx_mock.get(TAGS).mock(return_value=_tags("qwen3:8b"))
    completer = ShellComplete(cli, {}, "smartpipe", "_SMARTPIPE_COMPLETE")
    values = [item.value for item in completer.get_completions(["use"], "ollama")]
    assert values == ["ollama", "ollama/qwen3:8b"]
