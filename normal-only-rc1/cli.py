
import getpass
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

from runtime_paths import (
    CONFIG_DIR, CONFIG_FILE, STATE_DIR, require_enabled
)

DB_POINTER = 'dbpath'

def save_config(config):
    if CONFIG_FILE.is_symlink():
        raise RuntimeError("Symlinked configuration is not allowed")
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

    temporary = CONFIG_DIR / (".config-" + uuid.uuid4().hex)
    fd = os.open(
        str(temporary),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600
    )
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
    from setup_normal import initialize
    initialize()

def configure():
    if not CONFIG_FILE.is_file():
        raise RuntimeError("Run init first")
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    print("Configure normal LINE synchronization")
    print("The token is requested without terminal echo.")

    endpoint = input("HTTPS endpoint: ").strip()
    source_id = input("source_id: ").strip()
    db_path = input("Absolute LINE SQLite path: ").strip()
    token = getpass.getpass("Authentication token: ").strip()

    if not endpoint.startswith("https://"):
        raise RuntimeError("HTTPS endpoint required")
    if not source_id or not token:
        raise RuntimeError("source_id and token required")
    if not Path(db_path).is_file():
        raise RuntimeError("LINE database file not found")

    config["transport"] = {
        "endpoint": endpoint,
        "token": token
    }
    config["source_id"] = source_id
    config["enabled"] = False
    config.setdefault("normal_line", {})["enabled"] = True
    save_config(config)

    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    pointer = STATE_DIR / DB_POINTER

    if pointer.is_symlink():
        raise RuntimeError("Symlinked database pointer not allowed")

    if pointer.exists():
        raise RuntimeError(
            "Database pointer already exists; refusing overwrite"
        )

    fd = os.open(
        str(pointer),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600
    )
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(db_path + "\n")

    print("Configuration saved; synchronization remains DISABLED")
    print("Review settings, then use enable and doctor")

def doctor():
    if not CONFIG_FILE.is_file():
        raise RuntimeError("Missing config.json")
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    if not STATE_DIR.is_dir():
        raise RuntimeError("State folder missing")

    pointer = STATE_DIR / DB_POINTER
    if not pointer.is_file():
        raise RuntimeError("Database pointer missing")

    db_path = Path(pointer.read_text().strip())
    if not db_path.is_file():
        raise RuntimeError("LINE SQLite database not found")

    connection = sqlite3.connect(
        db_path.resolve().as_uri() + "?mode=ro",
        uri=True
    )
    try:
        connection.execute("SELECT name FROM sqlite_master LIMIT 1")
    finally:
        connection.close()

    print("PASS: state/config directory and SQLite readable")
    print("Configuration enabled:", config.get("enabled") is True)

    if config.get("enabled") is True:
        require_enabled()
        print("PASS: configuration validation")
    else:
        print("INFO: intentionally disabled")
    return True

def enable():
    doctor()
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    config["enabled"] = True
    save_config(config)
    try:
        require_enabled()
    except Exception:
        config["enabled"] = False
        save_config(config)
        raise
    print("ENABLED: manual run is now permitted")

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
    import subprocess
    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("sync.py"))],
        check=True
    )

def main():
    os.umask(0o077)
    if len(sys.argv) != 2:
        print("Usage: line-auto-sync init|configure|doctor|enable|disable|run")
        raise SystemExit(2)

    actions = {
        "init": initialize,
        "configure": configure,
        "doctor": doctor,
        "enable": enable,
        "disable": disable,
        "run": run
    }

    action = actions.get(sys.argv[1])
    if action is None:
        raise RuntimeError("Unknown command")
    action()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", exc, file=sys.stderr)
        raise SystemExit(1)
