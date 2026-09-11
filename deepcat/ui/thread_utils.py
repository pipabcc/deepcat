from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QThread


_PENDING_THREADS: set[QThread] = set()


def _forget_thread(thread: QThread) -> None:
    _PENDING_THREADS.discard(thread)


def request_thread_cancel(thread: Any, *, wait_ms: int = 0) -> bool:
    """请求工作线程协作式退出，并保证仍在运行的 QThread 不会被提前析构。"""
    if thread is None:
        return True

    for method_name in ("request_cancel", "cancel"):
        method = getattr(thread, method_name, None)
        if callable(method):
            try:
                method()
            except Exception:
                pass
            break

    request_interruption = getattr(thread, "requestInterruption", None)
    if callable(request_interruption):
        try:
            request_interruption()
        except Exception:
            pass

    try:
        running = bool(thread.isRunning())
    except Exception:
        running = False

    if running and int(wait_ms) > 0:
        try:
            if QThread.currentThread() is not thread:
                running = not bool(thread.wait(int(wait_ms)))
        except Exception:
            running = True

    if not running:
        return True

    try:
        _PENDING_THREADS.add(thread)
        thread.finished.connect(lambda *_, thread_ref=thread: _forget_thread(thread_ref))
        thread.finished.connect(lambda *_, thread_ref=thread: thread_ref.deleteLater())
    except Exception:
        pass
    return False
