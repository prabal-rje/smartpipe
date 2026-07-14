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
    assert "\r" not in err  # in-process stages bind non-TTY stderr and never animate


def test_pipeline_model_stages_stay_spinner_free_and_receipt_prefixed(
    tmp_path: Path, run_cli: RunCli, respx_mock: respx.MockRouter
) -> None:
    script = tmp_path / "pipeline.sem"
    script.write_text(
        """\
[stage.first]
verb = "map"
prompt = "first"

[stage.second]
verb = "map"
prompt = "second"
""",
        encoding="utf-8",
    )
    respx_mock.post(CHAT).side_effect = [_reply("FIRST"), _reply("SECOND")]
    code, out, err = run_cli(["run", str(script)], stdin="input\n")
    assert code == 0
    assert out == "SECOND\n"
    assert "\r" not in err


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


# --- strict rows by default (item 19) ----------------------------------------------


MIXED = '{"level": "error"}\nplain text\n'


def test_sem_run_defaults_strict_rows(run_cli: RunCli, tmp_path: Path) -> None:
    """A .sem run treats a mixed stream as an ERROR (unattended = loud)."""
    script = _sem(tmp_path, 'verb = "split"\n')
    code, _out, err = run_cli(["run", str(script)], stdin=MIXED)
    assert code == 64
    assert "line 2 is a plain text line in a record stream" in err
    assert "demands one kind" in err


def test_sem_strict_rows_false_restores_the_census_note(run_cli: RunCli, tmp_path: Path) -> None:
    script = _sem(tmp_path, 'verb = "split"\nstrict-rows = false\n')
    code, _out, err = run_cli(["run", str(script)], stdin=MIXED)
    assert code == 0
    assert "input: 1 records · 1 plain lines" in err


def test_sem_pure_stream_passes_under_the_strict_default(run_cli: RunCli, tmp_path: Path) -> None:
    script = _sem(tmp_path, 'verb = "split"\n')
    code, out, _err = run_cli(["run", str(script)], stdin='{"a": 1}\n{"b": 2}\n')
    assert code == 0
    assert out.count("\n") == 2


def test_pipeline_mixed_stream_fails_loudly_naming_the_row(run_cli: RunCli, tmp_path: Path) -> None:
    script = tmp_path / "mixed.sem"
    script.write_text(
        """\
[stage.keep]
verb = "sample"
count = 5

[stage.cut]
verb = "split"
""",
        encoding="utf-8",
    )
    code, _out, err = run_cli(["run", str(script)], stdin=MIXED)
    assert code == 64
    assert "line 2 is a plain text line in a record stream" in err


def test_pipeline_stage_opt_out_restores_the_note(run_cli: RunCli, tmp_path: Path) -> None:
    script = tmp_path / "mixed.sem"
    script.write_text(
        """\
[stage.keep]
verb = "sample"
count = 5

[stage.cut]
verb = "split"
strict-rows = false
""",
        encoding="utf-8",
    )
    code, _out, err = run_cli(["run", str(script)], stdin=MIXED)
    assert code == 0
    assert "input: 1 records · 1 plain lines" in err


def test_pipeline_top_level_opt_out_covers_every_stage(run_cli: RunCli, tmp_path: Path) -> None:
    script = tmp_path / "mixed.sem"
    script.write_text(
        """\
strict-rows = false

[stage.keep]
verb = "sample"
count = 5

[stage.cut]
verb = "split"
""",
        encoding="utf-8",
    )
    code, _out, err = run_cli(["run", str(script)], stdin=MIXED)
    assert code == 0
    assert "input: 1 records · 1 plain lines" in err


def test_cli_strict_rows_flag_wins_over_the_sem_opt_out(run_cli: RunCli, tmp_path: Path) -> None:
    script = _sem(tmp_path, 'verb = "split"\nstrict-rows = false\n')
    code, _out, err = run_cli(["run", str(script), "--strict-rows"], stdin=MIXED)
    assert code == 64
    assert "line 2 is a plain text line" in err


def test_env_strict_rows_wins_over_the_sem_opt_out(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTPIPE_STRICT_ROWS", "1")
    script = _sem(tmp_path, 'verb = "split"\nstrict-rows = false\n')
    code, _out, err = run_cli(["run", str(script)], stdin=MIXED)
    assert code == 64
    assert "line 2 is a plain text line" in err


def test_interactive_pipe_keeps_the_permissive_note(run_cli: RunCli) -> None:
    """The flip is .sem-scoped: a typed pipe keeps today's census note."""
    code, _out, err = run_cli(["split"], stdin=MIXED)
    assert code == 0
    assert "input: 1 records · 1 plain lines" in err
    assert "demands one kind" not in err


def test_sem_field_miss_rows_are_errors_too(run_cli: RunCli, tmp_path: Path) -> None:
    """where's field-less-rows note is an error under the .sem default."""
    script = _sem(tmp_path, 'verb = "where"\npredicate = \'level == "error"\'\n')
    code, _out, err = run_cli(["run", str(script)], stdin="plain one\nplain two\n")
    assert code == 64
    assert "demands records" in err


def test_strict_rows_key_wrong_type_screen(run_cli: RunCli, tmp_path: Path) -> None:
    script = _sem(tmp_path, 'verb = "split"\nstrict-rows = "yes"\n')
    code, _out, err = run_cli(["run", str(script)], stdin=MIXED)
    assert code == 64
    assert "'strict-rows' should be true or false" in err


def test_pipeline_stages_set_and_clear_the_status_line_stage_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each stage runs under its own name so a status line (were one drawn)
    wears the same ``[name]`` prefix its receipts do — and the name never
    leaks past the stage."""
    from smartpipe.cli.run_cmd import execute_script
    from smartpipe.io import progress

    seen: list[str | None] = []

    def probe(argv: list[str]) -> None:
        seen.append(progress.stage_label())

    monkeypatch.setattr("smartpipe.cli.run_cmd._invoke", probe)
    script = tmp_path / "triage.sem"
    script.write_text(
        """\
[stage.hot]
verb = "where"
predicate = 'text has "ERROR"'

[stage.themes]
verb = "summarize"
expression = "count()"
""",
        encoding="utf-8",
    )
    execute_script(script)
    assert seen == ["hot", "themes"]
    assert progress.stage_label() is None
