from __future__ import annotations

import atexit
import faulthandler
import importlib.metadata
import json
import logging
import os
import platform
import signal
import sys
import threading
import time
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

from deepcat.utils.log_settings import is_crash_log_enabled
from deepcat.utils.logger import get_log_dir


_LOCK = threading.RLock()
_CRASH_HANDLER: Optional["_CrashRotatingHandler"] = None
_INSTALLED = False
_ATEXIT_REGISTERED = False
_FAULTHANDLER_ENABLED = False
_SIGBREAK_REGISTERED = False
_SESSION_ID = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
_MAX_FIELD_LENGTH = 4000
_MAX_CRASH_LOG_BYTES = 2 * 1024 * 1024
_CRASH_LOG_BACKUP_COUNT = 3
_REDACTED = "<redacted>"
_SENSITIVE_NAME_TOKENS = ("api_key", "apikey", "token", "secret", "password", "passwd")


def _is_sensitive_name(name: str) -> bool:
    lowered = str(name or "").lower().replace("-", "_")
    return any(token in lowered for token in _SENSITIVE_NAME_TOKENS)


def redact_argv(argv: Any) -> list[str]:
    """脱敏命令行参数：--xxx-key VALUE 与 --xxx-key=VALUE 两种形式的值都替换掉。"""
    out: list[str] = []
    redact_next = False
    try:
        items = [str(item) for item in (argv or [])]
    except Exception:
        return []
    for text in items:
        if redact_next:
            out.append(_REDACTED)
            redact_next = False
            continue
        if text.startswith("-"):
            name, sep, _value = text.partition("=")
            if _is_sensitive_name(name):
                if sep:
                    out.append(f"{name}={_REDACTED}")
                else:
                    out.append(text)
                    redact_next = True
                continue
        out.append(text)
    return out


def redact_mapping(data: Any) -> dict[str, Any]:
    """脱敏字典：键名含 key/token/secret/password 的非空字符串值替换掉。"""
    try:
        items = dict(data or {})
    except Exception:
        return {}
    out: dict[str, Any] = {}
    for k, v in items.items():
        if _is_sensitive_name(str(k)) and isinstance(v, str) and v:
            out[str(k)] = _REDACTED
        else:
            out[str(k)] = v
    return out


class _CrashRotatingHandler(RotatingFileHandler):
    def doRollover(self) -> None:
        super().doRollover()
        # faulthandler 持有的是轮转前旧文件的 fd，必须重新挂到新 stream 上
        _rearm_faulthandler_locked(self)


def get_crash_log_file_path() -> Path:
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "crash.log"


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        text = value if isinstance(value, str) else None
    else:
        try:
            text = str(value)
        except Exception:
            text = repr(type(value))
    if isinstance(text, str) and len(text) > _MAX_FIELD_LENGTH:
        return text[:_MAX_FIELD_LENGTH] + "...<truncated>"
    return value if text is None else text


def _safe_json(data: dict[str, Any]) -> str:
    try:
        return json.dumps({k: _safe_value(v) for k, v in data.items()}, ensure_ascii=False, sort_keys=True)
    except Exception:
        return repr(data)


def _open_crash_handler() -> "_CrashRotatingHandler":
    global _CRASH_HANDLER
    if _CRASH_HANDLER is None:
        handler = _CrashRotatingHandler(
            str(get_crash_log_file_path()),
            maxBytes=_MAX_CRASH_LOG_BYTES,
            backupCount=_CRASH_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        _CRASH_HANDLER = handler
    return _CRASH_HANDLER


def _rearm_faulthandler_locked(handler: "_CrashRotatingHandler") -> None:
    if not _FAULTHANDLER_ENABLED:
        return
    try:
        faulthandler.enable(file=handler.stream, all_threads=True)
    except Exception:
        pass
    if _SIGBREAK_REGISTERED:
        try:
            sigbreak = getattr(signal, "SIGBREAK", None)
            register = getattr(faulthandler, "register", None)
            if sigbreak is not None and register is not None:
                register(sigbreak, file=handler.stream, all_threads=True, chain=False)
        except Exception:
            pass


def _write_line(line: str, *, flush: bool = True) -> None:
    # 绕过 handler.emit（它无条件 flush）：先做轮转检查再写 stream，
    # 高频 breadcrumb 可传 flush=False 省掉每行一次的写盘 syscall
    if not is_crash_log_enabled():
        return
    try:
        with _LOCK:
            handler = _open_crash_handler()
            record = logging.LogRecord(
                "deepcat.crash", logging.ERROR, __file__, 0, line.rstrip("\n"), None, None
            )
            if handler.shouldRollover(record):
                handler.doRollover()
            stream = handler.stream
            if stream is None:
                stream = handler._open()
                handler.stream = stream
            stream.write(handler.format(record) + "\n")
            if flush:
                handler.flush()
    except Exception:
        pass


def _package_version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except Exception:
        return ""


def _session_header() -> dict[str, Any]:
    info: dict[str, Any] = {
        "session": _SESSION_ID,
        "pid": os.getpid(),
        "ppid": getattr(os, "getppid", lambda: 0)(),
        "cwd": os.getcwd(),
        "argv": redact_argv(sys.argv),
        "executable": sys.executable,
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "thread": threading.current_thread().name,
        "main_thread_id": threading.main_thread().ident,
        "pyqt6": _package_version("PyQt6"),
        "qt6": _package_version("PyQt6-Qt6"),
        "numpy": _package_version("numpy"),
        "opencv_python": _package_version("opencv-python"),
        "opencv_contrib_python": _package_version("opencv-contrib-python"),
        "paddleocr": _package_version("paddleocr"),
        "paddlex": _package_version("paddlex"),
    }
    if os.name == "nt":
        try:
            info["win32_ver"] = platform.win32_ver()
        except Exception:
            pass
        try:
            info["windows_version"] = tuple(sys.getwindowsversion())
        except Exception:
            pass
    return info


def install_crash_reporter() -> None:
    global _ATEXIT_REGISTERED, _INSTALLED, _FAULTHANDLER_ENABLED, _SIGBREAK_REGISTERED
    if not is_crash_log_enabled():
        return
    with _LOCK:
        if not is_crash_log_enabled():
            return
        if _INSTALLED:
            return
        _INSTALLED = True
        handler = _open_crash_handler()
        _write_line("")
        _write_line("=" * 88)
        _write_line(f"[{_now()}] crash_reporter.session_start {_safe_json(_session_header())}")
        try:
            faulthandler.enable(file=handler.stream, all_threads=True)
            _FAULTHANDLER_ENABLED = True
            write_crash_breadcrumb("faulthandler.enabled", log_path=str(get_crash_log_file_path()))
        except Exception as e:
            write_crash_breadcrumb("faulthandler.enable_failed", error=repr(e))
        try:
            sigbreak = getattr(signal, "SIGBREAK", None)
            register = getattr(faulthandler, "register", None)
            if sigbreak is not None and register is not None:
                register(sigbreak, file=handler.stream, all_threads=True, chain=False)
                _SIGBREAK_REGISTERED = True
                write_crash_breadcrumb("faulthandler.sigbreak_registered")
        except Exception as e:
            write_crash_breadcrumb("faulthandler.sigbreak_register_failed", error=repr(e))
        if not _ATEXIT_REGISTERED:
            atexit.register(_on_process_exit)
            _ATEXIT_REGISTERED = True


def uninstall_crash_reporter() -> None:
    global _CRASH_HANDLER, _INSTALLED, _FAULTHANDLER_ENABLED, _SIGBREAK_REGISTERED
    with _LOCK:
        if not _INSTALLED and _CRASH_HANDLER is None:
            return
        try:
            faulthandler.disable()
        except Exception:
            pass
        try:
            sigbreak = getattr(signal, "SIGBREAK", None)
            unregister = getattr(faulthandler, "unregister", None)
            if sigbreak is not None and unregister is not None:
                unregister(sigbreak)
        except Exception:
            pass
        _FAULTHANDLER_ENABLED = False
        _SIGBREAK_REGISTERED = False
        try:
            if _CRASH_HANDLER is not None:
                _CRASH_HANDLER.close()
        except Exception:
            pass
        _CRASH_HANDLER = None
        _INSTALLED = False


def sync_crash_reporter_with_settings() -> None:
    if is_crash_log_enabled():
        install_crash_reporter()
    else:
        uninstall_crash_reporter()


def _on_process_exit() -> None:
    write_crash_breadcrumb("process.exit", _flush=True)


def write_crash_breadcrumb(event: str, **fields: Any) -> None:
    if not is_crash_log_enabled():
        return
    flush = bool(fields.pop("_flush", True))
    payload = {
        "event": str(event),
        "session": _SESSION_ID,
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "thread_id": threading.get_ident(),
        "time_monotonic": round(time.monotonic(), 6),
    }
    payload.update(fields)
    _write_line(f"[{_now()}] breadcrumb {_safe_json(payload)}", flush=flush)


def write_crash_exception(label: str, exc_type: Any, exc: BaseException, tb: Any) -> None:
    if not is_crash_log_enabled():
        return
    try:
        write_crash_breadcrumb(
            "python.exception",
            label=label,
            exc_type=getattr(exc_type, "__name__", str(exc_type)),
            exc=repr(exc),
        )
        formatted = "".join(traceback.format_exception(exc_type, exc, tb))
        _write_line(f"[{_now()}] traceback {label}\n{formatted}")
    except Exception:
        pass


def write_runtime_snapshot(label: str, *, include_stacks: bool = False) -> None:
    if not is_crash_log_enabled():
        return
    try:
        threads = []
        for thread in threading.enumerate():
            threads.append(
                {
                    "name": thread.name,
                    "ident": thread.ident,
                    "daemon": thread.daemon,
                    "alive": thread.is_alive(),
                }
            )
        write_crash_breadcrumb("runtime.snapshot", label=label, threads=threads)
        if include_stacks:
            frames = sys._current_frames()
            for thread in threading.enumerate():
                frame = frames.get(thread.ident or 0)
                if frame is None:
                    continue
                stack = "".join(traceback.format_stack(frame))
                _write_line(f"[{_now()}] thread_stack {label} {thread.name} ({thread.ident})\n{stack}")
    except Exception:
        pass
