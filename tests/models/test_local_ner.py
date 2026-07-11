"""The local NER wire (wave G1): GLiNER span decoding, typed boundary, no downloads.

Every test injects a fake engine — CI never touches the network. The one live
test at the bottom runs only when the model is already in the HF cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import SetupFault
from smartpipe.engine.graphkg import EntitySpan
from smartpipe.models.local_ner import (
    MAX_SPAN_WIDTH,
    MAX_TEXT_WORDS,
    NER_REPO,
    GlinerEntityFinder,
    NerEngine,
    hf_implicit_token_env,
    ner_precision,
    span_grid,
    split_words,
    word_level_mask,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


# --- pure preprocessing ------------------------------------------------------


def test_split_words_offsets_and_hyphenated_tokens() -> None:
    words = split_words("account 4471-2209 closed.")
    assert words == (
        ("account", 0, 7),
        ("4471-2209", 8, 17),
        ("closed", 18, 24),
        (".", 24, 25),
    )


def test_split_words_empty_text() -> None:
    assert split_words("") == ()


def test_word_level_mask_numbers_text_words_and_zeroes_the_rest() -> None:
    # [CLS] <<ENT>> per|son <<SEP>> Bob slept [SEP] — prompt is 3 words
    word_ids = [None, 0, 1, 1, 2, 3, 4, None]
    assert word_level_mask(word_ids, prompt_words=3) == [0, 0, 0, 0, 0, 1, 2, 0]


def test_word_level_mask_marks_only_first_subtokens() -> None:
    word_ids = [None, 0, 1, 1, 1, None]
    assert word_level_mask(word_ids, prompt_words=1) == [0, 0, 1, 0, 0, 0]


def test_span_grid_enumerates_starts_by_width() -> None:
    spans, mask = span_grid(num_words=2, max_width=3)
    assert spans == [(0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (1, 3)]
    assert mask == [True, True, False, True, False, False]


# --- the finder with a fake engine --------------------------------------------


@dataclass(frozen=True, slots=True)
class FakeEncoding:
    ids: list[int]
    attention_mask: list[int]
    word_ids: list[int | None]


class FakeTokenizer:
    """One token per word, wrapped in [CLS]/[SEP] — the shape the wire expects."""

    def encode(
        self,
        sequence: list[str],
        pair: str | None = None,
        is_pretokenized: bool = False,
        add_special_tokens: bool = True,
    ) -> FakeEncoding:
        assert is_pretokenized
        count = len(sequence)
        return FakeEncoding(
            ids=[1, *range(10, 10 + count), 2],
            attention_mask=[1] * (count + 2),
            word_ids=[None, *range(count), None],
        )


def _as_list(value: object) -> Sequence[object]:
    """Unwrap a numpy array through its tolist() duck — the boundary pattern."""
    from smartpipe.core.jsontools import as_items

    lister = getattr(value, "tolist", None)
    assert callable(lister)
    plain: object = lister()
    items = as_items(plain)
    assert items is not None
    return items


def _first_row(value: object) -> Sequence[object]:
    from smartpipe.core.jsontools import as_items

    row = as_items(_as_list(value)[0])
    assert row is not None
    return row


class FakeSession:
    """Returns crafted logits: strong scores at the given (start, width, class)."""

    def __init__(self, hits: dict[tuple[int, int, int], float], num_classes: int) -> None:
        self.hits = hits
        self.num_classes = num_classes
        self.calls: list[dict[str, object]] = []

    def run(self, output_names: object, input_feed: Mapping[str, object]) -> Sequence[object]:
        self.calls.append(dict(input_feed))
        lengths: object = _first_row(input_feed["text_lengths"])[0]
        assert isinstance(lengths, int)
        num_words = lengths
        row = [[-10.0] * self.num_classes for _ in range(MAX_SPAN_WIDTH)]
        logits = [[[list(cells) for cells in row] for _ in range(num_words)]]
        for (start, width, cls), logit in self.hits.items():
            if start < num_words:
                logits[0][start][width][cls] = logit
        return [logits]


def _finder(
    hits: dict[tuple[int, int, int], float],
    labels: tuple[str, ...] = ("person", "company"),
    *,
    window_words: int = MAX_TEXT_WORDS,
) -> tuple[GlinerEntityFinder, FakeSession]:
    session = FakeSession(hits, num_classes=max(len(labels), 1))
    finder = GlinerEntityFinder(
        labels=labels,
        engine=NerEngine(session=session, tokenizer=FakeTokenizer()),
        window_words=window_words,
    )
    return finder, session


def test_find_maps_word_spans_back_to_char_offsets() -> None:
    # "Steve Jobs founded Apple" — words 0-1 = person, word 3 = company
    finder, _ = _finder({(0, 1, 0): 8.0, (3, 0, 1): 8.0})
    assert finder.find("Steve Jobs founded Apple") == (
        EntitySpan(name="Steve Jobs", label="person", start=0, end=10),
        EntitySpan(name="Apple", label="company", start=19, end=24),
    )


def test_find_feeds_the_prompt_and_masks_correctly() -> None:
    finder, session = _finder({})
    assert finder.find("Bob slept") == ()
    feed = session.calls[0]
    ids = _first_row(feed["input_ids"])
    # [CLS] + 5 prompt words (<<ENT>> person <<ENT>> company <<SEP>>) + 2 text words + [SEP]
    assert len(ids) == 1 + 5 + 2 + 1
    assert _first_row(feed["words_mask"]) == [0, 0, 0, 0, 0, 0, 1, 2, 0]
    assert _as_list(feed["text_lengths"]) == [[2]]
    assert str(getattr(feed["span_mask"], "dtype", "")) == "bool"


def test_find_greedy_keeps_the_higher_scoring_overlap() -> None:
    # (0,1) "Steve Jobs" at 0.9995 beats the overlapping (1,0) "Jobs"
    finder, _ = _finder({(0, 1, 0): 7.6, (1, 0, 0): 3.0})
    found = finder.find("Steve Jobs spoke")
    assert [span.name for span in found] == ["Steve Jobs"]


def test_find_drops_spans_hanging_past_the_text() -> None:
    finder, _ = _finder({(1, 4, 0): 8.0})  # words 1..5 of a 3-word text
    assert finder.find("Ada wrote code") == ()


def test_find_scores_below_threshold_stay_silent() -> None:
    finder, _ = _finder({(0, 0, 0): -1.0})  # sigmoid ≈ 0.27 < 0.5
    assert finder.find("Ada wrote code") == ()


def test_find_empty_text_makes_no_model_call() -> None:
    finder, session = _finder({(0, 0, 0): 8.0})
    assert finder.find("   ") == ()
    assert session.calls == []


def test_find_windows_long_texts_and_keeps_absolute_offsets() -> None:
    finder, session = _finder({(0, 0, 0): 8.0}, labels=("person",), window_words=2)
    text = "Ada met Bob today"
    found = finder.find(text)
    assert len(session.calls) == 2  # 4 words / window of 2
    assert found == (
        EntitySpan(name="Ada", label="person", start=0, end=3),
        EntitySpan(name="Bob", label="person", start=8, end=11),
    )


def test_finder_without_labels_finds_nothing() -> None:
    finder, session = _finder({(0, 0, 0): 8.0}, labels=())
    assert finder.find("Ada wrote code") == ()
    assert session.calls == []


# --- the precision dial (the 8 GB-machine pin) ---------------------------------


def test_default_precision_is_quantized_never_fp32_by_accident() -> None:
    assert ner_precision({}) == "q8"
    assert ner_precision({"SMARTPIPE_NER_PRECISION": ""}) == "q8"


def test_fp32_is_an_explicit_opt_out() -> None:
    assert ner_precision({"SMARTPIPE_NER_PRECISION": "fp32"}) == "fp32"
    assert ner_precision({"SMARTPIPE_NER_PRECISION": " FP32 "}) == "fp32"


def test_unknown_precision_is_loud() -> None:
    with pytest.raises(SetupFault, match="SMARTPIPE_NER_PRECISION"):
        ner_precision({"SMARTPIPE_NER_PRECISION": "int4"})


def test_finder_loads_the_precision_injected_by_the_composition_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import smartpipe.models.local_ner as local_ner

    expected = NerEngine(session=FakeSession({}, num_classes=1), tokenizer=FakeTokenizer())
    observed: list[str] = []

    def load(precision: str) -> NerEngine:
        observed.append(precision)
        return expected

    monkeypatch.setattr(local_ner, "_announced", True)
    monkeypatch.setattr(local_ner, "_load_engine", load)
    finder = GlinerEntityFinder(labels=("person",), precision="fp32")

    finder.find("Alice")
    assert observed == ["fp32"]


# --- the huggingface_hub warning knob (B5) -------------------------------------


def test_hf_implicit_token_env_disables_the_unauthenticated_warning() -> None:
    env: dict[str, str] = {}
    hf_implicit_token_env(env)
    assert env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"


def test_hf_implicit_token_env_never_overrides_a_configured_choice() -> None:
    env = {"HF_HUB_DISABLE_IMPLICIT_TOKEN": "0"}
    hf_implicit_token_env(env)
    assert env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "0"


# --- the live wire (owner-run; CI always skips) --------------------------------


def _weights_cached() -> bool:
    try:
        # absent on 3.14 (rides the fastembed marker) - collection must survive
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False

    return all(
        isinstance(try_to_load_from_cache(NER_REPO, filename), str)
        for filename in ("onnx/model_quantized.onnx", "tokenizer.json")
    )


@pytest.mark.skipif(not _weights_cached(), reason="gliner weights not cached — no downloads in CI")
def test_live_gliner_finds_named_entities() -> None:
    finder = GlinerEntityFinder(labels=("person", "company", "city"))
    found = finder.find("Steve Jobs founded Apple in Cupertino.")
    names = {(span.name, span.label) for span in found}
    assert ("Steve Jobs", "person") in names
    assert ("Apple", "company") in names
    assert ("Cupertino", "city") in names
