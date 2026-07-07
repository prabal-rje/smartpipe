# extend - your record, plus columns

Add extracted fields to each record. Everything the record already had
survives; the new fields land beside it. This is `map` for people who own a
*dataset* rather than a question - results flow straight back into the
pipeline that produced them.

```console
$ head -1 tickets.jsonl
{"id": 812, "customer": "acme", "body": "app crashes when saving"}
$ cat tickets.jsonl \
    | smartpipe extend "Add {sentiment enum(pos, neg, neutral), product string}"
{"id": 812, "customer": "acme", "body": "app crashes when saving", "sentiment": "neg", "product": "app"}
```

Same prompt language as [`map`](map.md): typed braces, `--schema`,
`--schema-from`, plus `--tally`, `--explode`, `--fields`, `--max-calls`, and
the [video frame controls](map.md#video-frame-control)
(`--frame-every`, `--max-frames`).

## Semantics worth knowing

- **Plain text lines** become records: `{"text": <the line>, …new fields}` -
  the on-ramp from text files to structured pipelines.
- **Collisions**: an extracted field with an existing name **overwrites** it
  (re-running enrichment stays idempotent), disclosed once per field on
  stderr: `note: overwriting 'sentiment' on incoming records`.
- **`--explode FIELD`** emits one row per list element with the *merged*
  record's other fields copied onto every row - provenance rides along.
- **Media records** (from `split`): the base64 transport fields are dropped
  from the output (the model consumed them); `source` and other metadata
  survive.
- `extend` requires named fields (braces or a schema): a plain prompt is a
  usage error pointing at `map`.

Typical loops: feature columns for a model (`extend "Add {complaint_type
enum(billing, product, support), urgency number: 0 to 1}"`), judge scores
onto training rows (`extend "Add {quality number: 0 to 1, refusal boolean}"`
then `where 'quality >= 0.7'`), IOC extraction onto alerts.
