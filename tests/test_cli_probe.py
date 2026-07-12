"""doctor --probe (D31): four tiny calls, one honest matrix."""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.conftest import RunCli

if TYPE_CHECKING:
    import respx

    from smartpipe.models.base import AudioData

CHAT = "http://localhost:11434/api/chat"
EMBED = "http://localhost:11434/api/embed"


@pytest.fixture(autouse=True)
def local_models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.setenv("SMARTPIPE_EMBED_MODEL", "nomic-embed-text")
    # FULL isolation - without these the test read the developer's real
    # ~/.config and ambient OPENAI_API_KEY, flipping the audio row to the
    # whisper-1 auto path (the order-dependent flake seen twice in gates)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
    for var in ("OPENAI_API_KEY", "SMARTPIPE_STT_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_probe_charts_the_matrix(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        has_images = any("images" in message for message in body["messages"])
        content = "red" if has_images else "OK"
        return httpx.Response(200, json={"message": {"content": content}})

    respx_mock.post(CHAT).mock(side_effect=answer)
    respx_mock.post(EMBED).mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})
    )
    respx_mock.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
    )
    code, out, err = run_cli(["doctor", "--probe"])
    assert "probing modalities with 5 tiny calls" in err
    assert "text" in out and "image" in out and "audio" in out
    assert "replied 'OK'" in out
    assert "saw it — 'red'" in out
    assert "3-dim vector" in out
    # ollama refuses audio pre-send; with whisper wheels present the matrix
    # shows the dash+asterisk fallback naming local whisper — not a red ✗ (D42)
    if find_spec("faster_whisper") is not None:  # absent on 3.14 until upstream ships
        assert "transcribed, then chat" in out
        assert "audio → local whisper" in out
    # image embedding rides the caption pivot on a text-only embedder — the
    # dash+asterisk fallback mark with the footnote naming the path (D42)
    assert "caption, then embed" in out
    assert "* fallback paths:" in out and "caption pivot" in out
    del code  # exit reflects the FREE checks; capability gaps don't flip it


def test_probe_writes_the_capability_cache(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    """The picker's chips come from here: --probe persists what it PAID to learn
    (native abilities only — fallback paths make no chip claims)."""
    import os

    from smartpipe.config.state_cache import load_probe_chips, probe_path

    def answer(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        has_images = any("images" in message for message in body["messages"])
        return httpx.Response(200, json={"message": {"content": "red" if has_images else "OK"}})

    respx_mock.post(CHAT).mock(side_effect=answer)
    respx_mock.post(EMBED).mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})
    )
    respx_mock.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
    )
    run_cli(["doctor", "--probe"])
    chip = load_probe_chips(probe_path(os.environ))["ollama/qwen3:8b"]
    assert chip.sees is True  # the mock saw the image and answered
    assert chip.hears is False  # ollama refuses audio pre-send — never claimed
    assert chip.ts > 0


# --- the stt path is EXERCISED, not displayed (C4) ----------------------------------

STT_WIRE = "https://api.openai.com/v1/audio/transcriptions"


def _mock_free_matrix(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).mock(
        return_value=httpx.Response(200, json={"message": {"content": "OK"}})
    )
    respx_mock.post(EMBED).mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})
    )
    respx_mock.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
    )


def test_probe_exercises_a_remote_stt_resolution(
    run_cli: RunCli, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote stt resolution costs ONE more tiny call — announced in the
    count, sent through the real container wire, reported as exercised."""
    monkeypatch.setenv("SMARTPIPE_STT_MODEL", "openai/whisper-1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    _mock_free_matrix(respx_mock)
    wire = respx_mock.post(STT_WIRE).mock(return_value=httpx.Response(200, text="a steady tone"))
    _code, out, err = run_cli(["doctor", "--probe"])
    assert "probing modalities with 6 tiny calls" in err
    assert "stt: whisper-1" in err  # the announcement names what the 5th call buys
    assert wire.call_count == 1
    assert "stt: ✓ transcribed via openai/whisper-1" in out


def test_probe_reports_the_wire_error_on_a_bad_key(
    run_cli: RunCli, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The false-positive matrix is dead: an invalid key surfaces as the wire's
    own error, never as a healthy footnote."""
    monkeypatch.setenv("SMARTPIPE_STT_MODEL", "openai/whisper-1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-bad")
    _mock_free_matrix(respx_mock)
    respx_mock.post(STT_WIRE).mock(return_value=httpx.Response(401, text="bad key"))
    _code, out, err = run_cli(["doctor", "--probe"])
    assert "probing modalities with 6 tiny calls" in err  # the attempt was announced
    assert "stt: ✗ the STT wire rejected the API key" in out


def test_probe_reports_a_build_fault_without_charging_for_it(
    run_cli: RunCli, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote resolution that can't even build (missing key) never announces
    a 5th call — the fault line carries the container's own wording."""
    monkeypatch.setenv("SMARTPIPE_STT_MODEL", "openai/whisper-1")
    _mock_free_matrix(respx_mock)
    _code, out, err = run_cli(["doctor", "--probe"])
    assert "probing modalities with 5 tiny calls" in err
    assert "stt: ✗" in out
    assert "OPENAI_API_KEY" in out


def test_probe_never_runs_local_whisper(
    run_cli: RunCli, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stt-model=local: a model download inside doctor is hostile — the probe
    verifies the wheels and says honestly that it did not exercise them."""
    monkeypatch.setenv("SMARTPIPE_STT_MODEL", "local")
    _mock_free_matrix(respx_mock)
    _code, out, err = run_cli(["doctor", "--probe"])
    assert "probing modalities with 5 tiny calls" in err
    if find_spec("faster_whisper") is not None:  # absent on 3.14 until upstream ships
        assert "stt: – local whisper ready (not exercised)" in out  # noqa: RUF001 — matrix dash
    else:
        assert "stt: ✗ local whisper unavailable — reinstall smartpipe" in out


def test_probe_adds_no_stt_line_on_the_ladder(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    """Nothing resolved (the ladder): no stt line, the count stays 5 — the
    audio row's footnote still documents the fallback path."""
    _mock_free_matrix(respx_mock)
    _code, out, err = run_cli(["doctor", "--probe"])
    assert "probing modalities with 5 tiny calls" in err
    assert "not exercised" not in out
    assert "transcribed via" not in out


async def test_exercise_stt_reports_pass_and_fail_through_the_seam() -> None:
    """The exercise helper with a literal fake Transcriber — both verdict lines."""
    from smartpipe.cli.probe_cmd import exercise_stt
    from smartpipe.core.errors import SetupFault
    from tests.verbs.test_graph import FakeTranscriber

    passing = FakeTranscriber("openai/whisper-1")
    assert await exercise_stt(passing) == "  stt: ✓ transcribed via openai/whisper-1"
    assert len(passing.heard) == 1  # the probe clip actually went through the wire

    class _Rejecting(FakeTranscriber):
        async def transcribe(self, audio: AudioData) -> str:
            del audio
            raise SetupFault(
                "error: the STT wire rejected the API key\n  Remote transcription uses …"
            )

    line = await exercise_stt(_Rejecting("openai/whisper-1"))
    assert line == "  stt: ✗ the STT wire rejected the API key"


async def test_exercise_stt_caps_a_stalled_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """C4 review: a hung endpoint must not hold doctor for the shared 120s HTTP
    timeout x retries — the probe's own cap renders as an honest ✗ verdict."""
    import asyncio

    from smartpipe.cli import probe_cmd
    from tests.verbs.test_graph import FakeTranscriber

    class _Stalled(FakeTranscriber):
        async def transcribe(self, audio: AudioData) -> str:
            del audio
            await asyncio.sleep(3600)  # never returns inside any sane probe window
            raise AssertionError("unreachable")  # pragma: no cover

    monkeypatch.setattr(probe_cmd, "_STT_PROBE_SECONDS", 0.05)
    line = await probe_cmd.exercise_stt(_Stalled("openai/whisper-1"))
    assert line == "  stt: ✗ no reply in 0s — the wire is stalled"


def test_without_probe_no_model_calls(run_cli: RunCli, respx_mock: respx.MockRouter) -> None:
    chat = respx_mock.post(CHAT)
    respx_mock.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
    )
    run_cli(["doctor"])
    assert chat.call_count == 0  # the D18 pin stands


def test_doctor_without_probe_shouts_about_it(
    run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
    )
    _code, out, _err = run_cli(["doctor"])
    assert "verify SETUP, not ABILITY" in out
    assert "doctor --probe" in out


# --- C8 configured-role exercises -------------------------------------------------


async def test_configured_role_exercises_are_independent_and_count_sent_calls() -> None:
    from smartpipe.cli.probe_cmd import RoleProbe, exercise_role_probes
    from smartpipe.core.errors import SetupFault

    calls: list[str] = []

    async def broken() -> str:
        calls.append("ocr")
        raise SetupFault("error: OCR runtime failed")

    async def passing() -> str:
        calls.append("media-embed")
        return "1024-dim image vector via jina/jina-clip-v2"

    lines, sent = await exercise_role_probes(
        (
            RoleProbe("ocr", "mistral/mistral-ocr-latest", broken),
            RoleProbe("media-embed", "jina/jina-clip-v2", passing),
        )
    )
    assert calls == ["ocr", "media-embed"]
    assert lines == (
        "  ocr: ✗ OCR runtime failed",
        "  media-embed: ✓ 1024-dim image vector via jina/jina-clip-v2",
    )
    assert sent == 2


def test_role_probe_planning_build_fault_makes_no_call_and_later_roles_survive() -> None:
    from collections.abc import Sequence

    from smartpipe.cli.probe_cmd import plan_role_probes
    from smartpipe.config.store import Config
    from smartpipe.core.errors import SetupFault
    from smartpipe.models.base import CompletionRequest, ImageData, ModelRef, parse_model_ref

    built: list[str] = []

    class Container:
        config = Config(
            ocr_model="mistral/mistral-ocr-latest",
            fallback_model="openai/gpt-4o-mini",
            media_embed_model="jina/jina-clip-v2",
        )

        def document_parser(self):
            built.append("ocr")
            raise SetupFault("error: MISTRAL_API_KEY missing")

        def probe_fallback_model(self):
            class Chat:
                ref: ModelRef = parse_model_ref("openai/gpt-4o-mini")

                async def complete(self, request: CompletionRequest) -> str:
                    del request
                    return "OK"

            built.append("fallback-model")
            return Chat()

        async def media_embedding_model(self):
            class MediaEmbed:
                ref: ModelRef = parse_model_ref("jina/jina-clip-v2")

                async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
                    return tuple((0.0,) for _text in texts)

                async def embed_parts(
                    self, parts: Sequence[str | ImageData]
                ) -> tuple[tuple[float, ...], ...]:
                    return tuple((0.0,) for _part in parts)

            built.append("media-embed")
            return MediaEmbed()

    plans, faults = __import__("asyncio").run(plan_role_probes(Container()))
    assert built == ["ocr", "fallback-model", "media-embed"]
    assert tuple(plan.role for plan in plans) == ("fallback-model", "media-embed")
    assert faults == ("  ocr: ✗ MISTRAL_API_KEY missing",)
    assert len(plans) == 2
