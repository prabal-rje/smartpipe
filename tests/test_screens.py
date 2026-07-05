"""Byte-exact golden pins for every user-facing screen (plan/ux.md).

A screen change becomes a visible diff in review, like an API change. Refresh
with ``make golden`` (i.e. ``UPDATE_GOLDEN=1 uv run pytest``) after an
intentional edit.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sempipe.cli import screens

GOLDEN = Path(__file__).parent / "golden" / "screens"

_SCREENS: dict[str, str] = {
    "welcome": screens.WELCOME,
    "no_model": screens.NO_MODEL,
    "field_ref_on_plain_input": screens.FIELD_REF_ON_PLAIN_INPUT,
    "binary_stdin_unparseable": screens.BINARY_STDIN_UNPARSEABLE,
    "chatgpt_login_expired": screens.CHATGPT_LOGIN_EXPIRED,
    "embeddings_need_key": screens.EMBEDDINGS_NEED_KEY,
    "openai_needs_key_or_login": screens.openai_needs_key_or_login("gpt-5.4"),
    "stdin_document_failed": screens.stdin_document_failed("parse error"),
    "ollama_unreachable": screens.ollama_unreachable(
        "http://localhost:11434", "ollama/qwen3:8b", "connection refused"
    ),
    "ollama_model_missing": screens.ollama_model_missing(
        "qwen3:8b", "http://localhost:11434", "model 'qwen3:8b' not found"
    ),
    "missing_api_key_openai": screens.missing_api_key(
        "gpt-4o-mini", "OpenAI", "OPENAI_API_KEY", "sk-..."
    ),
    "missing_api_key_anthropic": screens.missing_api_key(
        "claude-opus-4-8", "Anthropic", "ANTHROPIC_API_KEY", "sk-ant-..."
    ),
    "missing_api_key_mistral": screens.missing_api_key(
        "mistral-large-latest",
        "Mistral",
        "MISTRAL_API_KEY",
        "...",
        note="create one at console.mistral.ai",
    ),
    "missing_anthropic_extra": screens.missing_anthropic_extra("claude-opus-4-8"),
    "cloud_model_missing": screens.cloud_model_missing("gpt-4o-mini-typo", "api.openai.com"),
    "schema_rejected": screens.schema_rejected("api.openai.com", "missing required property"),
}


@pytest.mark.parametrize("name", sorted(_SCREENS))
def test_screen_matches_golden(name: str) -> None:
    rendered = _SCREENS[name]
    path = GOLDEN / f"{name}.txt"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    assert rendered == path.read_text(encoding="utf-8"), (
        f"screen '{name}' drifted from its golden; if intended, run: make golden"
    )


def test_every_screen_export_is_pinned() -> None:
    exported = set(screens.__all__)
    covered = {
        "WELCOME",
        "NO_MODEL",
        "FIELD_REF_ON_PLAIN_INPUT",
        "BINARY_STDIN_UNPARSEABLE",
        "CHATGPT_LOGIN_EXPIRED",
        "EMBEDDINGS_NEED_KEY",
        "openai_needs_key_or_login",
        "stdin_document_failed",
        "ollama_unreachable",
        "ollama_model_missing",
        "missing_api_key",
        "missing_anthropic_extra",
        "cloud_model_missing",
        "schema_rejected",
    }
    assert exported == covered, "a screens.py export is not pinned in _SCREENS"
