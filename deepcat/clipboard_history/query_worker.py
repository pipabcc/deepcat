"""合并连续查询，只允许一个剪贴板查询线程在途。"""

from __future__ import annotations

from dataclasses import dataclass
import threading

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from deepcat.clipboard_history.clipboard_database import ClipboardDatabase
from deepcat.ui.thread_utils import request_thread_cancel


@dataclass(frozen=True)
class ClipboardQuery:
    generation: int
    keyword: str = ""
    content_type: str | None = None
    offset: int = 0
    limit: int = 50
    statistics_only: bool = False
    include_pinned: bool = False


class ClipboardQueryWorker(QThread):
    ready = pyqtSignal(object, object)
    failed = pyqtSignal(object, str)

    def __init__(self, database: ClipboardDatabase, query: ClipboardQuery) -> None:
        super().__init__()
        self.database = database
        self.query = query
        self._cancelled = threading.Event()

    def request_cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        query = self.query
        try:
            payload = {}
            if not query.statistics_only:
                if query.keyword:
                    records = self.database.search_records(
                        query.keyword,
                        query.content_type,
                        query.limit + 1,
                        query.offset,
                        include_pinned=query.include_pinned,
                        cancelled=self._cancelled.is_set,
                    )
                else:
                    records = self.database.get_records(
                        query.limit + 1,
                        query.offset,
                        query.content_type,
                        cancelled=self._cancelled.is_set,
                    )
                if self._cancelled.is_set():
                    return
                payload.update(records=records[: query.limit], has_more=len(records) > query.limit)
                if query.offset == 0:
                    payload["pinned"] = self.database.get_pinned_records()
            if self._cancelled.is_set():
                return
            payload["statistics"] = self.database.get_statistics(query.content_type)
            if not self._cancelled.is_set():
                self.ready.emit(query, payload)
        except Exception as error:
            if not self._cancelled.is_set():
                self.failed.emit(query, str(error))


class ClipboardQueryController(QObject):
    completed = pyqtSignal(object, object)
    failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._generation = 0
        self._worker: ClipboardQueryWorker | None = None
        self._pending: tuple[ClipboardDatabase, ClipboardQuery] | None = None
        self._closed = False

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def submit(self, database: ClipboardDatabase, **options) -> None:
        if self._closed:
            return
        self._generation += 1
        self._pending = (database, ClipboardQuery(self._generation, **options))
        if self._worker is not None:
            self._worker.request_cancel()
        else:
            self._start_pending()

    def _start_pending(self) -> None:
        if self._closed or self._pending is None:
            return
        database, query = self._pending
        self._pending = None
        worker = ClipboardQueryWorker(database, query)
        self._worker = worker
        worker.ready.connect(self._on_ready)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(worker.deleteLater)
        try:
            worker.start()
        except Exception as error:
            self._worker = None
            worker.deleteLater()
            self.failed.emit(str(error))

    def _on_ready(self, query: ClipboardQuery, payload: object) -> None:
        if not self._closed and query.generation == self._generation:
            self.completed.emit(query, payload)

    def _on_failed(self, query: ClipboardQuery, message: str) -> None:
        if not self._closed and query.generation == self._generation:
            self.failed.emit(message)

    def _on_finished(self) -> None:
        self._worker = None
        self._start_pending()

    def cancel(self, *, wait_ms: int = 0) -> bool:
        self._generation += 1
        self._pending = None
        return request_thread_cancel(self._worker, wait_ms=wait_ms)

    def close(self) -> None:
        self._closed = True
        self.cancel()

    def reopen(self) -> None:
        """重新激活控制器，允许后续查询。"""
        self._closed = False
        self._generation += 1
        self._pending = None
