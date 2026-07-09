"""``render_input`` (ledger item 57): the model-facing ``<input>`` block.

Records render as a minimal YAML-ish block (input key order, ``__`` spine and
``__media`` transport excluded); plain text rides unchanged. Both wear the
``<input>`` fences — the batching-prerequisite shape (a later feature numbers
them ``<input_1>``).
"""

from __future__ import annotations

from smartpipe.engine.prompts import render_input
from smartpipe.io.items import item_from_line


def test_plain_text_is_fenced_unchanged() -> None:
    item = item_from_line("an ERROR occurred\n", 0)
    assert render_input(item) == "<input>\nan ERROR occurred\n</input>"


def test_a_raw_string_payload_is_fenced_unchanged() -> None:
    # chunk/transcript call sites hold text, not an Item — same fence
    assert render_input("half a document") == "<input>\nhalf a document\n</input>"


def test_record_renders_yaml_ish_in_input_order() -> None:
    item = item_from_line('{"id": 812, "customer": "acme", "body": "crashes on save"}', 0)
    assert render_input(item) == (
        "<input>\nid: 812\ncustomer: acme\nbody: crashes on save\n</input>"
    )


def test_scalars_keep_their_json_spelling() -> None:
    item = item_from_line('{"n": 1.5, "ok": true, "note": null}', 0)
    assert render_input(item) == "<input>\nn: 1.5\nok: true\nnote: null\n</input>"


def test_lists_render_as_dash_rows() -> None:
    item = item_from_line('{"tags": ["ui", "urgent"], "empty": []}', 0)
    assert render_input(item) == "<input>\ntags:\n  - ui\n  - urgent\nempty: []\n</input>"


def test_nested_records_indent_two_spaces() -> None:
    item = item_from_line('{"meta": {"region": "eu", "tier": 2}, "empty": {}}', 0)
    assert render_input(item) == ("<input>\nmeta:\n  region: eu\n  tier: 2\nempty: {}\n</input>")


def test_multi_line_strings_render_as_indented_blocks() -> None:
    item = item_from_line('{"body": "line one\\nline two", "id": 1}', 0)
    assert render_input(item) == "<input>\nbody:\n  line one\n  line two\nid: 1\n</input>"


def test_spine_and_media_transport_never_reach_the_model() -> None:
    line = (
        '{"id": 7, "__source": {"path": "a.jsonl", "as": "jsonl", "line": 1},'
        ' "__score": 0.9, "__media": {"kind": "image", "mime": "image/png", "data_b64": "aGk="}}'
    )
    rendered = render_input(item_from_line(line, 0))
    assert rendered == "<input>\nid: 7\n</input>"


def test_pure_text_record_projects_to_its_text() -> None:
    # the shared text-projection rule: a reader-fed {"text": …} row prompts
    # exactly like the raw line would
    item = item_from_line('{"text": "hello there"}', 0)
    assert render_input(item) == "<input>\nhello there\n</input>"


def test_empty_payloads_render_nothing() -> None:
    # an image-only item has no text — the media rides the API parts instead
    from dataclasses import replace

    item = replace(item_from_line("x", 0), text="")
    assert render_input(item) == ""
    assert render_input("") == ""


def test_deep_structures_inside_lists_stay_compact_json() -> None:
    item = item_from_line('{"rows": [{"a": 1}, [2, 3]]}', 0)
    assert render_input(item) == '<input>\nrows:\n  - {"a":1}\n  - [2,3]\n</input>'


def test_a_record_with_only_spine_fields_renders_nothing() -> None:
    item = item_from_line('{"__score": 0.4}', 0)
    assert render_input(item) == ""
