import json
import os
from runtime_paths import CONFIG_DIR, CONFIG_FILE, STATE_DIR

def initialize():
    for folder in (CONFIG_DIR, STATE_DIR):
        if folder.is_symlink():
            raise RuntimeError("Symlinked directory not allowed")
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)

    if CONFIG_FILE.exists() or CONFIG_FILE.is_symlink():
        raise RuntimeError("Configuration already exists; no overwrite")

    settings = {
        "schema_version": 1,
        "source_id": "",
        "enabled": False,
        "normal_line": {"enabled": True},
        "openchat": {
            "enabled": False,
            "selected_chat_ids": []
        },
        "transport": {
            "endpoint": "",
            "token": ""
        },
        "schedule": {"interval_seconds": 120}
    }

    fd = os.open(
        str(CONFIG_FILE),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600
    )

    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(settings, stream, ensure_ascii=False, indent=2)
        stream.write("\n")

    print("INITIALIZATION COMPLETE")
    print("Configuration enabled: NO")
    print("Existing state deleted: NO")

if __name__ == "__main__":
    initialize()
