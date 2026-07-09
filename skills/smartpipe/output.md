# Output - machines, files, humans

Load when: consuming smartpipe output programmatically, writing results back
to files, or producing human-readable reports.
Parent: [SKILL.md](../../SKILL.md) · Sibling: [ingestion](ingestion.md)

## The three doors out

| Door | Command | Contract |
|---|---|---|
| machines | (default piped stdout) | JSONL, one record per line, never truncated; parse directly |
| files | `… \| smartpipe write 'out/{name}'` | routes items to files; prints written paths to stdout |
| humans | `… \| smartpipe readable` | YAML-ish blocks; NOT for parsing |

## Parsing rules (machines)

- Parse stdout only. stderr = notes/receipts/skips (human diagnostics).
- Text-only flows emit plain lines; anything structured emits JSONL.
- Records from `map`/`extend` carry `__source` (and other `__` fields). Expect them, or strip with `--bare` when a clean record is wanted (e.g. before `> out.jsonl` or `schema --check`).
- The TTY view is NOT output. At a terminal, records render as numbered pretty blocks; piped, the same run emits JSONL:
  - WRONG - screen-scraping what a terminal showed:
    ```
    #1
    vendor: Acme Corp
    total: 1234.56
    ```
    Ordinals (`#1`), indentation, truncation, and media thumbnails exist only at a TTY.
  - RIGHT - pipe or redirect, then parse the line:
    ```
    {"vendor":"Acme Corp","total":1234.56,"__source":{"path":"inv.txt","as":"file"}}
    ```
- `--fields a,b` selects and ORDERS the output columns of structured results (`map`/`extend`/`embed`/`top_k`/`reduce`; never `filter`). Unlisted fields - including `__source` - are dropped: `smartpipe extend "Add {label enum(spam, ok)}" --fields id,label --max-calls 20 < posts.jsonl` emits exactly `{"id":7,"label":"spam"}` per row.
- `--output json|csv|tsv` (on `map`/`extend`/`join` only) forces a format. Verbs that emit input verbatim (`filter`, `where`, `distinct`, `sample`, `sort`) have no `--output` - their output IS the input rows, unchanged.
- Multi-line plain text into a pipe is ambiguous (framing) - a warning says so; use `--output json` for one-line-per-item guarantees.

## write (egress mirrors ingress)

- Template vars: `{name}` `{stem}` `{ext}` `{path}` `{index}` + ANY record field - `write 'by-lang/{lang}.jsonl'` fans out by content.
- Items cut as whole files → one file each (same-path collision = error). Items cut as lines/rows → append into their source group, ORIGINAL order (that's `__source` at work - don't edit it).
- Text-only records write as plain text; records as JSONL.
- `--field NAME` writes one field's value as the raw file content.
- Written rows are stripped of `__` fields by default; `--keep-meta` retains them.
- The written paths land on stdout, one per line, so the pipe continues.

## readable (humans)

- Nested maps indent, lists bullet, multi-line strings render as blocks.
- `__` provenance dimmed at the bottom (`--bare` drops it); long values truncated with counts (`--full` disables).
- Media summarized (`image/png (48 KB)`), never base64.
- Send it to people and reports (`… | smartpipe readable > report.txt`), never to a parser.
