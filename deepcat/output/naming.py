"""截图输出文件的无覆盖命名。"""

from __future__ import annotations

import itertools
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import IO


_TOKEN_LOCK = threading.Lock()
_TOKEN_SEQUENCE = itertools.count()


def make_capture_token(now: datetime | None = None) -> str:
    """生成包含毫秒和进程内序号的批次标识。"""
    current = now or datetime.now()
    with _TOKEN_LOCK:
        sequence = next(_TOKEN_SEQUENCE) % 10000
    return f"{current:%Y%m%d_%H%M%S}_{current.microsecond // 1000:03d}_{sequence:04d}"


def unique_output_path(
    output_dir: str | Path,
    extension: str,
    *,
    prefix: str = "screenshot",
    token: str | None = None,
) -> Path:
    """返回当前不存在的输出路径；极端碰撞时追加自增后缀。"""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = str(extension or "png").strip().lower().lstrip(".") or "png"
    base_token = str(token or make_capture_token())
    candidate = directory / f"{prefix}_{base_token}.{suffix}"
    index = 1
    while candidate.exists():
        candidate = directory / f"{prefix}_{base_token}_{index:02d}.{suffix}"
        index += 1
    return candidate


def open_exclusive(path: str | Path) -> IO[bytes]:
    """以 O_CREAT|O_EXCL 独占创建文件并返回二进制写入句柄。

    用于规避"探测路径存在性"与"实际写入"之间的跨进程竞态（TOCTOU）：
    目标文件已存在时抛出 FileExistsError，由调用方决定换名重试。
    注意：Windows 下需附带 O_BINARY，避免运行时把句柄按文本模式处理。
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags)
    return os.fdopen(fd, "wb")
