"""有界 HTTP 客户端缓存，每次借用独占一个客户端。"""

from __future__ import annotations

from contextlib import contextmanager
import logging
import threading
import time
from typing import Any, Callable, Hashable


logger = logging.getLogger(__name__)


class HttpClientPool:
    def __init__(self, max_idle: int = 8, idle_seconds: float = 60.0) -> None:
        self._max_idle = max(0, int(max_idle))
        self._idle_seconds = max(0.0, float(idle_seconds))
        self._lock = threading.Lock()
        self._idle: list[tuple[Hashable, Any, float]] = []
        self._closed = False

    @staticmethod
    def _close_client(client: Any) -> None:
        try:
            client.close()
        except Exception:
            logger.warning("关闭 HTTP 客户端失败", exc_info=True)

    def _acquire(self, key: Hashable, factory: Callable[[], Any]):
        now = time.monotonic()
        expired = []
        client = None
        with self._lock:
            if self._closed:
                raise RuntimeError("HTTP 客户端池已关闭")
            retained = []
            for old_key, candidate, idle_since in self._idle:
                if now - idle_since >= self._idle_seconds or candidate.is_closed:
                    expired.append(candidate)
                elif old_key == key and client is None:
                    client = candidate
                else:
                    retained.append((old_key, candidate, idle_since))
            self._idle = retained
        for candidate in expired:
            self._close_client(candidate)
        return client if client is not None else factory()

    @contextmanager
    def lease(self, key: Hashable, factory: Callable[[], Any]):
        client = self._acquire(key, factory)
        try:
            yield client
        except BaseException:
            self._close_client(client)
            raise
        else:
            discarded = []
            with self._lock:
                if self._closed or client.is_closed or self._max_idle == 0:
                    discarded.append(client)
                else:
                    self._idle.append((key, client, time.monotonic()))
                    while len(self._idle) > self._max_idle:
                        discarded.append(self._idle.pop(0)[1])
            for candidate in discarded:
                self._close_client(candidate)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            clients = [client for _, client, _ in self._idle]
            self._idle.clear()
        for client in clients:
            self._close_client(client)
