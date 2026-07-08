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
