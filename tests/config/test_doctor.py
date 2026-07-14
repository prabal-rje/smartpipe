"""``smartpipe doctor`` — the report contract and the no-paid-call rule (D18).

The e2e runs under respx with ONLY `/api/tags` mocked: any chat/embedding request
would blow up the test, which is the machine proof that doctor never spends money.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.config.doctor import CheckResult, doctor_exit_code, render_report
from smartpipe.core.errors import ExitCode
from tests.conftest import RunCli

if TYPE_CHECKING:
    from pathlib import Path

    import respx

TAGS = "http://localhost:11434/api/tags"


# --- the pure half -----------------------------------------------------------------


def test_render_pads_sections_and_marks_statuses() -> None:
    report = render_report(
        [
            CheckResult("config", "ok", "parses (model: ollama/qwen3:8b)"),
            CheckResult("keys", "skip", "OPENAI_API_KEY set"),
            CheckResult("terminal", "fail", "no completions — fix: see install docs"),
        ],
        color=False,
    )
    assert report.splitlines() == [
        "config    ✓ parses (model: ollama/qwen3:8b)",
        "keys      – OPENAI_API_KEY set",  # noqa: RUF001 — pinned skip mark
        "terminal  ✗ no completions — fix: see install docs",
    ]


def test_render_report_uses_rich_styles_only_when_color_is_enabled() -> None:
    results = [CheckResult("config", "ok", "parses")]
    plain = render_report(results, color=False)
    colored = render_report(results, color=True)
    assert "\x1b[" not in plain
    assert "\x1b[" in colored
    assert re.sub(r"\x1b\[[0-9;]*m", "", colored) == plain


def test_exit_zero_when_green_or_skipped() -> None:
    results = [CheckResult("a", "ok", "x"), CheckResult("b", "skip", "y")]
    assert doctor_exit_code(results) is ExitCode.OK


def test_exit_one_on_any_failure() -> None:
    results = [CheckResult("a", "ok", "x"), CheckResult("b", "fail", "y")]
    assert doctor_exit_code(results) is ExitCode.PARTIAL


# --- e2e (respx-fenced: only the free Ollama probe is mocked) -----------------------


@pytest.fixture()
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
    for var in (
        "SMARTPIPE_MODEL",
        "SMARTPIPE_EMBED_MODEL",
        "SMARTPIPE_STT_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MISTRAL_API_KEY",
        "OLLAMA_HOST",
        "SHELL",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _tags(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": name} for name in names]})


def test_doctor_all_green_exits_zero(
    run_cli: RunCli, respx_mock: respx.MockRouter, isolated_home: Path
) -> None:
    (isolated_home / "smartpipe").mkdir()
    (isolated_home / "smartpipe" / "config.toml").write_text(
        'model = "ollama/qwen3:8b"\nembed-model = "nomic-embed-text"\n', encoding="utf-8"
    )
    respx_mock.get(TAGS).mock(return_value=_tags("qwen3:8b", "nomic-embed-text"))
    code, out, _err = run_cli(["doctor"])
    assert code == 0
    assert "config" in out and "✓" in out
    assert "qwen3:8b is installed" in out
    assert "keys" in out  # presence report, never values


def test_doctor_does_not_claim_unprobed_provider_schema_enforcement(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "openai/gpt-5.4-mini")
    respx_mock.get(TAGS).mock(return_value=_tags())
    _code, out, _err = run_cli(["doctor"])
    schema_line = next(line for line in out.splitlines() if line.startswith("schema"))
    assert "adapter requests structured output; enforcement unverified" in schema_line
    assert " ✓ " not in schema_line


def test_doctor_flags_a_missing_ollama_model(
    run_cli: RunCli, respx_mock: respx.MockRouter, isolated_home: Path
) -> None:
    (isolated_home / "smartpipe").mkdir()
    (isolated_home / "smartpipe" / "config.toml").write_text(
        'model = "ollama/qwen3:8b"\n', encoding="utf-8"
    )
    respx_mock.get(TAGS).mock(return_value=_tags("other-model"))
    code, out, _err = run_cli(["doctor"])
    assert code == 1
    assert "ollama pull qwen3:8b" in out  # the fix rides the line


def test_doctor_reports_a_broken_config_instead_of_dying(
    run_cli: RunCli, respx_mock: respx.MockRouter, isolated_home: Path
) -> None:
    (isolated_home / "smartpipe").mkdir()
    (isolated_home / "smartpipe" / "config.toml").write_text("model =\n", encoding="utf-8")
    respx_mock.get(TAGS).mock(return_value=_tags())
    code, out, _err = run_cli(["doctor"])
    assert code == 1  # sick, but reported — not exit 2
    assert "syntax error" in out


def test_doctor_never_prints_key_values(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-SUPER-SECRET-VALUE")
    respx_mock.get(TAGS).mock(return_value=_tags("qwen3:8b"))
    _code, out, err = run_cli(["doctor"])
    assert "SUPER-SECRET" not in out and "SUPER-SECRET" not in err
    assert "OPENAI_API_KEY set" in out


def test_doctor_survives_no_ollama(
    run_cli: RunCli, respx_mock: respx.MockRouter, isolated_home: Path
) -> None:
    respx_mock.get(TAGS).mock(side_effect=httpx.ConnectError("refused"))
    code, out, _err = run_cli(["doctor"])
    assert code == 1
    assert "not reachable" in out


def test_extras_line_never_doubles_the_mark(
    run_cli: RunCli, respx_mock: respx.MockRouter, isolated_home: Path
) -> None:
    # live-smoke finding: "extras ✓ ✓ files" — the section mark must not repeat
    respx_mock.get(TAGS).mock(return_value=_tags("qwen3:8b"))
    _code, out, _err = run_cli(["doctor"])
    extras_line = next(line for line in out.splitlines() if line.startswith("extras"))
    assert "✓ ✓" not in extras_line and "– –" not in extras_line  # noqa: RUF001 — pinned marks


# --- the stt row (D39/05 visibility) -------------------------------------------------


def test_doctor_stt_auto_row_names_the_ladder(
    run_cli: RunCli, respx_mock: respx.MockRouter, isolated_home: Path
) -> None:
    respx_mock.get(TAGS).mock(return_value=_tags("qwen3:8b"))
    code, out, _err = run_cli(["doctor"])
    stt_line = next(line for line in out.splitlines() if line.startswith("stt"))
    assert "auto — chat-model hearing, else local whisper" in stt_line
    assert code in (0, 1)  # the ladder row itself is a skip, never a fail


def test_doctor_stt_remote_without_key_fails_with_the_fix(
    run_cli: RunCli, respx_mock: respx.MockRouter, isolated_home: Path
) -> None:
    (isolated_home / "smartpipe").mkdir()
    (isolated_home / "smartpipe" / "config.toml").write_text(
        'model = "ollama/qwen3:8b"\nstt-model = "openai/whisper-1"\n', encoding="utf-8"
    )
    respx_mock.get(TAGS).mock(return_value=_tags("qwen3:8b"))
    code, out, _err = run_cli(["doctor"])
    assert code == 1
    stt_line = next(line for line in out.splitlines() if line.startswith("stt"))
    assert "needs OPENAI_API_KEY" in stt_line
    assert 'stt-model = "local"' in stt_line  # the free fix rides the line


def test_doctor_stt_non_openai_wire_fails_naming_both_fixes(
    run_cli: RunCli, respx_mock: respx.MockRouter, isolated_home: Path
) -> None:
    (isolated_home / "smartpipe").mkdir()
    (isolated_home / "smartpipe" / "config.toml").write_text(
        'stt-model = "ollama/whisper"\n', encoding="utf-8"
    )
    respx_mock.get(TAGS).mock(return_value=_tags("qwen3:8b"))
    code, out, _err = run_cli(["doctor"])
    assert code == 1
    stt_line = next(line for line in out.splitlines() if line.startswith("stt"))
    assert "no STT wire for 'ollama'" in stt_line
    assert "openai/whisper-1" in stt_line and '"local"' in stt_line


def test_doctor_stt_local_reports_the_on_device_wire(
    run_cli: RunCli, respx_mock: respx.MockRouter, isolated_home: Path
) -> None:
    import sys
    from importlib.util import find_spec

    (isolated_home / "smartpipe").mkdir()
    (isolated_home / "smartpipe" / "config.toml").write_text(
        'stt-model = "local"\n', encoding="utf-8"
    )
    respx_mock.get(TAGS).mock(return_value=_tags("qwen3:8b"))
    _code, out, _err = run_cli(["doctor"])
    stt_line = next(line for line in out.splitlines() if line.startswith("stt"))
    if find_spec("faster_whisper") is not None:
        assert "local whisper (config) — on-device, free" in stt_line
    elif sys.version_info >= (3, 14):
        assert "stt-model local — waiting on upstream Python 3.14 wheels" in stt_line
    else:
        assert "stt-model local but whisper is unavailable" in stt_line


def test_doctor_stt_auto_prefers_whisper_with_openai_chat_and_key(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    (isolated_home / "smartpipe").mkdir()
    (isolated_home / "smartpipe" / "config.toml").write_text(
        'model = "openai/gpt-5.4-mini"\n', encoding="utf-8"
    )
    respx_mock.get(TAGS).mock(return_value=_tags())
    _code, out, _err = run_cli(["doctor"])
    stt_line = next(line for line in out.splitlines() if line.startswith("stt"))
    assert "openai/whisper-1 (auto: openai chat + OPENAI_API_KEY)" in stt_line


def test_doctor_stt_env_local_overrides_a_remote_config(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib.util import find_spec

    monkeypatch.setenv("SMARTPIPE_STT_MODEL", "local")
    (isolated_home / "smartpipe").mkdir()
    (isolated_home / "smartpipe" / "config.toml").write_text(
        'stt-model = "openai/whisper-1"\n', encoding="utf-8"
    )
    respx_mock.get(TAGS).mock(return_value=_tags())
    _code, out, _err = run_cli(["doctor"])
    stt_line = next(line for line in out.splitlines() if line.startswith("stt"))
    if find_spec("faster_whisper") is not None:
        assert "local whisper (env)" in stt_line
    assert "OPENAI_API_KEY" not in stt_line  # env local wins — no remote complaint
