# diff — what distinguishes two sets

Semantic diff of two item **sets** (not a line diff): embed both sides,
group the union by meaning, report the themes that over-index on one side —
with both shares as evidence.

```console
$ smartpipe diff --right errors-before.log < errors-during.log
diff: left = stdin (2,114) · right = errors-before.log (1,884) · ~4,000 embeddings + labels for the lopsided themes
{"side": "left", "theme": "timeouts calling payments-v2", "share_left": 0.34, "share_right": 0.02, "examples": ["upstream payments-v2 504", "…"]}
```

Left is stdin, right is `--right FILE` — the same shape as `join`. Balanced
themes are omitted (a note counts them; `--all` shows them with
`"side": "both"`): the answer to "what's different" shouldn't bury you in
what's the same. `--top N` caps the list.

## The loops it owns

- **Post-incident:** errors during the window vs the day before — "what's
  new" as themes with examples, twenty minutes after mitigation.
- **Eval regressions:** `diff --right outputs-v1.jsonl < outputs-v2.jsonl` —
  "v2 refuses medical questions more" as a measured theme, not a hunch.
- **Dataset drift:** compare training-set versions BEFORE the GPU bill —
  "v2 over-indexes on code questions" with shares.

Cost: embeddings on both sides plus one label call per lopsided theme.
Without a chat model the themes come numbered; the shares and examples
still tell the story.
