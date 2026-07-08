# Training-data prep, end to end

The loop a model trainer or dataset curator runs, with the receipts
that go in the dataset card. Every stage streams JSONL to the next.

## 1. Know what you're holding

```bash
smartpipe getschema --all < raw.jsonl
```

## 2. Decontaminate

Near-duplicates degrade training; fold them first, and keep the
receipt for the card:

```bash
smartpipe distinct < raw.jsonl > deduped.jsonl
# → distinct: kept 412,308 of 1,204,551 (573,001 exact + 219,242 near duplicates folded)
```

Audit a few folds once with `--show-groups` before trusting.

## 3. Gate for free, then judge

```bash
smartpipe where 'lang == "en"' < deduped.jsonl \
| smartpipe extend "Add {quality number: 0 to 1, refusal boolean}" \
| smartpipe where 'quality >= 0.7 and refusal == false' > candidates.jsonl
```

`where` runs locally with no model calls - put every deterministic gate before the paid judge.
Use enum-typed extractions for anything you'll group later
(`{label enum(code, prose, math)}`), or the `groups` fragment.

## 4. Split reproducibly

```bash
smartpipe sample 5000 --seed 42 < candidates.jsonl > eval.jsonl
# → sample: 5,000 of 402,118 (seed 42)
```

Seeded by default, so the same seed over the same input reproduces the split and you can cite it.

## 5. Balance tables and drift checks

```bash
smartpipe summarize 'count() by source, lang' < candidates.jsonl
smartpipe chart --facet lang,domain --save balance.svg < candidates.jsonl
smartpipe diff --right v1-train.jsonl < candidates.jsonl     # drift BEFORE the GPU bill
```

## 6. Label at scale: the 60,000-row ritual

Hitting enter on a five-figure labeling run should be a verified ritual, not a
leap. Rehearse on the *same* seeded rows every time - seed 0 is the default,
so every prompt tweak is compared on identical input - and let `getschema`
prove the output contract for free:

```bash
# Rehearse the labeler on the same seeded 20 rows every time; getschema
# proves the output contract before a dollar is spent at scale
smartpipe sample 20 < posts.jsonl \
| smartpipe extend "Add {label enum(spam, promo, genuine), confidence number: 0 to 1}" \
| smartpipe getschema
```

When the schema reads back exactly as designed, run the real thing with a live
class tally on stderr and a hard spend ceiling:

```bash
# The real run: live class tally on stderr, hard spend ceiling, only confident labels ship
smartpipe extend "Add {label enum(spam, promo, genuine), confidence number: 0 to 1}" --tally label --max-calls 60000 < posts.jsonl \
| smartpipe where 'confidence >= 0.8' > labeled.jsonl
```

`--tally` reports class balance while the run flows (a skewed tally is your
early abort signal), and `--max-calls` means the run can never surprise the
credit card.

## 7. Sweep eval for paraphrase contamination

Exact-match and MinHash dedupe cannot see a paraphrased eval question hiding
in the training set. `join`'s embed-block-judge shape does the million-pair
search at `eval-rows × k` cost, and one run emits both deliverables:

```bash
# Contamination sweep: eval on the left streams, train.jsonl is indexed once;
# cost is eval-rows x 10 judge calls, never eval x train
cat eval-v4.jsonl \
| smartpipe join "eval question {left.prompt} and training example {right.text} ask the same question" --right train.jsonl --k 10 --unmatched eval-clean.jsonl \
| smartpipe sort --by __score --desc > contamination-pairs.jsonl
```

`contamination-pairs.jsonl` names the exact leaked training rows for
scrubbing; `--unmatched` hands back the clean eval set in the same pass.

## 8. After the fine-tune: ship or debug

The overnight run finished; the same verbs answer "did it work". One judge
call per row writes typed scores onto the row in place:

```bash
# One judge call per row writes typed scores onto the row in place; with the
# cache enabled, identical reruns are free (cache is off by default)
cat ckpt-1400-outputs.jsonl \
| SMARTPIPE_CACHE=1 smartpipe extend "Judge how well the response answers the prompt. Add {correctness number: 0 to 1, refusal boolean, failure_mode enum(none, hallucination, format_error, refusal, off_topic)}" \
> judged-1400.jsonl
```

```bash
# The standup table: counts and mean score per failure mode - free, no model calls
smartpipe summarize 'count(), avg(correctness) by failure_mode' < judged-1400.jsonl
```

Score dipped? Ask what this checkpoint started doing differently - themes
with measured shares, not vibes:

```bash
smartpipe diff --right ckpt-1200-outputs.jsonl --top 8 < ckpt-1400-outputs.jsonl
```

## Cost discipline for big runs

- `sample 20` while iterating on prompts; `--max-calls` as a hard cap on
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
