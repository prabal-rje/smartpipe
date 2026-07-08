# Recipes — proven pipelines by job

Load when: composing multi-step jobs; these are verified shapes, adapt names.
Parent: [SKILL.md](../../SKILL.md)

```console
# scanned invoices → rows; anti-join finds what's missing from the ledger
smartpipe map "Extract {vendor string, invoice_number string, total number}" invoices/*.pdf \
| smartpipe join "the same payment" --right ledger.jsonl --kind anti > missing.jsonl

# folder-to-folder translation, per line, provenance-reassembled, no bash loop
smartpipe map "Translate to French" docs/*.txt --as lines \
| smartpipe write 'fr/{name}'

# video/audio library search: index once (flat file), query free forever
smartpipe embed 'sessions/**/*.mp4' > lib.embeddings
smartpipe top_k 3 --near "user hits the checkout bug" < lib.embeddings

# dataset cleaning ritual: free dedupe → judge → gate (belted + tallied)
smartpipe distinct --exact --as jsonl corpus.jsonl \
| smartpipe extend "Add {quality number: 0 to 1, refusal boolean}" --tally quality --max-calls 50000 \
| smartpipe where 'quality >= 0.7 and refusal == false' > clean.jsonl

# alert storm → named causes + the one weird thing
smartpipe where 'status has "firing"' --as jsonl alerts.jsonl | smartpipe cluster --top 5
smartpipe where 'status has "firing"' --as jsonl alerts.jsonl | smartpipe outliers 3

# live tail triage (streaming; free cut keeps the judge affordable)
tail -f app.log \
| smartpipe where 'text has "ERROR" or text has "timeout"' \
| smartpipe filter "a real production failure, not a retry" \
| smartpipe reduce --window 50 --every 20 "What is failing and is it getting worse?"

# big document: split → map per chunk → one synthesis
smartpipe split --by pages:10 big.pdf \
| smartpipe map "Summarize these pages {summary string, page_range string}" \
| smartpipe reduce "Write the executive summary"

# week of meetings → one digest with source+timestamp citations
smartpipe split --by minutes:10 recordings/*.mp3 \
| smartpipe extend "Add {decisions string[], action_items string[]}" \
| smartpipe reduce "Weekly digest: decisions and action items by owner, cite source recording and time"
