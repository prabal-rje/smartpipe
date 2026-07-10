# Recipes - proven pipelines by job

Load when: composing multi-step jobs. These shapes are verified; the file
names are examples - swap in yours.
Parent: [SKILL.md](../../SKILL.md)

```console
# scanned invoices → rows; anti-join finds what's missing from the ledger
smartpipe map "Extract {vendor string, invoice_number string, total number, date date}" invoices/*.pdf \
| smartpipe join "the same payment" --right ledger.jsonl --kind anti > missing.jsonl
# each map row: {"vendor":"Acme Corp","invoice_number":"INV-104","total":1234.56,"date":"2026-03-05","__source":{"path":"invoices/acme.pdf","as":"file"}}
# --kind anti emits UNMATCHED left rows verbatim; --kind inner emits {"left": {...}, "right": {...}, "__score": ...}

# folder-to-folder translation, per line, provenance-reassembled, no bash loop
smartpipe map "Translate to French" docs/*.txt --as lines \
| smartpipe write 'fr/{name}'
# {name} = the source file's name, carried by __source - docs/a.txt becomes fr/a.txt (NOT a record field)

# video/audio library search: embed once (flat file); each later query costs one query embedding
smartpipe embed 'sessions/**/*.mp4' > lib.embeddings
smartpipe top_k 3 --near "user hits the checkout bug" < lib.embeddings
# --threshold 0.8 keeps everything ABOVE a similarity bar instead of a fixed K (combine: "3 --threshold 0.8" = at most 3, all >= 0.8)
# each row: {"text":"...","__score":0.8756,"__embedder":"...","__source":{...}} - rank by __score
# each result row gains "__score" (0-1, higher = closer)

# dataset cleaning ritual: free dedupe → judge → gate (belted + tallied)
smartpipe distinct --exact --as jsonl corpus.jsonl \
| smartpipe extend "Add {quality number: 0 to 1, refusal boolean}" --tally refusal --max-calls 50000 \
| smartpipe where 'quality >= 0.7 and refusal == false' > clean.jsonl

# alert storm → named causes + the one weird thing (where reads stdin ONLY - use <, not a file argument)
smartpipe where 'status has "firing"' < alerts.jsonl | smartpipe cluster --top 5
smartpipe where 'status has "firing"' < alerts.jsonl | smartpipe outliers 3
# cluster rows: {"cluster": "...", "size": N, "share": ..., "examples": [...]} · outliers rows gain "__distance"

# live tail triage (streaming; the free cut keeps the judge affordable)
tail -f app.log \
| smartpipe where 'text has "ERROR" or text has "timeout"' \
| smartpipe filter "a real production failure, not a retry" \
| smartpipe reduce --window 50 --every 20 "What is failing and is it getting worse?"

# big document: split → map per chunk → one synthesis
smartpipe split --by pages:10 big.pdf \
| smartpipe map "Summarize these pages {summary string, page_range string}" \
| smartpipe reduce "Write the executive summary"
# split chunks carry __source {"path":"big.pdf","as":"pages","segment":3,"label":"big.pdf §3/12"} - cite the label

# week of meetings → one digest with source+timestamp citations
smartpipe split --by minutes:10 recordings/*.mp3 \
| smartpipe extend "Add {decisions string[], action_items string[]}" \
| smartpipe reduce "Weekly digest: decisions and action items by owner, cite source recording and time"

# knowledge graph, FREE: local NER + co-occurrence - zero model calls, offline-safe
smartpipe graph --fast 'notes/*.md' --entities "person, organization, account" --save graph.html
# each edge: {"source":"Anatolia Star","relation":"co-occurs","target":"account 7741-0092","weight":4,"sources":[{"path":"01-intake-memo.md","as":"file"},...]}
# edges sort heaviest first; exit 1 = some files had no free text (images/scans - stderr censuses them)

# knowledge graph, hybrid: free pass first, then ONE call per edge names the N strongest relations
smartpipe graph "who pays whom" --name-top 50 'notes/*.md' --max-calls 50
# spend = N calls, capped; a belt shortfall DEGRADES, never lies: unnamed edges keep "co-occurs",
# stderr says "named 40 of 50 (belt); 10 strongest remain co-occurs", exit 1

# knowledge graph, full extraction: ~1 call per 2k-token chunk (+1 per embedded figure)
smartpipe graph "who pays whom" 'filings/*.pdf' --max-calls 500
# the cost plan prints BEFORE any spend; a belt below the plan → disclosed partial graph, exit 1
# (rerun with a higher --max-calls: cached extractions are free)

# adopt your own edges (free): fold + serialize rows you built with extend/jq - no extraction
cat edges.jsonl | smartpipe graph --save deals.graphml
# accepts {"source","target"} (graph's own shape) or {"subject","relation","object"} rows;
# --save by extension: .graphml/.dot/.mmd/.csv/.html, or a trailing-slash dir/ = Obsidian vault
```

Reminders that keep these safe:

- First run of any paid stage: add `--max-calls 25` and feed `smartpipe sample 20` rows; scale only after the output shape checks out.
- Expect `__source` on every `map`/`extend`/`split` record; `--bare` strips it when a consumer wants clean rows.
- All shapes above are the PIPED stdout contract; a terminal shows pretty numbered blocks instead - never parse those.
