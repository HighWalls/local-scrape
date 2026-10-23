# Third-party dependencies

LocalScrape is an independent application, not a client for ScrapeGraphAI's hosted service. Python dependencies are declared in `pyproject.toml` and the tested runtime inventory is recorded in `requirements.lock.txt`. Each dependency remains subject to its own license.

The source ZIP does not bundle Python, installed Python packages, Chromium, Ollama binaries, model weights or browser profiles. Setup downloads Python dependencies from PyPI and Chromium through Playwright; the recipient is responsible for reviewing their respective license terms.

Ollama Cloud is an optional external inference service, not included account access. AI operations transmit the selected input to the recipient's configured account and may consume its quota. Plain page retrieval does not require an AI key.
