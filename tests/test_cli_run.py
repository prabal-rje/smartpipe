"""``smartpipe run`` — a .sem stage behaves exactly like typing the argv (D17).

Equivalence is asserted against a twin invocation (same mocks, same stdin),
precedence by wire inspection, and error paths by exit codes through the real
``main()`` mapping — the run command adds no error handling of its own.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.conftest import RunCli

if TYPE_CHECKING:
    import respx

CHAT = "http://localhost:11434/api/chat"
FIXTURES = Path(__file__).parent / "fixtures" / "sem"


@pytest.fixture(autouse=True)
def local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": content}})


def _sem(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "stage.sem"
    path.write_text(body, encoding="utf-8")
    return path


def test_run_is_equivalent_to_typing_the_argv(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    script = _sem(tmp_path, 'verb = "map"\nprompt = "shout it"\n')
    respx_mock.post(CHAT).mock(return_value=_reply("LOUD"))
    run_code, run_out, _ = run_cli(["run", str(script)], stdin="hi\n")
    typed_code, typed_out, _ = run_cli(["map", "shout it"], stdin="hi\n")
    assert (run_code, run_out) == (typed_code, typed_out)
    assert run_out == "LOUD\n"


def test_cli_flag_overrides_the_sem_value(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    script = _sem(tmp_path, 'verb = "map"\nprompt = "x"\nmodel = "ollama/a"\n')
    route = respx_mock.post(CHAT).mock(return_value=_reply("ok"))
    code, _out, _err = run_cli(["run", str(script), "--model", "ollama/b"], stdin="hi\n")
    assert code == 0
    import json

    assert json.loads(route.calls.last.request.content)["model"] == "b"  # flag won


def test_sem_value_used_when_no_flag(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    script = _sem(tmp_path, 'verb = "map"\nprompt = "x"\nmodel = "ollama/a"\n')
    route = respx_mock.post(CHAT).mock(return_value=_reply("ok"))
    code, _out, _err = run_cli(["run", str(script)], stdin="hi\n")
    assert code == 0
    import json

    assert json.loads(route.calls.last.request.content)["model"] == "a"


def test_usage_errors_exit_64_naming_the_file(run_cli: RunCli, tmp_path: Path) -> None:
    script = _sem(tmp_path, 'verb = "map"\npromt = "typo"\nprompt = "x"\n')
    code, _out, err = run_cli(["run", str(script)], stdin="hi\n")
    assert code == 64
    assert str(script) in err
    assert "unknown key 'promt'" in err


def test_run_inside_a_sem_is_rejected(run_cli: RunCli, tmp_path: Path) -> None:
    script = _sem(tmp_path, 'verb = "run"\n')
    code, _out, err = run_cli(["run", str(script)], stdin="hi\n")
    assert code == 64
    assert "can't run from a script" in err


def test_unknown_extra_flag_is_still_a_usage_error(run_cli: RunCli, tmp_path: Path) -> None:
    # ignore_unknown_options on the outer run command must not leak into the verb
    script = _sem(tmp_path, 'verb = "map"\nprompt = "x"\n')
    code, _out, err = run_cli(["run", str(script), "--no-such-flag"], stdin="hi\n")
    assert code == 64
    assert "no such option" in err.lower()


def test_inner_setup_fault_still_exits_2(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the verb's SetupFault propagates to main()'s one exit-code mapping — no
    # double handling inside run
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    script = _sem(tmp_path, 'verb = "map"\nprompt = "x"\nmodel = "gpt-4o-mini"\n')
    code, _out, err = run_cli(["run", str(script)], stdin="hi\n")
    assert code == 2
    assert "OPENAI_API_KEY" in err


def test_missing_script_is_a_usage_error(run_cli: RunCli, tmp_path: Path) -> None:
    code, _out, err = run_cli(["run", str(tmp_path / "nope.sem")], stdin="hi\n")
    assert code == 64
    assert "nope.sem" in err


@pytest.mark.skipif(sys.platform == "win32", reason="shebang is a POSIX contract")
def test_shebang_executes_the_stage_directly(tmp_path: Path) -> None:
    from tests.helpers.paced import PacedOllama

    server = PacedOllama(lambda body: "MATCHES", paced=False)
    with server:
        script = tmp_path / "shout.sem"
        script.write_text(
            '#!/usr/bin/env -S smartpipe run\nverb = "map"\nprompt = "shout it"\n',
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        env = {
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "SMARTPIPE_MODEL": "ollama/qwen3:8b",
            "OLLAMA_HOST": server.url,
        }
        proc = subprocess.run(
            [str(script)],
            input="hi\n",
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            check=False,
        )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "MATCHES\n"


# --- pipelines (D38/14) ------------------------------------------------------------


def test_pipeline_runs_stage_into_stage(tmp_path: Path, run_cli: RunCli) -> None:
    script = tmp_path / "triage.sem"
    script.write_text(
        """\
[stage.hot]
verb = "where"
predicate = 'text has "ERROR"'

[stage.numbers]
verb = "summarize"
expression = "count()"
""",
        encoding="utf-8",
    )
    code, out, err = run_cli(["run", str(script)], stdin="ERROR one\nfine\nERROR two\n")
    assert code == 0
    assert out.strip() == '{"count":2}'
    assert "[hot]" in err  # stage receipts carry their stage name


def test_pipeline_dry_run_prints_postures_and_runs_nothing(tmp_path: Path, run_cli: RunCli) -> None:
    script = tmp_path / "triage.sem"
    script.write_text(
        """\
[stage.hot]
verb = "where"
predicate = 'text has "x"'

[stage.themes]
verb = "cluster"
""",
        encoding="utf-8",
    )
    code, out, _err = run_cli(["run", str(script), "--dry-run"], stdin="")
    assert code == 0
    assert "stage hot" in out and "[free]" in out
    assert "stage themes" in out and "[model calls]" in out
