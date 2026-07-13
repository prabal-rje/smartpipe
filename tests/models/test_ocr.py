"""The document-parsing wires (item 40): mistral /v1/ocr + the vision rung.

The mistral fixtures mirror the live-verified response shape (one tiny image
and one 1-page PDF against the real endpoint — recorded, scrubbed).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.core.errors import ItemError, RetryableError, SetupFault, TransportError
from smartpipe.models.base import CompletionRequest, ImageData, ModelRef
from smartpipe.models.ocr import MistralOcrParser, OcrPage, VisionOcrParser
from smartpipe.models.retry import RetryPolicy
from smartpipe.parsing.extract import EmbeddedImage, EmbeddedMedia

if TYPE_CHECKING:
    from pathlib import Path

    import respx

FAST = RetryPolicy(attempts=1, base_delay=0.0)
URL = "https://api.mistral.ai/v1/ocr"

# the live-verified page shape (fields beyond index/markdown pass through unread)
_PAGE: dict[str, object] = {
    "index": 0,
    "markdown": "# Hello\n\nWorld",
    "images": [],
    "tables": [],
    "dimensions": {"dpi": 200, "height": 100, "width": 200},
}


def _parser(client: httpx.AsyncClient) -> MistralOcrParser:
    return MistralOcrParser(
        ref=ModelRef("mistral", "mistral-ocr-latest"), client=client, api_key="mk-x", retry=FAST
    )


async def test_image_posts_a_data_url_document(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(URL).mock(return_value=httpx.Response(200, json={"pages": [_PAGE]}))
    async with httpx.AsyncClient() as client:
        text = await _parser(client).parse_image(ImageData(b"\x89PNGpayload", "image/png"))
    assert text == "# Hello\n\nWorld"
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "mistral-ocr-latest"
    assert body["document"]["type"] == "image_url"
    assert body["document"]["image_url"].startswith("data:image/png;base64,")


async def test_pdf_posts_a_document_url(respx_mock: respx.MockRouter, tmp_path: Path) -> None:
    pdf = tmp_path / "one.pdf"
    pdf.write_bytes(b"%PDF-1.4 tiny")
    route = respx_mock.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={"pages": [{"index": 1, "markdown": "two"}, {"index": 0, "markdown": "one"}]},
        )
    )
    async with httpx.AsyncClient() as client:
        pages = await _parser(client).parse_pdf(pdf)
    assert pages == (OcrPage(0, "one"), OcrPage(1, "two"))  # sorted by wire index
    body = json.loads(route.calls.last.request.content)
    assert body["document"]["type"] == "document_url"
    assert body["document"]["document_url"].startswith("data:application/pdf;base64,")


async def test_401_names_the_key(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(401, text="no"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(SetupFault, match="MISTRAL_API_KEY"):
            await _parser(client).parse_image(ImageData(b"x", "image/png"))


async def test_wire_error_is_an_item_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(422, text="bad document"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ItemError, match="422") as excinfo:
            await _parser(client).parse_image(ImageData(b"x", "image/png"))
    # B4: keep the status, drop the raw wire body — a human reason, not a JSON dump.
    assert "bad document" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("status", "fault", "reason"),
    ((429, RetryableError, "rate limited"), (503, TransportError, "server error")),
)
async def test_exhausted_transient_faults_reach_shared_admission(
    respx_mock: respx.MockRouter,
    status: int,
    fault: type[ItemError],
    reason: str,
) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(status, text="try later"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(fault) as excinfo:
            await _parser(client).parse_image(ImageData(b"x", "image/png"))
    # B4: the message renders the HUMAN reason, never the raw wire body.
    message = str(excinfo.value)
    assert reason in message and str(status) in message
    assert "try later" not in message


async def test_429_body_is_never_dumped_into_the_message(respx_mock: respx.MockRouter) -> None:
    """B4: a 429 whose JSON body could be a paragraph of wire detail collapses to
    the honest 'rate limited' — the body is dropped, the status kept."""
    respx_mock.post(URL).mock(
        return_value=httpx.Response(429, json={"detail": "quota_exhausted_marker_xyz"})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(RetryableError) as excinfo:
            await _parser(client).parse_image(ImageData(b"x", "image/png"))
    message = str(excinfo.value)
    assert message == "ocr error 429: rate limited"
    assert "quota_exhausted_marker_xyz" not in message


async def test_429_retry_after_reaches_the_fault(respx_mock: respx.MockRouter) -> None:
    """A5.2: the server's Retry-After survives onto the exhausted fault so the
    shared admission policy can pace the ref's next call — not just this wire's
    internal ladder."""
    respx_mock.post(URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "12"}, text="slow down")
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(RetryableError) as excinfo:
            await _parser(client).parse_image(ImageData(b"x", "image/png"))
    assert excinfo.value.retry_after == 12.0


async def test_429_without_a_retry_after_header_carries_no_hint(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(429, text="slow down"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(RetryableError) as excinfo:
            await _parser(client).parse_image(ImageData(b"x", "image/png"))
    assert excinfo.value.retry_after is None


async def test_the_dedicated_ocr_ladder_rides_out_four_rate_limits(
    respx_mock: respx.MockRouter,
) -> None:
    """A5.3: the OCR wire's dedicated ladder attempts five times, so four
    consecutive 429s are ridden out and the fifth attempt's success is returned —
    the default three-attempt ladder would have abandoned the (paid) page. The
    attempt count is taken from the real ``OCR_RETRY_POLICY`` (base delay zeroed so
    the test is instant) so a shrink to 3 fails here too."""
    from dataclasses import replace

    from smartpipe.models.ocr import OCR_RETRY_POLICY

    ladder = replace(OCR_RETRY_POLICY, base_delay=0.0)
    assert ladder.attempts == 5

    attempts = 0

    def responder(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 5:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json={"pages": [_PAGE]})

    respx_mock.post(URL).mock(side_effect=responder)
    async with httpx.AsyncClient() as client:
        parser = MistralOcrParser(
            ref=ModelRef("mistral", "mistral-ocr-latest"),
            client=client,
            api_key="mk-x",
            retry=ladder,
        )
        text = await parser.parse_image(ImageData(b"x", "image/png"))
    assert text == "# Hello\n\nWorld"
    assert attempts == 5


async def test_unexpected_shape_is_an_item_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(200, json={"nope": True}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ItemError, match="unexpected shape"):
            await _parser(client).parse_image(ImageData(b"x", "image/png"))


async def test_empty_pages_reply_is_an_item_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(200, json={"pages": []}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ItemError, match="no pages"):
            await _parser(client).parse_image(ImageData(b"x", "image/png"))


async def test_malformed_success_json_is_an_item_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(200, text="not-json"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ItemError, match="Mistral OCR returned malformed JSON"):
            await _parser(client).parse_image(ImageData(b"x", "image/png"))


class FakeVision:
    """A chat model that reads page images; blank replies are configurable."""

    ref = ModelRef("ollama", "llava")

    def __init__(self, reply: str = "READ TEXT") -> None:
        self.reply = reply
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> str:
        self.requests.append(request)
        return self.reply


async def test_vision_rung_frames_the_extraction() -> None:
    chat = FakeVision(reply="  # Scan\ncontents  ")
    parser = VisionOcrParser(chat=chat)
    text = await parser.parse_image(ImageData(b"png", "image/png"))
    assert text == "# Scan\ncontents"
    request = chat.requests[0]
    assert request.system is not None and "Extract ALL text" in request.system
    assert request.media and isinstance(request.media[0], ImageData)


async def test_vision_rung_refuses_an_empty_reply() -> None:
    parser = VisionOcrParser(chat=FakeVision(reply="   "))
    with pytest.raises(ItemError, match="no text"):
        await parser.parse_image(ImageData(b"png", "image/png"))


async def test_vision_pdf_reads_thin_pages_through_the_model(tmp_path: Path) -> None:
    """D39/03 outranked: rich pages keep their local text (zero calls); a
    thin page's image goes through the model and merges with its scraps."""
    scan = ImageData(b"jpegbytes", "image/jpeg")
    rich = "This page has a perfectly healthy extractable text layer, well past thin."
    parser = VisionOcrParser(
        chat=FakeVision(reply="OCR OF PAGE TWO"),
        page_texts=lambda _path: [rich, "p2"],
        page_images=lambda _path: EmbeddedMedia((EmbeddedImage(scan, "p.2 img.1"),), 0),
    )
    pages = await parser.parse_pdf(tmp_path / "scan.pdf")
    assert pages[0] == OcrPage(0, rich)
    assert pages[1] == OcrPage(1, "p2\n\nOCR OF PAGE TWO")


async def test_vision_pdf_thin_page_without_an_image_keeps_its_scraps(tmp_path: Path) -> None:
    parser = VisionOcrParser(
        chat=FakeVision(),
        page_texts=lambda _path: ["thin scraps"],
        page_images=lambda _path: EmbeddedMedia((), 0),
    )
    pages = await parser.parse_pdf(tmp_path / "scan.pdf")
    assert pages == (OcrPage(0, "thin scraps"),)


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("image-response.json", "HELLO OCR 42"),
        ("pdf-response.json", "SMARTPIPE PDF PA"),
    ],
)
async def test_recorded_live_responses_parse(
    respx_mock: respx.MockRouter, fixture: str, expected: str
) -> None:
    """The real wire's shape, recorded from the ONE live verification of each
    document type (2026-07-08, scrubbed) — the parser must keep reading it."""
    from pathlib import Path as _Path

    recorded = (_Path(__file__).parent.parent / "fixtures" / "ocr" / fixture).read_text(
        encoding="utf-8"
    )
    respx_mock.post(URL).mock(return_value=httpx.Response(200, text=recorded))
    async with httpx.AsyncClient() as client:
        text = await _parser(client).parse_image(ImageData(b"png", "image/png"))
    assert text == expected
