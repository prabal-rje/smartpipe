# Research methods

Everything a methods section (or an IRB protocol) asks of an LLM pipeline,
with the receipts to back it: a reproducible sample, a measured agreement
number, a citable record of every run, and a hard guarantee that sensitive
inputs were processed locally rather than uploaded.

The free examples below are real runs; the corpus is 1,000 labeled tickets
(`{"ticket": "T-0001", "label": "bug"}`, 70% bug / 20% feature / 10% question).

## 1. Draw the annotation sample - stratified, seeded, citable

A plain random subset can under-hit rare classes. `sample --by` keeps the
class balance (proportional allocation, largest-remainder rounding, total
exactly N), and the default seed makes the subset citable - "seed 0" in the
paper reselects the same rows from the same file:

```bash
smartpipe sample 10 --by label < labeled.jsonl > subset.jsonl
# stderr → note: sample: 10 of 1,000 (seed 0, 3 strata by 'label')

smartpipe summarize 'count() by label' < subset.jsonl
```

```json
{"label":"bug","count":7}
{"label":"feature","count":2}
{"label":"question","count":1}
```

The 70/20/10 corpus produced a 7/2/1 sample - by construction, not by luck.
Rows missing the field sample as their own `null` stratum (disclosed on
stderr), so unlabeled data can't silently vanish.

## 2. Measure inter-rater agreement - free, zero config

Two coders double-coded 40 tickets (or: your model labeled them, and you have
gold). `agree` aligns rows by a shared key and reports the numbers reviewers
expect:

```bash
smartpipe agree rater1.jsonl rater2.jsonl --on id
```

```json
{"n":40,"observed_agreement":0.925,"cohen_kappa":0.8819,"krippendorff_alpha":0.8828,"label_a":null,"label_b":null,"count":null}
{"n":null,"observed_agreement":null,"cohen_kappa":null,"krippendorff_alpha":null,"label_a":"feature","label_b":"feature","count":17}
{"n":null,"observed_agreement":null,"cohen_kappa":null,"krippendorff_alpha":null,"label_a":"bug","label_b":"bug","count":12}
{"n":null,"observed_agreement":null,"cohen_kappa":null,"krippendorff_alpha":null,"label_a":"question","label_b":"question","count":8}
{"n":null,"observed_agreement":null,"cohen_kappa":null,"krippendorff_alpha":null,"label_a":"bug","label_b":"feature","count":3}
```

Kappa and alpha are hand-verified against the published worked examples, and
the degenerate single-class case honestly reports `null` (undefined), never a
flattering 1.0. Model-vs-gold works the same way:

```bash
smartpipe extend "Classify: {label enum(bug, feature, question)}" --output json \
  < subset.jsonl > model.jsonl
smartpipe agree model.jsonl gold.jsonl --on ticket
```

## 3. Record the run - the manifest IS the methods paragraph

Every model verb takes `--manifest PATH`. At run end (partial and belted runs
included) smartpipe writes one JSON file: version, verb and raw argv, the
resolved model for every role, the prompt text **and its sha256**, the
compiled schema, the pinned temperature (0.0 - runs are reproducible by
default), item counts, the token/conversion receipt, UTC start/end stamps,
and the exit status.

```bash
smartpipe extend "Classify: {label enum(bug, feature, question)}" \
  --manifest run-manifest.json < subset.jsonl > model.jsonl
```

The file answers "which model, which prompt, over how many items, at what
cost, ending how" - copy the numbers straight into the paper, or archive the
file next to the outputs. It is written atomically and records THIS run; a
rerun overwrites it. A typo'd `--manifest` directory faults before any spend.

```json
{
  "manifest_version": 1,
  "smartpipe_version": "1.5.1",
  "verb": "extend",
  "argv": ["extend", "Classify: {label enum(bug, feature, question)}",
           "--manifest", "run-manifest.json"],
  "models": {"chat": "ollama/qwen3:8b"},
  "prompt": {"text": "Classify: {label enum(bug, feature, question)}",
             "sha256": "ee8dff5180d5e7f054a0ba144a64f2ca212629bc5c091ed4d549bff0d3db4c40"},
  "schema": {"type": "object", "...": "..."},
  "determinism": {"temperature": 0.0},
  "items": {"in": 10, "succeeded": 10, "skipped": 0, "failed": 0},
  "receipt": {"tokens_in": 1968, "tokens_out": 142, "paid_conversions": 0},
  "run": {"started_at": "2026-07-10T17:02:11Z", "finished_at": "2026-07-10T17:02:19Z",
          "exit_code": 0, "exit_status": "ok"}
}
```

## 4. Keep the data on the machine - the IRB checkbox

```bash
smartpipe --local-only map "Redact any patient name: {text}" < notes.jsonl
```

With `--local-only` (or `SMARTPIPE_LOCAL_ONLY=1`), **model execution is local
and input is not uploaded** - and the protocol can say so. The fence is
enforced where models are built, before spend: a cloud wire refuses with exit
2, naming the offender and the local alternative:

```console
$ smartpipe --local-only map "..." --model gpt-4o-mini < notes.jsonl
error: --local-only forbids the cloud chat wire 'openai/gpt-4o-mini'
  With --local-only, input stays on this machine - openai is a cloud endpoint.
  Local chat runs on ollama: smartpipe use ollama   (install: https://ollama.com)
```

The fence covers every role (chat, embeddings, media embeddings, OCR,
transcription). It is not an air-gap switch: a first local run may download
model artifacts, and other supporting requests are allowed when they carry no
user payload. The daily update ping and model catalogs happen to remain
suppressed in fenced runs. It is also honest about indirection: a remote
`OLLAMA_HOST` is refused, because sending items to another box IS data
leaving. What still works, fully local: ollama on localhost, the on-device
embedder, local whisper transcription, the local document-extraction ladder,
and `graph --fast`'s on-device NER.

## The whole study, end to end

```bash
export SMARTPIPE_LOCAL_ONLY=1                                # inputs stay local
smartpipe sample 200 --by label < corpus.jsonl > subset.jsonl # citable subset
smartpipe extend "Classify: {label enum(bug, feature, question)}" \
  --manifest run-manifest.json < subset.jsonl > model.jsonl   # recorded run
smartpipe agree model.jsonl gold.jsonl --on ticket            # the headline number
```

Four lines, and the methods section writes itself: the seed, the strata, the
manifest, the kappa - and the sentence "inputs were not uploaded."
