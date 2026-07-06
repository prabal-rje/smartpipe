# Visualizing results

sempipe emits data; your terminal already knows how to draw it. These recipes
cover the views people actually reach for: distributions, ranked tables, live
tallies, and the join threshold picker.

## A field's distribution (the 80% case)

Built in — `--tally` counts any extracted field, live on the status line and as
one final stderr line, without touching stdout:

```console
$ cat tickets.txt | sempipe map "Extract {label: bug, feature, or question}" --tally label
{"label":"bug"} …
tally: bug 14 · feature 7 · question 3
```

Or compose it the Unix way (works for any field, any time):

```console
$ sempipe map "Extract {label}" < tickets.txt | jq -r .label | sort | uniq -c | sort -rn
  14 bug
   7 feature
   3 question
```

Pipe that into a bar chart with [youplot](https://github.com/red-data-tools/YouPlot):

```console
$ … | jq -r .label | sort | uniq -c | sort -rn | youplot bar -d ' ' --title "labels"
```

## A ranked table you can read

`top_k` output is NDJSON with `_score`; [visidata](https://visidata.org) turns
it into an interactive table:

```console
$ sempipe top_k 20 --near "billing complaints" < feedback.jsonl | vd -f jsonl
```

Plain terminal version, no extra tools:

```console
$ … | jq -r '[(._score|tostring), .text[:70]] | @tsv' | column -t -s $'\t'
```

## Picking a join threshold from the score histogram

Run `join` once without `--threshold`, look at where scores cluster, then set
the floor between the clusters:

```console
$ sempipe join "{left.text} is about {right.name}" --right products.jsonl < tickets.txt \
    | jq ._score | sort -n | uniq -c
$ # scores bunch at ~0.4 (noise) and ~0.75 (real) → rerun with --threshold 0.6
```

## The unmatched remainder

`join --unmatched leftovers.txt` writes every zero-match left item verbatim —
the worklist for a second pass with a looser predicate, a bigger `--k`, or a
human:

```console
$ sempipe join "…" --right kb.jsonl --unmatched leftovers.txt < tickets.txt
join: 34 matched · 7 unmatched → leftovers.txt
```

## A live counter over a stream

`filter` already shows `N matched` on its status line while a `tail -f` runs;
`top_k --stream` is the live leaderboard. For a rolling digest, `reduce
--window 50` emits one synthesis per window as the stream flows.
