"""Scan tracked release files without printing credential values."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github-token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'aws-access-key': re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'api-secret': re.compile(rb'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b'),
    'personal-path': re.compile(rb'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
}
PRIVATE_FILES = {'network-client-inventory.json', 'unifi-live-check.json', 'network-physical-live.mmd', 'network-logical-live.mmd'}
PRIVATE_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.crt', '.jsonl'}


def release_files():
    if (ROOT / '.git').exists():
        names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
        return [ROOT / name for name in names if name]
    return [p for p in ROOT.rglob('*') if p.is_file() and not any(part in {'.git', '.venv', '__pycache__', '.pytest_cache', 'dist', 'build'} for part in p.relative_to(ROOT).parts)]


def scan(files):
    findings = []
    for path in files:
        relative = path.relative_to(ROOT)
        if (path.name.startswith('.env') and path.name != '.env.example') or path.name in PRIVATE_FILES or path.suffix in PRIVATE_SUFFIXES:
            findings.append((str(relative), 'private artifact'))
        content = path.read_bytes()
        for name, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append((str(relative), name))
    return findings


def main():
    files = release_files()
    findings = scan(files)
    if findings:
        for path, category in findings:
            print(f'{path}: {category}')
        raise SystemExit(1)
    print(f'Release privacy scan passed: {len(files)} files; no recognized credentials, personal paths or private artifacts.')


if __name__ == '__main__':
    main()
