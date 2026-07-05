# Structured output

sempipe can give you back plain text or structured JSON. Structured output is what
turns messy text into data you can pipe into `jq`, a spreadsheet, or a database.

There are two ways to ask for it: **inline braces** (quick) and a **`--schema`
file** (production). Both are `map` features.

## Inline braces — for quick work

Put field names in `{braces}` and sempipe asks the model for exactly those fields
as a JSON object:

```console
$ echo "Invoice from Acme Corp, dated 2026-01-15, total $1250" \
    | sempipe map "Extract {vendor, date, total}"
{"vendor": "Acme Corp", "date": "2026-01-15", "total": 1250}
```

A single field works too: `map "Extract {total}"`. Types are inferred by the model
— good enough for exploration. To put a literal brace in a prompt, double it:
`{{` and `}}`.

## `--schema` file — for production

When you need the output to *strictly* conform — exact types, no surprise fields —
point `--schema` at a standard [JSON Schema](https://json-schema.org) file:

```json
// invoice.json
{
  "type": "object",
  "properties": {
    "vendor": { "type": "string" },
    "date":   { "type": "string" },
    "total":  { "type": "number" }
  },
  "required": ["vendor", "total"],
  "additionalProperties": false
}
```

```console
$ cat invoices.txt | sempipe map "Extract the invoice data" --schema invoice.json
```

With a schema, sempipe:

- **enforces it** via the model's native structured-output mode where available;
- **coerces types** — a model that returns `"1250"` (a string) for a `number` field
  gets it turned into `1250`;
- **drops extra fields** when `additionalProperties` is `false`;
- **retries once** if the first reply doesn't validate, re-asking the model with the
  specific error — and skips the item (with a warning) only if that retry also fails.

## When to use which

| | Inline `{braces}` | `--schema` file |
|---|---|---|
| Speed to write | Instant | Write the schema once |
| Type guarantees | Model-inferred | Enforced + coerced |
| Best for | Exploration, one-offs | Pipelines, production |

## The brace grammar, across verbs

The same `{…}` syntax means different things depending on the verb — one sentence
covers it:

> **In `map`, braces describe the output. In `filter` and `reduce`, `{field}`
> references the input.**

- `map "Extract {vendor, total}"` → asks for those output fields.
- `filter "{priority} is wrong given {description}"` → substitutes each item's
  `priority` and `description` values into the condition. *(filter ships in a later
  release.)*

Comma-separated groups (`{a, b}`) are a `map`-only shorthand; in `filter`/`reduce`
each `{field}` is a single input reference.

## See also

- [`map`](../verbs/map.md) — the verb these features belong to
- [Quickstart](../quickstart.md) — structured output in context
