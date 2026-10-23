# Changelog

## 0.2.0 — Provider-neutral AI

- Default to ordinary scraping without an AI provider, key, Ollama installation or local model download.
- Add configurable model identifiers, API base URLs and recipient-selected credential environment variables.
- Support OpenAI Chat Completions, OpenAI-compatible endpoints, native Anthropic Messages and native Gemini generation APIs. Keep Ollama as an optional choice.
- Preserve independent JSON Schema validation, bounded output and deadlines, one repair attempt, guarded scraping URLs and task-scoped cleanup.
- Update the bundled Hermes skill, examples and recipient guides for provider choice. See `docs/providers.md` for supported protocols, local-server examples and compatibility limits.

## 0.1.0 — Windows distribution preparation

- Add recipient-facing setup and one-shot operation instructions.
- Bundle a portable Hermes skill with explicit root discovery and lifecycle rules.
- Document recipient-owned optional Ollama Cloud credentials and the cloud-data
  boundary, plus best-effort search and persistent-monitoring limits.
- Add offline documentation checks for relative links, skill metadata, CLI
  examples, wrapper presence and workstation-path leakage.

These notes describe distribution changes, not a fresh-machine compatibility or
live-provider certification. Release verification is separate.
