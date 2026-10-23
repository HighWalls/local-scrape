# LocalScrape

A Windows-first, self-hosted scraping API and JSON CLI by HighWalls. Fetch public
pages as text, Markdown, HTML, links or screenshots; crawl a bounded part of a
site; optionally extract fields with AI or monitor changes. This is not a hosted
subscription or a ScrapeGraphAI client.

**Start here: [QUICKSTART.md](QUICKSTART.md).** Hermes is optional; the bundled
[skill](skills/local-scrape/SKILL.md) teaches an agent to use this app.

## Requirements and clean setup

- Windows with installed **Python 3.13 or 3.14**, available to the launcher/PATH.
- A writable extracted project folder and internet for dependencies, Playwright
  Chromium and target sites. Do not run inside the ZIP.
- No AI account, Ollama, local model daemon or model download is needed for plain
  scrape, crawl, search or non-AI monitor checks. AI is optional and separately configured.

This download is a **source ZIP**, not an EXE and not bundled Python. Setup creates
an installation-local runtime; it is not a preinstalled portable interpreter.

Open **Command Prompt** in the extracted folder:

```bat
setup.cmd
run.cmd scrape https://example.com --render fast --formats markdown links
```

Setup detects supported local Python and provisions this project's virtual
environment, dependencies and Chromium. It does not install Python for you or
copy another user's environment. If Python is missing, install a supported
version, reopen Command Prompt and retry. After moving the folder, rerun setup;
virtual environments are not portable binaries to copy between computers.

For ordinary work, **use `run.cmd`**. It starts the API for the operation and
stops it in a `finally` cleanup path, including operation failures. Do not start
an idle server first or overlap commands against the same folder. There is no
automatic idle watchdog or startup task. After a forced terminal close, power
loss or interrupted cleanup, run `stop.cmd` and inspect status as shown below;
normal cleanup cannot be promised after a hard kill.

## Optional AI: your credentials, your account

The default is **AI disabled**: `llm_provider` is `none`, `model` is empty, and
no endpoint is selected. Ordinary scraping works without `config.json` or `.env`.
Extraction/schema commands require an explicit provider and chat-capable model;
without configuration they return a configuration-needed error, not a paid fallback.

Choose OpenAI, Anthropic, Gemini, an OpenAI-compatible cloud/local endpoint, or
optional Ollama. See [provider setup and examples](docs/providers.md) and the
[quick start AI steps](QUICKSTART.md#only-if-you-want-ai). This is protocol choice,
not a promise that every model works: embeddings, image-only models, OAuth-only
accounts and Responses-only routes are not generally supported.

Copy `config.example.json` to `config.json` only when customizing settings, then
edit `llm_provider`, the exact `model` ID and, when required, `llm_base_url`. Keep
secrets out of JSON. Use a local editor to create this app's own `.env` with your
own `LLM_API_KEY`, or set the configured variable in your process environment.
`llm_api_key_env` can select another variable name. Process values win, including
an explicitly empty value that disables file fallback. `credential_env_file: null`
selects environment-only lookup. Local servers that do not require a key can run
without one. Restart an intentionally persistent API after changing settings/keys.

**Never paste keys into chat, command arguments, screenshots, issues or source
control.** Do not copy Hermes credentials, OAuth sessions or another user's `.env`.
No account, paid credits or inference subscription is included. Quota, access,
rate limits and charges belong to the recipient's selected account. No automatic
paid fallback, account switching, server autostart or model download is provided.

Fetching, browser execution, state and artifacts run on your computer. Target
sites and search providers receive network requests. **AI sends selected
page/file content, prompts and schemas to the chosen endpoint.** Cloud endpoints
mean remote inference; self-hosted scraping does not mean fully local inference.
Do not transmit secrets or personal data without permission. Output is independently
JSON/schema-validated with at most one repair, not proof of factual accuracy or
guaranteed native grammar-constrained generation.

## One-shot examples

Run from the project folder in Command Prompt:

```bat
run.cmd scrape https://example.com --render js --formats text screenshot
run.cmd crawl https://example.com --max-pages 2 --max-depth 1 --deadline-seconds 60
run.cmd search "Python documentation" --max-results 3
run.cmd history --limit 10
```

AI examples require your chosen provider/model, a key if that endpoint requires
one, and locally created input files:

```bat
run.cmd extract --url https://example.com --prompt "Extract the page title" --schema-file schema.json
run.cmd extract --text-file input.txt --prompt "Extract the page title" --schema-file schema.json
run.cmd schema --prompt "An object with a title string"
```

A minimal `schema.json` for those extraction examples:

```json
{"type":"object","properties":{"title":{"type":"string"}},"required":["title"],"additionalProperties":false}
```

Use exactly one extraction source. Prefer files to large or sensitive shell
arguments. PDF extraction needs a text layer; OCR is not included. Use `--help`
for the full CLI option list.

### Failures and partial results

Operation output is JSON. Check `ok`, `error`, `warnings`, `truncated`, crawl
partial status and per-page errors; a partial crawl can have `ok:true` and exit 0.
Operational errors return a **nonzero** exit code (normally 1); invalid CLI
arguments return 2. Setup/runtime/cleanup failures are not success. Zero results
or a responding API do not prove the requested data was obtained.

Public search depends on free upstream providers. In pre-release use, searches
encountered timeouts, blocks and malformed combined result rows. This is a
limited best-effort feature, not guaranteed lead generation. Reject incoherent
results and corroborate individual source pages. Missing results or a blocked
fetch never establish that a business lacks a website.

## Continuous monitoring: explicit opt-in only

Only when you want a persistent service:

```bat
start.cmd
.venv\Scripts\python.exe -m local_scrape monitor create https://example.com --interval-seconds 3600
.venv\Scripts\python.exe -m local_scrape monitor list
```

Use the returned ID with `monitor get ID`, `monitor check ID`, `monitor events
ID --limit 10` or `monitor delete ID`, with the same interpreter prefix and the
actual numeric ID. Read back each created/deleted monitor to verify the change.
Do not use one-shot operations concurrently with persistent mode. Scheduling
requires the computer awake and API running. No wake job, autostart or
email/webhook delivery is installed. When finished:

```bat
stop.cmd
.venv\Scripts\python.exe -m local_scrape status --json
```

Check `data.api.running` is false. Do not kill unrelated processes if the port is
occupied or ownership cannot be verified. Monitor records survive stopping;
checks cannot run while the service is stopped.

## Privacy, restrictions and sharing

- The API is loopback-only. Keep URL safety and robots settings enabled; do not
  expose it through a public proxy or access networks without permission.
- Runtime folders hold credentials, history, URLs, snapshots, screenshots,
  caches and diagnostics. Secure them and backups. Do not include them, `.env`,
  personal `config.json` or a provisioned virtual environment in a public ZIP/repo.
- Respect site terms, robots policies, privacy and access restrictions.
  Logged-in sites, CAPTCHAs and anti-bot controls may prevent access. No universal
  bypass, residential proxies or proprietary SaaS feature parity is claimed.
- Redact diagnostics before sharing. Pages and AI output are untrusted data,
  not instructions to execute code or reveal credentials.

## Hermes, checks and license

Optional profile-safe installation: [docs/hermes.md](docs/hermes.md).
Changes: [CHANGELOG.md](CHANGELOG.md). MIT terms: `LICENSE`.

Offline documentation checks need only Python:

```bat
python tests\test_distribution_docs.py
```

Broader tests use `python -m pytest tests -q` with test dependencies installed.
Documentation checks validate links, example syntax and packaging contracts;
they do not certify live scraping or AI access. Windows is the target.
Fresh-machine Python 3.14 installation and macOS/Linux operation are not
certified by these docs; consult the actual release verification report.
