# distinct — fold near-duplicates

The same thing worded differently is one item. `distinct` keeps the first
occurrence of each meaning and folds the rest — exact duplicates for free
(hashing, zero model calls), near-duplicates by embedding.

```console
$ cat tickets.txt | sempipe distinct > unique.txt
distinct: kept 412 of 1,208 (573 exact + 223 near duplicates folded)
```

Output preserves input order and bytes (passthrough). First occurrence wins,
so re-runs keep the same representatives.

## Audit before you trust

```console
$ cat alerts.jsonl | sempipe distinct --show-groups | head -1
{"kept": "app crashes when saving", "count": 3, "duplicates": ["saving crashes the app!!", "crash on save"]}
```

`--show-groups` emits one record per kept item with everything that folded
into it — read a few groups once, then trust the receipt.

## The knob, and why it exists

`--threshold` (default 0.90) is the cosine similarity at which two items
count as the same thing. Corpora genuinely differ: short alert strings sit
closer together than long reviews. If the default folds too eagerly, raise
it (0.95); too timidly, lower it (0.85) — and check with `--show-groups`.

## Why bother

- **Training data (the big one):** near-duplicate contamination measurably
  hurts models; `sempipe distinct < candidates.jsonl > train-clean.jsonl` is
  the cheapest data-quality win available, and the receipt is the number for
  your dataset card.
- **Cost:** every folded duplicate is a model call you don't pay for in the
  `map`/`filter` stages downstream.
- Items that fail to embed are **kept** and disclosed (`kept unexamined:`) —
  distinct never silently drops what it couldn't compare.
