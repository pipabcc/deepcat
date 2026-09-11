"""让 curl_cffi 使用 Python 已解析出的地址，绕开 libcurl 自己的解析器。

libcurl 默认在请求进程内部解析域名（Windows 上还要额外起一个解析线程）。当所在
程序尚未被系统安全策略放行时，这一步会被直接拦掉，只留下一句
``curl: (6) getaddrinfo() thread failed to start``；它既不像超时也不像连接重置，
最终往往被上层显示成「没有拿到有效回复」，掩盖了真实原因。

Python 的 ``socket.getaddrinfo`` 走系统 DNS 客户端服务，不受上述限制。这里先解析，
再通过 ``CURLOPT_RESOLVE`` 把结果交给 libcurl，请求链路便不再依赖它的解析器。

只在直连时注入：一旦配置了 ``socks5h`` 这类远端解析代理，域名本就该交给代理解析。
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from typing import Any
from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}
_CACHE_TTL_SEC = 60.0
_CACHE: dict[tuple[str, int], tuple[float, tuple[str, ...]]] = {}
_CACHE_LOCK = threading.Lock()


def resolve_entries(url: str, proxy: Any = None) -> list[str]:
    """返回 ``CURLOPT_RESOLVE`` 所需的 ``host:port:addr[,addr]`` 条目。

    无需注入（走了代理、目标是字面量地址）或解析失败时返回空列表。
    """
    if proxy:
        return []
    host, port = _split_host_port(url)
    if not host or _is_ip_literal(host):
        return []
    addresses = _resolve_ipv4(host, port)
    if not addresses:
        return []
    return [f"{host}:{port}:{','.join(addresses)}"]


def apply_to_session(session: Any, url: str, proxy: Any = None) -> None:
    """把预解析结果挂到 curl_cffi 的 Session 上，供其后续请求使用。

    非 curl_cffi 会话或不支持 ``curl_options`` 的版本直接跳过：注入失败只是回到
    libcurl 自己解析的老行为，不应该让请求本身失败。
    """
    entries = resolve_entries(url, proxy=proxy)
    if not entries:
        return
    options = getattr(session, "curl_options", None)
    if options is None:
        return
    try:
        from curl_cffi import CurlOpt
    except Exception:
        return
    merged = dict(options)
    merged[CurlOpt.RESOLVE] = entries
    session.curl_options = merged


def _split_host_port(url: str) -> tuple[str, int]:
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return "", 0
    host = str(parts.hostname or "").lower()
    if not host:
        return "", 0
    try:
        port = int(parts.port or 0)
    except ValueError:
        port = 0
    if not port:
        port = _DEFAULT_PORTS.get(str(parts.scheme or "").lower(), 443)
    return host, port


def _is_ip_literal(host: str) -> bool:
    """字面量地址无需解析，注入反而多此一举。"""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _resolve_ipv4(host: str, port: int) -> tuple[str, ...]:
    """解析域名并短时缓存；只取 IPv4，避免走向未被 TUN 接管的 IPv6 路径。"""
    now = time.monotonic()
    key = (host, port)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] > now:
            return cached[1]
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return ()
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    result = tuple(addresses)
    if result:
        with _CACHE_LOCK:
            _CACHE[key] = (now + _CACHE_TTL_SEC, result)
    return result
