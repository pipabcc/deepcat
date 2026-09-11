"""
滚动截图拼接算法模块。

算法参考说明：
- combine_images / _find_best_match 的核心匹配与拼接逻辑参考自 ShareX 开源项目：
  https://github.com/ShareX/ShareX
- 参考文件：ShareX.ScreenCaptureLib/ScrollingCaptureManager.cs（CombineImages 方法）
- ShareX 许可证：GNU General Public License v3.0 (GPLv3)
- 本文件在 ShareX 算法基础上做了以下调整：
  1) 用 Python/numpy 做向量化重写；
  2) 针对小高度截取区域（<=600px）扩大 match_limit 搜索范围，防止漏匹配；
  3) 保留 BestGuess 回退、底部自动忽略、两侧忽略等机制。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional

import numpy as np

from deepcat.core.image_budget import MAX_RESIDENT_PIECE_BYTES, bounded_chunk_rows
from deepcat.core.image_piece_store import DiskImagePiece, ImagePieceStore


@dataclass(frozen=True)
class OverlapResult:
    overlap_end_y: int
    confidence: float


@dataclass
class CombineState:
    """跨帧 BestGuess 状态，用于 ShareX 风格拼接容错。"""

    best_match_count: int = 0
    best_match_index: int = 0
    best_ignore_bottom: int = 0


class StitchQuality(str, Enum):
    SUCCESSFUL = "successful"
    PARTIALLY_SUCCESSFUL = "partially_successful"
    FAILED = "failed"


@dataclass(frozen=True)
class StitchMemoryProfile:
    piece_bytes: int
    tail_bytes: int
    result_bytes: int
    estimated_peak_bytes: int
    chunk_buffer_bytes: int
    estimated_chunked_peak_bytes: int


@dataclass
class CombineResult:
    image: Optional[np.ndarray]
    state: CombineState
    status: StitchQuality


class PiecewiseStitcher:
    """Incremental stitcher that stores output fragments and composes once."""

    def __init__(
        self,
        *,
        auto_ignore_bottom_edge: bool = True,
        ignore_side_offset_ratio: float = 0.05,
        min_side_offset: int = 50,
        max_memory_bytes: int = MAX_RESIDENT_PIECE_BYTES,
    ) -> None:
        self.auto_ignore_bottom_edge = bool(auto_ignore_bottom_edge)
        self.ignore_side_offset_ratio = float(ignore_side_offset_ratio)
        self.min_side_offset = int(min_side_offset)
        self.state = CombineState()
        self._pieces = ImagePieceStore(max_memory_bytes)
        self._tail: Optional[np.ndarray] = None
        self._target_w: Optional[int] = None
        self._total_height = 0
        self._tail_keep_rows = 0

    @property
    def total_height(self) -> int:
        return int(self._total_height)

    @property
    def width(self) -> int:
        return int(self._target_w or 0)

    def close(self) -> None:
        self._pieces.close()
        self._tail = None
        self._total_height = 0

    @property
    def is_empty(self) -> bool:
        return not self._pieces

    def _append_piece(self, piece: np.ndarray) -> None:
        if piece is None or piece.size == 0:
            return
        if self._target_w is None:
            self._target_w = int(piece.shape[1])
        else:
            self._target_w = int(min(self._target_w, int(piece.shape[1])))
        cropped = piece[:, : int(self._target_w), :].copy()
        self._pieces.append(cropped)
        self._total_height += int(cropped.shape[0])

    def _trim_bottom(self, rows: int) -> None:
        remaining = int(max(0, rows))
        if remaining <= 0:
            return
        while remaining > 0 and self._pieces:
            last = self._pieces[-1]
            h = int(last.shape[0])
            if remaining >= h:
                self._pieces.pop()
                self._total_height -= h
                remaining -= h
                continue
            keep_h = h - remaining
            self._pieces[-1] = last[:keep_h, :, :].copy()
            self._total_height -= remaining
            remaining = 0
        if self._total_height < 0:
            self._total_height = 0

    def _rebuild_tail(self, min_rows: int) -> None:
        self._tail_keep_rows = int(max(self._tail_keep_rows, min_rows, 1))
        target_w = int(self._target_w or 0)
        if target_w <= 0 or not self._pieces:
            self._tail = None
            return
        need = int(min(self._tail_keep_rows, self._total_height))
        chunks: list[np.ndarray] = []
        remaining = need
        for piece in reversed(self._pieces):
            if remaining <= 0:
                break
            h = int(piece.shape[0])
            take = int(min(h, remaining))
            chunks.append(piece[h - take : h, :target_w, :])
            remaining -= take
        chunks.reverse()
        if not chunks:
            self._tail = None
        elif len(chunks) == 1:
            self._tail = chunks[0].copy()
        else:
            self._tail = np.concatenate(chunks, axis=0)

    def add(self, current: np.ndarray) -> StitchQuality:
        if current is None or current.size == 0:
            return StitchQuality.FAILED

        h, w = current.shape[:2]
        if h <= 0 or w <= 0:
            return StitchQuality.FAILED

        if self.is_empty:
            self._append_piece(current)
            self._rebuild_tail(h)
            return StitchQuality.SUCCESSFUL

        target_w = int(self._target_w or w)
        H = int(self._total_height)
        tail = self._tail
        if tail is None or tail.size == 0 or H <= 0:
            return StitchQuality.FAILED

        tail_h = int(tail.shape[0])
        W = int(target_w)

        ignore_side = max(self.min_side_offset, int(W * self.ignore_side_offset_ratio))
        ignore_side = min(ignore_side, W // 3, w // 3)

        W_cmp = min(W - ignore_side * 2, w - ignore_side * 2)
        if W_cmp <= 0:
            ignore_side = 0
            W_cmp = min(W, w)

        result_cmp = tail[:, ignore_side : ignore_side + W_cmp, :]
        curr_cmp = current[:, ignore_side : ignore_side + W_cmp, :]

        ignore_bottom = 0
        if self.auto_ignore_bottom_edge:
            ignore_bottom_max = h // 3
            ignore_bottom_base = max(50, h // 10)
            max_check = min(tail_h, h, ignore_bottom_max)
            for i in range(max_check):
                if not np.array_equal(result_cmp[tail_h - 1 - i], curr_cmp[h - 1 - i]):
                    ignore_bottom = ignore_bottom_base + i
                    break
            else:
                ignore_bottom = ignore_bottom_base
            ignore_bottom = max(ignore_bottom, self.state.best_ignore_bottom)
            ignore_bottom = min(ignore_bottom, ignore_bottom_max, max(0, tail_h - 1))

        effective_h = min(h, H, tail_h)
        search_h = effective_h - ignore_bottom
        if search_h <= 0:
            return StitchQuality.FAILED

        result_search = result_cmp[max(0, tail_h - effective_h) : tail_h - ignore_bottom, :, :]
        curr_search = curr_cmp[:, :, :]

        ch = curr_search.shape[0]
        match_limit = ch if ch <= 600 else max(50, search_h // 2)
        match_idx, match_count, used_best_guess = _find_best_match(
            result_search,
            curr_search,
            ignore_bottom=ignore_bottom,
            match_limit=match_limit,
            prev_state=self.state,
        )

        if match_count <= 0:
            return StitchQuality.FAILED

        match_height = curr_search.shape[0] - match_idx - 1
        if match_height <= 0:
            return StitchQuality.FAILED

        if match_count > self.state.best_match_count:
            self.state.best_match_count = match_count
            self.state.best_match_index = match_idx
            self.state.best_ignore_bottom = ignore_bottom

        new_w = int(min(int(self._target_w or w), w))
        if self._target_w is None or new_w < int(self._target_w):
            self._target_w = new_w

        self._trim_bottom(ignore_bottom)
        src_start = match_idx + 1
        src_end = src_start + match_height
        self._append_piece(current[src_start:src_end, : int(self._target_w), :])
        self._rebuild_tail(h)
        return StitchQuality.PARTIALLY_SUCCESSFUL if used_best_guess else StitchQuality.SUCCESSFUL

    def memory_profile(self, chunk_rows: int = 14000) -> StitchMemoryProfile:
        piece_bytes = self._pieces.resident_bytes
        tail_bytes = int(self._tail.nbytes) if self._tail is not None and self._tail.size > 0 else 0
        bytes_per_row = 0
        if self._pieces and self._target_w:
            sample = self._pieces[0]
            channels = int(sample.shape[2]) if sample.ndim >= 3 else 1
            bytes_per_row = int(self._target_w) * channels * int(sample.dtype.itemsize)
        result_bytes = int(self._total_height) * bytes_per_row
        read_buffer_bytes = max((piece.nbytes for piece in self._pieces if isinstance(piece, DiskImagePiece)), default=0)
        chunk_buffer_bytes = bounded_chunk_rows(int(self._target_w or 0), chunk_rows, self._total_height) * bytes_per_row
        return StitchMemoryProfile(
            piece_bytes=piece_bytes,
            tail_bytes=tail_bytes,
            result_bytes=result_bytes,
            estimated_peak_bytes=piece_bytes + tail_bytes + result_bytes + read_buffer_bytes,
            chunk_buffer_bytes=chunk_buffer_bytes,
            # 消费方可能仍持有上一块，另计读取落盘片段的临时缓冲。
            estimated_chunked_peak_bytes=piece_bytes + tail_bytes + chunk_buffer_bytes * 2 + read_buffer_bytes,
        )

    def iter_result_chunks(self, max_rows: int = 14000, *, vertical_flip: bool = False) -> Iterator[np.ndarray]:
        """按固定高度组合输出，避免额外分配完整长图。

        vertical_flip=True 时输出整体垂直翻转后的结果（反向滚动拼接的还原）：
        按"倒序片段 + 逐片段翻转"产出，全程只有块大小的拷贝，不构造完整长图。
        """
        if not self._pieces or self._target_w is None:
            return
        target_w = int(self._target_w)
        chunk_rows = bounded_chunk_rows(target_w, max_rows, self._total_height)
        pieces = [piece for piece in self._pieces if piece.size > 0]
        if vertical_flip:
            pieces = list(reversed(pieces))
        buffer = np.empty((chunk_rows, target_w, 3), dtype=np.uint8)
        filled = 0
        for piece in pieces:
            source = piece[:, :target_w, :]
            if vertical_flip:
                source = source[::-1, :, :]
            offset = 0
            while offset < int(source.shape[0]):
                take = min(chunk_rows - filled, int(source.shape[0]) - offset)
                buffer[filled : filled + take, :, :] = source[offset : offset + take, :, :]
                filled += take
                offset += take
                if filled == chunk_rows:
                    yield buffer
                    buffer = np.empty((chunk_rows, target_w, 3), dtype=np.uint8)
                    filled = 0
            del source
        if filled > 0:
            yield buffer[:filled, :, :]

    def result_image(self, *, vertical_flip: bool = False) -> Optional[np.ndarray]:
        """拼接结果。vertical_flip=True 直接产出翻转后的图（单份输出缓冲，
        避免先物化再翻转造成的 3 倍内存峰值）。"""
        if not self._pieces or self._target_w is None:
            return None
        target_w = int(self._target_w)
        total_h = sum(int(piece.shape[0]) for piece in self._pieces if piece.size > 0)
        if total_h <= 0:
            return None
        try:
            out = np.empty((int(total_h), int(target_w), 3), dtype=np.uint8)
        except MemoryError as e:
            need_mb = self.memory_profile().result_bytes / (1024 * 1024)
            raise MemoryError(f"拼接结果过大，无法分配约 {need_mb:.1f} MiB 内存") from e
        pieces = [piece for piece in self._pieces if piece.size > 0]
        if vertical_flip:
            pieces = list(reversed(pieces))
        y = 0
        for piece in pieces:
            src = piece[:, :target_w, :]
            if vertical_flip:
                src = src[::-1, :, :]
            piece_h = int(src.shape[0])
            out[y : y + piece_h, :, :] = src
            y += piece_h
            del src
        return out


def _find_best_match(
    result_bottom: np.ndarray,
    curr: np.ndarray,
    ignore_bottom: int = 0,
    match_limit: Optional[int] = None,
    prev_state: Optional[CombineState] = None,
) -> tuple[int, int, bool]:
    """
    ShareX 风格匹配：在 result_bottom（result 底部区域）和 curr 之间寻找最大连续匹配。

    返回: (match_index_in_curr, match_count, used_best_guess)
    """
    sh, ch, w = result_bottom.shape[0], curr.shape[0], curr.shape[1]
    if sh <= 0 or ch <= 0 or w <= 0:
        return 0, 0, False

    if match_limit is None:
        match_limit = max(50, ch // 2)

    # 展平每行用于快速比较
    a = result_bottom.reshape(sh, -1)
    b = curr.reshape(ch, -1)
    rev_a = a[::-1]  # result_bottom 底行 -> rev_a[0]

    best_count = 0
    best_idx = 0

    # 限制搜索范围：从 curr 底部向上 match_limit
    search_start = max(0, ch - match_limit)

    for curr_y in range(ch - 1, search_start - 1, -1):
        max_len = min(curr_y + 1, sh)
        if max_len <= best_count:
            continue  # 不可能超过当前最佳

        # 1. 高速行过滤：必要条件是 curr_y 行与 result_bottom 的最后一匹配行完全一致。
        # np.array_equal 是 C 级且带 Early Exit，在不匹配时可以在微秒内返回，瞬间过滤掉 99% 的非匹配位置。
        if not np.array_equal(b[curr_y], rev_a[0]):
            continue

        # 2. 深度向上行匹配检测：一旦有任何一行不匹配，立刻退出
        count = 1
        while count < max_len:
            if np.array_equal(b[curr_y - count], rev_a[count]):
                count += 1
            else:
                break

        if count > best_count:
            best_count = count
            best_idx = curr_y
            if best_count >= match_limit:
                break

    # BestGuess：如果当前无匹配，回退历史最佳
    used_best_guess = False
    if best_count == 0 and prev_state is not None and prev_state.best_match_count > 0:
        best_count = prev_state.best_match_count
        best_idx = prev_state.best_match_index
        ignore_bottom = prev_state.best_ignore_bottom
        used_best_guess = True

    return best_idx, best_count, used_best_guess


def combine_images(
    result: Optional[np.ndarray],
    current: np.ndarray,
    auto_ignore_bottom_edge: bool = True,
    ignore_side_offset_ratio: float = 0.05,
    min_side_offset: int = 50,
    state: Optional[CombineState] = None,
) -> CombineResult:
    """
    ShareX 风格拼接算法。

    Args:
        result: 当前拼接结果，None 表示第一帧
        current: 新截图 [h, w, 3] BGR
        auto_ignore_bottom_edge: 是否自动检测底部固定边缘（状态栏/工具栏）
        ignore_side_offset_ratio: 两侧忽略宽度占图片宽度的比例
        min_side_offset: 两侧忽略的最小像素
        state: 上一次的 CombineState，用于 BestGuess 回退

    Returns:
        CombineResult
    """
    if state is None:
        state = CombineState()

    if result is None:
        return CombineResult(image=current.copy(), state=state, status=StitchQuality.SUCCESSFUL)

    H, W = result.shape[:2]
    h, w = current.shape[:2]

    # 1. 两侧忽略（避免滚动条干扰）
    ignore_side = max(min_side_offset, int(W * ignore_side_offset_ratio))
    ignore_side = min(ignore_side, W // 3, w // 3)

    W_cmp = min(W - ignore_side * 2, w - ignore_side * 2)
    if W_cmp <= 0:
        ignore_side = 0
        W_cmp = min(W, w)

    result_cmp = result[:, ignore_side : ignore_side + W_cmp, :]
    curr_cmp = current[:, ignore_side : ignore_side + W_cmp, :]

    # 2. 底部自动忽略（检测固定底部栏）
    ignore_bottom = 0
    if auto_ignore_bottom_edge:
        ignore_bottom_max = h // 3
        ignore_bottom_base = max(50, h // 10)

        # 从底部向上逐行比较
        max_check = min(H, h, ignore_bottom_max)
        for i in range(max_check):
            if not np.array_equal(result_cmp[H - 1 - i], curr_cmp[h - 1 - i]):
                ignore_bottom = ignore_bottom_base + i
                break
        else:
            ignore_bottom = ignore_bottom_base

        # 与历史最佳取较大值（ShareX 逻辑：避免底部栏闪烁导致 ignore_bottom 变小）
        ignore_bottom = max(ignore_bottom, state.best_ignore_bottom)
        ignore_bottom = min(ignore_bottom, ignore_bottom_max)

    # 3. 确定搜索区域
    # ShareX 逻辑：result 中取 [H-effective_h : H-ignore_bottom] 区域与 current 全图匹配
    effective_h = min(h, H)
    search_h = effective_h - ignore_bottom
    if search_h <= 0:
        return CombineResult(image=None, state=state, status=StitchQuality.FAILED)

    result_search = result_cmp[max(0, H - effective_h) : H - ignore_bottom, :, :]
    curr_search = curr_cmp[:, :, :]  # 完整高度参与匹配

    # 4. 寻找最佳匹配
    # 当区域高度较小时，滚动产生的重叠区可能不在 curr 底部，必须扩大搜索范围。
    # ShareX 原版 matchLimit = Max(50, searchHeight/2) 在大窗口下有效，
    # 但对 h<500 的小区域会导致最佳匹配点在搜索范围外而拼接失败。
    ch = curr_search.shape[0]
    match_limit = ch if ch <= 600 else max(50, search_h // 2)
    match_idx, match_count, used_best_guess = _find_best_match(
        result_search,
        curr_search,
        ignore_bottom=ignore_bottom,
        match_limit=match_limit,
        prev_state=state,
    )

    if match_count > 0:
        match_height = curr_search.shape[0] - match_idx - 1
        if match_height > 0:
            # 更新 BestGuess 状态
            if match_count > state.best_match_count:
                state.best_match_count = match_count
                state.best_match_index = match_idx
                state.best_ignore_bottom = ignore_bottom

            # 5. 拼接
            new_h = H - ignore_bottom + match_height
            new_w = min(W, w)
            new_result = np.empty((new_h, new_w, 3), dtype=np.uint8)

            # result 保留到 H - ignore_bottom
            new_result[: H - ignore_bottom, :, :] = result[: H - ignore_bottom, :new_w, :]
            # current 从 match_idx + 1 开始的 match_height 像素
            src_start = match_idx + 1
            src_end = src_start + match_height
            new_result[H - ignore_bottom :, :, :] = current[src_start:src_end, :new_w, :]

            status = StitchQuality.PARTIALLY_SUCCESSFUL if used_best_guess else StitchQuality.SUCCESSFUL
            return CombineResult(image=new_result, state=state, status=status)

    return CombineResult(image=None, state=state, status=StitchQuality.FAILED)


def stitch_frames(
    frames: list[np.ndarray],
    auto_ignore_bottom_edge: bool = True,
    ignore_side_offset_ratio: float = 0.05,
    min_side_offset: int = 50,
) -> tuple[np.ndarray, StitchQuality]:
    """Stitch frames with O(n) byte movement by composing fragments once."""
    if not frames:
        raise ValueError("frames 为空")

    stitcher = PiecewiseStitcher(
        auto_ignore_bottom_edge=auto_ignore_bottom_edge,
        ignore_side_offset_ratio=ignore_side_offset_ratio,
        min_side_offset=min_side_offset,
    )
    overall_status = StitchQuality.SUCCESSFUL

    for frame in frames:
        if frame is None or frame.size == 0:
            continue
        status = stitcher.add(frame)
        if status == StitchQuality.FAILED:
            if stitcher.is_empty:
                raise RuntimeError("stitch failed on first frame")
            overall_status = StitchQuality.PARTIALLY_SUCCESSFUL
            continue
        if status == StitchQuality.PARTIALLY_SUCCESSFUL:
            overall_status = StitchQuality.PARTIALLY_SUCCESSFUL

    result = stitcher.result_image()
    if result is None:
        raise RuntimeError("拼接失败：无有效结果")
    return result, overall_status


# ---------------------------------------------------------------------------
# 向后兼容：保留旧的 find_overlap 和 stitch_images
# ---------------------------------------------------------------------------


def find_overlap(
    img_prev_bgr: np.ndarray,
    img_curr_bgr: np.ndarray,
    strip_height: int = 100,
    min_confidence: float = 0.95,
    fixed_regions: Optional[object] = None,
    expected_new_pixels: Optional[float] = None,
    reverse: bool = False,
) -> OverlapResult:
    """基于 OpenCV matchTemplate 的重叠检测（旧算法，保留兼容）。"""
    try:
        import cv2
    except Exception as e:
        raise RuntimeError("未安装 opencv-python，请先 pip install opencv-python") from e

    if img_prev_bgr is None or img_curr_bgr is None:
        return OverlapResult(overlap_end_y=0, confidence=0.0)
    if img_prev_bgr.size == 0 or img_curr_bgr.size == 0:
        return OverlapResult(overlap_end_y=0, confidence=0.0)

    h = int(min(int(img_prev_bgr.shape[0]), int(img_curr_bgr.shape[0])))
    w = int(min(int(img_prev_bgr.shape[1]), int(img_curr_bgr.shape[1])))
    if h <= 2 or w <= 2:
        return OverlapResult(overlap_end_y=0, confidence=0.0)
    prev = img_prev_bgr[:h, :w]
    curr = img_curr_bgr[:h, :w]

    strip_height = int(max(8, min(int(strip_height), h // 2)))

    header_h = 0
    footer_h = 0
    if fixed_regions is not None:
        header_h = max(0, int(getattr(fixed_regions, "header_height", 0)))
        footer_h = max(0, int(getattr(fixed_regions, "footer_height", 0)))
        header_h = int(min(header_h, h - 1))
        footer_h = int(min(footer_h, max(0, (h - 1) - header_h)))

    content_h = int(max(1, h - header_h - footer_h))
    if content_h < 16:
        return OverlapResult(overlap_end_y=int(header_h), confidence=0.0)
    strip_height = int(max(8, min(strip_height, content_h // 2)))

    prev_content = prev[header_h : h - footer_h, :, :]
    curr_content = curr[header_h : h - footer_h, :, :]

    if reverse:
        template_bgr = prev_content[:strip_height, :, :]
    else:
        template_bgr = prev_content[content_h - strip_height : content_h, :, :]
    search_bgr = curr_content[:, :, :]
    if template_bgr.size == 0 or search_bgr.size == 0:
        return OverlapResult(overlap_end_y=int(header_h), confidence=0.0)
    template = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    search_region = cv2.cvtColor(search_bgr, cv2.COLOR_BGR2GRAY)

    margin = max(48, int(w * 0.06))
    if w > 2 * margin + 20:
        template = template[:, margin : w - margin]
        search_region = search_region[:, margin : w - margin]

    result = cv2.matchTemplate(search_region, template, cv2.TM_SQDIFF_NORMED)
    vec = result[:, 0]
    max_y = int(vec.shape[0] - 1)

    if expected_new_pixels is not None and expected_new_pixels > 0:
        if reverse:
            y_pred = int(round(float(expected_new_pixels)))
        else:
            y_pred = int(round(float(content_h - expected_new_pixels - strip_height)))
        dist = np.abs(np.arange(vec.shape[0]) - y_pred)
        penalty_vec = 0.25 * (dist / max(1, max_y))
        vec2 = vec + penalty_vec
    else:
        vec2 = vec

    k = int(min(8, vec2.shape[0]))
    cand_ys = np.argpartition(vec2, k - 1)[:k]
    cand_ys = cand_ys[np.argsort(vec2[cand_ys])]

    template2 = None
    if int(content_h) >= int(strip_height) * 4:
        if reverse:
            template2_bgr = prev_content[2 * strip_height : 3 * strip_height, :, :]
        else:
            template2_bgr = prev_content[content_h - 3 * strip_height : content_h - 2 * strip_height, :, :]
        if template2_bgr.size > 0:
            template2 = cv2.cvtColor(template2_bgr, cv2.COLOR_BGR2GRAY)
            if w > 2 * margin + 20:
                template2 = template2[:, margin : w - margin]

    def absdiff_mean(a: np.ndarray, b: np.ndarray) -> float:
        d = cv2.absdiff(a, b)
        return float(cv2.mean(d)[0])

    best_y = int(cand_ys[0])
    best_val = float(vec[int(best_y)])
    best_score = float("inf")

    expected = float(expected_new_pixels) if expected_new_pixels is not None else None
    filtered: list[int] = []
    if expected is not None and expected > 0:
        for y in cand_ys.tolist():
            if reverse:
                new_px = float(y)
            else:
                new_px = float(content_h - (int(y) + int(strip_height)))
            ratio = new_px / expected if expected > 0 else 1.0
            v_val = float(vec[int(y)])
            if 0.15 <= ratio <= 2.5 or v_val < 0.02:
                filtered.append(int(y))
    cand_list = filtered if filtered else [int(y) for y in cand_ys.tolist()]

    for y in cand_list:
        y = int(max(0, min(max_y, y)))
        y0 = max(0, y - 4)
        y1 = min(max_y, y + 4)
        local_best_y = y
        local_best_s1 = float("inf")
        for yy in range(int(y0), int(y1) + 1):
            s1 = absdiff_mean(search_region[yy : yy + strip_height, :], template)
            if s1 < local_best_s1:
                local_best_s1 = s1
                local_best_y = int(yy)

        s2 = 0.0
        if template2 is not None:
            if reverse:
                y2 = int(local_best_y + 2 * strip_height)
            else:
                y2 = int(local_best_y - 2 * strip_height)
            if 0 <= y2 <= max_y:
                s2 = absdiff_mean(search_region[y2 : y2 + strip_height, :], template2)
            else:
                s2 = 50.0

        penalty = 0.0
        if expected is not None and expected > 0:
            if reverse:
                new_px = float(local_best_y)
            else:
                new_px = float(content_h - (float(local_best_y) + float(strip_height)))
            penalty = abs(new_px - expected) / expected * 8.0

        v = float(vec[int(local_best_y)])
        composite = float(local_best_s1 + 0.6 * s2 + 500.0 * v + penalty)
        if composite < best_score:
            best_score = composite
            best_y = int(local_best_y)
            best_val = float(v)

    if reverse:
        overlap_end_y = int(header_h + best_y)
    else:
        overlap_end_y = int(header_h + best_y + strip_height)
    confidence = float(max(0.0, min(1.0, 1.0 - float(best_val))))
    return OverlapResult(overlap_end_y=overlap_end_y, confidence=confidence)


def stitch_images(
    frames: list[np.ndarray],
    strip_height: int = 100,
    min_confidence: float = 0.95,
    fixed_regions: Optional[object] = None,
) -> np.ndarray:
    """
    将多帧截图拼接为一张长图（返回 BGR numpy array）——旧算法，保留兼容。
    新项目请使用 stitch_frames。
    """
    if not frames:
        raise ValueError("frames 为空")

    header_h = 0
    footer_h = 0
    if fixed_regions is not None:
        header_h = max(0, int(getattr(fixed_regions, "header_height", 0)))
        footer_h = max(0, int(getattr(fixed_regions, "footer_height", 0)))

    pieces_bgr: list[np.ndarray] = []
    prev = frames[0]
    expected_new_px: Optional[float] = None
    if len(frames) == 1:
        pieces_bgr.append(prev)
    else:
        h0 = prev.shape[0]
        end0 = max(1, int(h0 - footer_h))
        pieces_bgr.append(prev[:end0, :, :])

    for idx in range(1, len(frames)):
        curr = frames[idx]
        overlap = find_overlap(
            prev,
            curr,
            strip_height=strip_height,
            min_confidence=min_confidence,
            fixed_regions=fixed_regions,
            expected_new_pixels=expected_new_px,
        )

        h = min(curr.shape[0], prev.shape[0])
        w = min(curr.shape[1], prev.shape[1])
        curr2 = curr[:h, :w]

        top_crop = 0 if idx == 0 else header_h
        bottom_crop = 0 if idx == len(frames) - 1 else footer_h

        start_y = max(int(overlap.overlap_end_y), int(top_crop))
        end_y = int(h - bottom_crop)
        if start_y >= end_y:
            start_y = int(top_crop)

        pieces_bgr.append(curr2[start_y:end_y, :, :])
        piece_h = int(max(1, end_y - start_y))
        if expected_new_px is None:
            expected_new_px = float(piece_h)
        else:
            expected_new_px = float(expected_new_px * 0.7 + float(piece_h) * 0.3)
        prev = curr2

    widths = [p.shape[1] for p in pieces_bgr if p.size > 0]
    if not widths:
        raise RuntimeError("拼接失败：无有效图像片段")
    target_w = min(widths)

    total_h = sum(p.shape[0] for p in pieces_bgr if p.size > 0)
    if total_h <= 0:
        raise RuntimeError("拼接失败：无有效图像片段")

    try:
        out = np.empty((int(total_h), int(target_w), 3), dtype=np.uint8)
    except MemoryError as e:
        need_mb = (int(total_h) * int(target_w) * 3) / (1024 * 1024)
        raise MemoryError(f"拼接结果过大，无法分配约 {need_mb:.1f} MiB 内存") from e

    y = 0
    for p in pieces_bgr:
        if p.size == 0:
            continue
        p2 = p[:, :target_w, :]
        h = int(p2.shape[0])
        out[y : y + h, :, :] = p2
        y += h

    return out
