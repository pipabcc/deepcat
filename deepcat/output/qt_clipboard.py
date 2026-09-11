from __future__ import annotations

from PyQt6.QtGui import QGuiApplication, QImage

import numpy as np

from deepcat.utils.image_utils import bgr_to_rgb


def copy_bgr_image(image_bgr: np.ndarray) -> bool:
    try:
        rgb = bgr_to_rgb(image_bgr)
        return copy_rgb_image(rgb)
    except Exception:
        return False


def copy_rgb_image(rgb: np.ndarray) -> bool:
    try:
        h, w = int(rgb.shape[0]), int(rgb.shape[1])
        bytes_per_line = int(rgb.strides[0])
        qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()
        cb = QGuiApplication.clipboard()
        cb.setImage(qimg)
        return True
    except Exception:
        return False


def flush_clipboard() -> None:
    """把延迟渲染的剪贴板数据落为系统全局数据（OleFlushClipboard）。

    建议在应用 aboutToQuit 时调用：进程崩溃/被强杀时，延迟渲染的数据会丢失，
    flush 后剪贴板内容由系统持有，不再依赖本进程存活。失败时静默忽略。
    """
    try:
        import ctypes

        ctypes.windll.ole32.OleFlushClipboard()
    except Exception:
        pass
