from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.config.store import Config
from smartpipe.container import AppContainer, build_container
from smartpipe.core.errors import SetupFault
from smartpipe.io.writers import OutputFormat
from smartpipe.models.anthropic_adapter import AnthropicChatModel
from smartpipe.models.ollama import OllamaChatModel
from smartpipe.models.openai_compat import OpenAIChatModel, OpenAIEmbeddingModel
from smartpipe.models.retry import RetryPolicy

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
    # XDG pinned to nowhere: these tests must never see the developer's real
    # ~/.config/smartpipe (a stored ChatGPT login there satisfies key-or-login
    # and silently flips the no-key tests)
    isolated = {
        "XDG_CONFIG_HOME": "/nonexistent-smartpipe-tests",
        "APPDATA": "/nonexistent-smartpipe-tests",  # the windows config root (D09)
        **(env or {}),
    }
    return AppContainer(env=isolated, config=config or Config(), http_client=client, retry=FAST)


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


async def test_embed_defaults_to_local_nomic(client: httpx.AsyncClient) -> None:
    from importlib.util import find_spec

    from smartpipe.models.local_embed import LocalEmbeddingModel

    if find_spec("fastembed") is None:  # 3.14 until upstream wheels land (D46)
        pytest.skip("fastembed wheels absent on this python")
    model = await _container(client).embedding_model()  # D44: no server needed
    assert isinstance(model, LocalEmbeddingModel)
    assert model.ref.name == "nomic-embed-text-v1.5"


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


# --- media previews wire through the writer factory (TTY + color only) ----------


def _media_row() -> dict[str, object]:
    import base64

    from tests.io.test_preview import TINY_PNG

    return {
        "result": "a chart",
        "__media": {
            "kind": "image",
            "mime": "image/png",
            "data_b64": base64.b64encode(TINY_PNG).decode(),
        },
    }


def _human_writer_output(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, config: Config
) -> str:
    from smartpipe.container import ColorMode
    from smartpipe.io import tty

    monkeypatch.setattr(tty, "stdout_is_tty", lambda: True)  # AUTO + structured → HUMAN
    stream = io.StringIO()
    container = _container(client, config=config)
    container = AppContainer(
        env=container.env,
        config=container.config,
        http_client=client,
        retry=FAST,
        color_mode=ColorMode.ALWAYS,  # color without faking the whole environment
    )
    container.writer(OutputFormat.AUTO, structured=True, stdout=stream).write_record(_media_row())
    return stream.getvalue()


def test_writer_previews_render_at_a_color_tty(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _human_writer_output(client, monkeypatch, Config())
    assert "█" in out  # the thumbnail rendered under the summary line


def test_writer_previews_honor_the_kill_switch(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _human_writer_output(client, monkeypatch, Config(media_previews=False))
    assert "█" not in out
    assert len([line for line in out.splitlines() if line]) == 2  # result + summary, nothing else


def test_writer_previews_never_reach_a_pipe(client: httpx.AsyncClient) -> None:
    stream = io.StringIO()  # not a TTY → NDJSON, whatever the config says
    writer = _container(client).writer(OutputFormat.AUTO, structured=True, stdout=stream)
    writer.write_record(_media_row())
    first_line = stream.getvalue().splitlines()[0]
    assert first_line.startswith('{"result":"a chart",')
    assert "█" not in stream.getvalue()


# --- lifecycle ----------------------------------------------------------------


async def test_build_container_yields_and_closes_client() -> None:
    async with build_container({"OPENAI_API_KEY": "sk-x"}) as container:
        assert container.env["OPENAI_API_KEY"] == "sk-x"
        held = container.http_client
        assert not held.is_closed
    assert held.is_closed


async def test_build_container_surfaces_broken_config(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "smartpipe"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text("model =\n", encoding="utf-8")
    env = {"XDG_CONFIG_HOME": str(tmp_path), "APPDATA": str(tmp_path)}
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
        env={"MISTRAL_API_KEY": "mk-x", "SMARTPIPE_MISTRAL_BASE_URL": "http://proxy:9999/"},
        config=Config(model="mistral-small-latest"),
    )
    model = await container.chat_model()
    assert isinstance(model, OpenAIChatModel)
    assert model.base_url == "http://proxy:9999"


async def test_builds_gemini_chat_on_the_native_wire(client: httpx.AsyncClient) -> None:
    from smartpipe.models.gemini_native import GeminiNativeChatModel

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


# --- the stt-model role (D39/05) ----------------------------------------------------


def test_stt_role_unset_is_none(client: httpx.AsyncClient) -> None:
    assert _container(client).remote_transcriber() is None  # today's ladder, untouched


def test_stt_role_builds_the_openai_wire(client: httpx.AsyncClient) -> None:
    from smartpipe.models.stt import RemoteTranscriber

    container = _container(
        client,
        env={"OPENAI_API_KEY": "sk-x"},
        config=Config(stt_model="openai/whisper-1"),
    )
    transcriber = container.remote_transcriber()
    assert isinstance(transcriber, RemoteTranscriber)
    assert transcriber.ref.name == "whisper-1"


def test_stt_env_overrides_config(client: httpx.AsyncClient) -> None:
    container = _container(
        client,
        env={"OPENAI_API_KEY": "sk-x", "SMARTPIPE_STT_MODEL": "openai/gpt-4o-mini-transcribe"},
        config=Config(stt_model="openai/whisper-1"),
    )
    transcriber = container.remote_transcriber()
    assert transcriber is not None and transcriber.ref.name == "gpt-4o-mini-transcribe"


def test_stt_non_openai_provider_is_a_helpful_fault(client: httpx.AsyncClient) -> None:
    container = _container(client, config=Config(stt_model="ollama/whisper"))
    with pytest.raises(SetupFault, match="openai/whisper-1"):
        container.remote_transcriber()


def test_stt_without_key_names_it(client: httpx.AsyncClient) -> None:
    container = _container(client, config=Config(stt_model="openai/whisper-1"))
    with pytest.raises(SetupFault, match="OPENAI_API_KEY"):
        container.remote_transcriber()


def test_stt_auto_matrix(client: httpx.AsyncClient) -> None:
    """The owner's matrix: key → whisper-1; OAuth-only/gemini/ollama → None."""
    from smartpipe.models.base import parse_model_ref

    openai_ref = parse_model_ref("gpt-5.4-mini")
    gemini_ref = parse_model_ref("gemini-2.5-flash")
    ollama_ref = parse_model_ref("ollama/qwen3:8b")

    keyed = _container(client, env={"OPENAI_API_KEY": "sk-x"})
    auto = keyed.remote_transcriber(openai_ref)
    assert auto is not None and auto.ref.name == "whisper-1"  # the API supports it

    assert keyed.remote_transcriber(gemini_ref) is None  # gemini hears natively
    assert keyed.remote_transcriber(ollama_ref) is None  # no STT — local whisper

    oauth_only = _container(client)  # no key: the ChatGPT login can't transcribe
    assert oauth_only.remote_transcriber(openai_ref) is None


# --- fallback-model resolution (item 11) ----------------------------------------


def test_fallback_ref_is_none_when_unset(client: httpx.AsyncClient) -> None:
    assert _container(client).fallback_ref() is None


def test_fallback_ref_precedence_flag_env_config(client: httpx.AsyncClient) -> None:
    container = _container(
        client,
        env={"SMARTPIPE_FALLBACK_MODEL": "gpt-4o"},
        config=Config(fallback_model="ollama/qwen3:8b"),
    )
    assert str(container.fallback_ref("claude-opus-4-8")) == "anthropic/claude-opus-4-8"
    assert str(container.fallback_ref()) == "openai/gpt-4o"
    unflagged = _container(client, config=Config(fallback_model="ollama/qwen3:8b"))
    assert str(unflagged.fallback_ref()) == "ollama/qwen3:8b"


@pytest.mark.parametrize(
    "embedder",
    ["nomic-embed-text", "text-embedding-3-small", "local/clip", "jina/clip-v2", "mistral-embed"],
)
def test_fallback_refuses_embedding_models(client: httpx.AsyncClient, embedder: str) -> None:
    from smartpipe.core.errors import UsageFault

    with pytest.raises(UsageFault, match="chat models only"):
        _container(client).fallback_ref(embedder)


async def test_fallback_chat_model_builds_the_normal_wire(client: httpx.AsyncClient) -> None:
    container = _container(client, env={"OPENAI_API_KEY": "sk-test"})
    ref = container.fallback_ref("gpt-4o-mini")
    assert ref is not None
    model = await container.fallback_chat_model(ref)
    assert isinstance(model, OpenAIChatModel)


async def test_fallback_chat_model_missing_key_is_setup_fault(client: httpx.AsyncClient) -> None:
    container = _container(client)
    ref = container.fallback_ref("gpt-4o-mini")
    assert ref is not None
    with pytest.raises(SetupFault):
        await container.fallback_chat_model(ref)


# --- the media-embed-model role (item 40) ---------------------------------------


async def test_media_embed_role_unset_is_none(client: httpx.AsyncClient) -> None:
    assert await _container(client).media_embedding_model() is None


async def test_media_embed_role_builds_a_joint_embedder(client: httpx.AsyncClient) -> None:
    from smartpipe.models.base import supports_media_embedding

    container = _container(
        client,
        env={"JINA_API_KEY": "jk-x"},
        config=Config(media_embed_model="jina/jina-clip-v2"),
    )
    model = await container.media_embedding_model()
    assert model is not None
    assert supports_media_embedding(model)
    assert str(model.ref) == "jina/jina-clip-v2"


async def test_media_embed_env_overrides_config(client: httpx.AsyncClient) -> None:
    container = _container(
        client,
        env={"JINA_API_KEY": "jk-x", "SMARTPIPE_MEDIA_EMBED_MODEL": "jina/jina-clip-v2"},
        config=Config(media_embed_model="jina/other"),
    )
    model = await container.media_embedding_model()
    assert model is not None
    assert model.ref.name == "jina-clip-v2"


async def test_media_embed_role_refuses_a_text_only_embedder(client: httpx.AsyncClient) -> None:
    container = _container(client, config=Config(media_embed_model="nomic-embed-text"))
    with pytest.raises(SetupFault, match="joint"):
        await container.media_embedding_model()


async def test_media_embed_role_keeps_capability_under_the_budget(
    client: httpx.AsyncClient,
) -> None:
    from smartpipe.models.base import supports_media_embedding
    from smartpipe.models.budget import CallBudget

    container = AppContainer(
        env={"JINA_API_KEY": "jk-x", "XDG_CONFIG_HOME": "/nonexistent-smartpipe-tests"},
        config=Config(media_embed_model="jina/jina-clip-v2"),
        http_client=client,
        retry=FAST,
        budget=CallBudget(limit=3, stop=None),
    )
    model = await container.media_embedding_model()
    assert model is not None
    assert supports_media_embedding(model)  # the belt must not strip embed_parts


# --- the ocr-model role (item 40) -------------------------------------------------


def test_ocr_role_unset_is_none(client: httpx.AsyncClient) -> None:
    assert _container(client).document_parser() is None


def test_ocr_role_mistral_rides_the_dedicated_wire(client: httpx.AsyncClient) -> None:
    from smartpipe.models.ocr import MistralOcrParser

    container = _container(
        client, env={"MISTRAL_API_KEY": "mk-x"}, config=Config(ocr_model="mistral-ocr-latest")
    )
    parser = container.document_parser()
    assert isinstance(parser, MistralOcrParser)
    assert str(parser.ref) == "mistral/mistral-ocr-latest"


def test_ocr_role_mistral_without_key_is_setup_fault(client: httpx.AsyncClient) -> None:
    container = _container(client, config=Config(ocr_model="mistral-ocr-latest"))
    with pytest.raises(SetupFault, match="MISTRAL_API_KEY"):
        container.document_parser()


def test_ocr_role_other_refs_ride_the_vision_wire(client: httpx.AsyncClient) -> None:
    from smartpipe.models.ocr import VisionOcrParser

    container = _container(client, config=Config(ocr_model="ollama/llava"))
    parser = container.document_parser()
    assert isinstance(parser, VisionOcrParser)
    assert str(parser.ref) == "ollama/llava"


def test_ocr_role_flag_beats_env_beats_config(client: httpx.AsyncClient) -> None:
    container = _container(
        client,
        env={"SMARTPIPE_OCR_MODEL": "ollama/from-env"},
        config=Config(ocr_model="ollama/from-config"),
    )
    parser = container.document_parser()
    assert parser is not None and parser.ref.name == "from-env"
    flagged = container.document_parser("ollama/from-flag")
    assert flagged is not None and flagged.ref.name == "from-flag"


def test_ocr_role_refuses_an_embedding_ref(client: httpx.AsyncClient) -> None:
    container = _container(client, config=Config(ocr_model="jina/jina-clip-v2"))
    with pytest.raises(SetupFault, match="embedding model"):
        container.document_parser()
