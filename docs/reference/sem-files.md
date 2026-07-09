# `.sem` files - saved pipe stages

A `.sem` file captures **exactly one verb invocation** in TOML. `smartpipe run
stage.sem` executes it; a shebang makes it directly executable. Composition
stays where it belongs - in the shell:

```bash
cat tickets.log \
| ./filter-urgent.sem \
| ./extract.sem > urgent.csv
```

## A worked pair

`filter-urgent.sem`:

```toml
#!/usr/bin/env -S smartpipe run
verb = "filter"
prompt = "describes an urgent, customer-impacting problem"
```

`extract.sem`:

```toml
#!/usr/bin/env -S smartpipe run
verb = "map"
prompt = "Extract {customer, product, severity enum(low, medium, high)}"
output = "csv"
fields = ["severity", "customer", "product"]
```

Make them executable once (`chmod +x *.sem`), and each behaves exactly like the
command it stands for - `stdin` in, `stdout` out, same exit codes, same behavior.

## The format

- The file is TOML. The shebang line is legal because `#` opens a TOML comment.
- `verb` is required: any pipeline verb - `map`, `extend`, `filter`, `where`,
  `embed`, `top_k`, `reduce`, `join`, `split`, `distinct`, `outliers`,
  `cluster`, `diff`, `summarize`, `sample`, `getschema`, `sort`, `chart`.
  (`run` and `config` are refused - no recursion, no config mutation from
  scripts.)
- Each verb accepts exactly the keys below - anything else is an error, and
  the error lists that verb's exact valid keys (the authoritative source).

The model verbs share these keys:

| Key | Type | Verbs | Becomes |
|---|---|---|---|
| `prompt` | string | map, extend, filter, reduce, join | the positional prompt |
| `prompt-file` | string | map, extend, filter, reduce, join | `--prompt-file`, resolved **relative to the `.sem` file** |
| `near` | string | top_k | `--near` |
| `k` | integer | top_k, join, cluster | top_k's positional K; `--k` elsewhere |
| `threshold` | number | top_k, join, distinct | `--threshold` |
| `not` | boolean | filter | `--not` |
| `right` | string | join, diff | `--right`, resolved **relative to the `.sem` file** |
| `model` | string | map, extend, filter, reduce, join, cluster, diff | `--model` |
| `embed-model` | string | embed, top_k, join, distinct, outliers, cluster, diff | `--embed-model` |
| `output` | string | map, extend, join | `--output` |
| `fields` | array of strings | map, extend, embed, top_k, reduce, join | `--fields` |
| `schema-file` | string | map, extend, reduce | `--schema`, resolved **relative to the `.sem` file** |
| `schema-from` | string | map, extend, reduce | `--schema-from` (the deterministic DSL) |
| `tally` | string | extend, reduce | `--tally FIELD` |
| `explode` | string | extend, reduce, cluster | `--explode FIELD` (one row per list element) |
| `group-by` | string | reduce | `--group-by` |
| `window` / `every` | integer | reduce | `--window` / `--every` |
| `verbose` | boolean | reduce | `--verbose` |
| `stream` | boolean | top_k | `--stream` |
| `top` | integer | cluster, diff | `--top` |
| `count` | integer | outliers, sample | the positional COUNT |
| `show-groups` | boolean | distinct | `--show-groups` |
| `all` | boolean | diff, getschema | `--all` |
| `allow-captions` | boolean | the media-converting verbs (filter, embed, top_k, reduce, join, distinct, outliers, cluster, diff) | `--allow-captions` |
| `concurrency` | integer | the model verbs | `--concurrency` |
| `max-calls` | integer | the model verbs | `--max-calls` |
| `in` | array of strings | the model verbs + split | repeated `--in` (globs resolve against the **current directory**, like the flag) |
| `from-files` | boolean | the model verbs + split | `--from-files` |

The free verbs take their natural keys: `where` takes `predicate`;
`summarize` takes `expression`; `sample` takes `count` and `seed`;
`getschema` takes `all`; `sort` takes `by` and `desc`; `chart` takes
`field`, `facet`, `by-time`, `top`, `save`, `title`; `split` takes `by`,
`media`, `max-tokens`.

## Unknown keys are errors - on purpose

`config.toml` ignores keys it doesn't know (a config must survive version
skew). A `.sem` script is the opposite trade: it runs unattended, so a typo'd
`promt` silently ignored would surface only later, in production. The
error names the key and lists the valid ones for that verb:

```bash
smartpipe run extract.sem
# → error: extract.sem: unknown key 'promt' - valid keys for map: concurrency, fields, from-files, in, max-calls, model, output, prompt, prompt-file, schema-file, schema-from
# →   A .sem script runs unattended - a typo silently ignored would be a disaster.
# →   Fix the key, then: smartpipe run extract.sem
```

## Precedence

**CLI flag > `.sem` value > environment > config file.** Anything you pass
after the script overrides the file:

```bash
smartpipe run extract.sem --model ollama/qwen3:8b     # wins over the file's model
```

## The shebang

`#!/usr/bin/env -S smartpipe run` needs `env -S` (GNU coreutils ≥ 8.30, any
modern macOS). If your platform lacks it, the spelled-out form works everywhere:

```bash
smartpipe run extract.sem < cards.txt
```

## Why one stage per file?

Multi-stage pipeline files were considered and rejected: they would re-implement
`|` - ordering, buffering, and error propagation the shell already does better.
One file = one stage keeps every `.sem` composable with everything else on your
system.


## Pipelines: several stages in one file

A `.sem` file can hold a whole pipeline as `[stage.NAME]` tables, run in
order - so the whole pipeline lives in one file you can review and version-control:

```toml
[stage.hot]
verb = "where"
predicate = 'text has "ERROR"'

[stage.themes]
verb = "cluster"
explode = "members"

[stage.picture]
verb = "chart"
field = "cluster"
top = 8
save = "themes.svg"
```

```bash
cat week.log \
| smartpipe run triage.sem
smartpipe run triage.sem --dry-run      # the graph + cost posture, zero calls
# → stage hot          where text has "ERROR"   [free]
# → stage themes       cluster --explode members   [model calls]
# → stage picture      chart cluster --top 8 --save themes.svg   [free]
```

Each stage reads the previous stage's output (`input = "name"` picks any
EARLIER stage instead); the first stage reads `stdin`, the last writes `stdout`.
Stage receipts on `stderr` carry their stage name (`[hot] where: 214 of 9,102
matched`). Stage keys are validated exactly like single-stage files - a typo
is a loud error, never silently ignored. Single-stage files are unchanged;
extra CLI flags apply to them only.
