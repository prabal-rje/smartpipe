# sort — order records by a field

Order NDJSON by a JSON field without the `jq -s 'sort_by(.x) | reverse | .[]'`
incantation. **Free — never calls a model.** Reads the whole input
(sorting is inherently whole-set).

```console
$ cat scored.ndjson | sempipe sort --by _score --desc | head -5
$ … | sempipe sort --by confidence --desc
```

Semantics: numbers sort numerically and come before strings when a field
mixes types; rows **missing the field always land last**, in both
directions, with a note (`sort: 12 rows missing 'confidence' placed last`);
ties keep input order (stable); rows pass through byte-for-byte.

There is deliberately no `take` verb — `head` already counts NDJSON rows,
and duplicating coreutils is the bloat line this tool holds.
