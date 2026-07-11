"""Document parsing (item 40): the ``ocr-model`` role's wires.

Owner ruling: "if a document parsing model is available, just use that for
parsing files." A configured role routes ingested PDFs and images through it —
page markdown becomes the item text, every use disclosed per row. Two wires
behind one seam: the ``mistral`` provider rides the dedicated ``/v1/ocr``
endpoint (charges per page); any other ref goes through the normal chat-vision
path with an extract-the-text framing, so a local llava is a free OCR.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import httpx

from smartpipe.core.errors import ItemError, RetryableError, SetupFault, TransportError
from smartpipe.models.base import CompletionRequest, ImageData
from smartpipe.models.http_support import (
    decode_json_response,
    is_retryable_http,
    retry_after_seconds,
)
from smartpipe.models.retry import RetryPolicy, with_retries
from smartpipe.parsing.extract import EmbeddedMedia, embedded_images, pdf_page_texts

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from smartpipe.models.base import ChatModel, ModelRef

__all__ = [
    "OCR_SYSTEM",
    "DocumentParser",
    "MistralOcrParser",
    "OcrBilling",
    "OcrPage",
    "VisionOcrParser",
    "parser_billing",
    "pdf_page_count",
]

OCR_SYSTEM = (
    "You are a document parser. Extract ALL text from this page image "
    "verbatim, as Markdown. Preserve the reading order; render headings as "
    "Markdown headings and tables as Markdown tables. Reply with only the "
    "extracted Markdown — no commentary."
)

_VISION_MAX_TOKENS = 4096
_THIN_TEXT = 64  # under this, a page's text layer reads as a scan (D39/03)


@dataclass(frozen=True, slots=True)
class OcrPage:
    index: int  # 0-based, mirroring the wire
    markdown: str


class OcrBilling(Enum):
    """How a document parser consumes the shared outbound-call budget."""

    MODEL_CALL = "model-call"
    PAGE = "page"


class DocumentParser(Protocol):
    """The ``ocr-model`` seam: one page-image → markdown, one PDF → pages."""

    @property
    def ref(self) -> ModelRef: ...

    async def parse_image(self, image: ImageData) -> str: ...

    async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]: ...


@runtime_checkable
class _BillingMetadata(Protocol):
    @property
    def billing(self) -> OcrBilling: ...


@runtime_checkable
class _WrappedParser(Protocol):
    @property
    def inner(self) -> DocumentParser: ...


def parser_billing(parser: DocumentParser) -> OcrBilling:
    """Read billing posture through transparent parser wrappers.

    Legacy third-party parsers without metadata use the ordinary model-call
    posture; only an explicit page posture may claim or reserve OCR pages.
    """
    if isinstance(parser, _BillingMetadata):
        return parser.billing
    if isinstance(parser, _WrappedParser):
        return parser_billing(parser.inner)
    return OcrBilling.MODEL_CALL


@dataclass(frozen=True, slots=True)
class MistralOcrParser:
    """The dedicated OCR wire: POST ``/v1/ocr`` with a data-URL document.

    Live-verified shape: ``{"model": …, "document": {"type": "image_url",
    "image_url": "data:<mime>;base64,<b64>"}}`` (PDFs: ``"type":
    "document_url"``) → ``{"pages": [{"index", "markdown", …}]}``.
    """

    ref: ModelRef
    client: httpx.AsyncClient
    api_key: str
    base_url: str = "https://api.mistral.ai"
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    @property
    def billing(self) -> OcrBilling:
        return OcrBilling.PAGE

    async def parse_image(self, image: ImageData) -> str:
        from smartpipe.io import metering

        metering.add_request_media((image,))
        pages = await self._pages(
            {"type": "image_url", "image_url": _data_url(image.mime, image.data)}
        )
        return "\n\n".join(page.markdown for page in pages if page.markdown).strip()

    async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
        import asyncio

        data = await asyncio.to_thread(path.read_bytes)
        return await self._pages(
            {"type": "document_url", "document_url": _data_url("application/pdf", data)}
        )

    async def _pages(self, document: dict[str, str]) -> tuple[OcrPage, ...]:
        from smartpipe.core.jsontools import as_items, as_record
        from smartpipe.io import metering

        payload: dict[str, object] = {"model": self.ref.name, "document": document}
        headers = {"Authorization": f"Bearer {self.api_key}"}

        async def attempt() -> object:
            response = await self.client.post(
                f"{self.base_url}/v1/ocr", json=payload, headers=headers
            )
            response.raise_for_status()
            return decode_json_response(response, provider="Mistral OCR")

        try:
            parsed = await with_retries(
                self.retry,
                attempt,
                is_retryable=is_retryable_http,
                delay_hint=retry_after_seconds,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise SetupFault(
                    "error: the OCR wire rejected the API key\n"
                    "  Mistral document parsing uses MISTRAL_API_KEY — set it and retry\n"
                    "  (create one at console.mistral.ai), or unset ocr-model."
                ) from exc
            # B4: the message carries the HUMAN reason for the status, never the raw
            # wire body — a 429/5xx JSON blob dumped verbatim buried the load-bearing
            # lines. The status code stays; the body is dropped.
            if status == 429:
                raise RetryableError(f"ocr error {status}: rate limited") from exc
            if status >= 500:
                raise TransportError(f"ocr error {status}: server error") from exc
            raise ItemError(f"ocr error {status}: request rejected") from exc
        except httpx.HTTPError as exc:
            raise TransportError(f"ocr request failed ({exc})") from exc
        record = as_record(parsed)
        rows = as_items(record.get("pages")) if record is not None else None
        if rows is None:
            raise ItemError("the OCR endpoint returned an unexpected shape")
        if not rows:
            raise ItemError("the OCR endpoint returned no pages")
        pages: list[OcrPage] = []
        for position, row in enumerate(rows):
            entry = as_record(row)
            if entry is None:
                raise ItemError("the OCR endpoint returned an unexpected shape")
            index = entry.get("index")
            markdown = entry.get("markdown")
            pages.append(
                OcrPage(
                    index=index if isinstance(index, int) else position,
                    markdown=markdown.strip() if isinstance(markdown, str) else "",
                )
            )
        for _ in pages:
            metering.add_conversion()  # the wire charges per page (D40: observed units)
        return tuple(sorted(pages, key=lambda page: page.index))


@dataclass(frozen=True, slots=True)
class VisionOcrParser:
    """Any non-mistral ref: the normal chat-vision wire with an extract-the-
    text framing (a local llava works as a free OCR). PDFs parse per page —
    the local text layer where it is rich, the page's image through the model
    where the layer is thin (the D39/03 scanned-page case, outranked here).

    The PDF readers are injected first-class functions (the ``with_retries``
    pattern): production uses the real extractors, tests hand in fakes."""

    chat: ChatModel
    page_texts: Callable[[Path], list[str]] = pdf_page_texts
    page_images: Callable[[Path], EmbeddedMedia] = embedded_images

    @property
    def billing(self) -> OcrBilling:
        return OcrBilling.MODEL_CALL

    @property
    def ref(self) -> ModelRef:
        return self.chat.ref

    async def parse_image(self, image: ImageData) -> str:
        text = await self.chat.complete(
            CompletionRequest(
                system=OCR_SYSTEM,
                user="Extract the text from this page.",
                media=(image,),
                max_tokens=_VISION_MAX_TOKENS,
            )
        )
        if not text.strip():
            raise ItemError("the model returned no text for this page")
        return text.strip()

    async def parse_pdf(self, path: Path) -> tuple[OcrPage, ...]:
        import asyncio

        texts = await asyncio.to_thread(self.page_texts, path)
        media = await asyncio.to_thread(self.page_images, path)
        by_page: dict[int, ImageData] = {}
        for found in media.images:
            page_number = _pdf_image_page(found.where)
            if page_number is not None and page_number not in by_page:
                by_page[page_number] = found.image  # the page's FIRST image = the scan
        pages: list[OcrPage] = []
        for number, text in enumerate(texts, start=1):
            layer = text.strip()
            if len(layer) >= _THIN_TEXT or number not in by_page:
                pages.append(OcrPage(index=number - 1, markdown=layer))
                continue
            read = await self.parse_image(by_page[number])
            pages.append(OcrPage(index=number - 1, markdown=_merge(layer, read)))
        if not pages:
            raise ItemError("the PDF contains no pages")
        return tuple(pages)


def pdf_page_count(path: Path) -> int:
    """Count a PDF's billable pages without extracting or uploading it.

    Mistral's dedicated OCR wire charges per page. This local admission check
    therefore has to finish before the request body is built or sent.
    """
    try:
        from pypdf import PdfReader

        count = len(PdfReader(str(path)).pages)
    except Exception as exc:
        raise ItemError(f"{path.name} couldn't be counted as a PDF ({exc})") from exc
    if count < 1:
        raise ItemError(f"{path.name} contains no pages")
    return count


def _pdf_image_page(where: str) -> int | None:
    """``"p.7 img.2"`` → 7 — the page an embedded PDF image lives on."""
    matched = re.match(r"p\.(\d+) ", where)
    return int(matched.group(1)) if matched else None


def _merge(layer: str, read: str) -> str:
    return f"{layer}\n\n{read}".strip() if layer else read


def _data_url(mime: str, data: bytes) -> str:
    import base64

    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
