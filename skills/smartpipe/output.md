# Output — machines, files, humans

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

- stdout only. stderr = notes/receipts/skips (human diagnostics).
- Text-only flows emit plain lines; anything structured emits JSONL.
- `--output json|csv|tsv` forces a format; `--bare` strips `__` metadata.
- Multi-line plain text into a pipe is ambiguous (framing) — a warning says
  so; use `--output json` for one-line-per-item guarantees.

## write (egress mirrors ingress)

- Template vars: `{name}` `{stem}` `{ext}` `{path}` `{index}` + ANY record
  field — `write 'by-lang/{lang}.jsonl'` fans out by content.
- Items cut as whole files → one file each (same-path collision = error).
  Items cut as lines/rows → append into their source group, ORIGINAL order.
- Text-only records write as plain text; records as JSONL; `--field NAME`
  extracts one field as the file content. `--keep-meta` retains `__` fields.

## readable (humans)

Nested maps indent, lists bullet, multi-line strings render as blocks,
`__` provenance dimmed at the bottom, long values truncated with counts
(`--full` disables), media summarized (`image/png (48 KB)`) never base64.
