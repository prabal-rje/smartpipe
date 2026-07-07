# Structured output

smartpipe can give you back plain text or structured JSON. Structured output is what
turns messy text into data you can pipe into `jq`, a spreadsheet, or a database.

There are two ways to ask for it: **inline braces** (quick) and a **`--schema`
file** (production). Both are `map` features.

## Inline braces - for quick work

Put field names in `{braces}` and smartpipe asks the model for exactly those fields
as a JSON object:

```console
$ echo "Invoice from Acme Corp, dated 2026-01-15, total $1250" \
    | smartpipe map "Extract {vendor, date, total}"
{"vendor": "Acme Corp", "date": "2026-01-15", "total": 1250}
```

A single field works too: `map "Extract {total}"`. Types are inferred by the model
- good enough for exploration. To put a literal brace in a prompt, double it:
`{{` and `}}`.

## The ladder, top to bottom

Five rungs; each teaches the next. Climb only as far as your task needs:

| Rung | You write | You get |
|---|---|---|
| 1 | `map "Extract {vendor, total}"` | fields, model-inferred types |
| 2 | `{vendor: the supplier name, total}` | + plain-English guidance per field |
| 2.5 | `{vendor string: the supplier, status enum(paid, unpaid)}` | + real types inline (same vocabulary as the DSL); a fully-typed group regains server-side strict mode |
| 3 | `--schema-from "vendor string; total number >= 0; status enum(paid, unpaid)"` | + real types and constraints - parsed deterministically, **no model call, typos fail free** |
| 4 | `smartpipe schema "an invoice with …" > invoice.json` | a drafted schema **file** (one model call, meta-validated; a failed draft exits 3 with empty stdout) |
| 5 | `--schema invoice.json` | full JSON Schema control |

Braces carry names, types, and descriptions (`ident [type] [: description]`).
Constraints (`>=`, lengths, `optional`) stay in the DSL or the file - that's
where the fence stands now.

## Already have Pydantic or Zod models? Export them

JSON Schema is the interchange format, and both libraries emit it in one line.
No smartpipe plugin needed:

```console
$ python -c "import json; from myapp.models import Invoice; print(json.dumps(Invoice.model_json_schema()))" > invoice.json
$ smartpipe map "Extract the invoice" --schema invoice.json
```

```console
$ npx zod-to-json-schema src/schemas.ts InvoiceSchema > invoice.json   # zod v3
# zod v4 has it built in: z.toJSONSchema(InvoiceSchema)
$ smartpipe map "Extract the invoice" --schema invoice.json
```

One caveat: providers' strict mode rejects some valid JSON Schema (optional
fields, missing per-property types, `$ref`s). smartpipe detects that and falls
back to client-side validation automatically, so exported schemas still work;
they just may not get the server-side guarantee.

(Native `--schema-from-pydantic module:Model` was considered and rejected:
it would import and execute your application code from a CLI flag and pull
pydantic into a frozen dependency budget, for something a one-liner already
does.)

## `--schema-from` - the deterministic DSL

`field type constraints; field type …` - semicolon-separated:

- Types: `string` · `number` · `integer` · `boolean` · `enum(a, b, …)` ·
  `string[]` · `number[]`
- Constraints: `>= N` · `<= N` (numbers) · `minLength=N` · `maxLength=N`
  (strings) · `optional`

Everything is required unless marked `optional` (which also, correctly, stops
smartpipe claiming the provider's strict mode). Any typo is a usage error naming
the exact fragment - before a single model call.

## `--schema` file - for production

When you need the output to *strictly* conform - exact types, no surprise fields -
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
$ cat invoices.txt \
    | smartpipe map "Extract the invoice data" --schema invoice.json
```

Two layers make this reliable. The schema is sent to the provider as guidance
(their native JSON mode; smartpipe only claims the provider's *strict* variant
when the schema qualifies - every field required, no open objects - because
claiming it for a schema with optional fields, like `date` above, would be
rejected outright). The guarantee, either way, is client-side: every reply is
validated against your schema, repaired once if it fails, and skipped with a
warning if it fails again.

With a schema, smartpipe:

- **enforces it** via the model's native structured-output mode where available;
- **coerces types** - a model that returns `"1250"` (a string) for a `number` field
  gets it turned into `1250`;
- **drops extra fields** when `additionalProperties` is `false`;
- **retries once** if the first reply doesn't validate, re-asking the model with the
  specific error - and skips the item (with a warning) only if that retry also fails.

## When to use which

| | Inline `{braces}` | `--schema` file |
|---|---|---|
| Speed to write | Instant | Write the schema once |
| Type guarantees | Model-inferred | Enforced + coerced |
| Best for | Exploration, one-offs | Pipelines, production |

## The brace grammar, across verbs

The same `{…}` syntax means different things depending on the verb - one sentence
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

- [`map`](../verbs/map.md) - the verb these features belong to
- [Quickstart](../quickstart.md) - structured output in context
