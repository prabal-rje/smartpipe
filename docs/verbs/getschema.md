# getschema - what's in this stream

getschema reports which fields a stream has, their types, and how complete each is: **Free - never calls a model.**

```bash
cat data.jsonl \
| smartpipe getschema
# → field      type            coverage  example
# → id         integer|string  100%      1
# → sentiment  string          67%       "neg"
# → tags       array           33%       ["a"]
# → try: smartpipe chart id · smartpipe where 'id …'
```

A table on a terminal, JSONL records when piped. Mixed types show as
unions (`integer|string`) - that's the kind of inconsistency worth catching before a
pipeline runs. Coverage counts non-null presence. Scans the first 10,000
rows by default (a note says so); `--all` scans everything without loading it all into memory.

Plain-text input gets a one-line answer instead of an error:
`plain text lines (no fields) - 4,112 lines · median 84 chars`.

The footer suggests the loop's next move: `chart` the best-covered field,
or a `where` over it.
