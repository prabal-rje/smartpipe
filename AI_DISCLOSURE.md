# AI Disclosure

This file documents AI use in this repository.

## Scope of AI tool usage

**Coding.** The code in this repository was written by LLM coding assistants
under human direction, review, and oversight. The human author specified
requirements and priorities, reviewed behavior (frequently via live runs
against real providers), redirected designs, and made the final decisions —
including rejecting proposed designs and demanding reworks when live output
fell short. Design decisions are recorded in a decision log maintained with
the project.

**The tool itself invokes LLMs at runtime.** smartpipe is a client: it sends
your data to whichever model endpoint you configure (local Ollama by default;
cloud providers by explicit choice), and to nowhere else. Paid media
conversions sit behind an explicit consent flag; per-row disclosures name
every conversion; telemetry stays on your machine (docs/privacy.md).

**Tests and docs.** Test suites, documentation, and the cookbook were
AI-drafted and human-reviewed. Golden files pin user-facing text so that
behavior changes are always deliberate.

## Why disclose

The author believes AI assistance is a fact about how software is built now,
and that stating it plainly is more honest than implying otherwise.
