# distinct - fold near-duplicates

The same thing worded differently is one item. `distinct` keeps the first
occurrence of each meaning and folds the rest - exact duplicates for free
(hashing, zero model calls), near-duplicates by embedding.

```bash
cat tickets.txt \
| smartpipe distinct > unique.txt
# → distinct: kept 412 of 1,208 (573 exact + 223 near duplicates folded)
```

Output preserves input order and bytes (passthrough). First occurrence wins,
so the first occurrence in each group is the one kept.

## Audit before you trust

```bash
cat alerts.jsonl \
| smartpipe distinct --show-groups \
| head -1
# → {"kept": "app crashes when saving", "count": 3, "duplicates": ["saving crashes the app!!", "crash on save"]}
```

`--show-groups` emits one record per kept item with everything that folded
into it - read a few groups once, then rely on the group counts.

## The knob, and why it exists

`--threshold` (default 0.90) is the cosine similarity at which two items
count as the same thing. Corpora genuinely differ: short alert strings sit
closer together than long reviews. If the default folds too eagerly, raise
it (0.95); too timidly, lower it (0.85) - and check with `--show-groups`.

## Image corpora, natively

With a media-native embedder, image items are compared as IMAGES, not as
captions of images:

```bash
export JINA_API_KEY=…
cat images.jsonl \
| smartpipe distinct --embed-model jina/jina-clip-v2
# → note: media embedded natively (jina/jina-clip-v2) - no captions
```

Mentioning the media embedder is the whole switch - there is no second
flag. Without it, images dedupe through the caption pivot (weaker, and the
note says which path ran). Audio and video still pivot either way.

## Why bother

- **Training data:** near-duplicate contamination
  hurts models; `smartpipe distinct < candidates.jsonl > train-clean.jsonl` is
  a cheap data-quality win, and the receipt is the number for
  your dataset card.
- **Cost:** every folded duplicate is a model call you don't pay for in the
  `map`/`filter` stages downstream.
- Items that fail to embed are **kept** and disclosed (`kept unexamined:`) -
  distinct never silently drops what it couldn't compare.

## Scanned corpora

With an [`ocr-model`](../concepts/models-and-providers.md#the-ocr-model-role) configured, ingested PDFs and images parse
through it at ingestion - one item per page, disclosed per row, `--ocr-model`
overrides per run. That includes `--exact`: the hash rung stays free, but the
parse itself spends (cap it with `--max-calls`). Unset, nothing changes.
