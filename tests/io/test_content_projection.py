"""The text-projection rule (ledger item 40, deliverable 4).

A record flowing into an embedding verb must embed its MEANING, not its
serialized wrapper — the ``__`` spine never reaches a model.
"""

from __future__ import annotations

from smartpipe.io.items import Item, ItemSource, content_text, item_from_line, project_content
from smartpipe.models.base import ImageData


def _line(raw: str, index: int = 0) -> Item:
    return item_from_line(raw, index)


def test_plain_text_is_itself() -> None:
    item = _line("hello world")
    assert content_text(item) == "hello world"


def test_text_only_record_projects_the_text_field() -> None:
    item = _line('{"text": "hello world"}')
    assert content_text(item) == "hello world"


def test_reader_wrapped_text_record_projects_identically_to_the_raw_line() -> None:
    spine = '"__source": {"path": "n.txt", "as": "lines", "line": 7}'
    wrapped = _line('{"text": "hello world", ' + spine + "}")
    direct = _line("hello world")
    assert content_text(wrapped) == content_text(direct)


def test_record_without_spine_keeps_its_raw_bytes() -> None:
    item = _line('{"a":  1, "b": "x"}')
    assert content_text(item) == '{"a":  1, "b": "x"}'  # byte-identical: today's behavior


def test_record_with_spine_serializes_content_fields_only() -> None:
    item = _line('{"a": 1, "__source": {"path": "n.jsonl", "as": "jsonl", "line": 1}}')
    assert content_text(item) == '{"a": 1}'


def test_non_string_text_field_stays_a_record() -> None:
    item = _line('{"text": 5, "__source": {"path": "n", "as": "jsonl", "line": 1}}')
    assert content_text(item) == '{"text": 5}'


def test_project_content_rewrites_only_spined_records() -> None:
    plain = _line("hello")
    assert project_content(plain) is plain
    wrapped = _line('{"text": "hi", "__source": {"path": "n.txt", "as": "lines", "line": 1}}')
    assert project_content(wrapped).text == "hi"
    assert project_content(wrapped).data == wrapped.data  # only the projection changes


def test_project_content_leaves_media_items_alone() -> None:
    source = ItemSource(kind="file", name="p.png", index=0)
    item = Item(raw="", text="", data=None, source=source, media=(ImageData(b"x", "image/png"),))
    assert project_content(item) is item
