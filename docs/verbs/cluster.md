# cluster - themes with sizes and quotes

Group items by meaning, label each group, size it, and pull the most
representative quotes. Common uses: a summary slide, grouping phishing lures, and
qualitative codebook in one verb.

```console
$ cat feedback.txt \
    | smartpipe cluster
cluster: ~612 embeddings + one label call per cluster (typically < 20)
{"cluster": "checkout fails on mobile", "size": 41, "share": 0.31, "examples": ["payment dies on iPhone", "…", "…"]}
{"cluster": "praise for dark mode", "size": 17, "share": 0.13, "examples": ["…"]}
```

The grouping threshold adapts to your corpus automatically, so there is no threshold to
tune - `--k` is the only shape control.

**Cost shape:** N embeddings plus one label call per
cluster - never N chat calls. The preview line prints before anything is
spent. Labels run at temperature 0, which keeps cluster names stable across most reruns of the same corpus (identical output is not guaranteed).

## Shaping the output

- `--top 8` shows the eight biggest and folds the tail into an
  `{"cluster": "(other)"}` row.
- `--k 5` forces exactly five clusters (smallest merge into their nearest).
- `--explode members` flips the output: one record per input item, original
  fields intact, plus `"cluster"` - ready for a spreadsheet, `chart cluster`,
  or a training file:

```console
$ cat snippets.txt \
    | smartpipe cluster --explode members > coded.jsonl
```

Without a chat model configured the clusters still form, just numbered
(`cluster 1`, …) with a note - the clusters are still usable; only the labels are missing.
