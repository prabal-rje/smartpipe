# agree - how much do two annotators agree?

Score inter-rater agreement between two label files. **Free - never calls a
model, needs zero config.** One summary record - observed agreement, Cohen's
kappa, Krippendorff's alpha (nominal) - then the confusion matrix as records.

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

The stable seven-field union keeps non-applicable values null, so CSV/TSV can
carry the summary and confusion cells without dropping later columns. At a
terminal the same records render as readable blocks; piped, they are JSONL
for `jq` or table output. The two most common shapes:

```bash
# did my model agree with the gold labels?
smartpipe map "Classify: {label enum(bug, feature, question)}" --output json \
  < tickets.jsonl > model.jsonl
smartpipe agree model.jsonl gold.jsonl --on id

# double-coded sample for the methods section
smartpipe agree coder1.jsonl coder2.jsonl --on id --label sentiment
```

## Alignment

- `--on FIELD` pairs rows by that key on both sides (order-independent).
  Duplicate keys fault loudly - a key must identify each row uniquely. Keys
  present on only one side are excluded from the stats and counted on stderr.
- Without `--on`, rows pair by **row order**; unequal row counts fault loudly
  (exit 64) instead of guessing an alignment.
- `--label FIELD` names the compared value (default `label`). A row whose
  label is missing or `null` is excluded and counted; a file where **no** row
  carries the field faults with a census of the fields it does have.

## The coefficients

- **Observed agreement** - plain fraction of matching pairs.
- **Cohen's kappa** - chance-corrected via each rater's own marginals
  (Cohen 1960).
- **Krippendorff's alpha (nominal)** - chance-corrected via the pooled value
  distribution (coincidence-matrix form, Krippendorff 2011). Also the number
  reviewers ask for when data might one day have missing entries or more
  coders.

Both implementations are pinned in tests against the published worked
examples (kappa 0.40 / 0.1304; alpha 0.095 / 0.692).

**The degenerate case is honest:** when only one label class appears anywhere,
kappa and alpha are mathematically undefined (0/0). They come back `null` -
never `NaN`, never a pretended 1.0 - with a stderr note saying why.

## See also

- [Research methods recipe](../cookbook/research-methods.md) - agreement,
  stratified samples, run manifests, and `--local-only` as one workflow
- [`sample --by`](sample.md#stratified-sampling-by-field) - build the
  double-coding subset with the class balance intact
