"""Trusted installation-only LLM configuration; no SDKs or server management."""
import ipaddress
import re
from urllib.parse import urlsplit

PROVIDERS = ('none', 'openai', 'openai_compatible', 'anthropic', 'gemini', 'ollama')
BASE_URLS = {'openai': 'https://api.openai.com/v1',
             'anthropic': 'https://api.anthropic.com/v1',
             'gemini': 'https://generativelanguage.googleapis.com/v1beta',
             'ollama': 'http://127.0.0.1:11434'}
LEGACY_KEYS = ('ollama_mode', 'ollama_url', 'ollama_api_key_env', 'ollama_api_key')


def validate_endpoint(url):
    """Keep user base paths intact; forbid credential-bearing/ambiguous URLs."""
    if not isinstance(url, str) or not url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError('llm_base_url must be a safe HTTPS or HTTP loopback URL')
    if any(c in url for c in ('\\', '?', '#')):
        raise ValueError('llm_base_url must not contain query, fragment or backslash')
    try:
        parts = urlsplit(url)
        if not parts.hostname or not parts.netloc or '@' in parts.netloc or parts.port == 0:
            raise ValueError()
        # Access .port even when absent: malformed and out-of-range ports fail closed.
        if parts.netloc.endswith(':'):
            raise ValueError()
        loopback = parts.hostname.lower() == 'localhost'
        try:
            loopback = loopback or ipaddress.ip_address(parts.hostname).is_loopback
        except ValueError:
            pass
        if parts.scheme != 'https' and not (parts.scheme == 'http' and loopback):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('llm_base_url requires HTTPS, or HTTP on loopback, with a valid port and no userinfo') from None
    return url


def normalize(settings):
    """Normalize explicit v0.1 dictionaries separately from new generic settings."""
    legacy = settings.get('_legacy_ollama', False) or (
        'llm_provider' not in settings and any(k in settings for k in LEGACY_KEYS))
    if legacy:
        mode = settings.get('ollama_mode', 'cloud')
        url = settings.get('ollama_url', 'https://ollama.com')
        expected = 'https://ollama.com' if mode == 'cloud' else 'http://127.0.0.1:11434'
        if mode not in ('local', 'cloud') or url != expected:
            raise ValueError('Only the exact allowed Ollama endpoint for this legacy mode is supported')
        model = settings.get('model', 'deepseek-v4.1-flash')
        if not isinstance(model, str) or not model or len(model) > 100 or any(c in model for c in '/\\@ ') or 'cloud' in model.lower():
            raise ValueError('A valid legacy Ollama model name is required')
        provider = 'openai_compatible' if mode == 'cloud' else 'ollama'
        base = url + '/v1' if mode == 'cloud' else url
        key_name = settings.get('ollama_api_key_env', 'OLLAMA_API_KEY')
        key = settings.get('ollama_api_key') if mode == 'cloud' else None
        timeout = settings.get('ollama_timeout_seconds', 180)
    else:
        provider = settings.get('llm_provider', 'none')
        if provider not in PROVIDERS:
            raise ValueError('llm_provider must be one of: ' + ', '.join(PROVIDERS))
        model = settings.get('model', '')
        if not isinstance(model, str) or (provider != 'none' and not model.strip()):
            raise ValueError('model must be a nonempty provider model ID when AI is enabled')
        base = settings.get('llm_base_url')
        if base is None:
            base = BASE_URLS.get(provider)
        if provider == 'openai_compatible' and base is None:
            raise ValueError('llm_base_url is required for openai_compatible')
        key_name = settings.get('llm_api_key_env', 'LLM_API_KEY')
        key = settings.get('llm_api_key')
        timeout = settings.get('llm_timeout_seconds', 180)
    if base is not None:
        validate_endpoint(base)
    if not isinstance(key_name, str) or not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,99}', key_name):
        raise ValueError('llm_api_key_env must be an uppercase environment variable name')
    tokens = settings.get('llm_max_tokens', 2048)
    for name, value, low, high in (('llm_timeout_seconds', timeout, 1, 600), ('llm_max_tokens', tokens, 1, 131072)):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{name} must be an integer in [{low}, {high}]')
    return {'llm_provider': provider, 'model': model, 'llm_base_url': base,
            'llm_api_key_env': key_name, 'llm_api_key': key, 'llm_timeout_seconds': timeout,
            'llm_max_tokens': tokens, '_legacy_ollama': bool(legacy),
            '_auth_required': provider in ('openai', 'anthropic', 'gemini') or (legacy and mode == 'cloud')}


def public_status(config):
    return {'llm_provider': config['llm_provider'], 'model': config['model'],
            'configured': config['llm_provider'] != 'none' and bool(config['model']),
            'auth_configured': bool(config.get('llm_api_key')), 'inference_verified': False}
