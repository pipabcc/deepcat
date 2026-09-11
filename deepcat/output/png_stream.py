"""逐块编码一张完整 PNG，避免先构造整张编码缓冲区。"""

from __future__ import annotations

from collections.abc import Iterable
import logging
from pathlib import Path
import struct
import tempfile
from typing import BinaryIO
import zlib

import numpy as np


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IDAT_CHUNK_BYTES = 1024 * 1024
logger = logging.getLogger(__name__)


def _write_chunk(stream: BinaryIO, kind: bytes, data: bytes) -> None:
    stream.write(struct.pack("!I", len(data)))
    stream.write(kind)
    stream.write(data)
    checksum = zlib.crc32(data, zlib.crc32(kind)) & 0xFFFFFFFF
    stream.write(struct.pack("!I", checksum))


def _write_compressed_data(stream: BinaryIO, data: bytes) -> None:
    for start in range(0, len(data), _IDAT_CHUNK_BYTES):
        _write_chunk(stream, b"IDAT", data[start : start + _IDAT_CHUNK_BYTES])


def save_png_chunks(chunks: Iterable[np.ndarray], path: str | Path, width: int, height: int) -> str:
    """按 BGR 行块保存全部像素；全部成功后才替换目标文件。"""
    width, height = int(width), int(height)
    if not (0 < width < 2**31 and 0 < height < 2**31):
        raise ValueError("PNG 图像尺寸无效")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(_PNG_SIGNATURE)
            _write_chunk(stream, b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
            compressor = zlib.compressobj(level=3)
            rows_written = 0
            for chunk in chunks:
                if chunk.dtype != np.uint8 or chunk.ndim != 3 or chunk.shape[1:] != (width, 3):
                    raise ValueError("PNG 分块必须是同宽的 uint8 BGR 图像")
                rows = int(chunk.shape[0])
                if rows_written + rows > height:
                    raise ValueError("PNG 分块总高度超过目标高度")
                if rows == 0:
                    continue
                # 每行一个 PNG 过滤字节；只暂存当前块，不累积整图的 RGB 或压缩数据。
                scanlines = np.empty((rows, width * 3 + 1), dtype=np.uint8)
                scanlines[:, 0] = 0
                scanlines[:, 1:] = chunk[:, :, ::-1].reshape(rows, width * 3)
                _write_compressed_data(stream, compressor.compress(memoryview(scanlines).cast("B")))
                rows_written += rows
            if rows_written != height:
                raise ValueError(f"PNG 分块高度不完整：需要 {height} 行，实际 {rows_written} 行")
            _write_compressed_data(stream, compressor.flush())
            _write_chunk(stream, b"IEND", b"")
        temporary.replace(target)
        return str(target)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("清理 PNG 临时文件失败: %s", temporary, exc_info=True)
