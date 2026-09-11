from __future__ import annotations

import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from deepcat.utils.log_settings import is_app_log_enabled
from deepcat.utils.paths import get_app_dir


class _AppLogEnabledFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return bool(is_app_log_enabled())


_FILE_HANDLERS: dict[str, _LazyDirRotatingFileHandler] = {}
_FILE_HANDLERS_LOCK = threading.Lock()


class _LazyDirRotatingFileHandler(RotatingFileHandler):
    def __init__(self, *args, **kwargs):
        self._closed = False
        self._cache_key = str(kwargs.get("filename") or (args[0] if args else ""))
        super().__init__(*args, **kwargs)

    # 目录到真正写文件时才创建，避免 get_logger 在模块导入期产生磁盘副作用
    def _open(self):
        try:
            Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return super()._open()

    def rotate(self, source: str, dest: str) -> None:
        """重写 rotate 方法，针对 Windows 文件瞬态占用增加退避重试与容错保护。"""
        if not os.path.exists(source):
            return
        for attempt in range(5):
            try:
                if os.path.exists(dest):
                    try:
                        os.remove(dest)
                    except PermissionError:
                        pass
                os.rename(source, dest)
                return
            except PermissionError as exc:
                # WinError 32: 另一个程序正在使用此文件，进程无法访问。
                if getattr(exc, "winerror", None) == 32 and attempt < 4:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                # 达到重试上限后静默跳过本次文件的重命名，不抛出异常打断日志记录
                break
            except Exception:
                break

    def doRollover(self) -> None:
        """重写 doRollover，捕获 PermissionError 并确保流恢复打开，彻底杜绝 WinError 32。"""
        try:
            super().doRollover()
        except (PermissionError, OSError):
            if self.stream is None:
                try:
                    self.stream = self._open()
                except Exception:
                    pass
        except Exception:
            if self.stream is None:
                try:
                    self.stream = self._open()
                except Exception:
                    pass

    def close(self) -> None:
        self._closed = True
        with _FILE_HANDLERS_LOCK:
            if self._cache_key and _FILE_HANDLERS.get(self._cache_key) is self:
                _FILE_HANDLERS.pop(self._cache_key, None)
        super().close()


def get_log_dir() -> Path:
    return get_app_dir() / "logs"


def get_log_file_path() -> Path:
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "app.log"


def get_logger(
    name: str = "deepcat",
    *,
    level: int = logging.ERROR,
    enable_console: bool = True,
    enable_file: bool = True,
    file_path: Optional[Path] = None,
) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        logger.setLevel(int(level))
        return logger

    logger.setLevel(int(level))
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s")

    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(int(level))
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if enable_file:
        raw_path = file_path or (get_log_dir() / "app.log")
        path_key = str(Path(raw_path).resolve())
        with _FILE_HANDLERS_LOCK:
            file_handler = _FILE_HANDLERS.get(path_key)
            if file_handler is None or getattr(file_handler, "_closed", False):
                file_handler = _LazyDirRotatingFileHandler(
                    path_key,
                    maxBytes=2 * 1024 * 1024,
                    backupCount=5,
                    encoding="utf-8",
                    delay=file_path is None,
                )
                if file_path is None:
                    file_handler.addFilter(_AppLogEnabledFilter())
                file_handler.setLevel(int(level))
                file_handler.setFormatter(formatter)
                _FILE_HANDLERS[path_key] = file_handler
            elif file_handler.level > int(level):
                file_handler.setLevel(int(level))

        if file_handler not in logger.handlers:
            logger.addHandler(file_handler)

    logger.propagate = False
    return logger
