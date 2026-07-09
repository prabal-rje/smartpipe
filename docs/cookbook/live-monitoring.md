# Live monitoring

**Goal:** watch a live stream - a log, a ticket queue, a feed - through semantic
verbs, in real time.

## Semantic `tail -f`

`map`, `filter`, and `extend` process each item as it arrives. No flag needed:
pipe a live source in and results appear as lines come through.

```bash
tail -f app.log \
| smartpipe filter "a user is hitting a real error"
```

Every new log line is judged as it lands; matches flow out immediately. The `stderr`
status line keeps score without touching your data:

```
⠹ Processing [847] 3.1/s · 23 matched
```

Classify a live stream into structured records the same way:

```bash
tail -f app.log \
| smartpipe map "Classify this log line. Add {severity enum(critical, warning, info), category}" \
| tee incidents.jsonl
```

## Rolling synthesis: `reduce --window`

A stream never ends, so `reduce` needs a boundary. `--window N` gives it one: every
N lines, one synthesis, emitted immediately:

```bash
tail -f server.log \
| smartpipe reduce --window 100 "What's the current error trend?"
# → {"window_end": 100, "result": "Mostly timeouts against the payments service..."}
# → {"window_end": 200, "result": "The timeout cluster is resolving; new auth errors..."}
```

Add `--every M` to slide the window instead of resetting it - a fresh summary of the *last* N lines
after every M new ones:

```bash
tail -f server.log \
| smartpipe reduce --window 100 --every 20 "error trend?"
```

When the stream ends (or you press Ctrl-C), whatever is buffered is synthesized and
emitted as a final record marked `"partial": true` - buffered lines are flushed on shutdown rather than discarded.

## The live leaderboard: `top_k --stream`

Keep a running "most relevant so far" over a stream:

```bash
tail -f tickets.jsonl \
| smartpipe top_k 5 --stream --near "billing dispute"
```

At a terminal, the top-5 block repaints in place as better matches arrive. In a
pipe, each change emits a JSONL *snapshot*: a `{"_snapshot": N}` marker line
followed by the K records in rank order, each with `_score` and `_rank` - split on
the markers to consume programmatically. No change, no output.

## The on-call tail, end to end

The pieces above compose into the shift-long triage assistant - the upgrade
to the `tail -f | grep` every SRE already lives in:

```bash
# The on-call tail -f: free grep first, judgment second, a fresh digest every 20 lines
tail -f /var/log/api/api.log \
| smartpipe where 'text has "error" or text has "timeout"' \
| smartpipe filter "a real production failure, not a retry, health check, or graceful shutdown" \
| tee triage.log \
| smartpipe reduce --window 50 --every 20 "What is failing right now, which service, and is it getting worse or better?"
```

Reading it stage by stage: `where` is the free gate that keeps the paid calls
to a trickle, `filter` judges meaning (a retry storm is noise; a graceful
shutdown is not a page), `tee` keeps the raw matched lines for the postmortem,
and the sliding `reduce` narrates what is failing and whether it is getting
worse - a fresh written digest of the last 50 judged lines after every 20 new
ones.

## Ending a live pipeline

Two natural exits, both clean:

- **`| head`** - take what you need and go. smartpipe exits immediately with code 141 when the pipe closes:
  ```bash
  tail -f app.log | smartpipe filter "signals an outage" | head -1 && page-oncall
  ```
- **Ctrl-C** - the first press stops intake, finishes what's in flight, flushes
  `reduce`'s partial window, prints a `done: interrupted - …` summary, and exits
  with the run's true outcome code. A second press bails immediately.

## See also

- [Pipes & items](../concepts/pipes-and-items.md) - batch vs stream in one picture
- [`reduce`](../verbs/reduce.md) · [`top_k`](../verbs/top-k.md) · [`filter`](../verbs/filter.md)
