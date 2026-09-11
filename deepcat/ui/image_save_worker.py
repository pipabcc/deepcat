"""一次性的整图编码落盘工作线程。

长图 PNG/PDF 编码可达数秒，必须离开 GUI 线程执行；
QImage/QPixmap 相关操作仍由调用方在 GUI 线程完成，这里只做纯磁盘编码写入。
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal


_ACTIVE_SAVE_WORKERS: set = set()


class ImageSaveWorker(QThread):
    saved = pyqtSignal(object, str)  # tag, 保存路径
    failed = pyqtSignal(object, str)  # tag, 错误消息

    def __init__(
        self,
        image_bgr: np.ndarray,
        path: str,
        fmt: str,
        quality: int,
        *,
        saver: Optional[Callable[[np.ndarray, str, Optional[str], int], str]] = None,
        tag: object = None,
    ) -> None:
        super().__init__()
        self._image_bgr = image_bgr
        self._path = str(path)
        self._fmt = str(fmt or "")
        self._quality = int(quality)
        self._saver = saver
        self.tag = tag

    def run(self) -> None:
        saver = self._saver
        if saver is None:
            from deepcat.output.saver import save_image_to_path

            saver = save_image_to_path
        try:
            path = saver(self._image_bgr, self._path, self._fmt, self._quality)
        except Exception as exc:
            self.failed.emit(self.tag, str(exc))
            return
        self.saved.emit(self.tag, str(path or self._path))


def start_image_save(
    image_bgr: np.ndarray,
    path: str,
    fmt: str,
    quality: int,
    *,
    saver: Optional[Callable[[np.ndarray, str, Optional[str], int], str]] = None,
    tag: object = None,
    on_saved: Callable[[object, str], None],
    on_failed: Callable[[object, str], None],
) -> ImageSaveWorker:
    """创建并启动保存线程；线程运行期间由模块持有引用，避免被提前销毁。"""
    worker = ImageSaveWorker(image_bgr, path, fmt, quality, saver=saver, tag=tag)
    _ACTIVE_SAVE_WORKERS.add(worker)
    worker.saved.connect(on_saved)
    worker.failed.connect(on_failed)
    worker.finished.connect(lambda w=worker: _ACTIVE_SAVE_WORKERS.discard(w))
    worker.finished.connect(worker.deleteLater)
    worker.start()
    return worker
