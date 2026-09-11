from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    state: str
    message: str
    current: int = 0
    total: int = 0
    elapsed_seconds: float = 0.0
    payload: Any = None


class TaskFeedback(QObject):
    started = pyqtSignal(object)
    status_changed = pyqtSignal(object)
    progress_changed = pyqtSignal(object)
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._started_at: dict[str, float] = {}

    def start(self, task_id: str, message: str = "", payload: Any = None) -> None:
        key = str(task_id or "task")
        self._started_at[key] = time.time()
        self.started.emit(TaskSnapshot(key, "running", str(message or ""), payload=payload))

    def status(self, task_id: str, message: str, payload: Any = None) -> None:
        self.status_changed.emit(self._snapshot(task_id, "running", message, payload=payload))

    def progress(self, task_id: str, current: int, total: int = 0, message: str = "", payload: Any = None) -> None:
        self.progress_changed.emit(
            self._snapshot(task_id, "running", message, int(current), int(total), payload)
        )

    def succeed(self, task_id: str, message: str = "", payload: Any = None) -> None:
        key = str(task_id or "task")
        self.finished.emit(self._snapshot(key, "succeeded", message, payload=payload))
        self._started_at.pop(key, None)

    def fail(self, task_id: str, message: str = "", payload: Any = None) -> None:
        key = str(task_id or "task")
        self.failed.emit(self._snapshot(key, "failed", message, payload=payload))
        self._started_at.pop(key, None)

    def _snapshot(
        self,
        task_id: str,
        state: str,
        message: str,
        current: int = 0,
        total: int = 0,
        payload: Any = None,
    ) -> TaskSnapshot:
        key = str(task_id or "task")
        started = float(self._started_at.get(key, time.time()))
        return TaskSnapshot(
            task_id=key,
            state=str(state),
            message=str(message or ""),
            current=int(current),
            total=int(total),
            elapsed_seconds=max(0.0, time.time() - started),
            payload=payload,
        )
