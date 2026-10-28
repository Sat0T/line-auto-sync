"""Manual, disabled-by-default CLI for the experimental normal-LINE adapter.

Do not use with production data until iOS/LINE schema and endpoint are verified.
"""
import datetime as dt
import getpass
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

from runtime_paths import CONFIG_DIR, CONFIG_FILE, STATE_DIR, require_enabled, valid_endpoint

DB_POINTER = "dbpath"
REQUIRED_MESSAGE_COLUMNS = {"Z_PK", "ZTIMESTAMP", "ZCHAT", "ZSENDER", "ZTEXT"}


def save_config(config):
    if CONFIG_DIR.is_symlink() or CONFIG_FILE.is_symlink():
        raise RuntimeError("Symlinked configuration path is not allowed")
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = CONFIG_DIR / (".config-" + uuid.uuid4().hex)
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, CONFIG_FILE)
        os.chmod(CONFIG_FILE, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def initialize():
    from setup_normal import initialize as setup
    setup()


def configure():
    if not CONFIG_FILE.is_file() or CONFIG_FILE.is_symlink():
        raise RuntimeError("Run init first; do not use symlinked configuration")
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if config.get("enabled") is True:
        raise RuntimeError("Disable synchronization before reconfiguration")
    if STATE_DIR.is_symlink():
        raise RuntimeError("Symlinked state directory is not allowed")
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    pointer = STATE_DIR / DB_POINTER
    if pointer.exists() or pointer.is_symlink():
        raise RuntimeError("Database pointer exists; refusing to overwrite")

    print("Configure normal LINE synchronization (not yet enabling it).")
    print("Only select a database you are authorized to process.")
    endpoint = input("Your HTTPS endpoint: ").strip()
    source_id = input("Unique source_id (letters, digits, ._-): ").strip()
    db_input = input("Absolute LINE SQLite database path: ").strip()
    token = getpass.getpass("Authentication token (hidden): ").strip()

    if not valid_endpoint(endpoint):
        raise RuntimeError("HTTPS endpoint required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", source_id) or "REPLACE" in source_id:
        raise RuntimeError("Invalid source_id")
    if not token:
        raise RuntimeError("Authentication token required")

    db_path = Path(db_input).expanduser()
    if not db_path.is_absolute() or not db_path.is_file():
        raise RuntimeError("Existing absolute LINE database path required")
    db_path = db_path.resolve(strict=True)

    # These values are also required by legacy_names.Session/Resolver.
    # unix_ms is a hypothesis; doctor() must check real timestamps before enable.
    config["source_id"] = source_id
    config["line_db"] = str(db_path)
    config["group_db"] = None  # Optional external group database is not configured.
    config["timestamp_unit"] = "unix_ms"
    config["transport"] = {"endpoint": endpoint, "token": token}
    config["enabled"] = False
    config.setdefault("normal_line", {})["enabled"] = True

    # Create pointer first; if config save fails, remove only our new pointer.
    fd = os.open(str(pointer), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(str(db_path) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        save_config(config)
    except BaseException:
        pointer.unlink(missing_ok=True)
        raise
    print("Configuration saved and DISABLED. Run doctor before enable.")


def doctor():
    if not CONFIG_FILE.is_file() or CONFIG_FILE.is_symlink():
        raise RuntimeError("Configuration missing or symlinked")
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise RuntimeError("Unsupported configuration version")
    if config.get("timestamp_unit") != "unix_ms":
        raise RuntimeError("Timestamp unit is not configured as unix_ms")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(config.get("source_id", ""))):
        raise RuntimeError("Invalid or missing source_id")
    if not STATE_DIR.is_dir() or STATE_DIR.is_symlink():
        raise RuntimeError("State folder missing or symlinked")

    pointer = STATE_DIR / DB_POINTER
    if not pointer.is_file() or pointer.is_symlink():
        raise RuntimeError("Database pointer missing or symlinked")
    db_path = Path(pointer.read_text(encoding="utf-8").strip())
    if not db_path.is_absolute() or not db_path.is_file():
        raise RuntimeError("LINE SQLite database not found")
    resolved = db_path.resolve(strict=True)
    if config.get("line_db") != str(resolved):
        raise RuntimeError("Database pointer and configured database mismatch")

    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        columns = {row[1] for row in connection.execute(
            'PRAGMA table_info("ZMESSAGE")'
        )}
        if not REQUIRED_MESSAGE_COLUMNS <= columns:
            raise RuntimeError("Unsupported ZMESSAGE schema")
        sample = connection.execute(
            "SELECT ZTIMESTAMP FROM ZMESSAGE WHERE ZTIMESTAMP IS NOT NULL "
            "ORDER BY Z_PK DESC LIMIT 1"
        ).fetchone()
        if sample is None:
            raise RuntimeError("Cannot verify timestamp unit on an empty database")
        value = sample[0]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError("Unexpected timestamp type")
        try:
            year = dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).year
        except (OverflowError, OSError, ValueError):
            raise RuntimeError("Timestamp is not a valid unix_ms value") from None
        if not 2000 <= year <= 2100:
            raise RuntimeError("Timestamp may not be Unix milliseconds")
    finally:
        connection.close()

    print("PASS: read-only database, ZMESSAGE columns and timestamp sample")
    print("Configuration enabled:", config.get("enabled") is True)
    if config.get("enabled") is True:
        require_enabled()
        print("PASS: enabled configuration")
    else:
        print("INFO: synchronization remains disabled")
    print("NOTICE: other LINE tables, names and GAS behavior are NOT verified")
    return True


def enable():
    doctor()
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if not config.get("transport", {}).get("token"):
        raise RuntimeError("Missing authentication token")
    if not valid_endpoint(config.get("transport", {}).get("endpoint")):
        raise RuntimeError("Missing HTTPS endpoint")
    config["enabled"] = True
    save_config(config)
    try:
        require_enabled()
    except Exception:
        config["enabled"] = False
        save_config(config)
        raise
    print("ENABLED for manual use; no scheduled service is installed")


def disable():
    if not CONFIG_FILE.is_file():
        raise RuntimeError("Configuration missing")
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    config["enabled"] = False
    save_config(config)
    print("DISABLED")


def run():
    require_enabled()
    doctor()
    subprocess.run([sys.executable, str(Path(__file__).with_name("sync.py"))], check=True)


def status():
    from delivery_state import APPLICATION_ID
    path = STATE_DIR / 'normal-delivery.sqlite'
    if path.is_symlink() or STATE_DIR.is_symlink():
        raise RuntimeError('unsafe_state_path')
    if not path.exists():
        print(json.dumps({'queue_initialized': False}))
        return
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        if connection.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
            raise RuntimeError('foreign_delivery_database')
        meta = dict(connection.execute('SELECT key,value FROM meta'))
        result = {'queue_initialized': True,
                  'collected': int(meta.get('collected', '0')),
                  'acknowledged': int(meta.get('acked', '0')),
                  'pending': connection.execute('SELECT COUNT(*) FROM messages WHERE acked=0').fetchone()[0],
                  'frozen': bool(connection.execute('SELECT 1 FROM delivery').fetchone()),
                  'success_at': meta.get('success_at')}
        print(json.dumps(result))
    finally:
        connection.close()


def main():
    os.umask(0o077)
    actions = {
        "init": initialize, "configure": configure, "doctor": doctor,
        "enable": enable, "disable": disable, "run": run, "status": status,
    }
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        print("Usage: line-auto-sync init|configure|doctor|enable|disable|run|status")
        raise SystemExit(2)
    actions[sys.argv[1]]()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not print user-supplied credentials or exception stack traces.
        print("ERROR: operation_failed (" + type(exc).__name__ + ")", file=sys.stderr)
        raise SystemExit(1)
