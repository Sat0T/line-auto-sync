"""Prepare private owner-transfer files or accept an independently verified iPad offer.

Local files and ssh-keygen fingerprint checks only. No connection, key generation,
TOFU, service start, or replacement of existing files.
"""
import argparse
import base64
import hashlib
import hmac
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile


def _support(name):
    spec = importlib.util.spec_from_file_location('connection_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manager = _support('manage_ipad')
enrollment = _support('enroll_ipad_key')
RUNTIME_FILES = ('checkpoint.py', 'cli.py', 'delivery_state.py', 'legacy_names.py',
                 'normal_hold.py', 'presentation.py', 'recovery.py', 'resolver.py',
                 'runner.py', 'runtime_paths.py', 'sender.py', 'setup_normal.py',
                 'sync.py', 'transport.py')
TOOL_FILES = ('keep_lock_delivery.py', 'keep_power_lock.py', 'observe_line_db.py', 'root_bootstrap.py')
ENROLLMENT_FILES = ('SHA256.json', 'enroll_ipad_key.py', 'pc.pub')
PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(value) for value in
                         ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


class PreparationError(RuntimeError):
    pass


def _plain(path, directory=False):
    path = Path(path)
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
        raise PreparationError('redirected_input_refused')
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise PreparationError('directory_required')
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PreparationError('regular_unlinked_file_required')
    return info


def _read(path, limit=2000000):
    before = _plain(path)
    if before.st_size > limit:
        raise PreparationError('input_too_large')
    raw = Path(path).read_bytes()
    after = _plain(path)
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        raise PreparationError('input_changed_during_read')
    return raw


def _tree(directory):
    directory = Path(directory)
    _plain(directory, True)
    files = {}
    def visit(parent):
        for path in parent.iterdir():
            if len(path.relative_to(directory).parts) > 2 or len(files) >= 32:
                raise PreparationError('unexpected_nested_or_large_input_tree')
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                _plain(path, True)
                visit(path)
            else:
                _plain(path)
                files[path.relative_to(directory).as_posix()] = path
    visit(directory)
    return files


def _new_directory(output, protect):
    output = Path(output).absolute()
    try:
        output.lstat()
    except FileNotFoundError:
        pass
    else:
        raise PreparationError('new_private_output_directory_required')
    _plain(output.parent, True)
    output.mkdir(mode=0o700)
    protect(output, True)
    return output


def _write_new(path, raw, protect):
    path = Path(path)
    with os.fdopen(os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    protect(path, False)


def package(enrollment_dir, kit_dir, output, *, offer_helper=None, source_root=None,
            protect=manager.protect_private_path):
    """Copy fixed enrollment and reviewed 18-file kit without changing either."""
    enrolled = _tree(enrollment_dir)
    kit = _tree(kit_dir)
    expected_kit = {'runtime/' + name for name in RUNTIME_FILES} | {'tools/' + name for name in TOOL_FILES}
    if set(enrolled) != set(ENROLLMENT_FILES) or set(kit) != expected_kit:
        raise PreparationError('fixed_enrollment_three_and_kit_eighteen_required_no_secret_files')
    manifest_raw = _read(enrolled['SHA256.json'], 16384)
    previous = json.loads(manifest_raw)
    if not isinstance(previous, dict) or set(previous) != {'pc.pub', 'enroll_ipad_key.py'}:
        raise PreparationError('invalid_enrollment_manifest')
    payload = {'enrollment/SHA256.json': manifest_raw}
    for name in ('pc.pub', 'enroll_ipad_key.py'):
        raw = _read(enrolled[name])
        if not re.fullmatch(r'[0-9a-f]{64}', str(previous[name])) or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), previous[name]):
            raise PreparationError('enrollment_version_mismatch')
        payload['enrollment/' + name] = raw
    enrollment.parse_public_key(payload['enrollment/pc.pub'])
    source_root = Path(__file__).resolve().parents[1] if source_root is None else Path(source_root)
    for relative, path in kit.items():
        raw = _read(path)
        if relative == 'tools/root_bootstrap.py':
            reviewed = manager.root_setup_source().encode('utf-8')
        else:
            folder, name = relative.split('/')
            reviewed = _read(source_root / ('normal-only-rc1' if folder == 'runtime' else 'tools') / name)
        if raw != reviewed:
            raise PreparationError('kit_version_mismatch_prepare_new_reviewed_kit')
        payload['kit/' + relative] = raw
    if offer_helper is not None:
        offer_helper = Path(offer_helper)
        if offer_helper.name != 'ipad_connection_offer.py':
            raise PreparationError('fixed_offer_helper_basename_required')
        raw = _read(offer_helper)
        if raw != _read(source_root / 'tools/ipad_connection_offer.py'):
            raise PreparationError('offer_helper_version_mismatch')
        payload['enrollment/ipad_connection_offer.py'] = raw
    output = _new_directory(output, protect)
    for name, raw in sorted(payload.items()):
        path = output / name
        if not path.parent.exists():
            path.parent.mkdir(mode=0o700, parents=True)
        _write_new(path, raw, protect)
    manifest = {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(payload.items())}
    _write_new(output / 'SHA256.json', (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode(), protect)
    return {'private_owner_bundle_prepared': True, 'payload_files': len(payload),
            'manifest_files': len(manifest), 'connected': False, 'existing_files_changed': False}


def _fingerprint(public_key):
    encoded = enrollment.parse_public_key(public_key)
    return 'SHA256:' + base64.b64encode(hashlib.sha256(base64.b64decode(encoded)).digest()).decode().rstrip('=')


def accept_offer(offer_file, expected_fingerprint, identity_file, output, *, owner_confirmed=False,
                 execute=subprocess.run, which=shutil.which, protect=manager.protect_private_path,
                 verify_identity=manager.verify_private_path):
    """Bind only an independently confirmed public host key; do not contact it."""
    if not owner_confirmed:
        raise PreparationError('independent_owner_fingerprint_confirmation_required')
    if not isinstance(expected_fingerprint, str) or not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', expected_fingerprint):
        raise PreparationError('invalid_expected_fingerprint')
    offer = json.loads(_read(offer_file, 32768))
    if (not isinstance(offer, dict) or type(offer.get('version')) is not int or offer['version'] != 1
            or type(offer.get('uid')) is not int or offer['uid'] != 501 or offer.get('os_version') != '16.7.14'):
        raise PreparationError('expected_ipad_environment_required')
    try:
        if not isinstance(offer.get('ipv4'), str):
            raise ValueError('string_required')
        address = ipaddress.IPv4Address(offer.get('ipv4'))
    except (ValueError, TypeError):
        raise PreparationError('rfc1918_ipv4_required') from None
    if not any(address in network for network in PRIVATE_NETWORKS):
        raise PreparationError('rfc1918_ipv4_required')
    port = offer.get('port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise PreparationError('invalid_ssh_port')
    if not isinstance(offer.get('host_public_key'), str) or not isinstance(offer.get('host_key_fingerprint'), str):
        raise PreparationError('public_host_key_and_fingerprint_required')
    try:
        encoded = enrollment.parse_public_key(offer['host_public_key'].encode('ascii'))
    except (UnicodeError, enrollment.EnrollmentError):
        raise PreparationError('native_ed25519_public_key_required') from None
    public = ('ssh-ed25519 ' + encoded + '\n').encode('ascii')
    computed = _fingerprint(public)
    if not hmac.compare_digest(expected_fingerprint, computed) or not hmac.compare_digest(offer['host_key_fingerprint'], computed):
        raise PreparationError('independent_host_fingerprint_mismatch')
    identity = Path(identity_file).expanduser().absolute()
    _plain(identity)
    verify_identity(identity, False)  # Read-only; never repair/replace the existing key here.
    keygen = which('ssh-keygen')
    if not keygen:
        raise PreparationError('existing_ssh_keygen_required')
    # Inspect a public half only. Neither the private host key nor the PC private
    # key is opened by this process or passed to ssh-keygen.
    with tempfile.TemporaryDirectory(prefix='las-offer-public-check-') as temporary:
        temporary = Path(temporary)
        protect(temporary, True)
        public_file = temporary / 'host.pub'
        _write_new(public_file, public, protect)
        try:
            result = execute([keygen, '-lf', str(public_file), '-E', 'sha256'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            raise PreparationError('local_public_fingerprint_check_unavailable') from None
        fingerprints = re.findall(rb'\bSHA256:[A-Za-z0-9+/]{43}\b', result.stdout)
        if result.returncode or len(fingerprints) != 1 or not hmac.compare_digest(fingerprints[0].decode(), computed):
            raise PreparationError('local_public_fingerprint_mismatch')
    output = _new_directory(output, protect)
    host = str(address) if port == 22 else '[' + str(address) + ']:' + str(port)
    _write_new(output / 'known_hosts', (host + ' ssh-ed25519 ' + encoded + '\n').encode(), protect)
    connection = {'destination': 'mobile@' + str(address), 'port': port,
                  'known_hosts': str(output / 'known_hosts'), 'identity_file': str(identity),
                  'device_expected_uid': 501, 'device_expected_os_version': '16.7.14',
                  'owner_confirmed_via_separate_channel': True}
    _write_new(output / 'connection.json', (json.dumps(connection, indent=2) + '\n').encode(), protect)
    _write_new(output / 'verification.json', (json.dumps({'version': 1, 'independent_host_key_verified': True,
        'native_public_fingerprint_verified': True, 'server_listening_verified': False,
        'ssh_authenticated': False, 'connected': False}, indent=2) + '\n').encode(), protect)
    return {'private_connection_prepared': True, 'independent_host_key_verified': True,
            'connected': False, 'server_listening_verified': False, 'ssh_authenticated': False,
            'existing_identity_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    pack = commands.add_parser('package', help='Copy verified enrollment 3 + reviewed kit 18 into a fresh private bundle; no connection')
    pack.add_argument('--enrollment', type=Path, required=True)
    pack.add_argument('--kit', type=Path, required=True)
    pack.add_argument('--offer-helper', type=Path)
    pack.add_argument('--output', type=Path, required=True)
    accept = commands.add_parser('accept-offer', help='Create new private known_hosts/config only after independent owner fingerprint confirmation; no connection')
    accept.add_argument('--offer', type=Path, required=True)
    accept.add_argument('--expected-fingerprint', required=True)
    accept.add_argument('--owner-confirmed', action='store_true')
    accept.add_argument('--identity-file', type=Path, required=True)
    accept.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == 'package':
        result = package(args.enrollment, args.kit, args.output, offer_helper=args.offer_helper)
    else:
        result = accept_offer(args.offer, args.expected_fingerprint, args.identity_file,
                              args.output, owner_confirmed=args.owner_confirmed)
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'prepared': False, 'error': str(error) if isinstance(error, PreparationError)
                          else type(error).__name__, 'connected': False, 'automatic_retry': False}))
        raise SystemExit(1)
