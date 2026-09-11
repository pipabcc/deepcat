"""在后台准备完整分辨率的滚动截图，不以缩略图替代原图。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from PyQt6.QtCore import QRect, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QImageReader


def load_saved_preview(paths: list[str], cancelled: Callable[[], bool]) -> tuple[Any, tuple[int, int] | None, bool]:
    images: list[tuple[str, int, int]] = []
    for path in paths:
        if cancelled():
            return None, None, False
        if Path(path).suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
            continue
        # 只读取尺寸；不对本程序保存的完整长图施加缩略预览阈值。
        size = QImageReader(str(path)).size()
        if not size.isValid():
            raise ValueError(f"无法读取截图尺寸: {Path(path).name}")
        images.append((path, size.width(), size.height()))
    if not images:
        raise ValueError("没有可读取的完整截图图像")
    width = images[0][1]
    height = sum(item[2] for item in images)
    if any(item[1] != width for item in images):
        raise ValueError("滚动截图分块宽度不一致，原图文件已保留")
    # 单文件直接交付解码结果；兼容历史分块时仅分配一份完整输出缓冲。
    preview = np.empty((height, width, 3), dtype=np.uint8) if len(images) > 1 else None
    offset = 0
    for path, _, part_height in images:
        if cancelled():
            return None, None, False
        image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (part_height, width):
            raise ValueError(f"截图图像不完整或已发生变化: {Path(path).name}")
        if len(images) == 1:
            return image, (width, height), False
        preview[offset : offset + part_height] = image
        offset += part_height
        del image
    return preview, (width, height), False


class ScrollResultPrepareWorker(QThread):
    prepared = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, image_bgr: Any, copy_on_capture: bool, anchor_rect: QRect | None) -> None:
        super().__init__()
        self._image_bgr = image_bgr
        self._copy_on_capture = bool(copy_on_capture)
        self._anchor_rect = QRect(anchor_rect) if anchor_rect is not None else None

    def run(self) -> None:
        try:
            source = self._image_bgr
            paths = [source] if isinstance(source, str) else list(source) if isinstance(source, (list, tuple)) else []
            if paths:
                image_bgr, original_size, _ = load_saved_preview(paths, self.isInterruptionRequested)
            else:
                image_bgr = np.ascontiguousarray(source)
                original_size = (image_bgr.shape[1], image_bgr.shape[0])
            if self.isInterruptionRequested():
                return
            if image_bgr is None or image_bgr.dtype != np.uint8 or image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
                raise ValueError("截图图像格式无效")
            height, width = image_bgr.shape[:2]
            qimage = QImage(image_bgr.data, width, height, image_bgr.strides[0], QImage.Format.Format_BGR888)
            # 在后台完成 Qt 显示格式转换，GUI 侧不再复制 RGB、QImage 和 BGR 整图。
            qimage = qimage.convertToFormat(QImage.Format.Format_RGB32)
            if qimage.isNull():
                raise MemoryError("无法为完整截图创建显示缓冲，原图文件已保留")
            self.prepared.emit(
                {
                    "image_bgr": image_bgr,
                    "rgb": image_bgr[:, :, ::-1],
                    "qimage": qimage,
                    "source_paths": paths,
                    "original_size": original_size,
                    "preview_scaled": False,
                    "copy_on_capture": self._copy_on_capture,
                    "anchor_rect": self._anchor_rect,
                }
            )
        except Exception as error:
            self.failed.emit(str(error) or repr(error))
        finally:
            self._image_bgr = None

    @staticmethod
    def _load_image_bgr(result: Any) -> Any:
        if isinstance(result, (str, list, tuple)):
            paths = [result] if isinstance(result, str) else list(result)
            return load_saved_preview(paths, lambda: False)[0]
        return result
