"""The schema workshop's pure core: draft state, command parsing, braces
rendering, coverage aggregation — value-in/value-out, 100% covered."""

from __future__ import annotations

import json
import re

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.engine.schema_repl import (
    AddCommand,
    CheckCommand,
    CoverageReport,
    DraftField,
    DropCommand,
    ExampleCommand,
    FieldCoverage,
    QuitCommand,
    ReplaceCommand,
    SaveCommand,
    UnknownInput,
    add_field,
    aggregate_coverage,
    compile_draft,
    coverage_lines,
    draft_from_braces,
    drop_field,
    header_lines,
    parse_command,
    paste_lines,
    render_braces,
    type_text_of,
)

VENDOR = DraftField("vendor", "string", "legal name")
TOTAL = DraftField("total", "number", None)
BARE = DraftField("note", None, None)


# --- braces rendering ---------------------------------------------------------------


def test_render_braces_round_trips_types_and_guidance() -> None:
    assert render_braces((VENDOR, TOTAL)) == "{vendor string: legal name, total number}"


def test_render_braces_bare_field_and_empty_draft() -> None:
    assert render_braces((BARE,)) == "{note}"
    assert render_braces(()) == "{}"


def test_render_braces_colored_keeps_the_plain_text_underneath() -> None:
    colored = render_braces((VENDOR, TOTAL), color=True)
    assert re.sub(r"\x1b\[[0-9;]*m", "", colored) == render_braces((VENDOR, TOTAL))
    assert "\x1b[36mvendor\x1b[0m" in colored  # field names cyan
    assert "\x1b[33mstring\x1b[0m" in colored  # types yellow
    assert "\x1b[2m: legal name\x1b[0m" in colored  # guidance dim


# --- compile through the real compiler ------------------------------------------------


def test_compile_draft_is_the_real_braces_schema() -> None:
    schema = compile_draft((VENDOR, TOTAL))
    assert schema["required"] == ["vendor", "total"]
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert properties["vendor"] == {"type": "string", "description": "legal name"}


def test_compile_draft_surfaces_the_compilers_own_error() -> None:
    with pytest.raises(UsageFault, match="isn't a type"):
        compile_draft((DraftField("vendor", "strang", None),))


def test_compile_empty_draft_refuses() -> None:
    with pytest.raises(UsageFault, match="draft is empty"):
        compile_draft(())


# --- draft edits ---------------------------------------------------------------------


def test_add_field_appends_and_replaces_in_place() -> None:
    draft = add_field((), VENDOR)
    draft = add_field(draft, TOTAL)
    assert [field.name for field in draft] == ["vendor", "total"]
    retyped = add_field(draft, DraftField("vendor", "string?", None))
    assert [field.name for field in retyped] == ["vendor", "total"]  # position kept
    assert retyped[0].type_text == "string?"


def test_drop_field_removes_or_says_no() -> None:
    assert drop_field((VENDOR, TOTAL), "vendor") == (TOTAL,)
    assert drop_field((VENDOR,), "missing") is None


# --- paste a braces string -----------------------------------------------------------


def test_draft_from_braces_extracts_names_types_guidance() -> None:
    draft = draft_from_braces("{vendor string: legal name, total number, note}")
    assert draft == (VENDOR, TOTAL, DraftField("note", None, None))


def test_draft_from_braces_round_trips_through_render() -> None:
    text = "{status enum(todo, done), tags string[], score number?}"
    assert render_braces(draft_from_braces(text)) == text


def test_draft_from_braces_accepts_surrounding_prompt_text() -> None:
    assert draft_from_braces("Extract {vendor, total} from each") == (
        DraftField("vendor", None, None),
        DraftField("total", None, None),
    )


def test_draft_from_braces_rejects_bad_grammar_with_the_compilers_screen() -> None:
    with pytest.raises(UsageFault, match="enum needs at least one value"):
        draft_from_braces("{status enum()}")


def test_draft_from_braces_rejects_escapes_only() -> None:
    with pytest.raises(UsageFault, match=re.escape("no {field} group")):
        draft_from_braces("{{literal}}")


# --- type_text_of: the reverse of the braces type vocabulary ---------------------------


@pytest.mark.parametrize(
    "token",
    ["string", "number", "integer", "boolean", "string[]", "number[]", "string?", "number[]?"],
)
def test_type_text_of_round_trips_every_type_token(token: str) -> None:
    from smartpipe.engine.schema_dsl import type_token

    prop = type_token(token)
    assert prop is not None
    assert type_text_of(prop) == token


def test_type_text_of_enum_and_oddities() -> None:
    assert type_text_of({"enum": ["a", "b"]}) == "enum(a, b)"
    assert type_text_of({"type": ["null"]}) == "null?"  # all-null union: honest, odd
    assert type_text_of({}) == "any"  # vocabulary we don't speak
    assert type_text_of({"type": "array"}) == "array"  # array with no items type


# --- command parsing ------------------------------------------------------------------


def test_parse_command_add_with_type_and_guidance() -> None:
    assert parse_command("/add vendor string: legal name") == AddCommand(
        "vendor", "string", "legal name"
    )
    assert parse_command("/add total number") == AddCommand("total", "number", None)
    assert parse_command("/add total number:  ") == AddCommand("total", "number", None)


def test_parse_command_add_usage_errors() -> None:
    command = parse_command("/add")
    assert isinstance(command, UnknownInput)
    assert "/add NAME TYPE" in command.message
    assert isinstance(parse_command("/add vendor"), UnknownInput)  # type is required


def test_parse_command_drop_test_save_quit_example() -> None:
    assert parse_command("/drop vendor") == DropCommand("vendor")
    assert isinstance(parse_command("/drop"), UnknownInput)
    assert parse_command("/test data.jsonl") == CheckCommand("data.jsonl")
    assert isinstance(parse_command("/test"), UnknownInput)
    assert parse_command("/example") == ExampleCommand()
    assert parse_command("/save") == SaveCommand("schema.json")
    assert parse_command("/save out/invoice.json") == SaveCommand("out/invoice.json")
    assert parse_command("/quit") == QuitCommand()


def test_parse_command_unknown_slash_and_plain_garbage() -> None:
    unknown = parse_command("/frob")
    assert isinstance(unknown, UnknownInput)
    assert "/frob" in unknown.message
    garbage = parse_command("hello there")
    assert isinstance(garbage, UnknownInput)
    assert "{braces}" in garbage.message


def test_parse_command_blank_is_none_and_braces_replace() -> None:
    assert parse_command("   ") is None
    assert parse_command("{vendor, total}") == ReplaceCommand("{vendor, total}")
    assert parse_command("Extract {vendor}") == ReplaceCommand("Extract {vendor}")


# --- coverage aggregation ---------------------------------------------------------------


def _rows(*records: object) -> tuple[str, ...]:
    return tuple(json.dumps(record) for record in records)


def test_aggregate_coverage_counts_presence_and_type_misses() -> None:
    schema = compile_draft((VENDOR, TOTAL))
    lines = (
        *_rows(
            {"vendor": "Acme", "total": 5},
            {"vendor": 7, "total": 1},  # vendor type miss
            {"total": 2},  # vendor absent
        ),
        "",  # blank lines don't count
        "not json",  # counts as a failing row, no fields present
    )
    report = aggregate_coverage(schema, lines)
    assert report == CoverageReport(
        total=4,
        passed=1,
        fields=(
            FieldCoverage("vendor", present=2, misses=1),
            FieldCoverage("total", present=3, misses=0),
        ),
    )


def test_aggregate_coverage_non_object_rows_count_but_carry_no_fields() -> None:
    schema = compile_draft((TOTAL,))
    report = aggregate_coverage(schema, ("[1, 2]", "null"))
    assert report.total == 2
    assert report.passed == 0
    assert report.fields == (FieldCoverage("total", present=0, misses=0),)


def test_coverage_lines_plain_shape() -> None:
    report = CoverageReport(
        total=5,
        passed=4,
        fields=(
            FieldCoverage("vendor", present=5, misses=0),
            FieldCoverage("total", present=4, misses=1),
        ),
    )
    assert coverage_lines(report) == (
        "5 rows · 4 pass, 1 fail",
        "  vendor [██████████] 100% present, 0 type misses",
        "  total  [████████░░]  80% present, 1 type miss",
    )


def test_coverage_lines_all_green_and_color_tiers() -> None:
    report = CoverageReport(total=2, passed=2, fields=(FieldCoverage("v", 2, 0),))
    assert coverage_lines(report)[0] == "2 rows · 2 pass, 0 fail"
    colored = coverage_lines(report, color=True)
    assert colored[0].startswith("\x1b[32m")  # all pass: green
    assert "\x1b[32m[██████████]\x1b[0m" in colored[1]  # 100% bar: green
    tiers = CoverageReport(
        total=10,
        passed=0,
        fields=(FieldCoverage("mid", 6, 0), FieldCoverage("low", 2, 3)),
    )
    lines = coverage_lines(tiers, color=True)
    assert lines[0].startswith("\x1b[31m")  # failures: red
    assert "\x1b[33m[██████░░░░]\x1b[0m" in lines[1]  # 60%: yellow
    assert "\x1b[31m[██░░░░░░░░]\x1b[0m" in lines[2]  # 20%: red
    assert "\x1b[31m3 type misses\x1b[0m" in lines[2]  # misses always red


def test_coverage_lines_empty_file() -> None:
    report = CoverageReport(total=0, passed=0, fields=())
    assert coverage_lines(report) == ("0 rows · nothing to check",)


# --- header and paste lines ---------------------------------------------------------------


def test_header_lines_plain_with_a_draft() -> None:
    lines = header_lines((VENDOR, TOTAL))
    assert lines == (
        "schema workshop — free, no model calls",
        "{vendor string: legal name, total number}",
        "✓ compiles · 2 fields",
        "/add NAME TYPE [: guidance] · /drop NAME · /test FILE · /example · /save [PATH] · /quit",
    )


def test_header_lines_empty_draft_and_singular_field() -> None:
    assert header_lines(())[2] == "empty draft — /add a field, or paste a {braces} draft"
    assert header_lines((TOTAL,))[2] == "✓ compiles · 1 field"


def test_header_lines_color_voice() -> None:
    lines = header_lines((VENDOR,), color=True)
    assert lines[0].startswith("\x1b[1;36mschema workshop\x1b[0m")  # heading: bold cyan
    assert lines[2].startswith("\x1b[32m")  # valid draft: green checkmark line
    assert lines[3].startswith("\x1b[2m")  # command line: dim


def test_paste_lines_with_and_without_a_save() -> None:
    assert paste_lines((VENDOR,), saved=None) == (
        "paste-ready:",
        "  '{vendor string: legal name}'",
    )
    assert paste_lines((VENDOR,), saved="schema.json") == (
        "paste-ready:",
        "  '{vendor string: legal name}'",
        "  --schema schema.json",
    )
    assert paste_lines((), saved=None) == ()
