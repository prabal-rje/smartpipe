"""The distinct verb: exact folds free, near folds by meaning, silence discloses."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode
from smartpipe.models.base import ModelRef

if True:  # runtime import for the Protocol annotation
    from smartpipe.models.base import ChatModel
from smartpipe.verbs.distinct import DistinctRequest, run_distinct

if TYPE_CHECKING:
    from collections.abc import Sequence


VECTORS: dict[str, tuple[float, ...]] = {
    "app crashes when saving": (1.0, 0.0),
    "saving crashes the app!!": (0.995, 0.0999),  # near-dup of the first
    "dark mode please": (0.0, 1.0),
}


class FakeEmbedding:
    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake-embed")
        self.seen: list[str] = []

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.seen.extend(texts)
        return tuple(VECTORS[text] for text in texts)


class FakeContext:
    def __init__(self) -> None:
        self.embedder = FakeEmbedding()

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        raise RuntimeError("no chat configured")  # optional_chat handles this

    async def embedding_model(self, flag: str | None = None) -> FakeEmbedding:
        return self.embedder

    def concurrency(self, flag: int | None = None) -> int:
        return 2

    def document_parser(self, flag: str | None = None) -> None:
        return None

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None


async def _run(stdin_text: str, **kwargs: object) -> tuple[ExitCode, str, str, FakeContext]:
    import contextlib

    context = FakeContext()
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = await run_distinct(
            DistinctRequest(**kwargs),  # type: ignore[arg-type]
            context,
            stdin=io.StringIO(stdin_text),
            stdout=out,
        )
    return code, out.getvalue(), err.getvalue(), context


async def test_exact_duplicates_fold_before_any_embedding() -> None:
    stdin_text = "dark mode please\ndark mode please\ndark mode please\n"
    code, out, err, context = await _run(stdin_text)
    assert code is ExitCode.OK
    assert out == "dark mode please\n"
    assert context.embedder.seen == ["dark mode please"]  # one embed, not three
    assert "kept 1 of 3 (2 exact + 0 near duplicates folded)" in err


async def test_near_duplicates_fold_by_meaning_first_wins() -> None:
    stdin_text = "app crashes when saving\nsaving crashes the app!!\ndark mode please\n"
    code, out, err, _context = await _run(stdin_text)
    assert code is ExitCode.OK
    assert out == "app crashes when saving\ndark mode please\n"  # order + bytes kept
    assert "kept 2 of 3 (0 exact + 1 near duplicates folded)" in err


async def test_show_groups_is_the_audit_trail() -> None:
    stdin_text = "app crashes when saving\nsaving crashes the app!!\napp crashes when saving\n"
    _code, out, _err, _context = await _run(stdin_text, show_groups=True)
    rows = [json.loads(line) for line in out.splitlines()]
    assert rows == [
        {
            "kept": "app crashes when saving",
            "count": 3,
            "duplicates": ["saving crashes the app!!", "app crashes when saving"],
        }
    ]


async def test_empty_input_is_ok_and_silent() -> None:
    code, out, _err, context = await _run("")
    assert code is ExitCode.OK
    assert out == ""
    assert context.embedder.seen == []


async def test_bad_threshold_is_a_usage_fault() -> None:
    import pytest

    from smartpipe.core.errors import UsageFault

    with pytest.raises(UsageFault, match="between 0 and 1"):
        await _run("x\n", threshold=1.5)


# --- native media embeddings (D39/04) ----------------------------------------------


async def test_image_only_items_route_natively_no_captions() -> None:
    import base64
    import contextlib
    import io as io_module
    import json

    from smartpipe.core.errors import ExitCode
    from smartpipe.models.base import ImageData
    from smartpipe.models.base import ImageData as ImagePart

    class MediaEmbedder:
        """jina-shaped: embed_parts marks it media-capable."""

        def __init__(self) -> None:
            self.ref = ModelRef("jina", "jina-clip-v2")
            self.part_calls: list[str | ImagePart] = []

        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("image-only items must not reach the text path")

        async def embed_parts(
            self, parts: Sequence[str | ImagePart]
        ) -> tuple[tuple[float, ...], ...]:
            self.part_calls.extend(parts)
            return tuple((1.0, 0.0) for _part in parts)

    class MediaContext:
        def __init__(self) -> None:
            self.media_embedder = MediaEmbedder()

        async def chat_model(self, flag: str | None = None) -> ChatModel:
            raise RuntimeError("no chat configured")

        async def embedding_model(self, flag: str | None = None) -> MediaEmbedder:
            return self.media_embedder

        def concurrency(self, flag: int | None = None) -> int:
            return 2

        def document_parser(self, flag: str | None = None) -> None:
            return None

        def remote_transcriber(self, chat_ref: object | None = None) -> None:
            return None

    line = json.dumps(
        {
            "__media": {
                "kind": "image",
                "mime": "image/png",
                "data_b64": base64.b64encode(b"pixels").decode(),
            },
            "source": "a.png",
        }
    )
    context = MediaContext()
    out = io_module.StringIO()
    err = io_module.StringIO()
    with contextlib.redirect_stderr(err):
        code = await run_distinct(
            DistinctRequest(),
            context,
            stdin=io_module.StringIO(line + "\n"),
            stdout=out,
        )
    assert code is ExitCode.OK
    assert len(context.media_embedder.part_calls) == 1
    assert isinstance(context.media_embedder.part_calls[0], ImageData)  # bytes, not a caption
    assert "media embedded natively (jina/jina-clip-v2)" in err.getvalue()


# --- --exact: the hash rung only (wave 2, item 22) ---------------------------------


class _NeverEmbed:
    """A context whose embedding model must never be touched."""

    async def embedding_model(self, flag: str | None = None) -> object:
        raise AssertionError("--exact must not resolve an embedding model")

    async def chat_model(self, flag: str | None = None) -> object:
        raise AssertionError("--exact must not resolve a chat model")

    def document_parser(self, flag: str | None = None) -> None:
        return None

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None

    def concurrency(self, flag: int | None = None) -> int:
        return 2


async def test_exact_folds_identical_records_with_zero_model_calls(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [
        '{"b": 1, "a": 2}',
        '{"a": 2, "b": 1}',  # same record, different key order — canonicalized
        '{"a": 3}',
    ]
    out = io.StringIO()
    code = await run_distinct(
        DistinctRequest(exact=True),
        _NeverEmbed(),  # type: ignore[arg-type]
        stdin=io.StringIO("\n".join(rows) + "\n"),
        stdout=out,
    )
    assert code is ExitCode.OK
    assert out.getvalue() == '{"b": 1, "a": 2}\n{"a": 3}\n'  # first bytes preserved
    assert "kept 2 of 3 (1 exact + 0 near duplicates folded)" in capsys.readouterr().err


async def test_exact_compares_plain_text_byte_for_byte() -> None:
    out = io.StringIO()
    await run_distinct(
        DistinctRequest(exact=True),
        _NeverEmbed(),  # type: ignore[arg-type]
        stdin=io.StringIO("hello\nhello \nhello\n"),  # trailing space differs — no fuzz
        stdout=out,
    )
    assert out.getvalue() == "hello\nhello \n"


async def test_exact_folds_identical_media_bytes() -> None:
    import base64
    import json as _json

    payload = base64.b64encode(b"same-bytes").decode()

    def media_row(path: str) -> str:
        return _json.dumps(
            {
                "__media": {"kind": "image", "mime": "image/png", "data_b64": payload},
                "__source": {"path": path, "as": "file"},
            }
        )

    out = io.StringIO()
    await run_distinct(
        DistinctRequest(exact=True),
        _NeverEmbed(),  # type: ignore[arg-type]
        stdin=io.StringIO(media_row("a.png") + "\n" + media_row("b.png") + "\n"),
        stdout=out,
    )
    assert len(out.getvalue().splitlines()) == 1  # identical files folded, free


# --- the ocr-model role (item 48) + the converter's OCR rung (item 49d) ---------------


async def test_ocr_role_parses_pattern_scans_at_ingestion(tmp_path: object) -> None:
    import contextlib
    import pathlib

    from smartpipe.io.inputs import InputSpec
    from tests.io.test_ocr_ingest import FakeParser

    base = pathlib.Path(str(tmp_path))
    parser = FakeParser(image_text="dark mode please")

    class OcrContext(FakeContext):
        def document_parser(self, flag: str | None = None) -> FakeParser:  # type: ignore[override]
            return parser

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    (base / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    out = io.StringIO()
    err = io.StringIO()
    context = OcrContext()
    with contextlib.redirect_stderr(err):
        code = await run_distinct(
            DistinctRequest(input=InputSpec(patterns=(str(base / "scan.png"),), from_files=False)),
            context,
            stdin=_Tty(),
            stdout=out,
        )
    assert code is ExitCode.OK
    assert len(parser.image_calls) == 1
    assert out.getvalue() == "dark mode please\n"  # the parsed markdown IS the item
    assert "parsed by mistral/mistral-ocr-latest" in err.getvalue()


async def test_media_records_convert_through_the_converters_ocr_rung(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 49(d): a record carrying __media pixels (e.g. from split --media)
    converts through the configured parser — no chat model needed."""
    import base64
    import contextlib

    from tests.io.test_ocr_ingest import FakeParser

    parser = FakeParser(image_text="dark mode please")
    monkeypatch.setitem(VECTORS, "[figure 1] dark mode please", (0.0, 1.0))

    class OcrContext(FakeContext):
        def document_parser(self, flag: str | None = None) -> FakeParser:  # type: ignore[override]
            return parser

    row = json.dumps(
        {
            "__media": {
                "kind": "image",
                "mime": "image/png",
                "data_b64": base64.b64encode(b"pixels").decode(),
            }
        }
    )
    out = io.StringIO()
    err = io.StringIO()
    context = OcrContext()
    with contextlib.redirect_stderr(err):
        code = await run_distinct(
            DistinctRequest(),
            context,
            stdin=io.StringIO(row + "\n"),
            stdout=out,
        )
    assert code is ExitCode.OK
    assert len(parser.image_calls) == 1  # the OCR rung converted; no chat exists here
    assert context.embedder.seen == ["[figure 1] dark mode please"]
    assert "image → text (parsed by mistral/mistral-ocr-latest)" in err.getvalue()
