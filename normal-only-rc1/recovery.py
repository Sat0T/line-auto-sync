"""Private snapshot of this collector's config and queue, without LINE data access."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing

from runner import worker_lock
from runtime_paths import CONFIG_FILE, CONFIG_DIR, STATE_DIR
from sender import sender_lock

STATE_FILES = ('dbpath', 'normal-delivery.sqlite', 'named-cache.sqlite', 'new.csv', 'lastid',
               'sentid', 'initial_scan_id', 'sent_at')


def snapshot(destination):
    if CONFIG_DIR.is_symlink() or CONFIG_FILE.is_symlink() or not CONFIG_FILE.is_file():
        raise RuntimeError('unsafe_snapshot_config')
    destination = Path(destination).absolute()
    resolved = destination.resolve()
    if any(root.resolve() == resolved or root.resolve() in resolved.parents
           for root in (CONFIG_DIR, STATE_DIR)):
        raise RuntimeError('snapshot_must_be_outside_runtime')
    with worker_lock(), sender_lock(STATE_DIR):
        config = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        if config.get('enabled') is not False:
            raise RuntimeError('disable_before_snapshot')
        sources = [(CONFIG_FILE, 'config.json')]
        for name in STATE_FILES:
            path = STATE_DIR / name
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise RuntimeError('unsafe_snapshot_state')
            if path.is_file():
                sources.append((path, name))
        destination.mkdir(mode=0o700)
        manifest = {}
        for source, name in sources:
            target = destination / name
            if name.endswith('.sqlite'):
                with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
                    with closing(sqlite3.connect(target)) as dst:
                        os.chmod(target, 0o600)
                        src.backup(dst)
            else:
                fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as out:
                    out.write(source.read_bytes())
            manifest[name] = hashlib.sha256(target.read_bytes()).hexdigest()
        (destination / 'snapshot.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print('Private snapshot complete. Keep it offline and separate from package files.')
