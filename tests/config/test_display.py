from __future__ import annotations

from smartpipe.config.display import Setting, render_show, settings_with_origin
from smartpipe.config.store import Config
from smartpipe.io.text import display_width


def test_origins_default_when_nothing_set() -> None:
    settings = settings_with_origin({}, Config())
    by_key = {s.key: s for s in settings}
    assert by_key["model"] == Setting("model", "(auto-detect)", "default")
    assert by_key["fallback-model"] == Setting("fallback-model", "(none)", "default")
    assert by_key["embed-model"] == Setting("embed-model", "nomic-embed-text", "default")
    assert by_key["concurrency"] == Setting("concurrency", "4", "default")
    assert by_key["output"] == Setting("output", "auto", "default")


def test_config_file_origin() -> None:
    config = Config(model="ollama/qwen3:8b", embed_model="nomic-embed-text")
    by_key = {s.key: s for s in settings_with_origin({}, config)}
    assert by_key["model"] == Setting("model", "ollama/qwen3:8b", "config file")
    assert by_key["embed-model"] == Setting("embed-model", "nomic-embed-text", "config file")


def test_env_origin_wins_over_config() -> None:
    env = {"SMARTPIPE_MODEL": "gpt-4o-mini", "SMARTPIPE_CONCURRENCY": "8"}
    by_key = {s.key: s for s in settings_with_origin(env, Config(model="ollama/x"))}
    assert by_key["model"] == Setting("model", "gpt-4o-mini", "env")
    assert by_key["concurrency"] == Setting("concurrency", "8", "env")


def test_fallback_model_env_origin() -> None:
    env = {"SMARTPIPE_FALLBACK_MODEL": "gpt-4o-mini"}
    by_key = {s.key: s for s in settings_with_origin(env, Config(fallback_model="ollama/x"))}
    assert by_key["fallback-model"] == Setting("fallback-model", "gpt-4o-mini", "env")


def test_render_show_is_aligned_and_ends_with_file_path() -> None:
    config = Config(model="ollama/qwen3:8b", embed_model="nomic-embed-text")
    path = "/home/u/.config/smartpipe/config.toml"
    rendered = render_show(settings_with_origin({}, config), path)
    lines = rendered.splitlines()
    assert lines[0] == "model           ollama/qwen3:8b   (config file)"
    assert lines[1] == "fallback-model  (none)            (default)"
    assert lines[2] == "embed-model     nomic-embed-text  (config file)"
    assert lines[3] == "concurrency     4                 (default)"
    assert lines[4] == "output          auto              (default)"
    assert lines[5] == "config file     /home/u/.config/smartpipe/config.toml"


def test_render_show_aligns_cjk_values_by_display_width() -> None:
    # DEFER-2: a Wide value must not push its origin column out of line
    config = Config(
        model="ollama/日本語モデル", fallback_model="ollama/backup", embed_model="nomic-embed-text"
    )
    rendered = render_show(settings_with_origin({}, config), "~/.config/smartpipe/config.toml")
    lines = rendered.splitlines()
    cells_before_origin = {display_width(line[: line.index("(")]) for line in lines[:5]}
    assert len(cells_before_origin) == 1  # every origin starts in the same terminal cell
