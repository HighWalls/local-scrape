"""Validated installation-local settings. No provider configuration is inherited."""
import json
import os
from pathlib import Path
import secrets
import re

from .llm import normalize, LEGACY_KEYS

DEFAULTS = dict(model="", llm_provider="none", llm_base_url=None,
    llm_api_key_env="LLM_API_KEY", llm_timeout_seconds=180, llm_max_tokens=2048,
    num_ctx=8192, max_input_chars=18000,
    max_response_bytes=5000000, timeout_seconds=45, ollama_timeout_seconds=180,
    allow_private_urls=False, respect_robots=True, max_pages=20, max_depth=3,
    api_host="127.0.0.1", api_port=8765, ollama_url="https://ollama.com",
    ollama_mode="cloud", ollama_api_key_env="OLLAMA_API_KEY",
    credential_env_file=".env",
    crawl_deadline_seconds=180, search_max_results=10, monitor_poll_seconds=10,
    max_monitors=100, history_limit=10000, event_limit=1000)
BOUNDS = dict(num_ctx=(1024,32768), max_input_chars=(100,100000),
    max_response_bytes=(1024,20000000), timeout_seconds=(1,120),
    ollama_timeout_seconds=(1,600), max_pages=(1,100), max_depth=(0,10),
    api_port=(1024,65535), crawl_deadline_seconds=(1,600), search_max_results=(1,20),
    monitor_poll_seconds=(1,3600), max_monitors=(1,1000), history_limit=(1,100000), event_limit=(1,10000))


def load_settings(root=None, overrides=None):
    root = Path(root or os.environ.get("LOCAL_SCRAPE_ROOT") or Path(__file__).resolve().parent.parent).resolve()
    values = dict(DEFAULTS)
    supplied = {}
    config = root / "config.json"
    if config.exists():
        loaded = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("config.json must be an object")
        supplied.update(loaded)
    supplied.update(overrides or {})
    values.update(supplied)
    unknown = set(values) - set(DEFAULTS) - {"data_dir", "artifact_dir"}
    if unknown:
        raise ValueError("Unknown settings: " + ", ".join(sorted(unknown)))
    for key, (low, high) in BOUNDS.items():
        value = values[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key} must be an integer in [{low}, {high}]")
    for key in ("allow_private_urls", "respect_robots"):
        if type(values[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    if values["api_host"] != "127.0.0.1":
        raise ValueError("API must bind 127.0.0.1")
    legacy = 'llm_provider' not in supplied and any(k in supplied for k in LEGACY_KEYS)
    if legacy and 'model' not in supplied:
        values['model'] = 'deepseek-v4.1-flash'
    values['_legacy_ollama'] = legacy
    normalized = normalize(values)
    key_name = normalized['llm_api_key_env']
    values["root"] = str(root)
    for key, default in (("data_dir", "data"), ("artifact_dir", "data/artifacts")):
        path = Path(values.get(key, default))
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError(f"{key} must be within project root")
        path.mkdir(parents=True, exist_ok=True)
        values[key] = str(path)
    filename = values['credential_env_file']
    if filename is not None:
        if not isinstance(filename, str) or not filename:
            raise ValueError('credential_env_file must be an installation-local path or null')
        credential = (root / filename).resolve()
        if not credential.is_relative_to(root) or credential == root:
            raise ValueError('credential_env_file must be within project root')
        values['credential_env_file'] = str(credential)
    values['llm_api_key'] = None
    if normalized['llm_provider'] != 'none' and not (legacy and values['ollama_mode'] == 'local'):
        # An explicitly empty process variable also wins (disables file fallback).
        values['llm_api_key'] = ((os.environ[key_name] or None) if key_name in os.environ
                                    else _read_named_key(values["credential_env_file"], key_name))
    normalized['llm_api_key'] = values['llm_api_key']
    values.update({key: value for key, value in normalized.items() if not key.startswith('_auth')})
    values['ollama_api_key'] = values['llm_api_key'] if legacy and values['ollama_mode'] == 'cloud' else None
    return values


def _read_named_key(filename, key_name):
    """Read ONLY the named credential. No dotenv interpolation or environment mutation."""
    if not filename:
        return None
    path = Path(filename).expanduser()
    if not path.is_file():
        return None
    pattern = re.compile(r"^\s*(?:export\s+)?" + re.escape(key_name) + r"\s*=\s*(.*?)\s*$")
    with path.open(encoding="utf-8-sig") as source:
        for line in source:
            match = pattern.match(line)
            if not match:
                continue
            value = match.group(1)
            if value.startswith(('\"', "'")):
                quote = value[0]
                end = value.find(quote, 1)
                if end < 1:
                    raise ValueError("Malformed named credential in env file")
                value = value[1:end]
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
            if any(c in value for c in ('\r', '\n')):
                raise ValueError("Invalid named credential")
            return value or None
    return None


def token_for(settings):
    path = Path(settings["data_dir"]) / "api-token"
    try:
        with path.open("x", encoding="utf-8") as out:
            out.write(secrets.token_urlsafe(48))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except FileExistsError:
        pass
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 40:
        raise ValueError("Invalid local API token file")
    return token
