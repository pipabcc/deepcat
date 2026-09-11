from __future__ import annotations

import logging
import platform
import time
from typing import Optional

import numpy as np

from deepcat.utils.image_utils import bgr_to_rgb

logger = logging.getLogger(__name__)


def copy_image_to_clipboard(image_bgr: np.ndarray) -> bool:
    """
    复制到剪贴板：
    - GUI 场景建议用 PyQt6 的 QClipboard
    - 命令行仅在 Windows 且安装 pywin32 时可用
    """
    if platform.system().lower() != "windows":
        return False

    try:
        import win32clipboard  # type: ignore
        import win32con  # type: ignore
    except Exception:
        return False

    try:
        from PIL import Image
    except Exception:
        return False

    import io

    rgb = bgr_to_rgb(image_bgr)
    img = Image.fromarray(rgb)

    output = io.BytesIO()
    img.convert("RGB").save(output, "BMP")
    data = output.getvalue()[14:]
    output.close()

    try:
        # OpenClipboard 在其他程序（浏览器、剪贴板工具）短暂占用剪贴板时会失败，
        # 重试数次可消除偶发"复制失败"
        last_error: Optional[BaseException] = None
        opened = False
        for _ in range(5):
            try:
                win32clipboard.OpenClipboard()
                opened = True
                break
            except Exception as exc:
                last_error = exc
                time.sleep(0.02)
        if not opened:
            logger.warning("OpenClipboard 重试后仍失败: %s", last_error)
            return False
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, data)
            return True
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
    except Exception:
        logger.exception("复制图像到剪贴板失败")
        return False
