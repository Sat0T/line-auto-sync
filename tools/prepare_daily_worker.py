"""Prepare, but never install/start, one collect-only rootless launchd job."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re

LABEL = 'com.sat0t.line-auto-sync.daily-collect'
RUNTIME_FILES = ('checkpoint.py', 'cli.py', 'delivery_state.py', 'legacy_names.py',
                 'normal_hold.py', 'presentation.py', 'recovery.py', 'resolver.py',
                 'runner.py', 'runtime_paths.py', 'sender.py', 'setup_normal.py',
                 'sync.py', 'transport.py')

ENTRY = r'''
import hashlib,json,os,pathlib,sys
PROFILE=pathlib.Path(PROFILE_PATH)
RUNTIME=pathlib.Path(RUNTIME_PATH)
def load():return json.loads((PROFILE/'config/config.json').read_text(encoding='utf-8'))
def binding(value):
    value=dict(value);value['enabled']=False
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
def inactive(value):return value.get('enabled') is not True or value.get('normal_line',{}).get('enabled') is not True
def main():
    config=load()
    if inactive(config):
        print('DAILY_COLLECT: disabled',flush=True);return 0
    if binding(config)!=PROFILE_BINDING:
        print('DAILY_COLLECT: profile_changed',flush=True);return 0
    for name,digest in RUNTIME_HASHES.items():
        path=RUNTIME/name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('runtime_version_changed')
    os.environ['LINE_AUTO_SYNC_BASE_DIR']=str(PROFILE)
    os.environ['LINE_AUTO_SYNC_CONFIG_DIR']=str(PROFILE/'config')
    os.environ['LINE_AUTO_SYNC_STATE_DIR']=str(PROFILE/'state')
    sys.path.insert(0,str(RUNTIME))
    import cli,runner
    try:runner.start(cli.doctor,cli.queue_status,send=False)
    except RuntimeError:
        current=load()
        if inactive(current) or binding(current)!=PROFILE_BINDING:return 0
        raise
    return 0
if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:raise SystemExit(0)
    except Exception as error:
        print('DAILY_COLLECT: failed '+type(error).__name__,file=sys.stderr,flush=True)
        try:
            import syslog
            syslog.syslog(syslog.LOG_ERR,'LINE Auto Sync daily collect failed: '+type(error).__name__)
        except Exception:pass
        raise SystemExit(1)
'''


def normalized_binding(config):
    config = dict(config); config['enabled'] = False
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def overlaps(left, right):
    return left == right or left in right.parents or right in left.parents


def prepare(profile, stage, output):
    profile, stage, output = Path(profile).absolute(), Path(stage).absolute(), Path(output).absolute()
    if any(p.is_symlink() for p in (profile, stage, output)) or output.exists():
        raise ValueError('new_nonlinked_output_required')
    config_path = profile / 'config/config.json'
    if (config_path.is_symlink() or not config_path.is_file()
            or (profile / 'config').is_symlink() or (profile / 'state').is_symlink()
            or not (profile / 'state').is_dir()):
        raise ValueError('existing_stopped_profile_required')
    config = json.loads(config_path.read_text(encoding='utf-8'))
    chats = config.get('normal_line', {}).get('allowed_chat_pks')
    if (config.get('schema_version') != 1 or config.get('enabled') is not False
            or config.get('normal_line', {}).get('enabled') is not True
            or config.get('source_id') != 'MVP_IPAD_LIVE'
            or not isinstance(chats, list) or len(chats) != 1
            or type(chats[0]) is not int or not 0 < chats[0] <= 2**63 - 1):
        raise ValueError('disabled_single_authorized_chat_profile_required')
    marker = profile / 'state/worker-status.json'
    if marker.is_symlink() or (marker.is_file() and json.loads(marker.read_text()).get('state') == 'running'):
        raise ValueError('confirm_previous_worker_stopped')
    database = Path(config['line_db']).resolve(strict=True)
    if not database.is_file():
        raise ValueError('line_database_file_required')
    pointer = profile / 'state/dbpath'
    if (pointer.is_symlink() or not pointer.is_file()
            or Path(pointer.read_text().strip()).resolve(strict=True) != database):
        raise ValueError('profile_database_pointer_mismatch')
    runtime = stage / 'runtime'
    resolved = (profile.resolve(), stage.resolve(), output.resolve(), database.parent)
    if (any(overlaps(resolved[i], resolved[j]) for i, j in ((0, 1), (0, 2), (1, 2)))
            or any(overlaps(p, database.parent) for p in resolved[:3])):
        raise ValueError('separate_profile_runtime_output_and_line_directory_required')
    manifest_path = stage / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or runtime.is_symlink():
        raise ValueError('verified_managed_stage_required')
    manifest = json.loads(manifest_path.read_text())
    hashes = {}
    for name in RUNTIME_FILES:
        path = runtime / name; digest = manifest.get('runtime/' + name)
        if (path.is_symlink() or not path.is_file() or not isinstance(digest, str)
                or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ValueError('staged_runtime_version_mismatch')
        hashes[name] = digest
    source = ('PROFILE_PATH=' + repr(str(profile)) + '\nRUNTIME_PATH=' + repr(str(runtime))
              + '\nPROFILE_BINDING=' + repr(normalized_binding(config))
              + '\nRUNTIME_HASHES=' + repr(hashes) + '\n' + ENTRY)
    compile(source, '<daily-collect-entry>', 'exec')
    spec = {'Label': LABEL, 'UserName': 'mobile', 'RunAtLoad': True,
            'KeepAlive': {'SuccessfulExit': False}, 'ThrottleInterval': 60,
            'Umask': 0o077, 'ExitTimeOut': 20,
            'EnvironmentVariables': {'HOME': '/var/mobile'},
            'ProgramArguments': ['/var/jb/usr/bin/python3', str(output / 'daily-collect.py')]}
    os.umask(0o077)
    output.mkdir(mode=0o700)
    for name, raw in (('daily-collect.py', source.encode('utf-8')),
                      ('daily-collect.plist', plistlib.dumps(spec))):
        with (output / name).open('xb') as stream:stream.write(raw)
    plan = {'mode': 'collect_only', 'installed': False, 'started': False,
            'label': LABEL, 'runtime_manifest_verified': True,
            'entry_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
            'profile_binding': normalized_binding(config), 'runtime_hashes': hashes,
            'restart_only_after_unsuccessful_exit': True, 'throttle_seconds': 60,
            'new_persistent_stdout_stderr_files': False}
    with (output / 'plan.json').open('x', encoding='utf-8') as stream:json.dump(plan, stream, indent=2)
    return {'prepared': True, 'mode': 'collect_only', 'installed': False, 'started': False,
            'external_send': False, 'runtime_manifest_verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.profile, args.stage, args.output)))


if __name__ == '__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'prepared': False, 'reason': str(error) if isinstance(error, ValueError) else type(error).__name__,
                          'installed': False, 'started': False, 'preserve_existing_state': True}))
        raise SystemExit(1)
