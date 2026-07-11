# sample - the same N random rows, every run

Keep N random rows. **Free - never calls a model.** Without `--by`, reservoir
sampling is one pass with O(N) memory; output order is preserved.

```bash
cat huge.jsonl \
| smartpipe sample 20 \
| smartpipe map "Extract {label}" --tally label
cat evals.jsonl \
| smartpipe sample 50 --seed 7 > eval-subset.jsonl
# → sample: 50 of 12,408 (seed 7)
```

## Deterministic by default

The seed defaults to `0`, so the same input gives the **same sample with no
flags**. That's the property the iteration loop needs: tweak the prompt, run
against the same 20 rows, and the comparison compares prompts, not samples.
It's also what makes a sample *citable* - a training report or methods
section can say "seed 0" and rerunning with that seed on the same input
selects the same rows. `--seed K` picks a different sample; there is no
random-each-run mode.

## Stratified sampling: --by FIELD

`--by` keeps a field's class balance in the sample - each value contributes
rows in proportion to its share of the input:

```bash
smartpipe sample 10 --by label < labeled.jsonl
# → sample: 10 of 1,000 (seed 0, 3 strata by 'label')
```

Exact stratification spools rows to an owned temporary SQLite store, counts
typed strata, then holds only the final N selected payloads in memory. The
store is removed on success, failure, or interruption, so a million unique
values cannot turn RAM into O(input). Proportional allocation uses
largest-remainder rounding, so the total is **exactly N** and a 70/20/10
corpus yields a 7/2/1 sample instead of whatever a plain random draw happens
to hit. Equal remainders use a seed-derived stable tie order rather than
first-seen order. Values are type-tagged (`1`, `1.0`, `true`, and `"1"` are
different strata). Deterministic under the same seed semantics; output keeps input
order. Rows missing the field (plain text rows included) sample as their own
`null` stratum - the same null-group convention as `summarize` - and one
stderr note counts them. Proportional means proportional: a value too rare
for a slot at your N gets zero rows (raise N if every class must appear).

## sample vs --max-calls

`--max-calls` truncates the head of the stream - a cap on runaway
spend, but a head isn't representative when the input is ordered (sorted exports, time-ordered
logs). `sample` is the representative gate; use both:

```bash
cat huge.jsonl \
| smartpipe sample 20 \
| smartpipe map "…" --max-calls 25
```
