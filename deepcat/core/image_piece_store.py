"""超出常驻预算的拼接片段写入临时文件，按片段读取。"""

from __future__ import annotations

from dataclasses import dataclass
import io
import tempfile
from typing import BinaryIO, Iterator

import numpy as np


@dataclass(frozen=True)
class DiskImagePiece:
    stream: BinaryIO
    offset: int
    shape: tuple[int, ...]
    dtype: np.dtype

    @property
    def size(self) -> int:
        return int(np.prod(self.shape))

    @property
    def ndim(self) -> int:
        return len(self.shape)

    @property
    def nbytes(self) -> int:
        return self.size * self.dtype.itemsize

    def __getitem__(self, key):
        array = np.empty(self.shape, dtype=self.dtype)
        view = memoryview(array).cast("B")
        self.stream.seek(self.offset)
        offset = 0
        while offset < len(view):
            count = self.stream.readinto(view[offset:])
            if not count:
                raise OSError("拼接临时数据不完整")
            offset += count
        return array[key]


class ImagePieceStore:
    def __init__(self, max_memory_bytes: int) -> None:
        self.max_memory_bytes = max(0, int(max_memory_bytes))
        self.resident_bytes = 0
        self._pieces: list[np.ndarray | DiskImagePiece] = []
        self._stream: BinaryIO | None = None
        self._spill_index = 0

    def __len__(self) -> int:
        return len(self._pieces)

    def __iter__(self) -> Iterator[np.ndarray | DiskImagePiece]:
        return iter(self._pieces)

    def __reversed__(self):
        return reversed(self._pieces)

    def __getitem__(self, index: int):
        return self._pieces[index]

    def __setitem__(self, index: int, value: np.ndarray) -> None:
        index %= len(self._pieces)
        old = self._pieces[index]
        self.resident_bytes -= old.nbytes if isinstance(old, np.ndarray) else 0
        self._pieces[index] = value
        self.resident_bytes += value.nbytes
        self._spill_index = min(self._spill_index, index)
        self._spill_to_budget()

    def append(self, piece: np.ndarray) -> None:
        self._pieces.append(piece)
        self.resident_bytes += piece.nbytes
        self._spill_to_budget()

    def pop(self):
        piece = self._pieces.pop()
        self.resident_bytes -= piece.nbytes if isinstance(piece, np.ndarray) else 0
        self._spill_index = min(self._spill_index, len(self._pieces))
        return piece

    def _spill_to_budget(self) -> None:
        while self.resident_bytes > self.max_memory_bytes:
            piece = self._pieces[self._spill_index]
            if isinstance(piece, np.ndarray):
                if self._stream is None:
                    self._stream = tempfile.TemporaryFile(mode="w+b", buffering=0)
                self._stream.seek(0, io.SEEK_END)
                offset = self._stream.tell()
                view = memoryview(piece).cast("B")
                written = 0
                while written < len(view):
                    count = self._stream.write(view[written:])
                    if not count:
                        raise OSError("无法写入拼接临时文件，请检查磁盘空间")
                    written += count
                self._pieces[self._spill_index] = DiskImagePiece(self._stream, offset, piece.shape, piece.dtype)
                self.resident_bytes -= piece.nbytes
            self._spill_index += 1

    def close(self) -> None:
        self._pieces.clear()
        self.resident_bytes = 0
        self._spill_index = 0
        if self._stream is not None:
            self._stream.close()
            self._stream = None
