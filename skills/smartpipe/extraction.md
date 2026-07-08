# Extraction — typed fields out of anything

Load when: pulling structured data from text/records/media; building JSONL
datasets; labeling; enriching.
Parent: [SKILL.md](../../SKILL.md) · Siblings: [ingestion](ingestion.md) · [cost-and-reliability](cost-and-reliability.md)

## map vs extend (pick deliberately)

- `extend "Add {…}"` MERGES fields onto each record — every existing field
  survives (same-name overwrites, disclosed). Datasets: almost always extend.
- `map "Extract {…}"` REPLACES the row with the extraction (+ `__source`).
- Plain-prompt `map` on records returns `{"result": "...", "__source": …}`.

## The braces grammar (goes INSIDE the quoted prompt)

```console
smartpipe extend "Add {label enum(spam, promo, genuine), urgency number: 0 to 1, tags string[], vendor string?: legal name}" --as jsonl posts.jsonl
```

| Piece | Syntax |
|---|---|
| types | `string` `number` `integer` `boolean` `enum(a, b, …)` `string[]` `number[]` `integer[]` `boolean[]` |
| nullable | any type + `?` (`string?`) — bare fields NEVER admit null |
| description | after a colon: `{vendor string: the legal name}` |
| bare field | any scalar or scalar-list, model's choice |

Richer schemas: `--schema file.json` (full JSON Schema). enum needs a real
"unknown" VALUE, not null (`enum(paid, unpaid, unknown)`).

## Iterate free, then spend

```console
smartpipe schema 'vendor string; total number >= 0'          # compiled schema, zero calls (constraints live in the DSL, never in braces)
smartpipe schema '{vendor string}' --check out.jsonl          # validate old output, exit 0/1
smartpipe map "Extract {vendor, total}" invoices/*.pdf --dry-run   # the exact request, zero calls
smartpipe sample 20 < posts.jsonl | smartpipe extend "…"      # rehearse on fixed rows (sample reads stdin only)
```

## Robust batch runs

- `--keep-invalid`: failed extractions become `{"__invalid": true, "__error": …, "__raw": …}`
  rows instead of skips — route them later with `where`, or re-run just those
  through another model.
- `--tally FIELD`: live class distribution on stderr while a big run streams.
- Validation is enforced: replies that don't match the schema get one repair
  retry, then keep-invalid/skip. You never receive silently-wrong shapes.
