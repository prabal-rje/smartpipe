"""``smartpipe doctor`` — the report contract and the no-paid-call rule (D18).

The e2e runs under respx with ONLY `/api/tags` mocked: any chat/embedding request
would blow up the test, which is the machine proof that doctor never spends money.
"""

from __future__ import annotations

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
        ]
    )
    assert report.splitlines() == [
        "config    ✓ parses (model: ollama/qwen3:8b)",
        "keys      – OPENAI_API_KEY set",  # noqa: RUF001 — pinned skip mark
        "terminal  ✗ no completions — fix: see install docs",
    ]


def test_exit_zero_when_green_or_skipped() -> None:
    results = [CheckResult("a", "ok", "x"), CheckResult("b", "skip", "y")]
    assert doctor_exit_code(results) is ExitCode.OK


def test_exit_one_on_any_failure() -> None:
    results = [CheckResult("a", "ok", "x"), CheckResult("b", "fail", "y")]
    assert doctor_exit_code(results) is ExitCode.PARTIAL


# --- e2e (respx-fenced: only the free Ollama probe is mocked) -----------------------


@pytest.fixture()
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path)  # the windows config root (D09))
    for var in (
        "SMARTPIPE_MODEL",
        "SMARTPIPE_EMBED_MODEL",
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
