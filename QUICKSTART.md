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
though setup succeeded. No Ollama account is needed for this scrape.

`run.cmd` starts the API, performs the command and stops it afterward, including
normal failures. You do not need to run `start.cmd`. Do not overlap commands.
Inspect JSON `ok`, errors and partial-result warnings; a nonzero exit means failure.
After a forced close or interrupted cleanup, run `stop.cmd` and check status as
shown in the [README](README.md).

## Only if you want AI

Use a local text editor to create `.env` in this folder and assign your own key
to `OLLAMA_API_KEY`. Or set it with your secure environment-management UI.
**Do not paste the key into chat or a command line.**

AI uses **Ollama Cloud** (`deepseek-v4.1-flash`), sends the selected input there,
and consumes your own account's quota/billing. No local model download is needed.
Plain scrape, crawl and search do not use this key. See the [README](README.md)
for AI examples and limits.

## Optional: use with Hermes

Hermes is not required. Follow [docs/hermes.md](docs/hermes.md) to copy only the
bundled skill into your chosen Hermes profile and point it at this app folder.

Continuous monitoring is a separate opt-in mode using `start.cmd`/`stop.cmd`;
see the [README](README.md). Do not leave the API running for ordinary scraping.
