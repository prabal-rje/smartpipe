from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from sempipe.io.items import ItemSource, describe_source, item_from_line


def test_plain_line() -> None:
    item = item_from_line("hello world\n", 0)
    assert item.raw == "hello world"
    assert item.text == "hello world"
    assert item.data is None
    assert item.source == ItemSource(kind="stdin", name="-", index=0)


def test_json_object_line_sets_data_and_keeps_raw_verbatim() -> None:
    line = '{"b": 1, "a": 2}\n'
    item = item_from_line(line, 3)
    assert item.raw == '{"b": 1, "a": 2}'  # key order and spacing untouched
    assert item.data == {"b": 1, "a": 2}
    assert list(item.data or {}) == ["b", "a"]  # insertion order preserved


def test_json_scalar_and_array_lines_do_not_set_data() -> None:
    assert item_from_line("42\n", 0).data is None
    assert item_from_line('"text"\n', 0).data is None
    assert item_from_line("[1, 2]\n", 0).data is None


def test_malformed_json_starting_with_brace_is_just_text() -> None:
    item = item_from_line("{not json\n", 0)
    assert item.data is None
    assert item.raw == "{not json"


def test_crlf_stripped() -> None:
    assert item_from_line("hello\r\n", 0).raw == "hello"
    assert item_from_line("hello\r", 0).raw == "hello"  # split('\n') leaves the \r


def test_bom_stripped_on_first_line_only() -> None:
    assert item_from_line("﻿hello\n", 0).raw == "hello"
    assert item_from_line("﻿hello\n", 1).raw == "﻿hello"


def test_empty_line_is_a_valid_item() -> None:
    item = item_from_line("\n", 5)
    assert item.raw == ""
    assert item.source.index == 5


def test_describe_source_is_human_one_based() -> None:
    assert describe_source(ItemSource(kind="stdin", name="-", index=11)) == "line 12"
    file_source = ItemSource(kind="file", name="reports/a.pdf", index=0)
    assert describe_source(file_source) == "reports/a.pdf"


@given(
    body=st.text(alphabet=st.characters(exclude_characters="\n\r")),
    suffix=st.sampled_from(["", "\n", "\r\n"]),
    index=st.integers(min_value=0, max_value=1000),
)
def test_line_shaped_input_round_trips(body: str, suffix: str, index: int) -> None:
    item = item_from_line(body + suffix, index)
    expected = body.removeprefix("﻿") if index == 0 else body
    assert item.raw == expected


@given(
    body=st.text(alphabet=st.characters(blacklist_characters="\r\n")),
    terminator=st.sampled_from(["", "\n", "\r\n"]),
    index=st.integers(min_value=0, max_value=1000),
)
def test_never_raises_and_never_keeps_trailing_newline(
    body: str, terminator: str, index: int
) -> None:
    # the reader contract: a line carries AT MOST one terminator (readline/pump
    # split on \n) — hypothesis found the old any-text strategy admitted "\n\n",
    # which no reader can produce
    item = item_from_line(body + terminator, index)
    assert not item.raw.endswith("\n")
    assert not item.raw.endswith("\r")
    assert item.raw == body if index > 0 or not body.startswith("\ufeff") else True


def test_multi_newline_input_strips_exactly_one_terminator() -> None:
    # documents the single-strip semantics for inputs outside the reader contract
    assert item_from_line("\n\n", 0).raw == "\n"
