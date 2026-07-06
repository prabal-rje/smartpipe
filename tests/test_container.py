from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from sempipe.config.store import Config
from sempipe.container import AppContainer, build_container
from sempipe.core.errors import SetupFault
from sempipe.io.writers import OutputFormat
from sempipe.models.anthropic_adapter import AnthropicChatModel
from sempipe.models.ollama import OllamaChatModel, OllamaEmbeddingModel
from sempipe.models.openai_compat import OpenAIChatModel, OpenAIEmbeddingModel
from sempipe.models.retry import RetryPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    import respx

FAST = RetryPolicy(attempts=1, base_delay=0.0)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as instance:
        yield instance


def _container(
    client: httpx.AsyncClient, env: Mapping[str, str] | None = None, config: Config | None = None
) -> AppContainer:
    return AppContainer(env=env or {}, config=config or Config(), http_client=client, retry=FAST)


# --- chat model construction per provider -------------------------------------


async def test_builds_ollama_chat_from_config(client: httpx.AsyncClient) -> None:
    container = _container(client, config=Config(model="ollama/qwen3:8b"))
    model = await container.chat_model()
    assert isinstance(model, OllamaChatModel)
    assert model.client is client  # injected, not reconstructed


async def test_builds_openai_chat_with_key(client: httpx.AsyncClient) -> None:
    container = _container(
        client, env={"OPENAI_API_KEY": "sk-x"}, config=Config(model="gpt-4o-mini")
    )
    model = await container.chat_model()
    assert isinstance(model, OpenAIChatModel)
    assert model.api_key == "sk-x"


async def test_openai_without_key_is_setup_fault(client: httpx.AsyncClient) -> None:
    container = _container(client, config=Config(model="gpt-4o-mini"))
    with pytest.raises(SetupFault, match="OPENAI_API_KEY"):
        await container.chat_model()


async def test_builds_anthropic_chat(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    container = _container(client, config=Config(model="claude-opus-4-8"))
    model = await container.chat_model()
    assert isinstance(model, AnthropicChatModel)


async def test_flag_overrides_config(client: httpx.AsyncClient) -> None:
    container = _container(client, config=Config(model="ollama/qwen3:8b"))
    model = await container.chat_model("ollama/other")
    assert isinstance(model, OllamaChatModel)
    assert model.ref.name == "other"


async def test_autodetect_emits_note_to_stderr(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    respx_mock.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
    )
    container = _container(client)
    model = await container.chat_model()
    assert isinstance(model, OllamaChatModel)
    assert model.ref.name == "qwen3:8b"
    assert "no model configured" in capsys.readouterr().err


async def test_no_model_no_ollama_is_the_screen(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    respx_mock.get("http://localhost:11434/api/tags").mock(
        side_effect=httpx.ConnectError("refused")
    )
    with pytest.raises(SetupFault, match="no model configured"):
        await _container(client).chat_model()


# --- embedding model construction ---------------------------------------------


async def test_embed_defaults_to_ollama_nomic(client: httpx.AsyncClient) -> None:
    model = await _container(client).embedding_model()
    assert isinstance(model, OllamaEmbeddingModel)
    assert model.ref.name == "nomic-embed-text"


async def test_embed_openai(client: httpx.AsyncClient) -> None:
    container = _container(client, env={"OPENAI_API_KEY": "sk-x"})
    model = await container.embedding_model("text-embedding-3-small")
    assert isinstance(model, OpenAIEmbeddingModel)


async def test_embed_anthropic_is_a_helpful_setup_fault(client: httpx.AsyncClient) -> None:
    with pytest.raises(SetupFault, match="don't provide embeddings"):
        await _container(client).embedding_model("claude-opus-4-8")


# --- writer factory -----------------------------------------------------------


def test_writer_picks_ndjson_for_structured_pipe(client: httpx.AsyncClient) -> None:
    stream = io.StringIO()
    # a StringIO is not a TTY, so AUTO + structured → NDJSON
    writer = _container(client).writer(OutputFormat.AUTO, structured=True, stdout=stream)
    writer.write_record({"a": 1})
    assert stream.getvalue() == '{"a":1}\n'


def test_writer_respects_explicit_text(client: httpx.AsyncClient) -> None:
    stream = io.StringIO()
    writer = _container(client).writer(OutputFormat.TEXT, structured=True, stdout=stream)
    writer.write_text("hola")
    assert stream.getvalue() == "hola\n"


# --- lifecycle ----------------------------------------------------------------


async def test_build_container_yields_and_closes_client() -> None:
    async with build_container({"OPENAI_API_KEY": "sk-x"}) as container:
        assert container.env["OPENAI_API_KEY"] == "sk-x"
        held = container.http_client
        assert not held.is_closed
    assert held.is_closed


async def test_build_container_surfaces_broken_config(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "sempipe"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text("model =\n", encoding="utf-8")
    env = {"XDG_CONFIG_HOME": str(tmp_path)}
    with pytest.raises(SetupFault, match="syntax error"):
        async with build_container(env):
            pass


async def test_builds_mistral_chat_with_key(client: httpx.AsyncClient) -> None:
    container = _container(
        client, env={"MISTRAL_API_KEY": "mk-x"}, config=Config(model="mistral-large-latest")
    )
    model = await container.chat_model()
    assert isinstance(model, OpenAIChatModel)  # same wire, parametrized
    assert model.api_key == "mk-x"
    assert model.base_url == "https://api.mistral.ai"
    assert model.wire.key_env == "MISTRAL_API_KEY"


async def test_mistral_without_key_is_setup_fault(client: httpx.AsyncClient) -> None:
    container = _container(client, config=Config(model="mistral-large-latest"))
    with pytest.raises(SetupFault, match="MISTRAL_API_KEY"):
        await container.chat_model()


async def test_embed_mistral(client: httpx.AsyncClient) -> None:
    container = _container(
        client, env={"MISTRAL_API_KEY": "mk-x"}, config=Config(embed_model="mistral-embed")
    )
    model = await container.embedding_model()
    assert isinstance(model, OpenAIEmbeddingModel)
    assert model.base_url == "https://api.mistral.ai"


async def test_mistral_base_url_override(client: httpx.AsyncClient) -> None:
    container = _container(
        client,
        env={"MISTRAL_API_KEY": "mk-x", "SEMPIPE_MISTRAL_BASE_URL": "http://proxy:9999/"},
        config=Config(model="mistral-small-latest"),
    )
    model = await container.chat_model()
    assert isinstance(model, OpenAIChatModel)
    assert model.base_url == "http://proxy:9999"


async def test_builds_gemini_chat_on_the_native_wire(client: httpx.AsyncClient) -> None:
    from sempipe.models.gemini_native import GeminiNativeChatModel

    container = _container(
        client, env={"GEMINI_API_KEY": "g-x"}, config=Config(model="gemini-2.5-flash")
    )
    model = await container.chat_model()
    assert isinstance(model, GeminiNativeChatModel)  # D34: the wire that watches video
    assert model.base_url == "https://generativelanguage.googleapis.com/v1beta"


async def test_gemini_without_key_names_the_env_var(client: httpx.AsyncClient) -> None:
    container = _container(client, config=Config(model="gemini-2.5-flash"))
    with pytest.raises(SetupFault, match="GEMINI_API_KEY"):
        await container.chat_model()


async def test_builds_openrouter_chat_with_slashed_name(client: httpx.AsyncClient) -> None:
    container = _container(
        client,
        env={"OPENROUTER_API_KEY": "or-x"},
        config=Config(model="openrouter/deepseek/deepseek-chat"),
    )
    model = await container.chat_model()
    assert isinstance(model, OpenAIChatModel)
    assert model.ref.name == "deepseek/deepseek-chat"  # the slashed name survives whole
    assert model.base_url == "https://openrouter.ai/api"
