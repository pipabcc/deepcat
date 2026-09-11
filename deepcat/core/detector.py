from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np


def mean_abs_diff(prev_frame: np.ndarray, curr_frame: np.ndarray) -> float:
    if prev_frame.shape != curr_frame.shape:
        h = min(prev_frame.shape[0], curr_frame.shape[0])
        w = min(prev_frame.shape[1], curr_frame.shape[1])
        prev_frame = prev_frame[:h, :w]
        curr_frame = curr_frame[:h, :w]
    try:
        import cv2

        h, w = prev_frame.shape[0], prev_frame.shape[1]
        prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
        curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)
        l1 = float(cv2.norm(prev_gray, curr_gray, cv2.NORM_L1))
        denom = float(h * w)
        return l1 / denom
    except Exception:
        h, w = prev_frame.shape[0], prev_frame.shape[1]
        prev_gray = (
            prev_frame[:, :, 0].astype(np.uint16)
            + prev_frame[:, :, 1].astype(np.uint16)
            + prev_frame[:, :, 2].astype(np.uint16)
        ) // 3
        curr_gray = (
            curr_frame[:, :, 0].astype(np.uint16)
            + curr_frame[:, :, 1].astype(np.uint16)
            + curr_frame[:, :, 2].astype(np.uint16)
        ) // 3

        total = 0
        block = 128
        for y0 in range(0, h, block):
            y1 = min(h, y0 + block)
            a = prev_gray[y0:y1, :].astype(np.int16)
            b = curr_gray[y0:y1, :].astype(np.int16)
            total += int(np.abs(a - b).sum())
        return float(total) / float(h * w)


def is_at_bottom(prev_frame: np.ndarray, curr_frame: np.ndarray, diff_mean_threshold: float = 1.0) -> bool:
    """
    判断是否已滚动到底部：
    两帧的平均像素差值很小则认为已到底。
    """
    return mean_abs_diff(prev_frame, curr_frame) < float(diff_mean_threshold)


@dataclass(frozen=True)
class FixedRegions:
    header_height: int
    footer_height: int


def detect_fixed_regions(
    frames: Iterable[np.ndarray],
    sample_count: int = 3,
    max_check_rows: int = 240,
) -> FixedRegions:
    frames_list = list(frames)
    if len(frames_list) < 2:
        return FixedRegions(0, 0)

    samples = frames_list[: max(2, min(sample_count, len(frames_list)))]
    h = min(f.shape[0] for f in samples)
    w = min(f.shape[1] for f in samples)
    samples = [f[:h, :w] for f in samples]

    max_check_rows = int(min(max_check_rows, h // 2))
    header_height = 0
    for y in range(max_check_rows):
        row0 = samples[0][y, :, :]
        if all(np.array_equal(row0, s[y, :, :]) for s in samples[1:]):
            header_height += 1
        else:
            break

    footer_height = 0
    for i in range(1, max_check_rows + 1):
        row0 = samples[0][-i, :, :]
        if all(np.array_equal(row0, s[-i, :, :]) for s in samples[1:]):
            footer_height += 1
        else:
            break

    return FixedRegions(header_height, footer_height)
