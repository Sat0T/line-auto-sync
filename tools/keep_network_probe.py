"""One read-only lock-network check; does not collect, send, or modify LINE state."""
from pathlib import Path
import os
import plistlib
import sys

LABEL = 'com.sat0t.line-auto-sync.keep-network-probe'
BASE = Path('/var/mobile/las-keep-lock-power-r3')
PROBE = r'''
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json, os, sys, time, urllib.request
sys.path.insert(0, HELPER_DIR)
import keep_power_lock_r3 as trial
if os.getuid() != 501:
    raise RuntimeError('mobile_required')
os.umask(0o077)
path = Path('/var/mobile/las-keep-lock-power-r3/network-probe.jsonl')
with path.open('x', encoding='utf-8') as stream:
    def log(event, **fields):
        print(json.dumps(dict(jst=datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds'),
                              event=event, **fields)), file=stream, flush=True)
    try:
        with trial.stay_awake():
            started = time.monotonic()
            for offset in (0, 50, 85):
                time.sleep(max(0, started + offset - time.monotonic()))
                try:
                    with urllib.request.urlopen('https://www.apple.com/library/test/success.html', timeout=8) as response:
                        log('network', offset=offset, ok=response.status == 200)
                except Exception as exc:
                    log('network', offset=offset, ok=False, error=type(exc).__name__)
    except Exception as exc:
        log('error', error=type(exc).__name__)
    finally:
        log('finished')
'''


def main():
    if os.getuid() != 501 or BASE.is_symlink() or not BASE.is_dir():
        raise RuntimeError('existing_mobile_trial_required')
    if (BASE / 'network-probe.jsonl').exists():
        raise RuntimeError('previous_probe_requires_review')
    helper = Path('keep_power_lock_r3.py').resolve(strict=True)
    code = 'HELPER_DIR = ' + repr(str(helper.parent)) + '\n' + PROBE
    compile(code, '<network-probe>', 'exec')
    os.umask(0o077)
    with Path('las-network-probe.plist').open('xb') as stream:
        plistlib.dump({'Label': LABEL, 'UserName': 'mobile', 'RunAtLoad': True,
                      'Umask': 0o077,
                      'ProgramArguments': ['/var/jb/usr/bin/python3', '-c', code]}, stream)
    print('NETWORK_PROBE_PREPARED_NOT_STARTED')


if __name__ == '__main__':
    main()
