# AI Disclosure

This file documents how AI systems are used in this repository.

## Development use

Much of the code, tests, documentation, and cookbook material in this
repository was drafted with LLM coding assistants under human direction. The
maintainer specified requirements, selected designs, reviewed diffs and
behavior, ran local and live-provider checks, and made final decisions before
changes were merged.

Golden files and tests pin user-facing text and behavior so changes can be
reviewed intentionally.

## Runtime use

smartpipe is an LLM client. It sends input to the model endpoint selected
by the user: local providers such as Ollama when configured, or cloud
providers when explicitly configured. The privacy documentation describes
provider data flows and media handling in detail.

Paid or remote media conversions require explicit consent. Per-row disclosures
identify conversions, and telemetry is stored locally.

## Purpose

This file records AI involvement in repository development and distinguishes
that development assistance from runtime model calls made by the tool.
