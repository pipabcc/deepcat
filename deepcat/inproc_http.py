"""进程内 HTTP 传输层：让 BaseHTTPRequestHandler 脱离 socket 运行。

内置 Web2API 服务运行在 DeepCat 自己的进程里，却通过 127.0.0.1 的 TCP 回环
给自己发请求。这条链路要求操作系统放行进程的「监听端口」动作：防火墙或安全
软件未放行时 listen 会被拒绝、连接可能被静默丢弃，最终只表现为「服务启动
超时」，必须手动把程序加入白名单才能恢复。

本模块用内存队列驱动同一个 handler：请求字节直接喂给 handler 的 rfile，响应
字节由队列逐块交回调用方。全程不产生任何 socket，因此不依赖防火墙规则，同时
保留流式语义（SSE 仍可增量读取）。
"""

from __future__ import annotations

import io
import json
import queue
import threading
from email.message import Message
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlencode, urlsplit

# 交换工厂：把一次 HTTP 请求交给具体的 handler 类，返回可消费的交换对象。
ExchangeFactory = Callable[[str, str, bytes, Mapping[str, str]], "InProcessExchange"]

_HEADER_TERMINATOR = b"\r\n\r\n"
_STREAM_END = object()
_DEFAULT_HEADER_TIMEOUT_SEC = 60.0


class InProcessTransportError(RuntimeError):
    """进程内 HTTP 交换失败：handler 抛异常、等待响应头超时或响应格式非法。"""


class _ExchangeAbort:
    """驱动线程把异常投递给等待中的消费者。"""

    __slots__ = ("exception",)

    def __init__(self, exception: BaseException) -> None:
        self.exception = exception


class _ResponseWriter:
    """BaseHTTPRequestHandler.wfile 的替身。

    handler 严格按「状态行 → 响应头 → 空行 → 正文」的顺序写字节，因此这里就地
    切分：空行之前的字节解析成状态与响应头，之后的字节按块交给消费者。
    """

    def __init__(self) -> None:
        self._head = bytearray()
        self._chunks: queue.Queue[Any] = queue.Queue()
        self._head_ready = threading.Event()
        self._finished = False
        self.status_code = 0
        self.reason = ""
        self.headers: dict[str, str] = {}
        self.failure: BaseException | None = None

    def write(self, data: Any) -> int:
        if isinstance(data, str):
            data = data.encode("utf-8")
        elif isinstance(data, (bytearray, memoryview)):
            data = bytes(data)
        if not data:
            return 0
        if self._head_ready.is_set():
            self._chunks.put(data)
        else:
            self._consume_head(bytes(data))
        return len(data)

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        """写端没有缓冲区，flush 是空操作。"""

    def wait_head(self, timeout: float | None) -> None:
        if not self._head_ready.wait(timeout):
            raise InProcessTransportError("进程内服务未在超时前返回响应头")
        if self.failure is not None:
            raise InProcessTransportError(str(self.failure)) from self.failure
        if not self.status_code:
            raise InProcessTransportError("进程内服务未产生有效响应")

    def iter_chunks(self) -> Iterator[bytes]:
        while True:
            item = self._chunks.get()
            if item is _STREAM_END:
                return
            if isinstance(item, _ExchangeAbort):
                raise item.exception
            yield item

    def finish(self, failure: BaseException | None = None) -> None:
        """结束写端；重复调用只生效一次。"""
        if self._finished:
            return
        self._finished = True
        if failure is not None:
            self.failure = failure
            self._chunks.put(_ExchangeAbort(failure))
        # handler 可能一个字节都没写（例如直接抛异常），必须唤醒等待者
        self._head_ready.set()
        self._chunks.put(_STREAM_END)

    def _consume_head(self, data: bytes) -> None:
        self._head.extend(data)
        marker = self._head.find(_HEADER_TERMINATOR)
        if marker < 0:
            return
        head = bytes(self._head[:marker])
        rest = bytes(self._head[marker + len(_HEADER_TERMINATOR) :])
        self._head.clear()
        try:
            self._parse_head(head)
        except BaseException as exc:  # 解析失败同样要唤醒等待者，避免调用方挂死
            self.failure = exc
        self._head_ready.set()
        if rest:
            self._chunks.put(rest)

    def _parse_head(self, head: bytes) -> None:
        lines = head.decode("iso-8859-1").split("\r\n")
        parts = lines[0].split(" ", 2)
        if len(parts) < 2 or not parts[0].upper().startswith("HTTP/"):
            raise InProcessTransportError(f"非法的响应状态行: {lines[0]!r}")
        self.status_code = int(parts[1])
        self.reason = parts[2] if len(parts) > 2 else ""
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if not separator:
                continue
            self.headers[name.strip()] = value.strip()


class _HandlerServer:
    """BaseHTTPRequestHandler 需要 server 属性；进程内不需要真实服务器。"""


class InProcessExchange:
    """一次完整的进程内 HTTP 请求/响应交换。

    驱动线程运行 handler，调用线程从队列取响应，两者之间不共享 socket。
    响应头就绪后 ``start()`` 即返回，正文可通过 ``iter_bytes()`` 增量读取。
    """

    def __init__(
        self,
        handler_class: type[BaseHTTPRequestHandler],
        method: str,
        target: str,
        body: bytes = b"",
        headers: Mapping[str, str] | None = None,
        *,
        header_timeout: float | None = _DEFAULT_HEADER_TIMEOUT_SEC,
    ) -> None:
        self._handler_class = handler_class
        self._method = str(method or "GET").upper()
        self._target = str(target or "/")
        self._body = bytes(body or b"")
        self._headers = {str(k): str(v) for k, v in dict(headers or {}).items()}
        if self._body and not any(name.lower() == "content-length" for name in self._headers):
            # handler 依据 Content-Length 决定读取多少请求体，缺失时会静默拿到空 body
            self._headers["Content-Length"] = str(len(self._body))
        self._header_timeout = header_timeout
        self._writer = _ResponseWriter()
        self._closed = False
        self._thread = threading.Thread(
            target=self._drive,
            name=f"InProcessHTTP-{self._method}",
            daemon=True,
        )

    @property
    def status_code(self) -> int:
        return self._writer.status_code

    @property
    def reason(self) -> str:
        return self._writer.reason

    @property
    def headers(self) -> dict[str, str]:
        return dict(self._writer.headers)

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self) -> "InProcessExchange":
        self._thread.start()
        self._writer.wait_head(self._header_timeout)
        return self

    def iter_bytes(self) -> Iterator[bytes]:
        yield from self._writer.iter_chunks()

    def read(self) -> bytes:
        return b"".join(self.iter_bytes())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._writer.finish()

    def _drive(self) -> None:
        failure: BaseException | None = None
        try:
            handler = self._build_handler()
            do_method = getattr(handler, f"do_{self._method}", None)
            if do_method is None:
                raise InProcessTransportError(f"handler 不支持 {self._method} 方法")
            do_method()
        except BaseException as exc:  # 驱动线程里的异常必须转交给消费者
            failure = exc
        finally:
            self._writer.finish(failure)

    def _build_handler(self) -> BaseHTTPRequestHandler:
        handler = self._handler_class.__new__(self._handler_class)
        handler.client_address = ("127.0.0.1", 0)
        handler.server = _HandlerServer()
        handler.connection = None
        handler.rfile = io.BytesIO(self._body)
        handler.wfile = self._writer
        handler.command = self._method
        handler.path = self._target
        handler.request_version = "HTTP/1.1"
        handler.requestline = f"{self._method} {self._target} HTTP/1.1"
        handler.close_connection = True
        handler.headers = self._build_request_headers()
        return handler

    def _build_request_headers(self) -> Message:
        message = Message()
        for name, value in self._headers.items():
            message[name] = value
        return message


def build_request_target(url: str, params: Any = None) -> str:
    """把完整 URL（可附带查询参数）转成 handler 需要的 request target。"""
    parts = urlsplit(str(url))
    query = parts.query
    if params:
        encoded = urlencode(params, doseq=True)
        query = f"{query}&{encoded}" if query else encoded
    path = parts.path or "/"
    return f"{path}?{query}" if query else path


def _to_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    raise InProcessTransportError(f"不支持的请求体类型: {type(value).__name__}")


def _requests_body_and_headers(kwargs: Mapping[str, Any]) -> tuple[bytes, dict[str, str]]:
    """按 requests 的语义把 json/data/content 归一成请求体和请求头。"""
    headers = {str(k): str(v) for k, v in dict(kwargs.get("headers") or {}).items()}
    body = b""
    if kwargs.get("json") is not None:
        body = json.dumps(kwargs["json"]).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif kwargs.get("data") is not None:
        body = _to_bytes(kwargs["data"])
    elif kwargs.get("content") is not None:
        body = _to_bytes(kwargs["content"])
    if body:
        headers["Content-Length"] = str(len(body))
    return body, headers


class _RequestsRawStream:
    """requests.Response.raw 的最小替身，提供 iter_content 依赖的 stream()。"""

    def __init__(self, exchange: InProcessExchange) -> None:
        self._exchange = exchange

    def stream(self, amt: int = 65536, decode_content: bool | None = None) -> Iterator[bytes]:
        yield from self._exchange.iter_bytes()

    def read(self, amt: int | None = None, decode_content: bool | None = None) -> bytes:
        return self._exchange.read()

    def close(self) -> None:
        self._exchange.close()

    def release_conn(self) -> None:
        """进程内没有连接需要归还。"""

    @property
    def closed(self) -> bool:
        return self._exchange.closed


def send_requests_request(
    exchange_factory: ExchangeFactory,
    method: str,
    url: str,
    **kwargs: Any,
) -> Any:
    """按 requests 语义发起进程内请求，返回等价的 requests.Response。

    ``proxies`` / ``stream`` / ``auth`` / ``verify`` 等只在真实网络链路上有意义，
    这里按进程内直连处理并忽略。
    """
    import requests
    from requests.structures import CaseInsensitiveDict

    body, headers = _requests_body_and_headers(kwargs)
    target = build_request_target(url, kwargs.get("params"))
    exchange = exchange_factory(method, target, body, headers)
    exchange.start()

    response = requests.models.Response()
    response.status_code = exchange.status_code
    response.reason = exchange.reason
    response.headers = CaseInsensitiveDict(exchange.headers)
    response.url = str(url)
    response.encoding = "utf-8"
    response.raw = _RequestsRawStream(exchange)
    return response


_httpx_stream_class: type | None = None


def _httpx_stream_class_for(httpx_module: Any) -> type:
    """构造 httpx.SyncByteStream 子类（httpx 缺失时不影响本模块导入）。"""
    global _httpx_stream_class
    if _httpx_stream_class is None:

        class _InProcessByteStream(httpx_module.SyncByteStream):
            def __init__(self, exchange: InProcessExchange) -> None:
                self._exchange = exchange

            def __iter__(self) -> Iterator[bytes]:
                try:
                    yield from self._exchange.iter_bytes()
                finally:
                    self._exchange.close()

            def close(self) -> None:
                self._exchange.close()

        _httpx_stream_class = _InProcessByteStream
    return _httpx_stream_class


class HttpxInProcessTransport:
    """httpx 传输层适配器：命中进程内 handler，不建立任何连接。"""

    def __init__(self, exchange_factory: ExchangeFactory) -> None:
        self._exchange_factory = exchange_factory

    def handle_request(self, request: Any) -> Any:
        import httpx

        exchange = self._exchange_factory(
            request.method,
            request.url.raw_path.decode("ascii", errors="replace"),
            bytes(request.content or b""),
            dict(request.headers),
        )
        exchange.start()
        stream_class = _httpx_stream_class_for(httpx)
        return httpx.Response(
            exchange.status_code,
            headers=exchange.headers,
            stream=stream_class(exchange),
            request=request,
        )

    def __enter__(self) -> "HttpxInProcessTransport":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        """进程内传输层没有连接池需要关闭。"""
