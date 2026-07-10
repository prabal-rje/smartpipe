# 6 · Pipelines that last

A pipeline that worked once wants to run every week. This chapter turns
one-liners into artifacts.

## Save it: .sem files

A `.sem` file pins one verb invocation (or a multi-stage pipeline) as TOML:

```toml
#!/usr/bin/env -S smartpipe run
verb = "map"
prompt = "Extract {vendor, total}"
schema-from = "vendor string; total number >= 0"
```

`smartpipe run extract.sem` executes it; unknown keys are loud errors, so a
typo can't silently change an unattended run. Drop it in
`~/.config/smartpipe/verbs/` and it becomes a verb: `smartpipe extract`.
Details: [.sem files](../reference/sem-files.md) ·
[custom verbs](../reference/custom-verbs.md).

## Land the results: write and readable

```bash
# translate a folder, mirror it back under fr/
smartpipe 'notes/*.txt' --as lines \
| smartpipe map "translate to French" \
| smartpipe write 'fr/{name}'
# {name} = the source file's name, carried by __source - notes/a.txt becomes fr/a.txt

# a human-readable report of a JSONL run
smartpipe readable < results.jsonl > report.txt
```

`write` routes items to files by template. `{name}`, `{stem}`, `{ext}`,
`{path}`, and `{index}` fill from the item's provenance (the `__source`
spine that remembers which file each item was cut from - `{name}` is NOT a
record field); any other `{field}` fills from the record's own data, for
fan-out like `by-lang/{lang}.jsonl`. Line-cut items reassemble in order.
`readable` renders the same blocks you see at the terminal, anywhere.

## Trust it: doctor and the receipts

```bash
smartpipe doctor          # config, models, keys, completions - with fixes
smartpipe doctor --probe  # what your models can actually see and hear
smartpipe usage           # the run ledger: tokens and media over time
```

Every run already ends with a receipt on stderr (tokens, media, cache hits) -
the numbers that go in the report.

That's the track. From here: the [cookbook](../cookbook/README.md) for
task-shaped recipes, [concepts](../concepts/the-item.md) for how it works,
and the [CLI reference](../reference/cli.md) for every flag.
