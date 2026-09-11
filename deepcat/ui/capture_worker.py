"""
滚动截图工作线程。

算法参考说明：
- 整体截图-拼接-保存的工作流参考自 ShareX 开源项目：https://github.com/ShareX/ShareX
- 参考文件：ShareX.ScreenCaptureLib/ScrollingCaptureManager.cs
- ShareX 许可证：GNU General Public License v3.0 (GPLv3)
- 本文件针对 Python/PyQt6 环境做了重构：
  1) 移除逐帧裁剪、trim/seam-checking 等复杂微调逻辑；
  2) 正向滚动时实时增量拼接（PiecewiseStitcher.add）；反向滚动时对每帧垂直翻转后
     同样走增量拼接，最后翻转回正常方向——避免缓存全部原始帧造成的 OOM。
"""

from __future__ import annotations

import traceback
import time
import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from deepcat.core.capturer import capture_screen
from deepcat.core.detector import FixedRegions, detect_fixed_regions, is_at_bottom, mean_abs_diff
from deepcat.core.scroller import scroll_down
from deepcat.core.scroll_capture_status import ScrollCaptureOutcome, resolve_scroll_capture_status
from deepcat.core.stitcher import PiecewiseStitcher, StitchQuality
from deepcat.core.image_budget import MAX_INLINE_IMAGE_BYTES
from deepcat.input.hotkey_listener import HotkeyListener
from deepcat.output.naming import make_capture_token
from deepcat.output.scroll_saver import save_scroll_result
from deepcat.utils.logger import get_logger

logger = get_logger()
_MAX_IN_MEMORY_STITCH_BYTES = MAX_INLINE_IMAGE_BYTES


@dataclass(frozen=True)
class CaptureSettings:
    region: Optional[tuple[int, int, int, int]]
    scroll_delay: float
    scroll_amount: int
    scroll_method: str
    max_frames: int
    adaptive_wait: bool
    boost_scroll: bool
    reverse_scroll: bool
    merge_pdf: bool
    merge_image: bool
    dual_output: bool

    strip_height: int
    min_confidence: float

    bottom_diff_mean_threshold: float
    bottom_confirm_count: int

    detect_fixed_header: bool
    detect_fixed_footer: bool
    output_format: str
    jpg_quality: int
    auto_save: bool
    pause_hotkey: str = "<ctrl>+<space>"


class CaptureWorker(QObject):
    progress = pyqtSignal(int)
    metrics = pyqtSignal(int, int, int)
    toggle_overlay = pyqtSignal(bool)
    status = pyqtSignal(str)
    attention = pyqtSignal(str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str, str)
    stopped = pyqtSignal(str)

    def __init__(self, settings: CaptureSettings, stop_event: Optional[threading.Event] = None) -> None:
        super().__init__()
        self._settings = settings
        self._external_stop_event = stop_event or threading.Event()
        self._resume_event = threading.Event()
        self._cancelled = False

    def request_stop(self) -> None:
        self._external_stop_event.set()

    def request_cancel(self) -> None:
        self._cancelled = True
        self._external_stop_event.set()

    def resume(self) -> None:
        self._resume_event.set()

    def run(self) -> None:
        self._active_stitchers: list[PiecewiseStitcher] = []
        try:
            self._run_capture()
        finally:
            for stitcher in self._active_stitchers:
                close = getattr(stitcher, "close", None)
                if callable(close):
                    close()
            self._active_stitchers.clear()

    def _run_capture(self) -> None:
        s = self._settings
        method = str(s.scroll_method) if s.scroll_method else "mouse_wheel"
        manual_mode = method == "manual"
        reverse = bool(getattr(s, "reverse_scroll", False))
        listener = HotkeyListener(
            pause_hotkey=getattr(s, "pause_hotkey", "<ctrl>+<space>"),
            stop_event=self._external_stop_event,
        )
        try:
            listener.start()
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("热键监听启动失败: %s", repr(e))
            logger.error(tb)
            self.failed.emit(str(e) or repr(e), tb)
            return

        try:
            import mss
            sct = mss.mss()
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("截图后端初始化失败: %s", repr(e))
            logger.error(tb)
            listener.cleanup()
            self.failed.emit(str(e) or repr(e), tb)
            return

        prev: Optional[np.ndarray] = None
        bottom_count = 0
        stop_reason: Optional[str] = None
        should_break_after_frame = False
        frame_count = 0
        fixed: Optional[FixedRegions] = None
        sample_frames: list[np.ndarray] = []
        started_at = time.monotonic()
        capture_token = make_capture_token()

        # 新拼接系统（ShareX 风格）
        result: Optional[np.ndarray] = None
        stitcher = PiecewiseStitcher()
        self._active_stitchers.append(stitcher)
        stitch_degraded = False
        skipped_frames = 0

        # 反向滚动：对每帧垂直翻转后增量拼接，结束时再翻转回来。
        # 取代原"缓存全部原始帧 + 最后统一拼接"的实现，消除长页面 OOM 风险。
        reverse_stitcher: Optional[PiecewiseStitcher] = PiecewiseStitcher() if reverse else None
        if reverse_stitcher is not None:
            self._active_stitchers.append(reverse_stitcher)
        scroll_anchor_index = 0
        scroll_rescue_count = 0

        def handle_failsafe() -> None:
            self.status.emit("已暂停：鼠标请勿移动到屏幕四角")
            self._resume_event.clear()
            self.attention.emit("鼠标请勿移动到屏幕四角，请将鼠标移回屏幕中间后点击确定继续。")
            # 等待用户确认恢复；期间保持对停止/取消请求的响应，避免 worker 最长挂起 1 小时。
            while not self._resume_event.wait(timeout=0.2):
                if self._external_stop_event.is_set() or listener.should_stop():
                    return
            try:
                import ctypes
                from pynput.mouse import Controller
            except Exception:
                self._external_stop_event.wait(0.3)
                return
            # 角落检测覆盖整个虚拟桌面（多显示器下副屏角落同样触发 failsafe）
            SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
            SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
            vx = int(ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
            vy = int(ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
            vw = int(ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
            vh = int(ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
            if vw <= 0 or vh <= 0:
                vx, vy, vw, vh = 0, 0, int(ctypes.windll.user32.GetSystemMetrics(0)), int(ctypes.windll.user32.GetSystemMetrics(1))
            mouse = Controller()
            margin = 2
            right = vx + vw - 1
            bottom = vy + vh - 1
            while True:
                if self._external_stop_event.is_set() or listener.should_stop():
                    return
                x, y = mouse.position
                x = int(x)
                y = int(y)
                in_corner = (
                    (x <= vx + margin and y <= vy + margin)
                    or (x <= vx + margin and y >= bottom - margin)
                    or (x >= right - margin and y <= vy + margin)
                    or (x >= right - margin and y >= bottom - margin)
                )
                if not in_corner:
                    break
                self._external_stop_event.wait(0.05)
            self.status.emit("继续截图中...")

        def hide_overlay() -> None:
            try:
                self.toggle_overlay.emit(False)
            except Exception:
                pass

        def show_overlay() -> None:
            try:
                self.toggle_overlay.emit(True)
            except Exception:
                pass

        def grab_main() -> np.ndarray:
            return capture_screen(s.region, sct=sct)

        def _scroll_anchor_points() -> list[tuple[int, int]]:
            region = s.region
            if region is None:
                return []
            left, top, width, height = [int(v) for v in region]
            if width <= 0 or height <= 0:
                return []
            margin_x = min(max(16, width // 12), max(1, width // 3))
            margin_y = min(max(16, height // 12), max(1, height // 3))

            def clamp_point(rx: float, ry: float) -> tuple[int, int]:
                x = int(round(left + width * float(rx)))
                y = int(round(top + height * float(ry)))
                x = max(left + margin_x, min(left + width - margin_x, x))
                y = max(top + margin_y, min(top + height - margin_y, y))
                return (int(x), int(y))

            points = [
                clamp_point(0.50, 0.50),
                clamp_point(0.50, 0.66),
                clamp_point(0.50, 0.34),
                clamp_point(0.34, 0.50),
                clamp_point(0.66, 0.50),
            ]
            unique: list[tuple[int, int]] = []
            for pt in points:
                if pt not in unique:
                    unique.append(pt)
            return unique

        scroll_anchor_points = _scroll_anchor_points()

        def _move_cursor_to_scroll_anchor(index: int = 0) -> None:
            if manual_mode or method not in {"mouse_wheel", "scroll_message"}:
                return
            if not scroll_anchor_points:
                return
            x, y = scroll_anchor_points[int(index) % len(scroll_anchor_points)]
            try:
                import ctypes
                ctypes.windll.user32.SetCursorPos(int(x), int(y))
                self._external_stop_event.wait(0.015)
                return
            except Exception:
                pass
            try:
                import pyautogui
                pyautogui.PAUSE = 0.0
                pyautogui.moveTo(int(x), int(y), duration=0)
                self._external_stop_event.wait(0.015)
            except Exception:
                pass

        def _quick_sample() -> np.ndarray:
            region = s.region
            if region is not None:
                left, top, width, height = region
                w = int(max(220, width // 2))
                h = int(max(220, height // 2))
                x = int(left + max(0, (width - w) // 2))
                y = int(top + max(0, (height - h) // 2))
                img = capture_screen((x, y, w, h), sct=sct)
                return img[::4, ::4, :]
            img = capture_screen(None, sct=sct)
            return img[::6, ::6, :]

        _MAX_CONSECUTIVE_SCROLL_FAILURES = 3

        def do_scroll(amount_value: int) -> None:
            """发送一次滚动指令。失败不再静默：记录连续失败次数，
            由主循环据此显式停止，避免把滚动失败误报成"已到达底部"。"""
            nonlocal scroll_start_time, consecutive_scroll_failures, scroll_failed_last
            try:
                # 传入 delay=0.0：滚动消息发出后不强制等待，剩余延时在下一轮循环头部精准补齐。
                _move_cursor_to_scroll_anchor(scroll_anchor_index)
                scroll_down(amount_value, 0.0, method=method)
                consecutive_scroll_failures = 0
                scroll_failed_last = False
                scroll_start_time = time.monotonic()
            except BaseException as e:
                if e.__class__.__name__ == "FailSafeException":
                    handle_failsafe()
                    scroll_start_time = time.monotonic()
                    return
                if isinstance(e, Exception):
                    consecutive_scroll_failures += 1
                    scroll_failed_last = True
                    logger.warning(
                        "滚动指令发送失败（连续第 %d 次）: %s", consecutive_scroll_failures, repr(e)
                    )
                    self._external_stop_event.wait(min(0.5, 0.1 * consecutive_scroll_failures))
                    return
                raise


        def _fast_similarity(a: np.ndarray, b: np.ndarray) -> float:
            if a.shape != b.shape:
                return 0.0
            diff = np.abs(a.astype(np.int16) - b.astype(np.int16))
            max_possible = 255.0 * float(diff.size)
            return float(1.0 - (float(diff.sum()) / max_possible))

        def wait_until_stable(pre_sample: np.ndarray, max_wait: float) -> None:
            self._external_stop_event.wait(0.06)
            start = time.monotonic()
            check_interval = 0.03
            stability_threshold = 0.995
            change_threshold = 0.9995

            prev_s = pre_sample
            seen_change = False

            while time.monotonic() - start < max_wait:
                if self._external_stop_event.is_set() or listener.should_stop():
                    return
                self._external_stop_event.wait(check_interval)
                curr_s = _quick_sample()
                if not seen_change:
                    if _fast_similarity(pre_sample, curr_s) < change_threshold:
                        seen_change = True
                        prev_s = curr_s
                        continue
                sim = _fast_similarity(prev_s, curr_s)
                if seen_change and sim >= stability_threshold:
                    return
                prev_s = curr_s

        try:
            max_frames = 0 if manual_mode else int(s.max_frames)
            i = 0
            scroll_start_time = None
            pre_sample = None
            scroll_failed_last = False
            consecutive_scroll_failures = 0

            amount = int(s.scroll_amount)
            if bool(s.boost_scroll) and amount:
                boosted = min(150, max(abs(amount) * 3, 60))
                amount = -boosted if amount < 0 else boosted
            if reverse:
                amount = abs(amount)

            while True:
                if max_frames > 0 and i >= max_frames:
                    stop_reason = "已达到最大帧数，自动停止"
                    self.stopped.emit(stop_reason)
                    break
                i += 1
                if self._external_stop_event.is_set() or listener.should_stop():
                    if bool(self._cancelled):
                        self.stopped.emit("用户取消截图")
                        self.finished.emit({"cancelled": True})
                        return
                    stop_reason = "用户手动停止"
                    self.stopped.emit(stop_reason)
                    break

                listener.wait_if_paused()
                if self._external_stop_event.is_set() or listener.should_stop():
                    if bool(self._cancelled):
                        self.stopped.emit("用户取消截图")
                        self.finished.emit({"cancelled": True})
                        return
                    stop_reason = "用户手动停止"
                    self.stopped.emit(stop_reason)
                    break

                # 精准延时等待与网页稳定探测（上一轮发送了滚动指令）
                if scroll_start_time is not None and not manual_mode:
                    delay = float(s.scroll_delay)
                    if bool(s.adaptive_wait):
                        delay = float(min(delay, 0.06))

                    elapsed = time.monotonic() - scroll_start_time
                    remaining_delay = max(0.0, delay - elapsed)

                    if remaining_delay > 0:
                        self._external_stop_event.wait(remaining_delay)

                    if bool(s.adaptive_wait) and pre_sample is not None:
                        wait_until_stable(pre_sample, max_wait=1.2)

                frame = grab_main()

                # 上一轮滚动指令发送失败：内容未变化并非"已到达底部"，
                # 跳过底部判定与重复拼接，直接重试滚动；连续失败达到上限则显式停止。
                if scroll_failed_last:
                    if consecutive_scroll_failures >= _MAX_CONSECUTIVE_SCROLL_FAILURES:
                        stop_reason = "页面滚动失败，已自动停止（目标窗口可能无法滚动）"
                        self.stopped.emit(stop_reason)
                        break
                    self._external_stop_event.wait(0.05)
                    do_scroll(amount)
                    continue

                if manual_mode and prev is not None:
                    if mean_abs_diff(prev, frame) < 0.35:
                        self._external_stop_event.wait(0.06)
                        continue
                if prev is not None and not manual_mode:
                    if is_at_bottom(prev, frame, diff_mean_threshold=s.bottom_diff_mean_threshold):
                        # 容忍系统级滚动消息响应延迟：最多重新抓图4次，每次间隔30ms，以防误判到底而提前退出
                        real_bottom = True
                        for _ in range(4):
                            self._external_stop_event.wait(0.03)
                            frame = grab_main()
                            if not is_at_bottom(prev, frame, diff_mean_threshold=s.bottom_diff_mean_threshold):
                                real_bottom = False
                                break
                        if real_bottom:
                            bottom_count += 1
                            if bottom_count >= int(s.bottom_confirm_count):
                                can_rescue_scroll_focus = (
                                    method in {"mouse_wheel", "scroll_message"}
                                    and bool(scroll_anchor_points)
                                    and int(scroll_rescue_count) < min(4, len(scroll_anchor_points) - 1)
                                    and int(frame_count) <= 2
                                )
                                if bool(can_rescue_scroll_focus):
                                    scroll_rescue_count += 1
                                    scroll_anchor_index = int(scroll_rescue_count)
                                    bottom_count = 0
                                    should_break_after_frame = False
                                    self.status.emit("正在重新定位滚动区域...")
                                else:
                                    stop_reason = "已到达底部，自动停止"
                                    self.stopped.emit(stop_reason)
                                    should_break_after_frame = True
                        else:
                            bottom_count = 0
                    else:
                        bottom_count = 0

                # 固定区域检测（前 3 帧）
                if fixed is None:
                    sample_frames.append(frame)
                    if len(sample_frames) > 3:
                        sample_frames.pop(0)
                    if len(sample_frames) >= 3 and (s.detect_fixed_header or s.detect_fixed_footer):
                        fixed_all = detect_fixed_regions(sample_frames, sample_count=3)
                        fixed = fixed_all
                        if not s.detect_fixed_header:
                            fixed = type(fixed_all)(0, fixed_all.footer_height)
                        if not s.detect_fixed_footer:
                            fixed = type(fixed_all)(fixed_all.header_height, 0)
                        # 确定固定头尾后，立即清空 3 帧大图缓存释放内存
                        sample_frames = []

                # ---------- 核心：拼接 ----------
                if reverse:
                    # 反向滚动：垂直翻转后增量拼接（等价于"reverse 帧序后正向拼接"，但无需缓存全帧）。
                    # ascontiguousarray 保证翻转后内存连续，使拼接行为与正向路径完全一致。
                    assert reverse_stitcher is not None
                    rev_status = reverse_stitcher.add(np.ascontiguousarray(frame[::-1, :, :]))
                    if rev_status == StitchQuality.FAILED:
                        skipped_frames += 1
                    elif rev_status == StitchQuality.PARTIALLY_SUCCESSFUL:
                        stitch_degraded = True
                else:
                    frame_to_stitch = frame
                    if fixed is not None and not stitcher.is_empty:
                        fh = frame.shape[0]
                        crop_top = int(fixed.header_height)
                        crop_bottom = int(fixed.footer_height)
                        if crop_top > 0 or crop_bottom > 0:
                            frame_to_stitch = frame[crop_top : fh - crop_bottom, :, :]

                    stitch_status = stitcher.add(frame_to_stitch)
                    if stitch_status == StitchQuality.FAILED:
                        skipped_frames += 1
                    elif stitch_status == StitchQuality.PARTIALLY_SUCCESSFUL:
                        stitch_degraded = True

                frame_count += 1
                prev = frame

                if should_break_after_frame:
                    break

                self.progress.emit(frame_count)
                elapsed_s = int(max(0.0, time.monotonic() - started_at))
                result_h = int(reverse_stitcher.total_height) if (reverse and reverse_stitcher is not None) else int(stitcher.total_height)
                self.metrics.emit(frame_count, result_h, elapsed_s)

                # ---------- 滚动 ----------
                if manual_mode:
                    self._external_stop_event.wait(0.06)
                    continue

                pre_sample = _quick_sample() if bool(s.adaptive_wait) else None

                do_scroll(amount)
        except BaseException as e:
            tb = traceback.format_exc()
            logger.error("截取过程异常: %s", repr(e))
            logger.error(tb)
            self.failed.emit(str(e) or repr(e), tb)
            return
        finally:
            listener.cleanup()
            try:
                sct.close()
            except Exception:
                pass
            prev = None
            frame = None
            sample_frames = None

        self._finish_capture(
            s, reverse_stitcher if reverse else stitcher, reverse, capture_token,
            stop_reason, stitch_degraded, skipped_frames, frame_count,
        )

    def _finish_capture(
        self, s: CaptureSettings, active_stitcher: PiecewiseStitcher, reverse: bool,
        capture_token: str, stop_reason: Optional[str], stitch_degraded: bool,
        skipped_frames: int, frame_count: int,
    ) -> None:
        memory_profile_fn = getattr(active_stitcher, "memory_profile", None)
        memory_profile = memory_profile_fn() if callable(memory_profile_fn) else None
        estimated_peak_bytes = int(memory_profile.estimated_peak_bytes) if memory_profile is not None else 0
        chunked = False
        preview_image_bgr = None

        if active_stitcher is None or active_stitcher.is_empty:
            self.failed.emit("截取帧数不足", "")
            return

        if (memory_profile is not None and int(memory_profile.result_bytes) > _MAX_IN_MEMORY_STITCH_BYTES
                and s.auto_save and str(s.output_format or "png").lower() == "png" and not s.dual_output):
            # 逐块编码成一张完整 PNG；预览随后读取原图，不使用缩略图代替。
            self.status.emit("正在保存完整长图...")
            try:
                saved = save_scroll_result(active_stitcher, s, capture_token, chunked=True, vertical_flip=reverse)
            except Exception as e:
                self.failed.emit(str(e) or repr(e), traceback.format_exc())
                return
            chunked = True
            estimated_peak_bytes = int(memory_profile.estimated_chunked_peak_bytes)
            result = None
        elif reverse:
            # 拼接时每帧已垂直翻转，此处直接产出翻转回正常阅读方向的长图
            # （单份输出缓冲，避免先物化再翻转的 3 倍内存峰值）。
            try:
                result = active_stitcher.result_image(vertical_flip=True)
                if result is None or result.size == 0:
                    self.failed.emit("截取帧数不足", "")
                    return
            except Exception as e:
                self.failed.emit(str(e) or repr(e), traceback.format_exc())
                return
        else:
            try:
                result = active_stitcher.result_image()
                if result is None or result.size == 0:
                    self.failed.emit("截取帧数不足", "")
                    return
            except Exception as e:
                self.failed.emit(str(e) or repr(e), traceback.format_exc())
                return

        # ---------- 保存 ----------
        try:
            if not chunked:
                saved = save_scroll_result(result, s, capture_token)
                preview_image_bgr = result
            completion_status = resolve_scroll_capture_status(
                stop_reason=stop_reason,
                stitch_degraded=stitch_degraded,
                skipped_frames=skipped_frames,
            )
            outcome = ScrollCaptureOutcome(
                result=saved,
                status=completion_status,
                stop_reason=str(stop_reason or ""),
                frame_count=int(frame_count),
                captured_height=int(active_stitcher.total_height if active_stitcher is not None else 0),
                estimated_peak_bytes=estimated_peak_bytes,
                chunked=chunked,
                skipped_frames=int(skipped_frames),
                preview_image_bgr=preview_image_bgr,
            )
            self.status.emit(outcome.message)
            self.finished.emit(outcome)
        except BaseException as e:
            tb = traceback.format_exc()
            logger.error("保存过程异常: %s", repr(e))
            logger.error(tb)
            self.failed.emit(str(e) or repr(e), tb)
