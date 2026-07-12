from __future__ import annotations

import pytest

from smartpipe.config.store import Config
from smartpipe.core.errors import SetupFault
from smartpipe.models.resolve import Resolved, resolve_chat_ref, resolve_embed_ref


async def _probe_none() -> tuple[str, ...] | None:
    return None


# --- chat precedence: flag > env > config > autodetect > screen ----------------


async def test_flag_wins_over_everything() -> None:
    resolved = await resolve_chat_ref(
        "openai/gpt-4o-mini",
        {"SMARTPIPE_MODEL": "ollama/a"},
        Config(model="ollama/b"),
        _probe_none,
    )
    assert resolved == Resolved(resolved.ref)
    assert str(resolved.ref) == "openai/gpt-4o-mini"


async def test_env_wins_over_config() -> None:
    resolved = await resolve_chat_ref(
        None, {"SMARTPIPE_MODEL": "gpt-4o-mini"}, Config(model="qwen3:8b"), _probe_none
    )
    assert str(resolved.ref) == "openai/gpt-4o-mini"


async def test_config_used_when_no_flag_or_env() -> None:
    resolved = await resolve_chat_ref(None, {}, Config(model="ollama/qwen3:8b"), _probe_none)
    assert str(resolved.ref) == "ollama/qwen3:8b"
    assert resolved.notice is None


async def test_empty_env_value_is_treated_as_unset() -> None:
    resolved = await resolve_chat_ref(
        None, {"SMARTPIPE_MODEL": "  "}, Config(model="ollama/x"), _probe_none
    )
    assert str(resolved.ref) == "ollama/x"


async def test_autodetect_picks_first_non_embed_model_with_notice() -> None:
    async def probe() -> tuple[str, ...] | None:
        return ("nomic-embed-text", "qwen3:8b", "llama3.2")

    resolved = await resolve_chat_ref(None, {}, Config(), probe)
    assert str(resolved.ref) == "ollama/qwen3:8b"  # embed model skipped
    assert resolved.notice is not None
    assert "no model configured" in resolved.notice
    assert "smartpipe use ollama/qwen3:8b" in resolved.notice


async def test_no_model_and_no_ollama_is_the_screen() -> None:
    with pytest.raises(SetupFault) as excinfo:
        await resolve_chat_ref(None, {}, Config(), _probe_none)
    assert "no model configured" in str(excinfo.value)


async def test_ollama_present_but_only_embed_models_is_the_screen() -> None:
    async def probe() -> tuple[str, ...] | None:
        return ("nomic-embed-text",)

    with pytest.raises(SetupFault):
        await resolve_chat_ref(None, {}, Config(), probe)


# --- embed chain: flag > env > config > nomic-embed-text -----------------------


def test_embed_flag_wins() -> None:
    ref = resolve_embed_ref(
        "text-embedding-3-small", {"SMARTPIPE_EMBED_MODEL": "a"}, Config(embed_model="b")
    )
    assert str(ref) == "openai/text-embedding-3-small"


def test_embed_env_over_config() -> None:
    ref = resolve_embed_ref(
        None, {"SMARTPIPE_EMBED_MODEL": "mxbai-embed-large"}, Config(embed_model="x")
    )
    assert str(ref) == "ollama/mxbai-embed-large"


def test_embed_defaults_to_nomic() -> None:
    # D44/D46: on-device fastembed where its wheels exist; ollama otherwise
    from importlib.util import find_spec

    ref = resolve_embed_ref(None, {}, Config())
    expected = (
        "local/nomic-embed-text-v1.5" if find_spec("fastembed") else "ollama/nomic-embed-text"
    )
    assert str(ref) == expected


async def test_mistral_env_model_resolves() -> None:
    resolved = await resolve_chat_ref(
        None, {"SMARTPIPE_MODEL": "mistral/mistral-large-latest"}, Config(), _probe_none
    )
    assert str(resolved.ref) == "mistral/mistral-large-latest"


# --- the stt-model matrix (D39/05), shared by container + doctor + --probe -------


def test_stt_env_wins_over_config() -> None:
    from smartpipe.models.resolve import resolve_stt

    resolution = resolve_stt(
        {"SMARTPIPE_STT_MODEL": "openai/gpt-4o-mini-transcribe"}, "openai/whisper-1", None
    )
    assert resolution.kind == "remote"
    assert resolution.ref == "openai/gpt-4o-mini-transcribe"
    assert resolution.source == "env"


def test_stt_flag_outranks_env_and_config() -> None:
    from smartpipe.models.resolve import resolve_stt

    resolution = resolve_stt(
        {"SMARTPIPE_STT_MODEL": "openai/gpt-4o-mini-transcribe"},
        "openai/whisper-1",
        None,
        flag="openai/gpt-4o-transcribe",
    )
    assert (resolution.kind, resolution.ref, resolution.source) == (
        "remote",
        "openai/gpt-4o-transcribe",
        "flag",
    )


def test_stt_flag_local_pins_on_device() -> None:
    from smartpipe.models.resolve import resolve_stt

    resolution = resolve_stt({"SMARTPIPE_STT_MODEL": "openai/whisper-1"}, None, None, flag="Local")
    assert (resolution.kind, resolution.ref, resolution.source) == ("local", None, "flag")


def test_stt_blank_flag_falls_through_to_env() -> None:
    from smartpipe.models.resolve import resolve_stt

    resolution = resolve_stt({"SMARTPIPE_STT_MODEL": "openai/whisper-1"}, None, None, flag="  ")
    assert (resolution.kind, resolution.source) == ("remote", "env")


def test_stt_config_used_when_env_blank() -> None:
    from smartpipe.models.resolve import resolve_stt

    resolution = resolve_stt({"SMARTPIPE_STT_MODEL": "  "}, "openai/whisper-1", None)
    assert resolution == resolve_stt({}, "openai/whisper-1", None)
    assert (resolution.kind, resolution.source) == ("remote", "config")


def test_stt_local_sentinel_is_case_insensitive_from_either_source() -> None:
    from smartpipe.models.resolve import resolve_stt

    for resolution in (
        resolve_stt({"SMARTPIPE_STT_MODEL": "Local"}, None, None),
        resolve_stt({}, "local", None),
    ):
        assert resolution.kind == "local"
        assert resolution.ref is None  # never parsed — "local" is no ollama model


def test_stt_auto_needs_openai_chat_plus_the_key() -> None:
    from smartpipe.models.resolve import resolve_stt

    keyed = {"OPENAI_API_KEY": "sk-x"}
    assert resolve_stt(keyed, None, "openai") == resolve_stt(keyed, "", "openai")
    assert resolve_stt(keyed, None, "openai").kind == "remote"
    assert resolve_stt(keyed, None, "openai").ref == "openai/whisper-1"
    assert resolve_stt(keyed, None, "openai").source == "auto"

    assert resolve_stt(keyed, None, "gemini").kind == "ladder"  # hears natively
    assert resolve_stt(keyed, None, "ollama").kind == "ladder"  # no STT wire
    assert resolve_stt(keyed, None, None).kind == "ladder"
    assert resolve_stt({}, None, "openai").kind == "ladder"  # OAuth-only stays local
