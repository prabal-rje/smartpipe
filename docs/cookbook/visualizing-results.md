# Visualizing results

smartpipe emits data and standard terminal tools can render it. These recipes
cover the views people actually reach for: distributions, ranked tables, live
tallies, and the join threshold picker.

## A field's distribution (the 80% case)

Built in twice over. `smartpipe chart` draws it (no model calls; `--save`
writes an SVG or PNG - the extension picks the format):

```bash
cat tickets.txt \
| smartpipe map "Extract {label}" \
| smartpipe chart label --save labels.svg
# → bug      ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇ 14
# → feature  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇ 7
# → question ▇▇▇▇▇▇ 3
```

Several distributions in one pass - the analyst's first look:

```bash
cat tickets.jsonl \
| smartpipe chart --facet label,severity,region --save tickets.svg
# → ── label ──────────────────────────────────
# → bug      ▇▇▇▇▇▇▇▇▇▇▇▇▇▇ 41
# → feature  ▇▇▇▇▇▇ 17
# → ── severity ───────────────────────────────
# → …
```

And `--tally` counts any extracted field live on the status line and as one
final `stderr` line, without touching `stdout`:

```bash
cat tickets.txt \
| smartpipe map "Extract {label: bug, feature, or question}" --tally label
# → {"label":"bug"} …
# → tally: bug 14 · feature 7 · question 3
```

Or compose it the Unix way (works for any field):

```bash
smartpipe map "Extract {label}" < tickets.txt \
| jq -r .label \
| sort \
| uniq -c \
| sort -rn
# →   14 bug
# →    7 feature
# →    3 question
```

Pipe that into a bar chart with [youplot](https://github.com/red-data-tools/YouPlot):

```bash
… \
| jq -r .label \
| sort \
| uniq -c \
| sort -rn \
| youplot bar -d ' ' --title "labels"
```

## A ranked table you can read

`top_k` output is JSONL with `__score`; [visidata](https://visidata.org) turns
it into an interactive table:

```bash
smartpipe top_k 20 --near "billing complaints" < feedback.jsonl \
| vd -f jsonl
```

Plain terminal version, no extra tools:

```bash
… \
| jq -r '[(.__score|tostring), .text[:70]] | @tsv' \
| column -t -s $'\t'
```

## Picking a join threshold from the score histogram

Run `join` once without `--threshold`, look at where scores cluster, then set
the floor between the clusters:

```bash
smartpipe join "{left.text} is about {right.name}" --right products.jsonl < tickets.txt \
| jq .__score \
| sort -n \
| uniq -c
# scores bunch at ~0.4 (noise) and ~0.75 (real) → rerun with --threshold 0.6
```

## The unmatched remainder

`join --unmatched leftovers.txt` writes every zero-match left item verbatim -
the worklist for a second pass with a looser predicate, a bigger `--k`, or a
human:

```bash
smartpipe join "…" --right kb.jsonl --unmatched leftovers.txt < tickets.txt
# → join: 34 matched · 7 unmatched → leftovers.txt
```

## A live counter over a stream

`filter` already shows `N matched` on its status line while a `tail -f` runs;
`top_k --stream` is the live leaderboard. For a rolling digest, `reduce
--window 50` emits one synthesis per window as the stream flows.
