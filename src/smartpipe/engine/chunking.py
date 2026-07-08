"""Chunking math for the recursive ``reduce`` (spec §3.5) — pure.

The reduce tree "just works": when the input exceeds what the model can hold,
smartpipe splits it into chunks, summarizes each, and recurses on the summaries —
no flags, no strategy selection. This module owns the arithmetic (token estimate,
context budget, item-boundary splitting); the verb drives the recursion, since
each level's size isn't known until the model produces the notes.

Token estimation is deliberately crude (≈4 chars/token) and used with a safety
factor — being conservative just means more levels, never a truncated call.
"""

from __future__ import annotations

import math
import re
import struct
from typing import TYPE_CHECKING, assert_never

from smartpipe.models.base import AudioData, ImageData, VideoData

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from smartpipe.models.base import MediaData

__all__ = [
    "budget_for",
    "chunk_indices",
    "estimate_tokens",
    "fits_in_one",
    "halve",
    "image_dimensions",
    "is_context_overflow",
    "mean_pool",
    "media_tokens",
    "split_text",
]

# Conservative per-provider input windows; ollama is deliberately small because we
# can't cheaply know a local model's context length (a smaller budget is always safe).
_CONTEXT: dict[str, int] = {
    "ollama": 8000,
    "openai": 128000,
    "anthropic": 200000,
    "mistral": 128000,
    "gemini": 128000,  # conservative: flash models carry ≥1M, but budget for the floor
    "openrouter": 32000,  # unknowable per-model — the safe floor for routed models
}
_SAFETY = 0.6
_CHARS_PER_TOKEN = 4

# CJK-block codepoints tokenize at roughly one token per character, not one per
# four — chars/4 alone under-counts ideographic text 4x (D26 v2). The class spans
# radicals/kana/unified ideographs, hangul syllables, compatibility ideographs,
# fullwidth forms, and the supplementary ideographic planes.
_CJK = re.compile(
    "["
    "\u2e80-\u9fff"  # radicals, kana, CJK unified ideographs
    "\uac00-\ud7af"  # hangul syllables
    "\uf900-\ufaff"  # CJK compatibility ideographs
    "\uff00-\uffef"  # fullwidth and halfwidth forms
    "\U00020000-\U0002fa1f"  # supplementary ideographic planes (ext. B-F)
    "]"
)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    rough = math.ceil(len(text) / _CHARS_PER_TOKEN)
    if text.isascii():  # the hot path: no CJK scan on plain western text
        return rough
    return max(rough, len(_CJK.findall(text)))


def budget_for(provider: str, *, prompt_overhead: int, window: int | None = None) -> int:
    """Token budget for one call. ``window`` (from a live probe or the
    SMARTPIPE_CONTEXT_TOKENS override) beats the static table when present."""
    resolved = window if window is not None else _CONTEXT.get(provider, _CONTEXT["ollama"])
    return int(resolved * _SAFETY) - prompt_overhead


_OVERFLOW_MARKERS = (
    "context_length",
    "context length",
    "context window",
    "maximum context",
    "too long",
    "too large",
    "input length",
    "prompt is too long",
    "exceeds the limit",
    "input token count",  # gemini's 400 INVALID_ARGUMENT wording
    "exceeds the maximum number of tokens",
)


def is_context_overflow(message: str) -> bool:
    """Does this per-item error text look like a context-window overflow?

    The classifier that lets reduce self-correct (D26): estimates are hints,
    the wire's own rejection is ground truth — matched loosely on the phrases
    the five wired providers actually use."""
    lowered = message.lower()
    return any(marker in lowered for marker in _OVERFLOW_MARKERS)


def halve(chunk: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Split a chunk of item indexes into two non-empty halves (len must be ≥ 2)."""
    middle = len(chunk) // 2
    return chunk[:middle], chunk[middle:]


def fits_in_one(sizes: Sequence[int], budget: int) -> bool:
    return sum(sizes) <= budget


def chunk_indices(sizes: Sequence[int], budget: int) -> tuple[tuple[int, ...], ...]:
    """Group consecutive item indexes into chunks whose total estimate fits the
    budget. An item is never split; one that alone exceeds the budget gets its own
    (over-budget) chunk rather than being dropped."""
    chunks: list[tuple[int, ...]] = []
    current: list[int] = []
    current_size = 0
    for index, size in enumerate(sizes):
        if current and current_size + size > budget:
            chunks.append(tuple(current))
            current = []
            current_size = 0
        current.append(index)
        current_size += size
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


def split_text(text: str, budget: int) -> tuple[str, ...]:
    """Break one oversized text into ≤-budget chunks (D26 layer 3).

    Paragraph boundaries first, then lines, then a hard character cut — and the
    chunks concatenate back to the original text exactly (nothing added, nothing
    lost; separators travel with their preceding piece)."""
    if estimate_tokens(text) <= budget:
        return (text,)
    pieces = _carrying_separators(text, r"\n\n+")
    if len(pieces) == 1:
        pieces = _carrying_separators(text, r"\n")
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        for part in _hard_cut(piece, budget):
            if current and estimate_tokens(current + part) > budget:
                chunks.append(current)
                current = ""
            current += part
    if current:
        chunks.append(current)
    return tuple(chunks)


def _carrying_separators(text: str, pattern: str) -> list[str]:
    """Split by a separator regex, attaching each separator to the piece before
    it, so ``"".join(result) == text``."""
    import re

    tokens = re.split(f"({pattern})", text)
    pieces: list[str] = []
    for token in tokens:
        if pieces and re.fullmatch(pattern, token or " ") and token:
            pieces[-1] += token
        elif token:
            pieces.append(token)
    return pieces or [text]


def _hard_cut(piece: str, budget: int) -> tuple[str, ...]:
    """A single piece that alone exceeds the budget is cut at character bounds.

    The cut width comes from the piece's own token density (D26 v2): CJK text
    runs ~1 token per character, so a flat chars-per-token width would leave
    ideographic pieces 4x over budget."""
    tokens = estimate_tokens(piece)
    if tokens <= budget:
        return (piece,)
    parts = math.ceil(tokens / budget)
    limit = max(len(piece) // parts, 1)
    return tuple(piece[i : i + limit] for i in range(0, len(piece), limit))


def mean_pool(vectors: Sequence[tuple[float, ...]]) -> tuple[float, ...]:
    """Component-wise mean of chunk embeddings — the standard whole-document
    vector when one text had to be embedded in pieces (D26)."""
    assert vectors, "mean_pool needs at least one vector"
    count = len(vectors)
    return tuple(sum(component) / count for component in zip(*vectors, strict=True))


# --- media-aware token estimation (D26 v2) -------------------------------------
#
# Media parts spend context too; pretending they're free let an "under-budget"
# item overflow on the wire. The per-wire arithmetic below follows the published
# billing formulas; everything is multiplied by a 1.25 safety factor, and an
# UNKNOWN wire takes the most expensive answer (assume-expensive humility —
# over-estimating means chunking sooner, never a truncated call).

_MEDIA_SAFETY = 1.25
_ASSUMED_DIMENSIONS = (4096, 4096)  # unparseable headers: assume large
_AUDIO_TOKENS_PER_SECOND = 32  # gemini's published rate — the priciest wired
_VIDEO_TOKENS_PER_SECOND = 300  # gemini: ~258/frame + audio at 1 fps
_AUDIO_FALLBACK_SECONDS_PER_MB = 180.0  # ≈ 46 kbps — voice codecs run this low
_VIDEO_FALLBACK_SECONDS_PER_MB = 20.0  # ≈ 400 kbps — a low-bitrate screencast
_MB = 1_048_576


def media_tokens(
    parts: Sequence[MediaData],
    provider: str,
    *,
    seconds_of: Callable[[bytes, str], float | None] | None = None,
) -> int:
    """Estimated context tokens the media parts of one item will consume.

    ``seconds_of`` is the injected duration probe (io/metering owns the WAV +
    ffmpeg machinery); with none — or when it can't tell — a conservative
    per-MB rate stands in.
    """
    total = 0
    for part in parts:
        match part:
            case ImageData():
                width, height = image_dimensions(part.data) or _ASSUMED_DIMENSIONS
                total += math.ceil(_image_tokens(provider, width, height) * _MEDIA_SAFETY)
            case AudioData():
                seconds = _clip_seconds(part, seconds_of, _AUDIO_FALLBACK_SECONDS_PER_MB)
                total += math.ceil(seconds * _AUDIO_TOKENS_PER_SECOND * _MEDIA_SAFETY)
            case VideoData():
                seconds = _clip_seconds(part, seconds_of, _VIDEO_FALLBACK_SECONDS_PER_MB)
                total += math.ceil(seconds * _VIDEO_TOKENS_PER_SECOND * _MEDIA_SAFETY)
            case _ as unreachable:  # pragma: no cover — the union is closed
                assert_never(unreachable)
    return total


def _clip_seconds(
    part: AudioData | VideoData,
    seconds_of: Callable[[bytes, str], float | None] | None,
    fallback_per_mb: float,
) -> float:
    probed = seconds_of(part.data, part.mime) if seconds_of is not None else None
    if probed is not None:
        return probed
    return len(part.data) / _MB * fallback_per_mb


def _image_tokens(provider: str, width: int, height: int) -> int:
    match provider:
        case "gemini":
            return _gemini_image_tokens(width, height)
        case "openai":
            return _openai_image_tokens(width, height)
        case "anthropic":
            return _anthropic_image_tokens(width, height)
        case _:  # unknown wire: the most expensive published formula
            return max(
                _gemini_image_tokens(width, height),
                _openai_image_tokens(width, height),
                _anthropic_image_tokens(width, height),
            )


def _gemini_image_tokens(width: int, height: int) -> int:
    """258 per ~768px tile; a small image is a flat 258 (one tile)."""
    return 258 * math.ceil(width / 768) * math.ceil(height / 768)


def _openai_image_tokens(width: int, height: int) -> int:
    """85 base + 170 per 512px tile, after the published downscale (fit in
    2048², then shortest side to 768)."""
    scale = min(1.0, 2048 / max(width, height))
    scaled_w, scaled_h = width * scale, height * scale
    scale = min(1.0, 768 / min(scaled_w, scaled_h))
    scaled_w, scaled_h = scaled_w * scale, scaled_h * scale
    return 85 + 170 * math.ceil(scaled_w / 512) * math.ceil(scaled_h / 512)


def _anthropic_image_tokens(width: int, height: int) -> int:
    return math.ceil(width * height / 750)


# --- image header parsing (pure struct reads — no imaging dependency) -----------


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """(width, height) from PNG/JPEG/GIF/WebP header bytes; None when the
    format is unknown or the header is truncated (callers assume large)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return int(width), int(height)
    if data.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(data)
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return _webp_dimensions(data)
    return None


_JPEG_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
_JPEG_BARE_MARKERS = frozenset({0x01, *range(0xD0, 0xD9)})  # no length field


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            return None  # lost sync — not a well-formed JPEG stream
        marker = data[offset + 1]
        if marker == 0xFF:  # fill byte
            offset += 1
            continue
        if marker in _JPEG_SOF_MARKERS:
            if offset + 9 > len(data):
                return None
            height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
            return int(width), int(height)
        if marker in _JPEG_BARE_MARKERS:
            offset += 2
            continue
        (length,) = struct.unpack(">H", data[offset + 2 : offset + 4])
        offset += 2 + length
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    kind = data[12:16]
    if kind == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if kind == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", data[26:30])
        return int(width) & 0x3FFF, int(height) & 0x3FFF
    if kind == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        (bits,) = struct.unpack("<I", data[21:25])
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None
