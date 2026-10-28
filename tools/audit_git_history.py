#!/usr/bin/env python3
"""Read-only Git history audit: scan all reachable blobs and commit metadata.

Never prints captured secrets, actual email addresses or matching source lines.
Does not rewrite history, install software, read live LINE/GAS or send network data.
"""
import argparse
from collections import Counter
from pathlib import Path
import re
import subprocess
import sys

PATTERNS = {
    "github_token": re.compile(rb"gh(?:p|o|u|s|r)_[A-Za-z0-9_]{20,}"),
    "google_api_key": re.compile(rb"AIza[0-9A-Za-z_-]{30,}"),
    "openai_api_key": re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{25,}"),
    "aws_access_key": re.compile(rb"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "google_gas_deployment": re.compile(
        rb"https://script\.google\.com/macros/s/[A-Za-z0-9_-]{18,}/exec"),
    "private_windows_home": re.compile(rb"[A-Za-z]:\\Users\\[^\s\\]{2,}\\"),
    "generic_credential_assignment": re.compile(
        rb"(?im)^\s*(?:export\s+)?(?:TOKEN|PASSWORD|API_KEY|CLIENT_SECRET)\s*=\s*"
        rb"['\"]?(?!REPLACE|EXAMPLE|DUMMY|SYNTHETIC|OFFLINE)[A-Za-z0-9_\-]{25,}"),
}
EMAIL = re.compile(rb"(?i)\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b")
PUBLIC_PLACEHOLDERS = {b"example.com", b"example.org", b"example.net",
                       b"example.invalid", b"localhost", b"invalid"}

def scan_blob(data):
    """Return *categories only*. Never return a captured token or message."""
    flags = set()
    for category, pattern in PATTERNS.items():
        if pattern.search(data):
            flags.add(category)
    for match in EMAIL.finditer(data):
        if match.group(1).lower() not in PUBLIC_PLACEHOLDERS:
            flags.add("email_address_in_content")
            break
    return flags

def git(*args):
    return subprocess.run(["git", *args], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True).stdout

def audit(repo):
    if not (repo / ".git").exists():
        raise RuntimeError("Expected Git checkout with history; not a source ZIP")
    objects = git("-C", str(repo), "rev-list", "--objects", "--all").splitlines()
    stats = Counter()
    suspects = Counter()
    blob_count = 0
    max_size = 16 * 1024 * 1024
    for line in objects:
        sha = line.split(b" ", 1)[0].decode("ascii")
        info = git("-C", str(repo), "cat-file", "-s", sha).strip()
        obj_type = git("-C", str(repo), "cat-file", "-t", sha).strip()
        if obj_type != b"blob":
            continue
        blob_count += 1
        size = int(info)
        if size > max_size:
            stats["large_or_uninspected_blob"] += 1
            continue
        raw = git("-C", str(repo), "cat-file", "blob", sha)
        if b"\x00" in raw:
            stats["binary_blob"] += 1
        # Scan binary blobs for exact byte-patterns too; this is not full
        # decompilation or semantic review of historical .pyc files.
        for flag in scan_blob(raw):
            suspects[flag] += 1
    email_list = git("-C", str(repo), "log", "--all",
                     "--format=%ae%n%ce").decode("utf-8", "replace").splitlines()
    published = {e.strip() for e in email_list if e.strip() and
                 not e.lower().endswith("@users.noreply.github.com")}
    commit_count = len(git("-C", str(repo), "rev-list", "--all").splitlines())
    print("HISTORY AUDIT: read-only")
    print("COMMITS:", commit_count)
    print("BLOBS CHECKED:", blob_count)
    print("BINARY BLOBS:", stats["binary_blob"])
    print("TOO-LARGE/UNINSPECTED BLOBS:", stats["large_or_uninspected_blob"])
    print("DISTINCT NON-NOREPLY COMMIT EMAILS:", len(published))
    print("CONTENT FLAG CATEGORIES:", ",".join(sorted(suspects)) or "none")
    print("FLAGGED BLOBS BY CATEGORY:",
          ",".join("%s:%s" % (k, v) for k, v in sorted(suspects.items())) or "none")
    print("CAVEAT: regex/history check is NOT a complete secrets, rights, or PII audit")
    # Known local paths and email-shaped content are privacy review flags,
    # whereas detected credential material is an immediate technical failure.
    # A green workflow never constitutes approval to distribute.
    credential_categories = set(PATTERNS) - {"private_windows_home"}
    critical = {k: v for k, v in suspects.items() if k in credential_categories}
    review = {k: v for k, v in suspects.items() if k not in credential_categories}
    print("CREDENTIAL_PATTERN_GATE:", "FAIL" if critical else "NO_MATCH")
    print("HISTORICAL_PRIVACY_REVIEW:", "REQUIRED" if review or published else "NONE_FLAGGED")
    if critical or stats["large_or_uninspected_blob"]:
        return 2
    return 0

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        return audit(args.repo.resolve())
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        print("HISTORY AUDIT: could not inspect all reachable Git objects (" +
              type(exc).__name__ + ")", file=sys.stderr)
        return 3

if __name__ == "__main__":
    sys.exit(main())
