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


def test_config_model_argument_completes_through_the_callback(
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
    values = [item.value for item in completer.get_completions(["config", "model"], "")]
    assert values == ["ollama/qwen3:8b"]
