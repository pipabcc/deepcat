"""ChatGPT Web 的独占连接借用及短时登录态缓存。"""

from __future__ import annotations

import atexit
import base64
import copy
import json
import sys
import threading
import time
from typing import Any, Callable

from deepcat.utils.http_client_pool import HttpClientPool


AUTH_CACHE_SECONDS = 120.0
_pool_lock = threading.Lock()
_pool = HttpClientPool(max_idle=4, idle_seconds=60.0)


def _auth_cache_lifetime(access_token: str) -> float:
    """令牌中的过期时间仅用于缩短缓存寿命，鉴权仍由上游完成。"""
    lifetime = AUTH_CACHE_SECONDS
    parts = access_token.split(".")
    if len(parts) == 3 and len(parts[1]) < 65536:
        try:
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
            expires_at = float(payload["exp"])
        except (KeyError, TypeError, ValueError, UnicodeError):
            return lifetime
        lifetime = min(lifetime, max(0.0, expires_at - time.time() - 30.0))
    return lifetime


class _ReusableSession:
    def __init__(self, session: Any) -> None:
        self._session = session
        self._closed = False
        self._auth: tuple[dict[str, Any], str, str] | None = None
        self._auth_valid_until = 0.0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    @property
    def is_closed(self) -> bool:
        return self._closed or getattr(self._session, "_closed", False) is True

    def cached_auth(self) -> tuple[dict[str, Any], str, str] | None:
        if self._auth is None or time.monotonic() >= self._auth_valid_until:
            return None
        return copy.deepcopy(self._auth)

    def remember_auth(self, result: tuple[dict[str, Any] | None, str | None, str]) -> None:
        info, token, error = result
        self._auth = None
        self._auth_valid_until = 0.0
        # 请求失败后沿用的旧 token 没有重新验证，不能写入有效登录态缓存。
        if (
            not isinstance(info, dict)
            or not isinstance(token, str)
            or not token
            or error
            or info.get("accessToken") != token
        ):
            return
        lifetime = _auth_cache_lifetime(token)
        if lifetime > 0:
            self._auth = copy.deepcopy((info, token, ""))
            self._auth_valid_until = time.monotonic() + lifetime

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._auth = None
        self._session.close()


class ChatGPTSessionLease:
    """保持原有 session 接口，close 时归还连接；异常或取消时丢弃连接。"""

    def __init__(self, context: Any) -> None:
        self._context = context
        self._session = context.__enter__()
        self._released = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def close(self) -> None:
        if self._released:
            return
        self._released = True
        # 调用方的 finally 仍携带异常状态，连接池据此区分成功、失败和取消。
        self._context.__exit__(*sys.exc_info())


def acquire_session(key: str, factory: Callable[[], Any]) -> ChatGPTSessionLease:
    with _pool_lock:
        return ChatGPTSessionLease(_pool.lease(key, lambda: _ReusableSession(factory())))


def clear_session_cache() -> None:
    """配置切换或服务停止后关闭闲置连接，正在使用的连接归还时关闭。"""
    global _pool
    with _pool_lock:
        previous = _pool
        _pool = HttpClientPool(max_idle=4, idle_seconds=60.0)
    previous.close()


def _close_pool() -> None:
    with _pool_lock:
        _pool.close()


atexit.register(_close_pool)
