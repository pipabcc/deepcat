from __future__ import annotations

import json
import socket
import struct
import time
from typing import Any


PROTOCOL_VERSION = 3
MAX_FRAME_BYTES = 64 * 1024 * 1024
MAX_RAW_BYTES = 1 << 30
_FRAME_HEADER = struct.Struct("!I")
_RAW_HEADER = struct.Struct("!Q")
_RAW_CHUNK_BYTES = 4 * 1024 * 1024


class OcrProtocolError(RuntimeError):
    """OCR Worker 通信协议无效。"""


def send_message(connection: socket.socket, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_FRAME_BYTES:
        raise OcrProtocolError(f"OCR消息过大：{len(encoded)} bytes")
    connection.sendall(_FRAME_HEADER.pack(len(encoded)) + encoded)


def send_raw_bytes(connection: socket.socket, data: bytes | bytearray | memoryview) -> None:
    """发送原始字节帧（8 字节大端长度头），避免图像经 PNG 编码 + 临时文件往返。"""
    view = memoryview(data)
    total = int(view.nbytes)
    if total > MAX_RAW_BYTES:
        raise OcrProtocolError(f"OCR原始数据过大：{total} bytes")
    connection.sendall(_RAW_HEADER.pack(total))
    for offset in range(0, total, _RAW_CHUNK_BYTES):
        connection.sendall(view[offset : offset + _RAW_CHUNK_BYTES])


def receive_raw_bytes(connection: socket.socket, *, deadline: float | None = None) -> bytearray:
    """接收原始字节帧（8 字节大端长度头）。

    直接接收进预分配的 bytearray（recv_into），相比"chunks 列表 + join"少两次
    全量拷贝；bytearray 是可写缓冲，调用方 np.frombuffer 可直接得到可写数组。
    """
    header = _receive_exact(connection, _RAW_HEADER.size, deadline=deadline)
    total = int(_RAW_HEADER.unpack(header)[0])
    if total <= 0 or total > MAX_RAW_BYTES:
        raise OcrProtocolError(f"OCR原始数据长度无效：{total}")
    buffer = bytearray(total)
    _receive_exact_into(connection, buffer, deadline=deadline)
    return buffer


def receive_message(connection: socket.socket, *, deadline: float | None = None) -> dict[str, Any]:
    header = _receive_exact(connection, _FRAME_HEADER.size, deadline=deadline)
    frame_length = _FRAME_HEADER.unpack(header)[0]
    if frame_length <= 0 or frame_length > MAX_FRAME_BYTES:
        raise OcrProtocolError(f"OCR消息长度无效：{frame_length}")
    raw_payload = _receive_exact(connection, frame_length, deadline=deadline)
    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OcrProtocolError("OCR消息不是有效的UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise OcrProtocolError("OCR消息顶层必须是对象")
    return payload


def _receive_exact(connection: socket.socket, byte_count: int, *, deadline: float | None) -> bytes:
    chunks: list[bytes] = []
    remaining = int(byte_count)
    while remaining > 0:
        if deadline is not None:
            timeout = float(deadline) - time.perf_counter()
            if timeout <= 0:
                raise socket.timeout("OCR消息接收超时")
            connection.settimeout(timeout)
        chunk = connection.recv(remaining)
        if not chunk:
            raise EOFError("OCR Worker连接已关闭")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _receive_exact_into(
    connection: socket.socket,
    buffer: bytearray,
    *,
    deadline: float | None,
) -> None:
    view = memoryview(buffer)
    received = 0
    total = len(buffer)
    while received < total:
        if deadline is not None:
            timeout = float(deadline) - time.perf_counter()
            if timeout <= 0:
                raise socket.timeout("OCR消息接收超时")
            connection.settimeout(timeout)
        count = connection.recv_into(view[received:], total - received)
        if not count:
            raise EOFError("OCR Worker连接已关闭")
        received += count
