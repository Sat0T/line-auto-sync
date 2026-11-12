"""Run the RC6 Keep Memo check in a new, offline profile."""
from contextlib import closing
import getpass
import json
import os
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

BASE_MARKER = 'LAS-KEEP-6D19-BASE'
CHECK_MARKER = 'LAS-KEEP-6D19-CHECK'
SOURCE = 'RC6_SCOPE_CHECK'
ENDPOINT = 'https://example.invalid/'


def readonly(path):
    return closing(sqlite3.connect(path.resolve(strict=True).as_uri() + '?mode=ro', uri=True))


def setup(profile, cli, sync):
    paths = {p.read_text(encoding='utf-8').strip()
             for p in Path.home().glob('las-rc5-check.*/state/dbpath') if not p.is_symlink()}
    if len(paths) != 1:
        raise RuntimeError('expected_one_diagnostic_database')
    database = Path(next(iter(paths)))
    with readonly(database) as conn:
        matches = conn.execute('SELECT DISTINCT ZCHAT FROM ZMESSAGE WHERE ZTEXT=?',
                               (BASE_MARKER,)).fetchall()
    if len(matches) != 1 or type(matches[0][0]) is not int or matches[0][0] <= 0:
        raise RuntimeError('keep_marker_missing_or_ambiguous')
    profile.mkdir(mode=0o700)
    cli.initialize()
    with patch('builtins.input', side_effect=[ENDPOINT, SOURCE, str(database)]), \
            patch.object(getpass, 'getpass', return_value='offline-check-only'):
        cli.configure()
    cli.restrict_chats([str(matches[0][0])])
    cli.enable()
    try:
        if sync.collect()['pending'] != 0:
            raise RuntimeError('expected_empty_baseline')
    finally:
        cli.disable()
    print('KEEP_BASELINE_READY')


def check(cli, sync, runtime):
    config = json.loads(runtime.CONFIG_FILE.read_text(encoding='utf-8'))
    chats = runtime.allowed_chat_pks(config)
    if (config.get('source_id') != SOURCE or config.get('enabled') is not False
            or config['transport']['endpoint'] != ENDPOINT or len(chats or ()) != 1):
        raise RuntimeError('expected_disabled_offline_profile')
    cli.enable()
    try:
        collected = sync.collect()
        with readonly(runtime.STATE_DIR / 'normal-delivery.sqlite') as conn:
            rows = [json.loads(row[0]) for row in conn.execute('SELECT raw FROM messages')]
        hits = sum(row[4] == CHECK_MARKER for row in rows)
        allowed = all(row[2] == str(chats[0]) for row in rows)
        print(json.dumps({'pending': collected['pending'], 'keep_marker': hits,
                          'all_rows_in_scope': allowed, 'external_send': False}))
        if hits != 1 or not allowed:
            raise RuntimeError('keep_scope_not_confirmed')
    finally:
        cli.disable()


def main():
    if sys.argv[1:] not in (['setup'], ['check']):
        raise ValueError('expected_setup_or_check')
    profile = Path.home() / 'las-rc6-keep-check'
    if profile.is_symlink():
        raise RuntimeError('symlinked_check_profile')
    for key in ('LINE_AUTO_SYNC_STATE_DIR', 'LINE_AUTO_SYNC_CONFIG_DIR'):
        os.environ.pop(key, None)
    os.environ['LINE_AUTO_SYNC_BASE_DIR'] = str(profile)
    sys.path.insert(0, '/var/jb/usr/libexec/line-auto-sync')
    import cli
    import runtime_paths
    import sync
    if sys.argv[1] == 'setup':
        setup(profile, cli, sync)
    else:
        check(cli, sync, runtime_paths)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('KEEP_CHECK_ERROR:', type(exc).__name__, file=sys.stderr)
        sys.exit(1)
