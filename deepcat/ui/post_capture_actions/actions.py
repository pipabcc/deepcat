from __future__ import annotations

import logging
import shutil
import threading
import time
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import QApplication, QFileDialog, QPushButton, QHBoxLayout, QFrame, QWidget, QGraphicsDropShadowEffect, QLabel, QVBoxLayout
from deepcat.ui.popup_behavior import set_disable_global_tooltip
from deepcat.utils.ocr_runtime import configure_ocr_dll_search_paths
from PyQt6.QtWidgets import QFrame, QPushButton
from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame
from PyQt6.QtGui import QPixmap, QIcon, QColor
from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton

from deepcat.ui.post_capture_actions._shared import logger
from deepcat.ocr_engines import (
    OCR_ENGINE_PPOCRV6,
    OCR_ENGINE_RAPIDOCR,
    normalize_ocr_engine,
    ocr_engine_display_name,
)
from deepcat.ocr_text_layout import format_ocr_entries
from deepcat.ui.post_capture_actions.helpers import classify_ocr_error_message
from deepcat.ui.post_capture_actions.model_menus import OcrGenericMenuPopup
from deepcat.ui.post_capture_actions.tooltips import SmoothToolTip
from deepcat.ui.post_capture_actions.workers import OcrExtractWorker
from deepcat.ui.post_capture_actions.annotation import AnnotationCanvasOverlay
from deepcat.ui.post_capture_actions.text_panel import OcrTextPanel

if TYPE_CHECKING:
    import numpy as np


# 持有正在运行的 OCR worker，防止窗口在识别期间被销毁后 QThread 对象被 GC 导致崩溃
_ACTIVE_OCR_WORKERS: set["OcrExtractWorker"] = set()
# 截图工具成功关闭后，OCR 结果窗口仍需独立存活，直到用户主动关闭。
_ACTIVE_OCR_RESULT_PANELS: set[OcrTextPanel] = set()
_OCR_PERFORMANCE_LOGGER = None
_OCR_PERFORMANCE_LOGGER_LOCK = threading.Lock()


def _retain_ocr_result_panel(panel: Optional[OcrTextPanel]) -> None:
    if panel is None:
        return
    try:
        if not panel.isVisible():
            return
    except (RuntimeError, AttributeError):
        return
    if panel in _ACTIVE_OCR_RESULT_PANELS:
        return

    _ACTIVE_OCR_RESULT_PANELS.add(panel)

    def release_panel(*_args, retained_panel: OcrTextPanel = panel) -> None:
        _ACTIVE_OCR_RESULT_PANELS.discard(retained_panel)

    try:
        panel.destroyed.connect(release_panel)
    except Exception:
        _ACTIVE_OCR_RESULT_PANELS.discard(panel)


def _get_ocr_performance_logger():
    global _OCR_PERFORMANCE_LOGGER
    if _OCR_PERFORMANCE_LOGGER is not None:
        return _OCR_PERFORMANCE_LOGGER
    with _OCR_PERFORMANCE_LOGGER_LOCK:
        if _OCR_PERFORMANCE_LOGGER is None:
            from deepcat.utils.logger import get_log_dir, get_logger

            _OCR_PERFORMANCE_LOGGER = get_logger(
                "deepcat.ocr_performance",
                level=logging.INFO,
                enable_console=False,
                file_path=get_log_dir() / "ocr_performance.log",
            )
    return _OCR_PERFORMANCE_LOGGER


class PostCaptureActions(QWidget):
    _ocr_engine = None
    _ocr_engine_rec_only = None
    _ocr_engines_by_bucket: dict[str, tuple[object, Optional[object]]] = {}
    _ocr_prewarm_started = False
    OCR_DEFAULT_BUDGET_SECONDS = 80.0
    OCR_SMALL_BUDGET_SECONDS = 40.0
    OCR_LARGE_BUDGET_SECONDS = 120.0
    first_frame_ready = pyqtSignal()
    _capture_phase_signal = pyqtSignal(bool)
    _pin_cache_ready = pyqtSignal(int, object, object)
    _gif_export_finished = pyqtSignal(bool, str)
    _icon_cache = {}  # 静态 QIcon 内存缓存，彻底消除 SVG 多次加载与磁盘 IO 瓶颈
    OCR_MAX_IMAGE_HEIGHT = 10000
    _pin_lifecycle_tip_shown = False

    def __init__(
        self,
        region_rect: QRect,
        image_bgr: np.ndarray,
        default_dir: str,
        default_format: str,
        jpg_quality: int,
        capture_frames: int,
        region_px: Optional[tuple[int, int, int, int]],
        on_close: Callable[[], None],
        on_toast: Optional[Callable[[str, str, int], None]] = None,
        on_recording_changed: Optional[Callable[[bool], None]] = None,
        on_annotation_mode_changed: Optional[Callable[[bool], None]] = None,
        on_annotation_tool_changed: Optional[Callable[[str], None]] = None,
        on_annotation_undo: Optional[Callable[[], None]] = None,
        on_annotation_redo: Optional[Callable[[], None]] = None,
        on_annotation_clear: Optional[Callable[[], None]] = None,
        on_annotation_commit: Optional[Callable[[], None]] = None,
        on_render_annotations: Optional[Callable[[np.ndarray], np.ndarray]] = None,
        save_button_auto: bool = True,
        save_button_mode: str = "",
        on_auto_save: Optional[Callable[[np.ndarray], object]] = None,
        on_ai_recognize: Optional[Callable[[np.ndarray], object]] = None,
        on_ocr_panel_visibility_changed: Optional[Callable[[bool], None]] = None,
        button_style: str = "icon",
        auto_show: bool = True,
        on_image_annotated: Optional[Callable[[np.ndarray], None]] = None,
        on_dialog_show: Optional[Callable[[], None]] = None,
        on_dialog_close: Optional[Callable[[], None]] = None,
        annotation_parent: Optional[QWidget] = None,
        ocr_busy_tooltip_anchor: Optional[Callable[[], QPoint]] = None,
        ocr_busy_tooltip_direction: str = "below",
        ocr_busy_tooltip_show: Optional[Callable[[str], None]] = None,
        ocr_busy_tooltip_hide: Optional[Callable[[], None]] = None,
        quick_action_handler: Optional[Callable[[str], None]] = None,
        on_ocr_text_result: Optional[Callable[[str, float], bool]] = None,
    ) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._region = QRect(region_rect)
        self._image_bgr = image_bgr
        self._base_image_bgr = image_bgr
        self._annotation_parent = annotation_parent
        self._default_dir = str(default_dir)
        self._default_format = (default_format or "png").lower()
        self._jpg_quality = int(jpg_quality)
        self._capture_frames = int(capture_frames)
        self._region_px = region_px
        self._on_close = on_close
        self._close_notified = False
        self._closing_all = False
        self._on_toast = on_toast
        self._on_recording_changed = on_recording_changed
        self._on_annotation_mode_changed = on_annotation_mode_changed
        self._on_annotation_tool_changed = on_annotation_tool_changed
        self._on_annotation_undo = on_annotation_undo
        self._on_annotation_redo = on_annotation_redo
        self._on_annotation_clear = on_annotation_clear
        self._on_annotation_commit = on_annotation_commit
        self._on_render_annotations = on_render_annotations
        self._save_button_mode = self._normalize_save_button_mode(save_button_mode, save_button_auto)
        self._save_button_auto = self._save_button_mode == "auto"
        self._on_auto_save = on_auto_save
        self._on_ai_recognize = on_ai_recognize
        self._on_ocr_panel_visibility_changed = on_ocr_panel_visibility_changed
        self._button_style = self._normalize_button_style(button_style)
        self._on_image_annotated = on_image_annotated
        self._on_dialog_show = on_dialog_show
        self._on_dialog_close = on_dialog_close
        self._ocr_busy_tooltip_anchor = ocr_busy_tooltip_anchor
        self._ocr_busy_tooltip_direction = str(ocr_busy_tooltip_direction or "below")
        self._ocr_busy_tooltip_show = ocr_busy_tooltip_show
        self._ocr_busy_tooltip_hide = ocr_busy_tooltip_hide
        self._quick_action_handler = quick_action_handler
        self._on_ocr_text_result = on_ocr_text_result
        self._recording = False
        self._record_stop_event: Optional[threading.Event] = None
        self._record_thread: Optional[threading.Thread] = None
        self._record_path: Optional[str] = None
        self._record_gif_path: Optional[str] = None
        self._record_listener = None
        self._last_pinned = None
        self._capture_hide_border = False
        self._ocr_panel: Optional[OcrTextPanel] = None
        self._ocr_panel_destroyed_callback = None
        self._ocr_worker: Optional[OcrExtractWorker] = None
        self._ocr_cancel_token = None
        self._ocr_active_engine: Optional[str] = None
        self._ocr_started_ts: float = 0.0
        self._ocr_busy_tooltip_custom = False
        self._annotation_overlay: Optional[AnnotationCanvasOverlay] = None
        self._annotation_mode = ""
        self._image_pending = False
        self._first_frame_ready_emitted = False
        self._annotation_can_undo = False
        self._annotation_can_redo = False
        self._pos_before_record: Optional[QPoint] = None
        self._record_started_ts: float = 0.0
        self._pin_cache_seq = 0
        self._pin_cache_dirty = True
        self._pin_cache_bgr = None
        self._pin_cache_pixmap = None
        self._pin_cache_thread: Optional[threading.Thread] = None
        self._icon_tooltip = SmoothToolTip()
        self._record_ui_timer = QTimer(self)
        self._record_ui_timer.setInterval(200)
        self._record_ui_timer.timeout.connect(self._update_record_elapsed)
        self._pin_cache_timer = QTimer(self)
        self._pin_cache_timer.setSingleShot(True)
        self._pin_cache_timer.timeout.connect(self._start_pin_cache_build)
        self._pin_cache_ready.connect(self._accept_pin_cache)
        self._ocr_action_menu_popup: Optional[OcrGenericMenuPopup] = None
        self._ocr_action_menu_hide_timer = QTimer(self)
        self._ocr_action_menu_hide_timer.setSingleShot(True)
        self._ocr_action_menu_hide_timer.timeout.connect(self._hide_ocr_action_menu_if_outside)

        self._lbl_record_time = QLabel("")
        self._lbl_record_time.setStyleSheet(
            "QLabel { background: #FFFFFF; border: 1px solid rgba(0,0,0,0.14); border-radius: 8px; padding: 0 8px; color: #333333; font-size: 12px; }"
        )
        self._lbl_record_time.setFixedHeight(28)
        self._lbl_record_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_record_time.hide()

        self._btn_ocr = QPushButton("识别")
        self._btn_eraser = QPushButton("擦除")
        self._btn_blur = QPushButton("模糊")
        self._btn_record = QPushButton("录制")
        self._btn_pin = QPushButton("贴图")
        self._btn_save = QPushButton("保存")
        self._btn_copy = QPushButton("复制")
        self._btn_close = QPushButton("关闭")
        self._btn_rect = QPushButton("框选")
        self._btn_arrow = QPushButton("箭头")
        self._btn_pen = QPushButton("画笔")
        self._btn_marker = QPushButton("记号")
        self._btn_number = QPushButton("序号")
        self._btn_text = QPushButton("文本")
        self._btn_undo = QPushButton("撤销")
        self._btn_redo = QPushButton("还原")
        self._annotation_mode_buttons = [self._btn_eraser, self._btn_blur, self._btn_rect, self._btn_arrow, self._btn_pen, self._btn_marker, self._btn_number, self._btn_text]
        for b in self._annotation_mode_buttons:
            b.setCheckable(True)
        self._buttons = [
            self._btn_ocr,
            self._btn_eraser,
            self._btn_blur,
            self._btn_record,
            self._btn_save,
            self._btn_copy,
            self._btn_pin,
            self._btn_close,
            self._btn_rect,
            self._btn_arrow,
            self._btn_pen,
            self._btn_marker,
            self._btn_number,
            self._btn_text,
            self._btn_undo,
            self._btn_redo,
        ]

        self._primary_buttons = [self._btn_ocr, self._btn_eraser, self._btn_blur, self._btn_record, self._btn_save, self._btn_pin, self._btn_copy, self._btn_close]
        self._secondary_buttons = [self._btn_rect, self._btn_arrow, self._btn_pen, self._btn_marker, self._btn_number, self._btn_text, self._btn_undo, self._btn_redo]
        self._icon_buttons = [
            self._btn_ocr,
            self._btn_blur,
            self._btn_record,
            self._btn_rect,
            self._btn_arrow,
            self._btn_pen,
            self._btn_number,
            self._btn_marker,
            self._btn_text,
            self._btn_eraser,
            self._btn_undo,
            self._btn_redo,
            self._btn_copy,
            self._btn_save,
            self._btn_pin,
            self._btn_close,
        ]
        self._button_labels = {b: str(b.text()) for b in self._buttons}
        self._static_button_labels = {b: str(b.text()) for b in self._buttons}
        self._button_icon_names = {
            self._btn_ocr: "icon_action_ocr.svg",
            self._btn_eraser: "icon_action_eraser.svg",
            self._btn_blur: "icon_action_blur.svg",
            self._btn_record: "icon_action_record.svg",
            self._btn_save: "icon_action_save.svg",
            self._btn_copy: "icon_action_copy.svg",
            self._btn_pin: "icon_action_pin.svg",
            self._btn_close: "icon_action_close.svg",
            self._btn_rect: "icon_action_rect.svg",
            self._btn_arrow: "icon_action_arrow.svg",
            self._btn_pen: "icon_action_pen.svg",
            self._btn_marker: "icon_action_marker.svg",
            self._btn_number: "icon_action_number.svg",
            self._btn_text: "icon_action_text.svg",
            self._btn_undo: "icon_action_undo.svg",
            self._btn_redo: "icon_action_redo.svg",
        }
        self._button_tooltips = {
            self._btn_ocr: "识别当前截图中的文字",
            self._btn_eraser: "擦除截图上的标注内容",
            self._btn_blur: "模糊截图中的敏感区域",
            self._btn_record: "录制当前截图区域 (MP4/GIF)",
            self._btn_save: "保存当前截图到文件",
            self._btn_copy: "复制当前截图到剪贴板",
            self._btn_pin: "将当前截图置顶为贴图",
            self._btn_close: "退出截图",
            self._btn_rect: "在截图上绘制矩形框",
            self._btn_arrow: "在截图上绘制箭头",
            self._btn_pen: "在截图上自由绘制",
            self._btn_marker: "用记号笔标注截图",
            self._btn_number: "添加序号标记",
            self._btn_text: "添加文字标注",
            self._btn_undo: "撤销上一步标注",
            self._btn_redo: "还原撤销的标注",
        }
        self._asset_dir = Path(__file__).resolve().parent.parent / "assets"
        self._separators = []

        # 创建精致无缝容器 QFrame
        self._container = QFrame(self)
        self._container.setObjectName("ToolbarContainer")

        # 阴影效果
        shadow = QGraphicsDropShadowEffect(self._container)
        shadow.setBlurRadius(14)
        shadow.setColor(QColor(15, 23, 42, 38))  # 精致的浅slate阴影
        shadow.setOffset(0, 4)
        self._container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(6, 6, 6, 6) # 精致的大气内间距
        container_layout.setSpacing(4)

        self._row1 = QHBoxLayout()
        self._row1.setContentsMargins(0, 0, 0, 0)
        self._row1.setSpacing(0)
        self._row2 = QHBoxLayout()
        self._row2.setContentsMargins(0, 0, 0, 0)
        self._row2.setSpacing(0)

        container_layout.addLayout(self._row1)
        container_layout.addLayout(self._row2)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)  # 10px 为阴影留出空间
        root.setSpacing(0)
        root.addWidget(self._container)
        self.setLayout(root)
        self._refresh_save_button_label()
        self._apply_button_style(self._button_style)
        self._refresh_ocr_availability()

        self._btn_ocr.clicked.connect(self._show_ocr_action_menu)
        self._btn_eraser.clicked.connect(lambda *_: self._toggle_annotation_mode("eraser"))
        self._btn_blur.clicked.connect(lambda *_: self._toggle_annotation_mode("blur"))
        self._btn_record.clicked.connect(self._toggle_record)
        self._btn_pin.clicked.connect(self._pin)
        self._btn_save.clicked.connect(self._save_as)
        self._btn_copy.clicked.connect(self._copy)
        self._btn_close.clicked.connect(self._close_all)
        self._btn_rect.clicked.connect(lambda checked=False: self._toggle_annotation_mode("rect"))
        self._btn_arrow.clicked.connect(lambda checked=False: self._toggle_annotation_mode("arrow"))
        self._btn_pen.clicked.connect(lambda checked=False: self._toggle_annotation_mode("pen"))
        self._btn_marker.clicked.connect(lambda checked=False: self._toggle_annotation_mode("marker"))
        self._btn_number.clicked.connect(lambda checked=False: self._toggle_annotation_mode("number"))
        self._btn_text.clicked.connect(lambda checked=False: self._toggle_annotation_mode("text"))
        self._btn_undo.clicked.connect(self._annotation_undo)
        self._btn_redo.clicked.connect(self._annotation_redo)
        self._update_annotation_history(False, False)

        self._shortcuts = []
        for seq, callback in [
            ("Alt+T", self._pin),
            ("Alt+S", self._save_as),
            ("Alt+C", self._copy),
        ]:
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        esc_shortcut = QShortcut(QKeySequence("Esc"), self)
        esc_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        esc_shortcut.activated.connect(self.handle_escape)
        self._shortcuts.append(esc_shortcut)

        self.installEventFilter(self)
        for b in self._buttons:
            set_disable_global_tooltip(b)
            b.installEventFilter(self)
        self._capture_phase_signal.connect(self._apply_capture_phase)
        self._reposition()
        if bool(auto_show):
            self.ensurePolished()
            if self.layout() is not None:
                self.layout().activate()
            QApplication.sendPostedEvents(self)
            self.show()
            self.raise_()
            try:
                self.activateWindow()
                self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            except Exception:
                pass
        self._invalidate_pin_cache(schedule=False)


    def _normalize_button_style(self, style: str) -> str:
        return "text" if str(style or "").strip().lower() in {"text", "文字", "文字按钮"} else "icon"

    def _normalize_save_button_mode(self, mode: str, save_button_auto: bool) -> str:
        value = str(mode or "").strip().lower()
        if value in {"manual", "manual_save", "手动", "手动保存", "手动保存（自己选路径）"}:
            return "manual"
        if value in {"auto", "automatic", "自动", "自动保存"}:
            return "auto"
        return "auto" if bool(save_button_auto) else "manual"

    def _refresh_save_button_label(self) -> None:
        if not hasattr(self, "_btn_save"):
            return
        label = "保存"
        if hasattr(self, "_static_button_labels"):
            self._static_button_labels[self._btn_save] = label
        if hasattr(self, "_button_labels"):
            self._button_labels[self._btn_save] = label
        try:
            self._set_action_button_text(self._btn_save, label)
        except Exception:
            try:
                self._btn_save.setText(label)
                self._btn_save.setToolTip(label)
            except Exception:
                pass

    def _tooltip_for_action_button(self, button: QPushButton, label: str = "") -> str:
        text = str(label or "")
        static_text = str(getattr(self, "_static_button_labels", {}).get(button, "") or "")
        if text and text != static_text:
            return text
        tooltip_map = getattr(self, "_button_tooltips", {})
        return str(tooltip_map.get(button) or text or static_text)

    def _show_action_toast(self, title: str, message: str, duration_ms: int = 1800) -> None:
        callback = getattr(self, "_on_toast", None)
        if callback is None:
            return
        try:
            callback(str(title), str(message), int(duration_ms))
        except RuntimeError:
            pass
        except Exception:
            pass

    @staticmethod
    def _capture_action_error_message(exc: object, default_message: str) -> str:
        text = str(exc or "").strip()
        lower = text.lower()
        if isinstance(exc, PermissionError) or "permission" in lower or "access denied" in lower or "winerror 5" in lower or "拒绝访问" in text:
            return "权限不足，请检查保存目录或剪贴板权限。"
        if any(token in lower for token in ("invalid path", "invalid filename", "filename or extension", "no such file", "not a directory")) or any(token in text for token in ("路径无效", "文件名", "目录不存在")):
            return "保存路径无效，请重新选择可用路径。"
        if "no space" in lower or "disk full" in lower or "磁盘空间" in text:
            return "磁盘空间不足，请清理空间后重试。"
        if text:
            return f"{default_message}：{text[:160]}"
        return str(default_message)

    def _reset_button_layouts(self) -> None:
        for layout in (self._row1, self._row2):
            for button in self._buttons:
                try:
                    layout.removeWidget(button)
                except Exception:
                    pass
            try:
                layout.removeWidget(self._lbl_record_time)
            except Exception:
                pass

    def _set_action_button_text(self, button: QPushButton, text: str) -> None:
        label = str(text or "")
        old_tooltip = button.toolTip()
        self._button_labels[button] = label
        tooltip = self._tooltip_for_action_button(button, label)
        if self._button_style == "icon":
            button.setText("")
            if tooltip:
                button.setToolTip(tooltip)
            # 如果自定义 tooltip 正在显示该按钮的旧文本，实时更新
            try:
                tip = self._icon_tooltip
                if tip.isVisible() and getattr(tip, "_text", "") == old_tooltip:
                    pos = button.mapToGlobal(QPoint(int(button.width() / 2), int(button.height() + 4)))
                    tip.show_text(tooltip, pos, padding=21)
            except Exception:
                pass
            return
        button.setText(label)
        button.setToolTip(tooltip)

    def _cursor_for_action_button(self, button: QPushButton):
        return Qt.CursorShape.PointingHandCursor

    def _apply_button_style(self, style: str = "icon") -> None:
        target_style = self._normalize_button_style(style)
        is_text = (target_style == "text")

        # 防刷过滤：如果模式未改变且布局分隔线已准备好，仅更新文字状态，避开拆装布局以彻底根治文字闪烁问题
        layout_has_separators = hasattr(self, "_separators") and len(self._separators) > 0
        if getattr(self, "_button_style", None) == target_style and layout_has_separators:
            for b in self._buttons:
                if is_text:
                    original_text = self._button_labels.get(b) or self._static_button_labels.get(b, "")
                    self._set_action_button_text(b, original_text)
            return

        self._button_style = target_style
        self._reset_button_layouts()

        # 清理旧的分隔线 (显式隐藏并置空父类，彻底杜绝异步删除造成的左上角幽灵残留与重影)
        if hasattr(self, "_separators"):
            for sep in self._separators:
                try:
                    sep.hide()
                    self._row1.removeWidget(sep)
                    self._row2.removeWidget(sep)
                    sep.setParent(None)
                    sep.deleteLater()
                except Exception:
                    pass
            self._separators.clear()
        else:
            self._separators = []

        def add_separator(layout):
            sep = QFrame()
            # 使用1px极简扁平实色组件，彻底舍弃系统立体VLine组件，从而彻底根治系统高光/阴影立体边缘带来的双线重影感
            sep.setFixedWidth(1)
            # 文字模式下分割线的左右 margin 从 4px 缩减到 2px，极致压缩冗余空间
            sep_margin = "2px" if is_text else "4px"
            sep.setStyleSheet(f"background-color: #e2e8f0; margin: 5px {sep_margin};")
            layout.addWidget(sep)
            self._separators.append(sep)

        # 统一的高级现代化 QSS 样式表：与主界面左侧导航栏极其一致，强制使用精致微软雅黑字体
        self.setStyleSheet(
            "QFrame#ToolbarContainer {"
            "    background: #ffffff;"
            "    border: 1px solid #cbd5e1;"
            "    border-radius: 8px;"
            "}"
            "QPushButton {"
            "    background: transparent;"
            "    border: none;"
            "    border-radius: 6px;"
            "    color: #475569;"
            "    font-weight: 500;"
            "    font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif;"
            "    font-size: 11px;"
            "}"
            "QPushButton:hover {"
            "    background: rgba(15, 23, 42, 0.045);"
            "    color: #0f172a;"
            "}"
            "QPushButton:checked {"
            "    background: rgba(59, 130, 246, 0.12);"
            "    color: #2563eb;"
            "    border: 1px solid rgba(59, 130, 246, 0.24);"
            "    font-weight: 700;"
            "}"
            "QPushButton:disabled {"
            "    background: transparent;"
            "    color: #cbd5e1;"
            "}"
        )

        # 无论文字模式还是图标模式，都采用单行胶囊化流式布局。文字模式的 spacing 设为 0px 以完全无缝拼合，图标模式为 2px 预留小呼吸感
        self._row1.setSpacing(0 if is_text else 2)
        self._row2.setSpacing(0)

        # 统一的一体化四段逻辑功能分组排布：
        # 第一组：高级分析 (识别、模糊、录制)
        self._row1.addWidget(self._btn_ocr)
        self._row1.addWidget(self._btn_blur)
        self._row1.addWidget(self._btn_record)
        self._row1.addWidget(self._lbl_record_time)

        add_separator(self._row1)

        # 第二组：常规画笔标注 (框选、箭头、画笔、记号、序号、文本、擦除)
        self._row1.addWidget(self._btn_rect)
        self._row1.addWidget(self._btn_arrow)
        self._row1.addWidget(self._btn_pen)
        self._row1.addWidget(self._btn_marker)
        self._row1.addWidget(self._btn_number)
        self._row1.addWidget(self._btn_text)
        self._row1.addWidget(self._btn_eraser)

        add_separator(self._row1)

        # 第三组：撤销还原历史 (撤销、还原)
        self._row1.addWidget(self._btn_undo)
        self._row1.addWidget(self._btn_redo)

        add_separator(self._row1)

        # 第四组：截图结果动作 (保存、贴图、复制、关闭)
        self._row1.addWidget(self._btn_save)
        self._row1.addWidget(self._btn_pin)
        self._row1.addWidget(self._btn_copy)
        self._row1.addWidget(self._btn_close)

        is_text = (self._button_style == "text")
        for b in self._buttons:
            b.setCursor(self._cursor_for_action_button(b))
            if is_text:
                b.setIcon(QIcon())
                b.setIconSize(QSize(0, 0))
                # 文字按钮固定紧凑大小微调至 46x28，极致小巧扁平
                b.setFixedSize(46, 28)
                original_text = self._button_labels.get(b) or self._static_button_labels.get(b, "")
                self._set_action_button_text(b, original_text)
            else:
                icon_name = str(self._button_icon_names.get(b, ""))
                if icon_name:
                    if b.isChecked():
                        base, ext = icon_name.rsplit(".", 1)
                        active_name = f"{base}_active.{ext}"
                        icon_path = self._asset_dir / active_name
                        if not icon_path.exists():
                            icon_path = self._asset_dir / icon_name
                    else:
                        icon_path = self._asset_dir / icon_name
                else:
                    icon_path = None

                icon_path_str = str(icon_path) if icon_path else ""
                if icon_path_str:
                    if icon_path_str in PostCaptureActions._icon_cache:
                        icon = PostCaptureActions._icon_cache[icon_path_str]
                    else:
                        icon = QIcon(icon_path_str)
                        PostCaptureActions._icon_cache[icon_path_str] = icon
                else:
                    icon = QIcon()
                b.setIcon(icon)
                b.setIconSize(QSize(16, 16))
                # 图标按钮固定大小微调为 30x28，维持极其精致的水滴气泡悬浮感
                b.setFixedSize(30, 28)
                # 绝对不污染主逻辑文字字典，仅清空按钮显示并更新 ToolTip
                b.setText("")
                b.setToolTip(self._tooltip_for_action_button(b, self._button_labels.get(b) or self._static_button_labels.get(b, "")))


    def prepare_for_capture(
        self,
        region_rect: QRect,
        image_bgr: np.ndarray,
        default_dir: str,
        default_format: str,
        jpg_quality: int,
        capture_frames: int,
        region_px: Optional[tuple[int, int, int, int]],
        on_close: Callable[[], None],
        on_toast: Optional[Callable[[str, str, int], None]] = None,
        on_recording_changed: Optional[Callable[[bool], None]] = None,
        on_annotation_mode_changed: Optional[Callable[[bool], None]] = None,
        on_annotation_tool_changed: Optional[Callable[[str], None]] = None,
        on_annotation_undo: Optional[Callable[[], None]] = None,
        on_annotation_redo: Optional[Callable[[], None]] = None,
        on_annotation_clear: Optional[Callable[[], None]] = None,
        on_annotation_commit: Optional[Callable[[], None]] = None,
        on_render_annotations: Optional[Callable[[np.ndarray], np.ndarray]] = None,
        save_button_auto: bool = True,
        save_button_mode: str = "",
        on_auto_save: Optional[Callable[[np.ndarray], object]] = None,
        on_ai_recognize: Optional[Callable[[np.ndarray], object]] = None,
        on_ocr_panel_visibility_changed: Optional[Callable[[bool], None]] = None,
        button_style: str = "icon",
        on_image_annotated: Optional[Callable[[np.ndarray], None]] = None,
        on_dialog_show: Optional[Callable[[], None]] = None,
        on_dialog_close: Optional[Callable[[], None]] = None,
        quick_action_handler: Optional[Callable[[str], None]] = None,
        on_ocr_text_result: Optional[Callable[[str, float], bool]] = None,
    ) -> None:
        self._region = QRect(region_rect)
        self._image_bgr = image_bgr
        self._base_image_bgr = image_bgr
        self._default_dir = str(default_dir)
        self._default_format = (default_format or "png").lower()
        self._jpg_quality = int(jpg_quality)
        self._capture_frames = int(capture_frames)
        self._region_px = region_px
        self._on_close = on_close
        self._close_notified = False
        self._closing_all = False
        self._on_toast = on_toast
        self._on_recording_changed = on_recording_changed
        self._on_annotation_mode_changed = on_annotation_mode_changed
        self._on_annotation_tool_changed = on_annotation_tool_changed
        self._on_annotation_undo = on_annotation_undo
        self._on_annotation_redo = on_annotation_redo
        self._on_annotation_clear = on_annotation_clear
        self._on_annotation_commit = on_annotation_commit
        self._on_render_annotations = on_render_annotations
        self._save_button_mode = self._normalize_save_button_mode(save_button_mode, save_button_auto)
        self._save_button_auto = self._save_button_mode == "auto"
        self._on_auto_save = on_auto_save
        self._on_ai_recognize = on_ai_recognize
        self._on_ocr_panel_visibility_changed = on_ocr_panel_visibility_changed
        self._refresh_save_button_label()
        self._apply_button_style(button_style)
        self._on_image_annotated = on_image_annotated
        self._on_dialog_show = on_dialog_show
        self._on_dialog_close = on_dialog_close
        self._quick_action_handler = quick_action_handler
        self._on_ocr_text_result = on_ocr_text_result
        self._recording = False
        self._record_stop_event = None
        self._record_thread = None
        self._record_path = None
        self._record_gif_path = None
        self._last_pinned = None
        self._capture_hide_border = False
        self._detach_ocr_panel()
        self._annotation_mode = ""
        self._image_pending = False
        self._first_frame_ready_emitted = False
        self._annotation_can_undo = False
        self._annotation_can_redo = False
        self._pos_before_record = None
        self._record_started_ts = 0.0
        self._set_action_button_text(self._btn_record, "录制")
        self._set_action_button_text(self._btn_ocr, "识别")
        self._set_action_button_text(self._btn_pin, "贴图")
        for b in self._annotation_mode_buttons:
            b.setChecked(False)
        self.set_image_pending(False)
        self._refresh_ocr_availability()
        self._update_annotation_history(False, False)
        self._reposition()
        self.ensurePolished()
        if self.layout() is not None:
            self.layout().activate()
        QApplication.sendPostedEvents(self)
        self.show()
        self.raise_()
        try:
            self.activateWindow()
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        except Exception:
            pass
        self._invalidate_pin_cache(schedule=False)


    def _exclude_window_from_capture(self) -> None:
        try:
            import ctypes

            hwnd = int(self.winId())
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            ctypes.windll.user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), ctypes.c_uint(WDA_EXCLUDEFROMCAPTURE))
        except Exception:
            pass

    def showEvent(self, event) -> None:
        self._exclude_window_from_capture()
        super().showEvent(event)
        QTimer.singleShot(32, self._emit_first_frame_ready_once)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        QTimer.singleShot(0, self._emit_first_frame_ready_once)

    def _emit_first_frame_ready_once(self) -> None:
        if bool(getattr(self, "_first_frame_ready_emitted", False)):
            return
        self._first_frame_ready_emitted = True
        try:
            self.first_frame_ready.emit()
        except RuntimeError:
            pass

    def set_image_pending(self, pending: bool) -> None:
        self._image_pending = bool(pending)
        if bool(self._image_pending):
            self._annotation_mode = ""
            for b in self._annotation_mode_buttons:
                b.setChecked(False)
        for b in self._buttons:
            if b is self._btn_close:
                b.setEnabled(True)
            elif b is self._btn_undo or b is self._btn_redo:
                continue
            else:
                b.setEnabled(not bool(self._image_pending))
        if not bool(self._image_pending):
            self._refresh_ocr_availability()
        self._update_annotation_history(self._annotation_can_undo, self._annotation_can_redo)

    def _image_height_px(self) -> int:
        try:
            shape = getattr(self._image_bgr, "shape", None)
            if shape is not None and len(shape) >= 1:
                return int(shape[0])
        except Exception:
            pass
        return 0

    def _ocr_disabled_reason(self) -> str:
        height = self._image_height_px()
        limit = int(self.OCR_MAX_IMAGE_HEIGHT)
        if height > limit:
            return f"当前图片高度 {height}px，超过 {limit}px，文字识别不可用"
        return ""

    def _refresh_ocr_availability(self) -> None:
        try:
            pending = bool(getattr(self, "_image_pending", False))
            ai_available = self._on_ai_recognize is not None
            if getattr(self, "_ocr_worker", None) is not None:
                self._btn_ocr.setEnabled(False)
                return
            reason = self._ocr_disabled_reason()
            if pending:
                self._btn_ocr.setEnabled(False)
                return
            if reason and not ai_available:
                self._btn_ocr.setEnabled(False)
                self._btn_ocr.setToolTip(reason)
                self._btn_ocr.setCursor(Qt.CursorShape.ForbiddenCursor)
                return
            self._btn_ocr.setEnabled(True)
            self._btn_ocr.setCursor(Qt.CursorShape.PointingHandCursor)
            label = self._button_labels.get(self._btn_ocr) or self._static_button_labels.get(self._btn_ocr, "识别")
            tooltip_for_action = getattr(self, "_tooltip_for_action_button", None)
            tooltip = (
                tooltip_for_action(self._btn_ocr, str(label or "识别"))
                if callable(tooltip_for_action)
                else str(label or "识别")
            )
            self._btn_ocr.setToolTip(tooltip)
        except Exception:
            pass

    def _ocr_action_menu_items(self) -> list[tuple[str, Optional[Callable[[], None]], bool, ...]]:
        ocr_disabled_reason = self._ocr_disabled_reason()
        ocr_enabled = (
            not bool(getattr(self, "_image_pending", False))
            and getattr(self, "_ocr_worker", None) is None
            and not bool(ocr_disabled_reason)
        )
        ai_enabled = (
            not bool(getattr(self, "_image_pending", False))
            and self._on_ai_recognize is not None
        )
        return [
            ("OCR识别", self._ocr_extract_from_menu, bool(ocr_enabled), [], "不可用" if ocr_disabled_reason else ""),
            (
                "PP-OCRv6识别",
                self._ppocrv6_extract_from_menu,
                bool(ocr_enabled),
                [],
                "不可用" if ocr_disabled_reason else "",
            ),
            ("AI识别", self._ai_recognize_from_menu, bool(ai_enabled)),
        ]

    def _run_ocr_action_from_menu(self, callback: Callable[[], None]) -> None:
        self._close_ocr_action_menu()
        QTimer.singleShot(0, callback)

    def _ocr_extract_from_menu(self) -> None:
        self._run_ocr_action_from_menu(self._ocr_extract)

    def _ppocrv6_extract_from_menu(self) -> None:
        self._run_ocr_action_from_menu(lambda: self._ocr_extract(OCR_ENGINE_PPOCRV6))

    def _ai_recognize_from_menu(self) -> None:
        self._run_ocr_action_from_menu(self._ai_recognize)

    def _show_ocr_action_menu(self) -> None:
        if bool(getattr(self, "_image_pending", False)):
            return
        if not hasattr(self, "_btn_ocr") or self._btn_ocr is None:
            return
        try:
            self._ocr_action_menu_hide_timer.stop()
        except Exception:
            pass
        popup = getattr(self, "_ocr_action_menu_popup", None)
        try:
            if popup is not None and popup.isVisible():
                return
        except RuntimeError:
            popup = None
            self._ocr_action_menu_popup = None

        popup = OcrGenericMenuPopup(
            self._ocr_action_menu_items(),
            parent=self._btn_ocr,
            match_parent_width=True,
        )
        self._ocr_action_menu_popup = popup
        popup.destroyed.connect(lambda *_: setattr(self, "_ocr_action_menu_popup", None))
        popup.installEventFilter(self)
        pos = self._btn_ocr.mapToGlobal(QPoint(0, self._btn_ocr.height() + 4))
        popup.show_at_pos(pos)

    def _cursor_over_ocr_action_area(self) -> bool:
        for widget in (getattr(self, "_btn_ocr", None), getattr(self, "_ocr_action_menu_popup", None)):
            if widget is None:
                continue
            try:
                if widget.isVisible() and widget.rect().contains(widget.mapFromGlobal(QCursor.pos())):
                    return True
            except RuntimeError:
                continue
            except Exception:
                pass
        return False

    def _schedule_ocr_action_menu_hide(self, delay_ms: int = 180) -> None:
        try:
            self._ocr_action_menu_hide_timer.start(max(0, int(delay_ms)))
        except Exception:
            pass

    def _hide_ocr_action_menu_if_outside(self) -> None:
        if self._cursor_over_ocr_action_area():
            return
        self._close_ocr_action_menu()

    def _close_ocr_action_menu(self) -> None:
        popup = getattr(self, "_ocr_action_menu_popup", None)
        try:
            if popup is not None:
                popup.close()
        except RuntimeError:
            self._ocr_action_menu_popup = None
        except Exception:
            pass
        try:
            self._ocr_action_menu_hide_timer.stop()
        except Exception:
            pass

    def set_region(self, rect: QRect, region_px: Optional[tuple[int, int, int, int]] = None) -> None:
        self._region = QRect(rect)
        self._region_px = region_px
        if self._annotation_overlay is not None:
            try:
                self._annotation_overlay.set_region(QRect(self._region), self._base_image_bgr, clear=False)
            except Exception:
                pass
        try:
            self._reposition()
        except Exception:
            pass
        try:
            panel = self._live_ocr_panel()
            if panel is not None and panel.isVisible():
                panel.set_region(QRect(self._region))
        except RuntimeError:
            self._ocr_panel = None
        except Exception:
            pass

    def set_image(self, image_bgr: np.ndarray) -> None:
        try:
            self._base_image_bgr = image_bgr
            if self._on_annotation_clear is not None:
                self._on_annotation_clear()
            elif self._annotation_overlay is not None:
                self._annotation_overlay.set_region(QRect(self._region), self._base_image_bgr, clear=True)
            self._image_bgr = self._base_image_bgr
            self._invalidate_pin_cache(schedule=False)
            self._refresh_ocr_availability()
        except Exception:
            pass

    def setVisible(self, visible: bool) -> None:
        if bool(visible):
            # 强制确保样式应用和布局在内存中就绪，一步到位，彻底根除透明度闪烁和跳跃感
            self.ensurePolished()
            if self.layout() is not None:
                self.layout().activate()
            QApplication.sendPostedEvents(self)
        super().setVisible(bool(visible))

        for panel in [self._annotation_overlay, self._live_ocr_panel()]:
            try:
                if panel is not None:
                    panel.setVisible(bool(visible))
                    if bool(visible):
                        panel.raise_()
            except Exception:
                pass

    def _ensure_annotation_overlay(self) -> AnnotationCanvasOverlay:
        if self._annotation_overlay is None:
            self._annotation_overlay = AnnotationCanvasOverlay(QRect(self._region), self._base_image_bgr, getattr(self, "_annotation_parent", None))
            self._annotation_overlay.changed.connect(self._apply_annotations_to_image)
            self._annotation_overlay.history_changed.connect(self._update_annotation_history)
            owner = self
            self._annotation_overlay.destroyed.connect(lambda *_, owner=owner: setattr(owner, "_annotation_overlay", None))
        self._annotation_overlay.set_region(QRect(self._region), self._base_image_bgr, clear=False)
        return self._annotation_overlay

    def _toggle_annotation_mode(self, mode: str) -> None:
        if bool(getattr(self, "_image_pending", False)):
            return
        next_mode = "" if str(self._annotation_mode) == str(mode) else str(mode)
        self._set_annotation_mode(next_mode)

    def _set_annotation_mode(self, mode: str) -> None:
        self._annotation_mode = str(mode or "")
        for b, m in [
            (self._btn_eraser, "eraser"),
            (self._btn_blur, "blur"),
            (self._btn_rect, "rect"),
            (self._btn_arrow, "arrow"),
            (self._btn_pen, "pen"),
            (self._btn_marker, "marker"),
            (self._btn_number, "number"),
            (self._btn_text, "text"),
        ]:
            b.setChecked(bool(self._annotation_mode) and str(self._annotation_mode) == str(m))

        # 刷新相关标注按钮的图标，以使其在选中/取消选中时动态变换 active/normal 图标 (仅限非文字模式)
        if getattr(self, "_button_style", "icon") != "text":
            for b in self._annotation_mode_buttons:
                icon_name = str(self._button_icon_names.get(b, ""))
                if not icon_name:
                    continue
                if b.isChecked():
                    base, ext = icon_name.rsplit(".", 1)
                    active_name = f"{base}_active.{ext}"
                    icon_path = self._asset_dir / active_name
                    if not icon_path.exists():
                        icon_path = self._asset_dir / icon_name
                else:
                    icon_path = self._asset_dir / icon_name

                icon_path_str = str(icon_path)
                if icon_path_str in PostCaptureActions._icon_cache:
                    icon = PostCaptureActions._icon_cache[icon_path_str]
                else:
                    icon = QIcon(icon_path_str)
                    PostCaptureActions._icon_cache[icon_path_str] = icon
                b.setIcon(icon)

        if self._on_annotation_tool_changed is not None:
            try:
                self._on_annotation_tool_changed(str(self._annotation_mode))
            except Exception:
                pass
            overlay = None
        else:
            overlay = self._ensure_annotation_overlay()
            overlay.set_mode(str(self._annotation_mode))
        if self._on_annotation_mode_changed is not None:
            try:
                self._on_annotation_mode_changed(bool(self._annotation_mode))
            except Exception:
                pass
        if bool(self._annotation_mode) and overlay is not None:
            overlay.raise_()
            self.raise_()

    def _apply_annotations_to_image(self) -> None:
        try:
            def set_parent_preview_active(active: bool) -> None:
                try:
                    if self._annotation_overlay is not None:
                        self._annotation_overlay.set_commands_rendered_to_parent(bool(active))
                except Exception:
                    pass

            if self._on_render_annotations is not None:
                self._image_bgr = self._on_render_annotations(self._base_image_bgr)
                self._invalidate_pin_cache(schedule=False)
                if hasattr(self, "_on_image_annotated") and self._on_image_annotated is not None:
                    self._on_image_annotated(self._image_bgr)
                return
            if self._annotation_overlay is None:
                self._image_bgr = self._base_image_bgr
                self._invalidate_pin_cache(schedule=False)
                if hasattr(self, "_on_image_annotated") and self._on_image_annotated is not None:
                    self._on_image_annotated(self._image_bgr)
                return

            in_drawing = False
            mode = ""
            try:
                if self._annotation_overlay is not None and self._annotation_overlay._drawing:
                    in_drawing = True
                if self._annotation_overlay is not None:
                    mode = str(getattr(self._annotation_overlay, "_mode", "") or "")
            except Exception:
                pass
            has_parent_preview = self._on_image_annotated is not None and getattr(self, "_annotation_parent", None) is not None

            if in_drawing:
                if mode == "eraser" and bool(has_parent_preview):
                    self._image_bgr = self._annotation_overlay.render_to_bgr(self._base_image_bgr, commit_text=False)
                    set_parent_preview_active(True)
                    self._invalidate_pin_cache(schedule=False)
                    if hasattr(self, "_on_image_annotated") and self._on_image_annotated is not None:
                        self._on_image_annotated(self._image_bgr)
                    if self._annotation_overlay is not None:
                        self._annotation_overlay.update()
                    return
                # 处于高频拖动绘制/擦除中，由 AnnotationCanvasOverlay 自身进行极致流畅的原生实时轨迹与擦除渲染。
                # 此时我们不需要进行巨幅底图的慢速转换和 setPixmap 缩放，只需强刷屏幕显存级联合重绘，瞬间浮现出轨迹/擦除效果，开销为 0！
                if getattr(self, "_annotation_parent", None) is not None:
                    self._annotation_parent.update()
                if self._annotation_overlay is not None:
                    self._annotation_overlay.update()
                return

            self._image_bgr = self._annotation_overlay.render_to_bgr(self._base_image_bgr, commit_text=not in_drawing)
            set_parent_preview_active(bool(has_parent_preview))
            self._invalidate_pin_cache(schedule=False)
            if hasattr(self, "_on_image_annotated") and self._on_image_annotated is not None:
                self._on_image_annotated(self._image_bgr)
        except Exception:
            logger.exception("应用截图标注失败")

    def _finalize_annotations(self) -> None:
        try:
            if self._annotation_overlay is not None:
                self._annotation_overlay.commit_pending()
            if self._on_render_annotations is not None or self._annotation_overlay is not None:
                self._apply_annotations_to_image()
        except Exception:
            pass

    def _output_image_bgr(self):
        if bool(getattr(self, "_image_pending", False)):
            return None
        try:
            if self._annotation_overlay is not None:
                self._annotation_overlay.commit_pending()
        except Exception:
            pass
        image_bgr = self._image_bgr
        base_bgr = self._base_image_bgr if self._base_image_bgr is not None else image_bgr
        if base_bgr is None:
            return None
        try:
            if self._on_render_annotations is not None:
                image_bgr = self._on_render_annotations(base_bgr)
            elif self._annotation_overlay is not None:
                image_bgr = self._annotation_overlay.render_to_bgr(base_bgr)
            self._image_bgr = image_bgr
        except Exception:
            logger.exception("输出前合成截图标注失败")
            image_bgr = self._image_bgr
        try:
            return image_bgr.copy()
        except Exception:
            return image_bgr

    def _annotation_undo(self) -> None:
        if bool(getattr(self, "_image_pending", False)):
            return
        if self._on_annotation_undo is not None:
            self._on_annotation_undo()
        elif self._annotation_overlay is not None:
            self._annotation_overlay.undo()

    def _annotation_redo(self) -> None:
        if bool(getattr(self, "_image_pending", False)):
            return
        if self._on_annotation_redo is not None:
            self._on_annotation_redo()
        elif self._annotation_overlay is not None:
            self._annotation_overlay.redo()

    def _update_annotation_history(self, can_undo: bool, can_redo: bool) -> None:
        self._annotation_can_undo = bool(can_undo)
        self._annotation_can_redo = bool(can_redo)
        if bool(getattr(self, "_image_pending", False)):
            self._btn_undo.setEnabled(False)
            self._btn_redo.setEnabled(False)
            return
        self._btn_undo.setEnabled(bool(can_undo))
        self._btn_redo.setEnabled(bool(can_redo))

    def _apply_capture_phase(self, hidden: bool) -> None:
        self._capture_hide_border = bool(hidden)
        if self._on_recording_changed is not None:
            try:
                self._on_recording_changed(bool(hidden))
            except Exception:
                pass

    def eventFilter(self, obj, event) -> bool:
        t = event.type()
        if t in {QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride} and int(event.key()) == int(Qt.Key.Key_Escape):
            self.handle_escape()
            event.accept()
            return True
        if obj is getattr(self, "_ocr_action_menu_popup", None):
            if t == QEvent.Type.Enter:
                try:
                    self._ocr_action_menu_hide_timer.stop()
                except Exception:
                    pass
            elif t in {QEvent.Type.Leave, QEvent.Type.Hide, QEvent.Type.Close}:
                self._schedule_ocr_action_menu_hide()
            return super().eventFilter(obj, event)
        if isinstance(obj, QPushButton):
            if t in {QEvent.Type.Enter, QEvent.Type.Leave}:
                try:
                    eff = obj.graphicsEffect()
                    if isinstance(eff, QGraphicsDropShadowEffect):
                        if t == QEvent.Type.Enter:
                            eff.setBlurRadius(12)
                            eff.setOffset(0, 3)
                            eff.setColor(QColor(0, 0, 0, 95))
                        else:
                            eff.setBlurRadius(8)
                            eff.setOffset(0, 2)
                            eff.setColor(QColor(0, 0, 0, 70))
                except Exception:
                    pass
                if obj is getattr(self, "_btn_ocr", None):
                    if t == QEvent.Type.Enter:
                        try:
                            self._icon_tooltip.hide()
                        except Exception:
                            pass
                        self._show_ocr_action_menu()
                    elif t == QEvent.Type.Leave:
                        self._schedule_ocr_action_menu_hide()
                try:
                    if str(getattr(self, "_button_style", "")) == "icon":
                        if obj is getattr(self, "_btn_ocr", None):
                            pass
                        elif t == QEvent.Type.Enter and str(obj.toolTip()).strip():
                            pos = obj.mapToGlobal(QPoint(int(obj.width() / 2), int(obj.height() + 4)))
                            self._icon_tooltip.show_text(str(obj.toolTip()), pos, padding=21)
                        elif t == QEvent.Type.Leave:
                            self._icon_tooltip.hide()
                except Exception:
                    pass
            elif t == QEvent.Type.ToolTip and str(getattr(self, "_button_style", "")) == "icon":
                try:
                    if obj is getattr(self, "_btn_ocr", None):
                        self._show_ocr_action_menu()
                    elif str(obj.toolTip()).strip():
                        pos = obj.mapToGlobal(QPoint(int(obj.width() / 2), int(obj.height() + 4)))
                        self._icon_tooltip.show_text(str(obj.toolTip()), pos, padding=21)
                except Exception:
                    pass
                return True
        return super().eventFilter(obj, event)

    @staticmethod
    def toolbar_geometry_for_region(region_rect: QRect, size_hint: QSize, screen_geo: QRect) -> QRect:
        geo = QRect(screen_geo) if screen_geo is not None else QRect(0, 0, 800, 600)
        region = QRect(region_rect)
        hint = QSize(size_hint) if size_hint is not None else QSize()
        w = int(max(260, min(int(geo.width()), int(hint.width()))))
        h = int(max(44, min(120, int(hint.height()))))
        x = int(region.left() + (region.width() - w) / 2)
        y = int(region.bottom() - 6) # 实体与截图底部距离更近，更精致
        if y + h > geo.y() + geo.height():
            y = int(region.top() - h + 6) # 实体与截图顶部距离更近，更精致
        x = max(int(geo.x()), min(int(x), int(geo.x() + geo.width() - w)))
        y = max(int(geo.y()), min(int(y), int(geo.y() + geo.height() - h)))
        return QRect(int(x), int(y), int(w), int(h))

    def _reposition(self) -> None:
        screen = QGuiApplication.screenAt(self._region.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 800, 600)
        target = self.toolbar_geometry_for_region(QRect(self._region), self.sizeHint(), geo)
        self.setGeometry(target)

    def _notify_close_once(self) -> None:
        if bool(getattr(self, "_close_notified", False)):
            return
        self._close_notified = True
        callback = self._on_close
        self._on_close = lambda: None
        try:
            callback()
        except Exception:
            pass

    def _close_all(self) -> None:
        if bool(getattr(self, "_closing_all", False)):
            return
        self._closing_all = True
        if self._recording:
            self._stop_record(wait_join=True)
        self._close_ocr_action_menu()
        try:
            self._icon_tooltip.close()
        except Exception:
            pass
        if self._annotation_overlay is not None:
            try:
                self._annotation_overlay.close()
            except Exception:
                pass
            self._annotation_overlay = None
        self._detach_ocr_panel()
        self._release_large_image_refs()
        try:
            self.close()
        except Exception:
            pass
        self._notify_close_once()

    def close_all(self) -> None:
        self._close_all()

    def _detach_ocr_panel(self) -> Optional[OcrTextPanel]:
        try:
            panel = self._live_ocr_panel()
        except RuntimeError:
            panel = None
        except Exception:
            panel = None
        callback = getattr(self, "_ocr_panel_destroyed_callback", None)
        _retain_ocr_result_panel(panel)
        self._ocr_panel = None
        self._ocr_panel_destroyed_callback = None
        if panel is not None and callback is not None:
            try:
                panel.destroyed.disconnect(callback)
            except Exception:
                pass
        return panel

    def closeEvent(self, event) -> None:
        # 放弃结果并取消该窗口排队或正在运行的 OCR，避免阻塞其他窗口的共享 Worker。
        self._cancel_ocr_request()
        self._ocr_worker = None
        self._ocr_active_engine = None
        self._close_ocr_action_menu()
        self._detach_ocr_panel()
        self._release_large_image_refs()
        self._notify_close_once()
        super().closeEvent(event)

    def _cancel_ocr_request(self) -> None:
        cancel_token = getattr(self, "_ocr_cancel_token", None)
        self._ocr_cancel_token = None
        if cancel_token is None:
            return
        try:
            from deepcat.ocr_worker_client import get_ocr_worker_client

            get_ocr_worker_client().cancel_request(cancel_token)
        except Exception:
            pass

    def close_ocr_panel_if_visible(self) -> bool:
        try:
            panel = self._live_ocr_panel()
        except RuntimeError:
            self._ocr_panel = None
            return False
        except Exception:
            return False
        if panel is None or not panel.isVisible():
            return False
        try:
            panel.close()
        except Exception:
            return False
        try:
            if self._on_ocr_panel_visibility_changed is not None:
                self._on_ocr_panel_visibility_changed(False)
        except Exception:
            pass
        self._release_pin_cache()
        self._refocus_after_ocr_close()
        return True

    def _refocus_after_ocr_close(self) -> None:
        try:
            if self.isVisible():
                self.raise_()
                self.activateWindow()
                self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        except Exception:
            pass

    def handle_escape(self) -> None:
        if self.close_ocr_panel_if_visible():
            return
        if self._recording:
            self._stop_record(wait_join=False)
            return
        self._close_all()

    def _invalidate_pin_cache(self, *, schedule: bool = False) -> None:
        self._pin_cache_seq += 1
        self._pin_cache_dirty = True
        self._pin_cache_bgr = None
        self._pin_cache_pixmap = None
        if not bool(schedule):
            try:
                self._pin_cache_timer.stop()
            except Exception:
                pass
            return
        if not bool(getattr(self, "_image_pending", False)):
            try:
                self._pin_cache_timer.start(160)
            except Exception:
                pass

    def _release_pin_cache(self) -> None:
        self._invalidate_pin_cache(schedule=False)

    def _release_large_image_refs(self) -> None:
        self._release_pin_cache()
        self._image_bgr = None
        self._base_image_bgr = None

    @staticmethod
    def _qimage_from_bgr_for_pin(image_bgr) -> QImage:
        rgb = image_bgr[:, :, ::-1].copy()
        h, w = int(rgb.shape[0]), int(rgb.shape[1])
        return QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format.Format_RGB888).copy()

    def _accept_pin_cache(self, seq: int, image_bgr, qimg: QImage) -> None:
        if int(seq) != int(self._pin_cache_seq):
            return
        try:
            pix = QPixmap.fromImage(qimg)
            try:
                dpr = float(self.devicePixelRatioF())
                if dpr > 0:
                    pix.setDevicePixelRatio(dpr)
            except Exception:
                pass
            self._pin_cache_bgr = image_bgr
            self._pin_cache_pixmap = pix
            self._pin_cache_dirty = False
        except Exception:
            self._pin_cache_dirty = True
            self._pin_cache_bgr = None
            self._pin_cache_pixmap = None

    def _start_pin_cache_build(self) -> None:
        if bool(getattr(self, "_image_pending", False)) or not bool(self._pin_cache_dirty):
            return
        try:
            image_bgr = self._image_bgr
            if image_bgr is None:
                return
            seq = int(self._pin_cache_seq)
        except Exception:
            return

        def build() -> None:
            try:
                qimg = self._qimage_from_bgr_for_pin(image_bgr)
                self._pin_cache_ready.emit(int(seq), image_bgr, qimg)
            except Exception:
                pass

        try:
            t = threading.Thread(target=build, daemon=True)
            self._pin_cache_thread = t
            t.start()
        except Exception:
            pass

    def _commit_pending_annotations_for_output(self) -> None:
        try:
            if self._annotation_overlay is not None:
                self._annotation_overlay.commit_pending()
        except Exception:
            pass
        try:
            if self._on_annotation_commit is not None:
                self._on_annotation_commit()
        except Exception:
            pass

    def _pin_payload(self):
        if bool(getattr(self, "_image_pending", False)):
            return None
        self._commit_pending_annotations_for_output()
        if (
            not bool(self._pin_cache_dirty)
            and self._pin_cache_bgr is not None
            and self._pin_cache_pixmap is not None
        ):
            return self._pin_cache_pixmap, self._pin_cache_bgr
        try:
            image_bgr = self._output_image_bgr()
            if image_bgr is None:
                return None
            qimg = self._qimage_from_bgr_for_pin(image_bgr)
            pix = QPixmap.fromImage(qimg)
            try:
                dpr = float(self.devicePixelRatioF())
                if dpr > 0:
                    pix.setDevicePixelRatio(dpr)
            except Exception:
                pass
            return pix, image_bgr
        except Exception:
            image_bgr = self._output_image_bgr()
            if image_bgr is None:
                return None
            try:
                qimg = self._qimage_from_bgr_for_pin(image_bgr)
                pix = QPixmap.fromImage(qimg)
                return pix, image_bgr
            except Exception:
                return None

    def pin_and_close(self) -> bool:
        before = self._last_pinned
        self._pin()
        return self._last_pinned is not None and self._last_pinned is not before

    def _pin(self) -> None:
        if bool(getattr(self, "_image_pending", False)):
            return
        payload = self._pin_payload()
        if payload is None:
            return
        try:
            from deepcat.ui.pinned_image_window import PinnedImageWindow

            pix, image_bgr = payload
            win = PinnedImageWindow(
                pix,
                image_bgr=image_bgr,
                default_dir=self._default_dir,
                default_format=self._default_format,
                jpg_quality=int(self._jpg_quality),
                anchor_rect=self._region,
                capture_frames=int(self._capture_frames),
                blur_mode=False,
                on_image_changed=self._on_image_changed_from_pin,
                on_toast=self._on_toast,
            )
            self._last_pinned = win
            self._release_pin_cache()
            win.show()
            win.raise_()
            try:
                win.activateWindow()
            except Exception:
                pass
            if not bool(PostCaptureActions._pin_lifecycle_tip_shown):
                PostCaptureActions._pin_lifecycle_tip_shown = True
                self._show_action_toast("贴图提示", "贴图仅在本次运行期间有效，关闭软件后会失效。", 3500)
            QTimer.singleShot(0, self._close_all)
        except Exception as exc:
            logger.exception("打开贴图窗口失败")
            self._show_action_toast("贴图失败", self._capture_action_error_message(exc, "贴图窗口打开失败"), 3200)

    def _blur_edit(self) -> None:
        if bool(getattr(self, "_image_pending", False)):
            return
        image_bgr = self._output_image_bgr()
        if image_bgr is None:
            return
        try:
            from deepcat.utils.image_utils import bgr_to_rgb
            from deepcat.ui.pinned_image_window import PinnedImageWindow

            rgb = bgr_to_rgb(image_bgr)
            h, w = int(rgb.shape[0]), int(rgb.shape[1])
            qimg = QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format.Format_RGB888).copy()
            win = PinnedImageWindow(
                qimg,
                image_bgr=image_bgr,
                default_dir=self._default_dir,
                default_format=self._default_format,
                jpg_quality=int(self._jpg_quality),
                anchor_rect=self._region,
                capture_frames=int(self._capture_frames),
                blur_mode=True,
                on_image_changed=self._on_image_changed_from_pin,
                on_toast=self._on_toast,
            )
            self._last_pinned = win
            win.show()
            win.raise_()
            win.activateWindow()
            if self._on_toast is not None:
                self._on_toast("提示", "已进入模糊模式，按住鼠标左键涂抹", 1200)
        except Exception:
            return

    def _on_image_changed_from_pin(self, image_bgr: np.ndarray) -> None:
        try:
            base_image_bgr = image_bgr.copy()
            self._base_image_bgr = base_image_bgr
            self._image_bgr = base_image_bgr
            self._refresh_ocr_availability()
            if self._annotation_overlay is not None:
                self._annotation_overlay.set_region(QRect(self._region), self._base_image_bgr, clear=True)
            self._invalidate_pin_cache(schedule=False)
        except Exception:
            pass

    def _ocr_extract(self, engine: str = OCR_ENGINE_RAPIDOCR) -> None:
        engine = normalize_ocr_engine(engine)
        if bool(getattr(self, "_image_pending", False)):
            return
        if self._ocr_worker is not None:
            return
        reason = self._ocr_disabled_reason()
        if reason:
            self._refresh_ocr_availability()
            self._show_action_toast("OCR不可用", reason, 2200)
            return
        self._finalize_annotations()
        self._ocr_active_engine = engine
        self._ocr_started_ts = time.time()
        self._set_action_button_text(self._btn_ocr, "识别中")
        self._btn_ocr.setEnabled(False)
        custom_busy_tooltip = False
        # 自动定位按钮中心并展示常驻 ToolTip
        try:
            if self._ocr_busy_tooltip_show is not None:
                self._ocr_busy_tooltip_show("识别中")
                custom_busy_tooltip = True
            elif self._ocr_busy_tooltip_anchor is not None:
                btn_pos = self._ocr_busy_tooltip_anchor()
                direction = str(self._ocr_busy_tooltip_direction or "above")
                self._icon_tooltip.show_text("识别中", btn_pos, direction=direction)
                self._icon_tooltip._hide_timer.stop()
            else:
                btn_pos = self._btn_ocr.mapToGlobal(QPoint(self._btn_ocr.width() // 2, self._btn_ocr.height()))
                btn_pos.setY(btn_pos.y() + 6)
                self._icon_tooltip.show_text("识别中", btn_pos, direction="below")
                self._icon_tooltip._hide_timer.stop()
        except Exception:
            pass
        self._ocr_busy_tooltip_custom = custom_busy_tooltip
        # OCR 推理（含 subprocess 路径，预算最长 120 秒）移入后台线程，避免冻结 GUI
        from deepcat.ocr_worker_client import OcrCancellationToken

        cancel_token = OcrCancellationToken()
        self._ocr_cancel_token = cancel_token
        if engine == OCR_ENGINE_PPOCRV6:
            extract_fn = lambda image, token=cancel_token: self._ocr_extract_ppocrv6(image, cancel_token=token)
        else:
            extract_fn = lambda image, token=cancel_token: self._ocr_extract_rapidocr(image, cancel_token=token)
        worker = OcrExtractWorker(self._image_bgr, extract_fn, engine_name=engine)
        self._ocr_worker = worker
        _ACTIVE_OCR_WORKERS.add(worker)
        worker.ocr_finished.connect(self._on_ocr_extract_finished)
        worker.finished.connect(self._on_ocr_worker_finished)
        worker.finished.connect(lambda w=worker: _ACTIVE_OCR_WORKERS.discard(w))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_ocr_worker_finished(self) -> None:
        worker = self.sender()
        if self._ocr_worker is worker:
            self._ocr_worker = None
            self._ocr_cancel_token = None
            self._ocr_active_engine = None

    def _on_ocr_extract_finished(self, txt: str, err: str, exc: str) -> None:
        try:
            if sip.isdeleted(self):
                return
        except Exception:
            pass
        # closeEvent 会清空 _ocr_worker：此时结果已过期，只复位状态不展示
        worker = self.sender()
        stale = worker is not None and worker is not self._ocr_worker
        engine = normalize_ocr_engine(
            getattr(worker, "engine_name", None)
            or getattr(self, "_ocr_active_engine", None)
            or OCR_ENGINE_RAPIDOCR
        )
        engine_display_name = ocr_engine_display_name(engine)
        completed_title = f"{engine_display_name}识别完成"
        self._ocr_worker = None
        self._ocr_cancel_token = None
        self._ocr_active_engine = None
        display_text = ""
        err_msg = ""
        if exc:
            err_msg = classify_ocr_error_message(exc) or f"OCR引擎加载失败：{str(exc)[:200]}"
            logger.warning("OCR加载失败: %s", str(exc)[:400])
        else:
            err_s = str(err or "").strip()
            if err_s:
                logger.warning("%s stderr: %s", engine_display_name, err_s[:400])
                err_msg = classify_ocr_error_message(err_s) or err_s[:200]
            txt_s = str(txt or "").strip()
            if not txt_s and not err_msg:
                try:
                    shape = tuple(int(x) for x in self._image_bgr.shape)
                except Exception:
                    shape = ()
                logger.warning("OCR未识别到文字 shape=%s", shape)
                err_msg = "未识别到文字"
            display_text = self._fix_ocr_backslash(txt_s)
        ocr_succeeded = bool(display_text)

        # 计算识别耗时
        elapsed = time.time() - float(self._ocr_started_ts or time.time())

        # 构造最终显示文本：有错误时把错误信息放到窗口顶部
        final_text = display_text
        if err_msg:
            if final_text:
                final_text = f"【{err_msg}】\n\n{final_text}"
            else:
                final_text = f"【{err_msg}】\n（无识别结果）"

        # 截图操作窗口在识别期间被关闭则只做状态复位，不再展示结果
        if not stale:
            panel_text = display_text if ocr_succeeded else final_text
            try:
                self._show_ocr_panel(panel_text, elapsed=elapsed, submit_to_ai=ocr_succeeded)
            except Exception:
                logger.exception("显示OCR结果窗口失败")

            # 仅在真正识别成功时复制到剪贴板
            if display_text and not err_msg:
                try:
                    QApplication.clipboard().setText(display_text)
                    self._show_action_toast(completed_title, "识别结果已复制到剪贴板。", 1700)
                except Exception as copy_exc:
                    logger.exception("OCR结果复制到剪切板失败")
                    self._show_action_toast(
                        completed_title,
                        self._capture_action_error_message(copy_exc, "识别完成，但复制到剪贴板失败"),
                        3000,
                    )
            elif display_text and err_msg:
                self._show_action_toast(completed_title, f"识别完成，但有提示：{err_msg}", 3000)
            elif err_msg == "未识别到文字":
                self._show_action_toast(
                    f"{engine_display_name}未识别到文字",
                    "请尝试扩大区域或提高图片清晰度。",
                    2200,
                )
            elif err_msg:
                self._show_action_toast(f"{engine_display_name}失败", err_msg, 3600)

        try:
            self._set_action_button_text(self._btn_ocr, "识别")
            self._btn_ocr.setEnabled(True)
            self._refresh_ocr_availability()
        except Exception:
            pass
        # 隐藏常驻 ToolTip
        try:
            if self._ocr_busy_tooltip_custom and self._ocr_busy_tooltip_hide is not None:
                self._ocr_busy_tooltip_hide()
            else:
                self._icon_tooltip.hide()
        except Exception:
            pass

        if ocr_succeeded and not stale:
            # 等当前信号处理完成后再销毁带 WA_DeleteOnClose 的截图操作窗口。
            QTimer.singleShot(0, self._close_all)

    def _show_ocr_panel(self, text: str, elapsed: float = 0.0, *, submit_to_ai: bool = True) -> None:
        callback = getattr(self, "_on_ocr_text_result", None)
        if submit_to_ai and callable(callback):
            try:
                if bool(callback(str(text), float(elapsed or 0.0))):
                    return
            except Exception:
                logger.exception("OCR文本回填到AI对话失败")
        for _ in range(2):
            try:
                panel = self._ensure_ocr_panel()
                panel.set_text_and_reposition(
                    str(text),
                    QRect(self._region),
                    elapsed=elapsed,
                    auto_ocr_translate=False,
                    force_initial_view=True,
                    ocr_input_auto_height=submit_to_ai,
                    input_origin="ocr",
                )
                try:
                    if self._on_ocr_panel_visibility_changed is not None:
                        self._on_ocr_panel_visibility_changed(True)
                except Exception:
                    pass
                return
            except RuntimeError:
                self._ocr_panel = None
            except Exception:
                logger.exception("显示OCR结果窗口失败")
                return

    def _live_ocr_panel(self) -> Optional[OcrTextPanel]:
        panel = self._ocr_panel
        if panel is None:
            return None
        try:
            if sip.isdeleted(panel):
                self._ocr_panel = None
                return None
        except Exception:
            pass
        return panel

    def _ensure_ocr_panel(self) -> OcrTextPanel:
        panel = self._live_ocr_panel()
        if panel is not None:
            return panel
        panel = OcrTextPanel(
            on_toast=self._on_toast,
            restore_history_sidebar=False,
        )
        self._ocr_panel = panel

        def _drop_reference(*_args) -> None:
            if self._ocr_panel is not panel:
                return
            self._ocr_panel = None
            self._ocr_panel_destroyed_callback = None
            try:
                if self._on_ocr_panel_visibility_changed is not None:
                    self._on_ocr_panel_visibility_changed(False)
            except Exception:
                pass
            self._refocus_after_ocr_close()

        try:
            self._ocr_panel_destroyed_callback = _drop_reference
            panel.destroyed.connect(_drop_reference)
        except Exception:
            pass
        return panel

    def _is_rapidocr_unavailable(self, msg: str) -> bool:
        m = str(msg or "").lower()
        return (
            ("rapidocr_onnxruntime" in m and "modulenotfounderror" in m)
            or ("rapidocr_onnxruntime" in m and "no module named" in m)
            or ("onnxruntime_pybind11_state" in m and "dll load failed" in m)
            or ("onnxruntime.dll" in m and "dll load failed" in m)
        )

    @staticmethod
    def _ocr_size_bucket(image_bgr: np.ndarray) -> str:
        h, w = image_bgr.shape[:2]
        ratio = w / max(1, h)
        if h < 180 or w < 180:
            return "small"
        if h > 4000:
            return "long"
        if ratio > 4:
            return "wide"
        return "normal"

    @classmethod
    def _ocr_budget_seconds(cls, image_bgr: np.ndarray) -> float:
        h, w = image_bgr.shape[:2]
        bucket = cls._ocr_size_bucket(image_bgr)
        if bucket == "small":
            return float(cls.OCR_SMALL_BUDGET_SECONDS)
        if bucket == "long" or int(h) * int(w) > 12_000_000:
            return float(cls.OCR_LARGE_BUDGET_SECONDS)
        return float(cls.OCR_DEFAULT_BUDGET_SECONDS)

    @classmethod
    def _get_ocr_engines(cls, bucket: str) -> tuple[object, Optional[object]]:
        # Worker 内的请求严格串行，检测缩放参数也会在每次推理前重置，
        # 因此不同尺寸可以安全复用同一组模型，避免按 bucket 重复加载模型。
        cache_key = "shared"
        cached = cls._ocr_engines_by_bucket.get(cache_key)
        needs_rec_only = str(bucket) != "normal"
        if cached is not None and (not needs_rec_only or cached[1] is not None):
            return cached

        configure_ocr_dll_search_paths()

        # 惰性触发 onnxruntime 的 CPU 核心限制补丁，限制并行线程，防止吃满物理核心导致系统卡顿
        try:
            from deepcat.utils.ort_patch import patch_onnxruntime
            patch_onnxruntime()
        except Exception:
            pass

        from rapidocr_onnxruntime import RapidOCR

        engine = cached[0] if cached is not None else RapidOCR()
        rec_only = cached[1] if cached is not None else None
        if needs_rec_only and rec_only is None:
            rec_only = RapidOCR(use_text_det=False, text_score=0.05, min_height=1, width_height_ratio=-1)
        cls._ocr_engines_by_bucket[cache_key] = (engine, rec_only)
        cls._ocr_engine = engine
        cls._ocr_engine_rec_only = rec_only
        return engine, rec_only

    @classmethod
    def _release_ocr_engines(cls) -> None:
        """清空 Python 引用；OCR 子进程退出时由操作系统释放全部原生资源。"""

        cls._ocr_engines_by_bucket.clear()
        cls._ocr_engine = None
        cls._ocr_engine_rec_only = None

    @classmethod
    def prewarm_ocr_async(cls) -> None:
        # 保留兼容入口，但禁止启动预热；OCR 只能由用户操作按需触发。
        return

    def _ocr_extract_rapidocr(self, image_bgr: np.ndarray, *, cancel_token=None) -> tuple[str, str]:
        budget_seconds = self._ocr_budget_seconds(image_bgr)
        if cancel_token is None:
            return self._ocr_extract_rapidocr_subprocess(image_bgr, timeout_seconds=budget_seconds)
        return self._ocr_extract_rapidocr_subprocess(
            image_bgr,
            timeout_seconds=budget_seconds,
            cancel_token=cancel_token,
        )

    def _ocr_extract_ppocrv6(self, image_bgr: np.ndarray, *, cancel_token=None) -> tuple[str, str]:
        budget_seconds = self._ocr_budget_seconds(image_bgr)
        return PostCaptureActions._ocr_extract_ppocrv6_subprocess(
            self,
            image_bgr,
            timeout_seconds=budget_seconds,
            cancel_token=cancel_token,
        )

    @staticmethod
    def _norm_bbox(value: Any) -> Optional[list[float]]:
        try:
            if not isinstance(value, (list, tuple)) or len(value) < 1:
                return None
            # 4 元素 bbox [x1,y1,x2,y2]
            if len(value) == 4 and not isinstance(value[0], (list, tuple)):
                return [float(value[0]), float(value[1]), float(value[2]), float(value[3])]
            # 多点 bbox [[x1,y1],[x2,y2],...]
            xs = []
            ys = []
            for p in value:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    xs.append(float(p[0]))
                    ys.append(float(p[1]))
            if xs and ys:
                return [min(xs), min(ys), max(xs), max(ys)]
            return None
        except Exception:
            return None

    def _extract_ocr_entries_from_result(self, res: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not res:
            return out
        items = res
        if isinstance(res, tuple) and len(res) >= 1 and isinstance(res[0], (list, tuple)):
            items = res[0]
        if not isinstance(items, (list, tuple)):
            items = [items]
        for it in items:
            t = None
            b = None
            score = 0.0
            if isinstance(it, (list, tuple)):
                if len(it) >= 1 and isinstance(it[0], (list, tuple)):
                    b = self._norm_bbox(it[0])
                if len(it) >= 3 and isinstance(it[1], str):
                    t = it[1]
                    try:
                        score = float(it[2])
                    except Exception:
                        score = 0.0
                elif len(it) >= 2 and isinstance(it[1], (list, tuple)) and len(it[1]) >= 1:
                    t = it[1][0]
                    try:
                        if len(it[1]) >= 2:
                            score = float(it[1][1])
                    except Exception:
                        score = 0.0
                elif len(it) >= 2:
                    t = it[1]
            elif isinstance(it, dict):
                t = it.get("text") or it.get("txt") or it.get("transcription")
                bb = it.get("box") or it.get("points") or it.get("bbox")
                b = self._norm_bbox(bb)
                score = float(it.get("score") or it.get("confidence") or 0.0)
            else:
                t = it
            s = str(t).strip() if t is not None else ""
            if s:
                out.append({"text": s, "box": b, "score": score})
        return out

    @staticmethod
    def _preprocess_ocr_clahe(image_bgr: np.ndarray) -> np.ndarray:
        import cv2
        lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

    @staticmethod
    def _preprocess_ocr_unsharp(image_bgr: np.ndarray) -> np.ndarray:
        import cv2
        gaussian = cv2.GaussianBlur(image_bgr, (0, 0), 3)
        sharpened = cv2.addWeighted(image_bgr, 1.5, gaussian, -0.5, 0)
        return sharpened

    @staticmethod
    def _subtract_padding(entries: list[dict[str, Any]], pad_h: float, pad_v: float) -> None:
        for entry in entries:
            box = entry.get("box")
            if isinstance(box, (list, tuple)) and len(box) == 4:
                box[0] = max(0.0, float(box[0]) - pad_h)
                box[1] = max(0.0, float(box[1]) - pad_v)
                box[2] = max(0.0, float(box[2]) - pad_h)
                box[3] = max(0.0, float(box[3]) - pad_v)

    @staticmethod
    def _set_ocr_limit_side_len(ocr_engine, w: int, h: int) -> None:
        """根据图像尺寸动态调整检测模型的缩放策略。
        正常图片保持默认 limit_type='min'、limit_side_len=736。
        对于矮图/宽图，改为 limit_type='max'、limit_side_len=4096，
        避免短边被强制放大导致长边膨胀到模型无法处理的尺寸。
        """
        try:
            td = getattr(ocr_engine, "text_detector", None)
            if td is None:
                return
            ratio = w / max(1, h)
            is_wide = ratio > 4 or h < 80
            for op in getattr(td, "preprocess_op", []):
                if not hasattr(op, "limit_side_len"):
                    continue
                if is_wide:
                    op.limit_side_len = 4096
                    if hasattr(op, "limit_type"):
                        op.limit_type = "max"
                else:
                    op.limit_side_len = 736
                    if hasattr(op, "limit_type"):
                        op.limit_type = "min"
        except Exception:
            pass

    @staticmethod
    def _preprocess_ocr_pad_height(image_bgr: np.ndarray) -> np.ndarray:
        """对过矮/过宽的截图，在上下拼接白色区域增高，
        避免检测模型因短边被过度放大而处理失败。"""
        import cv2

        h, w = image_bgr.shape[:2]
        ratio = w / max(1, h)
        if ratio <= 4 and h >= 80:
            return image_bgr
        target_h = max(120, int(w / 4) + 1)
        if target_h <= h:
            return image_bgr
        pad_total = target_h - h
        pad_top = pad_total // 2
        pad_bottom = pad_total - pad_top
        return cv2.copyMakeBorder(image_bgr, pad_top, pad_bottom, 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))

    @staticmethod
    def _preprocess_ocr_resize_large(image_bgr: np.ndarray, max_h: int = 6000) -> np.ndarray:
        """对超高分辨率长图进行等比缩放，限制最大高度，避免 OCR 处理超时。"""
        import cv2

        h, w = image_bgr.shape[:2]
        if h <= max_h:
            return image_bgr
        scale = max_h / h
        new_w = max(1, int(w * scale))
        return cv2.resize(image_bgr, (new_w, max_h), interpolation=cv2.INTER_AREA)

    def _ocr_extract_rapidocr_inprocess(
        self,
        image_bgr: np.ndarray,
        *,
        budget_seconds: float = 8.0,
        metrics: Optional[dict[str, Any]] = None,
    ) -> tuple[str, str]:
        started_at = time.perf_counter()
        perf = metrics if metrics is not None else {}
        perf["pass_count"] = 0
        perf["passes"] = []
        perf["inference_ms"] = 0.0

        def run_pass(engine, image: np.ndarray, pass_name: str, **kwargs):
            pass_started_at = time.perf_counter()
            try:
                return engine(image, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - pass_started_at) * 1000.0
                perf["pass_count"] = int(perf.get("pass_count", 0)) + 1
                perf["inference_ms"] = float(perf.get("inference_ms", 0.0)) + elapsed_ms
                cast_passes = perf.get("passes")
                if isinstance(cast_passes, list):
                    cast_passes.append(pass_name)

        try:
            import cv2

            if image_bgr is not None and image_bgr.size > 0:
                blank_started_at = time.perf_counter()
                gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
                std_dev = cv2.meanStdDev(gray)[1][0][0]
                perf["blank_check_ms"] = (time.perf_counter() - blank_started_at) * 1000.0
                if std_dev < 2.0:
                    perf["blank_fast_path"] = True
                    return "", ""

            deadline = time.perf_counter() + float(max(1.0, budget_seconds))
            bucket = self._ocr_size_bucket(image_bgr)
            perf["bucket"] = bucket
            try:
                perf["input_shape"] = [int(x) for x in image_bgr.shape[:2]]
            except Exception:
                perf["input_shape"] = []

            engine_started_at = time.perf_counter()
            ocr, ocr2 = self._get_ocr_engines(bucket)
            perf["engine_ms"] = (time.perf_counter() - engine_started_at) * 1000.0

            preprocess_started_at = time.perf_counter()
            image_bgr = self._preprocess_ocr_pad_height(image_bgr)
            image_bgr = self._preprocess_ocr_resize_large(image_bgr)
            h, w = image_bgr.shape[:2]
            self._set_ocr_limit_side_len(ocr, w, h)
            pad_v = max(12, int(h * 0.12))
            pad_h = max(8, int(w * 0.04))
            sharp = self._preprocess_ocr_unsharp(image_bgr)
            padded = cv2.copyMakeBorder(
                sharp,
                pad_v,
                pad_v,
                pad_h,
                pad_h,
                cv2.BORDER_CONSTANT,
                value=(255, 255, 255),
            )
            perf["preprocess_ms"] = (time.perf_counter() - preprocess_started_at) * 1000.0

            lines: list[dict[str, Any]] = []
            r, _ = run_pass(ocr, padded, "standard", text_score=0.1, box_thresh=0.25)
            lines.extend(self._extract_ocr_entries_from_result(r))
            if lines:
                self._subtract_padding(lines, pad_h, pad_v)

            # 普通截图最多执行一次补充推理：低阈值原图与锐化首轮互补。
            # 特殊尺寸仍保留更积极的兜底，以兼顾窄条、超小图和长图识别率。
            if not lines and bucket == "normal" and time.perf_counter() < deadline:
                orig_padded = cv2.copyMakeBorder(
                    image_bgr,
                    pad_v,
                    pad_v,
                    pad_h,
                    pad_h,
                    cv2.BORDER_CONSTANT,
                    value=(255, 255, 255),
                )
                r, _ = run_pass(ocr, orig_padded, "normal_relaxed", text_score=0.05, box_thresh=0.2)
                lines.extend(self._extract_ocr_entries_from_result(r))
                if lines:
                    self._subtract_padding(lines, pad_h, pad_v)

            if not lines and bucket != "normal" and ocr2 is not None and time.perf_counter() < deadline:
                r, _ = run_pass(ocr, padded, "relaxed", text_score=0.05, box_thresh=0.2)
                lines.extend(self._extract_ocr_entries_from_result(r))
                if lines:
                    self._subtract_padding(lines, pad_h, pad_v)

            if not lines and bucket != "normal" and ocr2 is not None and time.perf_counter() < deadline:
                orig_padded = cv2.copyMakeBorder(
                    image_bgr,
                    pad_v,
                    pad_v,
                    pad_h,
                    pad_h,
                    cv2.BORDER_CONSTANT,
                    value=(255, 255, 255),
                )
                r, _ = run_pass(ocr, orig_padded, "original", text_score=0.05, box_thresh=0.2)
                lines.extend(self._extract_ocr_entries_from_result(r))
                if lines:
                    self._subtract_padding(lines, pad_h, pad_v)

            if not lines and bucket != "normal" and (h < 150 or w < 150) and time.perf_counter() < deadline:
                up = cv2.resize(image_bgr, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
                enhanced = self._preprocess_ocr_clahe(up)
                r, _ = run_pass(ocr, enhanced, "clahe", text_score=0.05, box_thresh=0.2)
                lines.extend(self._extract_ocr_entries_from_result(r))
                if lines:
                    for entry in lines:
                        box = entry.get("box")
                        if isinstance(box, (list, tuple)) and len(box) == 4:
                            box[0] = max(0.0, float(box[0]) / 2.0)
                            box[1] = max(0.0, float(box[1]) / 2.0)
                            box[2] = max(0.0, float(box[2]) / 2.0)
                            box[3] = max(0.0, float(box[3]) / 2.0)

            if not lines and bucket != "normal" and time.perf_counter() < deadline:
                try:
                    r, _ = run_pass(ocr2, image_bgr, "rec_only", text_score=0.01)
                    lines.extend(self._extract_ocr_entries_from_result(r))
                except Exception:
                    pass
            if not lines and bucket != "normal" and time.perf_counter() < deadline:
                try:
                    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
                    r, _ = run_pass(ocr2, rgb, "rec_only_rgb", text_score=0.01)
                    lines.extend(self._extract_ocr_entries_from_result(r))
                except Exception:
                    pass

            format_started_at = time.perf_counter()
            filtered: list[dict[str, Any]] = []
            seen = set()
            for entry in lines:
                text = str(entry.get("text", "")).strip()
                box = entry.get("box")
                if not text:
                    continue
                if isinstance(box, (list, tuple)) and len(box) == 4:
                    box_height = float(box[3]) - float(box[1])
                    if box_height < 3.0:
                        continue
                key = (text, tuple(box or []))
                if key in seen:
                    continue
                seen.add(key)
                filtered.append(entry)

            text = self._format_ocr_entries(filtered)
            perf["format_ms"] = (time.perf_counter() - format_started_at) * 1000.0
            perf["line_count"] = len(filtered)
            return str(text or ""), ""
        except Exception as exc:
            return "", str(exc)
        finally:
            perf["total_ms"] = (time.perf_counter() - started_at) * 1000.0

    def _format_ocr_entries(self, entries: list[dict[str, Any]]) -> str:
        return format_ocr_entries(entries)

    @staticmethod
    def _fix_ocr_backslash(text: str) -> str:
        """修复 OCR 将 Windows 路径反斜杠误识别为 /、(、) 的常见问题。"""
        if not text:
            return text
        # 提前判断：包含盘符、命令提示符、或疑似路径的行才处理
        lines = text.split("\n")
        out: list[str] = []
        for line in lines:
            is_cmd = bool(
                re.search(r"\b[A-Za-z]:[/\\()]", line)
                or ">" in line
            )
            if not is_cmd:
                out.append(line)
                continue
            # 1. 盘符后跟着 /、(、) -> \（把 D:/、D:(、D:) 都纠正为 D:\）
            line = re.sub(r"\b([A-Za-z]):([/\u002f()])", r"\1:\\", line)
            # 2. 路径内部夹在单词字符之间的 /、(、) -> \（排除 URL）
            if "://" not in line:
                line = re.sub(r"(?<=[A-Za-z0-9_.])[/()](?=[A-Za-z0-9_.])", r"\\", line)
            out.append(line)
        return "\n".join(out)

    def _ocr_extract_rapidocr_subprocess(
        self,
        image_bgr: np.ndarray,
        *,
        timeout_seconds: float = 8.0,
        cancel_token=None,
    ) -> tuple[str, str]:
        return PostCaptureActions._ocr_extract_subprocess(
            self,
            image_bgr,
            engine=OCR_ENGINE_RAPIDOCR,
            timeout_seconds=timeout_seconds,
            cancel_token=cancel_token,
        )

    def _ocr_extract_ppocrv6_subprocess(
        self,
        image_bgr: np.ndarray,
        *,
        timeout_seconds: float = 8.0,
        cancel_token=None,
    ) -> tuple[str, str]:
        return PostCaptureActions._ocr_extract_subprocess(
            self,
            image_bgr,
            engine=OCR_ENGINE_PPOCRV6,
            timeout_seconds=timeout_seconds,
            cancel_token=cancel_token,
        )

    def _ocr_extract_subprocess(
        self,
        image_bgr: np.ndarray,
        *,
        engine: str,
        timeout_seconds: float,
        cancel_token=None,
    ) -> tuple[str, str]:
        engine = normalize_ocr_engine(engine)
        total_started_at = time.perf_counter()
        payload: dict[str, Any] = {}
        try:
            from deepcat.ocr_worker_client import get_ocr_worker_client

            # 图像以原始 BGR 字节直传 worker（无临时文件、无 PNG 编解码），
            # 预处理完全由 worker 内部完成，保证尺寸桶判定基于原图。
            budget = float(max(4.0, min(130.0, timeout_seconds)))
            payload = get_ocr_worker_client().recognize(
                image_bgr,
                budget,
                cancel_token=cancel_token,
                engine=engine,
            )
            error = str(payload.get("error", "") or "").strip()
            if error:
                raise RuntimeError(error)
            return str(payload.get("text", "") or ""), str(payload.get("stderr", "") or "")
        finally:
            timings = payload.get("timings") if isinstance(payload, dict) else {}
            if not isinstance(timings, dict):
                timings = {}
            _get_ocr_performance_logger().info(
                "OCR性能 engine=%s total_ms=%.1f image_send_ms=%.1f "
                "worker_reused=%s client_startup_ms=%.1f engine_ms=%.1f inference_ms=%.1f "
                "pass_count=%s passes=%s bucket=%s",
                engine,
                (time.perf_counter() - total_started_at) * 1000.0,
                float(timings.get("image_send_ms", 0.0) or 0.0),
                bool(timings.get("worker_reused", False)),
                float(timings.get("client_startup_ms", 0.0) or 0.0),
                float(timings.get("engine_ms", 0.0) or 0.0),
                float(timings.get("inference_ms", 0.0) or 0.0),
                int(timings.get("pass_count", 0) or 0),
                ",".join(str(x) for x in timings.get("passes", []) if x),
                str(timings.get("bucket", "") or ""),
            )

    def _ocr_extract_rapidocr_subprocess_legacy(self, image_bgr: np.ndarray, *, timeout_seconds: float = 8.0) -> tuple[str, str]:
        image_bgr = self._preprocess_ocr_pad_height(image_bgr)
        image_bgr = self._preprocess_ocr_resize_large(image_bgr)
        configure_ocr_dll_search_paths()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        tmp_path = Path(tmp.name)
        tmp.close()
        try:
            import cv2

            ok = cv2.imwrite(str(tmp_path), image_bgr)
            if not bool(ok):
                return "", "write_tmp_failed"
            code = (
                "try:\n"
                "    from deepcat.utils.ocr_runtime import configure_ocr_dll_search_paths\n"
                "    configure_ocr_dll_search_paths()\n"
                "except Exception:\n"
                "    pass\n"
                "import cv2,sys,json\n"
                "from rapidocr_onnxruntime import RapidOCR\n"
                "def norm_box(b):\n"
                "    try:\n"
                "        if not isinstance(b,(list,tuple)) or len(b)<1: return None\n"
                "        if len(b)==4 and not isinstance(b[0],(list,tuple)):\n"
                "            return [float(b[0]),float(b[1]),float(b[2]),float(b[3])]\n"
                "        xs=[]; ys=[]\n"
                "        for p in b:\n"
                "            if isinstance(p,(list,tuple)) and len(p)>=2:\n"
                "                xs.append(float(p[0])); ys.append(float(p[1]))\n"
                "        if not xs or not ys: return None\n"
                "        return [min(xs),min(ys),max(xs),max(ys)]\n"
                "    except Exception:\n"
                "        return None\n"
                "def parse(res):\n"
                "    out=[]\n"
                "    if not res: return out\n"
                "    for it in res:\n"
                "        t=None; b=None; score=0.0\n"
                "        if isinstance(it,(list,tuple)):\n"
                "            if len(it)>=1 and isinstance(it[0],(list,tuple)): b=norm_box(it[0])\n"
                "            if len(it)>=3 and isinstance(it[1],str): t=it[1]; score=float(it[2]) if isinstance(it[2],(int,float)) else 0.0\n"
                "            elif len(it)>=2 and isinstance(it[1],(list,tuple)) and len(it[1])>=1: t=it[1][0]; score=float(it[1][1]) if len(it[1])>=2 and isinstance(it[1][1],(int,float)) else 0.0\n"
                "            elif len(it)>=2: t=it[1]\n"
                "        elif isinstance(it,dict):\n"
                "            t=it.get('text') or it.get('txt') or it.get('transcription')\n"
                "            bb=it.get('box') or it.get('points') or it.get('bbox')\n"
                "            b=norm_box(bb)\n"
                "            score=float(it.get('score',0.0) or it.get('confidence',0.0))\n"
                "        else: t=it\n"
                "        ss=str(t).strip() if t is not None else ''\n"
                "        if ss: out.append({'text':ss,'box':b,'score':score})\n"
                "    return out\n"
                "def subtract_padding(entries,pad_h,pad_v):\n"
                "    for e in entries:\n"
                "        b=e.get('box')\n"
                "        if isinstance(b,(list,tuple)) and len(b)==4:\n"
                "            b[0]=max(0.0,float(b[0])-pad_h); b[1]=max(0.0,float(b[1])-pad_v)\n"
                "            b[2]=max(0.0,float(b[2])-pad_h); b[3]=max(0.0,float(b[3])-pad_v)\n"
                "img=cv2.imread(sys.argv[1])\n"
                "if img is None:\n"
                "    print('')\n"
                "    raise SystemExit(0)\n"
                "h,w=img.shape[:2]\n"
                "pad_v=max(12,int(h*0.12))\n"
                "pad_h=max(8,int(w*0.04))\n"
                "gaussian=cv2.GaussianBlur(img,(0,0),3)\n"
                "sharp=cv2.addWeighted(img,1.5,gaussian,-0.5,0)\n"
                "padded=cv2.copyMakeBorder(sharp,pad_v,pad_v,pad_h,pad_h,cv2.BORDER_CONSTANT,value=(255,255,255))\n"
                "def set_limit(ocr_engine,w,h):\n"
                "    try:\n"
                "        td=getattr(ocr_engine,'text_detector',None)\n"
                "        if td is None: return\n"
                "        ratio=w/max(1,h); is_wide=ratio>4 or h<80\n"
                "        for op in getattr(td,'preprocess_op',[]):\n"
                "            if not hasattr(op,'limit_side_len'): continue\n"
                "            if is_wide:\n"
                "                op.limit_side_len=4096\n"
                "                if hasattr(op,'limit_type'): op.limit_type='max'\n"
                "            else:\n"
                "                op.limit_side_len=736\n"
                "                if hasattr(op,'limit_type'): op.limit_type='min'\n"
                "    except Exception: pass\n"
                "ocr=RapidOCR()\n"
                "set_limit(ocr,w,h)\n"
                "ocr2=RapidOCR(use_text_det=False,text_score=0.05,min_height=1,width_height_ratio=-1)\n"
                "lines=[]\n"
                "r,_=ocr(padded,text_score=0.1,box_thresh=0.25); lines.extend(parse(r))\n"
                "if lines: subtract_padding(lines,pad_h,pad_v)\n"
                "if not lines and (h<150 or w<150):\n"
                "    r,_=ocr(padded,text_score=0.05,box_thresh=0.2); lines.extend(parse(r))\n"
                "    if lines: subtract_padding(lines,pad_h,pad_v)\n"
                "if not lines:\n"
                "    orig_padded=cv2.copyMakeBorder(img,pad_v,pad_v,pad_h,pad_h,cv2.BORDER_CONSTANT,value=(255,255,255))\n"
                "    r,_=ocr(orig_padded,text_score=0.05,box_thresh=0.2); lines.extend(parse(r))\n"
                "    if lines: subtract_padding(lines,pad_h,pad_v)\n"
                "if not lines:\n"
                "    rgb=cv2.cvtColor(img,cv2.COLOR_BGR2RGB); r,_=ocr(rgb,text_score=0.05,box_thresh=0.2); lines.extend(parse(r))\n"
                "if not lines:\n"
                "    up=cv2.resize(img,None,fx=2.0,fy=2.0,interpolation=cv2.INTER_CUBIC)\n"
                "    lab=cv2.cvtColor(up,cv2.COLOR_BGR2LAB); l,a,b_ch=cv2.split(lab)\n"
                "    clahe=cv2.createCLAHE(clipLimit=2.0,tileGridSize=(8,8)); l=clahe.apply(l)\n"
                "    enhanced=cv2.cvtColor(cv2.merge([l,a,b_ch]),cv2.COLOR_LAB2BGR)\n"
                "    r,_=ocr(enhanced,text_score=0.05,box_thresh=0.2); lines.extend(parse(r))\n"
                "    if lines:\n"
                "        for e in lines:\n"
                "            bb=e.get('box')\n"
                "            if isinstance(bb,(list,tuple)) and len(bb)==4:\n"
                "                bb[0]=max(0.0,float(bb[0])/2.0); bb[1]=max(0.0,float(bb[1])/2.0)\n"
                "                bb[2]=max(0.0,float(bb[2])/2.0); bb[3]=max(0.0,float(bb[3])/2.0)\n"
                "if not lines:\n"
                "    try:\n"
                "        r,_=ocr2(img,text_score=0.01); lines.extend(parse(r))\n"
                "    except Exception: pass\n"
                "if not lines:\n"
                "    try:\n"
                "        r,_=ocr2(cv2.cvtColor(img,cv2.COLOR_BGR2RGB),text_score=0.01); lines.extend(parse(r))\n"
                "    except Exception: pass\n"
                "u=[]\n"
                "seen=set()\n"
                "for x in lines:\n"
                "    text=str(x.get('text','')).strip()\n"
                "    box=x.get('box')\n"
                "    if not text: continue\n"
                "    if isinstance(box,(list,tuple)) and len(box)==4:\n"
                "        bh=float(box[3])-float(box[1])\n"
                "        if bh<3.0: continue\n"
                "    key=(text,tuple(box or []))\n"
                "    if key in seen: continue\n"
                "    seen.add(key)\n"
                "    u.append(x)\n"
                "print(json.dumps(u,ensure_ascii=False))\n"
            )
            cp = subprocess.run(
                [sys.executable, "-c", code, str(tmp_path)],
                capture_output=True,
                text=True,
                timeout=float(max(4.0, min(130.0, timeout_seconds))),
            )
            out = str(cp.stdout or "").strip()
            try:
                obj = json.loads(out) if out else []
                if isinstance(obj, list):
                    txt = self._format_ocr_entries(obj)
                    return str(txt or ""), str(cp.stderr or "").strip()
            except Exception:
                pass
            return out, str(cp.stderr or "").strip()
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _parse_ocr_lines(self, result: Any) -> list[str]:
        if not result:
            return []
        lines: list[str] = []
        items = result
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], (list, tuple)):
            items = result[0]
        if not isinstance(items, (list, tuple)):
            items = [items]
        for item in items:
            text = None
            score = None
            if isinstance(item, (list, tuple)):
                if len(item) >= 3 and isinstance(item[1], str):
                    text = item[1]
                    try:
                        score = float(item[2])
                    except Exception:
                        score = None
                elif len(item) >= 2 and isinstance(item[1], (list, tuple)) and len(item[1]) >= 1:
                    text = item[1][0]
                    if len(item[1]) >= 2:
                        try:
                            score = float(item[1][1])
                        except Exception:
                            score = None
                elif len(item) >= 2:
                    text = item[1]
            elif isinstance(item, dict):
                text = item.get("text") or item.get("txt") or item.get("transcription")
                raw = item.get("score") or item.get("conf") or item.get("confidence")
                if raw is not None:
                    try:
                        score = float(raw)
                    except Exception:
                        score = None
            else:
                text = item
            s = str(text).strip() if text is not None else ""
            if not s:
                continue
            if score is not None and float(score) < 0.01:
                continue
            lines.append(s)
        return lines

    def _toggle_record(self) -> None:
        if bool(getattr(self, "_image_pending", False)):
            return
        if self._recording:
            self._stop_record(wait_join=False)
            return
        self._start_record()

    def _start_record(self) -> None:
        try:
            ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            self._record_path = str(Path(self._default_dir) / f"record_{ts}.mp4")
            self._record_gif_path = str(Path(self._default_dir) / f"record_{ts}.gif")
            self._record_stop_event = threading.Event()
            self._record_thread = threading.Thread(target=self._record_loop, daemon=True)
            self._recording = True
            self._record_started_ts = float(time.time())
            if not getattr(self, "_gif_export_signal_connected", False):
                # GIF 导出在非 GUI 线程完成，经信号回主线程提示（自动排队连接）
                self._gif_export_finished.connect(self._on_gif_export_finished)
                self._gif_export_signal_connected = True
            self._set_action_button_text(self._btn_record, "00:00")
            if self._button_style == "icon":
                self._lbl_record_time.setText("00:00")
                self._lbl_record_time.show()
            self._record_ui_timer.start()
            if self._on_recording_changed is not None:
                self._on_recording_changed(True)
                QApplication.processEvents()
                time.sleep(0.08)
                QApplication.processEvents()
            try:
                self._reposition()
            except Exception:
                pass
            self._record_thread.start()
            try:
                from pynput import keyboard

                def _on_press(key) -> bool:
                    try:
                        if key == keyboard.Key.esc:
                            QTimer.singleShot(0, lambda: self._stop_record(wait_join=False))
                            return False
                    except Exception:
                        return True
                    return True

                self._record_listener = keyboard.Listener(on_press=_on_press)
                self._record_listener.start()
            except Exception:
                self._record_listener = None
            if self._on_toast is not None:
                self._on_toast("提示", "开始录制，按 ESC 结束", 1500)
        except Exception:
            self._recording = False
            self._set_action_button_text(self._btn_record, "录制")

    def _stop_record(self, wait_join: bool = False) -> None:
        if not self._recording:
            return
        self._recording = False
        self._record_ui_timer.stop()
        self._set_action_button_text(self._btn_record, "录制")
        self._lbl_record_time.hide()
        try:
            if self._record_stop_event is not None:
                self._record_stop_event.set()
        except Exception:
            pass
        try:
            if self._record_listener is not None:
                self._record_listener.stop()
                self._record_listener = None
        except Exception:
            pass
        try:
            if wait_join and self._record_thread is not None and self._record_thread.is_alive():
                self._record_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            if self._on_recording_changed is not None:
                self._on_recording_changed(False)
        except Exception:
            pass
        try:
            self.setVisible(True)
        except Exception:
            pass
        try:
            self._reposition()
        except Exception:
            pass
        if self._on_toast is not None and self._record_path:
            gif_name = Path(self._record_gif_path).name if self._record_gif_path else ""
            msg = f"录制已保存：{self._record_path}"
            if gif_name:
                msg += "，高清 GIF 正在后台导出，完成后将另行提示"
            self._on_toast("提示", msg, 3500)

    def _update_record_elapsed(self) -> None:
        if not self._recording:
            return
        elapsed = max(0, int(time.time() - float(self._record_started_ts)))
        mm = int(elapsed // 60)
        ss = int(elapsed % 60)
        text = f"{mm:02d}:{ss:02d}"
        self._set_action_button_text(self._btn_record, text)
        if self._button_style == "icon" and self._lbl_record_time.isVisible():
            self._lbl_record_time.setText(text)

    def _reposition_for_recording(self) -> None:
        screen = QGuiApplication.screenAt(self._region.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 800, 600)
        h = int(self.height())
        current_x = int(self.x())
        current_y = int(self.y())
        current_bottom = int(current_y + h)
        if current_bottom <= int(self._region.top()):
            return
        target_y = int(self._region.bottom() + 1)
        max_y = int(geo.y() + geo.height() - h)
        if target_y > max_y:
            return
        safe_x = max(int(geo.x()), min(int(current_x), int(geo.x() + geo.width() - self.width())))
        self.move(int(safe_x), int(target_y))

    def _record_loop(self) -> None:
        try:
            import cv2
            import mss
            import numpy as np

            if self._region_px is not None:
                left, top, width, height = [int(x) for x in self._region_px]
            else:
                screen = QGuiApplication.screenAt(self._region.center()) or QGuiApplication.primaryScreen()
                dpr = float(screen.devicePixelRatio()) if screen else 1.0
                left = int(round(self._region.left() * dpr))
                top = int(round(self._region.top() * dpr))
                width = int(round(self._region.width() * dpr))
                height = int(round(self._region.height() * dpr))
            if width <= 0 or height <= 0:
                return
            mon = {"left": left, "top": top, "width": width, "height": height}
            fps = 12.0
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(self._record_path), fourcc, fps, (width, height))
            if not writer.isOpened():
                return
            # 高质量 JPEG 帧落盘而非驻留内存：编码耗时约为 PNG 的 1/5，
            # 不会挤占 12fps 的采集预算；画质损失远低于 GIF 256 色量化本身。
            # 超出字节预算后停止缓存，GIF 改用完整 MP4 导出（保内容完整，避免静默截断）。
            raw_frame_dir = Path(tempfile.mkdtemp(prefix="deepcat_gif_frames_"))
            raw_frame_paths: list[Path] = []
            raw_frame_bytes = 0
            max_raw_frame_bytes = 2 * 1024 * 1024 * 1024
            raw_frame_budget_exceeded = False
            try:
                with mss.mss() as sct:
                    interval = 1.0 / fps
                    time.sleep(0.08)
                    while self._record_stop_event is not None and not self._record_stop_event.is_set():
                        t0 = time.time()
                        raw = np.array(sct.grab(mon))
                        # 一次拷贝为连续数组，供 MP4 编码与 JPEG 缓存共用
                        frame = np.ascontiguousarray(raw[:, :, :3])
                        writer.write(frame)
                        if not raw_frame_budget_exceeded:
                            try:
                                ok, encoded = cv2.imencode(
                                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92]
                                )
                                if ok:
                                    frame_path = raw_frame_dir / f"frame_{len(raw_frame_paths):06d}.jpg"
                                    encoded.tofile(str(frame_path))
                                    raw_frame_paths.append(frame_path)
                                    raw_frame_bytes += int(encoded.nbytes)
                                    if raw_frame_bytes >= max_raw_frame_bytes:
                                        raw_frame_budget_exceeded = True
                                        logger.warning(
                                            "录屏原始帧缓存达到 %.0fMB 预算，GIF 将改用完整 MP4 导出",
                                            raw_frame_bytes / (1024 * 1024),
                                        )
                            except Exception:
                                logger.exception("缓存录屏帧失败，GIF 将改用 MP4 导出")
                                raw_frame_budget_exceeded = True
                        dt = time.time() - t0
                        if dt < interval:
                            time.sleep(interval - dt)
            finally:
                try:
                    self._capture_phase_signal.emit(False)
                except Exception:
                    pass
                writer.release()
                # GIF 导出移出录制线程：导出可达分钟级，daemon 录制线程若随退出被
                # 强杀会留下残缺 GIF 和永久残留的临时目录。把路径全部按值捕获，
                # 避免下一次录制改写实例属性后串写对方的输出文件。
                gif_path = str(self._record_gif_path or "")
                mp4_path = str(self._record_path or "")
                use_frame_files = bool(raw_frame_paths) and not raw_frame_budget_exceeded
                frame_dir = str(raw_frame_dir)
                frame_paths_snapshot = [str(p) for p in raw_frame_paths]
                raw_frame_paths.clear()
                export_thread = threading.Thread(
                    target=self._export_record_gif,
                    args=(use_frame_files, frame_paths_snapshot, gif_path, mp4_path, frame_dir, fps),
                    name="deepcat-gif-export",
                    daemon=False,
                )
                export_thread.start()
        except Exception:
            logger.exception("录制线程异常")
            return

    def _export_record_gif(
        self,
        use_frame_files: bool,
        frame_paths: list[str],
        gif_path: str,
        mp4_path: str,
        frame_dir: str,
        fps: float,
    ) -> None:
        """在独立的非 daemon 线程中导出 GIF：应用退出时进程会等待导出完成，
        不会把导出线程随录制线程一起强杀（曾导致残缺 GIF + 临时目录永久残留）。"""
        ok = False
        try:
            if not gif_path:
                return
            from deepcat.output.gif_exporter import (
                export_frame_files_to_gif,
                export_video_to_gif,
            )

            if use_frame_files and frame_paths:
                ok = export_frame_files_to_gif(frame_paths, gif_path, fps=fps)
            elif mp4_path and os.path.isfile(mp4_path):
                ok = export_video_to_gif(mp4_path, gif_path, fps=fps)
        except Exception:
            logger.exception("导出高清 GIF 失败")
            ok = False
        finally:
            shutil.rmtree(frame_dir, ignore_errors=True)
            try:
                self._gif_export_finished.emit(bool(ok), gif_path)
            except Exception:
                pass

    def _on_gif_export_finished(self, ok: bool, gif_path: str) -> None:
        if self._on_toast is None or not gif_path:
            return
        if ok:
            self._on_toast("提示", f"高清 GIF 已生成：{Path(gif_path).name}", 3000)
        else:
            self._on_toast("提示", "高清 GIF 导出失败，MP4 录像仍已保存", 3000)

    def keyPressEvent(self, event) -> None:
        if int(event.key()) == int(Qt.Key.Key_Escape):
            self.handle_escape()
            event.accept()
            return
        super().keyPressEvent(event)

    def export_image_bgr(self):
        return self._output_image_bgr()

    def _ai_recognize(self) -> None:
        if bool(getattr(self, "_image_pending", False)):
            return
        image_bgr = self.export_image_bgr()
        if image_bgr is None:
            return
        try:
            handled = False
            if self._on_ai_recognize is not None:
                handled = bool(self._on_ai_recognize(image_bgr))
            if handled:
                self._close_all()
            elif self._on_toast is not None:
                self._on_toast("AI识别失败", "无法发送截图到 AI 对话窗口", 2000)
        except Exception:
            logger.exception("发送截图到 AI 对话窗口失败")
            if self._on_toast is not None:
                self._on_toast("AI识别失败", "发送截图到 AI 对话窗口失败", 2000)

    def _save_as(self) -> None:
        if bool(getattr(self, "_image_pending", False)):
            return
        if bool(getattr(self, "_background_save_in_progress", False)):
            return
        image_bgr = self.export_image_bgr()
        if image_bgr is None:
            return

        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        fmt = self._default_format if self._default_format in {"png", "jpg", "pdf"} else "png"
        name = f"screenshot_{ts}.{fmt}"
        init = str(Path(self._default_dir) / name)
        if bool(self._save_button_auto):
            try:
                saved_path = None
                if self._on_auto_save is not None:
                    # 回调实现（如主窗口 _save_single_capture_outputs）会读取 Qt 控件状态，
                    # 必须留在 GUI 线程同步执行
                    saved_path = self._on_auto_save(image_bgr)
                else:
                    # 无回调时整图编码落盘可能耗时数秒，移入后台线程
                    from deepcat.ui.image_save_worker import start_image_save

                    self._background_save_in_progress = True
                    start_image_save(
                        image_bgr, init, fmt, int(self._jpg_quality),
                        on_saved=self._on_background_save_success,
                        on_failed=self._on_background_save_failed,
                    )
                    return
                if saved_path:
                    self._show_action_toast("保存成功", f"已保存到：{saved_path}", 3200)
                else:
                    self._show_action_toast("保存成功", "截图已保存。", 2200)
                self._close_all()
            except Exception as exc:
                logger.exception("自动保存截图失败")
                self._show_action_toast("保存失败", self._capture_action_error_message(exc, "截图保存失败"), 4200)
            return

        filters = "PNG (*.png);;JPG (*.jpg);;PDF (*.pdf)"
        if getattr(self, "_on_dialog_show", None) is not None:
            try:
                self._on_dialog_show()
            except Exception:
                pass

        path, _ = QFileDialog.getSaveFileName(self, "保存截图", init, filters)

        if getattr(self, "_on_dialog_close", None) is not None:
            try:
                self._on_dialog_close()
            except Exception:
                pass

        if not path:
            return
        ext = Path(path).suffix.lower().lstrip(".")
        fmt2 = ext or fmt
        # 整图编码落盘移入后台线程，完成后 toast 并关闭；失败保持面板打开
        self._background_save_in_progress = True
        try:
            from deepcat.ui.image_save_worker import start_image_save

            start_image_save(
                image_bgr, path, fmt2, int(self._jpg_quality),
                on_saved=self._on_background_save_success,
                on_failed=self._on_background_save_failed,
            )
        except Exception as exc:
            self._background_save_in_progress = False
            logger.exception("手动保存截图失败")
            self._show_action_toast("保存失败", self._capture_action_error_message(exc, "截图保存失败"), 4200)

    def _on_background_save_success(self, _tag: object, saved_path: str) -> None:
        self._background_save_in_progress = False
        try:
            self._show_action_toast("保存成功", f"已保存到：{saved_path}", 3200)
            self._close_all()
        except RuntimeError:
            # 面板可能在保存完成前被销毁
            pass

    def _on_background_save_failed(self, _tag: object, error: str) -> None:
        self._background_save_in_progress = False
        try:
            self._show_action_toast("保存失败", self._capture_action_error_message(RuntimeError(error), "截图保存失败"), 4200)
        except RuntimeError:
            pass

    def _copy(self) -> None:
        if bool(getattr(self, "_image_pending", False)):
            return
        from deepcat.output.qt_clipboard import copy_bgr_image

        image_bgr = self._output_image_bgr()
        if image_bgr is None:
            return
        try:
            ok = copy_bgr_image(image_bgr)
        except Exception as exc:
            logger.exception("截图复制到剪贴板失败")
            self._show_action_toast("复制失败", self._capture_action_error_message(exc, "截图复制失败"), 3200)
            return
        if ok:
            self._show_action_toast("复制成功", "已复制截图到剪贴板。", 1500)
        else:
            self._show_action_toast("复制失败", "剪贴板不可用，请稍后重试。", 2600)
        if ok:
            self._close_all()
        return
