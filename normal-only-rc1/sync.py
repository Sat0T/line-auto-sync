"""Read-only LINE snapshot to a durable local queue before sending."""
from pathlib import Path
import sqlite3
import subprocess
import sys
from delivery_state import DeliveryError, DeliveryState
from runtime_paths import STATE_DIR, require_enabled


def collect():
    config = require_enabled()
    queue = DeliveryState(STATE_DIR)
    connection = None
    try:
        with queue.transaction():
            queue.bind(config)
            database = Path(config['line_db']).resolve(strict=True)
            pointer = STATE_DIR / 'dbpath'
            if pointer.is_symlink() or Path(pointer.read_text().strip()).resolve(strict=True) != database:
                raise DeliveryError('database_pointer_mismatch')
            connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=3)
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            high = connection.execute('SELECT COALESCE(MAX(Z_PK),0) FROM ZMESSAGE').fetchone()[0]
            low = queue.initialize_cursor(high)
            if high < low:
                raise DeliveryError('source_database_rewound')
            rows = connection.execute('''SELECT Z_PK,ZTIMESTAMP,ZCHAT,ZSENDER,ZTEXT
                FROM ZMESSAGE WHERE Z_PK>? AND Z_PK<=? AND length(ZTEXT)>0 ORDER BY Z_PK''', (low, high))
            count = queue.insert_rows(rows)
            queue.set('collected', high)
        return dict(queue.status(), added=count)
    finally:
        if connection is not None:
            connection.close()
        queue.close()


def main():
    print('COLLECT', collect())
    subprocess.run([sys.executable, str(Path(__file__).with_name('sender.py'))], check=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('ERROR: collection_or_delivery_failed', file=sys.stderr)
        raise SystemExit(1)
