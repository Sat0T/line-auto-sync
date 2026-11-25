"""Owner-run read-only discovery of an existing rootless OpenSSH endpoint.

No login, service start, installation, private-key read, or LINE access.
The optional offer is private onboarding metadata, never public evidence.
"""
import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess

SUPPORTED_OS = '16.7.14'
SSHD = Path('/var/jb/usr/sbin/sshd')
PLIST = Path('/var/jb/Library/LaunchDaemons/com.openssh.sshd.plist')
HOST_PUBLIC = Path('/var/jb/etc/ssh/ssh_host_ed25519_key.pub')


class OfferError(RuntimeError):
    pass


def private_ipv4(value):
    address = ipaddress.ip_address(value)
    ranges = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
    if address.version != 4 or not any(address in ipaddress.ip_network(n) for n in ranges):
        raise OfferError('private_ipv4_required')
    return str(address)


def socket_ports(spec):
    ports = set()
    for value in spec.get('Sockets', {}).values():
        values = value if isinstance(value, list) else [value]
        for item in values:
            service = item.get('SockServiceName')
            if service == 'ssh':
                ports.add(22)
            elif str(service).isdigit() and 1 <= int(service) <= 65535:
                ports.add(int(service))
    return sorted(ports)


def discover(execute=subprocess.run):
    uid = os.getuid() if hasattr(os, 'getuid') else None
    version_file = Path('/System/Library/CoreServices/SystemVersion.plist')
    version = plistlib.loads(version_file.read_bytes()).get('ProductVersion')
    if uid != 501 or version != SUPPORTED_OS:
        raise OfferError('target_environment_not_verified')
    present = {'sshd': SSHD.is_file(), 'launchd_plist': PLIST.is_file(),
               'public_host_key': HOST_PUBLIC.is_file() and not HOST_PUBLIC.is_symlink()}
    ports = socket_ports(plistlib.loads(PLIST.read_bytes())) if present['launchd_plist'] else []
    return {'version': 1, 'uid': uid, 'os_version': version, 'components': present,
            'socket_ports_observed': ports,
            'device_jst': datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds'),
            'server_listening_verified': False, 'ssh_authenticated': False}


def make_offer(output, *, execute=subprocess.run, info=None, public_path=None, ipv4=None):
    info = discover(execute) if info is None else info
    if info.get('uid') != 501 or info.get('os_version') != SUPPORTED_OS:
        raise OfferError('target_environment_not_verified')
    if not all(info.get('components', {}).get(k) is True for k in ('sshd', 'launchd_plist', 'public_host_key')):
        raise OfferError('existing_openssh_components_required')
    if 22 not in info.get('socket_ports_observed', []):
        raise OfferError('expected_ssh_socket_not_observed')
    output = Path(output)
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise OfferError('new_private_offer_path_required')
    public_path = HOST_PUBLIC if public_path is None else Path(public_path)
    if public_path.is_symlink() or not public_path.is_file():
        raise OfferError('public_host_key_required')
    # Reuse the strict public Ed25519 wire-format parser; never open a private key.
    from enroll_ipad_key import parse_public_key
    raw = public_path.read_bytes()
    encoded = parse_public_key(raw)
    keygen = shutil.which('ssh-keygen')
    if not keygen:
        raise OfferError('existing_ssh_keygen_required')
    result = execute([keygen, '-lf', str(public_path), '-E', 'sha256'],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    fingerprint = re.search(rb'\bSHA256:[A-Za-z0-9+/]{43}\b', result.stdout)
    if result.returncode or fingerprint is None:
        raise OfferError('public_fingerprint_unavailable')
    if ipv4 is None:
        ifconfig = shutil.which('ifconfig')
        if not ifconfig:
            raise OfferError('existing_ifconfig_required')
        interface = execute([ifconfig, 'en0'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        addresses = re.findall(rb'\binet\s+([0-9.]+)', interface.stdout)
        if interface.returncode or len(addresses) != 1:
            raise OfferError('single_wifi_ipv4_not_observed')
        ipv4 = addresses[0].decode('ascii')
    address = private_ipv4(ipv4)
    offer = dict(info, ipv4=address, port=22,
                 host_public_key='ssh-ed25519 ' + encoded,
                 host_key_fingerprint=fingerprint.group().decode('ascii'))
    with os.fdopen(os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as stream:
        json.dump(offer, stream, ensure_ascii=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    # Show this on the iPad itself for the owner's independent comparison.
    return {'offer_written': True, 'device_fingerprint_private': offer['host_key_fingerprint'],
            'server_listening_verified': False, 'ssh_authenticated': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('probe', 'offer'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.action == 'probe':
        print(json.dumps(discover()))
    elif args.output is None:
        parser.error('--output required')
    else:
        print(json.dumps(make_offer(args.output)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'offer_written': False, 'reason': str(error) if isinstance(error, OfferError) else type(error).__name__,
                          'no_service_or_line_change': True}))
        raise SystemExit(1)
