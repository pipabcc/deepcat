"""长截图完成状态模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ScrollCaptureStatus(str, Enum):
    COMPLETE_SUCCESS = "complete_success"
    PARTIAL_SUCCESS = "partial_success"
    BOTTOM_REACHED = "bottom_reached"
    USER_STOPPED = "user_stopped"
    STITCH_DEGRADED = "stitch_degraded"


_STATUS_MESSAGES = {
    ScrollCaptureStatus.COMPLETE_SUCCESS: "长截图完整完成。",
    ScrollCaptureStatus.PARTIAL_SUCCESS: "长截图部分完成，已保留可用内容。",
    ScrollCaptureStatus.BOTTOM_REACHED: "已到达页面底部，长截图完成。",
    ScrollCaptureStatus.USER_STOPPED: "已按用户操作停止，并保留当前长截图。",
    ScrollCaptureStatus.STITCH_DEGRADED: "长截图已完成，但拼接使用了降级匹配，请检查接缝。",
}


@dataclass(frozen=True)
class ScrollCaptureOutcome:
    result: Any
    status: ScrollCaptureStatus
    stop_reason: str = ""
    frame_count: int = 0
    captured_height: int = 0
    estimated_peak_bytes: int = 0
    chunked: bool = False
    skipped_frames: int = 0
    preview_image_bgr: Any = field(default=None, repr=False, compare=False)

    @property
    def message(self) -> str:
        return _STATUS_MESSAGES[self.status]


def resolve_scroll_capture_status(
    *,
    stop_reason: str | None,
    stitch_degraded: bool,
    skipped_frames: int,
) -> ScrollCaptureStatus:
    if stitch_degraded:
        return ScrollCaptureStatus.STITCH_DEGRADED
    if int(skipped_frames) > 0:
        return ScrollCaptureStatus.PARTIAL_SUCCESS
    reason = str(stop_reason or "")
    if reason == "已到达底部，自动停止":
        return ScrollCaptureStatus.BOTTOM_REACHED
    if reason == "用户手动停止":
        return ScrollCaptureStatus.USER_STOPPED
    if reason == "已达到最大帧数，自动停止":
        return ScrollCaptureStatus.PARTIAL_SUCCESS
    return ScrollCaptureStatus.COMPLETE_SUCCESS
