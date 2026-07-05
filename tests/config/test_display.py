from __future__ import annotations

from sempipe.config.display import Setting, render_show, settings_with_origin
from sempipe.config.store import Config


def test_origins_default_when_nothing_set() -> None:
    settings = settings_with_origin({}, Config())
    by_key = {s.key: s for s in settings}
    assert by_key["model"] == Setting("model", "(auto-detect)", "default")
    assert by_key["embed-model"] == Setting("embed-model", "nomic-embed-text", "default")
    assert by_key["concurrency"] == Setting("concurrency", "4", "default")
    assert by_key["output"] == Setting("output", "auto", "default")


def test_config_file_origin() -> None:
    config = Config(model="ollama/qwen3:8b", embed_model="nomic-embed-text")
    by_key = {s.key: s for s in settings_with_origin({}, config)}
    assert by_key["model"] == Setting("model", "ollama/qwen3:8b", "config file")
    assert by_key["embed-model"] == Setting("embed-model", "nomic-embed-text", "config file")


def test_env_origin_wins_over_config() -> None:
    env = {"SEMPIPE_MODEL": "gpt-4o-mini", "SEMPIPE_CONCURRENCY": "8"}
    by_key = {s.key: s for s in settings_with_origin(env, Config(model="ollama/x"))}
    assert by_key["model"] == Setting("model", "gpt-4o-mini", "env")
    assert by_key["concurrency"] == Setting("concurrency", "8", "env")


def test_render_show_is_aligned_and_ends_with_file_path() -> None:
    config = Config(model="ollama/qwen3:8b", embed_model="nomic-embed-text")
    path = "/home/u/.config/sempipe/config.toml"
    rendered = render_show(settings_with_origin({}, config), path)
    lines = rendered.splitlines()
    assert lines[0] == "model        ollama/qwen3:8b   (config file)"
    assert lines[1] == "embed-model  nomic-embed-text  (config file)"
    assert lines[2] == "concurrency  4                 (default)"
    assert lines[3] == "output       auto              (default)"
    assert lines[4] == "config file  /home/u/.config/sempipe/config.toml"
