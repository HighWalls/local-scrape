"""Offline distribution-document checks; no credentials, API or browser access.

Run with either pytest or: python tests/test_distribution_docs.py
These checks validate documentation contracts, not a fresh-machine install.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from urllib.parse import urlsplit
import re
import runpy
import shlex
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["README.md", "QUICKSTART.md", "CHANGELOG.md", "docs/hermes.md", "docs/providers.md",
        "skills/local-scrape/SKILL.md",
        "skills/local-scrape/references/operations.md"]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class DistributionDocsTests(unittest.TestCase):
    def test_bundle_contains_recipient_docs_and_skill(self):
        for relative in DOCS:
            with self.subTest(file=relative):
                self.assertTrue((ROOT / relative).is_file(), relative)
                if (ROOT / relative).is_file():
                    self.assertTrue(read(relative).strip())

    def test_document_relative_links_resolve_inside_distribution(self):
        for relative in DOCS:
            if not (ROOT / relative).is_file():
                continue
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", read(relative)):
                if "://" in target or target.startswith("#"):
                    continue
                path = (ROOT / relative).parent / target.split("#")[0]
                with self.subTest(file=relative, target=target):
                    self.assertTrue(path.resolve().is_relative_to(ROOT.resolve()))
                    self.assertTrue(path.is_file(), target)

    def test_no_workstation_paths_or_unverified_portability_claims(self):
        forbidden = [r"(?i)\b[A-Z]:[/\\]", r"(?i)/home/[^\s]+",
                     r"(?i)/Users/[^\s/]+", r"(?i)profiles[/\\][a-z0-9_-]+",
                     r"(?i)(?:fresh[- ]machine|macOS|Linux)[^\n.]{0,50}(?:verified|tested successfully)",
                     r"(?i)(?:verified|tested successfully)[^\n.]{0,50}(?:fresh[- ]machine|macOS|Linux)"]
        for relative in DOCS:
            if not (ROOT / relative).is_file():
                continue
            for pattern in forbidden:
                with self.subTest(file=relative, pattern=pattern):
                    self.assertIsNone(re.search(pattern, read(relative)))

    def test_skill_metadata_and_root_resolution(self):
        path = ROOT / "skills/local-scrape/SKILL.md"
        self.assertTrue(path.is_file(), "Bundled skill is missing")
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        front, body = text[4:].split("\n---\n", 1)
        self.assertIn("name: local-scrape", front)
        self.assertRegex(front, r"(?m)^version: 0\.2\.0$")
        self.assertNotIn("ollama", front.lower())
        self.assertIn("author: HighWalls, Hermes Agent", front)
        self.assertIn("license: MIT", front)
        self.assertIn("platforms: [windows]", front)
        self.assertIn("requires_toolsets: [terminal]", front)
        description = re.search(r"(?m)^description: (.+)$", front).group(1).strip('"')
        self.assertLessEqual(len(description), 60)
        self.assertTrue(description.endswith("."))
        self.assertGreaterEqual(len(text.splitlines()), 100)
        self.assertLessEqual(len(text.splitlines()), 140)
        self.assertIn("LOCAL_SCRAPE_ROOT", body)
        self.assertIn("copied into Hermes", body)
        self.assertIn("ask", body.lower())
        self.assertIn("run.cmd", body)
        self.assertIn("finally", body)

    def test_publication_contract_and_risk_disclosures(self):
        for relative in ("README.md", "QUICKSTART.md"):
            self.assertTrue((ROOT / relative).is_file(), relative)
            text = read(relative)
            for term in ("setup.cmd", "run.cmd scrape https://example.com", "llm_provider", "LLM_API_KEY", "providers.md"):
                self.assertIn(term, text)
        readme = read("README.md")
        for term in ("3.13", "3.14", "partial", "nonzero", "start.cmd", "stop.cmd", "CAPTCHA", "LICENSE", "source ZIP", "not an EXE"):
            self.assertIn(term, readme)
        for term in ("HERMES_HOME", "hermes skills list", "skill_view", "LOCAL_SCRAPE_ROOT"):
            self.assertIn(term, read("docs/hermes.md"))

    def test_neutral_config_matches_backend_settings_contract(self):
        config = json.loads(read("config.example.json"))
        tree = ast.parse(read("local_scrape/settings.py"))
        call = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "DEFAULTS"
                            for target in node.targets))
        self.assertIsInstance(call, ast.Call)
        defaults = {item.arg: ast.literal_eval(item.value) for item in call.keywords}
        self.assertFalse(set(config) - set(defaults), "Example uses unknown settings")
        for key, value in config.items():
            with self.subTest(setting=key):
                self.assertEqual(value, defaults[key], "Downloaded example must match defaults")
        for key, value in {"llm_provider": "none", "model": "", "llm_base_url": None,
                           "llm_api_key_env": "LLM_API_KEY", "llm_timeout_seconds": 180,
                           "llm_max_tokens": 2048, "credential_env_file": ".env"}.items():
            self.assertEqual(config.get(key), value, key)
        self.assertFalse(any(key.startswith("ollama_") for key in config))
        self.assertFalse(any(key in config for key in ("api_key", "llm_api_key", "password")))
        env = read(".env.example")
        self.assertIn("# LLM_API_KEY=", env)
        self.assertNotIn("OLLAMA_API_KEY", env)
        self.assertFalse(any(line.strip() and not line.lstrip().startswith("#")
                             for line in env.splitlines()), "No active example credentials")

    def test_provider_examples_are_valid_json_and_base_urls(self):
        allowed = {"none", "openai", "openai_compatible", "anthropic", "gemini", "ollama"}
        config_keys = set(json.loads(read("config.example.json")))
        # llm.py is stdlib-only normalization: no settings loader, key reads,
        # filesystem writes, requests, provider SDKs or service startup.
        protocol = runpy.run_path(str(ROOT / "local_scrape/llm.py"))
        neutral = protocol["normalize"](json.loads(read("config.example.json")))
        self.assertEqual(neutral["llm_provider"], "none")
        self.assertEqual(neutral["model"], "")
        self.assertIsNone(neutral["llm_base_url"])
        self.assertIsNone(neutral["llm_api_key"])
        for base in protocol["BASE_URLS"].values():
            self.assertIn(f"`{base}`", read("docs/providers.md"))
        seen = set()
        slash_model = False
        for relative in DOCS:
            for block in re.findall(r"```json\s*\n(.*?)\n```", read(relative), re.S):
                example = json.loads(block)
                if not isinstance(example, dict) or "llm_provider" not in example:
                    continue
                with self.subTest(file=relative, example=example):
                    self.assertFalse(set(example) - config_keys)
                    provider = example["llm_provider"]
                    self.assertIn(provider, allowed)
                    normalized = protocol["normalize"](example)
                    self.assertEqual(normalized["llm_provider"], provider)
                    self.assertEqual(normalized["model"], example["model"])
                    if example.get("llm_base_url"):
                        self.assertEqual(normalized["llm_base_url"], example["llm_base_url"])
                    seen.add(provider)
                    if provider == "none":
                        continue
                    self.assertTrue(example.get("model"), "AI requires explicit model ID")
                    slash_model |= "/" in example["model"]
                    base = example.get("llm_base_url")
                    if provider == "openai_compatible":
                        self.assertTrue(base, "Compatible servers require a base URL")
                    if base:
                        url = urlsplit(base)
                        self.assertTrue(url.scheme == "https" or
                                        (url.scheme == "http" and url.hostname in
                                         {"127.0.0.1", "localhost", "::1"}))
                        self.assertTrue(url.hostname)
                        self.assertFalse(url.username or url.password or url.query or url.fragment)
                        self.assertFalse(base.endswith(("/chat/completions", "/messages", "/api/chat")))
                    self.assertEqual(example.get("llm_api_key_env", "LLM_API_KEY"), "LLM_API_KEY")
        self.assertTrue(allowed - {"none"} <= seen, "Each supported protocol needs an example")
        self.assertTrue(slash_model, "Retain a provider/model ID example")

    def test_provider_guide_and_skill_do_not_imply_automatic_ai(self):
        guide = read("docs/providers.md")
        for term in ("explicitly empty", "process environment", "credential_env_file",
                     "llm_timeout_seconds", "llm_max_tokens", "Responses-only", "OAuth",
                     "one repair", "schema", "HTTPS", "loopback", "No automatic",
                     "openrouter.ai/api/v1", "127.0.0.1:1234/v1"):
            self.assertIn(term, guide)
        for relative in ("README.md", "QUICKSTART.md", "docs/providers.md",
                         "skills/local-scrape/SKILL.md", "skills/local-scrape/references/operations.md"):
            text = read(relative)
            self.assertNotIn("deepseek-v4.1-flash", text)
            self.assertNotIn("configure.cmd", text)
            self.assertNotIn("OLLAMA_API_KEY", text)
        skill = read("skills/local-scrape/SKILL.md")
        for term in ("llm_provider", "none", "recipient", "Hermes chat model",
                     "ordinary scrape", "shared", "cancellation", "one repair", "OAuth"):
            self.assertIn(term, skill)
        for term in ("copy config.example.json config.json", "run.cmd extract --url",
                     "schema.json", "llm_provider", "LLM_API_KEY"):
            self.assertIn(term, read("QUICKSTART.md"))

    def test_documented_cli_examples_parse_without_runtime_or_network(self):
        # Execute only the actual CLI parser definitions, never settings/lifecycle.
        tree = ast.parse(read("local_scrape/cli.py"))
        definitions = [node for node in tree.body
                       if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                       and node.name in {"Parser", "parser"}]
        class ArgumentFailure(Exception):
            def __init__(self, code, message, status):
                super().__init__(message)
        namespace = {"argparse": argparse, "WorkflowError": ArgumentFailure}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), "cli-parser-only", "exec"), namespace)
        count = 0
        for relative in DOCS:
            if not (ROOT / relative).is_file():
                continue
            for line in read(relative).splitlines():
                line = line.strip()
                prefix = "run.cmd "
                if not line.startswith(prefix):
                    continue
                args = shlex.split(line[len(prefix):])
                with self.subTest(file=relative, command=line):
                    parsed = namespace["parser"]().parse_args(args)
                    self.assertNotIn(parsed.command, {"start", "stop"}, "Use explicit lifecycle wrappers")
                count += 1
        self.assertGreaterEqual(count, 8, "CLI examples were not discovered")

    def test_status_docs_use_the_actual_nested_api_field(self):
        tree = ast.parse(read("local_scrape/lifecycle.py"))
        status = next(node for node in tree.body
                      if isinstance(node, ast.FunctionDef) and node.name == "status")
        result = next(node.value for node in ast.walk(status) if isinstance(node, ast.Return))
        keys = [node.value for node in result.keys]
        self.assertIn("api", keys)
        api = result.values[keys.index("api")]
        self.assertIn("running", [node.value for node in api.keys])
        for relative in ("README.md", "skills/local-scrape/SKILL.md",
                         "skills/local-scrape/references/operations.md"):
            with self.subTest(file=relative):
                self.assertIn("data.api.running", read(relative))
                self.assertNotIn("`data.running`", read(relative))

    def test_named_wrappers_match_distribution_interfaces(self):
        for wrapper in ("setup.cmd", "run.cmd", "start.cmd", "stop.cmd"):
            self.assertTrue((ROOT / wrapper).is_file(), wrapper)
        self.assertRegex(read("setup.cmd"), r"(?:bootstrap_runtime\.py|install-runtime\.cmd)")
        self.assertIn("run_task.py", read("run.cmd"))
        self.assertIn("from local_scrape import cli", read("scripts/run_task.py"))
        self.assertIn("task_service(", read("scripts/run_task.py"))
        self.assertIn("start", read("start.cmd"))
        self.assertIn("stop", read("stop.cmd"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
