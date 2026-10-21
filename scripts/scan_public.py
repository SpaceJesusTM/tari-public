#!/usr/bin/env python3
"""Focused release scan; prints categories/paths only, never matching content.

Default scans the staged snapshot if present, otherwise tracked + unignored files.
Use --staged to require a staged candidate. Review the diff as well.
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ROOTS = {"config", "docker", "docs", "examples", "scripts", "tests", "workspace", "plugins"}
ALLOWED_FILES = {"LICENSE", "README.md", "SECURITY.md", ".gitignore", ".dockerignore", ".env.example", "compose.yaml", "tari"}
RULES = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider key": re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}"),
    "cloud access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "GitHub credential": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "Google credential": re.compile(r"(?:ya29\.[A-Za-z0-9_-]{20,}|1//[A-Za-z0-9_-]{25,})"),
    "machine-specific path": re.compile(r"(?:/Users/|/home/(?!node(?:/|\b))[^/\s]+/)", re.I),
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--private-term", action="append", default=[], help="Additional literal sensitive term; matches are never printed")
    args = parser.parse_args()
    staged = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split(b"\0")
    staged = [p.decode() for p in staged if p]
    if args.staged and not staged:
        print("No staged candidate to review.", file=sys.stderr)
        return 1
    use_index = bool(staged)
    paths = staged if use_index else sorted(set(p.decode() for p in git("ls-files", "-c", "-o", "--exclude-standard", "-z").split(b"\0") if p))
    findings = []
    for name in paths:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or (name not in ALLOWED_FILES and path.parts[0] not in ALLOWED_ROOTS):
            findings.append((name, "unapproved source path"))
            continue
        if any(part in {".git", ".local", "backups", "sessions", "__pycache__", "node_modules"} for part in path.parts) or path.suffix in {".sqlite", ".db", ".log", ".pyc"}:
            findings.append((name, "runtime/generated artifact"))
        if name != ".env.example" and (path.name.startswith(".env") or "credentials" in path.name.lower()):
            findings.append((name, "credential file"))
        if use_index:
            mode = git("ls-files", "--stage", "--", name).split(b" ", 1)[0]
            if mode == b"120000":
                findings.append((name, "symlink"))
                continue
            data = git("show", ":" + name)
        else:
            full = ROOT / path
            if any(parent.is_symlink() for parent in [full, *full.parents] if parent.is_relative_to(ROOT)):
                findings.append((name, "symlink"))
                continue
            data = full.read_bytes()
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            findings.append((name, "unexpected binary"))
            continue
        for category, pattern in RULES.items():
            # Scanner patterns describe forbidden strings; do not flag themselves.
            if name != "scripts/scan_public.py" and pattern.search(content):
                findings.append((name, category))
        if any(term.casefold() in content.casefold() for term in args.private_term):
            findings.append((name, "private source term"))
        for email in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", content):
            domain = email.rsplit("@", 1)[1].lower()
            if domain not in {"example.com", "example.org", "example.net", "example.test"} and not domain.endswith(".example.test"):
                findings.append((name, "non-example email"))
    for name, category in sorted(set(findings)):
        print(f"REVIEW: {name}: {category}")
    print(f"Scanned {len(paths)} {'staged' if use_index else 'working-tree'} source files; {len(set(findings))} findings.")
    return bool(findings)


if __name__ == "__main__":
    sys.exit(main())
