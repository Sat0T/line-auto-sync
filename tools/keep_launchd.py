"""Generate transient launchd jobs for a dedicated, single-chat Keep profile."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import sys

LABEL = 'com.sat0t.line-auto-sync.keep'
PYTHON = '/var/jb/usr/bin/python3'
CLI = '/var/jb/usr/libexec/line-auto-sync/cli.py'
BASE = Path('/var/mobile/las-keep-daily')
# Kept in ProgramArguments so the probe does not need a script installed as root.
PROBE = '''
from contextlib import closing
import json, os, sqlite3, time
from pathlib import Path
base = Path(os.environ['LINE_AUTO_SYNC_BASE_DIR'])
os.umask(0o077)
if os.getuid() == 0:
    raise SystemExit('mobile_required')
with (base / 'lock-probe.jsonl').open('x', encoding='utf-8') as out:
    for _ in range(42):
        tick = {'at': int(time.time())}
        try:
            config = json.loads((base / 'config/config.json').read_text())
            chat, = config['normal_line']['allowed_chat_pks']
            db = Path(config['line_db']).resolve(strict=True)
            with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=2)) as c:
                tick['keep_high'] = c.execute(
                    'SELECT MAX(Z_PK) FROM ZMESSAGE WHERE ZCHAT=? AND length(ZTEXT)>0',
                    (chat,)
                ).fetchone()[0]
        except Exception as exc:
            tick['db_error'] = type(exc).__name__
        out.write(json.dumps(tick) + '\\n')
        out.flush()
        time.sleep(5)
'''


def job(mode):
    if mode == 'probe':
        args = [PYTHON, '-c', PROBE]
    elif mode in ('collect', 'send', 'resume'):
        args = [PYTHON, CLI, 'start']
        if mode != 'collect':
            args.append('--send')
        if mode == 'resume':
            args.append('--resume-pending')
    else:
        raise ValueError('unknown_mode')
    return {
        'Label': LABEL, 'ProgramArguments': args, 'UserName': 'mobile',
        'RunAtLoad': True, 'Umask': 0o077,
        'EnvironmentVariables': {'HOME': '/var/mobile', 'LINE_AUTO_SYNC_BASE_DIR': str(BASE)},
    }


def validate_daily():
    config_file = BASE / 'config/config.json'
    if any(path.is_symlink() for path in (BASE, config_file.parent, config_file, BASE / 'state')):
        raise RuntimeError('unsafe_daily_profile')
    config = json.loads(config_file.read_text(encoding='utf-8'))
    chats = config['normal_line']['allowed_chat_pks']
    baseline = json.loads((BASE.parent / 'las-rc6-keep-check/config/config.json').read_text(encoding='utf-8'))
    if (config['source_id'] != 'MVP_IPAD_LIVE' or len(chats) != 1
            or baseline['normal_line']['allowed_chat_pks'] != chats
            or baseline['line_db'] != config['line_db']
            or type(chats[0]) is not int or chats[0] <= 0):
        raise RuntimeError('daily_profile_must_be_keep_only')
    return config


def prepare():
    if BASE.exists() or BASE.is_symlink():
        raise FileExistsError('daily_profile_already_exists')
    os.environ['LINE_AUTO_SYNC_BASE_DIR'] = str(BASE)
    for key in ('LINE_AUTO_SYNC_CONFIG_DIR', 'LINE_AUTO_SYNC_STATE_DIR'):
        os.environ.pop(key, None)
    sys.path.insert(0, str(Path(CLI).parent))
    import cli
    import runtime_paths
    import sync
    from rc6_keep_check import prepare_trial
    prepare_trial(BASE, cli, sync, runtime_paths)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'probe', 'collect', 'send', 'resume'))
    parser.add_argument('output', type=Path, nargs='?')
    args = parser.parse_args()
    if args.action == 'prepare':
        if args.output:
            parser.error('prepare takes no output path')
        prepare()
        return
    if not args.output:
        parser.error('provide a new output plist path')
    validate_daily()
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        plistlib.dump(job(args.action), stream)


if __name__ == '__main__':
    main()
