"""作用域安全的延迟回调工具。

`QTimer.singleShot(延迟, 回调)` 的回调若引用了短命控件，控件销毁后定时器
仍会触发，回调访问已删除的 C++ 对象抛 RuntimeError；在没有自定义
sys.excepthook 的进程里（如 pytest），PyQt6 会将其升级为 qFatal 直接
杀死进程。single_shot_scoped 把定时器挂到 owner 上：owner 销毁时定时器
一并销毁，回调自然不再触发。
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QObject, QTimer


def single_shot_scoped(msec: int, owner: QObject, callback: Callable[[], None]) -> QTimer:
    """延迟 msec 毫秒执行 callback；owner 销毁则自动取消。"""
    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.timeout.connect(callback)
    timer.timeout.connect(timer.deleteLater)
    timer.start(msec)
    return timer
