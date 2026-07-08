# Customer feedback analysis

**Goal:** turn raw NPS exports and app reviews into the Monday slide - sized
themes with verbatim quotes, week-over-week drift with measured shares, and a
deck-ready chart.

## The Monday slide: top detractor themes

```console
$ cat nps-w28.jsonl \
    | smartpipe where 'score <= 6' \
    | smartpipe cluster --top 8
```

One pipe replaces an afternoon of manual coding: `where` cuts to detractors
for free, and `cluster --top 8` emits exactly the slide content -
`{cluster, size, share, examples}` - with a cost preview before anything is
spent. Labels run at temperature 0, so the re-run names the same themes next
Monday. If copy-paste review spam inflates the counts, put a
[`distinct`](../verbs/distinct.md) stage before `cluster`.

## What changed this week: theme drift

"What are customers complaining about this week that they weren't last week"
is a `diff`, not a hunch:

```console
# Save this week's detractor comments as next week's baseline (free, no model calls)
$ smartpipe where 'score <= 6' < nps-w27.jsonl > detractors-w27.jsonl

# Wednesday drift check: themes that over-index this week vs last, both shares as evidence
$ cat nps-w28.jsonl \
    | smartpipe where 'score <= 6' \
    | smartpipe diff --right detractors-w27.jsonl --top 5
```

`diff` embeds both weeks, groups the union by meaning, and reports only the
lopsided themes as `{theme, share_left, share_right, examples}` - "checkout
complaints went 2% -> 34%" is a number a PM can put in front of engineering.
Balanced themes are omitted, so the answer never buries the signal.

## Deck night: code every review, chart the mix

```console
# Code each review in place, keep the enriched dataset, chart the mix + save the SVG
$ cat app-reviews.jsonl \
    | smartpipe extend "Add {sentiment enum(pos, neg, neutral), theme enum(pricing, onboarding, performance, support, reliability, other)}" \
    | tee coded-reviews.jsonl \
    | smartpipe chart --facet sentiment,theme --save review-mix.svg --title "App reviews, June"
```

`extend` enriches in place, so the original review text rides along into
`coded-reviews.jsonl` - the spreadsheet artifact analysts actually keep.
`chart --facet` draws both distributions in the terminal and `--save` writes a
dependency-free SVG for the deck. With the result cache on
(`smartpipe config cache on`), re-running to tweak the chart title costs zero
model calls.

## See also

- [`cluster`](../verbs/cluster.md) · [`diff`](../verbs/diff.md) ·
  [`extend`](../verbs/extend.md) · [`where`](../verbs/where.md) ·
  [Visualizing results](visualizing-results.md)
