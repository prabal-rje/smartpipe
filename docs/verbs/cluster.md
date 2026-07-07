# cluster — themes with sizes and quotes

Group items by meaning, label each group, size it, and pull the most
representative quotes. The Monday slide, the phishing lure families, and the
qualitative codebook in one verb.

```console
$ cat feedback.txt | smartpipe cluster
cluster: ~612 embeddings + one label call per cluster (typically < 20)
{"cluster": "checkout fails on mobile", "size": 41, "share": 0.31, "examples": ["payment dies on iPhone", "…", "…"]}
{"cluster": "praise for dark mode", "size": 17, "share": 0.13, "examples": ["…"]}
```

The grouping threshold adapts to your embedding model's geometry (derived
from the corpus's own similarity distribution), so there is no threshold to
tune — `--k` is the only shape control.

**The cost shape is the point:** N embeddings plus one label call per
cluster — never N chat calls. The preview line prints before anything is
spent. Labels run at temperature 0, so re-running the same corpus names the
same clusters: your slide doesn't change under you.

## Shaping the output

- `--top 8` shows the eight biggest and folds the tail into an honest
  `{"cluster": "(other)"}` row.
- `--k 5` forces exactly five clusters (smallest merge into their nearest).
- `--explode members` flips the output: one record per input item, original
  fields intact, plus `"cluster"` — ready for a spreadsheet, `chart cluster`,
  or a training file:

```console
$ cat snippets.txt | smartpipe cluster --explode members > coded.jsonl
```

Without a chat model configured the clusters still form, just numbered
(`cluster 1`, …) with a note — the math is the value, the names are sugar.
