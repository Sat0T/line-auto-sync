"""Stage one owner-approved onboarding bundle from iCloud into a fresh home directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re


def locate(name, roots=None):
    if not re.fullmatch(r'LAS-Private-Onboarding-[0-9]{8}', name):
        raise ValueError('fixed_bundle_name_required')
    if roots is None:
        roots = [Path('/var/mobile/Library/Mobile Documents/com~apple~CloudDocs')]
        groups = Path('/var/mobile/Containers/Shared/AppGroup')
        roots.extend(groups.glob('*/File Provider Storage'))
    matches = []
    seen = set()
    for root in roots:
        candidate = root / name
        if (candidate / 'SHA256.json').is_file():
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved); matches.append(candidate)
    if len(matches) != 1:
        raise ValueError('one_downloaded_icloud_bundle_required')
    return matches[0]


def stage(source, target, expected):
    source, target = Path(source), Path(target)
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise ValueError('new_home_directory_required')
    manifest_path = source / 'SHA256.json'
    manifest_raw = manifest_path.read_bytes()
    if len(manifest_raw) > 100000 or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('manifest_invalid')
    if hashlib.sha256(manifest_raw).hexdigest() != expected:
        raise ValueError('manifest_version_mismatch')
    manifest = json.loads(manifest_raw)
    if not isinstance(manifest, dict) or not 21 <= len(manifest) <= 23:
        raise ValueError('manifest_members_invalid')
    data = {}
    pattern = r'(enrollment/(pc\.pub|SHA256\.json|enroll_ipad_key\.py|ipad_connection_offer\.py)|kit/(tools|runtime)/[A-Za-z0-9_-]+\.py)'
    required = {'enrollment/pc.pub', 'enrollment/enroll_ipad_key.py',
                'enrollment/ipad_connection_offer.py', 'kit/tools/root_bootstrap.py'}
    if not required.issubset(manifest):
        raise ValueError('required_members_missing')
    for name, digest in manifest.items():
        if not re.fullmatch(pattern, name) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('unsafe_manifest_member')
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('regular_downloaded_file_required')
        raw = path.read_bytes()
        if len(raw) > 1000000 or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('bundle_file_version_mismatch')
        data[name] = raw
    if sum(map(len, data.values())) > 5000000:
        raise ValueError('bundle_too_large')
    os.umask(0o077)
    target.mkdir(mode=0o700)
    for name, raw in data.items():
        path = target / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
    with (target / 'SHA256.json').open('xb') as stream:
        stream.write(manifest_raw)
    return {'owner_bundle_verified': True, 'files': len(data), 'new_home_staged': True,
            'ssh_key_registered': False, 'job_started': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle-name', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    if not hasattr(os, 'getuid') or os.getuid() != 501:
        raise ValueError('mobile_user_required')
    target = Path('/var/mobile/las-ipad-onboarding-20261125')
    print(json.dumps(stage(locate(args.bundle_name), target, args.manifest_sha256)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'owner_bundle_verified': False,
                          'reason': str(error) if isinstance(error, ValueError) else type(error).__name__,
                          'preserve_existing_files': True, 'automatic_retry': False}))
        raise SystemExit(1)
