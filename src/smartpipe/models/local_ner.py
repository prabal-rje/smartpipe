"""Local NER (wave G1): GLiNER span-mode ONNX on CPU — no torch, no key, no server.

Rides dependencies that already ship (onnxruntime + tokenizers arrive via
fastembed; huggingface_hub does the one-time disclosed download, D44 style).
Preprocessing and span decoding reimplement gliner 0.2.27's
``UniEncoderSpanProcessor``/``SpanDecoder`` (Apache-2.0, read not imported —
the pip package would drag torch into core, which is forbidden). Model output
crosses the boundary as untrusted nesting (the ``jsontools`` pattern); the
decode math is pure Python. Heavy imports stay function-local; the model
loads once per instance and is injected in tests, so CI never downloads a thing.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from smartpipe.core.errors import ItemError, SetupFault
from smartpipe.engine.graphkg import EntitySpan
from smartpipe.io import diagnostics

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping, Sequence

__all__ = [
    "MAX_SPAN_WIDTH",
    "MAX_TEXT_WORDS",
    "NER_REPO",
    "GlinerEntityFinder",
    "NerEncoding",
    "NerEngine",
    "NerSession",
    "NerTokenizer",
    "hf_implicit_token_env",
    "ner_precision",
    "span_grid",
    "split_words",
    "word_level_mask",
]

NER_REPO = "onnx-community/gliner_small-v2.1"
# int8 by DEFAULT (owner ruling): ~2x the speed and a third of the memory of
# fp32, and the recall loss is noise for co-occurrence graphs. fp32 is the
# explicit opt-out for machines with headroom, never an accident.
_MODEL_FILES: dict[str, tuple[str, str]] = {
    "q8": ("onnx/model_quantized.onnx", "~190 MB"),
    "fp32": ("onnx/model.onnx", "~620 MB"),
}
_TOKENIZER_FILE = "tokenizer.json"

MAX_SPAN_WIDTH = 12  # gliner_config.json max_width — spans up to 12 words
MAX_TEXT_WORDS = 384  # gliner_config.json max_len — longer texts window over this
_THRESHOLD = 0.5  # gliner's default confidence bar
_ENT_TOKEN = "<<ENT>>"
_SEP_TOKEN = "<<SEP>>"

# gliner's WhitespaceTokenSplitter: words (hyphen/underscore-joined) or one symbol
_WORDS = re.compile(r"\w+(?:[-_]\w+)*|\S")

_announced = False  # the one-time download note, once per process


class NerEncoding(Protocol):
    """What the wire reads off a ``tokenizers.Encoding`` — fakes are trivial."""

    @property
    def ids(self) -> list[int]: ...
    @property
    def attention_mask(self) -> list[int]: ...
    @property
    def word_ids(self) -> list[int | None]: ...


class NerTokenizer(Protocol):
    """``tokenizers.Tokenizer``'s encode, structurally (fakes match it too)."""

    def encode(
        self,
        sequence: list[str],
        pair: str | None = None,
        is_pretokenized: bool = False,
        add_special_tokens: bool = True,
    ) -> NerEncoding: ...


class NerSession(Protocol):
    """The one onnxruntime call the wire makes."""

    def run(self, output_names: object, input_feed: Mapping[str, object]) -> Sequence[object]: ...


@dataclass(frozen=True, slots=True)
class NerEngine:
    session: NerSession
    tokenizer: NerTokenizer


def ner_precision(env: Mapping[str, str]) -> str:
    """The weight precision dial: q8 (the default — an 8 GB machine stays
    comfortable) or fp32 via ``SMARTPIPE_NER_PRECISION=fp32``. Loud otherwise,
    so nothing ever loads the heavy weights by accident."""
    raw = env.get("SMARTPIPE_NER_PRECISION", "").strip().lower() or "q8"
    if raw not in _MODEL_FILES:
        choices = ", ".join(sorted(_MODEL_FILES))
        raise SetupFault(
            f"error: SMARTPIPE_NER_PRECISION must be one of: {choices} (got {raw!r})\n"
            "  q8 is the default — fp32 trades ~3x the memory for a marginal recall gain."
        )
    return raw


def split_words(text: str) -> tuple[tuple[str, int, int], ...]:
    """gliner's word cut with char offsets: ``(word, start, end)`` per word."""
    return tuple((match.group(), match.start(), match.end()) for match in _WORDS.finditer(text))


def word_level_mask(word_ids: Sequence[int | None], prompt_words: int) -> list[int]:
    """The ``words_mask`` input: text words numbered from 1 at their FIRST
    subtoken; prompt words, special tokens, and continuations are 0."""
    mask: list[int] = []
    previous: int | None = None
    for word_id in word_ids:
        if word_id is None or word_id == previous or word_id < prompt_words:
            mask.append(0)
        else:
            mask.append(word_id - prompt_words + 1)
        previous = word_id
    return mask


def span_grid(num_words: int, max_width: int) -> tuple[list[tuple[int, int]], list[bool]]:
    """Every candidate span as inclusive word indices ``(start, start+width)``,
    plus the validity mask (spans hanging past the text are padding)."""
    spans = [(start, start + width) for start in range(num_words) for width in range(max_width)]
    mask = [end <= num_words - 1 for _, end in spans]
    return spans, mask


@dataclass(slots=True)
class GlinerEntityFinder:
    """The ``EntityFinder`` seam's local implementation: user-named labels in,
    ``EntitySpan``s out. Synchronous CPU compute — callers ``to_thread`` it."""

    labels: tuple[str, ...]
    precision: str = "q8"
    threshold: float = _THRESHOLD
    window_words: int = MAX_TEXT_WORDS
    engine: NerEngine | None = field(default=None, repr=False)  # injected in tests

    def find(self, text: str) -> tuple[EntitySpan, ...]:
        if not self.labels:
            return ()
        words = split_words(text)
        if not words:
            return ()
        engine = self._load()
        found: list[EntitySpan] = []
        for start in range(0, len(words), self.window_words):
            found.extend(self._find_window(engine, text, words[start : start + self.window_words]))
        return tuple(found)

    def _find_window(
        self, engine: NerEngine, text: str, words: tuple[tuple[str, int, int], ...]
    ) -> list[EntitySpan]:
        import numpy as np

        prompt: list[str] = []
        for label in self.labels:
            prompt += [_ENT_TOKEN, label]
        prompt.append(_SEP_TOKEN)
        encoding = engine.tokenizer.encode(
            prompt + [word for word, _, _ in words], is_pretokenized=True
        )
        spans, mask = span_grid(len(words), MAX_SPAN_WIDTH)
        feed: dict[str, object] = {
            "input_ids": np.asarray([encoding.ids], dtype=np.int64),
            "attention_mask": np.asarray([encoding.attention_mask], dtype=np.int64),
            "words_mask": np.asarray(
                [word_level_mask(encoding.word_ids, len(prompt))], dtype=np.int64
            ),
            "text_lengths": np.asarray([[len(words)]], dtype=np.int64),
            "span_idx": np.asarray([spans], dtype=np.int64),
            "span_mask": np.asarray([mask], dtype=np.bool_),
        }
        produced = engine.session.run(None, feed)
        grid = _logits_grid(produced[0] if produced else None)
        if grid is None:
            raise ItemError("the local NER model returned an unexpected shape")
        picks = _pick_spans(grid, len(words), len(self.labels), self.threshold)
        return [
            EntitySpan(
                name=text[words[first][1] : words[last][2]],
                label=self.labels[label_index],
                start=words[first][1],
                end=words[last][2],
            )
            for first, last, label_index in picks
        ]

    def _load(self) -> NerEngine:
        if self.engine is not None:
            return self.engine  # loaded once, reused for every window

        global _announced
        if not _announced:
            _announced = True
            size = _MODEL_FILES[self.precision][1]
            diagnostics.note(
                f"local NER: gliner-small v2.1 ({self.precision}) on CPU "
                f"(first use downloads {size}, once)"
            )
        self.engine = _load_engine(self.precision)
        return self.engine


def _plain(value: object) -> object:
    """numpy arrays expose tolist(); plain nesting passes through untouched."""
    lister = getattr(value, "tolist", None)
    return lister() if callable(lister) else value


def _logits_grid(raw: object) -> list[list[tuple[float, ...]]] | None:
    """The model's ``(1, words, widths, classes)`` logits as checked Python
    nesting — the untrusted-boundary pattern (``core/jsontools``); ``None``
    means "not the shape we were promised"."""
    from smartpipe.core.jsontools import as_float_vector, as_items

    plain = as_items(_plain(raw))
    if plain is None or len(plain) != 1:
        return None
    batch = as_items(plain[0])
    if batch is None:
        return None
    grid: list[list[tuple[float, ...]]] = []
    for row in batch:
        entries = as_items(row)
        if entries is None:
            return None
        widths: list[tuple[float, ...]] = []
        for cell in entries:
            vector = as_float_vector(cell)
            if vector is None:
                return None
            widths.append(vector)
        grid.append(widths)
    return grid


def _pick_spans(
    grid: list[list[tuple[float, ...]]], num_words: int, labels: int, threshold: float
) -> list[tuple[int, int, int]]:
    """gliner's flat-NER decode: candidates above threshold, invalid spans
    dropped, greedy best-score-first with no overlapping ranges; returns
    ``(first_word, last_word, label_index)`` sorted by position. Sigmoid is
    monotonic, so logits compare against the inverted threshold directly."""
    assert 0.0 < threshold < 1.0, "the confidence bar is a probability"
    bar = math.log(threshold / (1.0 - threshold))
    candidates: list[tuple[float, int, int, int]] = []
    for start, widths in enumerate(grid[:num_words]):
        for width, classes in enumerate(widths):
            last = start + width
            if last >= num_words:
                continue  # hangs past the text — a padding span
            candidates += [
                (logit, start, last, label_index)
                for label_index, logit in enumerate(classes[:labels])
                if logit > bar
            ]
    picked: list[tuple[int, int, int]] = []
    for _, first, last, label_index in sorted(candidates, key=lambda entry: -entry[0]):
        if any(last >= kept_first and first <= kept_last for kept_first, kept_last, _ in picked):
            continue  # flat NER: ranges never overlap
        picked.append((first, last, label_index))
    return sorted(picked)


def hf_implicit_token_env(env: MutableMapping[str, str]) -> None:
    """Silence huggingface_hub's "unauthenticated requests / set a HF_TOKEN"
    stderr warning the house way — its own documented toggle, set before the
    import reads it (the same per-library approach as onnxruntime's
    ``log_severity_level`` below). ``setdefault`` never overrides an operator who
    deliberately configured the flag; stderr belongs to ``io/diagnostics``."""
    env.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")


def _load_engine(  # pragma: no cover — the live wire; CI never downloads models
    precision: str,
) -> NerEngine:
    hf_implicit_token_env(os.environ)  # MUST precede the huggingface_hub import below
    try:
        import onnxruntime
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer
    except ImportError as exc:
        raise SetupFault(
            "error: graph --fast needs the local NER stack (onnxruntime + tokenizers)\n"
            "  Those wheels haven't landed for this Python yet — run smartpipe on 3.11-3.13."
        ) from exc

    model_path = hf_hub_download(NER_REPO, _MODEL_FILES[precision][0])
    tokenizer_path = hf_hub_download(NER_REPO, _TOKENIZER_FILE)
    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3  # stderr belongs to diagnostics, not ort chatter
    session = onnxruntime.InferenceSession(
        model_path, sess_options=options, providers=["CPUExecutionProvider"]
    )
    return NerEngine(session=session, tokenizer=Tokenizer.from_file(tokenizer_path))
