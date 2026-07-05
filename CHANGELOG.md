# Changelog

All notable changes to sempipe are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org).

## [Unreleased]

### Added
- Project scaffolding: CLI entry point, welcome screen, quality gates, CI.
- The io spine: `Item` model with NDJSON sniffing, batch stdin reader,
  TTY-adaptive writers (text/NDJSON/human view), stderr diagnostics with the
  documented exit-code contract, and a hidden `sempipe echo` debugging verb.
