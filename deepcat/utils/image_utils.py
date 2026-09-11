from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.width, self.height)


def bgr_to_rgb(image_bgr: np.ndarray) -> np.ndarray:
    if image_bgr is None:
        raise ValueError("image_bgr is None")
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image_bgr must be a HxWx3 BGR array")
    return image_bgr[:, :, ::-1].copy()


def rgb_to_bgr(image_rgb: np.ndarray) -> np.ndarray:
    if image_rgb is None:
        raise ValueError("image_rgb is None")
    if image_rgb.ndim != 3 or image_rgb.shape[2] != 3:
        raise ValueError("image_rgb must be a HxWx3 RGB array")
    return image_rgb[:, :, ::-1].copy()
