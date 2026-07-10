# Cost & reliability - spend and failure semantics

Load when: any run over ~100 items, anything paid, planning retries/failures,
or interpreting exit codes and receipts.
Parent: [SKILL.md](../../SKILL.md)

## What's free vs paid

| Free, always (no model) | Paid per item |
|---|---|
| `where` (predicates) · `summarize` · `sort` · `sample` (seeded) · `getschema` · `split` · `chart` · `schema` with braces/DSL · `--dry-run` (`map`/`extend`/`run` only) | `map` · `extend` · `filter` · `reduce` · judged `join` · `cluster` · `distinct` · `diff` · `outliers` |
| free RUNGS of paid verbs: `join --on 'left.k == right.k'` (key equality; also BLOCKS the judged join) · `distinct --exact` (hash dedupe) | `embed`/`top_k` (embedding-priced - much cheaper than judge calls) |

- The idiom: free cut → embed cut → judge the remnant.
  `smartpipe where 'text has "ERROR"' < app.log | smartpipe filter "a real outage"`
- `schema` with a plain-English description (no braces/DSL) is paid: 1 call + at most 1 repair.
- `summarize` is free KQL-style aggregation over record fields:
  `summarize 'count() by label'` · `'avg(score) by channel'` · `'count() by bin(ts, 1h)'` (bin works on ISO dates/datetimes).

## Belts

- `--max-calls N` on every exploratory run. Auto-chunked oversized items spend calls too - they count against it.
- `--model NAME` on any paid verb overrides the model for THAT run only - the cheapest cost control after not calling at all. Example: `smartpipe filter "spam" --model ollama/qwen3:0.6b --max-calls 20 < posts.txt` judges locally for free. Check what's available first: `smartpipe using`.
- The result cache is OFF by default. Turn it on once: `smartpipe config cache on`. After that, identical reruns are ~free and cache hits do NOT count against `--max-calls`. The cache keys on everything reply-affecting, including the model - switching models = cold cache.
- Receipt on stderr at run end (`note: run: ↑114 ↓56 tok`): real tokens/media spent. Trust it over estimates.
- `smartpipe usage` - free meter of model spend over time (hour/day/week/month/lifetime).

## Failure semantics

| Signal | Meaning | Your move |
|---|---|---|
| exit 0 | all good | proceed |
| exit 1 + stderr `skipped:` lines | partial; some items failed/refused | inspect skips; `--keep-invalid` captures them as rows |
| exit 2 | setup fault (keys, model missing, config) | `smartpipe doctor`; fix; rerun |
| exit 3 | run stopped: all items failing, or >50% failed the same way | read the first error; usually model/schema/capability |
| exit 64 | usage error (your command line) | the message includes the fix |
| exit 141 | downstream closed the pipe (e.g. `\| head`) - silent by design | not an error; expected with `head` |
| `provider looks down` screen | circuit breaker: 5 consecutive transport failures | rerun later - or configure `fallback-model` |

- `fallback-model` (config/env/flag): when the breaker trips, the run switches chat models wholesale, re-runs the failed window, and the receipt shows both models. There is no embedding fallback (vector spaces don't mix).
- Malformed model replies cost less than they used to: a free deterministic repair runs before the paid repair retry (automatic; disclosed as one stderr note).
- Oversized items: auto-chunked with a disclosed plan; `--whole` restores per-item refusal (see [ingestion](ingestion.md)).
- Capability gaps (model can't see/hear): converted via ladders when possible, else per-item skip naming the reason. `smartpipe doctor --probe` tests reality (4 tiny paid calls).
