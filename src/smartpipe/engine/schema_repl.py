"""The schema workshop's pure core (bare ``smartpipe schema`` at a TTY).

Draft state, command parsing, braces rendering, and per-field coverage
aggregation — value-in/value-out functions that ``cli/schema_workshop`` drives
through injected callables. Compilation goes through the REAL braces compiler
(``parse_prompt`` + ``plan_map``), so every grammar error the workshop shows is
the compiler's own screen. Zero model calls anywhere: everything here is free.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import UsageFault
from smartpipe.core.jsontools import as_items, as_record

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "AddCommand",
    "CheckCommand",
    "Command",
    "CoverageReport",
    "DraftField",
    "DropCommand",
    "ExampleCommand",
    "FieldCoverage",
    "QuitCommand",
    "ReplaceCommand",
    "SaveCommand",
    "UnknownInput",
    "add_field",
    "aggregate_coverage",
    "compile_draft",
    "coverage_lines",
    "draft_from_braces",
    "drop_field",
    "header_lines",
    "paint",
    "parse_command",
    "paste_lines",
    "render_braces",
    "type_text_of",
]

COMMAND_LINE = (
    "/add NAME TYPE [: guidance] · /drop NAME · /test FILE · /example · /save [PATH] · /quit"
)
_DEFAULT_SAVE = "schema.json"
_BAR_CELLS = 10


def paint(text: str, code: str, color: bool) -> str:
    """ANSI when the caller decided color is on — the decision itself (TTY,
    NO_COLOR) lives at the edge, never here."""
    return f"\x1b[{code}m{text}\x1b[0m" if color else text


# --- the draft -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DraftField:
    """One field of the draft, kept in the braces grammar's own words."""

    name: str
    type_text: str | None  # a type token from the braces vocabulary; None = bare
    guidance: str | None  # the ": guidance" rung-2 description


def _field_source(field: DraftField) -> str:
    typed = f"{field.name} {field.type_text}" if field.type_text else field.name
    return f"{typed}: {field.guidance}" if field.guidance else typed


def render_braces(draft: Sequence[DraftField], *, color: bool = False) -> str:
    """The draft as a braces string — the exact text a pipeline would paste."""
    if not color:
        return "{" + ", ".join(_field_source(field) for field in draft) + "}"
    parts = (
        paint(field.name, "36", True)  # field names cyan
        + (f" {paint(field.type_text, '33', True)}" if field.type_text else "")  # types yellow
        + (paint(f": {field.guidance}", "2", True) if field.guidance else "")  # guidance dim
        for field in draft
    )
    return "{" + ", ".join(parts) + "}"


def add_field(draft: tuple[DraftField, ...], field: DraftField) -> tuple[DraftField, ...]:
    """Append, or replace in place when the name is already drafted."""
    if any(existing.name == field.name for existing in draft):
        return tuple(field if existing.name == field.name else existing for existing in draft)
    return (*draft, field)


def drop_field(draft: tuple[DraftField, ...], name: str) -> tuple[DraftField, ...] | None:
    """The draft without ``name`` — or None when no such field exists."""
    if all(field.name != name for field in draft):
        return None
    return tuple(field for field in draft if field.name != name)


def compile_draft(draft: Sequence[DraftField]) -> dict[str, object]:
    """Draft → JSON Schema through the real braces compiler; every grammar
    problem is the compiler's own ``UsageFault`` screen."""
    from smartpipe.engine.prompts import parse_prompt, plan_map

    if not draft:
        raise UsageFault("the draft is empty — /add a field first, or paste a {braces} draft")
    tokens = parse_prompt(render_braces(draft), allow_descriptions=True)
    plan = plan_map(tokens, schema=None)
    assert plan.schema is not None  # a non-empty draft always renders a brace group
    return dict(plan.schema)


def draft_from_braces(text: str) -> tuple[DraftField, ...]:
    """A pasted braces string → a fresh draft, validated by the real compiler.

    Surrounding prompt text is ignored (people paste whole map prompts); the
    fields, inline types, and guidance are lifted from the brace groups.
    """
    from smartpipe.engine.prompts import (
        brace_fields,
        brace_notes,
        brace_props,
        parse_prompt,
        plan_map,
    )

    tokens = parse_prompt(text, allow_descriptions=True)
    plan = plan_map(tokens, schema=None)  # validates merged fields and types
    if plan.schema is None:  # only {{ }} escapes — nothing to compile
        raise UsageFault(
            "the expression has no {field} group to compile\n"
            "  {{ and }} are literal braces. Name fields: {vendor string, total number}"
        )
    notes = brace_notes(tokens)
    props = brace_props(tokens)
    return tuple(
        DraftField(name, type_text_of(props[name]) if name in props else None, notes.get(name))
        for name in brace_fields(tokens)
    )


def type_text_of(prop: Mapping[str, object]) -> str:
    """A compiled property back into its braces type token — the reverse of
    ``schema_dsl.type_token``, so pasted drafts render in the user's own words."""
    values = as_items(prop.get("enum"))
    if values is not None:
        return "enum(" + ", ".join(str(value) for value in values) + ")"
    kind = prop.get("type")
    union = as_items(kind)
    if union is not None:  # a list "type" is a nullable union (D48)
        bases = [entry for entry in union if isinstance(entry, str) and entry != "null"]
        suffix = "?" if len(bases) < len(union) else ""
        return (_base_text(bases[0], prop) if bases else "null") + suffix
    return _base_text(kind, prop) if isinstance(kind, str) else "any"


def _base_text(kind: str, prop: Mapping[str, object]) -> str:
    if kind == "string":
        match prop.get("format"):  # item 56: the temporal types are strings on the wire
            case "date":
                return "date"
            case "date-time":
                return "datetime"
            case _:
                return "string"
    if kind != "array":
        return kind
    items = as_record(prop.get("items"))
    if items is not None and as_record(items.get("properties")) is not None:
        return _object_list_text(items)  # item 16: back into the braces' own words
    inner = items.get("type") if items is not None else None
    return f"{inner}[]" if isinstance(inner, str) else "array"


def _object_list_text(items: Mapping[str, object]) -> str:
    """An array-of-objects items schema → ``{name type: guidance, …}[]`` — the
    exact text a paste round-trips through the real compiler."""
    from smartpipe.engine.schema import BARE_PROPERTY

    properties = as_record(items.get("properties")) or {}
    pieces: list[str] = []
    for name, raw_prop in properties.items():
        prop = as_record(raw_prop) or {}
        shape = {key: value for key, value in prop.items() if key != "description"}
        typed = "" if shape == BARE_PROPERTY else f" {type_text_of(prop)}"
        note = prop.get("description")
        guidance = f": {note}" if isinstance(note, str) else ""
        pieces.append(f"{name}{typed}{guidance}")
    return "{" + ", ".join(pieces) + "}[]"


# --- commands ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AddCommand:
    name: str
    type_text: str
    guidance: str | None


@dataclass(frozen=True, slots=True)
class DropCommand:
    name: str


@dataclass(frozen=True, slots=True)
class CheckCommand:
    path: str


@dataclass(frozen=True, slots=True)
class ExampleCommand:
    pass


@dataclass(frozen=True, slots=True)
class SaveCommand:
    path: str


@dataclass(frozen=True, slots=True)
class QuitCommand:
    pass


@dataclass(frozen=True, slots=True)
class ReplaceCommand:
    text: str


@dataclass(frozen=True, slots=True)
class UnknownInput:
    """Input the parser refuses — the message is the whole verdict."""

    message: str


Command = (
    AddCommand
    | DropCommand
    | CheckCommand
    | ExampleCommand
    | SaveCommand
    | QuitCommand
    | ReplaceCommand
    | UnknownInput
)


def parse_command(line: str) -> Command | None:
    """One input line → a command (None = blank). Slash-commands first; a line
    with braces replaces the draft; anything else is refused with the menu."""
    text = line.strip()
    if not text:
        return None
    if text.startswith("/"):
        word, _, rest = text.partition(" ")
        return _slash_command(word, rest.strip())
    if "{" in text:
        return ReplaceCommand(text)
    return UnknownInput(
        "not a /command or a {braces} draft\n"
        f"  Paste a full {{braces}} draft, or use: {COMMAND_LINE}"
    )


def _slash_command(word: str, rest: str) -> Command:
    match word:
        case "/add":
            return _parse_add(rest)
        case "/drop":
            return DropCommand(rest) if rest else UnknownInput("usage: /drop NAME")
        case "/test":
            return CheckCommand(rest) if rest else UnknownInput("usage: /test FILE")
        case "/example":
            return ExampleCommand()
        case "/save":
            return SaveCommand(rest or _DEFAULT_SAVE)
        case "/quit":
            return QuitCommand()
        case _:
            return UnknownInput(f"unknown command {word}\n  Commands: {COMMAND_LINE}")


def _parse_add(rest: str) -> Command:
    from smartpipe.engine.schema_dsl import TYPE_MENU

    head, _colon, guidance = rest.partition(":")
    name, _space, type_text = head.strip().partition(" ")
    if not name or not type_text.strip():
        return UnknownInput(f"usage: /add NAME TYPE [: guidance]\n  Types: {TYPE_MENU}")
    return AddCommand(name, type_text.strip(), guidance.strip() or None)


# --- coverage (the /test summary) ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldCoverage:
    name: str
    present: int  # rows where the field appears
    misses: int  # present but failing the field's own schema ("type misses")


@dataclass(frozen=True, slots=True)
class CoverageReport:
    total: int  # non-blank rows
    passed: int  # rows the full schema accepts
    fields: tuple[FieldCoverage, ...]


@dataclass(frozen=True, slots=True)
class _NotJson:
    pass


_NOT_JSON = _NotJson()


def _parse_row(line: str) -> object:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return _NOT_JSON


def _accepts(instance: object, schema: Mapping[str, object]) -> bool:
    import jsonschema  # function-local: --help must not pay for the validator stack

    try:
        jsonschema.validate(instance, dict(schema))
    except jsonschema.ValidationError:
        return False
    return True


def aggregate_coverage(schema: Mapping[str, object], lines: Iterable[str]) -> CoverageReport:
    """Validate every row against the schema AND each field's own subschema:
    row pass/fail, per-field presence, per-field type misses. Row pass/fail is
    OPEN-WORLD (item 46, same machinery as ``schema --check``): only declared
    fields are judged — extras and the ``__`` spine never fail a row here."""
    from smartpipe.engine.schema import open_check_schema

    check = open_check_schema(schema)
    properties = as_record(schema.get("properties")) or {}
    rows = tuple(_parse_row(line) for line in lines if line.strip())
    records = tuple(as_record(row) for row in rows if not isinstance(row, _NotJson))
    passed = sum(1 for row in rows if not isinstance(row, _NotJson) and _accepts(row, check))
    fields = tuple(
        _field_coverage(name, as_record(prop) or {}, records) for name, prop in properties.items()
    )
    return CoverageReport(total=len(rows), passed=passed, fields=fields)


def _field_coverage(
    name: str,
    prop: Mapping[str, object],
    records: Sequence[Mapping[str, object] | None],
) -> FieldCoverage:
    values = tuple(record[name] for record in records if record is not None and name in record)
    misses = sum(1 for value in values if not _accepts(value, prop))
    return FieldCoverage(name, present=len(values), misses=misses)


def coverage_lines(report: CoverageReport, *, color: bool = False) -> tuple[str, ...]:
    """The /test transcript block: one verdict line, then a bar per field —
    green/yellow/red by presence percentage, type misses red."""
    if report.total == 0:
        return (paint("0 rows · nothing to check", "33", color),)
    failed = report.total - report.passed
    verdict = paint(
        f"{report.total} rows · {report.passed} pass, {failed} fail",
        "32" if failed == 0 else "31",
        color,
    )
    width = max(len(field.name) for field in report.fields) if report.fields else 0
    return (
        verdict,
        *(_field_line(field, report.total, width, color=color) for field in report.fields),
    )


def _field_line(field: FieldCoverage, total: int, width: int, *, color: bool) -> str:
    percent = round(100 * field.present / total)
    filled = round(_BAR_CELLS * field.present / total)
    bar = f"[{'█' * filled}{'░' * (_BAR_CELLS - filled)}]"
    tier = "32" if percent >= 90 else "33" if percent >= 50 else "31"
    miss_text = f"{field.misses} type miss" + ("" if field.misses == 1 else "es")
    misses = paint(miss_text, "31", color) if field.misses else miss_text
    return f"  {field.name.ljust(width)} {paint(bar, tier, color)} {percent:>3}% present, {misses}"


# --- the pinned header and the paste-ready lines --------------------------------------------


def header_lines(draft: Sequence[DraftField], *, color: bool = False) -> tuple[str, ...]:
    """The workshop's pinned header: title, the draft as braces, one status
    line (green checkmark while it compiles), and the command menu (dim)."""
    count = len(draft)
    status = (
        paint(f"✓ compiles · {count} field" + ("" if count == 1 else "s"), "32", color)
        if draft
        else paint("empty draft — /add a field, or paste a {braces} draft", "2", color)
    )
    return (
        paint("schema workshop", "1;36", color) + paint(" — free, no model calls", "2", color),
        render_braces(draft, color=color),
        status,
        paint(COMMAND_LINE, "2", color),
    )


def paste_lines(draft: Sequence[DraftField], *, saved: str | None) -> tuple[str, ...]:
    """The two paste-ready lines: the braces string for inline use, and the
    ``--schema PATH`` flag once a save has happened. Empty draft: nothing."""
    if not draft:
        return ()
    lines = ("paste-ready:", f"  '{render_braces(draft)}'")
    return (*lines, f"  --schema {saved}") if saved else lines
