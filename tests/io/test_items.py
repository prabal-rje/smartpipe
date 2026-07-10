from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smartpipe.io.items import ItemSource, describe_source, item_from_line, source_record


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


# --- the __ metadata namespace (wave 2, item 12) --------------------------------


def _media_line(kind: str, data: bytes, mime: str, **extra: object) -> str:
    import base64
    import json

    record: dict[str, object] = {
        "__media": {"kind": kind, "mime": mime, "data_b64": base64.b64encode(data).decode()},
        **extra,
    }
    return json.dumps(record) + "\n"


def test_media_spine_rebuilds_audio_bytes() -> None:
    from smartpipe.models.base import AudioData

    item = item_from_line(_media_line("audio", b"RIFFfake", "audio/wav", source="call.wav"), 0)
    assert len(item.media) == 1 and isinstance(item.media[0], AudioData)
    assert item.media[0].data == b"RIFFfake"
    assert item.media[0].mime == "audio/wav"


def test_media_spine_list_rebuilds_every_part() -> None:
    import base64
    import json

    from smartpipe.models.base import ImageData

    parts = [
        {"kind": "image", "mime": "image/png", "data_b64": base64.b64encode(b"one").decode()},
        {"kind": "image", "mime": "image/jpeg", "data_b64": base64.b64encode(b"two").decode()},
    ]
    item = item_from_line(json.dumps({"__media": parts, "text": "page"}) + "\n", 0)
    assert [type(part) for part in item.media] == [ImageData, ImageData]
    assert item.text == "page"  # the record's text field, not the raw JSON


def test_media_spine_bad_payload_reads_as_plain_record() -> None:
    import json

    line = json.dumps({"__media": {"kind": "audio", "mime": "x", "data_b64": "@@@"}}) + "\n"
    item = item_from_line(line, 0)
    assert item.media == ()


def test_unknown_meta_field_warns_once_and_carries_through(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import smartpipe.io.items as items_module

    items_module._warned_meta.clear()  # pyright: ignore[reportPrivateUsage] — test isolation
    first = item_from_line('{"__custom": 1, "a": 2}\n', 0)
    second = item_from_line('{"__custom": 3}\n', 1)
    assert first.data is not None and first.data["__custom"] == 1  # carried, never dropped
    assert second.data is not None
    err = capsys.readouterr().err
    assert err.count("__custom") == 1  # warned once, not per line
    assert "reserved" in err


def test_known_meta_fields_never_warn(capsys: pytest.CaptureFixture[str]) -> None:
    import smartpipe.io.items as items_module

    items_module._warned_meta.clear()  # pyright: ignore[reportPrivateUsage] — test isolation
    item_from_line('{"__score": 0.5, "__invalid": true, "__error": "e", "__raw": "r"}\n', 0)
    item_from_line('{"__rank": 1, "__snapshot": 2, "__distance": 0.7}\n', 1)  # item 76 stamps
    assert capsys.readouterr().err == ""


def test_single_underscore_fields_are_user_data(capsys: pytest.CaptureFixture[str]) -> None:
    item = item_from_line('{"_mine": 1}\n', 0)
    assert item.data is not None
    assert capsys.readouterr().err == ""  # one leading underscore belongs to the user


# --- __source: granularity in the spine (wave 2, item 13) ------------------------


def test_plain_line_is_cut_as_lines() -> None:
    item = item_from_line("hello\n", 4)
    assert item.source.cut == "lines"
    assert source_record(item.source) == {"path": "-", "as": "lines", "line": 5}


def test_record_line_is_cut_as_jsonl() -> None:
    item = item_from_line('{"a": 1}\n', 0)
    assert item.source.cut == "jsonl"
    assert source_record(item.source) == {"path": "-", "as": "jsonl", "line": 1}


def test_file_item_is_cut_as_file() -> None:
    from smartpipe.io.items import item_from_file

    item = item_from_file("body", "reports/a.pdf", 2)
    assert item.source.cut == "file"
    assert source_record(item.source) == {"path": "reports/a.pdf", "as": "file"}


def test_incoming_source_record_is_adopted() -> None:
    import json

    line = (
        json.dumps(
            {
                "text": "chunk",
                "__source": {
                    "path": "report.pdf",
                    "as": "tokens",
                    "segment": 3,
                    "label": "report.pdf §3/12",
                },
            }
        )
        + "\n"
    )
    item = item_from_line(line, 9)
    assert item.source.cut == "tokens"
    assert describe_source(item.source) == "report.pdf §3/12"  # the label speaks for humans
    assert source_record(item.source) == {
        "path": "report.pdf",
        "as": "tokens",
        "segment": 3,
        "label": "report.pdf §3/12",
    }


def test_source_record_uses_page_and_segment_keys() -> None:
    from smartpipe.io.items import ItemSource

    pages = ItemSource(kind="file", name="r.pdf", index=1, cut="pages")
    assert source_record(pages) == {"path": "r.pdf", "as": "pages", "page": 2}
    seconds = ItemSource(kind="stdin", name="call.wav", index=0, cut="seconds")
    assert source_record(seconds) == {"path": "call.wav", "as": "seconds", "segment": 1}


def test_legacy_embed_row_source_is_adopted() -> None:
    """Pre-1.5 embed rows carried `"source": "name"` — read for one release."""
    item = item_from_line('{"text": "t", "vector": [1.0, 0.0], "source": "a.md"}', 4)
    assert item.source.name == "a.md"
    assert item.source.path == "a.md"
    assert describe_source(item.source) == "a.md"


def test_plain_source_field_without_vector_stays_user_data() -> None:
    item = item_from_line('{"text": "t", "source": "a.md"}', 0)
    assert item.source.name == "-"  # no vector → not an embed row; never hijack user fields


def test_embedder_stamp_is_known_meta(capsys: pytest.CaptureFixture[str]) -> None:
    from smartpipe.io import items as items_module

    items_module._warned_meta.clear()  # pyright: ignore[reportPrivateUsage] — test isolation
    item_from_line('{"text": "t", "__embedder": "jina/jina-clip-v2"}', 0)
    assert "__embedder" not in capsys.readouterr().err  # a known field never warns


def test_pair_sources_are_known_meta(capsys: pytest.CaptureFixture[str]) -> None:
    """join's __sources (item 64) round-trips like any known spine field."""
    from smartpipe.io import items as items_module

    items_module._warned_meta.clear()  # pyright: ignore[reportPrivateUsage] — test isolation
    item_from_line('{"left": {}, "right": {}, "__sources": []}', 0)
    assert "__sources" not in capsys.readouterr().err
