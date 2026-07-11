"""The --local-only fence (item 65d): predicate, host honesty, refusals."""

from __future__ import annotations

import pytest

from smartpipe.core.errors import SetupFault
from smartpipe.core.fence import ensure_local_wire, is_local_host, local_only
from smartpipe.models.base import ModelRef


@pytest.mark.parametrize("value", ["1", "true", "on", "yes", "TRUE", " 1 ", "anything"])
def test_local_only_fails_closed_on_any_affirmative_value(value: str) -> None:
    assert local_only({"SMARTPIPE_LOCAL_ONLY": value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "off", "no", "  "])
def test_local_only_off_when_unset_or_explicitly_off(value: str) -> None:
    assert local_only({"SMARTPIPE_LOCAL_ONLY": value}) is False
    assert local_only({}) is False


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://127.1.2.3:11434",
        "http://[::1]:11434",
        "http://0.0.0.0:11434",
    ],
)
def test_loopback_hosts_are_local(url: str) -> None:
    assert is_local_host(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.5:11434",
        "https://gpu-box:11434",
        "http://ollama.internal.corp",
        "http://example.com",
    ],
)
def test_remote_hosts_are_not_local(url: str) -> None:
    assert is_local_host(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://127.attacker.example:11434",
        "https://127.0.0.1.attacker.example:11434",
        "http://127.0.0.1@attacker.example:11434",
        "http://2130706433:11434",
        "http://0177.0.0.1:11434",
    ],
)
def test_loopback_looking_remote_names_are_not_local(url: str) -> None:
    assert is_local_host(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "http://[::ffff:127.0.0.1]:11434",
        "localhost:11434",
        "127.0.0.1:11434",
    ],
)
def test_canonical_loopback_variants_are_local(url: str) -> None:
    assert is_local_host(url) is True


@pytest.mark.parametrize("url", ["", "://", "http://[::1", "http://user@"])
def test_malformed_hosts_fail_closed(url: str) -> None:
    assert is_local_host(url) is False


FENCED = {"SMARTPIPE_LOCAL_ONLY": "1"}
LOCALHOST = "http://localhost:11434"


def test_fence_off_never_raises() -> None:
    ref = ModelRef(provider="openai", name="gpt-4o-mini")
    ensure_local_wire(ref, {}, role="chat", ollama_host=LOCALHOST)


def test_cloud_chat_is_refused_with_the_local_alternative() -> None:
    ref = ModelRef(provider="openai", name="gpt-4o-mini")
    with pytest.raises(SetupFault) as caught:
        ensure_local_wire(ref, FENCED, role="chat", ollama_host=LOCALHOST)
    screen = str(caught.value)
    assert "openai/gpt-4o-mini" in screen
    assert "input stays on this machine" in screen
    assert "ollama" in screen  # the fix names the local wire


def test_cloud_embedder_is_refused_naming_the_on_device_one() -> None:
    ref = ModelRef(provider="jina", name="jina-clip-v2")
    with pytest.raises(SetupFault, match="unset embed-model"):
        ensure_local_wire(ref, FENCED, role="embed", ollama_host=LOCALHOST)


def test_cloud_ocr_is_refused_naming_local_extraction() -> None:
    ref = ModelRef(provider="mistral", name="mistral-ocr-latest")
    with pytest.raises(SetupFault, match="unset ocr-model"):
        ensure_local_wire(ref, FENCED, role="ocr", ollama_host=LOCALHOST)


def test_cloud_stt_is_refused_naming_local_whisper() -> None:
    ref = ModelRef(provider="openai", name="whisper-1")
    with pytest.raises(SetupFault, match="unset stt-model"):
        ensure_local_wire(ref, FENCED, role="stt", ollama_host=LOCALHOST)


def test_cloud_media_embedder_is_refused() -> None:
    ref = ModelRef(provider="jina", name="jina-clip-v2")
    with pytest.raises(SetupFault, match="unset media-embed-model"):
        ensure_local_wire(ref, FENCED, role="media_embed", ollama_host=LOCALHOST)


def test_local_and_localhost_ollama_pass_the_fence() -> None:
    ensure_local_wire(
        ModelRef(provider="local", name="nomic-embed-text-v1.5"),
        FENCED,
        role="embed",
        ollama_host=LOCALHOST,
    )
    ensure_local_wire(
        ModelRef(provider="ollama", name="qwen3:8b"), FENCED, role="chat", ollama_host=LOCALHOST
    )


def test_a_remote_ollama_host_is_honestly_refused() -> None:
    ref = ModelRef(provider="ollama", name="qwen3:8b")
    with pytest.raises(SetupFault) as caught:
        ensure_local_wire(ref, FENCED, role="chat", ollama_host="http://gpu-box:11434")
    screen = str(caught.value)
    assert "OLLAMA_HOST" in screen
    assert "data leaving" in screen


def test_a_loopback_looking_dns_name_is_refused_at_the_wire_boundary() -> None:
    ref = ModelRef(provider="ollama", name="qwen3:8b")
    with pytest.raises(SetupFault, match="OLLAMA_HOST"):
        ensure_local_wire(
            ref,
            FENCED,
            role="chat",
            ollama_host="https://127.0.0.1.attacker.example:11434",
        )
