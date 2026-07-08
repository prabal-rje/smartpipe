# Live stream enrichment with `join`

Tag a live event stream with the catalog rows it concerns - as it happens.

## The setup

`customers.jsonl` - the finite side, indexed once at startup:

```json
{"name": "Acme Corp", "tier": "enterprise", "owner": "dana"}
{"name": "Blue Bakery", "tier": "starter", "owner": "sam"}
```

## The pipe

```console
$ tail -f support-events.log \
    | smartpipe join "this event {left.text} involves the customer {right.name}" --right customers.jsonl --k 3 \
    | jq -r 'select(.right.tier == "enterprise") | "\(.right.owner): \(.left.text)"'
```

Every arriving line is embedded, narrowed to its 3 nearest customers, and only
those pairs are judged - then `jq` routes enterprise matches to their owner.
First Ctrl-C drains in-flight judges and exits with the run's true outcome code.

## Cost control

- The `stderr` preview tells you the per-line worst case up front
  (`join: up to 3 model calls per input line`).
- For long sessions, add a cap: `--max-calls 500` stops intake at the cap and
  drains gracefully.
- `--threshold 0.6` skips judging candidates that aren't even close.

## Save it as a stage

```toml
#!/usr/bin/env -S smartpipe run
verb = "join"
prompt = "this event {left.text} involves the customer {right.name}"
right = "customers.jsonl"
k = 3
max-calls = 500
```

`chmod +x enrich.sem` and the whole thing becomes `tail -f … | ./enrich.sem`.
(`right` resolves next to the `.sem` file - the stage and its catalog travel
together.)
