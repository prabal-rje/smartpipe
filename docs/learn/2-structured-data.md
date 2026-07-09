# 2 · Structured data

Chapter 1 ended with braces: `{vendor, total}` turned prose into JSON. This
chapter teaches the whole ladder - from a two-second shorthand to a schema a
production pipeline can trust.

## Braces name the fields you want

```bash
echo "Invoice from Acme Corp, dated 2026-01-15, total $1250" \
| smartpipe map "Extract {vendor, date, total}"
# → {"vendor": "Acme Corp", "date": "2026-01-15", "total": 1250}
```

A bare field means "any scalar". Add a type when it matters, a `?` when the
answer may honestly be absent, and a plain-English description when the model
needs a hint:

```bash
smartpipe map "Extract {vendor string: the legal name, total number, po_number string?}"
```

Types: `string` · `number` · `integer` · `boolean` · `enum(a, b, …)` ·
`string[]` · `number[]` - and `enum` is the workhorse for labels:

```bash
cat tickets.jsonl \
| smartpipe map "Classify {label enum(bug, praise, request)}" --tally label
```

`--tally label` keeps a live count on the status line and prints the final
distribution - the fastest sanity check that your labels make sense.

## Records in, records out

Feed JSONL and `extend` adds your extracted fields WITHOUT dropping anything:

```bash
head -3 tickets.jsonl \
| smartpipe extend "Add {sentiment enum(pos, neg, neutral)}"
# → {"id": 812, "body": "app crashes when saving", "sentiment": "neg"}
```

Even a plain-prompt `map` over records answers with records
(`{"result": …}` plus provenance) - rows never silently flatten to prose.

## When braces aren't enough

The schema ladder, cheapest rung first:

1. **Braces** - free, instant, right there in the prompt.
2. **`--schema-from DSL`** - free, adds constraints:
   `--schema-from 'vendor string; total number >= 0'`.
3. **`smartpipe schema EXPR`** - compiles braces or the DSL into a JSON Schema
   file you can review, `--check data.jsonl` against, or `--example`. Free.
4. **`--schema FILE`** - full JSON Schema, enforced with one repair retry.

```bash
smartpipe schema '{vendor string, total number}' > invoice.json
smartpipe map "Extract the details" --schema invoice.json 'invoices/*.pdf'
```

Rather build it interactively? Bare `smartpipe schema` at a terminal opens a
small workshop - `/add` fields, `/test` a data file, `/save` the schema, all
free ([reference](../reference/cli.md#schema-workshop)).

When a reply fails validation even after the repair, the row is skipped and
counted - or kept as a machine-readable failure marker with `--keep-invalid`
(chapter 5).

## Shaping what leaves the pipe

- `--fields name,email` picks and orders columns.
- `--output csv` / `tsv` / `json` forces a format; the default adapts
  (blocks at a terminal, JSONL in a pipe).
- `--bare` strips the `__` metadata when you redirect with `>`.

Next: [3 · Files and media](3-files-and-media.md) - PDFs, images, audio, and
the granularity dial.
