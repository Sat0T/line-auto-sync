import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

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


def valid_endpoint(endpoint):
    if not isinstance(endpoint, str) or any(c in endpoint for c in '\r\n\t'):
        return False
    try:
        url = urlsplit(endpoint)
        return bool(url.scheme == 'https' and url.hostname and not url.username
                    and not url.password and not url.fragment and url.port != 0)
    except ValueError:
        return False

def allowed_chat_pks(config):
    raw = config.get("normal_line", {}).get("allowed_chat_pks")
    if raw is None:
        if config.get("source_id") == "MVP_IPAD_LIVE":
            raise ValueError("live_source_requires_chat_allowlist")
        return None
    if (not isinstance(raw, list) or not 1 <= len(raw) <= 100
            or any(type(pk) is not int or not 0 < pk <= 2**63 - 1 for pk in raw)
            or len(set(raw)) != len(raw)):
        raise ValueError("invalid_chat_allowlist")
    return tuple(sorted(raw))


def require_enabled():
    if CONFIG_DIR.is_symlink() or CONFIG_FILE.is_symlink() or STATE_DIR.is_symlink():
        raise RuntimeError('unsafe_runtime_symlink')
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

    if not valid_endpoint(endpoint):
        raise RuntimeError("A valid HTTPS endpoint is required")

    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("A nonempty authentication token is required")

    source_id = config.get("source_id")
    if not isinstance(source_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', source_id) or 'REPLACE' in source_id:
        raise RuntimeError("source_id is required")

    if not STATE_DIR.is_dir():
        raise RuntimeError("State directory has not been initialized")

    allowed_chat_pks(config)
    return config

def read_credential(name):
    if name not in ("endpoint", "token"):
        raise ValueError("Unsupported credential key")
    return require_enabled()["transport"][name]
