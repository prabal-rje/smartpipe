# summarize - the numbers, deterministically

Aggregate records in one pass. **Free - never calls a model.** A compact
grammar: `count()`, `avg()`, percentiles, and `by FIELD` grouping.

```bash
cat orders.jsonl \
| smartpipe summarize 'count(), avg(total), p95(total) by region'
# → {"region":"EU","count":812,"avg_total":74.2,"p95_total":189.0}
# → {"region":"US","count":310,"avg_total":61.8,"p95_total":140.5}
```

| Aggregation | Output name |
|---|---|
| `count()` | `count` |
| `sum(f)` `avg(f)` `min(f)` `max(f)` | `sum_f`, `avg_f`, … |
| `p50(f)` `p90(f)` `p95(f)` `p99(f)` | `p95_f`, … |
| `dcount(f)` (exact distinct count) | `dcount_f` |

Semantics worth knowing: groups sort largest first; a record missing the
`by` field groups under `null`, visibly; non-numeric values in numeric
aggregations are skipped and counted on `stderr` (the run continues); a group
with no numeric values reports `null`, not zero. Percentile aggregations
hold each group's values in memory - everything else streams.

## Time buckets

`by bin(ts, 1h)` groups by UTC time bucket (buckets: `1m` `5m` `15m` `1h` `6h` `1d`).
Limitation: timestamps parse as **ISO-8601 or epoch seconds/milliseconds
only** - anything else groups under `null` and any other format is a
preprocessing job for `jq`/`date`. Date-only values (a `{due date}` field)
bin as their UTC midnight, so `count() by bin(due, 1d)` just works. `chart --by-time ts:1h` draws the same
buckets chronologically, zero-filled (empty buckets are shown, not dropped).

The natural pairs: `map "…{label}" | summarize 'count() by label'` for the
numbers, `| chart label` for the picture.
