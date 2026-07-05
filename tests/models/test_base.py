from __future__ import annotations

import pytest

from sempipe.core.errors import UsageFault
from sempipe.models.base import parse_model_ref


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
        # namespaced ollama models keep working — unknown prefixes are names, not errors
        ("someuser/somemodel:latest", "ollama", "someuser/somemodel:latest"),
        ("hf.co/org/model", "ollama", "hf.co/org/model"),
    ],
)
def test_routing(text: str, provider: str, name: str) -> None:
    ref = parse_model_ref(text)
    assert (ref.provider, ref.name) == (provider, name)


def test_explicit_provider_needs_a_name() -> None:
    with pytest.raises(UsageFault, match="missing a name"):
        parse_model_ref("ollama/")


def test_blank_model_is_a_usage_fault() -> None:
    with pytest.raises(UsageFault, match="no model given"):
        parse_model_ref("   ")


def test_str_is_the_canonical_form() -> None:
    assert str(parse_model_ref("gpt-4o-mini")) == "openai/gpt-4o-mini"
    assert str(parse_model_ref("hf.co/org/model")) == "ollama/hf.co/org/model"
