"""The field-path grammar (ledger item 63): one shared mini-grammar, five surfaces.

Tables pin the three layers separately: ``parse_path`` (text → accessors, loud
errors), ``resolve`` (traversal, miss-not-error), and ``lookup`` (the compat
rule: an exact flat key always wins before any path parsing).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.engine.fieldpath import (
    MISSING,
    Accessor,
    Index,
    Key,
    has_path_syntax,
    lookup,
    parse_path,
    resolve,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

# --- parse_path: the grammar -------------------------------------------------------

PARSE_TABLE: tuple[tuple[str, tuple[Accessor, ...]], ...] = (
    ("a", (Key("a"),)),
    ("a.b.c", (Key("a"), Key("b"), Key("c"))),
    ("a.b[0]", (Key("a"), Key("b"), Index(0))),
    ("a.b[-1]", (Key("a"), Key("b"), Index(-1))),
    ("items[2].name", (Key("items"), Index(2), Key("name"))),
    ("a.b['weird key']", (Key("a"), Key("b"), Key("weird key"))),
    ("a['x']['y']", (Key("a"), Key("x"), Key("y"))),
    ("a[0][1]", (Key("a"), Index(0), Index(1))),
    ("a['dots.in.key']", (Key("a"), Key("dots.in.key"))),
    ("a['']", (Key("a"), Key(""))),
    ("_x9.y_2", (Key("_x9"), Key("y_2"))),
)


@pytest.mark.parametrize(("text", "expected"), PARSE_TABLE)
def test_parse_path_table(text: str, expected: tuple[Accessor, ...]) -> None:
    assert parse_path(text) == expected


ERROR_TABLE: tuple[tuple[str, str], ...] = (
    ("a.b[x]", "a.b[x] - index must be a number"),
    ("a[]", "a[] - index must be a number"),
    ("a[1.5]", "a[1.5] - index must be a number"),
    ("a.b.", "a.b. - trailing dot"),
    ("a..b", "a..b - expected a field name after '.'"),
    ("a.[0]", "a.[0] - expected a field name after '.'"),
    ("a.0", "a.0 - expected a field name after '.'"),
    ("a.b[0", "a.b[0 - unclosed '['"),
    ("a['w", "a['w - unclosed quote"),
    ("a['k]", "a['k] - unclosed quote"),
    ("a['k'", "a['k' - expected ']' after the quoted key"),
    (".a", ".a - leading dot"),
    ("[0]", "[0] - a path must start with a field name"),
    ("9a", "9a - a path must start with a field name"),
    ("a-b", "a-b - unexpected '-'"),
    ("a b", "a b - unexpected ' '"),
    ("a[0]b", "a[0]b - unexpected 'b'"),
    ("", "field path is empty"),
)


@pytest.mark.parametrize(("text", "message"), ERROR_TABLE)
def test_parse_path_errors_table(text: str, message: str) -> None:
    with pytest.raises(UsageFault) as excinfo:
        parse_path(text)
    assert str(excinfo.value) == message


# --- resolve: traversal that misses, never raises ----------------------------------

_RECORD: Mapping[str, object] = {
    "user": {"name": "Ada", "plan": "pro", "quota": None},
    "items": [{"name": "first", "total": 12}, {"name": "second", "total": 990}],
    "tags": ["alpha", "beta"],
    "note": "plain",
}

RESOLVE_TABLE: tuple[tuple[str, object], ...] = (
    ("user.name", "Ada"),
    ("items[1].total", 990),
    ("items[-1].name", "second"),
    ("tags[0]", "alpha"),
    ("user.quota", None),  # an explicit null is a VALUE, not a miss
    ("user.missing", MISSING),  # miss at the last hop
    ("ghost.name", MISSING),  # miss at the first hop
    ("user.name.deeper", MISSING),  # a scalar has no fields
    ("note[0]", MISSING),  # strings never index (a string is not a sequence here)
    ("tags.name", MISSING),  # sequences reject Key hops
    ("user[0]", MISSING),  # mappings reject Index hops
    ("items[7]", MISSING),  # out of range
    ("items[-3]", MISSING),  # out of range, negative
)


@pytest.mark.parametrize(("text", "expected"), RESOLVE_TABLE)
def test_resolve_table(text: str, expected: object) -> None:
    found = resolve(_RECORD, parse_path(text))
    assert found == expected if expected is not MISSING else found is MISSING


def test_resolve_empty_path_is_the_record_itself() -> None:
    assert resolve(_RECORD, ()) is _RECORD


# --- lookup: THE COMPAT RULE - exact flat key wins before path parsing -------------


def test_lookup_exact_flat_key() -> None:
    assert lookup({"total": 5}, "total") == 5


def test_lookup_dotted_literal_key_wins_over_traversal() -> None:
    # a CSV header column literally named "user.name" beats the nested path
    record: Mapping[str, object] = {"user.name": "the column", "user": {"name": "nested"}}
    assert lookup(record, "user.name") == "the column"


def test_lookup_traverses_when_no_exact_key() -> None:
    record: Mapping[str, object] = {"user": {"name": "nested"}}
    assert lookup(record, "user.name") == "nested"


def test_lookup_flat_miss_stays_a_miss_without_parsing() -> None:
    # no path punctuation → exact key or nothing; weird flat names never error
    assert lookup({"a": 1}, "b") is MISSING
    assert lookup({"a": 1}, "Total Amount") is MISSING


def test_lookup_exact_key_wins_even_for_malformed_path_text() -> None:
    # a literal column that would not parse as a path is still reachable exactly
    assert lookup({"a.b.": "kept"}, "a.b.") == "kept"


def test_lookup_malformed_path_text_is_loud_when_no_exact_key() -> None:
    with pytest.raises(UsageFault, match=r"a\.b\[x\] - index must be a number"):
        lookup({"a": {"b": [1]}}, "a.b[x]")


def test_lookup_valid_path_miss_is_missing() -> None:
    assert lookup({"user": {"plan": "pro"}}, "user.name") is MISSING


def test_lookup_bracket_paths() -> None:
    record: Mapping[str, object] = {"items": [{"total": 12}], "a": {"weird key": True}}
    assert lookup(record, "items[0].total") == 12
    assert lookup(record, "a['weird key']") is True


# --- has_path_syntax: the flat/path discrimination ----------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("plain", False),
        ("Total Amount", False),
        ("user.name", True),
        ("items[0]", True),
        ("a['k']", True),
    ),
)
def test_has_path_syntax(text: str, expected: bool) -> None:
    assert has_path_syntax(text) is expected
