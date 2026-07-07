# Security policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting ("Report a vulnerability"
under the Security tab) or email prabal@rjeinc.ca. Please do not open public
issues for security reports. You'll get an acknowledgment within a few days.

## Scope worth knowing

- API keys are read from environment variables only, never written to config.
- The ChatGPT login token lives in `~/.config/smartpipe/auth.json` (0600).
- The opt-in result cache and usage ledger store model outputs/usage locally
  (`~/.cache/smartpipe`, `~/.local/state/smartpipe`) — see docs/privacy.md.
- Custom verbs from Python entry points execute third-party code by design;
  install plugins you trust, as with any plugin system.
