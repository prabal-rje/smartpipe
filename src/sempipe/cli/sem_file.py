"""Translate a ``.sem`` stage file into the verb argv it stands for (D17).

A ``.sem`` file is TOML pinning exactly one verb invocation; the shebang line
``#!/usr/bin/env -S smartpipe run`` is legal because ``#`` opens a TOML comment.
The format is a public contract from day one, so validation is exhaustive and
loud: unknown keys are *errors* (scripts run unattended — the opposite trade
from config.toml's forward-compat ignore), types are checked with the offending
line echoed back, and ``run``/``config`` can never be scripted.

Pure translation — no click, no I/O beyond reading the file.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING

from sempipe.core.errors import UsageFault
from sempipe.core.jsontools import as_items

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

__all__ = ["Stage", "parse_pipeline", "parse_sem"]

_VERB_NAMES = (
    "map, extend, filter, where, embed, top_k, reduce, join, split, "
    "distinct, outliers, cluster, diff, summarize, sample, getschema, sort, chart"
)


@dataclass(frozen=True, slots=True)
class _KeySpec:
    expected: str  # names the type in the wrong-type screen
    accepts: Callable[[object], bool]
    render: Callable[[object, Path], tuple[str, ...]]


# --- type checks (TOML value space) -------------------------------------------------


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:  # `threshold = 1` is fine; demanding 1.0 is pedantry
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _is_str_list(value: object) -> bool:
    entries = as_items(value)
    return entries is not None and all(isinstance(entry, str) for entry in entries)


def _str_entries(value: object) -> tuple[str, ...]:
    entries = as_items(value)
    assert entries is not None  # accepts() proved it before render runs
    return tuple(entry for entry in entries if isinstance(entry, str))


# --- renderers: TOML value → argv chunk ----------------------------------------------


def _positional(value: object, _sem_dir: Path) -> tuple[str, ...]:
    return (str(value),)


def _flag(name: str) -> Callable[[object, Path], tuple[str, ...]]:
    def render(value: object, _sem_dir: Path) -> tuple[str, ...]:
        return (name, str(value))

    return render


def _switch(name: str) -> Callable[[object, Path], tuple[str, ...]]:
    def render(value: object, _sem_dir: Path) -> tuple[str, ...]:
        return (name,) if value else ()

    return render


def _fields_arg(value: object, _sem_dir: Path) -> tuple[str, ...]:
    return ("--fields", ",".join(_str_entries(value)))


def _globs(value: object, _sem_dir: Path) -> tuple[str, ...]:
    # globs resolve against the CWD, exactly like typing --in at the shell
    return tuple(chain.from_iterable(("--in", pattern) for pattern in _str_entries(value)))


def _schema(value: object, sem_dir: Path) -> tuple[str, ...]:
    assert isinstance(value, str)  # a script and its schema travel together
    return ("--schema", str((sem_dir / value).resolve()))


def _prompt_file(value: object, sem_dir: Path) -> tuple[str, ...]:
    assert isinstance(value, str)  # a script and its prompt travel together (D23)
    return ("--prompt-file", str((sem_dir / value).resolve()))


def _right(value: object, sem_dir: Path) -> tuple[str, ...]:
    assert isinstance(value, str)  # a script and its right side travel together (D21)
    return ("--right", str((sem_dir / value).resolve()))


def _str_key(render: Callable[[object, Path], tuple[str, ...]]) -> _KeySpec:
    return _KeySpec("a string", _is_str, render)


def _int_key(render: Callable[[object, Path], tuple[str, ...]]) -> _KeySpec:
    return _KeySpec("a whole number", _is_int, render)


def _num_key(render: Callable[[object, Path], tuple[str, ...]]) -> _KeySpec:
    return _KeySpec("a number", _is_number, render)


def _bool_key(render: Callable[[object, Path], tuple[str, ...]]) -> _KeySpec:
    return _KeySpec("true or false", _is_bool, render)


def _list_key(render: Callable[[object, Path], tuple[str, ...]]) -> _KeySpec:
    return _KeySpec("an array of strings", _is_str_list, render)


# --- the per-verb key tables (emit order is the tuple order; a public contract) -------

_COMMON_TAIL: tuple[tuple[str, _KeySpec], ...] = (
    ("concurrency", _int_key(_flag("--concurrency"))),
    ("max-calls", _int_key(_flag("--max-calls"))),
    ("in", _list_key(_globs)),
    ("from-files", _bool_key(_switch("--from-files"))),
)

_VERB_KEYS: Mapping[str, tuple[tuple[str, _KeySpec], ...]] = {
    "map": (
        ("prompt", _str_key(_positional)),
        ("prompt-file", _str_key(_prompt_file)),
        ("model", _str_key(_flag("--model"))),
        ("output", _str_key(_flag("--output"))),
        ("fields", _list_key(_fields_arg)),
        ("schema-file", _str_key(_schema)),
        ("schema-from", _str_key(_flag("--schema-from"))),
        *_COMMON_TAIL,
    ),
    "filter": (
        ("prompt", _str_key(_positional)),
        ("allow-captions", _bool_key(_switch("--allow-captions"))),
        ("prompt-file", _str_key(_prompt_file)),
        ("model", _str_key(_flag("--model"))),
        ("not", _bool_key(_switch("--not"))),
        *_COMMON_TAIL,
    ),
    "embed": (
        ("allow-captions", _bool_key(_switch("--allow-captions"))),
        ("embed-model", _str_key(_flag("--embed-model"))),
        ("fields", _list_key(_fields_arg)),
        *_COMMON_TAIL,
    ),
    "top_k": (
        ("k", _int_key(_positional)),
        ("allow-captions", _bool_key(_switch("--allow-captions"))),
        ("near", _str_key(_flag("--near"))),
        ("threshold", _num_key(_flag("--threshold"))),
        ("embed-model", _str_key(_flag("--embed-model"))),
        ("fields", _list_key(_fields_arg)),
        ("stream", _bool_key(_switch("--stream"))),
        *_COMMON_TAIL,
    ),
    "split": (  # no model and no concurrency — split never calls one
        ("by", _str_key(_flag("--by"))),
        ("media", _bool_key(_switch("--media"))),
        ("max-tokens", _int_key(_flag("--max-tokens"))),
        ("in", _list_key(_globs)),
        ("from-files", _bool_key(_switch("--from-files"))),
    ),
    "join": (
        ("prompt", _str_key(_positional)),
        ("allow-captions", _bool_key(_switch("--allow-captions"))),
        ("prompt-file", _str_key(_prompt_file)),
        ("right", _str_key(_right)),
        ("k", _int_key(_flag("--k"))),
        ("threshold", _num_key(_flag("--threshold"))),
        ("model", _str_key(_flag("--model"))),
        ("embed-model", _str_key(_flag("--embed-model"))),
        ("output", _str_key(_flag("--output"))),
        ("fields", _list_key(_fields_arg)),
        *_COMMON_TAIL,
    ),
    "reduce": (
        ("prompt", _str_key(_positional)),
        ("allow-captions", _bool_key(_switch("--allow-captions"))),
        ("prompt-file", _str_key(_prompt_file)),
        ("model", _str_key(_flag("--model"))),
        ("group-by", _str_key(_flag("--group-by"))),
        ("window", _int_key(_flag("--window"))),
        ("every", _int_key(_flag("--every"))),
        ("verbose", _bool_key(_switch("--verbose"))),
        ("schema-file", _str_key(_schema)),
        ("schema-from", _str_key(_flag("--schema-from"))),
        ("tally", _str_key(_flag("--tally"))),
        ("explode", _str_key(_flag("--explode"))),
        ("fields", _list_key(_fields_arg)),
        *_COMMON_TAIL,
    ),
    "where": (("predicate", _str_key(_positional)),),
    "extend": (
        ("prompt", _str_key(_positional)),
        ("prompt-file", _str_key(_prompt_file)),
        ("model", _str_key(_flag("--model"))),
        ("output", _str_key(_flag("--output"))),
        ("fields", _list_key(_fields_arg)),
        ("schema-file", _str_key(_schema)),
        ("schema-from", _str_key(_flag("--schema-from"))),
        ("tally", _str_key(_flag("--tally"))),
        ("explode", _str_key(_flag("--explode"))),
        *_COMMON_TAIL,
    ),
    "distinct": (
        ("show-groups", _bool_key(_switch("--show-groups"))),
        ("threshold", _num_key(_flag("--threshold"))),
        ("allow-captions", _bool_key(_switch("--allow-captions"))),
        ("embed-model", _str_key(_flag("--embed-model"))),
        *_COMMON_TAIL,
    ),
    "outliers": (
        ("count", _int_key(_positional)),
        ("allow-captions", _bool_key(_switch("--allow-captions"))),
        ("embed-model", _str_key(_flag("--embed-model"))),
        *_COMMON_TAIL,
    ),
    "cluster": (
        ("k", _int_key(_flag("--k"))),
        ("top", _int_key(_flag("--top"))),
        ("explode", _str_key(_flag("--explode"))),
        ("model", _str_key(_flag("--model"))),
        ("embed-model", _str_key(_flag("--embed-model"))),
        ("allow-captions", _bool_key(_switch("--allow-captions"))),
        *_COMMON_TAIL,
    ),
    "diff": (
        ("right", _str_key(_right)),
        ("top", _int_key(_flag("--top"))),
        ("all", _bool_key(_switch("--all"))),
        ("model", _str_key(_flag("--model"))),
        ("embed-model", _str_key(_flag("--embed-model"))),
        ("allow-captions", _bool_key(_switch("--allow-captions"))),
        ("concurrency", _int_key(_flag("--concurrency"))),
        ("max-calls", _int_key(_flag("--max-calls"))),
    ),
    "summarize": (("expression", _str_key(_positional)),),
    "sample": (
        ("count", _int_key(_positional)),
        ("seed", _int_key(_flag("--seed"))),
    ),
    "getschema": (("all", _bool_key(_switch("--all"))),),
    "sort": (
        ("by", _str_key(_flag("--by"))),
        ("desc", _bool_key(_switch("--desc"))),
    ),
    "chart": (
        ("field", _str_key(_positional)),
        ("facet", _str_key(_flag("--facet"))),
        ("by-time", _str_key(_flag("--by-time"))),
        ("top", _int_key(_flag("--top"))),
        ("save", _str_key(_flag("--save"))),
        ("title", _str_key(_flag("--title"))),
    ),
}

_REQUIRES_PROMPT = ("map", "extend", "filter", "reduce", "join")
_REQUIRED_KEY: Mapping[str, str] = {
    "where": "predicate",
    "summarize": "expression",
    "sample": "count",
    "sort": "by",
    "diff": "right",
}


def parse_sem(path: Path) -> list[str]:
    """The argv the ``.sem`` file stands for — or a ``UsageFault`` naming what's wrong."""
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise UsageFault(_syntax_screen(path, exc)) from exc
    verb = _checked_verb(path, document)
    table = _VERB_KEYS[verb]
    valid = tuple(key for key, _spec in table)
    unknown = next((key for key in document if key != "verb" and key not in valid), None)
    if unknown is not None:
        raise UsageFault(
            f"{path}: unknown key {unknown!r} — valid keys for {verb}: {', '.join(sorted(valid))}\n"
            "  A .sem script runs unattended — a typo silently ignored would be a disaster.\n"
            f"  Fix the key, then: smartpipe run {path}"
        )
    if verb in _REQUIRES_PROMPT and "prompt" not in document and "prompt-file" not in document:
        raise UsageFault(f'{path}: {verb} needs a prompt\n  Add one: prompt = "..."')
    needed = _REQUIRED_KEY.get(verb)
    if needed is not None and needed not in document:
        raise UsageFault(f'{path}: {verb} needs {needed!r}\n  Add one: {needed} = "..."')
    argv = [verb]
    for key, spec in table:
        if key not in document:
            continue
        value = document[key]
        if not spec.accepts(value):
            raise UsageFault(_wrong_type_screen(path, key, spec.expected, value))
        argv.extend(spec.render(value, path.parent))
    return argv


def _checked_verb(path: Path, document: Mapping[str, object]) -> str:
    verb = document.get("verb")
    if verb is None:
        raise UsageFault(
            f"{path}: 'verb' is required ({_VERB_NAMES})\n"
            "  A .sem file is one saved pipe stage: a verb plus its keys.\n"
            '  Start it with: verb = "map"'
        )
    if not isinstance(verb, str):
        raise UsageFault(_wrong_type_screen(path, "verb", "a string", verb))
    if verb in ("run", "config"):
        spelled_out = _VERB_NAMES.replace(", reduce", ", or reduce")
        raise UsageFault(
            f"{path}: verb {verb!r} can't run from a script — use {spelled_out}\n"
            "  Scripts hold pipe stages; composition and setup stay at the shell."
        )
    if verb not in _VERB_KEYS:
        raise UsageFault(
            f"{path}: verb {verb!r} isn't one of {_VERB_NAMES}\n"
            "  A .sem file is one saved pipe stage: a verb plus its keys."
        )
    return verb


def _wrong_type_screen(path: Path, key: str, expected: str, value: object) -> str:
    return (
        f"{path}: key {key!r} should be {expected}\n"
        f"  got: {key} = {value!r}\n"
        f"  Fix the line, then: smartpipe run {path}"
    )


def _syntax_screen(path: Path, exc: tomllib.TOMLDecodeError) -> str:
    detail = str(exc)
    located = re.search(r"at line (\d+)", detail)
    location = f", line {located.group(1)}" if located else ""
    detail = re.sub(r"\s*\(at line [^)]*\)$", "", detail)
    return (
        f"{path} has a syntax error\n"
        f"  {path}{location}: {detail}\n"
        f"  Fix the line, then: smartpipe run {path}"
    )


# --- multi-stage pipelines (D38/14) ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stage:
    name: str
    argv: tuple[str, ...]
    input_name: str | None  # None: the previous stage (or real stdin for the first)


def parse_pipeline(path: Path) -> tuple[Stage, ...] | None:
    """The stages a multi-stage ``.sem`` stands for; None for single-stage
    files (the original format is the degenerate case and stays untouched)."""
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise UsageFault(_syntax_screen(path, exc)) from exc
    from sempipe.core.jsontools import as_record

    raw_stages = document.get("stage")
    if raw_stages is None:
        return None
    stages_table = as_record(raw_stages)
    if stages_table is None:
        raise UsageFault(f"{path}: [stage.NAME] tables expected under 'stage'")
    others = [key for key in document if key != "stage"]
    if others:
        raise UsageFault(
            f"{path}: a pipeline file holds only [stage.NAME] tables — "
            f"found top-level {others[0]!r}\n"
            "  Single-stage files use top-level keys; pick one shape."
        )
    stages: list[Stage] = []
    seen: list[str] = []
    for name, raw_body in stages_table.items():
        body = as_record(raw_body)
        if body is None:
            raise UsageFault(f"{path}: [stage.{name}] must be a table of keys")
        upstream_value = body.get("input")
        upstream: str | None = None
        if upstream_value is not None:
            if not isinstance(upstream_value, str):
                raise UsageFault(
                    _wrong_type_screen(path, f"stage.{name}.input", "a string", upstream_value)
                )
            upstream = upstream_value
            if upstream not in seen:
                raise UsageFault(
                    f"{path}: stage {name!r} reads input = {upstream!r}, "
                    "which isn't an EARLIER stage\n"
                    "  Stages run in file order; input names one above."
                )
        stage_doc = {key: value for key, value in body.items() if key != "input"}
        argv = _stage_argv(path, name, stage_doc)
        stages.append(Stage(name=name, argv=tuple(argv), input_name=upstream))
        seen.append(name)
    if not stages:
        raise UsageFault(f"{path}: a pipeline needs at least one [stage.NAME]")
    return tuple(stages)


def _stage_argv(path: Path, name: str, document: dict[str, object]) -> list[str]:
    verb = _checked_verb(path, document)
    table = _VERB_KEYS[verb]
    valid = tuple(key for key, _spec in table)
    unknown = next((key for key in document if key != "verb" and key not in valid), None)
    if unknown is not None:
        raise UsageFault(
            f"{path}: [stage.{name}] unknown key {unknown!r} — "
            f"valid keys for {verb}: {', '.join(sorted(valid))}"
        )
    if verb in _REQUIRES_PROMPT and "prompt" not in document and "prompt-file" not in document:
        raise UsageFault(f"{path}: [stage.{name}] {verb} needs a prompt")
    needed = _REQUIRED_KEY.get(verb)
    if needed is not None and needed not in document:
        raise UsageFault(f"{path}: [stage.{name}] {verb} needs {needed!r}")
    argv = [verb]
    for key, spec in table:
        if key not in document:
            continue
        value = document[key]
        if not spec.accepts(value):
            raise UsageFault(_wrong_type_screen(path, f"stage.{name}.{key}", spec.expected, value))
        argv.extend(spec.render(value, path.parent))
    return argv
