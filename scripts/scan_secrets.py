#!/usr/bin/env python3
"""Local/CI best-effort secret guard. Exit != 0 blocks commit/release."""
from pathlib import Path
import ipaddress
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "state", "logs", "private", "secrets"}
BAD_NAME = re.compile(r"(^|/)(\.env($|\.)|[^/]+\.env$|id_(rsa|ed25519)|[^/]+\.(pem|p12|pfx|key|sqlite3?|db)(-.*)?$)", re.I)
BAD_LINES = [
    ("private SSH key", re.compile("-----BEGIN " + "(?:OPENSSH |RSA |EC |DSA )?" + "PRIVATE KEY-----")),
    ("real Feishu webhook", re.compile("https://open\.feishu\.cn/open-apis/bot/v2/hook/" + "[0-9a-zA-Z_-]{20,}")),
    ("webhook hardcoded", re.compile(r"FEISHU_WEBHOOK_URL_RSS\s*=\s*https?://")),
    ("X auth cookie hardcoded", re.compile(r"(?m)^\s*TWITTER_AUTH_TOKEN\s*=\s*[^\s#<]{20,}")),
    ("AWS secret key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*[A-Za-z0-9/+]{25,}")),
]
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")

def candidates():
    if (ROOT / ".git").is_dir():
        p = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True)
        if p.returncode != 0:
            raise RuntimeError("Cannot enumerate git tracked files")
        return [Path(k.decode("utf-8")) for k in p.stdout.split(b"\0") if k]
    return [x.relative_to(ROOT) for x in ROOT.rglob("*")
            if x.is_file() and not any(q in IGNORED for q in x.relative_to(ROOT).parts)]

def scan_file(name: Path, raw: bytes):
    findings = []
    relative = name.as_posix()
    if BAD_NAME.search(relative):
        findings.append("sensitive filename")
    if len(raw) > 3_000_000:
        findings.append("oversized/unreviewed file")
    if b"\0" in raw or any(name.suffix.lower() == e for e in (".jpg", ".png", ".gif", ".pdf", ".zip")):
        return findings
    text = raw.decode("utf-8", "replace")
    if relative == "scripts/scan_secrets.py":
        return findings  # scanner's own detection regexes are not secrets
    for label, expr in BAD_LINES:
        if expr.search(text):
            findings.append(label)
    # Public-cloud IPv4 addresses must never appear in tracked examples.
    for candidate in set(IPV4.findall(text)):
        try:
            addr = ipaddress.ip_address(candidate)
            if addr.is_global:
                findings.append("public IP address")
                break
        except ValueError:
            pass
    return findings

def main():
    errors = []
    use_index=(ROOT / ".git").is_dir()
    for rel in candidates():
        full = ROOT / rel
        if full.is_symlink():
            errors.append((rel.as_posix(), ["tracked symlinks forbidden"]))
            continue
        if use_index:
            # Git index is the actual commit payload, which can differ from
            # working-tree bytes. Fail closed when staged data cannot be read.
            staged=subprocess.run(["git","show",":"+rel.as_posix()],
                                  cwd=ROOT,capture_output=True)
            if staged.returncode:
                errors.append((rel.as_posix(),["cannot inspect staged file"]))
                continue
            data=staged.stdout
        else:
            if not full.is_file():
                continue
            data=full.read_bytes()
        issues = scan_file(rel, data)
        if issues:
            errors.append((rel.as_posix(), issues))
    for name, issues in errors:
        print("REJECT", name, ", ".join(issues))
    if errors:
        print("SECRET_SCAN_FAILED", len(errors))
        return 1
    print("SECRET_SCAN_OK", len(candidates()), "files")
    return 0

if __name__ == "__main__":
    sys.exit(main())
