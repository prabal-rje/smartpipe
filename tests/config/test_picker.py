"""The provider-first picker's pure decisions (detection, catalogs, pairing, chips)."""

from __future__ import annotations

import pytest

from smartpipe.config.picker import (
    MENU_CAP,
    ProbeChip,
    ProviderStatus,
    cache_day,
    capped_catalog,
    chip_text,
    detect_providers,
    embed_pair_allowed,
    first_embed_tag,
    has_jina_key,
    model_labels,
    ollama_chat_tags,
    paired_embed,
    parse_anthropic_catalog,
    parse_gemini_catalog,
    parse_mistral_catalog,
    parse_openai_catalog,
    parse_openrouter_catalog,
    preferred_index,
)

# --- detection -----------------------------------------------------------------


def _by_provider(statuses: tuple[ProviderStatus, ...]) -> dict[str, ProviderStatus]:
    return {status.provider: status for status in statuses}


def test_detects_nothing_from_an_empty_environment() -> None:
    statuses = detect_providers({}, ollama_tags=None, openai_login=False)
    assert [s.provider for s in statuses] == [
        "ollama",
        "openai",
        "gemini",
        "anthropic",
        "mistral",
        "openrouter",
    ]
    assert all(not s.detected for s in statuses)
    # every undetected provider says HOW to connect — the fix is the screen
    assert "https://ollama.com" in _by_provider(statuses)["ollama"].connect_hint
    assert "export OPENAI_API_KEY=" in _by_provider(statuses)["openai"].connect_hint
    assert "smartpipe auth login" in _by_provider(statuses)["openai"].connect_hint
    assert "export GEMINI_API_KEY=" in _by_provider(statuses)["gemini"].connect_hint
    assert "export ANTHROPIC_API_KEY=" in _by_provider(statuses)["anthropic"].connect_hint
    assert "export MISTRAL_API_KEY=" in _by_provider(statuses)["mistral"].connect_hint
    assert "export OPENROUTER_API_KEY=" in _by_provider(statuses)["openrouter"].connect_hint


def test_detects_env_keys_and_ollama_tags() -> None:
    env = {"OPENAI_API_KEY": "k", "MISTRAL_API_KEY": "k", "OPENROUTER_API_KEY": "k"}
    statuses = _by_provider(
        detect_providers(env, ollama_tags=("llava", "qwen3:8b"), openai_login=False)
    )
    assert statuses["ollama"].detected and "2 local models" in statuses["ollama"].detail
    assert statuses["openai"].detected and statuses["openai"].detail == "API key"
    assert statuses["mistral"].detected
    assert statuses["openrouter"].detected
    assert not statuses["gemini"].detected
    assert not statuses["anthropic"].detected


def test_blank_keys_do_not_count() -> None:
    statuses = _by_provider(
        detect_providers({"OPENAI_API_KEY": "  "}, ollama_tags=None, openai_login=False)
    )
    assert not statuses["openai"].detected


def test_chatgpt_login_detects_openai_without_a_key() -> None:
    statuses = _by_provider(detect_providers({}, ollama_tags=None, openai_login=True))
    assert statuses["openai"].detected
    assert statuses["openai"].detail == "ChatGPT login"


def test_key_plus_login_names_both() -> None:
    statuses = _by_provider(
        detect_providers({"OPENAI_API_KEY": "k"}, ollama_tags=None, openai_login=True)
    )
    assert statuses["openai"].detail == "API key + ChatGPT login"


def test_google_api_key_detects_gemini_and_names_the_var() -> None:
    statuses = _by_provider(
        detect_providers({"GOOGLE_API_KEY": "k"}, ollama_tags=None, openai_login=False)
    )
    assert statuses["gemini"].detected
    assert "GOOGLE_API_KEY" in statuses["gemini"].detail


def test_gemini_key_outranks_google_key_in_the_detail() -> None:
    env = {"GEMINI_API_KEY": "k", "GOOGLE_API_KEY": "k"}
    statuses = _by_provider(detect_providers(env, ollama_tags=None, openai_login=False))
    assert "GEMINI_API_KEY" in statuses["gemini"].detail


def test_jina_key_is_noted_but_never_a_chat_provider() -> None:
    assert has_jina_key({"JINA_API_KEY": "k"})
    assert not has_jina_key({"JINA_API_KEY": " "})
    statuses = detect_providers({"JINA_API_KEY": "k"}, ollama_tags=None, openai_login=False)
    assert "jina" not in {s.provider for s in statuses}


# --- ollama tag helpers ----------------------------------------------------------


def test_ollama_chat_tags_drop_embedders() -> None:
    assert ollama_chat_tags(("nomic-embed-text", "llava", "qwen3:8b")) == ("llava", "qwen3:8b")


def test_preferred_index_prefers_vision_families_first() -> None:
    tags = ("random-model", "qwen3:8b", "llava")
    assert preferred_index(tags) == tags.index("llava")  # llava outranks qwen


def test_preferred_index_falls_back_to_first() -> None:
    assert preferred_index(("mystery-model",)) == 0
    assert preferred_index(()) == 0


def test_first_embed_tag_detected_or_default() -> None:
    assert first_embed_tag(("llava", "mxbai-embed-large")) == "mxbai-embed-large"
    assert first_embed_tag(("llava",)) == "embeddinggemma"


# --- catalog parsers -------------------------------------------------------------


def test_openai_catalog_keeps_chat_models_only() -> None:
    payload = {
        "data": [
            {"id": "gpt-5.4-mini"},
            {"id": "o4-mini"},
            {"id": "chatgpt-4o-latest"},
            {"id": "text-embedding-3-small"},
            {"id": "whisper-1"},
            {"id": "gpt-4o-realtime-preview"},
            {"id": "gpt-4o-audio-preview"},
            {"id": "tts-1"},
            {"id": "dall-e-3"},
            {"id": "gpt-image-1"},
            {"id": "gpt-4o-transcribe"},
            {"id": "gpt-4o-search-preview"},
            {"id": "omni-moderation-latest"},
            {"id": "gpt-3.5-turbo-instruct"},
            {"id": "davinci-002"},
        ]
    }
    assert parse_openai_catalog(payload) == ("gpt-5.4-mini", "o4-mini", "chatgpt-4o-latest")


def test_openai_catalog_newest_first_and_no_dated_snapshots() -> None:
    """The live /v1/models payload lists oldest-first and includes dated
    snapshots - unsorted, the 30-cap menu shows gpt-3.5 relics and hides
    the current flagships. Sort by created (newest first), drop snapshots."""
    payload = {
        "data": [
            {"id": "gpt-3.5-turbo", "created": 1_100},
            {"id": "gpt-5.4", "created": 1_400},
            {"id": "gpt-5.4-2026-03-05", "created": 1_401},
            {"id": "gpt-5.5", "created": 1_500},
            {"id": "o4-mini", "created": 1_300},
        ]
    }
    assert parse_openai_catalog(payload) == ("gpt-5.5", "gpt-5.4", "o4-mini", "gpt-3.5-turbo")


def test_openai_catalog_tolerates_junk_shapes() -> None:
    assert parse_openai_catalog({"data": "nope"}) == ()
    assert parse_openai_catalog([1, 2]) == ()
    assert parse_openai_catalog({"data": [{"id": 3}, "x", {"id": "gpt-5.4"}]}) == ("gpt-5.4",)


def test_openai_catalog_dedupes_preserving_order() -> None:
    payload = {"data": [{"id": "gpt-5.4"}, {"id": "gpt-5.4"}]}
    assert parse_openai_catalog(payload) == ("gpt-5.4",)


def test_gemini_catalog_strips_prefix_and_filters_generate_content() -> None:
    payload = {
        "models": [
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/imagen-4", "supportedGenerationMethods": ["predict"]},
            {
                "name": "models/text-embedding-004",
                "supportedGenerationMethods": ["generateContent", "embedContent"],
            },
            # live-caught: TTS and image-generation variants also claim
            # generateContent — they'd poison a CHAT menu
            {
                "name": "models/gemini-2.5-flash-preview-tts",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/gemini-2.0-flash-preview-image-generation",
                "supportedGenerationMethods": ["generateContent"],
            },
        ]
    }
    assert parse_gemini_catalog(payload) == ("gemini-2.5-flash",)


def test_anthropic_catalog_lists_every_id() -> None:
    payload = {"data": [{"id": "claude-sonnet-5"}, {"id": "claude-opus-4-8"}]}
    assert parse_anthropic_catalog(payload) == ("claude-sonnet-5", "claude-opus-4-8")


def test_mistral_catalog_requires_completion_chat() -> None:
    payload: dict[str, object] = {
        "data": [
            {"id": "mistral-medium-2508", "capabilities": {"completion_chat": True}},
            {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
            {"id": "mystery", "capabilities": {}},
            {"id": "no-caps"},
        ]
    }
    assert parse_mistral_catalog(payload) == ("mistral-medium-2508",)


def test_openrouter_catalog_keeps_vision_capable_models_only() -> None:
    payload = {
        "data": [
            {"id": "x-ai/grok-4.5", "architecture": {"input_modalities": ["text", "image"]}},
            {"id": "some/text-only", "architecture": {"input_modalities": ["text"]}},
            {"id": "no-arch"},
        ]
    }
    assert parse_openrouter_catalog(payload) == ("x-ai/grok-4.5",)


# --- the pairing table (owner-ratified) --------------------------------------------


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openai", "openai/text-embedding-3-small"),
        ("gemini", "gemini/gemini-embedding-001"),
        ("mistral", "mistral/mistral-embed"),
        ("anthropic", None),  # no embeddings wire — the default ladder applies
        ("openrouter", None),
    ],
)
def test_paired_embed_static_table(provider: str, expected: str | None) -> None:
    assert paired_embed(provider, None) == expected


def test_paired_embed_ollama_prefers_a_detected_tag() -> None:
    assert paired_embed("ollama", ("llava", "nomic-embed-text")) == "ollama/nomic-embed-text"


def test_paired_embed_ollama_falls_back_to_the_profile_default() -> None:
    assert paired_embed("ollama", ("llava",)) == "ollama/embeddinggemma"
    assert paired_embed("ollama", None) == "ollama/embeddinggemma"


def test_pairing_fills_only_unset_or_previously_paired() -> None:
    assert embed_pair_allowed(None)  # unset: fill
    assert embed_pair_allowed("openai/text-embedding-3-small")  # ours: repairable
    assert embed_pair_allowed("gemini/gemini-embedding-001")
    assert embed_pair_allowed("mistral/mistral-embed")
    assert embed_pair_allowed("ollama/nomic-embed-text")  # ollama embed tag: ours
    assert embed_pair_allowed("ollama/embeddinggemma")
    # deliberate user choices are never clobbered
    assert not embed_pair_allowed("jina/jina-clip-v2")
    assert not embed_pair_allowed("local/nomic-embed-text-v1.5")
    assert not embed_pair_allowed("openai/text-embedding-3-large")


def test_pairing_treats_an_unparseable_value_as_replaceable() -> None:
    assert embed_pair_allowed("   ")


# --- capability chips ---------------------------------------------------------------


def test_chip_text_names_abilities_and_age() -> None:
    now = 1_000_000.0
    chip = ProbeChip(sees=True, hears=True, ts=now - 3 * 86_400)
    assert chip_text(chip, now) == "sees, hears — probed 3d ago"


def test_chip_text_sees_only_today() -> None:
    now = 1_000_000.0
    assert chip_text(ProbeChip(sees=True, hears=False, ts=now), now) == "sees — probed today"


def test_chip_text_no_abilities_is_honest() -> None:
    now = 1_000_000.0
    chip = ProbeChip(sees=False, hears=False, ts=now - 86_400)
    assert chip_text(chip, now) == "text only — probed 1d ago"


def test_model_labels_annotate_only_probed_entries() -> None:
    now = 1_000_000.0
    chips = {"ollama/llava": ProbeChip(sees=True, hears=False, ts=now)}
    labels = model_labels("ollama", ("llava", "qwen3:8b"), chips, now)
    assert labels == ("ollama/llava  (sees — probed today)", "ollama/qwen3:8b")


# --- cache staleness ------------------------------------------------------------------


def test_cache_day_is_a_utc_date() -> None:
    assert cache_day(0.0) == "1970-01-01"


def test_capped_catalog_caps_long_menus() -> None:
    names = tuple(f"model-{i}" for i in range(MENU_CAP + 7))
    shown, hidden = capped_catalog(names)
    assert len(shown) == MENU_CAP
    assert hidden == 7
    assert capped_catalog(("a", "b")) == (("a", "b"), 0)
