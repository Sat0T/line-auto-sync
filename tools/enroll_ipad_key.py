"""Owner-run, append-only enrollment of one restricted public key on an iPad."""
import argparse
import base64
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import stat
import struct

SSHD_PATHS = ('/var/jb/usr/sbin/sshd', '/usr/sbin/sshd')
HOST_PUBLIC_PATHS = ('/var/jb/etc/ssh/ssh_host_ed25519_key.pub',
                     '/etc/ssh/ssh_host_ed25519_key.pub')


class EnrollmentError(RuntimeError):
    pass


def current_uid():
    return os.getuid() if hasattr(os, 'getuid') else None


def doctor():
    """Check availability only; never read private host keys or start a service."""
    def public_file_exists(name):
        try:
            return stat.S_ISREG(Path(name).lstat().st_mode)
        except OSError:
            return False
    result = {
        'platform_supported': os.name == 'posix' and hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'),
        'mobile_uid': current_uid() == 501,
        'timezone_is_jst': datetime.now().astimezone().utcoffset() == timedelta(hours=9),
        'sshd_binary_available': any(Path(p).is_file() and os.access(p, os.X_OK) for p in SSHD_PATHS),
        'public_host_key_available': any(public_file_exists(p) for p in HOST_PUBLIC_PATHS),
    }
    result['ready_for_owner_enrollment'] = all(result.values())
    result['server_listening_verified'] = False
    return result


def parse_public_key(raw):
    """Accept exactly one native OpenSSH Ed25519 public-key line, not options."""
    if len(raw) > 8192 or b'PRIVATE KEY' in raw:
        raise EnrollmentError('invalid_public_key')
    try:
        text = raw.decode('ascii')
    except UnicodeError:
        raise EnrollmentError('invalid_public_key') from None
    lines = text.splitlines()
    if len(lines) != 1 or any(ord(c) < 32 and c != '\t' for c in lines[0]):
        raise EnrollmentError('invalid_public_key')
    match = re.fullmatch(r'ssh-ed25519[ \t]+([A-Za-z0-9+/]+={0,2})(?:[ \t]+[^\r\n]*)?', lines[0])
    if not match:
        raise EnrollmentError('invalid_public_key')
    encoded = match.group(1)
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise EnrollmentError('invalid_public_key') from None
    expected_prefix = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32)
    if len(decoded) != len(expected_prefix) + 32 or not decoded.startswith(expected_prefix):
        raise EnrollmentError('invalid_public_key')
    if base64.b64encode(decoded).decode('ascii') != encoded:
        raise EnrollmentError('invalid_public_key')
    return encoded


def check_directory(info, uid, *, ssh=False):
    mode = stat.S_IMODE(info.st_mode)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or mode & 0o022:
        raise EnrollmentError('unsafe_directory_review_required')
    if ssh and mode != 0o700:
        raise EnrollmentError('ssh_directory_permissions_review_required')


def check_file(info, uid, *, authorized=False):
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1:
        raise EnrollmentError('unsafe_file_review_required')
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o022 or authorized and mode != 0o600:
        raise EnrollmentError('file_permissions_review_required')


def read_bounded(fd, limit):
    data = bytearray()
    while True:
        piece = os.read(fd, min(65536, limit + 1 - len(data)))
        if not piece:
            return bytes(data)
        data.extend(piece)
        if len(data) > limit:
            raise EnrollmentError('file_too_large_review_required')


def read_public_under_home(home_fd, relative, uid):
    parts = relative.parts
    if not parts or any(p in ('', '.', '..') for p in parts):
        raise EnrollmentError('public_key_path_outside_home')
    directory = os.dup(home_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_fd
            check_directory(os.fstat(directory), uid)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            check_file(os.fstat(fd), uid)
            raw = read_bounded(fd, 8192)
            return parse_public_key(raw)
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def enroll(public_key_file, home=None):
    if not doctor()['ready_for_owner_enrollment']:
        raise EnrollmentError('existing_mobile_ssh_environment_required')
    if os.name != 'posix':
        raise EnrollmentError('posix_required')
    import fcntl
    uid = current_uid()
    home = Path.home() if home is None else Path(home)
    home = home.absolute()
    source = Path(public_key_file).expanduser().absolute()
    try:
        relative = source.relative_to(home)
    except ValueError:
        raise EnrollmentError('public_key_path_outside_home') from None
    # /var may be the native OS alias; every component below the selected home
    # is opened relative to a checked descriptor with O_NOFOLLOW.
    home_fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    ssh_fd = key_fd = None
    try:
        check_directory(os.fstat(home_fd), uid)
        encoded = read_public_under_home(home_fd, relative, uid)
        try:
            os.mkdir('.ssh', mode=0o700, dir_fd=home_fd)
        except FileExistsError:
            pass
        ssh_fd = os.open('.ssh', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home_fd)
        check_directory(os.fstat(ssh_fd), uid, ssh=True)
        try:
            key_fd = os.open('authorized_keys', os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW,
                             dir_fd=ssh_fd)
        except FileNotFoundError:
            key_fd = os.open('authorized_keys', os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=ssh_fd)
        check_file(os.fstat(key_fd), uid, authorized=True)
        fcntl.flock(key_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old = read_bounded(key_fd, 1024 * 1024)
        # Find the same blob even with existing options; do not add a duplicate
        # or silently rewrite an existing less-restricted authorization.
        needle = re.compile(rb'(?:^|[ \t])ssh-ed25519[ \t]+' + re.escape(encoded.encode()) + rb'(?:[ \t]|$)')
        for line in old.splitlines():
            if needle.search(line):
                restricted = line.startswith(b'restrict ssh-ed25519 ')
                return {'changed': False, 'key_present': True, 'restricted': restricted,
                        'requires_review': not restricted, 'existing_bytes_preserved': True}
        # Recheck identities before append. Never replace an existing file.
        expected_dir = os.fstat(ssh_fd)
        linked_dir = os.stat('.ssh', dir_fd=home_fd, follow_symlinks=False)
        expected_key = os.fstat(key_fd)
        linked_key = os.stat('authorized_keys', dir_fd=ssh_fd, follow_symlinks=False)
        if ((linked_dir.st_dev, linked_dir.st_ino) != (expected_dir.st_dev, expected_dir.st_ino)
                or (linked_key.st_dev, linked_key.st_ino) != (expected_key.st_dev, expected_key.st_ino)):
            raise EnrollmentError('authorization_path_changed')
        check_directory(linked_dir, uid, ssh=True)
        check_file(linked_key, uid, authorized=True)
        added = (b'' if not old or old.endswith(b'\n') else b'\n')
        added += b'restrict ssh-ed25519 ' + encoded.encode() + b' line-auto-sync-pc\n'
        view = memoryview(added)
        while view:
            written = os.write(key_fd, view)
            if written <= 0:
                raise EnrollmentError('append_incomplete_review_required')
            view = view[written:]
        os.fsync(key_fd)
        os.fsync(ssh_fd)
        return {'changed': True, 'key_present': True, 'restricted': True,
                'requires_review': False, 'existing_bytes_preserved': True}
    finally:
        if key_fd is not None:
            os.close(key_fd)
        if ssh_fd is not None:
            os.close(ssh_fd)
        os.close(home_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='doctor', choices=['doctor', 'enroll'])
    parser.add_argument('--public-key-file', type=Path)
    args = parser.parse_args()
    if args.action == 'doctor':
        print(json.dumps(doctor()))
    elif args.public_key_file is None:
        parser.error('--public-key-file required for enroll')
    else:
        print(json.dumps(enroll(args.public_key_file)))


if __name__ == '__main__':
    try:
        main()
    except (EnrollmentError, OSError):
        # No filenames, key material, host identifiers or raw exception strings.
        print(json.dumps({'enrolled': False, 'error': 'owner_review_required'}))
        raise SystemExit(1) from None
