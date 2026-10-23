# Quick start (Windows)

1. Install Python **3.13 or 3.14** if you do not already have it. Make it available
   to the Python launcher or PATH.
2. Extract the whole ZIP to a writable folder. Do not run from inside the ZIP.
3. Open that folder in **Command Prompt** and run:

```bat
setup.cmd
run.cmd scrape https://example.com --render fast --formats markdown links
```

Setup downloads this app's dependencies and Chromium. If it fails, read the
error, fix the prerequisite or network problem and retry. Do not continue as
though setup succeeded. This source ZIP is not an EXE and does not bundle Python.
No AI account, Ollama, local model daemon or model download is needed for this
scrape, ordinary crawl or search.

`run.cmd` starts the API, performs the command and stops it afterward, including
normal failures. You do not need to run `start.cmd`. Do not overlap commands.
Inspect JSON `ok`, errors and partial-result warnings; a nonzero exit means failure.
After a forced close or interrupted cleanup, run `stop.cmd` and check status as
shown in the [README](README.md).

## Only if you want AI

AI is disabled by default (`llm_provider: none`, empty `model`). Extraction and
schema generation need your explicit provider/model choice. Nothing chooses a
billable account automatically. Plain scrape, crawl and search need none of this.

1. If you do not already have a `config.json`, copy the neutral example:

```bat
copy config.example.json config.json
```

2. In a local editor, change `llm_provider` and `model` in `config.json` using one
   of the [provider examples](docs/providers.md). Set `llm_base_url` for an
   OpenAI-compatible endpoint; named providers have documented presets. Replace
   model placeholders with the exact chat-capable model ID from your endpoint.
3. For endpoints that require authentication, create this folder's `.env` with a
   local editor and assign your own key to `LLM_API_KEY` (the default
   `llm_api_key_env`). Or use your process environment. Do not put keys in JSON,
   chat or command arguments. Do not copy Hermes credentials. An explicitly empty
   process variable overrides the file; local servers may not need a key.
4. Save the following as `schema.json` in this folder:

```json
{"type":"object","properties":{"title":{"type":"string"}},"required":["title"],"additionalProperties":false}
```

5. Run this in the same Command Prompt:

```bat
run.cmd extract --url "https://example.com" --prompt "Extract the page title" --schema-file "schema.json"
```

Selected content, prompts and schemas go to your chosen endpoint. Cloud inference
uses your account's quota/billing; it is not fully local. No automatic paid
fallback, account switching, OAuth reuse, server autostart or model download is
provided. Output has bounded repair and independent schema validation; verify
important values against the source. A healthy service does not prove model
access. See [providers.md](docs/providers.md) for protocols, limits and errors.

## Optional: use with Hermes

Hermes is not required. Follow [docs/hermes.md](docs/hermes.md) to copy only the
bundled skill into your chosen Hermes profile and point it at this app folder.

Continuous monitoring is a separate opt-in mode using `start.cmd`/`stop.cmd`;
see the [README](README.md). Do not leave the API running for ordinary scraping.
