# Extraction - typed fields out of anything

Load when: pulling structured data from text/records/media; building JSONL
datasets; labeling; enriching.
Parent: [SKILL.md](../../SKILL.md) · Siblings: [ingestion](ingestion.md) · [cost-and-reliability](cost-and-reliability.md)

## map vs extend (pick deliberately)

- `extend "Add {…}"` KEEPS every existing field and merges the new ones in (same-name overwrite, noted on stderr). Datasets: almost always extend.
- `map "Extract {…}"` REPLACES the row with just the extraction.
- Both stamp `__source` (file + line provenance) on every output record. Expect it when parsing; add `--bare` for clean records.
- `map` with a plain prompt (no braces) on records returns `{"result": "…"}` plus `__source`.

What extend returns, exactly:

```console
printf '{"id": 812, "body": "app crashes when saving"}\n' | smartpipe extend "Add {sentiment enum(pos, neg, neutral)}" --max-calls 3
```
```
{"id":812,"body":"app crashes when saving","sentiment":"neg","__source":{"path":"-","as":"jsonl","line":1}}
```

## The braces grammar (goes INSIDE the quoted prompt)

| Piece | Syntax |
|---|---|
| types | `string` `number` `integer` `boolean` `date` `datetime` `enum(a, b, …)` |
| lists | any scalar + `[]`: `tags string[]`, `scores number[]` |
| nullable | any type + `?` (`vendor string?`) - fields WITHOUT `?` never come back null |
| description | after a colon: `{vendor string: the legal name}` |
| bare field | `{vendor, total}` - model picks a sensible scalar type |

- enum needs a real "unknown" VALUE, never null: `enum(paid, unpaid, unknown)`.
- A list field can become one row per element with `--explode FIELD` (other fields repeat):

```console
printf '{"id": 7, "body": "slow login and broken search"}\n' | smartpipe extend "Add {tags string[]}" --explode tags --max-calls 3
```
```
{"id":7,"body":"slow login and broken search","tags":"performance","__source":{"path":"-","as":"jsonl","line":1}}
{"id":7,"body":"slow login and broken search","tags":"login","__source":{"path":"-","as":"jsonl","line":1}}
```
- `date` always returns `YYYY-MM-DD`; `datetime` always returns full ISO-8601 - no matter how the source text phrases it ("March 5, 2026" → `2026-03-05`). Downstream, `where`/`sort`/`summarize 'count() by bin(due, 1d)'` compare these temporally.
- Constraints never go inside braces. Use the DSL (`--schema-from 'vendor string; total number >= 0'`) or a full JSON Schema (`--schema file.json`).

Date extraction, with the exact output shape:

```console
printf 'The call is at 3pm on July 10th, 2026, US Eastern.\n' | smartpipe map "Extract {when datetime}" --max-calls 3
```
```
{"when":"2026-07-10T15:00:00-04:00","__source":{"path":"-","as":"lines","line":1}}
```

## Rehearse free, then spend

```console
smartpipe schema '{vendor string, total number}'                     # braces → JSON Schema; free, zero calls
smartpipe schema 'vendor string; total number >= 0'                  # DSL with constraints; free
smartpipe schema '{status enum(todo, done)}' --example               # one synthetic valid instance; free
smartpipe schema '{vendor string, total number}' --check out.jsonl   # validate rows: exit 0 pass / 1 failures
smartpipe map "Extract {vendor, total}" invoice.txt --dry-run        # print the exact request; zero calls
smartpipe sample 20 < posts.jsonl | smartpipe extend "Add {label enum(spam, promo, genuine)}" --max-calls 25
```

- Agents: NEVER run bare `smartpipe schema` (no argument) - at a terminal it opens an interactive workshop and a non-interactive session gains nothing. Always pass the expression, with `--check`/`--example` as needed.
- A long instruction lives in a file: write it to `prompt.md`, then `smartpipe map @prompt.md data.txt` (or `--prompt-file prompt.md`). Braces inside the file still work. Verify free first: `smartpipe map @prompt.md data.txt --dry-run`.
- Braces/DSL compile deterministically (free). A plain-English description is the one paid rung: 1 draft call + at most 1 repair; a failed draft exits 3 with nothing on stdout.
- `--check` is open-world by default: it validates only the DECLARED fields (each must exist unless marked `?`/`optional`, and must match its type/enum); undeclared fields - your originals and the `__` spine alike - are ignored with a dim hint. So map/extend output checks cleanly as-is; `--bare` is NOT needed for checking.
  - Contract check (forbid unknown fields): add `--strict` - today's closed-world errors, verbatim.
- `sample` reads stdin only, is seeded (same rows every run), and is free.

## Robust batch runs

- `--keep-invalid`: failed extractions become `{"__invalid": true, "__error": …, "__raw": …}` rows instead of skips - route them later with `where`, or re-run just those through another model.
- `--tally FIELD`: live class distribution on stderr while a big run streams.
- Validation is enforced; you never receive silently-wrong shapes. A malformed reply is fixed by a free deterministic repair first (code fences, trailing commas, quotes), then one paid repair retry, then keep-invalid/skip.
