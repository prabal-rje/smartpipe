"""The ``split`` verb (D26 layer 3): oversized items → budget-sized chunk items.

Zero model calls. One 300-page PDF becomes N records of ``{"text",
"__source"}`` with machine-readable provenance (path, cut kind, position,
plus the human label ``report.pdf §3/12``), each small enough for whatever
verb comes next. The taught pipeline: ``smartpipe split --in big.pdf | smartpipe map … |
smartpipe reduce …``. Chunks concatenate back to the original text exactly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar

from smartpipe.core.errors import ExitCode, ItemError, UnsentError, UsageFault
from smartpipe.engine.chunking import split_text
from smartpipe.engine.units import SplitBy, parse_by
from smartpipe.io import diagnostics, readers, source_accounting
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source
from smartpipe.models.base import AudioData, ImageData, VideoData
from smartpipe.verbs.common import ensure_text, interrupted_exit_code, outcome_exit_code

if TYPE_CHECKING:
    from pathlib import Path as PathType
    from typing import TextIO

    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item
    from smartpipe.io.readers import OcrIngest
    from smartpipe.io.writers import OutputFormat, ResultWriter
    from smartpipe.models.ocr import DocumentParser

__all__ = ["SplitContext", "SplitRequest", "run_split"]

_M = TypeVar("_M", AudioData, VideoData)

_DEFAULT_BUDGET_TOKENS = 2_000  # comfortable for every wired window, ~8k chars


@dataclass(frozen=True, slots=True)
class SplitRequest:
    max_tokens_flag: int | None = None
    by_flag: str | None = None  # --by UNIT[:N] (D26 rich units)
    media: bool = False  # --media: embedded images become items (D29)
    input: InputSpec = STDIN
    ocr_model_flag: str | None = None  # --ocr-model: document parsing at ingestion (item 48)


class SplitContext(Protocol):
    """The slice of the container ``split`` needs: the writer, plus the
    ocr-model role (item 48) — the ONE way split ever calls a model."""

    def document_parser(self, flag: str | None = None) -> DocumentParser | None: ...
    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextIO,
        fields: tuple[str, ...] | None = None,
    ) -> ResultWriter: ...


def _resolve_by(request: SplitRequest) -> SplitBy:
    if request.by_flag is not None and request.max_tokens_flag is not None:
        raise UsageFault("--by and --max-tokens both set the unit — use one")
    if request.by_flag is not None:
        return parse_by(request.by_flag)
    if request.max_tokens_flag is not None:
        if request.max_tokens_flag < 1:
            raise UsageFault("--max-tokens must be at least 1")
        return SplitBy("tokens", request.max_tokens_flag)
    return SplitBy("tokens", _DEFAULT_BUDGET_TOKENS)


def _cut_source(item: Item, *, cut: str, position: int | None, label: str) -> dict[str, object]:
    """The chunk's ``__source`` spine record: how it was cut travels with it."""
    record: dict[str, object] = {"path": item.source.path or item.source.name, "as": cut}
    if position is not None:
        record["page" if cut == "pages" else "segment"] = position
    record["label"] = label
    return record


def _write_chunks(writer: ResultWriter, item: Item, by: SplitBy) -> None:
    origin = describe_source(item.source)  # "report.pdf" / "line 12"
    if by.unit in ("minutes", "seconds") and (video := _single(item, VideoData)) is not None:
        import base64

        from smartpipe.parsing.extract import slice_video

        step = by.slice_seconds
        slices = slice_video(video, seconds=step)
        total = len(slices)
        for position, part in enumerate(slices):
            marker = (
                origin
                if total == 1
                else f"{origin} §{_clock(position * step)}-{_clock((position + 1) * step)}"
            )
            writer.write_record(
                {
                    "__media": {
                        "kind": "video",
                        "mime": part.mime,
                        "data_b64": base64.b64encode(part.data).decode("ascii"),
                    },
                    "__source": _cut_source(item, cut=by.unit, position=position + 1, label=marker),
                }
            )
        return
    if by.unit in ("minutes", "seconds") and (audio := _single(item, AudioData)) is not None:
        import base64

        from smartpipe.parsing.extract import slice_audio

        step = by.slice_seconds
        slices = slice_audio(audio, seconds=step)
        total = len(slices)
        for position, part in enumerate(slices):
            marker = (
                origin
                if total == 1
                else f"{origin} §{_clock(position * step)}-{_clock((position + 1) * step)}"
            )
            # audio rides JSONL as base64 so the next verb can HEAR the slice
            writer.write_record(
                {
                    "__media": {
                        "kind": "audio",
                        "mime": part.mime,
                        "data_b64": base64.b64encode(part.data).decode("ascii"),
                    },
                    "__source": _cut_source(item, cut=by.unit, position=position + 1, label=marker),
                }
            )
        return
    chunks = split_text(item.text, by.amount if by.unit == "tokens" else _DEFAULT_BUDGET_TOKENS)
    total = len(chunks)
    for position, chunk in enumerate(chunks, start=1):
        marker = origin if total == 1 else f"{origin} §{position}/{total}"
        writer.write_record(
            {
                "text": chunk,
                "__source": _cut_source(item, cut="tokens", position=position, label=marker),
            }
        )
    figures = [part for part in item.media if isinstance(part, ImageData)]
    if figures:
        import base64

        for position, figure in enumerate(figures, start=1):
            writer.write_record(
                {
                    "__media": {
                        "kind": "image",
                        "mime": figure.mime,
                        "data_b64": base64.b64encode(figure.data).decode("ascii"),
                    },
                    "__source": _cut_source(
                        item, cut="file", position=None, label=f"{origin} img.{position}"
                    ),
                }
            )


def _single(item: Item, kind: type[_M]) -> _M | None:
    for part in item.media:
        if isinstance(part, kind):
            return part
    return None


def _clock(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _page_figures(path: PathType) -> dict[int, list[ImageData]]:
    """Figures grouped by 1-based page number (PDF only; the where marker is 'p.N img.M')."""
    from smartpipe.parsing.extract import embedded_images

    grouped: dict[int, list[ImageData]] = {}
    try:
        media = embedded_images(path)
    except ItemError:
        return {}
    for found in media.images:
        head = found.where.split(" ", 1)[0]  # "p.7"
        if head.startswith("p.") and head[2:].isdigit():
            grouped.setdefault(int(head[2:]), []).append(found.image)
    return grouped


async def _run_media(
    request: SplitRequest,
    context: SplitContext,
    *,
    stdout: TextIO,
    stop: asyncio.Event | None,
) -> ExitCode:
    """--media (D29): embedded images become items; icons under the floor drop, once-noted."""
    import base64
    from pathlib import Path

    from smartpipe.io.inputs import expand_globs
    from smartpipe.io.writers import OutputFormat
    from smartpipe.parsing.extract import embedded_images

    if not request.input.patterns:
        raise UsageFault(
            "--media reads document files — give it some: smartpipe split --media 'docs/*.pdf'"
        )
    if stop is not None and stop.is_set():
        return interrupted_exit_code(done=0, skipped=0, failed=0)
    writer = context.writer(OutputFormat.AUTO, structured=True, stdout=stdout)
    produced = 0
    skipped = 0
    failed = 0
    dropped_total = 0
    try:
        for path in expand_globs(request.input.patterns):
            if stop is not None and stop.is_set():
                break
            name = Path(path).name
            try:
                media = await asyncio.to_thread(embedded_images, Path(path))
            except ItemError as exc:
                diagnostics.warn(f"skipped: {name} ({exc})")
                skipped += 1
                failed += 1
                continue
            dropped_total += media.dropped_small
            if not media.images:
                diagnostics.note(f"{name} has no embedded images")
            for found in media.images:
                writer.write_record(
                    {
                        "__media": {
                            "kind": "image",
                            "mime": found.image.mime,
                            "data_b64": base64.b64encode(found.image.data).decode("ascii"),
                        },
                        "__source": {
                            "path": str(path),
                            "as": "file",
                            "label": f"{name} {found.where}",
                        },
                    }
                )
            produced += 1
    finally:
        writer.flush()
    if dropped_total:
        plural = "s" if dropped_total != 1 else ""
        diagnostics.note(
            f"skipped {dropped_total} embedded image{plural} under 4 KB (icons/decorations)"
        )
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=produced, skipped=skipped)
        return interrupted_exit_code(done=produced, skipped=skipped, failed=failed)
    return outcome_exit_code(done=produced, skipped=skipped, failed=failed)


async def _pdf_pages(path: PathType, ocr: OcrIngest | None) -> list[str]:
    """Page texts for --by pages: the configured ocr-model parses when set
    (item 48; each page disclosed, exactly like ingestion); a failed parse
    falls back to the local page extraction — never a hard stop."""
    from smartpipe.parsing.extract import pdf_page_texts

    if ocr is not None:
        parser = ocr.resolve_parser()
        if parser is None:
            return await asyncio.to_thread(pdf_page_texts, path)
        try:
            parsed = await ocr.parse_pdf(path)
        except ItemError as exc:
            note = readers.ocr_fallback_note(exc, where=path.name)
            if note is None:
                raise
            diagnostics.warn(note)
        else:
            detail = f"parsed by {parser.ref}"
            for page in parsed:
                marker = path.name if len(parsed) == 1 else f"{path.name} p.{page.index + 1}"
                ocr.log.note(marker, "document → markdown", detail)
            return [page.markdown for page in parsed]
    return await asyncio.to_thread(pdf_page_texts, path)


async def _run_pages(
    request: SplitRequest,
    context: SplitContext,
    *,
    by: SplitBy,
    stdout: TextIO,
    media: bool = False,
    ocr: OcrIngest | None = None,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    """--by pages reads PDF FILES directly (page structure dies in extraction);
    with --media, each page item carries that page's figures too (D32)."""
    import base64
    from pathlib import Path

    from smartpipe.io.inputs import expand_globs
    from smartpipe.io.writers import OutputFormat

    if not request.input.patterns:
        raise UsageFault(
            "--by pages reads PDF files — give it some:\n  smartpipe split --by pages 'docs/*.pdf'"
        )
    paths = expand_globs(request.input.patterns)
    if stop is not None and stop.is_set():
        return interrupted_exit_code(done=0, skipped=0, failed=0)
    if ocr is not None:
        readers.ocr_preflight(paths, None, ocr)
    writer = context.writer(OutputFormat.AUTO, structured=True, stdout=stdout)
    produced = 0
    skipped = 0
    failed = 0
    try:
        for path in paths:
            if stop is not None and stop.is_set():
                break
            name = Path(path).name
            if Path(path).suffix.lower() != ".pdf":
                diagnostics.warn(
                    f"skipped: {name} (--by pages reads PDF files — "
                    f"{name} has no fixed pages; use --by tokens)"
                )
                skipped += 1
                continue
            try:
                pages = await _pdf_pages(Path(path), ocr)
            except ItemError as exc:
                diagnostics.warn(f"skipped: {name} ({exc})")
                skipped += 1
                failed += 1
                continue
            figures_by_page = _page_figures(Path(path)) if media else {}
            groups = [pages[i : i + by.amount] for i in range(0, len(pages), by.amount)]
            for index, group in enumerate(groups):
                first = index * by.amount + 1
                last = min(first + by.amount - 1, len(pages))
                span = f"p.{first}" if first == last else f"p.{first}-{last}"
                marker = name if len(groups) == 1 else f"{name} {span}"
                record: dict[str, object] = {
                    "text": "\n\n".join(group).strip(),
                    "__source": {"path": str(path), "as": "pages", "page": first, "label": marker},
                }
                attached = [
                    {
                        "kind": "image",
                        "mime": figure.mime,
                        "data_b64": base64.b64encode(figure.data).decode("ascii"),
                    }
                    for page in range(first, last + 1)
                    for figure in figures_by_page.get(page, ())
                ]
                if attached:
                    record["__media"] = attached
                writer.write_record(record)
            produced += 1
    finally:
        writer.flush()
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=produced, skipped=skipped)
        return interrupted_exit_code(done=produced, skipped=skipped, failed=failed)
    return outcome_exit_code(done=produced, skipped=skipped, failed=failed)


async def run_split(
    request: SplitRequest,
    context: SplitContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    from smartpipe.io.writers import OutputFormat

    by = _resolve_by(request)
    if request.media and by.unit != "pages":
        if request.by_flag is not None or request.max_tokens_flag is not None:
            raise UsageFault(
                "--media combines with --by pages (fused page items) or stands alone — "
                "not with token/duration units"
            )
        # --media extracts embedded IMAGES — there is no text to parse, so the
        # ocr-model role never applies here (and never spends)
        return await _run_media(request, context, stdout=stdout, stop=stop)
    log = diagnostics.DegradationLog()  # per-row conversion disclosure (D27)
    ocr = readers.OcrIngest.lazy(lambda: context.document_parser(request.ocr_model_flag), log)
    if by.unit == "pages":
        code = await _run_pages(
            request,
            context,
            by=by,
            stdout=stdout,
            media=request.media,
            ocr=ocr,
            stop=stop,
        )
        log.finish()
        return code
    writer = context.writer(OutputFormat.AUTO, structured=True, stdout=stdout)
    items_iter, _total = readers.resolve_items(request.input, stdin, stop=stop, ocr=ocr)
    produced = 0
    sources = source_accounting.SourceCounter()
    try:
        async for item in items_iter:
            duration_slicing = by.unit in ("minutes", "seconds")
            has_clip = any(isinstance(part, AudioData | VideoData) for part in item.media)
            if not (duration_slicing and has_clip):
                try:
                    item = await ensure_text(item, log=log)  # converts, row-noted
                except ItemError as exc:
                    diagnostics.warn(f"skipped: {describe_source(item.source)} ({exc})")
                    sources.skip(item.source, failed=not isinstance(exc, UnsentError))
                    continue
            try:
                _write_chunks(writer, item, by)
            except ItemError as exc:
                diagnostics.warn(f"skipped: {describe_source(item.source)} ({exc})")
                sources.skip(item.source, failed=not isinstance(exc, UnsentError))
                continue
            produced += 1
            sources.done(item.source)
            if stop is not None and stop.is_set():
                break  # in-hand work drained; intake stops (Ctrl-C and the belt alike)
    finally:
        writer.flush()
        log.finish()
    counts = sources.counts
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=produced, skipped=counts.skipped)
        return interrupted_exit_code(
            done=counts.succeeded,
            skipped=counts.skipped,
            failed=counts.failed,
        )
    return outcome_exit_code(
        done=counts.succeeded,
        skipped=counts.skipped,
        failed=counts.failed,
    )
