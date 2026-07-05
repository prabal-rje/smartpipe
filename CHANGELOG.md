# Changelog

All notable changes to sempipe are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org).

## [Unreleased]

### Added
- Project scaffolding: CLI entry point, welcome screen, quality gates, CI.
- The io spine: `Item` model with NDJSON sniffing, batch stdin reader,
  TTY-adaptive writers (text/NDJSON/human view), stderr diagnostics with the
  documented exit-code contract, and a hidden `sempipe echo` debugging verb.
- Models & config: `sempipe config` (interactive setup, `show` with value
  origins, `model`/`embed-model` setters); three provider adapters (Ollama,
  any OpenAI-compatible endpoint, Anthropic via the optional SDK extra);
  local-first model resolution with Ollama autodetection; and a composition
  root (`AppContainer`) that wires every dependency for an invocation.
