from __future__ import annotations

from typing import Optional

import numpy as np

from deepcat.utils.image_utils import Region


def _capture_screen_with_sct(sct: object, region: Optional[tuple[int, int, int, int] | Region] = None) -> np.ndarray:
    if isinstance(region, Region):
        region = region.as_tuple()

    if region is None:
        monitor = sct.monitors[1]
    else:
        left, top, width, height = region
        monitor = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}

    shot = sct.grab(monitor)
    img = np.array(shot, dtype=np.uint8)
    if img.ndim != 3 or img.shape[2] < 3:
        raise RuntimeError("截图数据格式异常")

    return img[:, :, :3].copy()


def capture_screen(
    region: Optional[tuple[int, int, int, int] | Region] = None,
    *,
    sct: object | None = None,
) -> np.ndarray:
    """
    截取屏幕指定区域，默认全屏。
    返回 BGR 格式的 numpy 数组，兼容 OpenCV。
    """
    if sct is not None:
        return _capture_screen_with_sct(sct, region)

    try:
        import mss
    except Exception as e:
        raise RuntimeError("未安装 mss，请先 pip install mss") from e

    with mss.mss() as local_sct:
        return _capture_screen_with_sct(local_sct, region)
