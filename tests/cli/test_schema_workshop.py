"""The schema workshop loop (bare ``smartpipe schema`` at a TTY), driven
entirely through its injected callables — no terminal anywhere. The full
scripted session is golden-pinned in plain mode, picker-style."""

from __future__ import annotations

import json
import os
import re
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from smartpipe.cli.schema_workshop import PinnedScreen, WorkshopResult, run_workshop

if TYPE_CHECKING:
    from collections.abc import Sequence

GOLDEN = Path(__file__).parent.parent / "golden" / "screens"

CTRL_C = "\x03"  # scripted stand-in: ask raises KeyboardInterrupt


def _drive(
    commands: Sequence[str],
    *,
    color: bool = False,
    discard_answer: bool = False,
) -> tuple[WorkshopResult, list[str]]:
    """Run the loop over a script; the transcript is exactly what a plain-mode
    user would see (headers, prompt echoes, results)."""
    transcript: list[str] = []
    feed = iter(commands)

    def ask(prompt: str) -> str:
        answer = next(feed, None)
        if answer is None:
            raise EOFError
        if answer == CTRL_C:
            raise KeyboardInterrupt
        transcript.append(f"{prompt}{answer}")
        return answer

    def draw(lines: tuple[str, ...]) -> None:
        transcript.extend(lines)

    def confirm(question: str) -> bool:
        transcript.append(f"{question} {'y' if discard_answer else 'n'}")
        return discard_answer

    result = run_workshop(ask=ask, say=transcript.append, draw=draw, confirm=confirm, color=color)
    return result, transcript


def test_add_drop_and_quit_shape_the_draft() -> None:
    result, transcript = _drive(
        ["/add vendor string: legal name", "/add total number", "/drop vendor", "/quit"]
    )
    assert [field.name for field in result.draft] == ["total"]
    assert not result.discarded
    assert result.saved is None
    assert "✓ added vendor" in transcript
    assert "✓ dropped vendor" in transcript


def test_add_replaces_an_existing_field_in_place() -> None:
    result, transcript = _drive(["/add total number", "/add total integer", "/quit"])
    assert result.draft[0].type_text == "integer"
    assert "✓ replaced total" in transcript


def test_bad_type_shows_the_compilers_error_and_keeps_the_draft() -> None:
    result, transcript = _drive(["/add vendor strang", "/quit"], color=True)
    assert result.draft == ()
    joined = "\n".join(transcript)
    assert "'strang' isn't a type" in joined
    assert "\x1b[31m" in joined  # the compiler's own error, in red


def test_drop_of_a_missing_field_is_refused() -> None:
    result, transcript = _drive(["/drop ghost", "/quit"])
    assert result.draft == ()
    assert any("no field 'ghost' in the draft" in line for line in transcript)


def test_pasted_braces_replace_the_draft_and_bad_braces_do_not() -> None:
    result, transcript = _drive(
        ["/add vendor string", "{status enum(todo, done), note}", "{status enum()}", "/quit"]
    )
    assert [field.name for field in result.draft] == ["status", "note"]
    assert "✓ draft replaced · 2 fields" in transcript
    assert any("enum needs at least one value" in line for line in transcript)


def test_header_is_redrawn_after_every_command() -> None:
    _result, transcript = _drive(["/add vendor string", "/quit"])
    headers = [line for line in transcript if line.startswith("schema workshop")]
    assert len(headers) == 2  # once before each prompt
    assert "{vendor string}" in transcript  # the second header shows the new draft


def test_check_reports_rows_and_per_field_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    rows = [
        {"vendor": "Acme", "total": 5},
        {"vendor": 7, "total": 1},
        {"total": 2},
    ]
    Path("rows.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    _result, transcript = _drive(
        ["/add vendor string", "/add total number", "/test rows.jsonl", "/quit"]
    )
    assert "3 rows · 1 pass, 2 fail" in transcript
    assert "  vendor [███████░░░]  67% present, 1 type miss" in transcript
    assert "  total  [██████████] 100% present, 0 type misses" in transcript


def test_check_on_a_missing_file_is_a_red_line_not_a_crash() -> None:
    _result, transcript = _drive(["/add vendor string", "/test nowhere.jsonl", "/quit"])
    assert any("can't read nowhere.jsonl" in line for line in transcript)


def test_check_on_an_empty_draft_is_refused() -> None:
    _result, transcript = _drive(["/test rows.jsonl", "/quit"])
    assert any("the draft is empty" in line for line in transcript)


def test_example_and_save_on_an_empty_draft_are_refused() -> None:
    result, transcript = _drive(["/example", "/save", "/quit"])
    assert result.saved is None
    assert sum("the draft is empty" in line for line in transcript) == 2


def test_example_prints_a_deterministic_instance() -> None:
    _result, transcript = _drive(
        ["/add status enum(todo, done)", "/add total number", "/example", "/quit"]
    )
    block = "\n".join(transcript)
    start = block.index("{\n")
    instance = json.loads(block[start : block.index("\n}", start) + 2])
    assert instance == {"status": "todo", "total": 0}


def test_save_writes_the_schema_and_prints_the_paste_ready_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result, transcript = _drive(["/add vendor string: legal name", "/save", "/quit"])
    assert result.saved == "schema.json"
    written = json.loads(Path("schema.json").read_text(encoding="utf-8"))
    assert written["required"] == ["vendor"]
    assert "✓ wrote schema.json" in transcript
    assert "paste-ready:" in transcript
    assert "  '{vendor string: legal name}'" in transcript
    assert "  --schema schema.json" in transcript


def test_save_to_an_unwritable_path_is_a_red_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result, transcript = _drive(["/add vendor string", "/save missing/dir/x.json", "/quit"])
    assert result.saved is None
    assert any("can't write missing/dir/x.json" in line for line in transcript)


def test_unknown_command_and_plain_garbage_are_refused_with_the_menu() -> None:
    _result, transcript = _drive(["/frob", "hello", "/quit"])
    assert any("unknown command /frob" in line for line in transcript)
    assert any("not a /command or a {braces} draft" in line for line in transcript)


def test_blank_lines_are_ignored() -> None:
    result, transcript = _drive(["", "   ", "/quit"])
    assert result.draft == ()
    assert not any("error" in line for line in transcript)


def test_eof_quits_like_slash_quit() -> None:
    result, _transcript = _drive(["/add vendor string"])  # the script simply runs out
    assert [field.name for field in result.draft] == ["vendor"]
    assert not result.discarded


def test_interrupt_on_an_empty_draft_leaves_immediately() -> None:
    result, _transcript = _drive([CTRL_C])
    assert result.discarded


def test_interrupt_mid_draft_asks_once_then_keeps_going() -> None:
    result, transcript = _drive(["/add vendor string", CTRL_C, "/quit"], discard_answer=False)
    assert not result.discarded
    assert "discard draft? [y/N] n" in transcript
    assert [field.name for field in result.draft] == ["vendor"]


def test_interrupt_mid_draft_discards_on_yes() -> None:
    result, transcript = _drive(["/add vendor string", CTRL_C], discard_answer=True)
    assert result.discarded
    assert "discard draft? [y/N] y" in transcript


def test_second_interrupt_leaves_without_asking_again() -> None:
    result, transcript = _drive(
        ["/add vendor string", CTRL_C, CTRL_C, "/quit"], discard_answer=False
    )
    assert result.discarded
    assert sum("discard draft?" in line for line in transcript) == 1


# --- the pinned screen (rich mode plumbing) --------------------------------------------


def test_pinned_screen_enters_alt_screen_sets_the_region_and_restores() -> None:
    stream = StringIO()
    with PinnedScreen(stream=stream, rows=24) as screen:
        screen.draw(("title", "draft"))
    output = stream.getvalue()
    assert output.startswith("\x1b[?1049h")  # alt screen on
    assert "\x1b[6;24r" in output  # scroll region below the 5 header rows
    assert "title\x1b[K" in output and "draft\x1b[K" in output
    assert "\x1b7" in output and "\x1b8" in output  # header drawn without moving the prompt
    assert output.endswith("\x1b[r\x1b[?1049l")  # region reset, alt screen off


def test_pinned_screen_draws_are_idempotent_over_the_same_rows() -> None:
    stream = StringIO()
    with PinnedScreen(stream=stream, rows=24) as screen:
        screen.draw(("one",))
        screen.draw(("two",))
    assert stream.getvalue().count("\x1b[H") >= 2  # every draw homes and repaints


# --- the golden transcript (plain mode, picker-style) -------------------------------------


def test_workshop_session_matches_golden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    rows = [
        {"vendor": "Acme", "total": 12.5},
        {"vendor": "Bar", "total": "n/a"},
        {"total": 3},
    ]
    Path("rows.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    _result, transcript = _drive(
        [
            "/add vendor string: legal name",
            "/add total number",
            "/add status enum(todo, done)",
            "/drop status",
            "/frob",
            "/test rows.jsonl",
            "/example",
            "/save invoice.json",
            "/quit",
        ]
    )
    rendered = "\n".join(transcript) + "\n"
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", rendered)  # goldens pin PLAIN text (D42)
    path = GOLDEN / "schema_workshop_session.txt"
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(rendered, encoding="utf-8")
    if not path.exists():
        pytest.fail("golden 'schema_workshop_session' missing; create it with: make golden")
    assert rendered == path.read_text(encoding="utf-8"), (
        "the workshop transcript drifted from its golden; if intended, run: make golden"
    )
