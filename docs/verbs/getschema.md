# getschema — what's in this stream

Everyone's first 30 seconds with a new file: which fields, what types, how
complete. **Free — never calls a model.** The KQL name, kept on purpose.

```console
$ cat data.jsonl | smartpipe getschema
field      type            coverage  example
id         integer|string  100%      1
sentiment  string          67%       "neg"
tags       array           33%       ["a"]
try: smartpipe chart id · smartpipe where 'id …'
```

A table on a terminal, NDJSON records when piped. Mixed types show as
unions (`integer|string`) — that's exactly the dirt worth seeing before a
pipeline runs. Coverage counts non-null presence. Scans the first 10,000
rows by default (a note says so); `--all` scans everything — state is
per-field, so memory stays flat.

Plain-text input gets a one-line answer instead of an error:
`plain text lines (no fields) — 4,112 lines · median 84 chars`.

The footer suggests the loop's next move: `chart` the best-covered field,
or a `where` over it.
