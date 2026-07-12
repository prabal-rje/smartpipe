# A knowledge graph from a mixed corpus

**Goal:** turn a folder of PDFs, recordings, and raw text into an interactive,
cited entity graph - free first, and pay only to name the strongest links.

Every command below runs verbatim on the
[smartpipe-playground](https://github.com/prabal-rje/smartpipe-playground)
corpus (`smartpipe demo` fetches it - free, no model calls), and every
output shown is from a real run.

## The free pass: $0, on-device, cited

```bash
smartpipe graph --fast 'reports/*.pdf' 'recordings/*.mp3' data/feedback.txt --save corpus.html
```

`--fast` calls no chat model: a local NER model finds the entities,
co-occurrence weights the edges, and the recordings are transcribed on-device.
(Two configured roles can spend here, each disclosed: a cloud `embed-model`
on the name fold, a remote `stt-model` per clip. This run configures
neither.)
`stdout` is JSONL edges, heaviest first, each carrying the files behind it:

```json
{"source":"AIAA","relation":"co-occurs","target":"NASA","weight":2,"sources":[{"path":"reports/nasa-quadcopter-acoustics.pdf","as":"file"},{"path":"reports/nasa-x57-emi.pdf","as":"file"}]}
{"source":"NASA","relation":"co-occurs","target":"NASA Langley Research Center","weight":2,"sources":[{"path":"reports/nasa-quadcopter-acoustics.pdf","as":"file"},{"path":"reports/nasa-x57-emi.pdf","as":"file"}]}
```

Speech becomes graph too - `recordings/` is public-domain LibriVox narration,
and its characters arrive as cited nodes like anything else:

```json
{"source":"Big Joe Brady","relation":"co-occurs","target":"hardware store","weight":1,"sources":[{"path":"recordings/meeting-01.mp3","as":"file"}]}
```

The `stderr` receipt discloses every conversion and ends with the honest
tally (trimmed to the load-bearing lines):

```text
note: converted: recordings/meeting-01.mp3 audio → text (whisper tiny)
note: converted: audio → text ×5
note: degraded: figures dropped ×1
note: 14 entity names folded into 7 nodes
note: graph saved: corpus.html
note: graph: 78 entities (14 folded) · 969 edges (0 pruned) · 0 tok
```

`0 tok` is the point: nothing left the machine on this run. (The receipt is
live - had a cloud `embed-model` or remote `stt-model` been configured, its
spend would print here instead, and entity names or audio would ride that
wire.) `corpus.html` is the interactive view - search, a live weight filter,
and a provenance card on every hovered edge.

> **One long recording?** A corpus that is effectively one window (a single
> long MP3, one big document read whole) makes everything co-occur with
> everything - the run flags it (`near-complete graph …`), and `--window
> sentence` plus `--min-weight 2`, in that order, restore signal. The
> [`graph` page](../verbs/graph.md) explains why min-weight alone is not
> enough there.

## Name the entity types your corpus is about

The default types are `person, organization, location`. Retargeting the same
local model is one flag - no retraining:

```bash
smartpipe graph --fast 'reports/*.pdf' --entities "aircraft, organization, person"
```

```json
{"source":"Avanesian, David","relation":"co-occurs","target":"X-57","weight":1,"sources":[{"path":"reports/nasa-x57-emi.pdf","as":"file"}]}
```

## Point it at everything - the census is honest

The playground also holds image-only invoices, photos, and silent screen
sessions. Free NER cannot read pixels, and nothing is silently dropped:

```bash
smartpipe graph --fast 'invoices/*.pdf' 'photos/*.jpg' 'reports/*.pdf' 'sessions/*.mp4'
```

```text
note: 33 files skipped — no free text (images/scans); the full mode or ocr-model reads them
note: graph: 67 entities (14 folded) · 952 edges (0 pruned) · 0 tok
```

That is the 10 invoices, the 20 photos, and the 3 sessions - censused, and
the run exits `1`, not `0`, so a script can tell the graph is partial. The fix
is in the census line: drop `--fast` and give a focus prompt, and the vision
ladder reads the scans (about one call per ~2k-token chunk plus one per
figure; the [cost plan prints before any
spend](../verbs/graph.md#the-preflight-plan-and-the-belt)):

```bash
smartpipe graph "who invoices whom, and for what" 'invoices/*.pdf' 'reports/*.pdf' --max-calls 200
```

## Pay a capped amount to name the strongest links

The hybrid form keeps the free graph and spends one naming call per edge on
only the N heaviest:

```bash
smartpipe graph "who works on which program" --name-top 5 'reports/*.pdf' --entities "aircraft, organization, person" --max-calls 12
```

`--max-calls` caps the WHOLE run - the fold's embedding calls and any
repair retries draw from the same budget as the naming calls, so give the
belt headroom beyond N (here 12 for 5 names). A too-tight belt degrades
gracefully: unnamed edges keep `co-occurs`, disclosed.

Named edges trade `co-occurs` for a model-read relation:

```json
{"source":"A. Christian","relation":"presents to","target":"Association for Unmanned Vehicle Systems\nInternational","weight":1,"sources":[{"path":"reports/nasa-quadcopter-acoustics.pdf","as":"file"}]}
```

A belt shortfall degrades instead of lying - unnamed edges keep `co-occurs`,
and `stderr` says exactly how far the budget went (this run's belt was
deliberately tight):

```text
note: named 1 of 5 (belt); 4 strongest remain co-occurs
note: graph: 63 entities (10 folded) · 860 edges · 1 named · run: ↑1.9k ↓1.9k tok
```

Rerunning with a higher `--max-calls` is cheap: cached work is never re-bought.

## Six ways out

`--save` picks the format by extension: `.html` (the interactive view),
`.graphml` (Gephi, yEd), `.dot` (Graphviz), `.mmd` (Mermaid), `.csv`
(a nodes + edges pair, Neo4j-importable), or a trailing-slash `vault/` for an
Obsidian vault with one wikilinked, cited note per entity. The
[`graph` page](../verbs/graph.md) has the full format table.

## See also

- [`graph`](../verbs/graph.md) - the three cost forms, the modality matrix,
  and the belt in full
- [`extend`](../verbs/extend.md) for custom edge extraction - `graph` adopts
  `{"subject", "relation", "object"}` rows for free
- [Video Q&A](video-qa.md) · [Ranking documents](ranking-documents.md)
