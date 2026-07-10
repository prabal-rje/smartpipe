# sort - order records by a field

Order JSONL by a JSON field without the `jq -s 'sort_by(.x) | reverse | .[]'`
pipeline. **Free - never calls a model.** Reads the whole input
(sorting is inherently whole-set).

```bash
cat scored.ndjson \
| smartpipe sort --by __score --desc \
| head -5
… \
| smartpipe sort --by confidence --desc
# nested records: --by takes a field path (a literal "user.score" column wins first)
… \
| smartpipe sort --by user.score --desc
```

Semantics: numbers sort numerically and come before strings when a field
mixes types; a column whose every value is an ISO date/datetime (a
`{due date}` field, say) sorts temporally - mixed date/datetime columns
order correctly, offsets honored; rows **missing the field always land
last**, in both directions, with a note
(`sort: 12 rows missing 'confidence' placed last`); ties keep input order
(stable); rows pass through byte-for-byte.

There is deliberately no `take` verb - `head` already counts JSONL rows,
and duplicating coreutils would be unnecessary bloat.
