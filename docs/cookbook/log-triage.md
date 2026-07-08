# Log triage

**Goal:** cut through a noisy log to the entries that actually matter, then summarize
them - using meaning, not just string matching.

## Find the real problems

`grep` finds lines containing "error". `smartpipe filter` finds lines that *describe a
problem*, even when they don't say "error":

```console
$ cat server.log \
    | smartpipe filter "indicates a real failure, not a warning or retry"
```

Combine the two - `grep` to cheaply narrow, `filter` to judge:

```console
$ grep -i "POST /api" server.log \
    | smartpipe filter "the response indicates a bug in our code" \
    | wc -l
```

## Categorize each entry

Turn raw lines into structured records you can count and group:

```console
$ cat errors.log \
    | smartpipe map "Extract {service, severity, root_cause_category}" --output json \
    | jq -r .root_cause_category \
    | sort \
    | uniq -c \
    | sort -rn
```

```
  42 database_timeout
  18 auth_failure
   7 null_pointer
```

## Summarize an incident window

When something breaks, point `reduce` at the window and get a written analysis - even
if the window is far larger than the model's context (it chunks automatically):

```console
$ sed -n '/14:30/,/15:00/p' server.log \
    | smartpipe reduce "Write a root-cause analysis: what failed, when, and the likely cause"
```

## One summary per service

```console
$ cat incidents.jsonl \
    | smartpipe reduce "Summarize the failures and their impact" --group-by service
{"group": "checkout", "result": "Three payment timeouts between 14:32 and 14:41..."}
{"group": "search", "result": "Elevated latency traced to a cache eviction storm..."}
```

## Collapse an alert storm

Paged into 500 firing alerts at 3 a.m.? Two commands turn the storm into
"four causes and one weird thing", at embeddings-only prices - `distinct` and
`outliers` never make chat calls, and `cluster` pays one label call per family:

```console
# 500 firing alerts: fold near-duplicates for cheap, then name and size the real families
$ cat alerts.jsonl \
    | smartpipe where 'status has "firing"' \
    | smartpipe distinct \
    | smartpipe cluster --top 5
```

```console
# And rank the 3 alerts least like everything else - the failure shape you have not seen before
$ cat alerts.jsonl \
    | smartpipe where 'status has "firing"' \
    | smartpipe outliers 3
```

## What was actually new: diff the incident window

Twenty minutes after mitigation, the postmortem question is "what changed?".
Nothing in the standard Unix toolbox can diff two log sets *by meaning*;
`diff` reports the themes that over-index during the window, with shares and
example lines, ready to paste into the incident channel:

```console
# Baseline: yesterday's errors, straight from the rotated log already on disk
$ zgrep -h ERROR /var/log/api/api.log.1.gz > /tmp/yesterday.err

# Which error themes over-index during the incident window vs that baseline
$ sed -n '/14:30/,/15:10/p' /var/log/api/api.log \
    | smartpipe where 'text has "error"' \
    | smartpipe diff --right /tmp/yesterday.err --top 5
```

## See also

- [`filter`](../verbs/filter.md) · [`reduce`](../verbs/reduce.md) ·
  [`cluster`](../verbs/cluster.md) · [`diff`](../verbs/diff.md) ·
  [`outliers`](../verbs/outliers.md) ·
  [Pipes & items](../concepts/pipes-and-items.md) ·
  [Live monitoring](live-monitoring.md)
