# Live monitoring

**Goal:** watch a live stream — a log, a ticket queue, a feed — through semantic
verbs, in real time.

## Semantic `tail -f`

The per-item verbs stream by nature. No flag: pipe a live source in and results
appear as lines arrive.

```console
$ tail -f app.log | sempipe filter "a user is hitting a real error"
```

Every new log line is judged as it lands; matches flow out immediately. The stderr
status line keeps score without touching your data:

```
⠹ Processing [847] 3.1/s · 23 matched
```

Classify a live stream into structured records the same way:

```console
$ tail -f app.log | sempipe map "Classify: {severity, category}" | tee incidents.jsonl
```

## Rolling synthesis: `reduce --window`

A stream never ends, so `reduce` needs a boundary. `--window N` gives it one: every
N lines, one synthesis, emitted immediately:

```console
$ tail -f server.log | sempipe reduce --window 100 "What's the current error trend?"
{"window_end": 100, "result": "Mostly timeouts against the payments service..."}
{"window_end": 200, "result": "The timeout cluster is resolving; new auth errors..."}
```

Add `--every M` to slide instead of tumble — a fresh summary of the *last* N lines
after every M new ones:

```console
$ tail -f server.log | sempipe reduce --window 100 --every 20 "error trend?"
```

When the stream ends (or you press Ctrl-C), whatever is buffered is synthesized and
emitted as a final record marked `"partial": true` — buffered lines are never
silently discarded.

## The live leaderboard: `top_k --stream`

Keep a running "most relevant so far" over a stream:

```console
$ tail -f tickets.jsonl | sempipe top_k 5 --stream --near "billing dispute"
```

At a terminal, the top-5 block repaints in place as better matches arrive. In a
pipe, each change emits an NDJSON *snapshot*: a `{"_snapshot": N}` marker line
followed by the K records in rank order, each with `_score` and `_rank` — split on
the markers to consume programmatically. No change, no output.

## Ending a live pipeline

Two natural exits, both clean:

- **`| head`** — take what you need and go. When downstream closes, sempipe dies
  instantly and silently (exit 141), like every good filter:
  ```console
  $ tail -f app.log | sempipe filter "signals an outage" | head -1 && page-oncall
  ```
- **Ctrl-C** — the first press stops intake, finishes what's in flight, flushes
  `reduce`'s partial window, prints a `done: interrupted — …` summary, and exits
  with the run's true outcome code. A second press bails immediately.

## See also

- [Pipes & items](../concepts/pipes-and-items.md) — batch vs stream in one picture
- [`reduce`](../verbs/reduce.md) · [`top_k`](../verbs/top-k.md) · [`filter`](../verbs/filter.md)
