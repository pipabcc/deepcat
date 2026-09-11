from __future__ import annotations

import json
import threading
from typing import Any, Optional

from deepcat.utils.paths import get_app_dir


DEFAULT_LOG_SETTINGS = {
    "app_log_enabled": False,
    "crash_log_enabled": False,
}

_CACHE_LOCK = threading.Lock()
_CACHE_SIGNATURE: Optional[tuple[str, int, int]] = None
_CACHE_VALUE: Optional[dict[str, bool]] = None


def normalize_log_settings(v: Any) -> dict[str, bool]:
    incoming = dict(v) if isinstance(v, dict) else {}
    return {
        key: bool(incoming.get(key)) if isinstance(incoming.get(key), bool) else bool(default)
        for key, default in DEFAULT_LOG_SETTINGS.items()
    }


def _parse_log_settings(path: Any) -> dict[str, bool]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return normalize_log_settings(None)
    if not isinstance(data, dict):
        return normalize_log_settings(None)
    ui = data.get("ui")
    if not isinstance(ui, dict):
        return normalize_log_settings(None)
    return normalize_log_settings(ui.get("logging"))


def read_log_settings() -> dict[str, bool]:
    global _CACHE_SIGNATURE, _CACHE_VALUE
    path = get_app_dir() / "settings.json"
    try:
        st = path.stat()
        signature: Optional[tuple[str, int, int]] = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        signature = None
    if signature is not None:
        with _CACHE_LOCK:
            if _CACHE_VALUE is not None and _CACHE_SIGNATURE == signature:
                return dict(_CACHE_VALUE)
    value = _parse_log_settings(path)
    if signature is not None:
        with _CACHE_LOCK:
            _CACHE_SIGNATURE = signature
            _CACHE_VALUE = dict(value)
    return value


def is_app_log_enabled() -> bool:
    return bool(read_log_settings().get("app_log_enabled", False))


def is_crash_log_enabled() -> bool:
    return bool(read_log_settings().get("crash_log_enabled", False))
