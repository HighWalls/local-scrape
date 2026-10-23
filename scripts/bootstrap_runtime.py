"""Portable local runtime bootstrap. No machine-wide changes or cloud inference."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import re
import stat
import zipfile
import tomllib
from pathlib import Path, PurePosixPath


def verify_archive(archive: Path, size: int, published_digest: str | None) -> str:
    if not published_digest or not re.fullmatch(r'sha256:[a-f0-9]{64}', published_digest):
        raise ValueError('A published GitHub SHA-256 digest is required')
    if archive.stat().st_size != size:
        raise ValueError('Archive size differs from published metadata')
    with archive.open('rb') as source:
        actual = hashlib.file_digest(source, 'sha256').hexdigest()
    if 'sha256:' + actual != published_digest:
        raise ValueError('Archive SHA-256 differs from published digest')
    return actual


def safe_extract(archive: Path, destination: Path) -> None:
    """Preflight every member before extracting any content."""
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            name = entry.filename.replace('\\', '/')
            parts = PurePosixPath(name).parts
            mode = entry.external_attr >> 16
            target = (destination / name).resolve()
            if (name.startswith('/') or ':' in name or '..' in parts
                    or stat.S_ISLNK(mode) or not target.is_relative_to(destination)):
                raise ValueError(f'Unsafe archive member: {entry.filename}')
        bundle.extractall(destination)

ROOT = Path(__file__).resolve().parents[1]


def supported_python(version=None):
    return tuple((sys.version_info if version is None else version)[:2]) in ((3, 13), (3, 14))


def read_lock(root=ROOT):
    """Require the supplied exact inventory; never resolve an unpinned fallback."""
    result = {}
    for line in (root / 'requirements.lock.txt').read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#]+)', line)
        if not match:
            raise ValueError('requirements.lock.txt must contain exact name==version pins only')
        name, version = match.groups()
        canonical = re.sub(r'[-_.]+', '-', name).lower()
        if canonical in result:
            raise ValueError('Duplicate pin in requirements.lock.txt')
        result[canonical] = (name, version)
    if not result:
        raise ValueError('requirements.lock.txt must contain exact pins')
    return result


def verify_lock(root=ROOT):
    for name, version in read_lock(root).values():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise RuntimeError(f'Missing pinned dependency: {name}=={version}') from None
        if actual != version:
            raise RuntimeError(f'Pinned dependency mismatch: {name}=={version} (installed {actual})')



def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Install isolated Python dependencies and Chromium (default: verify only)')
    parser.add_argument('--python', default=sys.executable, help='Base Python interpreter for venv creation')
    parser.add_argument('--local-ollama', action='store_true', help='OPTIONAL: provision/verify pinned portable Ollama; not needed for cloud mode')
    parser.add_argument('--browser-smoke', action=argparse.BooleanOptionalAction, default=True, help='Launch headless Chromium and save local fixture screenshot')
    return parser.parse_args(argv)


def runtime_env(root: Path = ROOT, base: dict | None = None, *, local_ollama: bool = False) -> dict:
    root = root.resolve()
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.upper().startswith('PIP_') or key.upper() in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'VIRTUAL_ENV'):
            del env[key]
    home = root / '.cache/runtime-home'
    env.update({

        'PLAYWRIGHT_BROWSERS_PATH': str(root / 'browsers'),
        'LOCAL_SCRAPE_ROOT': str(root),
        'UV_CACHE_DIR': str(root / '.cache/uv'),
        'USERPROFILE': str(home), 'HOME': str(home),
        'LOCALAPPDATA': str(root / '.cache/localappdata'),
        'APPDATA': str(root / '.cache/appdata'),
        'PIP_CACHE_DIR': str(root / '.cache/pip'),
        'PIP_DISABLE_PIP_VERSION_CHECK': '1',
        'PIP_CONFIG_FILE': os.devnull,
        'PYTHONNOUSERSITE': '1',
        'PYTHONDONTWRITEBYTECODE': '1',
        'TEMP': str(root / '.cache/tmp'), 'TMP': str(root / '.cache/tmp'),
        'XDG_CACHE_HOME': str(root / '.cache'),
    })
    if local_ollama:
        for key in list(env):
            if key.upper().startswith('OLLAMA_'):
                del env[key]
        env.update({
            'OLLAMA_HOST': '127.0.0.1:11434', 'OLLAMA_MODELS': str(root / 'models'),
            'OLLAMA_NUM_PARALLEL': '1', 'OLLAMA_MAX_LOADED_MODELS': '1',
            'OLLAMA_CONTEXT_LENGTH': '8192', 'OLLAMA_NO_CLOUD': '1',
            'OLLAMA_KEEP_ALIVE': '60s', 'OLLAMA_NOHISTORY': '1',
        })
    return env


REQUIRED = {
    'fastapi': 'fastapi', 'uvicorn': 'uvicorn', 'httpx': 'httpx',
    'beautifulsoup4': 'bs4', 'markdownify': 'markdownify',
    'playwright': 'playwright.sync_api', 'pypdf': 'pypdf',
    'jsonschema': 'jsonschema', 'ddgs': 'ddgs', 'python-dotenv': 'dotenv',
    'pytest': 'pytest', 'pytest-asyncio': 'pytest_asyncio',
}


OLLAMA_VERSION = 'v0.40.8'
OLLAMA_SIZE = 1582537058
OLLAMA_SHA256 = '04d1d43ac62c2deb0393307bb49fed7caf6b192de41bdd497aaa9488089d5552'


def optional_ollama(root: Path = ROOT, *, install: bool = False) -> dict:
    """Explicit optional portable asset only; never starts a server or pulls a model."""
    archive = root / f'.cache/ollama-windows-amd64-{OLLAMA_VERSION}.zip'
    if not archive.is_file():
        if not install:
            raise FileNotFoundError('Optional Ollama archive missing; use --install --local-ollama to provision it')
        archive.parent.mkdir(parents=True, exist_ok=True)
        url = f'https://github.com/ollama/ollama/releases/download/{OLLAMA_VERSION}/ollama-windows-amd64.zip'
        partial = archive.with_suffix('.zip.part')
        with urllib.request.urlopen(url, timeout=120) as source, partial.open('wb') as target:
            shutil.copyfileobj(source, target, length=65536)
        verify_archive(partial, OLLAMA_SIZE, 'sha256:' + OLLAMA_SHA256)
        partial.replace(archive)
    digest = verify_archive(archive, OLLAMA_SIZE, 'sha256:' + OLLAMA_SHA256)
    target = root / 'runtime/ollama'
    if install:
        safe_extract(archive, target)
    with zipfile.ZipFile(archive) as bundle:
        files = [item for item in bundle.infolist() if not item.is_dir()]
        for item in files:
            installed = target / item.filename
            if not installed.is_file() or installed.stat().st_size != item.file_size:
                raise RuntimeError(f'Optional Ollama extraction incomplete: {item.filename}; rerun --install --local-ollama')
        with bundle.open('ollama.exe') as original, (target / 'ollama.exe').open('rb') as installed:
            if hashlib.file_digest(original, 'sha256').digest() != hashlib.file_digest(installed, 'sha256').digest():
                raise RuntimeError('Installed ollama.exe differs from verified release archive')
    return {'version': OLLAMA_VERSION, 'archive_sha256': digest,
            'executable': str(target / 'ollama.exe'), 'files_checked': len(files),
            'model_pulled': False, 'server_started': False}


def browser_smoke(root: Path = ROOT) -> dict:
    from playwright.sync_api import sync_playwright
    env = runtime_env(root)
    os.environ.update(env)
    started = time.perf_counter()
    screenshot = root / 'evidence/runtime-browser.png'
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, env=env, args=['--enable-automation'])
        try:
            cdp = browser.new_browser_cdp_session()
            command_line = cdp.send('Browser.getBrowserCommandLine')['arguments']
            executable = Path(command_line[0]).resolve()
            if not executable.is_file() or not executable.is_relative_to((root / 'browsers').resolve()):
                raise RuntimeError('Running Chromium executable is not project-local')
            page = browser.new_page(viewport={'width': 960, 'height': 540})
            page.route('**/*', lambda route: route.abort())
            page.set_content('<!doctype html><title>Local runtime smoke</title><h1>Portable Chromium</h1><p id="message">Pending</p><script>document.getElementById("message").textContent="JavaScript executed locally"</script>')
            text = page.locator('#message').inner_text()
            if text != 'JavaScript executed locally':
                raise RuntimeError('Browser JavaScript execution failed')
            page.screenshot(path=str(screenshot), full_page=True)
            report = {'ok': True, 'version': browser.version, 'executable': str(executable),
                      'binary_mode': 'headless-shell' if 'headless' in executable.name.lower() else 'full-chromium',
                      'title': page.title(), 'text': text, 'screenshot': str(screenshot),
                      'png_bytes': screenshot.stat().st_size, 'elapsed_seconds': time.perf_counter() - started,
                      'network_requests_allowed': False}
        finally:
            browser.close()
    if not screenshot.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'):
        raise RuntimeError('Screenshot is not a valid PNG')
    (root / 'evidence/runtime-browser.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def bounded_run(command: list[str], env: dict, timeout: int) -> None:
    logfile = Path(env['LOCAL_SCRAPE_ROOT']) / 'evidence/runtime-install.log'
    logfile.parent.mkdir(parents=True, exist_ok=True)
    print('Installing/verifying runtime step; output: evidence/runtime-install.log', flush=True)
    with logfile.open('ab', buffering=0) as output:
        process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        try:
            code = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            if process.poll() is None:
                if os.name == 'nt':
                    # A targeted tree kill is limited to our live installer child.
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   capture_output=True, timeout=15)
                else:
                    process.kill()
            process.wait(timeout=15)
            raise RuntimeError('Runtime installer cancelled or timed out; see evidence/runtime-install.log') from None
    if code:
        raise RuntimeError(f'Runtime installation step failed (exit {code}); see evidence/runtime-install.log')


def create_venv(base_python, root, env):
    """Create only the interpreter; avoid ensurepip's redundant pip extraction."""
    python = root / '.venv/Scripts/python.exe'
    if not python.is_file():
        bounded_run([str(base_python), '-I', '-m', 'venv', '--without-pip', str(root / '.venv')], env, 45)
    return python


def pip_bootstrap_wheel(root=ROOT):
    """Execute the lock-pinned pip wheel directly, independent of installed pip."""
    pinned = read_lock(root).get('pip')
    if pinned is None:
        raise ValueError('requirements.lock.txt must pin pip for bootstrap verification')
    _, version = pinned
    with urllib.request.urlopen(f'https://pypi.org/pypi/pip/{version}/json', timeout=30) as response:
        metadata = json.load(response)
    filename = f'pip-{version}-py3-none-any.whl'
    wheel_info = next((item for item in metadata['urls'] if item['filename'] == filename), None)
    if wheel_info is None:
        raise RuntimeError('Pinned pip wheel is not published on PyPI')
    digest = 'sha256:' + wheel_info['digests']['sha256']
    size = wheel_info['size']
    wheel = root / '.cache' / filename
    wheel.parent.mkdir(parents=True, exist_ok=True)
    # A trusted Python installation may already ship exactly this wheel.
    import ensurepip
    bundled = Path(ensurepip.__file__).parent / '_bundled' / filename
    for candidate in (wheel, bundled):
        if candidate.is_file():
            try:
                verify_archive(candidate, size, digest)
            except ValueError:
                continue
            if candidate != wheel:
                shutil.copyfile(candidate, wheel)
            return wheel
    partial = wheel.with_suffix('.whl.part')
    try:
        with urllib.request.urlopen(wheel_info['url'], timeout=60) as source, partial.open('wb') as target:
            shutil.copyfileobj(source, target, length=65536)
        verify_archive(partial, size, digest)
        partial.replace(wheel)
    finally:
        partial.unlink(missing_ok=True)
    return wheel


def main(argv=None) -> int:
    args = parse_args(argv)
    if not supported_python():
        raise RuntimeError('Install Python 3.13 or 3.14, then rerun setup.cmd')
    read_lock(ROOT)
    env = runtime_env(local_ollama=args.local_ollama)
    for directory in ('.cache/runtime-home', '.cache/localappdata', '.cache/appdata',
                      '.cache/tmp', '.cache/uv', '.cache/pip', 'browsers', 'evidence'):
        (ROOT / directory).mkdir(parents=True, exist_ok=True)
    python = ROOT / '.venv/Scripts/python.exe'
    optional_runtime = optional_ollama(install=args.install) if args.local_ollama else None
    if args.install:
        check = subprocess.run([args.python, '-I', '-c', 'import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,13),(3,14)) else 1)'], env=env, capture_output=True, timeout=20)
        if check.returncode:
            raise RuntimeError('Install Python 3.13 or 3.14; the selected --python is unsupported')
        python = create_venv(args.python, ROOT, env)
        wheel = pip_bootstrap_wheel(ROOT)
        runner = 'import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("pip",run_name="__main__")'
        # Install every exact pin including transitives. No resolver/latest fallback.
        # Reinstall exact pins: metadata may survive an interrupted file extraction.
        bounded_run([str(python), '-B', '-c', runner, str(wheel), 'install', '--force-reinstall',
                     '--no-compile', '--timeout', '30', '--retries', '1', '--only-binary=:all:',
                     '--index-url', 'https://pypi.org/simple', '--no-deps',
                     '-r', str(ROOT / 'requirements.lock.txt')], env, 600)
        bounded_run([str(python), '-m', 'pip', 'check'], env, 60)
        bounded_run([str(python), '-m', 'playwright', 'install', '--only-shell', 'chromium'], env, 600)
    if not python.is_file():
        raise RuntimeError('Missing isolated Python; run this script with --install')
    if Path(sys.executable).resolve() != python.resolve():
        forwarded = [x for x in (sys.argv[1:] if argv is None else argv) if x != '--install']
        return subprocess.run([str(python), str(Path(__file__).resolve()), *forwarded], env=env).returncode
    os.environ.update(env)
    versions = {}
    for distribution, module in REQUIRED.items():
        importlib.import_module(module)
        versions[distribution] = importlib.metadata.version(distribution)
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    from packaging.requirements import Requirement
    for module in ('pygments.plugin', 'lxml.etree', 'primp', 'pydantic_core', 'greenlet'):
        importlib.import_module(module)
    for constraint in project['dependencies'] + project['optional-dependencies']['test']:
        requirement = Requirement(constraint)
        if importlib.metadata.version(requirement.name) not in requirement.specifier:
            raise RuntimeError(f'Installed version violates {constraint}')
    distributions = {item.metadata['Name']: item.version for item in importlib.metadata.distributions()
                     if item.metadata.get('Name')}
    verify_lock(ROOT)
    # Verification writes evidence only. The supplied lock is an immutable input.
    (ROOT / 'evidence/installed-inventory.json').write_text(json.dumps(distributions, indent=2), encoding='utf-8')
    report = {
        'ok': True, 'root': str(ROOT),
        'lock_sha256': hashlib.sha256((ROOT / 'requirements.lock.txt').read_bytes()).hexdigest(),
        'lock_policy': 'exact versions; not artifact-hash locked',
        'python': {'executable': sys.executable, 'version': platform.python_version()},
        'dependencies': versions,
        'local_ollama_required': False,
        'optional_ollama': optional_runtime,
        'browser_smoke': None,
        'environment': {k: env[k] for k in ('PLAYWRIGHT_BROWSERS_PATH', 'UV_CACHE_DIR', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'TEMP', 'TMP')},
    }
    if args.browser_smoke:
        report['browser_smoke'] = browser_smoke()
    (ROOT / 'evidence/runtime-python.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
