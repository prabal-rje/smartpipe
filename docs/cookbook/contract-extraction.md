# Contract & document extraction

**Goal:** turn a folder of PDF contracts into a spreadsheet of structured fields.

## The pipeline

```console
$ sempipe map "Extract {party_a, party_b, effective_date, total_value, governing_law}" \
    --in 'contracts/*.pdf' \
    --output csv > contracts.csv
```

That's the whole thing. Each PDF becomes one item; sempipe parses it to text
automatically (with `sempipe[files]` installed), extracts the five fields, and writes
a CSV you can open in Excel.

```
party_a,party_b,effective_date,total_value,governing_law
Acme Corp,Globex LLC,2025-03-01,250000,Delaware
Initech,Umbrella Inc,2025-06-15,89000,California
```

## Enforce the shape for production

Inline braces are great for exploration. For a pipeline you run every week, pin a
[JSON Schema](../concepts/structured-output.md) so types are guaranteed and stray
fields are dropped:

```json
// contract.json
{
  "type": "object",
  "properties": {
    "party_a": { "type": "string" },
    "party_b": { "type": "string" },
    "effective_date": { "type": "string" },
    "total_value": { "type": "number" },
    "governing_law": { "type": "string" }
  },
  "required": ["party_a", "party_b"],
  "additionalProperties": false
}
```

```console
$ sempipe map "Extract the contract details" --in 'contracts/*.pdf' --schema contract.json \
    --output csv > contracts.csv
```

Now `total_value` is a real number, and a document that doesn't parse cleanly is
retried once, then skipped with a warning — the batch never dies on one bad file.

## Narrow to the contracts you care about first

Chain a semantic `filter` to process only the relevant documents:

```console
$ sempipe filter "is a signed vendor agreement" --in 'docs/**/*.pdf' \
    | sempipe map "Extract {vendor, renewal_date, annual_cost}" --from-files --output csv
```

The `filter` emits the *paths* of matching files; `--from-files` feeds those paths to
`map`. You extract fields from only the vendor agreements, skipping everything else.

## Save it as a stage

Once the extraction works, freeze it as an executable `.sem` file so the whole
team runs the same stage:

```toml
#!/usr/bin/env -S sempipe run
verb = "map"
prompt = "Extract {vendor, renewal_date, annual_cost}"
schema-file = "contract.json"
output = "csv"
```

```console
$ chmod +x extract-contract.sem
$ sempipe filter "is a signed vendor agreement" --in 'docs/**/*.pdf' \
    | ./extract-contract.sem --from-files > contracts.csv
```

`schema-file` resolves next to the `.sem` file, so the script and its schema
travel together. Full format: [.sem files](../reference/sem-files.md).

## See also

- [File inputs](../inputs/files.md) · [Structured output](../concepts/structured-output.md) ·
  [`map`](../verbs/map.md) · [.sem files](../reference/sem-files.md)
