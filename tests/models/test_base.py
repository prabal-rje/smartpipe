from __future__ import annotations

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.models.base import parse_model_ref


@pytest.mark.parametrize(
    ("text", "provider", "name"),
    [
        # explicit provider prefixes
        ("ollama/qwen3:8b", "ollama", "qwen3:8b"),
        ("openai/gpt-4o-mini", "openai", "gpt-4o-mini"),
        ("anthropic/claude-opus-4-8", "anthropic", "claude-opus-4-8"),
        # bare names route by shape (spec §3.6 sets models without prefixes)
        ("claude-opus-4-8", "anthropic", "claude-opus-4-8"),
        ("gpt-4o-mini", "openai", "gpt-4o-mini"),
        ("o4-mini", "openai", "o4-mini"),
        ("chatgpt-4o-latest", "openai", "chatgpt-4o-latest"),
        ("text-embedding-3-small", "openai", "text-embedding-3-small"),
        ("qwen3:8b", "ollama", "qwen3:8b"),
        ("nomic-embed-text", "ollama", "nomic-embed-text"),
        ("orca-mini", "ollama", "orca-mini"),  # 'o' but not o-series
        # the Mistral family (workstream 10): explicit and every bare-name prefix
        ("mistral/mistral-large-latest", "mistral", "mistral-large-latest"),
        ("mistral-large-latest", "mistral", "mistral-large-latest"),
        ("mistral-embed", "mistral", "mistral-embed"),
        ("ministral-8b-latest", "mistral", "ministral-8b-latest"),
        ("codestral-latest", "mistral", "codestral-latest"),
        ("magistral-medium-latest", "mistral", "magistral-medium-latest"),
        ("devstral-small-latest", "mistral", "devstral-small-latest"),
        ("pixtral-12b-latest", "mistral", "pixtral-12b-latest"),
        ("voxtral-mini-latest", "mistral", "voxtral-mini-latest"),
        ("open-mistral-7b", "mistral", "open-mistral-7b"),
        ("open-mixtral-8x7b", "mistral", "open-mixtral-8x7b"),
        # namespaced ollama models keep working — unknown prefixes are names, not errors
        ("someuser/somemodel:latest", "ollama", "someuser/somemodel:latest"),
        ("hf.co/org/model", "ollama", "hf.co/org/model"),
        # a namespaced name must NEVER be hijacked by the bare Mistral prefixes
        ("hf.co/mistralai/Mistral-7B-v0.3", "ollama", "hf.co/mistralai/Mistral-7B-v0.3"),
        # gemini: explicit + bare prefix (workstream post-1.1/03)
        ("gemini/gemini-2.5-flash", "gemini", "gemini-2.5-flash"),
        ("gemini-2.5-flash-lite", "gemini", "gemini-2.5-flash-lite"),
        # openrouter: EXPLICIT ONLY — its names are other vendors' names (D24)
        ("openrouter/deepseek/deepseek-chat", "openrouter", "deepseek/deepseek-chat"),
        ("openrouter/anthropic/claude-3.5-haiku", "openrouter", "anthropic/claude-3.5-haiku"),
        # a bare vendor-ish name still routes by the OLD rules, never to openrouter
        ("deepseek-chat", "ollama", "deepseek-chat"),
    ],
)
def test_routing(text: str, provider: str, name: str) -> None:
    ref = parse_model_ref(text)
    assert (ref.provider, ref.name) == (provider, name)


def test_explicit_provider_needs_a_name() -> None:
    with pytest.raises(UsageFault, match="missing a name"):
        parse_model_ref("ollama/")
    with pytest.raises(UsageFault, match="missing a name"):
        parse_model_ref("mistral/")


def test_blank_model_is_a_usage_fault() -> None:
    with pytest.raises(UsageFault, match="no model given"):
        parse_model_ref("   ")


def test_str_is_the_canonical_form() -> None:
    assert str(parse_model_ref("gpt-4o-mini")) == "openai/gpt-4o-mini"
    assert str(parse_model_ref("hf.co/org/model")) == "ollama/hf.co/org/model"
