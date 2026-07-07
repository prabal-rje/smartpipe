"""D46: the embed default detects fastembed instead of assuming it."""

from __future__ import annotations

import pytest

from smartpipe.config.store import Config
from smartpipe.models import resolve
from smartpipe.models.resolve import resolve_embed_ref


def test_embed_default_falls_back_to_ollama_without_fastembed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # simulate Python 3.14 today: fastembed wheels don't exist there yet
    monkeypatch.setattr(resolve, "_default_embed_model", lambda: "nomic-embed-text")
    ref = resolve_embed_ref(None, {}, Config())
    assert str(ref) == "ollama/nomic-embed-text"
