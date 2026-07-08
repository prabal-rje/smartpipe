from __future__ import annotations

import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smartpipe.engine.chunking import (
    budget_for,
    chunk_indices,
    estimate_tokens,
    fits_in_one,
    halve,
    image_dimensions,
    is_context_overflow,
    mean_pool,
    media_tokens,
    split_text,
)
from smartpipe.models.base import AudioData, ImageData, VideoData

# --- estimation ---------------------------------------------------------------


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1  # 4 chars ≈ 1 token
    assert estimate_tokens("a" * 10) == 3  # ceil(10/4)


def test_cjk_text_counts_at_least_one_token_per_ideograph() -> None:
    # 8 CJK codepoints: chars/4 would claim 2 tokens — a 4x lie for ideographs
    assert estimate_tokens("日本語のテキスト") == 8
    assert estimate_tokens("漢" * 100) == 100


def test_mixed_text_takes_the_larger_estimate() -> None:
    # 20 chars → 5 by the chars/4 rule; only 2 CJK chars — the rule wins
    assert estimate_tokens("The word 漢字 appears") == 5
    # mostly CJK: the per-ideograph floor wins
    assert estimate_tokens("ab" + "字" * 10) == 10


def test_hangul_and_kana_count_like_ideographs() -> None:
    assert estimate_tokens("한국어") == 3
    assert estimate_tokens("カタカナ") == 4


# --- budget -------------------------------------------------------------------


def test_budget_applies_safety_factor_and_overhead() -> None:
    # ollama default 8000 * 0.6 = 4800, minus 200 overhead
    assert budget_for("ollama", prompt_overhead=200) == 4600


def test_budget_by_provider() -> None:
    assert budget_for("anthropic", prompt_overhead=0) == 120000  # 200000 * 0.6
    assert budget_for("openai", prompt_overhead=0) == 76800  # 128000 * 0.6


# --- chunking -----------------------------------------------------------------


def test_all_items_fit_in_one_chunk() -> None:
    assert chunk_indices([10, 20, 30], budget=100) == ((0, 1, 2),)


def test_splits_when_over_budget() -> None:
    # budget 50: [30, 30] exceeds → split; [30] + [30, 10] etc.
    assert chunk_indices([30, 30, 10], budget=50) == ((0,), (1, 2))


def test_oversize_single_item_gets_its_own_chunk() -> None:
    # item 1 alone (100) exceeds budget 50 — can't split, so it's alone
    assert chunk_indices([10, 100, 10], budget=50) == ((0,), (1,), (2,))


def test_fits_in_one() -> None:
    assert fits_in_one([10, 20], budget=100) is True
    assert fits_in_one([60, 60], budget=100) is False


# --- properties ---------------------------------------------------------------


@given(
    sizes=st.lists(st.integers(min_value=0, max_value=100), max_size=30),
    budget=st.integers(min_value=1, max_value=200),
)
def test_chunking_invariants(sizes: list[int], budget: int) -> None:
    chunks = chunk_indices(sizes, budget)
    # every item appears exactly once, in order
    flat = [i for chunk in chunks for i in chunk]
    assert flat == list(range(len(sizes)))
    # each chunk fits, unless it's a single item that alone exceeds the budget
    for chunk in chunks:
        total = sum(sizes[i] for i in chunk)
        assert total <= budget or len(chunk) == 1


@given(sizes=st.lists(st.integers(min_value=0, max_value=50), min_size=1, max_size=20))
def test_fits_implies_one_chunk(sizes: list[int]) -> None:
    budget = sum(sizes) + 1
    assert fits_in_one(sizes, budget)
    assert len(chunk_indices(sizes, budget)) == 1


def test_window_override_beats_the_table() -> None:
    assert budget_for("ollama", prompt_overhead=0, window=100_000) == 60_000
    assert budget_for("openai", prompt_overhead=500) == int(128_000 * 0.6) - 500


@pytest.mark.parametrize(
    "message",
    [
        "This model's maximum context length is 8192 tokens",  # openai
        "prompt is too long: 210000 tokens > 200000 maximum",  # anthropic
        "the input length exceeds the limit",
        "Request too large",  # mistral 413-style
    ],
)
def test_overflow_messages_classify(message: str) -> None:
    assert is_context_overflow(message) is True


def test_ordinary_errors_do_not_classify() -> None:
    assert is_context_overflow("model returned invalid JSON") is False
    assert is_context_overflow("rate limited, retry later") is False


def test_halve_splits_non_empty() -> None:
    assert halve((0, 1, 2, 3, 4)) == ((0, 1), (2, 3, 4))
    assert halve((7, 9)) == ((7,), (9,))


# --- split_text (D26 layer 3) --------------------------------------------------


def test_small_text_is_one_chunk() -> None:
    assert split_text("hello", 100) == ("hello",)


def test_paragraph_boundaries_win() -> None:
    text = "para one is here.\n\npara two is here.\n\npara three."
    chunks = split_text(text, budget=6)  # ~24 chars per chunk
    assert all(estimate_tokens(c) <= 6 or "\n\n" not in c for c in chunks)
    assert "".join(chunks) == text  # nothing added, nothing lost


@given(
    body=st.text(min_size=0, max_size=2000),
    budget=st.integers(min_value=1, max_value=50),
)
def test_chunks_always_reassemble_exactly(body: str, budget: int) -> None:
    assert "".join(split_text(body, budget)) == body


@given(
    body=st.text(alphabet="ab \n", min_size=1, max_size=800),
    budget=st.integers(min_value=2, max_value=20),
)
def test_multi_piece_chunks_respect_the_budget(body: str, budget: int) -> None:
    # any chunk over budget must be a single indivisible hard-cut piece
    for chunk in split_text(body, budget):
        assert estimate_tokens(chunk) <= budget or len(chunk) <= budget * 4 + 2


def test_mean_pool_averages_componentwise() -> None:
    assert mean_pool([(1.0, 0.0), (0.0, 1.0)]) == (0.5, 0.5)
    assert mean_pool([(2.0, 4.0)]) == (2.0, 4.0)


# --- image header parsing (D26 v2: media-aware estimation) ----------------------
# Real tiny header bytes per format — the parser reads structs, never pixels.


def png_bytes(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
    )


def gif_bytes(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height)


def jpeg_bytes(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + bytes(9)
    sof0 = b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", height, width)
    return b"\xff\xd8" + app0 + sof0 + b"\x01\x11\x00"


def webp_vp8x_bytes(width: int, height: int) -> bytes:
    chunk = (
        b"VP8X"
        + struct.pack("<I", 10)
        + b"\x00\x00\x00\x00"
        + struct.pack("<I", width - 1)[:3]
        + struct.pack("<I", height - 1)[:3]
    )
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def webp_vp8_bytes(width: int, height: int) -> bytes:
    payload = b"\x00\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", width, height)
    chunk = b"VP8 " + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def webp_vp8l_bytes(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    payload = b"\x2f" + struct.pack("<I", bits)
    chunk = b"VP8L" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (png_bytes(1024, 512), (1024, 512)),
        (gif_bytes(320, 200), (320, 200)),
        (jpeg_bytes(640, 480), (640, 480)),
        (webp_vp8x_bytes(800, 600), (800, 600)),
        (webp_vp8_bytes(1280, 720), (1280, 720)),
        (webp_vp8l_bytes(333, 77), (333, 77)),
    ],
    ids=["png", "gif", "jpeg", "webp-vp8x", "webp-vp8", "webp-vp8l"],
)
def test_image_dimensions_from_real_header_bytes(data: bytes, expected: tuple[int, int]) -> None:
    assert image_dimensions(data) == expected


@pytest.mark.parametrize(
    "data",
    [b"", b"not an image at all", b"\x89PNG\r\n\x1a\ntrunc", b"RIFF\x00\x00\x00\x00WAVE"],
    ids=["empty", "garbage", "truncated-png", "riff-but-wav"],
)
def test_unparseable_dimensions_are_none(data: bytes) -> None:
    assert image_dimensions(data) is None


def _sof0(width: int, height: int) -> bytes:
    return b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", height, width)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"\xff\xd8" + b"\x00\x00\x00\x00", None),  # lost sync
        (b"\xff\xd8\xff" + _sof0(12, 34), (12, 34)),  # fill byte before the marker
        (b"\xff\xd8" + b"\xff\xc0\x00\x0b", None),  # SOF truncated before the dims
        (b"\xff\xd8" + b"\xff\x01" + _sof0(56, 78), (56, 78)),  # bare TEM marker first
        (b"\xff\xd8" + b"\xff\xe0\x00\x04\x00\x00", None),  # APP0 only, no SOF
    ],
    ids=["lost-sync", "fill-byte", "truncated-sof", "bare-marker", "no-sof"],
)
def test_jpeg_marker_walk_edges(data: bytes, expected: tuple[int, int] | None) -> None:
    assert image_dimensions(data) == expected


def test_webp_with_an_unknown_leading_chunk_is_none() -> None:
    chunk = b"ALPH" + struct.pack("<I", 4) + b"\x00\x00\x00\x00"
    data = b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk
    assert image_dimensions(data) is None


# --- media_tokens (D26 v2) -------------------------------------------------------
# All numbers carry the 1.25 safety multiplier.


def test_gemini_image_tokens_flat_258_when_small() -> None:
    part = ImageData(png_bytes(512, 512), "image/png")
    assert media_tokens((part,), "gemini") == 323  # 258 x 1.25, one tile


def test_gemini_image_tokens_per_768px_tile() -> None:
    part = ImageData(png_bytes(1024, 512), "image/png")  # 2x1 tiles of 768px
    assert media_tokens((part,), "gemini") == 645  # 258 x 2 x 1.25


def test_openai_image_tokens_use_the_512px_tile_math() -> None:
    part = ImageData(png_bytes(1024, 512), "image/png")  # 2 tiles of 512px
    assert media_tokens((part,), "openai") == 532  # (85 + 170x2) x 1.25, ceil


def test_openai_image_tokens_downscale_huge_images_first() -> None:
    # 4096² fits to 2048², then shortest side 768 → 768² → 4 tiles
    part = ImageData(png_bytes(4096, 4096), "image/png")
    assert media_tokens((part,), "openai") == 957  # (85 + 170x4) x 1.25, ceil


def test_anthropic_image_tokens_are_pixels_over_750() -> None:
    part = ImageData(png_bytes(1024, 512), "image/png")
    assert media_tokens((part,), "anthropic") == 875  # ceil(524288/750)=700, x 1.25


def test_unknown_provider_assumes_the_most_expensive_formula() -> None:
    part = ImageData(png_bytes(1024, 512), "image/png")
    # max(gemini 516, openai 425, anthropic 700) = 700 → x 1.25
    assert media_tokens((part,), "ollama") == 875


def test_unparseable_dimensions_assume_large() -> None:
    part = ImageData(b"not an image", "image/png")
    tokens = media_tokens((part,), "anthropic")
    assert tokens >= media_tokens((ImageData(png_bytes(2048, 2048), "image/png"),), "anthropic")


def test_audio_tokens_from_probed_seconds() -> None:
    part = AudioData(b"\x00" * 100, "audio/wav")
    tokens = media_tokens((part,), "gemini", seconds_of=lambda _data, _mime: 10.0)
    assert tokens == 400  # 32 tok/s x 10 s x 1.25


def test_video_tokens_from_probed_seconds() -> None:
    part = VideoData(b"\x00" * 100, "video/mp4")
    tokens = media_tokens((part,), "gemini", seconds_of=lambda _data, _mime: 10.0)
    assert tokens == 3750  # 300 tok/s x 10 s x 1.25


def test_unprobeable_audio_falls_back_to_a_conservative_per_mb_rate() -> None:
    one_mb = AudioData(b"\x00" * 1_048_576, "audio/mpeg")
    tokens = media_tokens((one_mb,), "gemini")  # no probe injected
    assert tokens == media_tokens((one_mb,), "gemini", seconds_of=lambda _d, _m: None)
    assert tokens >= 32 * 60  # at least a minute's worth per MB — assume expensive


def test_media_tokens_sum_across_parts() -> None:
    image = ImageData(png_bytes(512, 512), "image/png")
    audio = AudioData(b"\x00" * 100, "audio/wav")
    total = media_tokens((image, audio), "gemini", seconds_of=lambda _d, _m: 10.0)
    assert total == media_tokens((image,), "gemini") + media_tokens(
        (audio,), "gemini", seconds_of=lambda _d, _m: 10.0
    )


def test_no_media_is_zero_tokens() -> None:
    assert media_tokens((), "gemini") == 0
