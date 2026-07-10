"""One-shot ``smartpipe use`` bundles (item 30): complete stamps or loud refusals."""

from __future__ import annotations

import pytest

from smartpipe.config.bundles import Bundle, Refusal, resolve_bundle, target_provider
from smartpipe.core.errors import UsageFault

# --- target routing ----------------------------------------------------------------


def test_provider_names_route_to_themselves() -> None:
    assert target_provider("gemini") == "gemini"
    assert target_provider("ollama") == "ollama"


def test_model_refs_route_by_the_parse_rules() -> None:
    assert target_provider("gpt-5.4-mini") == "openai"
    assert target_provider("ollama/llava") == "ollama"


def test_junk_target_is_a_usage_fault() -> None:
    with pytest.raises(UsageFault):
        target_provider("   ")


# --- cloud providers -----------------------------------------------------------------


def test_gemini_with_key_stamps_the_full_bundle() -> None:
    bundle = resolve_bundle("gemini", env={"GEMINI_API_KEY": "g"}, login=False, ollama_tags=None)
    assert bundle == Bundle(
        provider="gemini",
        model="gemini/gemini-3.1-flash-lite",
        embed_model="gemini/gemini-embedding-001",
        allow_captions=True,
    )


def test_cloud_without_key_refuses_and_points_at_auth_login() -> None:
    refused = resolve_bundle("gemini", env={}, login=False, ollama_tags=None)
    assert isinstance(refused, Refusal)
    assert "smartpipe auth login gemini" in refused.screen
    assert "Then rerun: smartpipe use gemini" in refused.screen


def test_model_ref_with_key_stamps_that_model_plus_the_pair() -> None:
    bundle = resolve_bundle(
        "gpt-5.4-mini", env={"OPENAI_API_KEY": "sk-x"}, login=False, ollama_tags=None
    )
    assert isinstance(bundle, Bundle)
    assert bundle.model == "openai/gpt-5.4-mini"  # canonicalized
    assert bundle.embed_model == "openai/text-embedding-3-small"
    assert bundle.allow_captions is True


def test_openai_login_only_rides_the_plan_wire_without_embeddings() -> None:
    bundle = resolve_bundle("openai", env={}, login=True, ollama_tags=None)
    assert isinstance(bundle, Bundle)
    assert bundle.model == "openai/gpt-5.4"  # -mini is rejected on the Codex wire
    assert bundle.embed_model is None
    assert any("login wire" in note for note in bundle.notes)


def test_openai_with_neither_credential_names_both_doors() -> None:
    refused = resolve_bundle("openai", env={}, login=False, ollama_tags=None)
    assert isinstance(refused, Refusal)
    assert "auth login openai-api" in refused.screen
    assert "auth login openai " in refused.screen


def test_anthropic_stamps_chat_and_leaves_the_embed_ladder_alone() -> None:
    bundle = resolve_bundle(
        "anthropic", env={"ANTHROPIC_API_KEY": "sk-ant"}, login=False, ollama_tags=None
    )
    assert isinstance(bundle, Bundle)
    assert bundle.model == "anthropic/claude-opus-4-8"
    assert bundle.embed_model is None  # no embedding wire — the local default stands
    assert any("no embedding wire" in note for note in bundle.notes)


def test_stored_key_overlay_counts_as_connected() -> None:
    # the caller passes env WITH the auth-store overlay applied — any source works
    bundle = resolve_bundle("mistral", env={"MISTRAL_API_KEY": "mk"}, login=False, ollama_tags=None)
    assert isinstance(bundle, Bundle)
    assert bundle.model == "mistral/mistral-small-latest"
    assert bundle.embed_model == "mistral/mistral-embed"


# --- ollama ---------------------------------------------------------------------------


def test_ollama_daemon_down_refuses_with_the_serve_fix() -> None:
    refused = resolve_bundle("ollama", env={}, login=False, ollama_tags=None)
    assert isinstance(refused, Refusal)
    assert "ollama serve" in refused.screen
    assert "Then rerun: smartpipe use ollama" in refused.screen


def test_ollama_without_chat_models_refuses_with_a_pull_hint() -> None:
    refused = resolve_bundle("ollama", env={}, login=False, ollama_tags=("nomic-embed-text",))
    assert isinstance(refused, Refusal)
    assert "ollama pull gemma-4-e2b" in refused.screen


def test_ollama_stamps_the_preferred_chat_tag_and_detected_embedder() -> None:
    bundle = resolve_bundle(
        "ollama", env={}, login=False, ollama_tags=("qwen3:8b", "llava", "nomic-embed-text")
    )
    assert bundle == Bundle(
        provider="ollama",
        model="ollama/llava",  # vision-first family preference
        embed_model="ollama/nomic-embed-text",
        allow_captions=False,
    )


def test_ollama_without_an_embed_tag_pairs_the_default_and_says_pull() -> None:
    bundle = resolve_bundle("ollama", env={}, login=False, ollama_tags=("llava",))
    assert isinstance(bundle, Bundle)
    assert bundle.embed_model == "ollama/embeddinggemma"
    assert any("ollama pull embeddinggemma" in note for note in bundle.notes)


def test_explicit_ollama_ref_needs_the_daemon_too() -> None:
    refused = resolve_bundle("ollama/llava", env={}, login=False, ollama_tags=None)
    assert isinstance(refused, Refusal)
    assert "Then rerun: smartpipe use ollama/llava" in refused.screen


def test_explicit_ollama_ref_stamps_verbatim_when_the_daemon_is_up() -> None:
    bundle = resolve_bundle(
        "ollama/gemma-4-e2b", env={}, login=False, ollama_tags=("llava", "embeddinggemma")
    )
    assert isinstance(bundle, Bundle)
    assert bundle.model == "ollama/gemma-4-e2b"
    assert bundle.embed_model == "ollama/embeddinggemma"


# --- the chat fence --------------------------------------------------------------------


@pytest.mark.parametrize("target", ["local/nomic-embed-text-v1.5", "jina/jina-clip-v2"])
def test_embedding_only_providers_are_refused(target: str) -> None:
    refused = resolve_bundle(target, env={}, login=False, ollama_tags=None)
    assert isinstance(refused, Refusal)
    assert "embedding model, not a chat model" in refused.screen


def test_an_embedding_ref_on_a_chat_provider_is_refused() -> None:
    refused = resolve_bundle(
        "text-embedding-3-small", env={"OPENAI_API_KEY": "sk-x"}, login=False, ollama_tags=None
    )
    assert isinstance(refused, Refusal)
    assert "embedding model, not a chat model" in refused.screen
