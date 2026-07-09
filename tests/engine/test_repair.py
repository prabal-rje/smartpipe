"""Rung 0 of the structured-output ladder (item 58): deterministic JSON repair.

Table-driven: each transform alone, transforms in combination, and adversarial
non-JSON (``None``, never an exception). ``repair_json`` returns a *string*
that ``json.loads`` accepts so the repaired reply re-enters the existing
schema-coercion path unchanged.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smartpipe.engine.repair import repair_json


def _roundtrip(text: str) -> object:
    repaired = repair_json(text)
    assert repaired is not None, f"repair_json gave up on: {text!r}"
    return json.loads(repaired)


# --- each transform on its own --------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        # markdown code fences
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ("```json\n[1, 2]\n```", [1, 2]),
        # leading/trailing prose around the island
        ('Here you go:\n{"a": 1}\nHope that helps!', {"a": 1}),
        ("The list is [1, 2, 3] as requested.", [1, 2, 3]),
        # trailing commas
        ('{"a": 1,}', {"a": 1}),
        ("[1, 2,]", [1, 2]),
        ('{"a": [1, 2,],}', {"a": [1, 2]}),
        # single quotes (attempt-and-parse, never a blind swap)
        ("{'a': 'b'}", {"a": "b"}),
        ("{'a': ['x', 'y']}", {"a": ["x", "y"]}),
        # unquoted keys
        ('{key: "v"}', {"key": "v"}),
        ('{alpha: 1, beta_2: "x"}', {"alpha": 1, "beta_2": "x"}),
        # Python literals
        ('{"a": True, "b": False, "c": None}', {"a": True, "b": False, "c": None}),
    ],
)
def test_single_transforms(reply: str, expected: object) -> None:
    assert _roundtrip(reply) == expected


# --- combinations ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        # fence + trailing comma
        ('```json\n{"a": 1,}\n```', {"a": 1}),
        # fence + Python literals + trailing comma (a Python-repr reply)
        ("```json\n{'a': True, 'b': None,}\n```", {"a": True, "b": None}),
        # prose + fence + island
        ('Sure!\n```json\n{"a": 1}\n```\nLet me know.', {"a": 1}),
        # prose + single quotes
        ("Here: {'a': 'b'} done.", {"a": "b"}),
        # unquoted keys + Python literals + single quotes
        ("{flag: True, name: 'x'}", {"flag": True, "name": "x"}),
        # unquoted keys + trailing comma
        ('{key: "v",}', {"key": "v"}),
    ],
)
def test_combined_transforms(reply: str, expected: object) -> None:
    assert _roundtrip(reply) == expected


# --- fidelity: string interiors are sacred ---------------------------------------


def test_python_literals_inside_strings_survive() -> None:
    # the words True/None INSIDE a string value must not be rewritten
    assert _roundtrip("{'said': 'True story', 'x': None}") == {"said": "True story", "x": None}


def test_literal_fix_outside_strings_only() -> None:
    # unquoted keys force the regex path; the in-string "True" stays capitalized
    assert _roundtrip('{note: "True story", flag: True}') == {"note": "True story", "flag": True}


def test_trailing_comma_inside_string_survives() -> None:
    assert _roundtrip('{"a": "x,}", "b": 1,}') == {"a": "x,}", "b": 1}


def test_apostrophe_inside_single_quoted_reply() -> None:
    assert _roundtrip("{'a': \"it's fine\"}") == {"a": "it's fine"}


# --- already-valid input --------------------------------------------------------


def test_valid_json_returns_itself() -> None:
    assert repair_json('{"a": 1}') == '{"a": 1}'
    assert repair_json("  [1, 2]\n") == "[1, 2]"


# --- adversarial: None, never an exception ---------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "   \n\t",
        "I cannot do that",
        "not json at all",
        "{{{",
        "}",
        "```json\n```",
        "{'a': }",
        "[1, 2",  # no closing bracket ever appears
        "{unclosed: ",
        '{"a": 1} extra } garbage {',  # island parses? last } after prose braces
    ],
)
def test_hopeless_replies_return_none_or_parse(reply: str) -> None:
    repaired = repair_json(reply)
    if repaired is not None:  # whatever comes back MUST parse — that is the contract
        json.loads(repaired)


def test_plain_prose_is_none() -> None:
    assert repair_json("I cannot help with that request.") is None


def test_python_set_literal_is_none() -> None:
    # literal_eval succeeds but a set has no JSON container shape
    assert repair_json("{1, 2, 3}") is None


def test_python_bytes_value_is_none() -> None:
    # a dict literal whose value has no JSON spelling
    assert repair_json("{'a': b'bytes'}") is None


def test_empty_is_none() -> None:
    assert repair_json("") is None


@given(st.text(max_size=500))
def test_never_raises_and_result_always_parses(text: str) -> None:
    repaired = repair_json(text)
    if repaired is not None:
        json.loads(repaired)
