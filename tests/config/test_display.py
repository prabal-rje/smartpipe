from __future__ import annotations

import re

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
    # item 48: the role keys show honest default wordings, not silence
    assert by_key["stt-model"] == Setting(
        "stt-model", "(auto: whisper-1 with an OpenAI key, else local whisper)", "default"
    )
    assert by_key["ocr-model"] == Setting("ocr-model", "(built-in local extraction)", "default")
    assert by_key["media-embed-model"] == Setting(
        "media-embed-model", "(none - media rides embed-model)", "default"
    )
    assert by_key["cache"] == Setting("cache", "on", "default")  # owner directive: default on
    assert by_key["update-check"] == Setting("update-check", "on", "default")
    assert by_key["media-previews"] == Setting("media-previews", "on", "default")


def test_role_keys_resolve_env_over_config() -> None:
    env = {"SMARTPIPE_OCR_MODEL": "mistral/mistral-ocr-latest"}
    config = Config(
        ocr_model="ollama/llava",
        stt_model="openai/whisper-1",
        media_embed_model="jina/jina-clip-v2",
    )
    by_key = {s.key: s for s in settings_with_origin(env, config)}
    assert by_key["ocr-model"] == Setting("ocr-model", "mistral/mistral-ocr-latest", "env")
    assert by_key["stt-model"] == Setting("stt-model", "openai/whisper-1", "config file")
    assert by_key["media-embed-model"] == Setting(
        "media-embed-model", "jina/jina-clip-v2", "config file"
    )


def test_posture_keys_render_on_off_with_origins() -> None:
    config = Config(cache=True, update_check=False, media_previews=False)
    by_key = {s.key: s for s in settings_with_origin({}, config)}
    assert by_key["cache"] == Setting("cache", "on", "config file")
    assert by_key["update-check"] == Setting("update-check", "off", "config file")
    assert by_key["media-previews"] == Setting("media-previews", "off", "config file")


def test_cache_env_values_win_and_normalize() -> None:
    on = {s.key: s for s in settings_with_origin({"SMARTPIPE_CACHE": "1"}, Config(cache=False))}
    assert on["cache"] == Setting("cache", "on", "env")
    off = {s.key: s for s in settings_with_origin({"SMARTPIPE_CACHE": "off"}, Config(cache=True))}
    assert off["cache"] == Setting("cache", "off", "env")
    junk = {s.key: s for s in settings_with_origin({"SMARTPIPE_CACHE": "maybe"}, Config())}
    assert junk["cache"] == Setting("cache", "on", "default")  # junk falls through to default on


def test_batching_row_mirrors_the_container_ladder() -> None:
    # unset = on (item 62: coalescing is the default posture)
    default = {s.key: s for s in settings_with_origin({}, Config())}
    assert default["batching"] == Setting("batching", "on", "default")
    stamped = {s.key: s for s in settings_with_origin({}, Config(batching=False))}
    assert stamped["batching"] == Setting("batching", "off", "config file")
    env = {s.key: s for s in settings_with_origin({"SMARTPIPE_BATCH": "off"}, Config())}
    assert env["batching"] == Setting("batching", "off", "env")
    junk = {s.key: s for s in settings_with_origin({"SMARTPIPE_BATCH": "maybe"}, Config())}
    assert junk["batching"] == Setting("batching", "on", "default")  # junk falls through


def test_update_check_env_kill_switch_shows_as_env_off() -> None:
    env = {"SMARTPIPE_NO_UPDATE_CHECK": "1"}
    by_key = {s.key: s for s in settings_with_origin(env, Config(update_check=True))}
    assert by_key["update-check"] == Setting("update-check", "off", "env")


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
    rendered = render_show(settings_with_origin({}, config), path, color=False)
    lines = rendered.splitlines()
    value_width = len("(auto: whisper-1 with an OpenAI key, else local whisper)") + 2
    expected = [
        ("model", "ollama/qwen3:8b", "(config file)"),
        ("fallback-model", "(none)", "(default)"),
        ("embed-model", "nomic-embed-text", "(config file)"),
        ("stt-model", "(auto: whisper-1 with an OpenAI key, else local whisper)", "(default)"),
        ("ocr-model", "(built-in local extraction)", "(default)"),
        ("media-embed-model", "(none - media rides embed-model)", "(default)"),
        ("concurrency", "4", "(default)"),
        ("output", "auto", "(default)"),
        ("cache", "on", "(default)"),
        ("batching", "on", "(default)"),
        ("update-check", "on", "(default)"),
        ("media-previews", "on", "(default)"),
    ]
    for line, (key, value, origin) in zip(lines[:-1], expected, strict=True):
        assert line == f"{key:<19}{value:<{value_width}}{origin}"
    assert lines[-1] == f"{'config file':<19}/home/u/.config/smartpipe/config.toml"


def test_render_show_aligns_cjk_values_by_display_width() -> None:
    # DEFER-2: a Wide value must not push its origin column out of line
    config = Config(
        model="ollama/日本語モデル",
        fallback_model="ollama/backup",
        embed_model="nomic-embed-text",
        stt_model="openai/whisper-1",
        ocr_model="mistral/mistral-ocr-latest",
        media_embed_model="jina/jina-clip-v2",
        cache=True,
        update_check=True,
        media_previews=True,
    )
    rendered = render_show(
        settings_with_origin({}, config), "~/.config/smartpipe/config.toml", color=False
    )
    lines = rendered.splitlines()
    cells_before_origin = {display_width(line[: line.index("(")]) for line in lines[:11]}
    assert len(cells_before_origin) == 1  # every origin starts in the same terminal cell


def test_render_show_uses_rich_styles_only_when_color_is_enabled() -> None:
    settings = settings_with_origin({}, Config())
    plain = render_show(settings, "/tmp/config.toml", color=False)
    colored = render_show(settings, "/tmp/config.toml", color=True)
    assert "\x1b[" not in plain
    assert "\x1b[" in colored
    assert re.sub(r"\x1b\[[0-9;]*m", "", colored) == plain
