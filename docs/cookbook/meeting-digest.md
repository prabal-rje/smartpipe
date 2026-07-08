# Meeting digests from a folder of recordings

**Goal:** turn a week's folder of call recordings into one Monday digest -
decisions, action items by owner, open blockers - each citing its source
recording and minute.

## Three lines instead of an afternoon of re-listening

```bash
# Slice the calls free (segments STAY audio), pull commitments per slice
# with '§00:10-00:20' provenance, then one digest
smartpipe split --by minutes:10 --in 'recordings/2026-W28/*.mp3' \
| smartpipe extend "Add {decisions string[], action_items string[], blockers string[]}" \
| smartpipe reduce "Write the weekly ops digest: decisions made, action items grouped by owner, open blockers - cite each item's source recording and timestamp"
```

Stage by stage:

- **`split --by minutes:10`** costs zero model calls, and each slice stays
  playable audio (`standup-tue.mp3 §00:10-00:20`), so the next verb can *hear*
  it natively - or fall back to the built-in whisper transcription where the
  model can't ([file inputs](../inputs/files.md)).
- **`extend`** adds the typed arrays *beside* each slice's `source` field, so
  provenance rides through untouched.
- **`reduce`** takes any number of slices (it
  [chunks automatically](../verbs/reduce.md)), and because the prompt asks for
  citations, every action item in the digest points at the exact recording and
  ten-minute window.

## Notes

- Audio slicing is native for wav; mp3 and friends need `ffmpeg` on PATH.
- Audio slices travel as base64 inside the NDJSON records, so segment lines are
  large - that's the cost of a pipe that carries sound.
- Ten minutes is a slice you can replay while triaging the digest; drop to
  `--by minutes:5` when meetings are dense and the citations need to be tighter.

## See also

- [`split`](../verbs/split.md) · [`extend`](../verbs/extend.md) ·
  [`reduce`](../verbs/reduce.md) · [File inputs](../inputs/files.md)
