# Cost & reliability — spend and failure semantics

Load when: any run over ~100 items, anything paid, planning retries/failures,
or interpreting exit codes and receipts.
Parent: [SKILL.md](../../SKILL.md)

## What's free vs paid

| Free, always (no model) | Paid per item |
|---|---|
| `where` (predicates), `summarize` (count/avg/percentiles/bin), `sort`, `sample` (seeded), `getschema`, `split`, `chart`, `schema`, `--dry-run` | `map`, `extend`, `filter`, `reduce`, judged `join`, `cluster`, `distinct`, `diff`, `outliers` |
| free RUNGS of paid verbs: `join --on 'left.k == right.k'` (key equality; also BLOCKS the judged join), `distinct --exact` (hash dedupe) | `embed`/`top_k` (embedding-priced, cheaper than judge calls) |

The idiom: free cut → embed cut → judge remnant.
`where 'text has "ERROR"' | filter "a real outage"`.

## Belts

- `--max-calls N` on every exploratory run. Cache hits DON'T count against it.
- Identical reruns are ~free (result cache keys on everything reply-affecting,
  including the model — switching models = cold cache).
- Receipt on stderr at run end: real tokens/media spent. Trust it over estimates.

## Failure semantics

| Signal | Meaning | Your move |
|---|---|---|
| exit 0 | all good | proceed |
| exit 1 + stderr `skipped:` lines | partial; some items failed/refused | inspect skips; `--keep-invalid` captures them as rows |
| exit 2 | setup fault (keys, model missing, config) | `smartpipe doctor`; fix; rerun is cheap (cache) |
| exit 3 | all items failed | read the first error; usually model/schema/capability |
| exit 64 | usage error (your command) | the message includes the fix |
| `provider looks down` screen | circuit breaker: 5 consecutive transport failures | rerun later (cache covers survivors) — or configure `fallback-model` |

- `fallback-model` (config/env/flag): when the breaker trips, the run switches
  chat models wholesale, re-runs the failed window, receipt shows both models.
  Embedding fallback does not exist (vector spaces don't mix).
- Oversized items: auto-chunked with a disclosed plan; chunk calls count
  against `--max-calls`; `--whole` restores per-item refusal (see
  [ingestion](ingestion.md)).
- Capability gaps (model can't see/hear): converted via ladders when possible,
  else per-item skip naming the reason. `smartpipe doctor --probe` tests
  reality (4 tiny paid calls).
