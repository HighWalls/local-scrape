"""Build a source-only Windows ZIP using an explicit public-file allowlist."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tomllib
import zipfile

ROOT_FILES = {
    '.env.example', '.gitattributes', '.gitignore', 'CHANGELOG.md', 'LICENSE', 'QUICKSTART.md',
    'README.md', 'THIRD_PARTY_NOTICES.md', 'config.example.json',
    'configure.cmd', 'install-runtime.cmd', 'pyproject.toml', 'requirements.lock.txt',
    'run.cmd', 'setup.cmd', 'start.cmd', 'stop.cmd',
}
REQUIRED_FILES = {
    'LICENSE', 'README.md', 'QUICKSTART.md', 'pyproject.toml',
    'requirements.lock.txt', 'config.example.json', '.env.example',
    'setup.cmd', 'run.cmd', 'start.cmd', 'stop.cmd',
    'local_scrape/__main__.py', 'local_scrape/settings.py',
    'scripts/bootstrap_runtime.py', 'scripts/run_task.py',
    'skills/local-scrape/SKILL.md',
}
FORBIDDEN_PARTS = {
    '.git', '.venv', '.cache', '.pytest_cache', '__pycache__', 'node_modules',
    'browsers', 'models', 'runtime', 'data', 'logs', 'evidence', 'dist', 'build',
}


def is_public_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    if path.is_absolute() or '..' in path.parts or ':' in relative or '\\' in relative:
        return False
    if any(part in FORBIDDEN_PARTS or part.endswith('.egg-info') for part in path.parts):
        return False
    if len(path.parts) == 1:
        return relative in ROOT_FILES
    if path.parts[0] == 'local_scrape':
        return len(path.parts) == 2 and path.suffix == '.py'
    if path.parts[0] in ('scripts', 'packaging', 'tests'):
        return len(path.parts) == 2 and path.suffix in ('.py', '.cmd')
    if path.parts[0] == 'docs':
        return path.suffix == '.md'
    if path.parts[:2] == ('skills', 'local-scrape'):
        return path.suffix == '.md'
    return False


def public_files(root: Path) -> list[Path]:
    selected = []
    source_dirs = {'local_scrape', 'scripts', 'packaging', 'tests', 'docs', 'skills'}
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        subdirs[:] = [name for name in subdirs
                      if name not in FORBIDDEN_PARTS and not name.endswith('.egg-info')
                      and (current != root or name in source_dirs)
                      and not (current / name).is_symlink()
                      and not (hasattr(Path, 'is_junction') and (current / name).is_junction())]
        for name in filenames:
            path = current / name
            relative = path.relative_to(root).as_posix()
            if not is_public_path(relative):
                continue
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('Links must not be included in release sources')
            if path.is_file():
                selected.append(path)
    names = [path.relative_to(root).as_posix() for path in selected]
    if len(names) != len({name.casefold() for name in names}):
        raise ValueError('Case-insensitive source-file collision')
    missing = REQUIRED_FILES - set(names)
    if missing:
        raise ValueError('Required public files are missing: ' + ', '.join(sorted(missing)))
    return sorted(selected, key=lambda path: path.relative_to(root).as_posix())


def build(root: Path, output_dir: Path) -> dict:
    root = root.resolve()
    output_dir = output_dir.resolve()
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    if not version or any(char not in '0123456789.-abcdefghijklmnopqrstuvwxyz' for char in version):
        raise ValueError('Unsafe version')
    prefix = f'local-scrape-{version}'
    files = public_files(root)
    payloads = {path.relative_to(root).as_posix(): path.read_bytes() for path in files}
    manifest = {
        'format_version': 1, 'name': 'local-scrape', 'version': version,
        'distribution': 'Windows source package; Python and dependencies installed separately',
        'files': [{'path': name, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
                  for name, payload in payloads.items()],
    }
    payloads['RELEASE-MANIFEST.json'] = (json.dumps(manifest, indent=2) + '\n').encode('utf-8')
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f'{prefix}-windows.zip'
    if archive_path.exists():
        raise FileExistsError('Refusing to overwrite an existing release ZIP')
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, payload in payloads.items():
            info = zipfile.ZipInfo(prefix + '/' + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise ValueError('Release ZIP CRC check failed')
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    sums_path = output_dir / 'SHA256SUMS.txt'
    sums_path.write_text(f'{digest}  {archive_path.name}\n', encoding='ascii')
    return {'zip': str(archive_path), 'sha256': digest, 'bytes': archive_path.stat().st_size,
            'payload_files': len(files), 'zip_members': len(payloads), 'checksums': str(sums_path)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.output), indent=2))
