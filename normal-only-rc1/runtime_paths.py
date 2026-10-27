import json
import os
from pathlib import Path

BASE_DIR = Path(os.environ.get(
    "LINE_AUTO_SYNC_BASE_DIR",
    "/var/mobile/Library/Application Support/LineAutoSync"
))
STATE_DIR = Path(os.environ.get(
    "LINE_AUTO_SYNC_STATE_DIR", str(BASE_DIR / "state")
))
CONFIG_DIR = Path(os.environ.get(
    "LINE_AUTO_SYNC_CONFIG_DIR", str(BASE_DIR / "config")
))
CONFIG_FILE = CONFIG_DIR / "config.json"

def require_enabled():
    if not CONFIG_FILE.is_file():
        raise RuntimeError("Initial configuration is missing")

    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    if config.get("schema_version") != 1:
        raise RuntimeError("Unsupported configuration version")

    normal = config.get("normal_line", {})
    if config.get("enabled") is not True or normal.get("enabled") is not True:
        raise RuntimeError("Normal LINE synchronization is disabled")

    transport = config.get("transport", {})
    endpoint = transport.get("endpoint")
    token = transport.get("token")

    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        raise RuntimeError("A valid HTTPS endpoint is required")

    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("A nonempty authentication token is required")

    source_id = config.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise RuntimeError("source_id is required")

    if not STATE_DIR.is_dir():
        raise RuntimeError("State directory has not been initialized")

    return config

def read_credential(name):
    if name not in ("endpoint", "token"):
        raise ValueError("Unsupported credential key")
    return require_enabled()["transport"][name]
