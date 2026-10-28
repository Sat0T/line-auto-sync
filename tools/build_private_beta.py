#!/usr/bin/env python3
"""Offline-only PRIVATE rootless candidate builder, not an approved release."""
import ast
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "normal-only-rc1"
DIST = ROOT / "dist"
MODULES = (
    "checkpoint.py", "cli.py", "delivery_state.py", "legacy_names.py", "normal_hold.py",
    "presentation.py", "resolver.py", "runtime_paths.py", "sender.py",
    "setup_normal.py", "sync.py", "transport.py",
)
PACKAGE_NAME = "line-auto-sync_0.1.2-rc3_PRIVATE.deb"
APPROOT = "./var/jb/usr/libexec/line-auto-sync"
LAUNCHER = "./var/jb/usr/bin/line-auto-sync"
CONTROL = """Package: com.sat0t.line-auto-sync.private
Name: LINE Auto Sync Private Test Candidate
Version: 0.1.2~rc3
Architecture: iphoneos-arm64
Depends: firmware (>= 15.0)
Maintainer: Sat0T
Section: Utilities
Description: Unverified rootless normal LINE test candidate; manual only
"""
README = """LINE AUTO SYNC PRIVATE RC3 / NOT APPROVED FOR DISTRIBUTION
Generated reproducibly from reviewed source, not previous RC1/RC2 binaries.
Rootless /var/jb; manual Python 3; no automatic startup or Python bundled.
Commands: line-auto-sync init, configure, doctor, enable, run, status, disable.
Default state is DISABLED. Normal LINE only. OpenChat excluded.
Oversized heads stop delivery; a preceding valid prefix may be delivered.
The durable queue freezes retries. Plain OK does not prove receiver dedupe.
Legacy cursor files are import-only. Do not downgrade against new queue state.
Python 3.9+, sqlite3 and TLS must already be installed; provider package IDs
remain unverified. No launchd or root-running synchronization is installed.
Real LINE SQLite, real name lookup, GAS, device installation, permissions,
third-party code rights and full secret audit are NOT validated.
Do not install or redistribute until separate reviews and consent checks.
Do not send private conversation data without authorization.
"""
LAUNCHER_TEXT = """#!/bin/sh
umask 077
for PY in /var/jb/usr/bin/python3 /var/jb/usr/bin/python3.9; do
    if [ -x "$PY" ] && "$PY" -c 'import sys,sqlite3,ssl; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
        exec "$PY" /var/jb/usr/libexec/line-auto-sync/cli.py "$@"
    fi
done
echo "Compatible rootless Python not found" >&2
exit 1
"""

def stop(message):
    raise SystemExit("STOP: " + message)

def get_payloads():
    if not SOURCE.is_dir() or set(p.name for p in SOURCE.glob("*.py")) != set(MODULES):
        stop("Unexpected source module allowlist")
    entries = {}
    for name in MODULES:
        file = SOURCE / name
        if not file.is_file() or file.is_symlink():
            stop("Unexpected module file " + name)
        source = file.read_text(encoding="utf-8-sig")
        source = source.replace("\r\n", "\n").replace("\r", "\n")
        ast.parse(source, filename=name, feature_version=(3, 9))
        for pattern in ("script.google.com/macros/s/", "/private/preboot/",
                        "sender.py.before-names-", "ghp_", "AIza", "sk-proj-", "C:\\Users\\"):
            if pattern in source:
                stop("Suspicious literal needs manual review in " + name)
        entries[APPROOT + "/" + name] = (source.encode("utf-8"), 0o644)
    cli = (SOURCE / "cli.py").read_text(encoding="utf-8-sig")
    names = (SOURCE / "legacy_names.py").read_text(encoding="utf-8-sig")
    hold = (SOURCE / "normal_hold.py").read_text(encoding="utf-8-sig")
    for label, ok in (
        ("SQLite doctor", "REQUIRED_MESSAGE_COLUMNS" in cli),
        ("resolver config", '"timestamp_unit"' in cli and '"line_db"' in cli),
        ("state name cache", "STAGE = ROOT" in names),
        ("name failure safety", "name_enrichment_unavailable" in names),
        ("large message safety", 'raise RuntimeError("Oversized message:' in hold),
    ):
        if not ok:
            stop("Missing guard: " + label)
    entries[APPROOT + "/README_PRIVATE.txt"] = (README.encode("utf-8"), 0o644)
    entries[LAUNCHER] = (LAUNCHER_TEXT.encode("utf-8"), 0o755)
    return entries

def make_tar(entries):
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0, compresslevel=9) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tar:
            for name, (data, mode) in sorted(entries.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), mode
                info.uid, info.gid, info.mtime = 0, 0, 0
                info.uname, info.gname = "root", "root"
                tar.addfile(info, io.BytesIO(data))
    return output.getvalue()

def make_ar_member(name, blob):
    header = (f"{name + '/':<16}{0:<12}{0:<6}{0:<6}"
              f"{100644:<8}{len(blob):<10}" + chr(96) + "\n").encode("ascii")
    if len(header) != 60:
        stop("Wrong ar header size")
    return header + blob + (b"\n" if len(blob) % 2 else b"")

def verify(archive, expected):
    if not archive.startswith(b"!<arch>\n"):
        stop("Not an ar file")
    pos, members = 8, {}
    while pos < len(archive):
        header = archive[pos:pos+60]
        if len(header) != 60 or header[58:60] != bytes([96, 10]):
            stop("Bad ar header")
        name = header[:16].decode("ascii").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        pos += 60
        blob = archive[pos:pos+size]
        if len(blob) != size or name in members:
            stop("Bad ar payload")
        members[name] = blob
        pos += size + size % 2
    if pos != len(archive) or set(members) != {"debian-binary", "control.tar.gz", "data.tar.gz"}:
        stop("Unexpected .deb structure")
    if members["debian-binary"] != b"2.0\n":
        stop("Wrong debian-binary")
    with tarfile.open(fileobj=io.BytesIO(members['control.tar.gz']), mode='r:gz') as tar:
        controls = tar.getmembers()
        if len(controls) != 1 or controls[0].name != './control' or not controls[0].isfile():
            stop('Unexpected package control entries')
        if tar.extractfile(controls[0]).read() != CONTROL.encode():
            stop('Package control mismatch')
    with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"]), mode="r:gz") as tar:
        actual = {}
        for info in tar:
            if not info.isfile() or info.name in actual:
                stop("Unexpected data.tar entry")
            actual[info.name] = (tar.extractfile(info).read(), info.mode)
    if actual != expected:
        stop("Package does not match source payloads")

def main():
    os.umask(0o077)
    if sys.version_info < (3, 9):
        stop("Python 3.9 or later required")
    print("Running OFFLINE synthetic SQLite tests...", flush=True)
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                   cwd=str(ROOT), check=True)
    entries = get_payloads()
    archive = (b"!<arch>\n"
               + make_ar_member("debian-binary", b"2.0\n")
               + make_ar_member("control.tar.gz", make_tar({"./control": (CONTROL.encode(), 0o644)}))
               + make_ar_member("data.tar.gz", make_tar(entries)))
    verify(archive, entries)
    DIST.mkdir(exist_ok=True)
    target = DIST / PACKAGE_NAME
    report = DIST / "RC3_BUILD_REPORT.json"
    if target.exists() or target.is_symlink() or report.exists():
        stop("Refusing to overwrite existing build outputs")
    target.write_bytes(archive)
    sha = hashlib.sha256(target.read_bytes()).hexdigest()
    result = {
        "filename": target.name, "bytes": len(archive), "sha256": sha,
        "modules": len(MODULES), "offline_tests": "PASS",
        "archive_contents": "VERIFIED", "real_device": "NOT TESTED",
        "real_line_db": "NOT TESTED", "GAS_delivery": "NOT TESTED",
        "security_licensing_audit": "NOT COMPLETED",
        "runtime_dependency_package_ids": "NOT VERIFIED ON DEVICE",
        "distribution": "PRIVATE ONLY — DO NOT REDISTRIBUTE",
    }
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PRIVATE RC3 BUILD SUCCESS")
    print("SHA256:", sha)
    print("Build:", target)
    print("Report:", report)
    print("No iPad or real network accessed. NOT APPROVED FOR DISTRIBUTION.")

if __name__ == "__main__":
    main()
