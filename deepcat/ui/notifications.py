from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union


@dataclass(frozen=True)
class TrayNotification:
    title: str
    message: str
    duration_ms: int = 900


def map_stop_reason(reason_text: str) -> Optional[str]:
    t = str(reason_text or "")
    if t == "用户手动停止":
        return "manual"
    if t == "已到达底部，自动停止":
        return "bottomReached"
    return None


def format_saved_title(stop_reason: Optional[str], completion_status: Optional[str] = None) -> str:
    if completion_status == "stitch_degraded":
        return "已保存（拼接降级）"
    if completion_status == "partial_success":
        return "已保存（部分成功）"
    if stop_reason == "manual":
        return "已保存（手动停止）"
    if stop_reason == "bottomReached":
        return "已保存（已到达底部）"
    return "已保存"


def build_saved_message(result: Union[str, list[str]]) -> str:
    if isinstance(result, str):
        return f"已保存到：{result}"
    n = len(result)
    if n > 0:
        return f"已保存 {n} 个文件：{result[0]}"
    return "已保存"


class CaptureNotificationState:
    def __init__(self) -> None:
        self._stop_reason: Optional[str] = None
        self._notified: bool = False
        self._failed: bool = False
        self._completion_status: Optional[str] = None

    def reset(self) -> None:
        self._stop_reason = None
        self._notified = False
        self._failed = False
        self._completion_status = None

    def on_stopped(self, reason_text: str) -> None:
        if self._stop_reason is None:
            self._stop_reason = map_stop_reason(reason_text)

    def on_failed(self) -> None:
        self._failed = True

    def on_completion(self, status: object) -> None:
        self._completion_status = str(getattr(status, "value", status) or "") or None

    def build_saved_notification(self, result: Union[str, list[str]]) -> Optional[TrayNotification]:
        if self._notified or self._failed:
            return None
        self._notified = True
        return TrayNotification(
            title=format_saved_title(self._stop_reason, self._completion_status),
            message=build_saved_message(result),
            duration_ms=900,
        )
