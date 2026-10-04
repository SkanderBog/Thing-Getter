"""Fail closed on unexpected public files and common secret/path patterns."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {'.gitignore', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'pyproject.toml',
              'requirements-desktop.txt', 'requirements-build.txt', 'scout', 'Thing-Getter.spec'}
TREES = {'resource_scout', 'desktop', 'docs', 'scripts', '.github', 'tests'}
SUFFIXES = {'.py', '.md', '.txt', '.toml', '.yml', '.yaml', '.iss', '.svg', '.png', '.ico', '.icns', '.ttf'}


def allowed(path):
    return (str(path) in ROOT_FILES or len(path.parts) > 1 and path.parts[0] in TREES and path.suffix in SUFFIXES) and '__pycache__' not in path.parts


def public_files():
    return sorted(p.relative_to(ROOT) for p in ROOT.rglob('*') if p.is_file() and allowed(p.relative_to(ROOT)))


def audit(paths):
    failures = []
    patterns = [
        ('personal home path', re.compile(rb'/(?:home|Users)/[A-Za-z0-9_.-]+/|[A-Za-z]:\\Users\\[^\\\s]+\\')),
        ('private key', re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
        ('GitHub token', re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})')),
        ('provider credential', re.compile(rb'\b(?:sk-[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16})\b')),
        ('credential assignment', re.compile(rb'(?i)(?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*[\x22\x27][A-Za-z0-9_+/=-]{16,}[\x22\x27]')),
    ]
    for relative in paths:
        path = ROOT / relative
        if not allowed(relative) or path.is_symlink():
            failures.append(f'Unexpected public file: {relative}')
            continue
        content = path.read_bytes()
        for label, pattern in patterns:
            if pattern.search(content):
                failures.append(f'{label}: {relative}')
    if failures:
        raise SystemExit('\n'.join(failures))
    print(f'Public audit passed: {len(paths)} explicitly allowed files; no common secrets or personal home paths detected.')


if __name__ == '__main__':
    git = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True)
    paths = [Path(p) for p in git.stdout.decode().split('\0') if p] if git.returncode == 0 else public_files()
    if not paths:
        raise SystemExit('No public files found')
    audit(paths)
