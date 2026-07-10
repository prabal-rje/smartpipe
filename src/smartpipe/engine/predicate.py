"""The ``where`` predicate grammar (D38/01): a closed, KQL-flavored menu.

Pure: parse once into a small AST, evaluate per item with zero I/O. Missing
fields evaluate false (KQL behavior — streams keep flowing) but are tallied so
the verb can disclose them at the end; silence must never lie.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, assert_never

from smartpipe.core.errors import UsageFault
from smartpipe.engine.temporal import temporal_key

if TYPE_CHECKING:
    from smartpipe.io.items import Item

__all__ = [
    "WHERE_MENU",
    "Compare",
    "Contains",
    "FieldTally",
    "Has",
    "Matches",
    "Predicate",
    "evaluate",
    "parse_predicate",
    "referenced_fields",
]

WHERE_MENU = (
    "error: can't parse the where predicate\n"
    '  Operators: FIELD has "word" · FIELD contains "text" · FIELD matches /re/\n'
    '             FIELD == VALUE · != · > · >= · < · <=   (numbers or "strings")\n'
    "  Combine:   and · or · not · ( )     FIELD is a record field, or text\n"
    '  Example:   text has "ERROR" and not text contains "retry"\n'
    "  Semantic condition instead? smartpipe filter judges with a model."
)


@dataclass(frozen=True, slots=True)
class Has:
    field: str
    word: str


@dataclass(frozen=True, slots=True)
class Contains:
    field: str
    text: str


@dataclass(frozen=True, slots=True)
class Matches:
    field: str
    pattern: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class Compare:
    field: str
    op: Literal["==", "!=", ">", ">=", "<", "<="]
    value: str | float


@dataclass(frozen=True, slots=True)
class And:
    left: Predicate
    right: Predicate


@dataclass(frozen=True, slots=True)
class Or:
    left: Predicate
    right: Predicate


@dataclass(frozen=True, slots=True)
class Not:
    inner: Predicate


Predicate = Has | Contains | Matches | Compare | And | Or | Not


@dataclass(slots=True)
class FieldTally:
    """Mutable disclosure state owned by the verb: what silence would hide."""

    missing: Counter[str] = field(default_factory=Counter[str])
    non_numeric: Counter[str] = field(default_factory=Counter[str])


# --- tokenizer --------------------------------------------------------------------

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<op>==|!=|>=|<=|>|<)
      | (?P<string>"(?P<sbody>[^"]*)")
      | (?P<sopen>")
      | (?P<regex>/(?P<rbody>(?:\\/|[^/])*)/)
      | (?P<number>-?\d+(?:\.\d+)?)
      | (?P<word>[A-Za-z_][A-Za-z0-9_.]*)
    )""",
    re.VERBOSE,
)

_KEYWORDS = frozenset({"and", "or", "not", "has", "contains", "matches"})


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            remainder = text[position:].strip()
            if not remainder:
                break
            raise UsageFault(WHERE_MENU + f"\n  (stuck at: {remainder[:30]!r})")
        if match.lastgroup is None or match.end() == position:
            break
        kind = match.lastgroup
        if kind == "sopen":
            raise UsageFault(WHERE_MENU + "\n  (unclosed string literal)")
        if kind == "string":
            tokens.append(("string", match.group("sbody")))
        elif kind == "regex":
            tokens.append(("regex", match.group("rbody").replace("\\/", "/")))
        elif kind == "word":
            word = match.group("word")
            tokens.append((word, word) if word in _KEYWORDS else ("field", word))
        else:
            tokens.append((kind, match.group(kind) or match.group(0).strip()))
        position = match.end()
    return tokens


# --- recursive descent: or > and > not > leaf --------------------------------------


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.position = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise UsageFault(WHERE_MENU + "\n  (predicate ends mid-expression)")
        self.position += 1
        return token

    def parse_or(self) -> Predicate:
        node = self.parse_and()
        while (token := self.peek()) is not None and token[0] == "or":
            self.take()
            node = Or(node, self.parse_and())
        return node

    def parse_and(self) -> Predicate:
        node = self.parse_not()
        while (token := self.peek()) is not None and token[0] == "and":
            self.take()
            node = And(node, self.parse_not())
        return node

    def parse_not(self) -> Predicate:
        if (token := self.peek()) is not None and token[0] == "not":
            self.take()
            return Not(self.parse_not())
        return self.parse_leaf()

    def parse_leaf(self) -> Predicate:
        kind, value = self.take()
        if kind == "lparen":
            inner = self.parse_or()
            closing = self.peek()
            if closing is None or closing[0] != "rparen":
                raise UsageFault(WHERE_MENU + "\n  (missing closing ')')")
            self.take()
            return inner
        if kind != "field":
            raise UsageFault(WHERE_MENU + f"\n  (expected a field name, got {value!r})")
        return self.parse_operator(value)

    def parse_operator(self, field_name: str) -> Predicate:
        kind, value = self.take()
        match kind:
            case "has" | "contains":
                text_kind, text_value = self.take()
                if text_kind != "string":
                    raise UsageFault(WHERE_MENU + f'\n  ({kind} needs a "quoted string")')
                return (
                    Has(field_name, text_value)
                    if kind == "has"
                    else Contains(field_name, text_value)
                )
            case "matches":
                regex_kind, regex_value = self.take()
                if regex_kind != "regex":
                    raise UsageFault(WHERE_MENU + "\n  (matches needs /a regex/)")
                try:
                    return Matches(field_name, re.compile(regex_value))
                except re.error as exc:
                    raise UsageFault(WHERE_MENU + f"\n  (bad regex: {exc})") from exc
            case "op":
                literal_kind, literal_value = self.take()
                if literal_kind == "number":
                    return Compare(field_name, _as_op(value), float(literal_value))
                if literal_kind == "string":
                    return Compare(field_name, _as_op(value), literal_value)
                if literal_kind == "field" and literal_value in ("true", "false"):
                    # booleans compare as their JSON spelling
                    return Compare(field_name, _as_op(value), literal_value)
                raise UsageFault(WHERE_MENU + f'\n  ({value} needs a number or "string")')
            case _:
                raise UsageFault(WHERE_MENU + f"\n  (expected an operator after {field_name!r})")


def _as_op(text: str) -> Literal["==", "!=", ">", ">=", "<", "<="]:
    match text:
        case "==" | "!=" | ">" | ">=" | "<" | "<=":
            return text
        case _ as unreachable:  # pragma: no cover — the tokenizer only emits the six
            raise AssertionError(unreachable)


def parse_predicate(text: str) -> Predicate:
    parser = _Parser(_tokenize(text))
    node = parser.parse_or()
    leftover = parser.peek()
    if leftover is not None:
        raise UsageFault(WHERE_MENU + f"\n  (unexpected trailing {leftover[1]!r})")
    return node


# --- evaluation --------------------------------------------------------------------


def evaluate(predicate: Predicate, item: Item, tally: FieldTally) -> bool:
    match predicate:
        case And(left, right):
            return evaluate(left, item, tally) and evaluate(right, item, tally)
        case Or(left, right):
            return evaluate(left, item, tally) or evaluate(right, item, tally)
        case Not(inner):
            return not evaluate(inner, item, tally)
        case Has(field_name, word):
            value = _resolve(field_name, item, tally)
            if value is None:
                return False
            boundary = rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])"
            return re.search(boundary, _as_text(value), re.IGNORECASE) is not None
        case Contains(field_name, needle):
            value = _resolve(field_name, item, tally)
            return value is not None and needle.lower() in _as_text(value).lower()
        case Matches(field_name, pattern):
            value = _resolve(field_name, item, tally)
            return value is not None and pattern.search(_as_text(value)) is not None
        case Compare(field_name, op, expected):
            value = _resolve(field_name, item, tally)
            if value is None:
                return False
            return _compare(field_name, value, op, expected, tally)
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)


def referenced_fields(predicate: Predicate) -> frozenset[str]:
    """Every field name the predicate reads — pure AST walk. ``where`` uses it
    to tell a true field-miss from a plain line judged by ``text`` alone
    (item 19: only the former is a strict-rows matter)."""
    match predicate:
        case And(left, right) | Or(left, right):
            return referenced_fields(left) | referenced_fields(right)
        case Not(inner):
            return referenced_fields(inner)
        case Has(field_name, _) | Contains(field_name, _) | Matches(field_name, _):
            return frozenset((field_name,))
        case Compare(field_name, _, _):
            return frozenset((field_name,))
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)


def _resolve(field_name: str, item: Item, tally: FieldTally) -> object | None:
    if field_name == "text":
        return item.text
    value = item.data.get(field_name) if item.data is not None else None
    if value is None:
        tally.missing[field_name] += 1
    return value


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else str(value)


def _numeric(value: object) -> float | None:
    if isinstance(value, bool):  # bool is int; keep booleans out of arithmetic
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _comparable(value: object, expected: str | float) -> tuple[float, float] | None:
    """Both sides on one axis: numbers numerically, ISO dates/datetimes
    temporally (item 56 — dates promote to midnight); otherwise None and the
    string/equality rules keep the wheel."""
    ours = _numeric(value)
    theirs = _numeric(expected)
    if ours is not None and theirs is not None:
        return ours, theirs
    ours = temporal_key(value)
    theirs = temporal_key(expected)
    if ours is not None and theirs is not None:
        return ours, theirs
    return None


def _compare(
    field_name: str,
    value: object,
    op: Literal["==", "!=", ">", ">=", "<", "<="],
    expected: str | float,
    tally: FieldTally,
) -> bool:
    pair = _comparable(value, expected)
    if pair is not None:
        ours, theirs = pair
        match op:
            case "==":
                return ours == theirs
            case "!=":
                return ours != theirs
            case ">":
                return ours > theirs
            case ">=":
                return ours >= theirs
            case "<":
                return ours < theirs
            case "<=":
                return ours <= theirs
    if op in ("==", "!="):
        # string equality — booleans/None compare as their JSON spelling
        rendered = _as_text(value) if not isinstance(value, bool) else str(value).lower()
        return (rendered == _as_text(expected)) is (op == "==")
    tally.non_numeric[field_name] += 1  # ordered compare on a non-number: no match
    return False
