# Optional Hermes skill

The app works without Hermes. This installs instructions only, not a service.

1. Complete [setup](../QUICKSTART.md). Review
   [SKILL.md](../skills/local-scrape/SKILL.md) before installing.
2. Identify the intended profile's actual `HERMES_HOME` from that Hermes session;
   do not guess a default path or copy into a different profile.
3. Copy the entire project folder `skills/local-scrape` (including `references`)
   to `<HERMES_HOME>/skills/local-scrape`. Stop if a skill of that name already
   exists; preserve it and choose intentionally rather than overwrite it.
4. In that same profile, run `hermes skills list`. Ask the agent to load
   `skill_view(name='local-scrape')` and confirm it loaded this version/path.
   Resolve duplicate names instead of deleting unrelated skills.
5. Give the agent this app's extracted folder path once, or make
   `LOCAL_SCRAPE_ROOT` available in its terminal environment. Verify that folder
   contains `run.cmd`, `setup.cmd` and `local_scrape/cli.py`. A copied skill's
   Hermes directory is **not** the app root.

Example request: “Use local-scrape to fetch https://example.com as Markdown.
Use the app folder I supplied and stop the API when finished.”

No Hermes profile config, credentials, memories or history belong in this ZIP.
AI is disabled by default (`llm_provider: none`); ordinary scraping needs no model
account, Ollama daemon or weights. LocalScrape does not inherit the Hermes chat
model, credential pools or OAuth subscription. Configure an optional provider and
exact model independently using [providers.md](providers.md). Recipient-owned
keys stay in this app's `.env` or process environment, never the skill or JSON.
Do not paste a secret into agent chat or copy the whole Hermes `.env`.

The 0.2.0 skill asks for provider/model only when AI is needed and the choice is
unknown. It uses task-scoped `run.cmd` by default, preserves partial-result/source
verification, and coordinates shared-worker cleanup; continuous monitoring is
explicit opt-in, not an idle service left running after ordinary scraping.

Primary references: [Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills),
[profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles).
