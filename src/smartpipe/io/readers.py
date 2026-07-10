"""Item sources — all with the same shape: ``AsyncIterator[Item]``.

Stdin is read **incrementally** (stage-08 as amended): a daemon pump thread does the
blocking ``readline`` and hands lines to a bounded asyncio queue, so items flow as
they arrive (``tail -f`` works), backpressure is real (the pump stalls when the queue
fills), and shutdown can never hang on a blocked read (the async side is cancellable;
the daemon flag is the last-resort guarantee). ``--in`` file lists stay finite.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from smartpipe.core.errors import ItemError, SetupFault, UsageFault
from smartpipe.io import diagnostics
from smartpipe.io.csvrows import CsvCutter, csv_file_items
from smartpipe.io.items import Item, ItemSource, item_from_file, item_from_line
from smartpipe.models.base import AudioData, ImageData, VideoData
from smartpipe.parsing.detect import FileKind, detect_kind, route
from smartpipe.parsing.extract import MissingExtra, extract

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from typing import Literal, TextIO

    from smartpipe.io.inputs import InputSpec
    from smartpipe.models.ocr import DocumentParser

__all__ = [
    "OcrIngest",
    "ensure_not_a_tty",
    "figure_note",
    "file_items",
    "from_files_items",
    "ocr_eligible_count",
    "ocr_route",
    "resolve_items",
    "stdin_items",
]

_HEAD_BYTES = 8192
_QUEUE_MAX = 1024  # lines buffered ahead of consumption — memory stays bounded


@dataclass(frozen=True, slots=True)
class OcrIngest:
    """The ``ocr-model`` role at ingestion (item 40): the parser plus the
    verb's degradation log, so every parsed page is disclosed per row."""

    parser: DocumentParser
    log: diagnostics.DegradationLog


def ocr_route(kind: FileKind, as_mode: str | None) -> Literal["image", "pdf"] | None:
    """Whether a configured ``ocr-model`` parses this file: whole-crate PDFs
    and images only — the lines/jsonl/csv dials read text rows, and every
    other kind keeps today's extraction ladder. Pure: the routing decision."""
    if as_mode in ("lines", "jsonl", "csv"):
        return None
    if kind is FileKind.PDF:
        return "pdf"
    if kind is FileKind.IMAGE:
        return "image"
    return None


def resolve_items(
    spec: InputSpec,
    stdin: TextIO,
    *,
    stop: asyncio.Event | None = None,
    ocr: OcrIngest | None = None,
) -> tuple[AsyncIterator[Item], int | None]:
    """The single entry point every verb uses: dispatch on the input flags.

    Returns ``(items, total)`` — total is known only for ``--in`` file lists;
    stdin is a stream (``tail -f`` works), so its total is ``None`` and the
    spinner shows count+rate instead of an ETA. Only the stdin paths guard
    against a bare terminal. ``spec.as_mode`` is the granularity dial (items
    15/54): file = whole crates, lines = text rows, jsonl = strict records,
    csv = header-named rows; None = auto (extension defaults for paths, the
    per-line sniff on stdin). ``ocr`` (item 40): a configured ocr-model parses
    PDF/image crates — page counts are unknown before parsing, so those runs
    report ``total=None``; csv runs stream row-at-a-time and do the same.
    Unset, every path below is byte-identical to before the role existed."""
    from smartpipe.io.inputs import expand_globs

    if spec.patterns and spec.from_files:
        raise UsageFault(
            "--in and --from-files are both file sources — use one\n"
            "  --in takes globs; --from-files reads filenames from stdin."
        )
    if spec.patterns:
        paths = expand_globs(spec.patterns)  # UsageFault if no match
        if spec.as_mode in ("lines", "jsonl", "csv"):
            _refuse_uncuttable(paths, spec.as_mode)  # every matched file must honor it
        if ocr is not None and _any_ocr_eligible(paths, spec.as_mode):
            chained = None if stdin.isatty() else stdin
            return _ocr_path_items(paths, spec.as_mode, ocr, chained, stop), None
        if _any_csv(paths, spec.as_mode):
            # item 54: a csv in the mix streams row-at-a-time — no slurp, no total
            chained = None if stdin.isatty() else stdin
            return _stream_path_items(paths, spec, chained, stop, ocr), None
        loaded = _path_items(paths, spec.as_mode)
        if stdin.isatty():  # files only — no pipe to chain
            return _iter_list(loaded), len(loaded)
        # spec §8: mixed input is files first (glob-sorted), then stdin lines
        return _chain_files_then_stdin(loaded, stdin, stop, spec.as_mode, ocr), None
    ensure_not_a_tty(stdin)
    if spec.from_files:
        return from_files_items(stdin, stop=stop, as_mode=spec.as_mode, ocr=ocr), None
    if spec.as_mode == "file":
        return _stdin_as_one_item(stdin, stop), None  # slurp: the whole pipe is one crate
    return stdin_items(
        stdin, stop=stop, as_mode=spec.as_mode, strict_rows=spec.strict_rows, ocr=ocr
    ), None


def _any_csv(paths: Sequence[Path], as_mode: str | None) -> bool:
    """Whether this run cuts csv rows: an explicit ``--as csv``, or (in auto
    mode) any matched ``.csv``/``.tsv`` path — those default to the csv cut."""
    if as_mode == "csv":
        return True
    return as_mode is None and any(path.suffix.lower() in _CSV_SUFFIXES for path in paths)


async def _stream_path_items(
    paths: Sequence[Path],
    spec: InputSpec,
    stdin: TextIO | None,
    stop: asyncio.Event | None,
    ocr: OcrIngest | None,
) -> AsyncIterator[Item]:
    """Path ingestion with a csv in the mix (item 54): csv files stream
    row-at-a-time (a 10 GB export must not materialize); every other file
    loads exactly as ``_path_items`` would."""
    warned_extras: set[str] = set()
    ordinal = 0
    for path in paths:
        if stop is not None and stop.is_set():
            return
        mode = spec.as_mode or _default_mode(path)
        if mode == "csv":
            for item in csv_file_items(path):
                yield item
        elif mode != "file":
            for row in _text_rows(path, mode):
                yield row
        else:
            item = _load_file(path, ordinal, warned_extras)
            if item is not None:
                yield item
                ordinal += 1
    if stdin is not None:
        _note_stdin_transition()
        async for item in stdin_items(
            stdin,
            stop=stop,
            as_mode=spec.as_mode,
            strict_rows=spec.strict_rows,
            ocr=ocr,
            csv_empty_ok=True,  # files already flowed — an idle chained pipe is ordinary
        ):
            yield item


def _any_ocr_eligible(paths: Sequence[Path], as_mode: str | None) -> bool:
    """Only an actually-parseable file flips ingestion onto the OCR path —
    text-only corpora keep their known total (and embed's batched calls)."""
    return any(_is_ocr_eligible(path, as_mode) for path in paths)


def ocr_eligible_count(paths: Sequence[Path], as_mode: str | None) -> int:
    """How many named files a configured ocr-model would parse — the reader's
    preflight arithmetic (item 48). Unreadable files count as not parseable;
    they get their own skip warning at load time."""
    return sum(1 for path in paths if _is_ocr_eligible(path, as_mode))


def _is_ocr_eligible(path: Path, as_mode: str | None) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(_HEAD_BYTES)
    except OSError:
        return False
    return ocr_route(detect_kind(path, head), as_mode) is not None


async def _ocr_file(path: Path, ordinal: int, ocr: OcrIngest) -> list[Item] | None:
    """The named file through the configured parser — ``None`` means "not
    OCR's case, or the parse failed (disclosed)": the caller falls back to
    today's extraction ladder, never a hard stop."""
    try:
        with path.open("rb") as handle:
            head = handle.read(_HEAD_BYTES)
    except OSError:
        return None  # _load_file owns the cannot-read warning
    kind = detect_kind(path, head)
    route_to = ocr_route(kind, None)
    if route_to is None:
        return None
    name = path.name
    detail = f"parsed by {ocr.parser.ref}"
    try:
        if route_to == "image":
            from smartpipe.parsing.detect import image_mime

            markdown = await ocr.parser.parse_image(
                ImageData(data=path.read_bytes(), mime=image_mime(path))
            )
            ocr.log.note(name, "document → markdown", detail)
            return [item_from_file(markdown, str(path), ordinal)]
        pages = await ocr.parser.parse_pdf(path)
        items: list[Item] = []
        for page in pages:
            marker = name if len(pages) == 1 else f"{name} p.{page.index + 1}"
            ocr.log.note(marker, "document → markdown", detail)
            items.append(
                Item(
                    raw=page.markdown,
                    text=page.markdown,
                    data=None,
                    source=ItemSource(
                        kind="file",
                        name=marker,
                        index=page.index,
                        cut="pages",
                        path=str(path),
                        label=marker,
                    ),
                )
            )
        return items
    except ItemError as exc:
        diagnostics.warn(f"ocr failed: {name} ({exc}) — falling back to local extraction")
        return None


async def _ocr_path_items(
    paths: Sequence[Path],
    as_mode: str | None,
    ocr: OcrIngest,
    stdin: TextIO | None,
    stop: asyncio.Event | None,
) -> AsyncIterator[Item]:
    """Path ingestion with the ocr-model set: PDF/image crates parse through
    the role (one item per page); everything else loads exactly as before."""
    warned_extras: set[str] = set()
    ordinal = 0
    for path in paths:
        if stop is not None and stop.is_set():
            return
        mode = as_mode or _default_mode(path)
        if mode == "csv":
            for row in csv_file_items(path):
                yield row
            continue
        if mode != "file":
            for row in _text_rows(path, mode):
                yield row
            continue
        parsed = await _ocr_file(path, ordinal, ocr)
        if parsed is None:
            item = _load_file(path, ordinal, warned_extras)
            if item is not None:
                yield item
                ordinal += 1
            continue
        for item in parsed:
            yield item
        ordinal += 1
    if stdin is not None:
        _note_stdin_transition()
        async for item in stdin_items(stdin, stop=stop, as_mode=as_mode, ocr=ocr):
            yield item


def _note_stdin_transition() -> None:
    """Item 69: positional files are done and the chain now WAITS on the piped
    stdin (spec §8). That wait looks exactly like a hang — one pinned note
    (wording is contract) names it and both ways out."""
    diagnostics.note(
        "files done - now reading stdin (pipe data or close it; files-only: add < /dev/null)"
    )


async def _chain_files_then_stdin(
    loaded: Sequence[Item],
    stdin: TextIO,
    stop: asyncio.Event | None,
    as_mode: str | None,
    ocr: OcrIngest | None = None,
) -> AsyncIterator[Item]:
    for item in loaded:
        yield item
    _note_stdin_transition()
    async for item in stdin_items(stdin, stop=stop, as_mode=as_mode, ocr=ocr):
        yield item


async def _iter_list(items: Sequence[Item]) -> AsyncIterator[Item]:
    for item in items:
        yield item


@dataclass(frozen=True, slots=True)
class _StdinDocument:
    tmp_name: str
    kind: FileKind


# A queue message: ("line", text) · ("document", _StdinDocument) ·
# ("image", ImageData) · ("fatal", screen) · None = EOF.
_Message = tuple[str, object] | None

_KIND_SUFFIX: dict[FileKind, str] = {
    FileKind.PDF: ".pdf",
    FileKind.DOCX: ".docx",
    FileKind.XLSX: ".xlsx",
    FileKind.PPTX: ".pptx",
    FileKind.HTML: ".html",
    FileKind.EPUB: ".epub",
    FileKind.AUDIO: ".mp3",
}


async def _messages(stdin: TextIO, stop: asyncio.Event | None) -> AsyncIterator[tuple[str, object]]:
    """Incremental stdin source: daemon pump thread → bounded queue → cancellable get.

    Real streams are read with ``os.read`` on the raw fd, NOT ``stdin.readline()``:
    a thread blocked in ``readline`` holds the TextIOWrapper's lock, and CPython's
    interpreter-shutdown finalization then deadlocks trying to close the stream —
    the exact hang the streaming e2e caught. On the fd path the FIRST read also
    sniffs (stage-07 task 4): a binary document redirected to stdin becomes one
    spooled document message; text proceeds as lines with the sniffed bytes as the
    carry. Objects without a usable fd (StringIO in tests) fall back to
    ``readline`` — text-only by construction, never blocking, never sniffed.
    """
    queue: asyncio.Queue[_Message] = asyncio.Queue(_QUEUE_MAX)
    loop = asyncio.get_running_loop()

    def put(message: _Message) -> None:
        asyncio.run_coroutine_threadsafe(queue.put(message), loop).result()

    def pump_text_fd(fd: int, carry: bytes) -> None:
        buffer = bytearray(carry)
        while True:
            while (newline := buffer.find(0x0A)) != -1:
                line = bytes(buffer[: newline + 1])
                del buffer[: newline + 1]
                put(("line", line.decode("utf-8", errors="replace")))
            chunk = os.read(fd, 65536)  # blocks WITHOUT holding any io lock
            if not chunk:
                if buffer:  # final line without a trailing newline
                    put(("line", bytes(buffer).decode("utf-8", errors="replace")))
                return
            buffer += chunk

    def spool_document(fd: int, head: bytes, kind: FileKind) -> None:
        import tempfile

        suffix = _KIND_SUFFIX.get(kind, "")
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(head)
            while chunk := os.read(fd, 65536):
                handle.write(chunk)
        put(("document", _StdinDocument(handle.name, kind)))

    def collect_image(fd: int, head: bytes) -> None:
        data = bytearray(head)
        while chunk := os.read(fd, 65536):
            data += chunk
        mime = _magic_image_mime(bytes(data[:16]))
        put(("image", ImageData(data=bytes(data), mime=mime)))

    def collect_audio(fd: int, head: bytes, kind: FileKind) -> None:
        data = bytearray(head)
        while chunk := os.read(fd, 65536):
            data += chunk
        from smartpipe.parsing.detect import audio_mime

        suffix = _KIND_SUFFIX.get(kind, ".mp3")
        put(("audio", AudioData(data=bytes(data), mime=audio_mime(Path(f"x{suffix}")))))

    def collect_video(fd: int, head: bytes) -> None:
        data = bytearray(head)
        while chunk := os.read(fd, 65536):
            data += chunk
        put(("video", VideoData(data=bytes(data), mime="video/mp4")))

    def pump_fd(fd: int) -> None:
        head = os.read(fd, _HEAD_BYTES)  # one read — a live stream must not stall here
        if not head:
            return  # empty stdin
        kind = detect_kind(Path("<stdin>"), head)
        match route(kind):
            case "text":
                pump_text_fd(fd, head)
            case "doc":
                spool_document(fd, head, kind)
            case "audio":
                collect_audio(fd, head, kind)
            case "video":
                collect_video(fd, head)
            case "image":
                collect_image(fd, head)
            case "skip":
                from smartpipe.cli import screens

                put(("fatal", screens.BINARY_STDIN_UNPARSEABLE))

    def pump_readline() -> None:
        while True:
            line = stdin.readline()  # non-blocking sources only (StringIO et al.)
            if not line:
                return
            put(("line", line))

    def pump() -> None:
        try:
            fd: int | None
            try:
                fd = stdin.fileno()
            except (OSError, ValueError, AttributeError):
                fd = None
            if fd is None:
                pump_readline()
            else:
                pump_fd(fd)
        except (RuntimeError, ValueError, OSError, concurrent.futures.CancelledError):
            # loop closed, consumer gone, or the stream was closed under us —
            # any of these means "stop pumping", never a crash or a stderr trace
            return
        finally:
            sentinel = queue.put(None)  # blocking put — a full queue can't swallow EOF
            try:
                asyncio.run_coroutine_threadsafe(sentinel, loop).result()
            except (RuntimeError, concurrent.futures.CancelledError):
                sentinel.close()  # loop gone — don't leave a never-awaited coroutine

    threading.Thread(target=pump, name="smartpipe-stdin-pump", daemon=True).start()
    while True:
        message = await _next_or_stop(queue, stop)
        if message is None:
            return
        yield message


def _magic_image_mime(head: bytes) -> str:
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return "image/webp" if head[8:12] == b"WEBP" else "image/png"


async def _next_or_stop(queue: asyncio.Queue[_Message], stop: asyncio.Event | None) -> _Message:
    if stop is None:
        return await queue.get()
    if stop.is_set():
        return None
    get_task = asyncio.ensure_future(queue.get())
    stop_task = asyncio.ensure_future(stop.wait())
    done, _pending = await asyncio.wait({get_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    if get_task in done:
        stop_task.cancel()
        return get_task.result()
    get_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await get_task  # reap it — no strays, no destroyed-pending warnings
    return None


async def _lines(stdin: TextIO, stop: asyncio.Event | None) -> AsyncIterator[str]:
    """Text-only view of the message stream (``--from-files`` wants filenames)."""
    async for message in _messages(stdin, stop):
        tag, payload = message
        if tag != "line":
            raise SetupFault(
                "error: --from-files expects filenames on stdin, got a binary document\n"
                "  Pipe a list of paths in, e.g.:\n"
                "  find . -name '*.md' | smartpipe map … --from-files"
            )
        assert isinstance(payload, str)
        yield payload


async def stdin_items(
    stdin: TextIO,
    *,
    stop: asyncio.Event | None = None,
    as_mode: str | None = None,
    strict_rows: bool = False,
    ocr: OcrIngest | None = None,
    csv_empty_ok: bool = False,
) -> AsyncIterator[Item]:
    """Each stdin line is one Item, yielded as it arrives (never waits for EOF).

    A redirected binary document (``smartpipe map … < report.pdf``) is ONE item:
    spooled, extracted, source ``<stdin>`` (stage-07 task 4). A final line without
    a trailing newline is still an item; empty input yields nothing; CRLF and the
    line-0 BOM are handled per-item by ``item_from_line``. ``as_mode`` (item 15):
    ``lines`` reads every line as TEXT (no JSON sniff); ``jsonl`` demands one
    record per line, loudly; ``csv`` (item 54) reads the first line as the header
    and every later line as one record; all three refuse a binary stdin — a
    document or clip has no rows to cut. ``ocr`` (item 40): a redirected PDF or
    image parses through the configured ocr-model, falling back on failure.
    """

    index = 0
    records = 0
    plain = 0
    strict = strict_rows or bool(os.environ.get("SMARTPIPE_STRICT_ROWS", "").strip())
    cutter = (
        CsvCutter(origin=None, delimiter=",", empty_ok=csv_empty_ok) if as_mode == "csv" else None
    )
    async for message in _messages(stdin, stop):
        tag, payload = message
        if as_mode is not None and tag in ("document", "image", "audio", "video"):
            raise UsageFault(_uncuttable_stdin(tag, as_mode))
        if tag == "line":
            assert isinstance(payload, str)
            if cutter is not None:
                for item in cutter.push(payload):
                    yield item
                continue  # the census is a sniffing concern; csv is declared
            item = _row_item(payload, index, as_mode, origin=None)
            if item.data is None:
                plain += 1
            else:
                records += 1
            if strict and records and plain:
                # item 19: fail at the FIRST mixed row, naming it — before the
                # verb sees it, before anything downstream could spend on it
                raise UsageFault(_mixed_row_screen(index, is_record=item.data is not None))
            yield item
            index += 1
        elif tag == "document":
            assert isinstance(payload, _StdinDocument)
            if ocr is not None and payload.kind is FileKind.PDF:
                pages = await _ocr_stdin_document(payload, ocr)
                if pages is not None:
                    for item in pages:
                        yield item
                    continue
            yield await asyncio.to_thread(_extract_stdin_document, payload.tmp_name, payload.kind)
        elif tag == "image":
            assert isinstance(payload, ImageData)
            if ocr is not None:
                parsed = await _ocr_stdin_image(payload, ocr)
                if parsed is not None:
                    yield parsed
                    continue
            item = item_from_file("", "<stdin>", 0)
            yield replace(item, media=(payload,))
        elif tag == "audio":
            assert isinstance(payload, AudioData)
            item = item_from_file("", "<stdin>", 0)
            yield replace(item, media=(payload,))
        elif tag == "video":
            assert isinstance(payload, VideoData)
            item = item_from_file("", "<stdin>", 0)
            yield replace(item, media=(payload,))
        else:  # "fatal"
            assert isinstance(payload, str)
            raise SetupFault(payload)
    if cutter is not None:
        for item in cutter.finish():  # EOF flush; a header-less stream refuses here
            yield item
    _report_census(records, plain)


def _report_census(records: int, plain: int) -> None:
    """The kind census (item 20): a MIXED stream gets one stderr note. Under
    --strict-rows (or SMARTPIPE_STRICT_ROWS, or a .sem run's default — item 19)
    the stream never reaches EOF mixed: the first mixed row raised already."""
    if not records or not plain:
        return
    diagnostics.note(f"input: {records:,} records · {plain:,} plain lines")


def _mixed_row_screen(index: int, *, is_record: bool) -> str:
    """The strict-rows error (items 19/20): name the first row that broke the
    stream's kind, then the fix."""
    kind = "a record" if is_record else "a plain text line"
    stream = "a plain-text stream" if is_record else "a record stream"
    return (
        f"input: line {index + 1} is {kind} in {stream}\n"
        "  --strict-rows demands one kind - declare it: --as jsonl (records) "
        "or --as lines (text)."
    )


async def _ocr_stdin_document(payload: _StdinDocument, ocr: OcrIngest) -> list[Item] | None:
    """A redirected PDF through the ocr-model — ``None`` (spool intact) means
    the parse failed, disclosed, and the local ladder takes over."""
    path = Path(payload.tmp_name)
    try:
        pages = await ocr.parser.parse_pdf(path)
    except ItemError as exc:
        diagnostics.warn(f"ocr failed: <stdin> ({exc}) — falling back to local extraction")
        return None
    with contextlib.suppress(OSError):
        path.unlink()  # statelessness: the spool never outlives the run
    detail = f"parsed by {ocr.parser.ref}"
    items: list[Item] = []
    for page in pages:
        marker = "<stdin>" if len(pages) == 1 else f"<stdin> p.{page.index + 1}"
        ocr.log.note(marker, "document → markdown", detail)
        items.append(
            Item(
                raw=page.markdown,
                text=page.markdown,
                data=None,
                source=ItemSource(
                    kind="file",
                    name=marker,
                    index=page.index,
                    cut="pages",
                    path="<stdin>",
                    label=marker,
                ),
            )
        )
    return items


async def _ocr_stdin_image(payload: ImageData, ocr: OcrIngest) -> Item | None:
    try:
        markdown = await ocr.parser.parse_image(payload)
    except ItemError as exc:
        diagnostics.warn(f"ocr failed: <stdin> ({exc}) — falling back to local extraction")
        return None
    ocr.log.note("<stdin>", "document → markdown", f"parsed by {ocr.parser.ref}")
    return item_from_file(markdown, "<stdin>", 0)


def _extract_stdin_document(tmp_name: str, kind: FileKind) -> Item:
    from smartpipe.cli import screens

    path = Path(tmp_name)
    try:
        extracted = extract(path, kind)
    except MissingExtra as exc:
        raise SetupFault(screens.stdin_document_failed(exc.guidance.splitlines()[0])) from exc
    except ItemError as exc:
        raise SetupFault(screens.stdin_document_failed(str(exc))) from exc
    finally:
        with contextlib.suppress(OSError):
            path.unlink()  # statelessness: the spool never outlives the run
    if extracted.warning is not None:
        diagnostics.warn(f"<stdin>: {extracted.warning}")
    return item_from_file(extracted.text, "<stdin>", 0)


def _row_item(line: str, index: int, as_mode: str | None, *, origin: str | None) -> Item:
    """One text row under the --as dial: ``lines`` keeps it TEXT even if it
    looks like JSON; ``jsonl`` demands a record, loudly naming the line; the
    default (None) is the ordinary per-line sniff."""
    if as_mode == "lines":
        raw = line.removesuffix("\n").removesuffix("\r")
        source = ItemSource(
            kind="file" if origin is not None else "stdin",
            name=origin or "-",
            index=index,
            cut="lines",
            path=origin,
        )
        return Item(raw=raw, text=raw, data=None, source=source)
    item = item_from_line(line, index)
    if as_mode == "jsonl" and item.data is None:
        where = f"{origin or 'stdin'} line {index + 1}"
        raise UsageFault(
            f"--as jsonl: {where} isn't a JSON object\n"
            "  jsonl means one {…} record per line; --as lines reads lines as plain text."
        )
    if origin is not None and item.source.label is None and item.source.path is None:
        # a fresh cut from a named file: stamp the file as its origin path
        item = replace(item, source=replace(item.source, kind="file", name=origin, path=origin))
    return item


def _cut_unit(as_mode: str) -> str:
    """What the dial cuts into — csv cuts rows, lines/jsonl cut lines."""
    return "rows" if as_mode == "csv" else "lines"


def _uncuttable_stdin(tag: str, as_mode: str) -> str:
    match tag:
        case "image":
            reason = "images have no finer granularity"
        case "audio" | "video":
            reason = "finer granularity is split --by minutes/seconds"
        case _:  # document
            reason = "pages are the honest unit: smartpipe split --by pages (or --by tokens)"
    kind = "document" if tag == "document" else tag
    return f"--as {as_mode}: stdin is a {kind}, not {_cut_unit(as_mode)}\n  {reason}"


def _refuse_uncuttable(paths: Sequence[Path], as_mode: str) -> None:
    """An EXPLICIT --as lines/jsonl/csv must be satisfiable by EVERY matched
    file — loud refusal with offender counts, never silent partial application."""
    images: list[str] = []
    clips: list[str] = []
    documents: list[str] = []
    for path in paths:
        try:
            with path.open("rb") as handle:
                head = handle.read(_HEAD_BYTES)
        except OSError:
            continue  # unreadable files get their own skip warning later
        match route(detect_kind(path, head)):
            case "image":
                images.append(path.name)
            case "audio" | "video":
                clips.append(path.name)
            case "doc":
                documents.append(path.name)
            case _:
                pass
    offenders = len(images) + len(clips) + len(documents)
    if offenders == 0:
        return
    plural = "s" if offenders != 1 else ""
    lines = [
        f"--as {as_mode}: {offenders} matched file{plural} can't be cut into {_cut_unit(as_mode)}"
    ]
    if images:
        lines.append(f"  images ({_examples(images)}) have no finer granularity")
    if clips:
        lines.append(
            f"  audio/video ({_examples(clips)}) — finer granularity is split --by minutes/seconds"
        )
    if documents:
        lines.append(
            f"  documents ({_examples(documents)}) — pages are the honest unit: "
            "split --by pages (or --by tokens)"
        )
    raise UsageFault("\n".join(lines))


def _examples(names: Sequence[str]) -> str:
    more = len(names) - 1
    return names[0] if more == 0 else f"{names[0]} +{more} more"


_JSONL_SUFFIXES = (".jsonl", ".ndjson")
_CSV_SUFFIXES = (".csv", ".tsv")


def _default_mode(path: Path) -> str:
    """AUTO's per-file extension default: .jsonl/.ndjson cut into records,
    .csv/.tsv cut into csv rows (item 54), everything else is one crate."""
    suffix = path.suffix.lower()
    if suffix in _JSONL_SUFFIXES:
        return "jsonl"
    if suffix in _CSV_SUFFIXES:
        return "csv"
    return "file"


def _path_items(paths: Sequence[Path], as_mode: str | None) -> list[Item]:
    """Named paths under the dial: explicit --as applies to every file; AUTO
    gives each file its extension default. csv never lands here — the caller
    routes any csv-bearing run through the streaming path instead."""
    items: list[Item] = []
    warned_extras: set[str] = set()
    ordinal = 0
    for path in paths:
        mode = as_mode or _default_mode(path)
        if mode == "file":
            item = _load_file(path, ordinal, warned_extras)
            if item is not None:
                items.append(item)
                ordinal += 1
            continue
        items.extend(_text_rows(path, mode))
    return items


def _text_rows(path: Path, mode: str) -> list[Item]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        diagnostics.warn(f"skipped: {path} (cannot read: {exc.strerror or exc})")
        return []
    return [
        _row_item(line, line_index, mode, origin=str(path))
        for line_index, line in enumerate(text.splitlines())
    ]


async def _stdin_as_one_item(stdin: TextIO, stop: asyncio.Event | None) -> AsyncIterator[Item]:
    """--as file on stdin: slurp the whole pipe as ONE document item. A binary
    stdin already arrives as one item; text lines collect until EOF."""
    collected: list[str] = []
    async for message in _messages(stdin, stop):
        tag, payload = message
        if tag == "line":
            assert isinstance(payload, str)
            collected.append(payload)
        elif tag == "document":
            assert isinstance(payload, _StdinDocument)
            yield await asyncio.to_thread(_extract_stdin_document, payload.tmp_name, payload.kind)
        elif tag in ("image", "audio", "video"):
            assert isinstance(payload, AudioData | ImageData | VideoData)
            yield replace(item_from_file("", "<stdin>", 0), media=(payload,))
        else:  # fatal
            assert isinstance(payload, str)
            raise SetupFault(payload)
    if collected:
        yield item_from_file("".join(collected).removesuffix("\n"), "<stdin>", 0)


async def from_files_items(
    stdin: TextIO,
    *,
    stop: asyncio.Event | None = None,
    as_mode: str | None = None,
    ocr: OcrIngest | None = None,
) -> AsyncIterator[Item]:
    """``--from-files``: each non-blank stdin line names a file to read — also
    incremental, so ``find … | smartpipe … --from-files`` processes as names
    arrive. The --as dial applies per named file (streamed names can't be
    pre-validated as a set, so an uncuttable file refuses when it arrives)."""
    from pathlib import Path

    warned_extras: set[str] = set()
    index = 0
    async for line in _lines(stdin, stop):
        name = line.strip()
        if not name:
            continue
        path = Path(name)
        if as_mode in ("lines", "jsonl", "csv"):
            _refuse_uncuttable([path], as_mode)
        mode = as_mode or _default_mode(path)
        if mode == "csv":
            for row in csv_file_items(path):
                yield row
                index += 1
            continue
        if mode == "file":
            if ocr is not None:
                parsed = await _ocr_file(path, index, ocr)
                if parsed is not None:
                    for item in parsed:
                        yield item
                    index += 1
                    continue
            item = _load_file(path, index, warned_extras)
            if item is not None:
                yield item
                index += 1
            continue
        for row in _text_rows(path, mode):
            yield row
            index += 1


def file_items(paths: Sequence[Path]) -> list[Item]:
    """Each file is one item. Unreadable, unparseable, or missing-dependency files
    are skipped with a warning (spec §6.3) — the run never crashes on a bad file."""
    warned_extras: set[str] = set()
    items: list[Item] = []
    for index, path in enumerate(paths):
        item = _load_file(path, index, warned_extras)
        if item is not None:
            items.append(item)
    return items


def _load_file(path: Path, index: int, warned_extras: set[str]) -> Item | None:
    try:
        with path.open("rb") as handle:
            head = handle.read(_HEAD_BYTES)
    except OSError as exc:
        diagnostics.warn(f"skipped: {path} (cannot read: {exc.strerror or exc})")
        return None
    kind = detect_kind(path, head)
    if route(kind) in ("audio", "video"):
        # D20/D27: media carries its BYTES — conversion is lazy and per-verb
        # (map tries the native wire first; text verbs convert on demand)
        from smartpipe.parsing.detect import audio_mime, video_mime

        try:
            data = path.read_bytes()
        except OSError as exc:
            diagnostics.warn(f"skipped: {path} (cannot read: {exc.strerror or exc})")
            return None
        item = item_from_file("", str(path), index)
        media = (
            AudioData(data=data, mime=audio_mime(path))
            if route(kind) == "audio"
            else VideoData(data=data, mime=video_mime(path))
        )
        return replace(item, media=(media,))
    try:
        extracted = extract(path, kind)
    except MissingExtra as exc:
        if exc.extra not in warned_extras:
            diagnostics.warn(exc.guidance)
            warned_extras.add(exc.extra)
        return None
    except ItemError as exc:
        diagnostics.warn(f"skipped: {path} ({exc})")
        return None
    if extracted.warning is not None:
        diagnostics.warn(f"{path}: {extracted.warning}")
    item = item_from_file(extracted.text, str(path), index)
    if extracted.image is not None:
        return replace(item, media=(extracted.image,))  # map sends it to a vision model
    figures = _document_figures(path, kind, extracted.text)
    if figures:
        return replace(item, media=figures)
    return item


_FIGURE_CAP = 8  # request-size and cost sanity per document item (D32)
_FIGURE_KINDS = {FileKind.PDF, FileKind.DOCX, FileKind.PPTX, FileKind.XLSX}


_THIN_TEXT = 64  # under this many chars, a figure-bearing document reads as a scan


def _document_figures(path: Path, kind: FileKind, text: str) -> tuple[ImageData, ...]:
    """D32: a document item carries its embedded figures by default — capped,
    icon-floored, announced once per file. D39/03: when the text layer is
    THIN, the announcement says so — a scanned document routed to the vision
    path must never look like silent emptiness."""
    if kind not in _FIGURE_KINDS:
        return ()
    from smartpipe.parsing.extract import MissingExtra, embedded_images

    try:
        media = embedded_images(path)
    except (MissingExtra, ItemError):
        return ()  # text still flows; --media names scan problems loudly
    total = len(media.images)
    if total == 0:
        return ()
    kept = media.images[:_FIGURE_CAP]
    capped = total - len(kept)
    diagnostics.note(figure_note(path.name, len(text.strip()), len(kept), capped))
    return tuple(found.image for found in kept)


def figure_note(name: str, text_length: int, kept: int, capped: int) -> str:
    if text_length < _THIN_TEXT:
        hint = (
            f" ({capped} more capped — split --by pages --media processes every page)"
            if capped
            else ""
        )
        return (
            f"{name}: thin text layer ({text_length} chars) — scanned? "
            f"routed {kept} page image(s) to the vision path{hint}"
        )
    suffix = f" ({capped} more capped)" if capped else ""
    plural = "s" if kept != 1 else ""
    return f"{name}: {kept} figure{plural} attached{suffix}"


def ensure_not_a_tty(stdin: TextIO) -> None:
    """A kind guardrail: bare `smartpipe map ...` at a terminal would silently wait."""
    if stdin.isatty():
        raise UsageFault(
            "reading from a terminal — pipe some input in, e.g.:\n"
            '  cat notes.txt | smartpipe map "..."'
        )
