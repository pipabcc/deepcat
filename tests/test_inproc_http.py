"""进程内 HTTP 传输层的通用行为：流式、异常传递、不占用端口。

内置 Web2API 服务改为进程内直调后，这个传输层是它与调用方之间的唯一通道，
因此这里覆盖三个关键性质：正文可以增量交付、handler 异常不会被吞掉、
整个过程不绑定任何端口。
"""

import socket
from http.server import BaseHTTPRequestHandler

import pytest

from deepcat.inproc_http import InProcessExchange, InProcessTransportError


class _SseHandler(BaseHTTPRequestHandler):
    """模拟流式端点：响应头之后分多次写出 SSE 片段。"""

    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for index in range(3):
            self.wfile.write(f"data: {index}\n\n".encode())
            self.wfile.flush()

    def log_message(self, fmt, *args):
        """测试里不需要访问日志。"""


class _BoomHandler(BaseHTTPRequestHandler):
    """处理过程中直接抛异常，且一个字节都不写。"""

    def do_GET(self):
        raise RuntimeError("handler 内部失败")

    def log_message(self, fmt, *args):
        """测试里不需要访问日志。"""


def test_exchange_streams_body_incrementally():
    exchange = InProcessExchange(_SseHandler, "GET", "/stream").start()

    assert exchange.status_code == 200
    assert exchange.headers["Content-Type"] == "text/event-stream"

    chunks = list(exchange.iter_bytes())
    assert b"".join(chunks) == b"data: 0\n\ndata: 1\n\ndata: 2\n\n"
    # 三次 write 对应三个块：正文是边产生边交付的，而不是攒完再给
    assert len(chunks) == 3


def test_exchange_surfaces_handler_failure():
    exchange = InProcessExchange(_BoomHandler, "GET", "/boom")

    # 异常必须带着原始信息回到调用方：旧实现把它吞在子线程里，只留下一句「启动超时」
    with pytest.raises(InProcessTransportError, match="handler 内部失败"):
        exchange.start()


def test_exchange_never_opens_a_listening_socket(monkeypatch):
    def fail_bind(*args, **kwargs):
        raise AssertionError("进程内交换不应绑定任何端口")

    monkeypatch.setattr(socket.socket, "bind", fail_bind)

    exchange = InProcessExchange(_SseHandler, "GET", "/stream").start()
    assert b"".join(exchange.iter_bytes()).startswith(b"data: 0")


class _EchoHandler(BaseHTTPRequestHandler):
    """回显请求体：用于验证请求体不会因为缺少显式 Content-Length 而丢失。"""

    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length) if length else b""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        """测试里不需要访问日志。"""


def test_exchange_infers_content_length_from_body():
    exchange = InProcessExchange(_EchoHandler, "POST", "/echo", b"hello").start()

    assert exchange.read() == b"hello"
