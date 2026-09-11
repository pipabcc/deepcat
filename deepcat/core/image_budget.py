"""滚动截图各阶段的原始像素预算，不以压缩文件大小估算内存。"""

MAX_RESIDENT_PIECE_BYTES = 64 * 1024 * 1024
MAX_INLINE_IMAGE_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_CHUNK_BYTES = 16 * 1024 * 1024


def bounded_chunk_rows(width: int, max_rows: int, total_rows: int) -> int:
    return max(1, min(max_rows, total_rows, MAX_OUTPUT_CHUNK_BYTES // max(1, width * 3)))
