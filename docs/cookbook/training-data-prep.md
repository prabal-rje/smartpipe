# Training-data prep, end to end

The loop a model trainer or dataset curator actually runs, with the receipts
that go in the dataset card. Every stage streams NDJSON to the next.

## 1. Know what you're holding

```console
$ smartpipe getschema --all < raw.jsonl
```

## 2. Decontaminate

Near-duplicates measurably hurt models; fold them first, and keep the
receipt for the card:

```console
$ smartpipe distinct < raw.jsonl > deduped.jsonl
distinct: kept 412,308 of 1,204,551 (573,001 exact + 219,242 near duplicates folded)
```

Audit a few folds once with `--show-groups` before trusting.

## 3. Gate for free, then judge

```console
$ smartpipe where 'lang == "en"' < deduped.jsonl \
    | smartpipe extend "Add {quality number: 0 to 1, refusal boolean}" \
    | smartpipe where 'quality >= 0.7 and refusal == false' > candidates.jsonl
```

`where` costs nothing - put every deterministic gate before the paid judge.
Use enum-typed extractions for anything you'll group later
(`{label enum(code, prose, math)}`), or the groups fragment.

## 4. Split reproducibly

```console
$ smartpipe sample 5000 --seed 42 < candidates.jsonl > eval.jsonl
sample: 5,000 of 402,118 (seed 42)
```

Seeded by default - the split is citable and survives re-runs.

## 5. Balance tables and drift checks

```console
$ smartpipe summarize 'count() by source, lang' < candidates.jsonl
$ smartpipe chart --facet lang,domain --save balance.svg < candidates.jsonl
$ smartpipe diff --right v1-train.jsonl < candidates.jsonl     # drift BEFORE the GPU bill
```

## Cost discipline for big runs

- `sample 20` while iterating on prompts; `--max-calls` as the belt on
  every paid stage.
- Turn on the cache (`smartpipe config cache on`): identical calls are free
  on re-run, which also makes an interrupted run resume cheaply.
- Watch the status bar: live token and media totals; the final
  `run: … tokens` receipt is the number for the training report.
- Whole-set verbs (`distinct`, `cluster`, `outliers`, `sort`) hold the
  corpus in memory - at tens of millions of rows, shard first
  (`split -l 1000000`), run per shard, then re-run `distinct` over the
  survivors.

## Make it the team's verb

Save the whole loop as `~/.config/smartpipe/verbs/prep.sem` ([custom
verbs](../reference/custom-verbs.md)) and the weekly run becomes
`cat raw.jsonl | smartpipe prep`.
