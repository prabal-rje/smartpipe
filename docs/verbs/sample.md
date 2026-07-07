# sample - the same N random rows, every run

Keep N random rows. **Free - never calls a model.** Reservoir sampling: one
pass, constant memory, input order preserved in the output.

```console
$ cat huge.jsonl \
    | smartpipe sample 20 \
    | smartpipe map "Extract {label}" --tally label
$ cat evals.jsonl \
    | smartpipe sample 50 --seed 7 > eval-subset.jsonl
sample: 50 of 12,408 (seed 7)
```

## Deterministic by default

The seed defaults to `0`, so the same input gives the **same sample with no
flags**. That's the property the iteration loop needs: tweak the prompt, run
against the same 20 rows, and the comparison compares prompts, not samples.
It's also what makes a sample *citable* - a training report or methods
section can say "seed 0" and anyone can reproduce it. `--seed K` picks a
different (still reproducible) sample; there is deliberately no
random-each-run mode.

## sample vs --max-calls

`--max-calls` truncates the head of the stream - a belt against runaway
spend, but heads are never representative (sorted exports, time-ordered
logs). `sample` is the representative gate; use both:

```console
$ cat huge.jsonl \
    | smartpipe sample 20 \
    | smartpipe map "…" --max-calls 25
```
