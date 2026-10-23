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
- Your own optional Ollama Cloud account/key for AI only. Plain scrape, crawl,
  search and non-AI monitor checks do not need a key.

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

The default is **Ollama Cloud**, model `deepseek-v4.1-flash`. No local Ollama
server or downloaded model is required. Account access, quota, rate limits and
charges are yours; this distribution includes no API key or paid credits.

Use a local text editor to create a project-root `.env` containing the variable
`OLLAMA_API_KEY` assigned to your own key. Alternatively set that variable in
your secure environment-management UI before launching the app. A process
value takes precedence. **Never paste keys into chat, command arguments,
screenshots, issues or source control.** Do not copy a Hermes environment file.
The app reads its own project credential file, not shared Hermes credentials.
Restart an intentionally persistent API after changing credentials.

Defaults work without `config.json`. Copy `config.example.json` to `config.json`
only to customize settings; never put secrets in JSON config. To read only the
process environment, set `credential_env_file` to `null`.

Fetching, browser execution, state and artifacts run on your computer. Target
sites and search providers still receive network requests. **AI extraction
sends selected page/file content, prompts and schemas to Ollama Cloud.** Schema
generation sends its prompt there too. Do not submit secrets or personal data
without permission. Cloud JSON is prompted and schema-validated, not a guarantee
of factual accuracy or native grammar-constrained output.

## One-shot examples

Run from the project folder in Command Prompt:

```bat
run.cmd scrape https://example.com --render js --formats text screenshot
run.cmd crawl https://example.com --max-pages 2 --max-depth 1 --deadline-seconds 60
run.cmd search "Python documentation" --max-results 3
run.cmd history --limit 10
```

AI examples require your key and locally created input files:

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
