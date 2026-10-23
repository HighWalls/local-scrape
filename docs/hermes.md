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
LocalScrape's optional key stays in the app's own `.env`, not in the skill.

Primary references: [Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills),
[profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles).
