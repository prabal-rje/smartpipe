# `.sem` files — saved pipe stages

A `.sem` file captures **exactly one verb invocation** in TOML. `sempipe run
stage.sem` executes it; a shebang makes it directly executable. Composition
stays where it belongs — in the shell:

```console
$ cat tickets.log | ./filter-urgent.sem | ./extract.sem > urgent.csv
```

## A worked pair

`filter-urgent.sem`:

```toml
#!/usr/bin/env -S sempipe run
verb = "filter"
prompt = "describes an urgent, customer-impacting problem"
```

`extract.sem`:

```toml
#!/usr/bin/env -S sempipe run
verb = "map"
prompt = "Extract {customer, product, severity}"
output = "csv"
fields = ["severity", "customer", "product"]
```

Make them executable once (`chmod +x *.sem`), and each behaves exactly like the
command it stands for — stdin in, stdout out, same exit codes, same everything.

## The format

- The file is TOML. The shebang line is legal because `#` opens a TOML comment.
- `verb` is required: one of `map`, `filter`, `embed`, `top_k`, `reduce`.
  (`run` and `config` are refused — no recursion, no config mutation from
  scripts.)
- Each verb accepts exactly the keys below — anything else is an error.

| Key | Type | Verbs | Becomes |
|---|---|---|---|
| `prompt` | string | map, filter, reduce | the positional prompt |
| `near` | string | top_k | `--near` |
| `k` | integer | top_k | the positional K |
| `threshold` | number | top_k | `--threshold` |
| `not` | boolean | filter | `--not` |
| `model` | string | map, filter, reduce | `--model` |
| `embed-model` | string | embed, top_k | `--embed-model` |
| `output` | string | map | `--output` |
| `fields` | array of strings | map, embed, top_k, reduce | `--fields` |
| `schema-file` | string | map, reduce | `--schema`, resolved **relative to the `.sem` file** |
| `group-by` | string | reduce | `--group-by` |
| `window` / `every` | integer | reduce | `--window` / `--every` |
| `verbose` | boolean | reduce | `--verbose` |
| `stream` | boolean | top_k | `--stream` |
| `concurrency` | integer | all | `--concurrency` |
| `max-calls` | integer | all | `--max-calls` |
| `in` | array of strings | all | repeated `--in` (globs resolve against the **current directory**, like the flag) |
| `from-files` | boolean | all | `--from-files` |

## Unknown keys are errors — on purpose

`config.toml` ignores keys it doesn't know (a config must survive version
skew). A `.sem` script is the opposite trade: it runs unattended, so a typo'd
`promt` silently ignored would be a disaster discovered in production. The
error names the key and lists the valid ones for that verb:

```console
$ sempipe run extract.sem
error: extract.sem: unknown key 'promt' — valid keys for map: concurrency, fields, from-files, in, model, output, prompt, schema-file
  A .sem script runs unattended — a typo silently ignored would be a disaster.
  Fix the key, then: sempipe run extract.sem
```

## Precedence

**CLI flag > `.sem` value > environment > config file.** Anything you pass
after the script overrides the file:

```console
$ sempipe run extract.sem --model ollama/qwen3:8b     # wins over the file's model
```

## The shebang

`#!/usr/bin/env -S sempipe run` needs `env -S` (GNU coreutils ≥ 8.30, any
modern macOS). If your platform lacks it, the spelled-out form always works:

```console
$ sempipe run extract.sem < cards.txt
```

## Why one stage per file?

Multi-stage pipeline files were considered and rejected: they would re-implement
`|` — ordering, buffering, and error propagation the shell already does better.
One file = one stage keeps every `.sem` composable with everything else on your
system, which is the whole point of sempipe.
