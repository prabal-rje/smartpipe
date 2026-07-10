# 5 · Scale and cost

The first four chapters worked on samples. This one is about the 100,000-row
night run: capping spend, surviving failures, and rerunning for free.

## Belts before the run

```bash
smartpipe map "Extract {label}" --max-calls 500 < big.jsonl   # hard spend ceiling
smartpipe map "Extract {label}" --dry-run < big.jsonl          # see the request, spend nothing
SMARTPIPE_CACHE=1 smartpipe map "Extract {label}" < big.jsonl  # identical calls are free
```

`--max-calls` stops intake when the budget is spent and drains what's in
flight; a capped run never exits 0, so scripts notice. The cache keys on
model + request, so a rerun after a crash (or a prompt that didn't change)
replays answers without paying twice.

## Batching: small items share one call

Small items don't each deserve their own HTTP call. By default, `map`,
`extend`, and `filter` collect items for a moment (about 75 ms, or until 12
are waiting) and send them as ONE request - each item in its own labeled
`<input id="r1">` block, answered by one JSON object keyed per item. You do
nothing; the run just costs less, and one stderr note discloses it:

```text
note: batched 500 items into 42 calls
```

What never batches: items carrying media (images, audio, video), oversized
items that need chunking, repair retries, and every other verb - those take
the same solo path as before. If an answer comes back missing or invalid for
some item, that item alone is retried as a normal solo call (with the usual
repair ladder); the rest of the batch keeps its answers.

Accounting stays honest: `--max-calls` counts real calls, so a batch of 12
items is 1 call - the cap stretches further. The cache still works per item:
cached items never enter a batch, and batched answers are cached individually
for later runs.

Turn it off with `smartpipe config batching off` (or per run:
`SMARTPIPE_BATCH=off`). `SMARTPIPE_BATCH_SIZE` and `SMARTPIPE_BATCH_WINDOW_MS`
tune the group size and the wait.

## Failures are rows, not mysteries

One bad item skips with a warning; the run continues; exit 1 says "partial".
For dataset work, keep the failures instead:

```bash
smartpipe map "Extract {v}" --keep-invalid < rows.jsonl > out.jsonl
smartpipe where '__invalid == true' < out.jsonl > failures.jsonl
```

Each failure becomes `{"__invalid": true, "__error": …, "__raw": …}` - a
machine-readable set you can inspect, count, or rerun through a different
model explicitly.

## When the provider goes down

Five consecutive transport failures (timeouts, 5xx) trip the circuit breaker:
the run stops early with a "provider looks down" screen instead of failing
the rest one by one. Work already done is safe, and rerunning is cheap with
the cache on. Tune with `SMARTPIPE_BREAKER` (0 disables).

Configure a fallback and the run doesn't stop at all:

```bash
smartpipe map "Extract {v}" --fallback-model gpt-5.4-mini < big.jsonl
```

At the threshold, smartpipe switches models wholesale, re-runs the failed
window on the fallback, and the end receipt shows how many answers came from
each model.

## Throughput

`--concurrency N` (default 4) sets parallel model calls. Order is preserved
regardless - outcomes emit in input order, always.

Next: [6 · Pipelines that last](6-pipelines-that-last.md) - saving, wiring,
and shipping what you built.
