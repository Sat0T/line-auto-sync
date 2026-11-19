"""Prepare (never start) an isolated, send-free 180-second launchd observation."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import plistlib

LABEL = 'com.sat0t.line-auto-sync.db-observation-only'
SECONDS = 180


def prepare(profile, bundle):
    profile = Path(profile).resolve(strict=True)
    config_path = profile / 'config/config.json'
    raw = config_path.read_bytes()
    config = json.loads(raw)
    chats = config.get('normal_line', {}).get('allowed_chat_pks')
    if (config.get('enabled') is not False or config.get('source_id') != 'MVP_IPAD_LIVE'
            or not isinstance(chats, list) or len(chats) != 1 or type(chats[0]) is not int or chats[0] <= 0):
        raise RuntimeError('existing_disabled_single_chat_profile_required')
    if not (profile / 'state/normal-delivery.sqlite').is_file():
        raise RuntimeError('existing_queue_required')
    tools = Path(__file__).resolve().parent
    observer_source = (tools / 'observe_line_db.py').read_text(encoding='utf-8')
    helper_source = (tools / 'keep_power_lock.py').read_text(encoding='utf-8')
    # Reuse just the proven power assertion, never import or start a trial worker.
    power = next(node for node in ast.parse(helper_source).body
                 if isinstance(node, ast.FunctionDef) and node.name == 'stay_awake')
    power_source = '@contextmanager\n' + ast.get_source_segment(helper_source, power)
    bundle = Path(bundle).absolute()
    if bundle.is_symlink() or bundle.exists():
        raise RuntimeError('new_private_bundle_required')
    bundle = bundle.resolve()
    if bundle == profile or profile in bundle.parents:
        raise RuntimeError('bundle_must_be_outside_profile')
    database_parent = Path(config['line_db']).resolve(strict=True).parent
    if bundle == database_parent or database_parent in bundle.parents:
        raise RuntimeError('bundle_must_be_outside_line_directory')
    payload = '''import ctypes as C
from contextlib import contextmanager
import hashlib, json, os, sys
from pathlib import Path
SECONDS = 180
''' + power_source + '\n'
    payload += 'OBSERVER_SOURCE = ' + repr(observer_source) + '\n'
    payload += 'PROFILE = ' + repr(str(profile)) + '\n'
    payload += 'EXPECTED_CONFIG = ' + repr(hashlib.sha256(raw).hexdigest()) + '\n'
    payload += '''
def observe():
    if os.getuid() != 501:
        raise RuntimeError('mobile_required')
    os.umask(0o077)
    current = (Path(PROFILE) / 'config/config.json').read_bytes()
    if hashlib.sha256(current).hexdigest() != EXPECTED_CONFIG:
        raise RuntimeError('profile_changed_since_prepare')
    output = Path(__file__).resolve().parent / 'observations.jsonl'
    if output.exists() or output.is_symlink():
        raise RuntimeError('observation_already_started')
    namespace = {'__name__': 'readonly_observer'}
    exec(compile(OBSERVER_SOURCE, '<readonly-observer>', 'exec'), namespace)
    # No marker or real conversation text needs to be copied by the user.
    with stay_awake():
        namespace['main'](['--profile', PROFILE, '--scope-only', '--output', str(output),
                           '--seconds', '180', '--interval', '5', '--darwin-lock-signal'])

if __name__ == '__main__':
    try:
        observe()
    except Exception as error:
        print(json.dumps({'event': 'bootstrap_error', 'error': type(error).__name__}), flush=True)
        raise SystemExit(1) from None
'''
    compile(payload, '<observation-launcher>', 'exec')
    spec = {'Label': LABEL, 'UserName': 'mobile', 'RunAtLoad': True, 'KeepAlive': False,
            'Umask': 0o077, 'ExitTimeOut': 15,
            'EnvironmentVariables': {'HOME': '/var/mobile'},
            'ProgramArguments': ['/var/jb/usr/bin/python3', str(bundle / 'observation.py')],
            'StandardOutPath': str(bundle / 'console.jsonl'),
            'StandardErrorPath': str(bundle / 'console.jsonl')}
    os.umask(0o077)
    bundle.mkdir(mode=0o700)
    for name, content in [('observation.py', payload.encode()),
                          ('observation.plist', plistlib.dumps(spec)), ('console.jsonl', b'')]:
        with (bundle / name).open('xb') as stream:
            stream.write(content)
    return spec


def status(bundle):
    path = Path(bundle) / 'observations.jsonl'
    if not path.is_file():
        return {'started': False, 'check_private_console': True}
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    snapshots = [r for r in rows if r['event'] == 'snapshot']
    return {'started': True, 'stopped': bool(rows and rows[-1]['event'] == 'stopped'),
            'snapshots': len(snapshots),
            'read_errors': sum(r['event'] == 'db_read_error' for r in rows),
            'fatal_errors': sum(r['event'] == 'observation_error' for r in rows),
            'first_read_utc': snapshots[0]['read_started_utc'] if snapshots else None,
            'last_read_utc': snapshots[-1]['read_finished_utc'] if snapshots else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'status'])
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--bundle', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        if args.profile is None:
            parser.error('--profile required')
        prepare(args.profile, args.bundle)
        print('OBSERVATION_PREPARED_NOT_STARTED')
    else:
        print(json.dumps(status(args.bundle)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('OBSERVATION_ERROR:', type(error).__name__)
        raise SystemExit(1) from None
