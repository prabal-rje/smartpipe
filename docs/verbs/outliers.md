# outliers - the items least like the rest

`top_k`'s mirror: instead of "nearest to my query", **farthest from
everything**. Novelty, surfaced - the failure shape you haven't seen, the
mislabeled training row, the alert that isn't like the others.

```bash
cat today.log \
| smartpipe outliers 5
# → outliers: median neighbor distance 0.21 - these are 3.1x-3.9x out
# → {"text": "kernel: watchdog: BUG: soft lockup CPU#3", "__distance": 0.81, "source": "line 48122"}
```

Embeddings only - no chat calls. The score is each item's mean cosine
distance to its nearest neighbors (robust when the corpus has several normal
clusters); the `stderr` line anchors it against the corpus median so the
distance has context.

Output records mirror `top_k`'s shape (`__distance` where `top_k` has
`__score`; original fields survive for JSON rows, `{"text": …}` for plain
lines).

Small print: needs at least 3 items; rows that fail to embed are excluded
with a warning (items that fail to embed can't be scored).

Typical loops: `smartpipe where 'level has "error"' | smartpipe outliers 5`
(triage novel failures), `smartpipe outliers 20 < train.jsonl` (hunt label
noise before a training run).
