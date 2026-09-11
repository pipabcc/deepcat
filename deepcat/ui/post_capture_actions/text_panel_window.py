from __future__ import annotations

import copy
import time
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QPushButton,
    QHBoxLayout,
    QFrame,
    QWidget,
    QGraphicsDropShadowEffect,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    QToolButton,
    QScrollArea,
    QSizePolicy,
)
from deepcat.settings_store import (
    AppSettings,
    DEFAULT_EXPLAIN_PROMPT,
    DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
    DEFAULT_REPLY_PROMPT,
    DEFAULT_REPLY_PROMPT_BUTTON_NAME,
    DEFAULT_SUMMARY_PROMPT,
    DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
    DEFAULT_AI_SEARCH_PROMPT,
    DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
    infer_translator_model_type,
    load_settings,
    normalize_translator_settings,
    update_settings,
    update_ui_settings,
)
from deepcat.translation_history_store import TranslationHistoryStore
from deepcat.ui.app_icon import create_app_icon
from deepcat.ui.chat_bubbles import BubbleListView
from deepcat.ui.clipboard_formats import clean_clipboard_text, copy_markdown_to_clipboard, copy_plain_text_to_clipboard
from deepcat.ui.popup_behavior import POPUP_EXACT_WIDTH_PROPERTY, set_disable_global_tooltip
from deepcat.ui.timer_scope import single_shot_scoped
from deepcat.utils.logger import get_logger
from PyQt6.QtWidgets import QFrame, QPushButton
from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame
from PyQt6.QtGui import QIcon, QColor, QFont
from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton

from deepcat.ui.post_capture_actions._shared import (
    CHAT_INPUT_WATERMARK_TEXT,
    PROMPT_ACTION_POPUP_WIDTH,
    _AI_CHAT_CONTEXT_RECENT_ROUNDS,
    _AI_CHAT_CONTEXT_WARNING_MESSAGES,
    _AI_CHAT_CONTEXT_WARNING_MESSAGES_TOKENS,
    _AI_CHAT_CONTEXT_WARNING_TOKENS,
    _AI_CHAT_DRAFTS_UI_KEY,
    _AI_DOTS_ANIMATION_ENABLED,
    _AI_GEOMETRY_TRACE_ENABLED,
    _ai_history_debug_logger,
    logger,
)
from deepcat.ui.post_capture_actions.helpers import (
    _get_ai_geometry_logger,
    _group_translator_model_menu_items,
    _history_text_has_image,
    _log_ai_history_debug,
    _normalize_ai_log_value,
    _point_to_log_value,
    _preview_log_text,
    _rect_to_log_value,
    _size_to_log_value,
    _trace_ai_panel,
    _translator_model_search_text,
)
from deepcat.ui.post_capture_actions.model_menus import OcrGenericMenuPopup, _OcrModelMenuPopup
from deepcat.ui.post_capture_actions.combos import CollapseArrowButton, UpwardComboBox
from deepcat.ui.post_capture_actions.tooltips import SmoothToolTip
from deepcat.ui.post_capture_actions.workers import (
    FileAttachmentPrepareWorker,
    ImageAttachmentPrepareWorker,
    ImaNoteSearchWorker,
    OcrTranslationWorker,
)
from deepcat.ui.thread_utils import request_thread_cancel
from deepcat.ui.post_capture_actions.text_widgets import PremiumTextEdit, RoundedTextEditContainer, _ThinkingCard
from deepcat.ui.post_capture_actions.title_bar import OcrTitleBar, _OcrTitleBarButton, _load_title_owl_pixmap
from deepcat.ui.post_capture_actions.quick_actions import HoverIconButton, OutputQuickActionBar
from deepcat.ui.post_capture_actions.ai_history import AIChatHistorySidebar, _ai_history_search_terms, _first_search_match
from deepcat.ui.post_capture_actions.attachments import _AttachmentPreviewChip, _refresh_attachment_preview_bar_if_available
from deepcat.ui.post_capture_actions.note_integration import GroupedSmoothNoteIntegrationDialog


from deepcat.ui.module_compat import DynamicModuleAttribute

QGuiApplication = DynamicModuleAttribute("deepcat.ui.post_capture_actions.text_panel", "QGuiApplication")
QTimer = DynamicModuleAttribute("deepcat.ui.post_capture_actions.text_panel", "QTimer")
copy_plain_text_to_clipboard = DynamicModuleAttribute(
    "deepcat.ui.post_capture_actions.text_panel", "copy_plain_text_to_clipboard"
)


def _ocr_text_panel_class():
    from deepcat.ui.post_capture_actions.text_panel import OcrTextPanel

    return OcrTextPanel


class TextPanelWindowMixin:
    def _screen_available_geometry_for_window(self) -> QRect:
        try:
            frame = self.frameGeometry()
            center = frame.center() if frame.isValid() else self.pos()
        except Exception:
            center = self.pos()
        screen = QGuiApplication.screenAt(center) or QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        return QRect(screen.availableGeometry()) if screen is not None else QRect()

    def _advance_window_mode_generation(self) -> int:
        """推进窗口模式代次，使上一模式遗留的异步回调立即失效。"""
        generation = int(getattr(self, "_window_mode_generation", 0) or 0) + 1
        self._window_mode_generation = generation
        return generation

    def _capture_maximized_work_area(self) -> QRect:
        geometry = self._screen_available_geometry_for_window()
        if geometry.isValid():
            self._maximized_work_area_geometry = QRect(geometry)
        return geometry

    def _target_maximized_work_area(self) -> QRect:
        stored = QRect(getattr(self, "_maximized_work_area_geometry", QRect()))
        if stored.isValid():
            return stored
        return self._capture_maximized_work_area()

    def _has_effective_maximized_geometry(self, tolerance: int = 12) -> bool:
        target = self._target_maximized_work_area()
        if not target.isValid():
            return False
        try:
            actual = QRect(self.frameGeometry())
        except Exception:
            actual = QRect(self.geometry())
        tolerance = max(0, int(tolerance))
        return bool(
            abs(actual.x() - target.x()) <= tolerance
            and abs(actual.y() - target.y()) <= tolerance
            and abs(actual.width() - target.width()) <= tolerance
            and abs(actual.height() - target.height()) <= tolerance
        )

    def _preserve_native_maximized_geometry(self) -> bool:
        """最大化意图存在时，禁止几何刷新把窗口还原。"""
        if bool(getattr(self, "_is_maximized", False)):
            return True
        if bool(getattr(self, "_pending_native_maximize_restore", False)):
            return True
        try:
            return bool(self.isMaximized())
        except Exception:
            return False

    def _force_native_maximized_window_state(self) -> None:
        """清除胶囊遗留状态后，同时通过 Qt 与 Windows 强制重建最大化。"""
        if bool(getattr(self, "_panel_collapsed", False)):
            return
        self._capture_maximized_work_area()
        self._release_panel_size_constraints(release_content=True)
        self._forcing_native_maximize = True
        try:
            try:
                self.setWindowState(Qt.WindowState.WindowNoState)
                self.showNormal()
            except Exception:
                pass
            self.showMaximized()
            if sys.platform == "win32":
                try:
                    import ctypes

                    SW_MAXIMIZE = 3
                    ctypes.windll.user32.ShowWindow(ctypes.c_void_p(int(self.winId())), SW_MAXIMIZE)
                except Exception:
                    pass
        finally:
            self._forcing_native_maximize = False

    def _force_normal_window_state(self) -> None:
        """同步清除 Qt 与 Windows 原生最大化状态，为精确窗口几何赋值解锁。"""
        self._pending_native_maximize_restore = False
        self._forcing_normal_window_state = True
        try:
            try:
                self.setWindowState(Qt.WindowState.WindowNoState)
            except Exception:
                pass
            try:
                self.showNormal()
            except Exception:
                pass
            if sys.platform == "win32":
                try:
                    import ctypes

                    SW_RESTORE = 9
                    ctypes.windll.user32.ShowWindow(ctypes.c_void_p(int(self.winId())), SW_RESTORE)
                except Exception:
                    pass
        finally:
            self._forcing_normal_window_state = False

    def _apply_panel_collapse_geometry(self, target_geometry: QRect) -> None:
        """通过 Qt 逻辑坐标写入胶囊几何，避免 Win32 物理像素覆盖导致高 DPI 裁切。"""
        target = QRect(target_geometry)
        if not target.isValid() or target.width() <= 1 or target.height() <= 1:
            return
        try:
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.setFixedSize(target.size())
            self.setGeometry(target)
        except Exception:
            pass

        try:
            window_handle = self.windowHandle()
            if window_handle is not None:
                window_handle.setGeometry(target)
        except Exception:
            pass

        if sys.platform == "win32":
            try:
                import ctypes

                SWP_NOSIZE = 0x0001
                SWP_NOMOVE = 0x0002
                SWP_NOZORDER = 0x0004
                SWP_NOACTIVATE = 0x0010
                SWP_SHOWWINDOW = 0x0040
                SWP_FRAMECHANGED = 0x0020
                ctypes.windll.user32.SetWindowPos(
                    ctypes.c_void_p(int(self.winId())),
                    ctypes.c_void_p(0),
                    0,
                    0,
                    0,
                    0,
                    SWP_NOSIZE
                    | SWP_NOMOVE
                    | SWP_NOZORDER
                    | SWP_NOACTIVATE
                    | SWP_SHOWWINDOW
                    | SWP_FRAMECHANGED,
                )
            except Exception:
                pass

    def _refresh_panel_collapse_visual(self) -> None:
        """在最终几何稳定后重建胶囊的布局、圆角与阴影绘制缓存。"""
        if not bool(getattr(self, "_panel_collapsed", False)):
            return
        widget = getattr(self, "_collapsed_widget", None)
        if widget is None:
            return

        for owner in (self, widget):
            try:
                layout = owner.layout()
            except Exception:
                layout = None
            if layout is None:
                continue
            try:
                layout.invalidate()
                layout.activate()
            except Exception:
                pass

        try:
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
        except Exception:
            pass

        try:
            effect = widget.graphicsEffect()
            if effect is not None:
                effect_was_enabled = bool(effect.isEnabled())
                if effect_was_enabled:
                    effect.setEnabled(False)
                    effect.setEnabled(True)
                effect.update()
        except Exception:
            pass

        for target in (widget, self):
            try:
                target.updateGeometry()
            except Exception:
                pass
            try:
                target.update()
                target.repaint()
            except Exception:
                pass

    def _ensure_panel_collapse_geometry(self, generation: int) -> None:
        """确认胶囊已脱离最大化且实际几何严格等于目标值。"""
        if int(getattr(self, "_window_mode_generation", 0) or 0) != int(generation):
            return
        if not bool(getattr(self, "_panel_collapsed", False)):
            return
        target = QRect(getattr(self, "_panel_collapse_target_geometry", QRect()))
        if not target.isValid():
            return

        try:
            still_maximized = bool(self.isMaximized())
        except Exception:
            still_maximized = False
        try:
            actual = QRect(self.geometry())
        except Exception:
            actual = QRect()
        if not still_maximized and actual == target:
            self._refresh_panel_collapse_visual()
            return

        self._force_normal_window_state()
        self._apply_panel_collapse_geometry(target)
        try:
            self.show()
        except Exception:
            pass
        self._refresh_panel_collapse_visual()

    def _schedule_panel_collapse_geometry_confirmation(self, generation: int) -> None:
        """覆盖 Windows 状态切换的短暂异步窗口，防止胶囊残留全屏外壳。"""
        for delay_ms in (0, 80, 250):
            def confirm_later(
                generation: int = int(generation),
                panel=self,
            ) -> None:
                try:
                    panel._ensure_panel_collapse_geometry(generation)
                except RuntimeError:
                    pass

            try:
                QTimer.singleShot(delay_ms, confirm_later)
            except Exception:
                pass

    def _apply_maximized_work_area_fallback(self) -> None:
        """原生状态失真时，按屏幕工作区精确铺满，保证任务栏不被覆盖。"""
        target = self._target_maximized_work_area()
        if not target.isValid():
            return
        self._forcing_native_maximize = True
        try:
            try:
                self.setWindowState(Qt.WindowState.WindowNoState)
                self.showNormal()
            except Exception:
                pass
            self._release_panel_size_constraints(release_content=True)
            self.setGeometry(target)
            self.show()
        finally:
            self._forcing_native_maximize = False

    def _ensure_native_maximized_state(self, *, allow_geometry_fallback: bool = False) -> None:
        """以实际窗口几何确认最大化，不能依赖可能失真的 isMaximized()。"""
        if bool(getattr(self, "_panel_collapsed", False)):
            return
        if not bool(getattr(self, "_is_maximized", False)):
            self._pending_native_maximize_restore = False
            return
        if self._has_effective_maximized_geometry():
            self._pending_native_maximize_restore = False
            return

        self._pending_native_maximize_restore = True
        self._force_native_maximized_window_state()
        if not self._has_effective_maximized_geometry() and bool(allow_geometry_fallback):
            self._apply_maximized_work_area_fallback()
        self._pending_native_maximize_restore = not self._has_effective_maximized_geometry()

    def _schedule_native_maximized_state_confirmation(self) -> None:
        # 80ms 后允许工作区兜底；后两次检查覆盖临时置顶释放回调。
        generation = int(getattr(self, "_window_mode_generation", 0) or 0)
        for delay_ms, allow_fallback in ((0, False), (80, True), (750, True), (1450, True)):
            def confirm_later(
                allow_fallback: bool = allow_fallback,
                generation: int = generation,
                panel=self,
            ) -> None:
                try:
                    if int(getattr(panel, "_window_mode_generation", 0) or 0) != generation:
                        return
                    panel._ensure_native_maximized_state(
                        allow_geometry_fallback=allow_fallback
                    )
                except RuntimeError:
                    pass

            try:
                QTimer.singleShot(delay_ms, confirm_later)
            except Exception:
                pass

    def _set_native_topmost(self, topmost: bool) -> None:
        import os
        if os.name != "nt":
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOACTIVATE = 0x0010
            SWP_SHOWWINDOW = 0x0040
            SWP_FRAMECHANGED = 0x0020

            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(hwnd),
                ctypes.c_void_p(HWND_TOPMOST if topmost else HWND_NOTOPMOST),
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED,
            )

            # 取消置顶时强行触发位置同步，促使 Windows DWM 强制刷新并重算窗口 Z-Order 层级关系
            if (
                not topmost
                and not self.isMinimized()
                and not self._preserve_native_maximized_geometry()
            ):
                self.setGeometry(self.geometry())
        except Exception:
            pass

    def _release_transient_topmost(self) -> None:
        if bool(getattr(self, "_panel_collapsed", False)) or bool(getattr(self, "_is_collapsed", False)):
            self._set_native_topmost(True)
            return
        self._set_native_topmost(False)

    def _schedule_transient_topmost_release(self, delay_ms: int = 600) -> None:
        def release_once() -> None:
            try:
                self._release_transient_topmost()
            except RuntimeError:
                pass

        QTimer.singleShot(max(0, int(delay_ms)), release_once)
        QTimer.singleShot(max(0, int(delay_ms) + 700), release_once)

    def _log_ai_geometry(self, event_name: str, **fields) -> None:
        if not _AI_GEOMETRY_TRACE_ENABLED:
            return
        try:
            now = time.perf_counter()
            started_at = float(getattr(self, "_ai_geometry_trace_started_at", now) or now)
            last_event_at = float(getattr(self, "_ai_geometry_last_event_at", now) or now)
            seq = int(getattr(self, "_ai_geometry_trace_seq", 0) or 0) + 1
            geometry = QRect(self.geometry())
            frame_geometry = QRect(self.frameGeometry())
            previous_geometry = getattr(self, "_ai_geometry_last_logged_geometry", None)
            previous_frame_geometry = getattr(self, "_ai_geometry_last_logged_frame_geometry", None)

            def rect_delta(current: QRect, previous) -> Optional[dict[str, int]]:
                try:
                    if previous is None:
                        return None
                    return {
                        "dx": int(current.x() - previous.x()),
                        "dy": int(current.y() - previous.y()),
                        "dw": int(current.width() - previous.width()),
                        "dh": int(current.height() - previous.height()),
                    }
                except Exception:
                    return None

            bubble_view = getattr(self, "_bubble_view", None)
            thinking_card = getattr(self, "_thinking_card", None)
            editor_container = getattr(self, "_editor_container", None)
            editor = getattr(self, "_editor", None)
            screen = (
                QGuiApplication.screenAt(frame_geometry.center())
                or QGuiApplication.screenAt(self.pos())
                or QGuiApplication.primaryScreen()
            )

            bubble_content_h = None
            if bubble_view is not None:
                try:
                    bubble_content_h = int(bubble_view.content_height())
                except Exception:
                    bubble_content_h = None

            editor_text_len = None
            if editor is not None:
                try:
                    editor_text_len = len(str(editor.toPlainText() or ""))
                except Exception:
                    editor_text_len = None

            payload = {
                "event": str(event_name),
                "seq": seq,
                "t_ms": round((now - started_at) * 1000.0, 2),
                "dt_ms": round((now - last_event_at) * 1000.0, 2),
                "wall_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "previous_event": str(getattr(self, "_ai_geometry_last_event_name", "") or ""),
                "geometry": _rect_to_log_value(geometry),
                "frame_geometry": _rect_to_log_value(frame_geometry),
                "geometry_delta": rect_delta(geometry, previous_geometry),
                "frame_geometry_delta": rect_delta(frame_geometry, previous_frame_geometry),
                "pos": _point_to_log_value(self.pos()),
                "size": _size_to_log_value(self.size()),
                "frame_bottom_y": int(frame_geometry.y() + frame_geometry.height()),
                "visible": bool(self.isVisible()),
                "active": bool(self.isActiveWindow()),
                "minimized": bool(self.isMinimized()),
                "is_maximized": bool(getattr(self, "_is_maximized", False)),
                "panel_collapsed": bool(getattr(self, "_panel_collapsed", False)),
                "history_showing": bool(getattr(self, "_history_showing", False)),
                "is_chatting": bool(getattr(self, "_is_chatting", False)),
                "suppress_reposition": bool(getattr(self, "_suppress_reposition", False)),
                "repositioning": bool(getattr(self, "_repositioning", False)),
                "geometry_frozen": bool(getattr(self, "_geometry_frozen", False)),
                "keep_bottom": getattr(self, "_keep_bottom_y_on_next_reposition", None),
                "last_stable_bottom_y": getattr(self, "_last_stable_bottom_y", None),
                "last_pos": _normalize_ai_log_value(getattr(_ocr_text_panel_class(), "_last_pos", None)),
                "bubble_visible": bool(bubble_view is not None and bubble_view.isVisible()),
                "bubble_hidden": bool(bubble_view is not None and bubble_view.isHidden()),
                "bubble_h": int(bubble_view.height()) if bubble_view is not None else None,
                "bubble_min_h": int(bubble_view.minimumHeight()) if bubble_view is not None else None,
                "bubble_content_h": bubble_content_h,
                "thinking_visible": bool(thinking_card is not None and thinking_card.isVisible()),
                "thinking_active": bool(thinking_card is not None and getattr(thinking_card, "is_thinking", lambda: False)()),
                "thinking_h": int(thinking_card.height()) if thinking_card is not None else None,
                "thinking_max_h": int(thinking_card.maximumHeight()) if thinking_card is not None else None,
                "editor_container_h": int(editor_container.height()) if editor_container is not None else None,
                "editor_container_min_h": int(editor_container.minimumHeight()) if editor_container is not None else None,
                "editor_container_max_h": int(editor_container.maximumHeight()) if editor_container is not None else None,
                "editor_text_len": editor_text_len,
                "screen_available": _rect_to_log_value(screen.availableGeometry()) if screen is not None else None,
                "region": _rect_to_log_value(getattr(self, "_region", QRect())),
            }
            payload.update({str(k): _normalize_ai_log_value(v) for k, v in fields.items()})

            self._ai_geometry_trace_seq = seq
            self._ai_geometry_last_event_at = now
            self._ai_geometry_last_event_name = str(event_name)
            self._ai_geometry_last_logged_geometry = geometry
            self._ai_geometry_last_logged_frame_geometry = frame_geometry

            _get_ai_geometry_logger().info(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
        except Exception:
            pass

    def _force_native_foreground(self) -> None:
        import os
        if os.name != "nt":
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = int(self.winId())
            if not hwnd:
                return
            SW_SHOWNORMAL = 1
            user32.ShowWindow(ctypes.c_void_p(hwnd), SW_SHOWNORMAL)
            user32.BringWindowToTop(ctypes.c_void_p(hwnd))

            # 当本进程不是当前前台窗口所有者时，Windows 会阻止 SetForegroundWindow，
            # 使用 AttachThreadInput 将当前线程临时附加到前台线程的输入队列，
            # 从而绕过 Windows 的前台窗口锁定保护（ForegroundLockTimeout），
            # 确保 SetForegroundWindow 能可靠成功。
            foreground_hwnd = user32.GetForegroundWindow()
            foreground_thread_id = 0
            current_thread_id = kernel32.GetCurrentThreadId()
            attached = False
            if foreground_hwnd and foreground_hwnd != hwnd:
                foreground_thread_id = user32.GetWindowThreadProcessId(
                    ctypes.c_void_p(foreground_hwnd), None
                )
                if foreground_thread_id and foreground_thread_id != current_thread_id:
                    attached = bool(user32.AttachThreadInput(
                        current_thread_id, foreground_thread_id, True
                    ))

            user32.SetForegroundWindow(ctypes.c_void_p(hwnd))

            # 立即分离线程输入，避免长期附加产生副作用
            if attached and foreground_thread_id:
                user32.AttachThreadInput(
                    current_thread_id, foreground_thread_id, False
                )
        except Exception:
            pass

    def _focus_editor_for_input(self) -> None:
        try:
            self._editor.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        except Exception:
            try:
                self._editor.setFocus()
            except Exception:
                pass
        try:
            self._editor.ensureCursorVisible()
        except Exception:
            pass

    def _can_start_question_editor_ime_focus_reset(self) -> bool:
        if not self.isVisible():
            return False
        if bool(getattr(self, "_history_showing", False)):
            return False
        if bool(getattr(self, "_panel_collapsed", False)) or bool(getattr(self, "_is_collapsed", False)):
            return False

        editor = getattr(self, "_editor", None)
        bubble_view = getattr(self, "_bubble_view", None)
        if editor is None or bubble_view is None:
            return False
        try:
            if not bubble_view.isVisible():
                return False
        except Exception:
            return False
        try:
            if str(editor.toPlainText() or ""):
                return False
        except Exception:
            return False
        try:
            viewport = editor.viewport()
            if not (editor.hasFocus() or (viewport is not None and viewport.hasFocus())):
                return False
        except Exception:
            return False
        return True

    def _question_editor_ready_for_ime_refocus(self) -> bool:
        if not self.isVisible():
            return False
        editor = getattr(self, "_editor", None)
        if editor is None:
            return False
        try:
            return not bool(str(editor.toPlainText() or ""))
        except Exception:
            return False

    def _schedule_question_editor_ime_focus_reset(self) -> None:
        """回答区展开后重建一次输入法焦点上下文，等价于用户先点输出区再点输入区。"""
        QTimer.singleShot(0, self._reset_question_editor_ime_focus_once)

    def _reset_question_editor_ime_focus_once(self) -> None:
        if not self._can_start_question_editor_ime_focus_reset():
            return

        editor = self._editor
        focus_target = None
        try:
            focus_target = self._bubble_view.viewport()
        except Exception:
            focus_target = None
        if focus_target is None:
            focus_target = self._bubble_view

        old_policy = None
        try:
            old_policy = focus_target.focusPolicy()
            focus_target.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            focus_target.setFocus(Qt.FocusReason.OtherFocusReason)
            QGuiApplication.inputMethod().reset()
        except Exception:
            pass

        def restore_editor_focus() -> None:
            try:
                if old_policy is not None:
                    focus_target.setFocusPolicy(old_policy)
            except Exception:
                pass
            if not self._question_editor_ready_for_ime_refocus():
                return
            try:
                editor.setFocus(Qt.FocusReason.OtherFocusReason)
            except Exception:
                try:
                    editor.setFocus()
                except Exception:
                    return
            try:
                editor.ensureCursorVisible()
            except Exception:
                pass
            try:
                QGuiApplication.inputMethod().reset()
            except Exception:
                pass

        QTimer.singleShot(0, restore_editor_focus)

    def _activate_for_text_input(self, *, repeat: bool = True) -> None:
        def activate_once(is_retry: bool = False) -> None:
            try:
                if bool(getattr(self, "_panel_collapsed", False)) or bool(getattr(self, "_is_collapsed", False)):
                    self.show()
                    self.raise_()
                    self._set_native_topmost(True)
                    return
                if is_retry:
                    # 如果是重试激活，仅当当前窗口已经是活动窗口时聚焦输入框，
                    # 绝不在重试（已失去用户热键或交互特权时）去强制调用 Windows 顶层 API 激活前台，
                    # 从而彻底避免导致 Windows 的任务栏图标产生亮起闪烁（FlashWindow）提示。
                    if self.isActiveWindow():
                        self._focus_editor_for_input()
                    return
                self._set_native_topmost(True)
                if self.isMinimized():
                    self.showNormal()
                else:
                    self.show()
                self.raise_()
                self._force_native_foreground()
                try:
                    self.activateWindow()
                except Exception:
                    pass
                self._focus_editor_for_input()
                self._schedule_transient_topmost_release(650)
            except RuntimeError:
                return

        activate_once()
        if bool(repeat):
            for delay in (60, 160, 320, 600):
                QTimer.singleShot(delay, lambda: activate_once(True))

    def _should_show_input_watermark(self) -> bool:
        if getattr(self, "_panel_collapsed", False) or getattr(self, "_is_collapsed", False):
            return False
        editor = getattr(self, "_editor", None)
        if editor is None:
            return False
        try:
            return str(editor.toPlainText() or "") == ""
        except Exception:
            return False

    def _sync_input_watermark_visibility(self) -> None:
        try:
            self._watermark.hide()
        except Exception:
            pass

    def _update_watermark_pos(self) -> None:
        try:
            self._watermark.hide()
        except Exception:
            pass

    def _update_translation_info_pos(self) -> None:
        if getattr(self, "_history_showing", False):
            self._translation_info.hide()
            return
        try:
            top_left = self._bubble_view.mapTo(self, QPoint(0, 0))
            w = self._bubble_view.width()
            h = self._bubble_view.height()
            fm = self._translation_info.fontMetrics()
            tw = int(fm.horizontalAdvance(self._translation_info.text())) + 8
            th = int(fm.height()) + 4
            self._translation_info.setFixedSize(tw, th)
            x = top_left.x() + w - tw - 4
            y = top_left.y() + h - th - 2
            self._translation_info.move(x, y)
            self._translation_info.raise_()
        except Exception:
            pass

    def _minimize_panel(self) -> None:
        self._toggle_panel_collapse(True)

    def _collapsed_panel_size(self) -> QSize:
        try:
            title_w = int(self._collapsed_title.fontMetrics().horizontalAdvance(self._collapsed_title.text()))
        except Exception:
            title_w = 56
        # 计入新增三色圆点控件宽度 (42px) 和布局间距 (8px)
        width = max(220, int(title_w + 42 + 8 + 20 + 12 + 16 + 28 + 28 + 12))
        return QSize(width, 46)

    def _start_dots_animation(self) -> None:
        if not _AI_DOTS_ANIMATION_ENABLED:
            return
        self._stop_dots_animation()

        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        from PyQt6.QtCore import QPropertyAnimation, QTimer, QEasingCurve

        self._dots_effects = []
        self._dots_anims = []

        # 收集主标题栏和折叠栏中的猫头图标
        owls = []

        # 1. 主标题栏图标
        if hasattr(self, "_title_bar") and self._title_bar is not None:
            tb = self._title_bar
            if hasattr(tb, "dot_owl") and tb.dot_owl is not None:
                owls.append(tb.dot_owl)

        # 2. 折叠态图标
        if hasattr(self, "c_dot_owl") and self.c_dot_owl is not None:
            owls.append(self.c_dot_owl)

        for i, owl in enumerate(owls):
            effect = QGraphicsOpacityEffect(owl)
            owl.setGraphicsEffect(effect)
            self._dots_effects.append(effect)

            anim = QPropertyAnimation(effect, b"opacity")
            anim.setDuration(1000)
            anim.setStartValue(1.0)
            anim.setKeyValueAt(0.5, 0.25)  # 呼吸渐变至 0.25 不透明度
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.InOutQuad)  # 平滑缓动
            anim.setLoopCount(-1)
            self._dots_anims.append((anim, i * 150))

        # 启动动画
        for anim, delay in self._dots_anims:
            QTimer.singleShot(delay, anim.start)

    def _stop_dots_animation(self) -> None:
        if hasattr(self, "_dots_anims") and self._dots_anims:
            for anim, _ in self._dots_anims:
                try:
                    anim.stop()
                except Exception:
                    pass
            self._dots_anims.clear()

        if hasattr(self, "_dots_effects") and self._dots_effects:
            for effect in self._dots_effects:
                try:
                    effect.setParent(None)
                except Exception:
                    pass
            self._dots_effects.clear()

        # 恢复状态
        owls = []
        if hasattr(self, "_title_bar") and self._title_bar is not None:
            tb = self._title_bar
            if hasattr(tb, "dot_owl"):
                owls.append(tb.dot_owl)
        if hasattr(self, "c_dot_owl"):
            owls.append(self.c_dot_owl)

        for owl in owls:
            if owl is not None:
                try:
                    owl.setGraphicsEffect(None)
                except Exception:
                    pass

    def _collapsed_window_size(self) -> QSize:
        capsule = self._collapsed_panel_size()
        return QSize(capsule.width() + 16, capsule.height() + 16)

    def _set_panel_collapsed_property(self, collapsed: bool) -> None:
        self.setProperty("panelCollapsed", bool(collapsed))
        try:
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()
        except Exception:
            pass

    def _set_maximized_property(self, maximized: bool) -> None:
        try:
            outer_margin = 0 if maximized else 8
            self._root_layout.setContentsMargins(outer_margin, outer_margin, outer_margin, outer_margin)
            self._root_layout.invalidate()
        except Exception:
            pass
        try:
            layout = self._interaction_container.layout()
            if layout is not None:
                layout.setContentsMargins(0, 0, 0, 0)
                layout.invalidate()
        except Exception:
            pass
        title_bar = getattr(self, "_title_bar", None)
        for widget in (
            self,
            getattr(self, "_view_stack", None),
            getattr(self, "_content_stack", None),
            getattr(self, "_interaction_container", None),
            title_bar,
            getattr(title_bar, "btn_close", None),
            getattr(self, "_bottom_row_widget", None),
        ):
            if widget is None:
                continue
            try:
                widget.setProperty("panelMaximized", bool(maximized))
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()
            except Exception:
                pass

    def _release_panel_size_constraints(self, *, release_content: bool = True) -> None:
        """释放普通窗口布局写入的固定尺寸，交还给原生最大化或后续布局计算。"""
        try:
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
        except Exception:
            pass
        if not release_content:
            return
        for widget in (
            getattr(self, "_editor_container", None),
            getattr(self, "_bubble_view", None),
        ):
            if widget is None:
                continue
            try:
                widget.setMinimumHeight(0)
                widget.setMaximumHeight(16777215)
            except Exception:
                pass

    def _sync_maximized_content_layout(self) -> None:
        """只同步最大化客户区内部布局，不修改原生窗口几何。"""
        if not bool(getattr(self, "_is_maximized", False)):
            return
        if bool(getattr(self, "_panel_collapsed", False)):
            return

        self._release_panel_size_constraints(release_content=True)
        layout_state = self._panel_layout_state()
        if layout_state == "empty":
            try:
                self._bubble_view.hide()
                self._bubble_view.setMinimumHeight(0)
                self._bubble_view.setMaximumHeight(16777215)
            except Exception:
                pass
            self._sync_compact_editor_height(self.height())
        else:
            input_height = 120
            try:
                self._editor_container.setFixedHeight(input_height)
            except Exception:
                pass
            try:
                self._bubble_view.setMinimumHeight(0)
                self._bubble_view.setMaximumHeight(16777215)
            except Exception:
                pass

        self._activate_panel_layouts()

    def _finish_native_maximize_layout(self, *, sync_bubbles: bool = True) -> None:
        """等待系统完成最大化后，按真实客户区尺寸刷新内部组件。"""
        if not bool(getattr(self, "_is_maximized", False)):
            return
        if bool(getattr(self, "_panel_collapsed", False)):
            return
        self._sync_maximized_content_layout()
        self._update_top_seam_cover()
        self._update_interaction_seam_covers()
        self._position_floating_window_buttons()
        self._position_context_warning_bar()
        if bool(sync_bubbles):
            self._sync_bubble_view_after_geometry_change(keep_bottom=True)

    def _hide_chat_transient_overlays(self) -> None:
        view = getattr(self, "_bubble_view", None)
        hide_overlays = getattr(view, "hide_transient_overlays", None)
        if callable(hide_overlays):
            try:
                hide_overlays()
            except Exception:
                pass

    def _restore_uncollapsed_editor_height(self) -> None:
        try:
            layout_state = self._panel_layout_state()
        except Exception:
            layout_state = "conversation" if getattr(self, "_chat_history", None) else "empty"

        if layout_state == "empty":
            try:
                self._bubble_view.hide()
                self._bubble_view.setMinimumHeight(0)
                self._bubble_view.setMaximumHeight(16777215)
            except Exception:
                pass
            self._sync_compact_editor_height(self.height())
            return

        try:
            restore_height = 120
            if hasattr(self._editor_container, "setFixedHeight"):
                self._editor_container.setFixedHeight(restore_height)
            else:
                self._editor_container.setMinimumHeight(restore_height)
                self._editor_container.setMaximumHeight(restore_height)
        except Exception:
            pass

    def _finish_panel_uncollapse_after_show(self) -> None:
        if bool(getattr(self, "_panel_collapsed", False)):
            return
        skip_heavy_layout = bool(getattr(self, "_skip_uncollapse_reposition_once", False))
        self._skip_uncollapse_reposition_once = False
        try:
            if bool(getattr(self, "_is_maximized", False)):
                self._finish_native_maximize_layout()
            elif not skip_heavy_layout:
                self._reposition()
                self._ensure_latest_chat_visible_after_layout(defer=True)
            else:
                self._restore_uncollapsed_editor_height()
        except RuntimeError:
            return
        except Exception:
            pass

        try:
            if not self._bubble_view.is_empty() and not getattr(self, "_history_showing", False):
                self._translation_info.show()
                self._update_translation_info_pos()
        except Exception:
            pass
        try:
            self._update_watermark_pos()
        except Exception:
            pass
        self._restore_title_bar_after_window_restore()
        self._schedule_title_bar_restore_check()
        try:
            QTimer.singleShot(0, self._activate_for_text_input)
        except Exception:
            self._activate_for_text_input()

    def _toggle_panel_collapse(self, collapse: bool) -> None:
        collapse = bool(collapse)
        if bool(getattr(self, "_panel_collapsed", False)) == collapse:
            return
        window_mode_generation = self._advance_window_mode_generation()
        self._hide_chat_transient_overlays()
        if collapse:
            self._last_expanded_pos = self.pos()
            restore_maximized = bool(getattr(self, "_is_maximized", False))
            try:
                restore_maximized = bool(restore_maximized or self.isMaximized())
            except Exception:
                pass
            self._panel_collapse_restore_maximized = restore_maximized
            self._pending_native_maximize_restore = False
            if restore_maximized:
                self._capture_maximized_work_area()
            restore_geometry = QRect(self.geometry())
            if restore_maximized:
                pre_max_geometry = QRect(getattr(self, "_pre_max_geometry", QRect()))
                if pre_max_geometry.isValid():
                    restore_geometry = pre_max_geometry
            self._panel_collapse_restore_size = QSize(
                max(1, restore_geometry.width()),
                max(1, restore_geometry.height()),
            )
            self._panel_collapse_restore_geometry = QRect(restore_geometry)
            self._skip_uncollapse_reposition_once = False
            self._panel_collapsed = True
            self._set_panel_collapsed_property(True)
            self._is_maximized = False
            self._set_maximized_property(False)
            self._watermark.hide()
            self._translation_info.hide()
            self._view_stack.hide()
            self._collapsed_widget.show()
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            collapsed_size = self._collapsed_panel_size()
            window_size = self._collapsed_window_size()
            self._collapsed_widget.setFixedSize(collapsed_size)
            if getattr(self, "_was_snapped", False) and getattr(self, "_last_snapped_pos", None) is not None:
                target_pos = QPoint(self._last_snapped_pos)
            else:
                position_source = (
                    restore_geometry.topLeft()
                    if restore_maximized and restore_geometry.isValid()
                    else self.pos()
                )
                target_pos = self._clamp_window_pos(
                    position_source,
                    width=window_size.width(),
                    height=window_size.height(),
                )
            target_geometry = QRect(target_pos, window_size)
            self._panel_collapse_target_geometry = QRect(target_geometry)
            if restore_maximized:
                self._force_normal_window_state()
            self._apply_panel_collapse_geometry(target_geometry)
            self.show()
            self._refresh_panel_collapse_visual()
            self.raise_()
            self._set_native_topmost(True)
            self._schedule_panel_collapse_geometry_confirmation(window_mode_generation)
            return

        self._panel_collapsed = False
        restore_maximized = bool(getattr(self, "_panel_collapse_restore_maximized", False))
        self._panel_collapse_restore_maximized = False
        self.setUpdatesEnabled(False)
        try:
            self._set_panel_collapsed_property(False)
            self._collapsed_widget.hide()
            self._view_stack.show()
            self._is_maximized = restore_maximized
            self._set_maximized_property(restore_maximized)
            self._release_panel_size_constraints(release_content=True)
            self._collapsed_widget.setMinimumSize(0, 0)
            self._collapsed_widget.setMaximumSize(16777215, 16777215)

            if restore_maximized:
                try:
                    self._title_bar.btn_maximize.setIcon(OcrTitleBar._make_restore_icon(self._title_bar._icon_size))
                    self._title_bar.btn_maximize.setToolTip("还原窗口")
                except Exception:
                    pass
                self._skip_uncollapse_reposition_once = True
                self._pending_native_maximize_restore = True
                self._force_native_maximized_window_state()
                self._schedule_native_maximized_state_confirmation()
            else:
                try:
                    self._title_bar.btn_maximize.setIcon(OcrTitleBar._make_maximize_icon(self._title_bar._icon_size))
                    self._title_bar.btn_maximize.setToolTip("最大化")
                except Exception:
                    pass
                self._pending_native_maximize_restore = False
                self._force_normal_window_state()
                restore_size = QSize(getattr(self, "_panel_collapse_restore_size", QSize()))
                restore_geometry = QRect(getattr(self, "_panel_collapse_restore_geometry", QRect()))
                if restore_geometry.isValid() and restore_geometry.width() > 1 and restore_geometry.height() > 1:
                    restore_pos = self._clamp_window_pos(
                        restore_geometry.topLeft(),
                        width=restore_geometry.width(),
                        height=restore_geometry.height(),
                    )
                    self.setGeometry(QRect(restore_pos, restore_geometry.size()))
                else:
                    if not restore_size.isValid() or restore_size.width() <= 1 or restore_size.height() <= 1:
                        restore_size = QSize(800, 520)
                    current_pos = self.pos()
                    self.resize(restore_size)
                    self.move(self._clamp_window_pos(current_pos, width=restore_size.width(), height=restore_size.height()))
                self._skip_uncollapse_reposition_once = True
            self._panel_collapse_target_geometry = QRect()
        finally:
            self.setUpdatesEnabled(True)
        if not restore_maximized:
            self.show()
        self.raise_()
        self.update()
        QTimer.singleShot(0, self._finish_panel_uncollapse_after_show)

    def _snap_collapsed_to_edge(self) -> None:
        if not bool(getattr(self, "_panel_collapsed", False)) and not bool(getattr(self, "_is_collapsed", False)):
            return
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)
        x = int(self.x())
        y = int(self.y())
        w = int(self.width())
        h = int(self.height())
        threshold = 28
        visible = 18
        distances = {
            "left": abs(x - int(geo.left())),
            "right": abs(int(geo.right() + 1) - int(x + w)),
            "top": abs(y - int(geo.top())),
            "bottom": abs(int(geo.bottom() + 1) - int(y + h)),
        }
        edge, distance = min(distances.items(), key=lambda item: item[1])
        if int(distance) > threshold:
            self._was_snapped = False
            self._last_snapped_pos = None
            return
        if edge == "left":
            target = QPoint(int(geo.left() - w + visible), max(int(geo.top()), min(int(y), int(geo.bottom() - h + 1))))
        elif edge == "right":
            target = QPoint(int(geo.right() + 1 - visible), max(int(geo.top()), min(int(y), int(geo.bottom() - h + 1))))
        elif edge == "top":
            target = QPoint(max(int(geo.left()), min(int(x), int(geo.right() - w + 1))), int(geo.top() - h + visible))
        else:
            target = QPoint(max(int(geo.left()), min(int(x), int(geo.right() - w + 1))), int(geo.bottom() + 1 - visible))
        self.move(target)
        self._was_snapped = True
        self._last_snapped_pos = target

    def toggle_editor_expand(self) -> None:
        """统一管理大文本框的完全展开与高保真折回"""
        if self._is_expanded:
            self.restore_pre_expand_state()
        else:
            self.perform_editor_expand()

    def perform_editor_expand(self) -> None:
        """执行完全展开算法：最大化利用屏幕空间，按几何平衡模型分配，确保底部按钮完全可视"""
        # 1. 采集当前尺寸作为还原快照
        pre_trans_h = self._bubble_view.height() if self._bubble_view.isVisible() else 0
        pre_window_h = self.height()
        pre_window_pos = self.pos()

        # 布局总的外边距 margins = 12 + 12 = 24
        margins_h = 24
        # 底部工具栏的实际高度
        row_h = self._bottom_row_widget.height()
        if row_h <= 0:
            row_h = self._bottom_row_widget.sizeHint().height() or 42

        if self._bubble_view.isVisible() and pre_trans_h > 0:
            pre_editor_h = self._editor_container.height()
        else:
            # 无回答框场景：优先采用精准真实的物理高度，若未稳定则使用高保真计算值回退，彻底排除捕获延迟偏差
            actual_h = self._editor_container.height()
            if actual_h > 30:
                pre_editor_h = actual_h
            else:
                pre_editor_h = pre_window_h - row_h - margins_h

        self._pre_expand_snapshot = (pre_editor_h, pre_trans_h, pre_window_h, pre_window_pos)

        # 2. 计算大文本框在无滚动条情况下的理想高度
        ideal_editor_h = int(self._editor.document().documentLayout().documentSize().height()) + 12
        ideal_editor_h = max(pre_editor_h, ideal_editor_h)
        delta_y = ideal_editor_h - pre_editor_h
        if delta_y <= 0:
            # 即使不需要拉伸，也更新标志位支持后续反向折回
            self._is_expanded = True
            self._editor_container._is_expanded = True
            self._editor_container._check_expand_button_visibility()
            return

        # 3. 确定当前屏幕物理边界
        from PyQt6.QtGui import QGuiApplication
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        max_bottom = geo.bottom() - 10
        min_top = geo.top() + 10
        max_allowed_h = geo.height() - 32

        # 4. 精密计算高度容纳量
        # 两个大编辑框在屏幕中能分到的最大累计空间
        max_available_for_editors = max_allowed_h - row_h - margins_h

        # 当前两编辑框实际占用总高度
        editors_current_h = pre_editor_h + pre_trans_h
        # 理想状态下，两编辑框的总高度
        editors_ideal_h = ideal_editor_h + pre_trans_h

        if self._bubble_view.isVisible() and pre_trans_h > 0:
            # 回答框可见，要为它保留至少 50px 的安全显示高度
            if editors_ideal_h > max_available_for_editors:
                editors_target_h = min(editors_ideal_h, max_available_for_editors)
                target_editor_h = editors_target_h - 50
                target_trans_h = 50
            else:
                target_editor_h = ideal_editor_h
                target_trans_h = pre_trans_h
        else:
            # 回答框未显示
            if ideal_editor_h > max_available_for_editors:
                target_editor_h = max_available_for_editors
            else:
                target_editor_h = ideal_editor_h
            target_trans_h = 0

        # 5. 计算窗口的目标高度与顶起平移量
        target_window_h = target_editor_h + target_trans_h + row_h + margins_h
        target_window_h = min(max_allowed_h, target_window_h)

        current_y = self.y()
        if current_y + target_window_h > max_bottom:
            new_y = max_bottom - target_window_h
            new_y = max(min_top, new_y)
        else:
            new_y = current_y

        # 6. 安全更新组件高度限制并执行重排
        self._editor_container.setFixedHeight(int(target_editor_h))
        if self._bubble_view.isVisible() and target_trans_h > 0:
            self._bubble_view.setFixedHeight(int(target_trans_h))

        self.move(self.x(), int(new_y))
        self.resize(self.width(), int(target_window_h))

        # 7. 更新标志状态与按钮外观
        self._is_expanded = True
        self._editor_container._is_expanded = True
        self._editor_container._check_expand_button_visibility()

    def restore_pre_expand_state(self) -> None:
        """执行一键高保真物理折回还原快照状态"""
        if not getattr(self, "_pre_expand_snapshot", None):
            # 无有效快照，强行重置标志
            self._is_expanded = False
            self._editor_container._is_expanded = False
            self._editor_container._check_expand_button_visibility()
            return

        pre_editor_h, pre_trans_h, pre_window_h, _ = self._pre_expand_snapshot

        # 1. 先安全物理缩小组件高度，允许窗口可以变矮，并死死锁定防止二次撑大
        self._editor_container.setFixedHeight(pre_editor_h)
        if self._bubble_view.isVisible() and pre_trans_h > 0:
            self._bubble_view.setFixedHeight(pre_trans_h)

        # 强制刷新布局，使布局的最小尺寸立即反映编辑器缩小后的真实值，
        # 否则后续 resize 会因布局缓存的旧最小尺寸（展开态的大值）大于目标高度而失败，
        # 导致窗口无法缩回，收回后编辑器被布局撑大、滚动条消失、展开图标也消失。
        self.layout().activate()

        # 2. 仅恢复窗口高度，不恢复位置（用户展开后可能已拖动窗口到新位置，收回时应保持当前位置）
        # 先解除窗口高度上下限约束，确保 resize 可将窗口缩小到 pre_window_h
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        self.resize(self.width(), pre_window_h)

        # 3. 更新所有状态标志与按钮矢量图形
        self._is_expanded = False
        self._editor_container._is_expanded = False
        self._editor_container._check_expand_button_visibility()
        self._pre_expand_snapshot = None

        # 4. 延迟释放高度限制（异步等待物理 resize 渲染完成），恢复原生布局自适应以消除任何计算误差
        from PyQt6.QtCore import QTimer
        def release_limits():
            try:
                self._editor_container.setMinimumHeight(0)
                self._editor_container.setMaximumHeight(16777215)
                self._bubble_view.setMinimumHeight(0)
                self._bubble_view.setMaximumHeight(16777215)
                # 安全校验：若窗口高度未能成功缩回（布局最小尺寸延迟刷新等偶发原因），
                # 则在此处再次强制收缩，确保编辑器不会因窗口仍处于展开高度而被布局撑大
                if self.height() != pre_window_h:
                    self.setMinimumHeight(0)
                    self.setMaximumHeight(16777215)
                    self.resize(self.width(), pre_window_h)
                # 重新刷新一次展开按钮可见性，确保收回后若有滚动条则正确显示展开图标
                self._editor_container._check_expand_button_visibility()
            except Exception:
                pass
        QTimer.singleShot(50, release_limits)

    def _toggle_collapse(self, collapse: bool) -> None:
        """切换历史记录界面的折叠胶囊态。"""
        if self._history_panel is None:
            return
        self._is_collapsed = collapse
        from PyQt6.QtGui import QIcon
        from PyQt6.QtCore import QTimer

        if collapse:
            self._last_expanded_pos = self.pos()
            # 1. 隐藏 Panel 的底部内容
            self._history_panel._splitter.hide()
            self._history_panel._status_bar.hide()

            # 2. 隐藏标题栏的部分按钮与标签，避免挤压右侧按钮
            self._history_panel._title_bar.btn_return.hide()
            self._history_panel._title_bar.btn_fullscreen.hide()
            if hasattr(self._history_panel._title_bar, "title_label"):
                self._history_panel._title_bar.title_label.show()
                self._history_panel._title_bar.title_label.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Medium))
                self._history_panel._title_bar.title_label.setStyleSheet("color: #475569; background: transparent; border: none; padding-left: 8px;")

            # 3. 改变标题栏的最小化按钮状态：最小化改为横竖等长英文字符加号，关闭改为纯字符叉号，以防手绘图标悬停白底时变白隐形
            self._history_panel._title_bar.btn_minimize.setIcon(QIcon())
            self._history_panel._title_bar.btn_minimize.setText("+")
            self._history_panel._title_bar.btn_minimize.setToolTip("还原")
            self._history_panel._title_bar.btn_minimize.setFixedSize(28, 28)

            self._history_panel._title_bar.btn_close.setIcon(QIcon())
            self._history_panel._title_bar.btn_close.setText("✕")
            self._history_panel._title_bar.btn_close.setToolTip("直接关闭窗口")
            self._history_panel._title_bar.btn_close.setFixedSize(28, 28)

            # 4. 调整样式，使折叠状态成为完美的、四角对称的 12px 圆角胶囊，且平时也清晰显示加号与关闭按钮
            self._history_panel.setStyleSheet("""
                QWidget#TranslationHistoryPanel {
                    background: #ffffff;
                    border: 1px solid #cbd5e1;
                    border-radius: 12px;
                }
                QWidget#HistoryLeftPanel, QWidget#HistoryRightPanel {
                    border: none;
                }
                QWidget#HistoryTitleBar {
                    background: #ffffff;
                    border: none;
                    border-radius: 12px;
                }
                QPushButton#TitleBarBtn, QPushButton#TitleBarBtnClose {
                    background: transparent !important;
                    border: none !important;
                    color: #64748b !important;
                    font-weight: bold !important;
                    font-size: 13px !important;
                    padding: 0px !important;
                    margin: 0px !important;
                    min-width: 28px !important;
                    max-width: 28px !important;
                    min-height: 28px !important;
                    max-height: 28px !important;
                }
                QPushButton#TitleBarBtn:hover {
                    color: #2563eb !important;
                    background: transparent !important;
                }
                QPushButton#TitleBarBtnClose:hover {
                    color: #ef4444 !important;
                    background: transparent !important;
                }
            """)

            # 5. 动态测算胶囊宽度，同步调整标题栏布局 margins，达到与划词胶囊 100% 对齐
            try:
                title_w = int(self._history_panel._title_bar.title_label.fontMetrics().horizontalAdvance("历史记录"))
            except Exception:
                title_w = 56
            width = max(168, int(title_w + 20 + 12 + 16 + 28 + 28 + 12))
            self._history_panel._title_bar.layout().setContentsMargins(20, 0, 12, 0)
            self._root_layout.setContentsMargins(8, 8, 8, 8)
            self._history_panel.setFixedSize(width, 44)
            self.setFixedSize(width + 16, 60)
            self.show()
            self.raise_()
            self._set_native_topmost(True)
        else:
            # 1. 恢复 Panel 的底部内容
            self._history_panel._splitter.show()
            self._history_panel._status_bar.show()

            # 2. 恢复标题栏的按钮与标签
            self._history_panel._title_bar.btn_return.show()
            self._history_panel._title_bar.btn_fullscreen.show()
            if hasattr(self._history_panel._title_bar, "title_label"):
                self._history_panel._title_bar.title_label.show()
                self._history_panel._title_bar.title_label.setFont(QFont("Microsoft YaHei UI", 11, QFont.Weight.Bold))
                self._history_panel._title_bar.title_label.setStyleSheet("color: #1e293b; background: transparent; border: none; padding-right: 4px;")

            # 3. 恢复控制按钮的原有手绘图标和文本并重置尺寸为 46x30，恢复标题栏边距
            self._history_panel._title_bar.layout().setContentsMargins(12, 0, 0, 0)
            self._history_panel._title_bar.btn_minimize.setFixedSize(46, 30)
            self._history_panel._title_bar.btn_minimize.setText("")
            from deepcat.ui.history_panel import HistoryTitleBar
            self._history_panel._title_bar.btn_minimize.setIcon(HistoryTitleBar._make_minimize_icon(self._history_panel._title_bar._icon_size))
            self._history_panel._title_bar.btn_minimize.setToolTip("最小化")

            self._history_panel._title_bar.btn_fullscreen.setFixedSize(46, 30)

            self._history_panel._title_bar.btn_close.setFixedSize(46, 30)
            self._history_panel._title_bar.btn_close.setText("")
            self._history_panel._title_bar.btn_close.setIcon(HistoryTitleBar._make_close_icon(self._history_panel._title_bar._icon_size))
            self._history_panel._title_bar.btn_close.setToolTip("直接关闭窗口")

            # 4. 恢复原有样式并追加嵌入式融合样式重载
            self._history_panel._apply_styles()
            self._history_panel.setStyleSheet(self._history_panel.styleSheet() + """
                QWidget#TranslationHistoryPanel {
                    background: #ffffff;
                    border: 1px solid #e2e8f0;
                    border-radius: 8px;
                }
                QWidget#HistoryLeftPanel, QWidget#HistoryRightPanel {
                    border: none;
                }
                QWidget#HistoryTitleBar {
                    border-top-left-radius: 8px;
                    border-top-right-radius: 8px;
                }
                QWidget#HistoryStatusBar {
                    border-bottom-left-radius: 8px;
                    border-bottom-right-radius: 8px;
                }
            """)

            # 5. 重置大窗口布局边距为展开态（8px），解除大小限制并调整尺寸
            self._root_layout.setContentsMargins(8, 8, 8, 8)
            self._history_panel.setMinimumSize(0, 0)
            self._history_panel.setMaximumSize(16777215, 16777215)
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)

            # 还原大窗口到折叠前最后一次停留的位置
            last_expanded_pos = getattr(self, "_last_expanded_pos", None)
            if last_expanded_pos is not None:
                self.move(self._clamp_window_pos(last_expanded_pos, width=800, height=520))
            else:
                self.move(self._clamp_window_pos(self.pos(), width=800, height=520))
            self.resize(800, 520)
            self._schedule_transient_topmost_release(600)
            QTimer.singleShot(100, self._history_panel._refresh_hover_state)

    def _on_history_fullscreen(self, fullscreen: bool) -> None:
        """处理历史面板全屏请求。"""
        if bool(fullscreen):
            self._pre_fullscreen_geo = self.geometry()
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                self.setWindowFlags(
                    self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
                )
                self.setGeometry(geo)
                self.showNormal()
        else:
            # 恢复原始几何，移除非全屏状态不需要的 WindowStaysOnTopHint
            self.setWindowFlags(
                self.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint
            )
            if hasattr(self, "_pre_fullscreen_geo"):
                self.setGeometry(self._pre_fullscreen_geo)
            else:
                self.resize(800, 520)
                self.move(self._clamp_window_pos(self.pos(), width=800, height=520))
        self.show()
        self.raise_()
        if not bool(fullscreen):
            self._set_native_topmost(False)
        # 全屏切换后刷新历史列表的悬停状态
        if self._history_panel is not None:
            QTimer.singleShot(200, self._history_panel._refresh_hover_state)

    def set_region(self, region: QRect) -> None:
        self._region = QRect(region)
        self._reposition()

    def _copy_text(self) -> None:
        try:
            text = self._copy_source_text()
            if self._bubble_view.isVisible():
                copied = copy_markdown_to_clipboard(text)
            else:
                copied = copy_plain_text_to_clipboard(text)
            if not copied:
                self._show_light_feedback("复制失败：剪贴板不可用。", tone="error", auto_hide_ms=2600)
                return
            self._show_light_feedback("内容已复制到剪贴板。", tone="success", auto_hide_ms=1500)
        except Exception as exc:
            self._show_light_feedback(f"复制失败：{exc}", tone="error", auto_hide_ms=5200)
            return

    def _copy_source_text(self) -> str:
        try:
            if self._bubble_view.isVisible():
                # 如果是多轮会话模式，智能复制最后一条 AI 回答的气泡内容（规避多余提问或历史杂音）
                if getattr(self, "_is_chatting", False) and hasattr(self, "_chat_history") and self._chat_history:
                    for msg in reversed(self._chat_history):
                        if msg.get("role") == "assistant" and msg.get("content"):
                            return msg["content"]

                text = str(self._bubble_view.plain_text() or "").strip()
                if text and text not in {"正在翻译...", "正在回答..."} and not text.startswith("翻译失败：") and not text.startswith("问答失败："):
                    return text
        except Exception:
            pass
        return str(self._editor.toPlainText() or "")

    @staticmethod
    def _clean_clipboard_text(text: str) -> str:
        return clean_clipboard_text(text)

    def _event_global_pos(self, event) -> QPoint:
        try:
            return event.globalPosition().toPoint()
        except Exception:
            return event.globalPos()

    def _start_window_drag(self, event) -> bool:
        try:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            start_pos = self._event_global_pos(event)
            self._dragging_window = True
            self._drag_start_pos = start_pos
            self._drag_offset = start_pos - self.frameGeometry().topLeft()
            event.accept()
            return True
        except Exception:
            self._dragging_window = False
            return False

    def _move_window_drag(self, event) -> bool:
        if not bool(getattr(self, "_dragging_window", False)):
            return False
        try:
            if not bool(event.buttons() & Qt.MouseButton.LeftButton):
                self._dragging_window = False
                return False
            self.move(self._clamp_window_pos(self._event_global_pos(event) - self._drag_offset))
            event.accept()
            return True
        except Exception:
            return False

    def _finish_window_drag(self, event, source: Optional[object] = None) -> bool:
        if not bool(getattr(self, "_dragging_window", False)):
            return False
        self._dragging_window = False
        if bool(getattr(self, "_panel_collapsed", False)) or bool(getattr(self, "_is_collapsed", False)):
            try:
                end_pos = self._event_global_pos(event)
                start_pos = getattr(self, "_drag_start_pos", end_pos)
                distance = (end_pos - start_pos).manhattanLength()
                restore_pos = self._collapsed_restore_btn.mapFromGlobal(end_pos)
                close_pos = self._collapsed_close_btn.mapFromGlobal(end_pos)
                clicked_restore = self._collapsed_restore_btn.rect().contains(restore_pos)
                clicked_close = self._collapsed_close_btn.rect().contains(close_pos)
                if distance < 5 and not clicked_restore and not clicked_close:
                    self._toggle_panel_collapse(False)
                elif distance >= 5:
                    self._snap_collapsed_to_edge()
            except Exception:
                pass
        event.accept()
        return True

    def _mark_text_selection_interaction(self, duration: float = 1.0) -> None:
        until = time.monotonic() + max(0.1, float(duration))
        self._text_selection_update_block_until = max(
            float(getattr(self, "_text_selection_update_block_until", 0.0) or 0.0),
            float(until),
        )
        try:
            from deepcat.ui.selection_translate import suppress_selection_reuse_for_text_input

            suppress_selection_reuse_for_text_input(float(duration))
        except Exception:
            pass

    def _editor_has_selection(self, editor: object) -> bool:
        try:
            if isinstance(editor, QTextEdit):
                cursor = editor.textCursor()
                return bool(cursor and cursor.hasSelection())
        except Exception:
            pass
        return False

    def _any_text_editor_has_selection(self) -> bool:
        return bool(self._editor_has_selection(self._editor))

    def _text_selection_update_blocked(self) -> bool:
        try:
            if self._any_text_editor_has_selection():
                return True
        except Exception:
            pass
        try:
            return time.monotonic() < float(getattr(self, "_text_selection_update_block_until", 0.0) or 0.0)
        except Exception:
            return False

    def _external_reuse_action_blocked(self) -> bool:
        try:
            return time.monotonic() < float(getattr(self, "_external_reuse_action_block_until", 0.0) or 0.0)
        except Exception:
            return False

    def _translation_content_height(self, editor_width: int) -> int:
        try:
            return self._bubble_view.content_height() + 30
        except Exception:
            return 0

    @staticmethod
    def _bounded_ocr_input_window_height(
        base_window_height: int,
        base_editor_height: int,
        content_editor_height: int,
        cap_height: int,
        available_height: int,
    ) -> int:
        return int(
            min(
                max(int(base_window_height), int(cap_height)),
                max(int(base_window_height), int(available_height)),
                max(
                    int(base_window_height),
                    int(base_window_height)
                    + max(0, int(content_editor_height) - int(base_editor_height)),
                ),
            )
        )

    def _ocr_input_content_height(self, editor_width: int) -> int:
        """按目标宽度测量纯文本高度，避免依赖尚未稳定的控件几何。"""

        try:
            document = self._editor.document().clone()
            document.setTextWidth(max(80, int(editor_width) - 4))
            document_height = int(document.documentLayout().documentSize().height() + 0.999)
            return max(int(self._COMPACT_EDITOR_MIN_HEIGHT), document_height + 4)
        except Exception:
            return int(self._COMPACT_EDITOR_MIN_HEIGHT)

    def _panel_layout_state(self) -> str:
        """返回 AI 窗口的稳定布局状态，避免内容高度反复参与外层几何计算。"""
        if bool(getattr(self, "_history_showing", False)):
            return "history"
        if (
            bool(getattr(self, "_is_chatting", False))
            or bool(getattr(self, "_chat_history", None))
            or bool(getattr(self, "_preserve_cleared_output_area", False))
        ):
            return "conversation"
        try:
            if self._bubble_view.isVisible() and not self._bubble_view.is_empty():
                return "conversation"
        except Exception:
            pass
        return "empty"

    def _stable_panel_metrics(self, geo: QRect, state: str, *, is_max: bool) -> dict:
        max_w = int(max(1, geo.width())) if is_max else int(max(260, geo.width() - 16))
        maximized_height = int(max(1, geo.height()))
        max_h = maximized_height if is_max else int(max(220, geo.height() - 16))

        if is_max:
            conversation_w = int(geo.width())
            h = maximized_height
            base_h = h
            limit_h = max_h
        else:
            conversation_w = int(min(max_w, min(760, max(360, max(620, int(geo.width() * 0.42)))))) + 16
            limit_h = int(min(max_h, max(220, int(geo.height() * 0.8))))
            conversation_target_h = int(min(max_h, max(720, int(geo.height() * 0.8))))
            if state == "conversation":
                base_h = conversation_target_h
            elif state == "history":
                base_h = conversation_target_h
            else:
                title_h = 0
                if getattr(self, "_title_bar_shell", None) is not None and self._title_bar_shell.isVisible():
                    title_h = int(self._title_bar.height() or self._title_bar.sizeHint().height() or 30)
                row_h = int(self._bottom_row_widget.height() or self._bottom_row_widget.sizeHint().height() or 44)
                margins_h = 0
                try:
                    layout = self._interaction_container.layout()
                    if layout is not None:
                        margins = layout.contentsMargins()
                        margins_h = int(margins.top() + margins.bottom())
                except Exception:
                    pass
                base_h = int(min(max_h, max(180, 120 + title_h + row_h + margins_h + 16)))
            h = base_h

        w = int(conversation_w)
        if state == "history":
            sidebar_w = self._history_sidebar_width()
            w = int(min(max_w, max(conversation_w, conversation_w + sidebar_w)))
            if not is_max:
                base_h = int(max(base_h, conversation_target_h))
                h = int(max(h, conversation_target_h))

        return {
            "w": int(w),
            "h": int(h),
            "base_h": int(base_h),
            "limit_h": int(limit_h),
            "max_w": int(max_w),
            "max_h": int(max_h),
            "conversation_w": int(conversation_w),
        }

    def _freeze_window_height(self) -> None:
        """冻结窗口高度：同时锁定最小与最大高度为当前值，从物理上阻止 Qt 因子控件
        可见性/内容变化而把窗口放大或缩小。仅靠 setMinimumHeight 挡不住"放大"，
        必须连 setMaximumHeight 一起钉死，才能彻底消除流式渲染期的"顶部固定、底端
        下推"非法膨胀，从而消除上下跳动闪烁。"""
        try:
            h = int(self.height())
            self.setMinimumHeight(h)
            self.setMaximumHeight(h)
        except Exception:
            pass

    def _thaw_window_height(self) -> None:
        """解冻窗口高度：释放最小/最大高度约束，恢复 Qt 原生自适应拉伸。"""
        try:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
        except Exception:
            pass

    def _reposition(
        self,
        keep_bottom_y: Optional[int] = None,
        restore_x: Optional[int] = None
    ) -> None:
        _trace_ai_panel(self, "reposition.begin", keep_bottom_arg=keep_bottom_y)
        # 强制防范：纯输入态隐藏气泡视图，保留输出区/历史态不受影响。
        if (
            not getattr(self, "_chat_history", None)
            and not bool(getattr(self, "_history_showing", False))
            and not bool(getattr(self, "_preserve_cleared_output_area", False))
        ):
            try:
                self._bubble_view.hide()
            except Exception:
                pass
        self._sync_title_bar_visibility()
        if bool(getattr(self, "_panel_collapsed", False)):
            _trace_ai_panel(self, "reposition.return_collapsed", keep_bottom_arg=keep_bottom_y)
            return
        if bool(getattr(self, "_suppress_reposition", False)):
            _trace_ai_panel(self, "reposition.return_suppressed", keep_bottom_arg=keep_bottom_y)
            return
        layout_state = self._panel_layout_state()
        base_h = None
        required_translation_h = None
        max_translation_h = None
        thinking_h = None
        k_bottom = None


        screen = QGuiApplication.screenAt(self._region.center()) or QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)

        is_max = bool(getattr(self, "_is_maximized", False))
        if is_max:
            self._sync_maximized_content_layout()
            self._update_top_seam_cover()
            self._update_interaction_seam_covers()
            _trace_ai_panel(self, "reposition.return_native_maximized", keep_bottom_arg=keep_bottom_y)
            return
        metrics = self._stable_panel_metrics(geo, layout_state, is_max=is_max)
        max_w = int(metrics["max_w"])
        max_h = int(metrics["max_h"])
        limit_h = int(metrics["limit_h"])
        w = int(metrics["w"])
        h = int(metrics["h"])
        base_h = int(metrics["base_h"])
        conversation_w = int(metrics["conversation_w"])

        ocr_input_cap = int(getattr(self, "_ocr_input_auto_height_cap", 0) or 0)
        if layout_state == "empty" and ocr_input_cap > 0:
            base_editor_h = self._compact_editor_fill_height(base_h)
            content_editor_h = self._ocr_input_content_height(max(160, conversation_w - 18))
            h = self._bounded_ocr_input_window_height(
                base_h,
                base_editor_h,
                content_editor_h,
                ocr_input_cap,
                max_h,
            )

        row_h = int(max(34, self._btn_translate.sizeHint().height()))
        if self._bubble_view.isVisible():
            margins_h = 20
            spacing_h = 16
            min_source_h = 120
            editor_w = int(max(160, conversation_w - 16 - 44))
            required_translation_h = self._translation_content_height(editor_w)
            if bool(getattr(self, "_history_forced_output_area", False)):
                required_translation_h = int(max(required_translation_h, 360))

            max_translation_h = int(max(120, h - 16 - margins_h - spacing_h - row_h - min_source_h))

            # 提取思考卡片的确定性状态高度，彻底消除 Qt layout 动荡状态的实时 height() 影响
            thinking_h = 0
            if hasattr(self, "_thinking_card") and self._thinking_card.isVisible():
                thinking_h = self._thinking_card.maximumHeight() or 68
                thinking_h += 8

            # 输出区分摊的计算：减去恒定的 thinking_h，确保高度绝对锁死不动
            max_translation_h = max(80, max_translation_h - thinking_h)
            if is_max:
                # 在最大化下，分发所有富余垂直空间给气泡列表
                self._bubble_view.setMinimumHeight(max_translation_h)
            else:
                self._bubble_view.setMinimumHeight(int(min(required_translation_h, max_translation_h)))
                self._bubble_view.setMaximumHeight(16777215)


            # 在气泡列表显示时恢复发送前的输入区高度，确保输出区扩展不会挤压初始化输入区。
            try:
                self._editor_container.setMinimumHeight(min_source_h)
                self._editor_container.setMaximumHeight(min_source_h)
            except Exception:
                pass
        else:
            self._bubble_view.setMinimumHeight(0)
            self._sync_compact_editor_height(h)
        self._activate_panel_layouts()

        # 关键时序：绝不能先 resize 再计算位置 —— resize 会立刻触发 resizeEvent，把
        # _last_stable_bottom_y 污染成「旧 y + 新高度」（底边被推下去 Δh），后续底边锚定全部失准。
        # 因此全部目标几何先基于变更前的 frameGeometry 计算完毕，最后一次性原子应用。
        pre_frame = self.frameGeometry()

        # 决定窗口左上角位置 pos
        if self.isVisible():
            if is_max:
                target_x = geo.x() + int((geo.width() - w) / 2)
                target_y = geo.y()
                pos = QPoint(target_x, target_y)
            else:
                # 保持在当前位置，使用 self.pos()
                k_bottom = keep_bottom_y if keep_bottom_y is not None else getattr(self, "_keep_bottom_y_on_next_reposition", None)
                history_anchor_x = None
                if (
                    k_bottom is None
                    and bool(getattr(self, "_history_transitioning", False))
                    and bool(getattr(self, "_history_showing", False))
                ):
                    # 展开历史记录时保持窗口左上角不动，只有 resize 没有 move，
                    # 这样初始化窗口在屏幕上的绝对位置始终不变，从根源消除 DWM 合成闪烁。
                    # 因此这里保持 k_bottom 为 None，使其在下方走 pos().y() 原地向下展开。
                    pass

                # 【黄金防抖设计升级】：如果在整个问答活跃期间，每次重定位时都强制保持底部不动，彻底锁死抖动
                if k_bottom is None and getattr(self, "_is_chatting", False):
                    # 内存级高精度逻辑坐标优先，如无缓存则回退至变更前的几何坐标
                    candidate_bottom = getattr(self, "_last_stable_bottom_y", None)
                    pre_bottom = pre_frame.y() + pre_frame.height()
                    # 防污染加固：流式渲染期间子控件可见性变化可能引发瞬时 resize，把"膨胀后的
                    # 底端"写进 _last_stable_bottom_y（比真实发送前底端大）。若两者差距过大（>5px），
                    # 视为污染值并丢弃，改用变更前的几何底端，避免窗口跳到错误位置/二次抖动。
                    if candidate_bottom is not None and abs(candidate_bottom - pre_bottom) > 5:
                        _trace_ai_panel(
                            self,
                            "reposition.dropped_polluted_lsb",
                            lsb=candidate_bottom,
                            pre_bottom=pre_bottom,
                            keep_bottom_arg=keep_bottom_y,
                        )
                        candidate_bottom = None
                    k_bottom = candidate_bottom
                    if k_bottom is None:
                        k_bottom = pre_bottom

                if k_bottom is not None:
                    # 向上展开的高度坐标
                    target_y = k_bottom - h
                    # 智能避让：如果上方空间不够 (即 target_y 小于屏幕可用工作区顶部 + 8px)
                    if target_y < geo.y() + 8:
                        # 自动往下寻找空间：把顶部贴着屏幕上方展开
                        target_y = geo.y() + 8
                    target_x = history_anchor_x if history_anchor_x is not None else (restore_x if restore_x is not None else self.pos().x())
                    pos = QPoint(target_x, target_y)
                    self._keep_bottom_y_on_next_reposition = None # 消费后即刻清空
                else:
                    target_x = restore_x if restore_x is not None else self.pos().x()
                    pos = QPoint(target_x, self.pos().y())
        else:
            # 首次打开，或者处于隐藏状态，优先使用上次记录的位置
            if _ocr_text_panel_class()._last_pos is not None:
                pos = _ocr_text_panel_class()._last_pos
            else:
                loaded_pos = self._load_last_pos_from_settings()
                if loaded_pos is not None:
                    _ocr_text_panel_class()._last_pos = loaded_pos
                    _ocr_text_panel_class()._last_pos_user_moved = True
                    pos = loaded_pos
                elif bool(getattr(self, "_center_on_first_show", False)):
                    pos = QPoint(
                        int(geo.x() + (geo.width() - w) / 2),
                        int(geo.y() + (geo.height() - h) / 2),
                    )
                else:
                    x = int(self._region.right() + 12)
                    y = int(self._region.top())
                    if x + w > geo.x() + geo.width():
                        x = int(self._region.left() - w - 12)

                    base_pos = self._clamp_window_pos(QPoint(int(x), int(y)), geo, width=w, height=base_h)
                    bottom_anchor = int(min(base_pos.y() + base_h, geo.y() + geo.height() - 8))
                    if self._bubble_view.isVisible() and h > base_h:
                        pos = QPoint(int(x), int(bottom_anchor - h))
                    else:
                        pos = QPoint(int(x), int(y))

        # 确保重新定位时，默认窗口完整保留在屏幕可用工作区内，绝对不跑到屏幕外
        safe_min_x = geo.x() + 8
        safe_min_y = geo.y() + 8
        safe_max_x = geo.x() + geo.width() - w - 8
        safe_max_y = geo.y() + geo.height() - h - 8

        if safe_max_x < safe_min_x: safe_max_x = safe_min_x
        if safe_max_y < safe_min_y: safe_max_y = safe_min_y

        if is_max:
            final_x = pos.x()
            final_y = pos.y()
        else:
            final_x = max(safe_min_x, min(safe_max_x, pos.x()))
            final_y = max(safe_min_y, min(safe_max_y, pos.y()))

        if (
            not self.isVisible()
            and bool(getattr(self, "_center_on_first_show", False))
            and _ocr_text_panel_class()._last_pos is None
        ):
            _ocr_text_panel_class()._last_pos = QPoint(int(final_x), int(final_y))
            _ocr_text_panel_class()._last_pos_user_moved = False

        target = QRect(int(final_x), int(final_y), int(w), int(h))
        animate = bool(
            self.isVisible()
            and not getattr(self, "_history_showing", False)
            and not getattr(self, "_history_transitioning", False)
            and not is_max
        )
        _trace_ai_panel(
            self,
            "reposition.computed",
            keep_bottom_arg=keep_bottom_y,
            consumed_keep_bottom=k_bottom,
            layout_state=layout_state,
            base_h=base_h,
            final_h=h,
            final_w=w,
            stable_limit_h=limit_h,
            stable_max_h=max_h,
            required_translation_h=required_translation_h,
            max_translation_h=max_translation_h,
            thinking_h=thinking_h,
            pre_frame=pre_frame,
            target=target,
            final_x=final_x,
            final_y=final_y,
            animate=animate,
            screen_available=geo,
        )
        _trace_ai_panel(self, "reposition.before_apply", target=target, animate=animate)
        self._apply_panel_geometry(target, animate=animate)
        _trace_ai_panel(self, "reposition.after_apply", target=target, animate=animate)
        self._update_top_seam_cover()
        self._update_interaction_seam_covers()
        _trace_ai_panel(self, "reposition.after_top_seam", target=target, animate=animate)
        # 若当前处于几何冻结期（流式渲染中），_reposition 在开头释放了窗口高度约束，
        # 这里必须把"锁到新几何"的高度重新钉死（同时最小与最大），否则 Qt 布局会在
        # 流式期间再次自动放大窗口，造成顶部固定、底端下推的闪烁。
        if bool(getattr(self, "_geometry_frozen", False)):
            self._freeze_window_height()

    def _apply_panel_geometry(self, target: QRect, animate: bool = True) -> None:
        """一次性原子应用窗口几何，精细分步 resize/move 以彻底消除 DWM 闪跃。"""
        current = self.geometry()

        # 仅在窗口当前可见，且窗口左上角物理位置发生变化（存在 move 动作）时启用透明度过滤
        need_opacity_transition = bool(
            self.isVisible()
            and (target.x() != current.x() or target.y() != current.y())
        )
        if need_opacity_transition:
            opacity_before = self.windowOpacity()
            self.setWindowOpacity(0.0)

        _trace_ai_panel(self, "apply_geometry.begin", current=current, target=target, animate=animate)
        anim = getattr(self, "_geometry_anim", None)
        if anim is not None:
            try:
                anim.stop()
            except Exception:
                pass
            self._geometry_anim = None
            _trace_ai_panel(self, "apply_geometry.stopped_animation", current=current, target=target, animate=animate)

        if target == current:
            _trace_ai_panel(self, "apply_geometry.noop", current=current, target=target, animate=animate)
            return

        self._repositioning = True
        try:
            self.setMinimumSize(0, 0)
            self.setMaximumHeight(16777215)

            # 只有需要动画化过渡且尺寸改变时，才执行双向分步调整算法；
            # 历史侧栏切换等显式关闭动画的路径必须一次性应用，避免可见跳闪。
            if self.isVisible() and bool(animate) and (target.height() != current.height() or target.width() != current.width()):
                if (target.height() > current.height() and target.y() < current.y()) or \
                   (target.width() > current.width() and target.x() < current.x()):
                    # 黄金双向平滑舒展算法：保持左上角 x 和 y 均为 current 坐标，
                    # 仅拉伸大宽度和大高度。这样能让富余尺寸在右侧和下方展开，避免位置瞬间闪变跳动。
                    temp_rect = QRect(current.x(), current.y(), target.width(), target.height())
                    _trace_ai_panel(self, "apply_geometry.before_set_expanding_resize", current=current, target=temp_rect)
                    self.setGeometry(temp_rect)
                    self.repaint()
                    _trace_ai_panel(self, "apply_geometry.before_set_expanding_move", current=temp_rect, target=target)
                    self.setGeometry(target)
                elif target.height() < current.height() and target.y() > current.y():
                    # 场景二：向底收缩（清除对话变矮）。
                    # 1. 临时将内部布局对齐到底部，防止 repaint 时由于大窗口高度未缩减导致组件分离拉伸产生大缝隙
                    layout = self._interaction_container.layout() if hasattr(self, "_interaction_container") else None
                    if layout is not None:
                        layout.setAlignment(Qt.AlignmentFlag.AlignBottom)

                    # 2. 同步 repaint() 擦除已隐藏气泡的旧像素
                    self.repaint()
                    _trace_ai_panel(self, "apply_geometry.before_set_collapsing_direct", current=current, target=target)

                    # 3. 执行 setGeometry 收缩窗口
                    self.setGeometry(target)

                    # 4. 恢复布局默认对齐方式，以支持后续正常自展
                    if layout is not None:
                        layout.setAlignment(Qt.AlignmentFlag(0))
                else:
                    _trace_ai_panel(self, "apply_geometry.before_set_direct", current=current, target=target)
                    self.setGeometry(target)
            else:
                _trace_ai_panel(self, "apply_geometry.before_set_direct_invisible", current=current, target=target)
                self.setGeometry(target)

            _trace_ai_panel(self, "apply_geometry.after_set", current=current, target=target, animate=animate)
        finally:
            self._repositioning = False

        if self.isVisible():
            _trace_ai_panel(self, "apply_geometry.before_repaint", current=current, target=target, animate=animate)
            self.repaint()
            _trace_ai_panel(self, "apply_geometry.after_repaint", current=current, target=target, animate=animate)

        self._last_stable_bottom_y = self.frameGeometry().y() + self.frameGeometry().height()

        # 【防 Qt 自动拉伸】setGeometry 设置完目标高度后，Qt 布局系统的
        # minimumSizeHint (763) > 目标高度 (759)，Qt 会优先 minimumHeight 强制拉伸窗口。
        # 同时钉死 min/max Height 从物理上阻止 Qt 拉伸；下次 _apply_panel_geometry 开头的
        # setMinimumSize(0,0) + setMaximumHeight(16777215) 会先解除约束再重新计算。
        if not bool(getattr(self, "_is_maximized", False)):
            try:
                self.setFixedHeight(target.height())
            except Exception:
                pass

        if need_opacity_transition:
            # 缩短延迟到 35ms，最大化降低空窗感，同时保证 DWM 完成平滑重组渲染
            def restore_opacity():
                try:
                    self.setWindowOpacity(opacity_before)
                except RuntimeError:
                    pass
            QTimer.singleShot(35, restore_opacity)

        _trace_ai_panel(
            self,
            "apply_geometry.end",
            current=current,
            target=target,
            animate=animate,
            last_stable_bottom_y=self._last_stable_bottom_y,
        )

    def _compact_editor_fill_height(self, window_height: Optional[int] = None) -> int:
        total_h = int(window_height if window_height is not None else self.height())
        try:
            margins = self._root_layout.contentsMargins()
            total_h -= int(margins.top() + margins.bottom())
        except Exception:
            total_h -= 16
        layout = self._interaction_container.layout() if hasattr(self, "_interaction_container") else None
        margins_h = 0
        if layout is not None:
            margins = layout.contentsMargins()
            margins_h = int(margins.top() + margins.bottom())
        title_h = 0
        if getattr(self, "_title_bar_shell", None) is not None and self._title_bar_shell.isVisible():
            title_h = int(self._title_bar.height() or self._title_bar.sizeHint().height() or 30)
        row_h = int(self._bottom_row_widget.height() or self._bottom_row_widget.sizeHint().height() or 44)
        return max(int(self._COMPACT_EDITOR_MIN_HEIGHT), int(total_h - margins_h - title_h - row_h))

    def _sync_compact_editor_height(self, window_height: Optional[int] = None) -> None:
        if bool(getattr(self, "_history_showing", False)) or bool(getattr(self, "_panel_collapsed", False)):
            return
        if self._panel_layout_state() in ("conversation", "history"):
            return
        try:
            self._editor_container.setFixedHeight(self._compact_editor_fill_height(window_height))
        except Exception:
            pass

    def _lock_editor_container_for_output_area(self) -> None:
        try:
            if hasattr(self._editor_container, "setFixedHeight"):
                self._editor_container.setFixedHeight(120)
            else:
                self._editor_container.setMinimumHeight(120)
                self._editor_container.setMaximumHeight(120)
        except Exception:
            pass
        try:
            bubble_h = int(getattr(self, "_cleared_output_restore_bubble_height", 0) or 300)
            self._cleared_output_restore_bubble_height = bubble_h
            self._bubble_view.setMinimumHeight(bubble_h)
            self._bubble_view.setMaximumHeight(16777215)
        except Exception:
            pass

    def _activate_panel_layouts(self) -> None:
        for layout in (
            self._interaction_container.layout() if hasattr(self, "_interaction_container") else None,
            self._content_stack.layout() if hasattr(self, "_content_stack") else None,
            self._view_stack.layout() if hasattr(self, "_view_stack") else None,
            self.layout(),
        ):
            if layout is None:
                continue
            try:
                layout.invalidate()
                layout.activate()
            except Exception:
                pass

    def _update_top_seam_cover(self) -> None:
        cover = getattr(self, "_top_seam_cover", None)
        if cover is None:
            return
        try:
            should_hide = (
                bool(getattr(self, "_panel_collapsed", False))
                or bool(getattr(self, "_history_showing", False))
                or not bool(self.isVisible())
                or getattr(self, "_content_body", None) is None
                or getattr(self, "_view_stack", None) is None
                or not bool(self._view_stack.isVisible())
                or getattr(self, "_bubble_view", None) is None
                or not bool(self._bubble_view.isVisible())
            )
            if should_hide:
                cover.hide()
                return
            body_geo = self._content_body.geometry()
            cover.setGeometry(
                int(body_geo.x()) + 1,
                int(body_geo.y()) - 1,
                max(0, int(body_geo.width()) - 2),
                10,
            )
            cover.show()
            cover.raise_()
        except Exception:
            try:
                cover.hide()
            except Exception:
                pass

    def _position_interaction_seam_cover(self, cover: Optional[QWidget], target: Optional[QWidget]) -> None:
        parent = getattr(self, "_interaction_container", None)
        if cover is None or parent is None or target is None:
            if cover is not None:
                try:
                    cover.hide()
                except Exception:
                    pass
            return
        try:
            if not bool(target.isVisible()) or not bool(parent.isVisible()):
                cover.hide()
                return
            top_left = target.mapTo(parent, QPoint(0, 0))
            seam_h = 4
            geometry = QRect(
                1,
                max(0, int(top_left.y()) - 2),
                max(0, int(parent.width()) - 2),
                seam_h,
            )
            old_geometry = QRect(cover.geometry())
            cover.setGeometry(geometry)
            cover.show()
            cover.raise_()
            try:
                update_rect = old_geometry.united(geometry).adjusted(0, -2, 0, 2)
                parent.update(update_rect)
                cover.update()
            except Exception:
                pass
        except Exception:
            try:
                cover.hide()
            except Exception:
                pass

    def _update_interaction_seam_covers(self, *, defer: bool = False) -> None:
        if bool(defer):
            QTimer.singleShot(0, lambda: _ocr_text_panel_class()._update_interaction_seam_covers(self, defer=False))
            return
        top_cover = getattr(self, "_interaction_top_seam_cover", None)
        editor_cover = getattr(self, "_editor_top_seam_cover", None)
        try:
            should_hide = (
                bool(getattr(self, "_panel_collapsed", False))
                or bool(getattr(self, "_history_showing", False))
                or not bool(self.isVisible())
                or getattr(self, "_interaction_container", None) is None
                or not bool(self._interaction_container.isVisible())
            )
            if should_hide:
                for cover in (top_cover, editor_cover):
                    if cover is not None:
                        cover.hide()
                return

            bubble_view = getattr(self, "_bubble_view", None)
            thinking_card = getattr(self, "_thinking_card", None)
            editor_container = getattr(self, "_editor_container", None)
            bubble_visible = bool(bubble_view is not None and bubble_view.isVisible())
            thinking_visible = bool(thinking_card is not None and thinking_card.isVisible())

            top_target = thinking_card if thinking_visible else editor_container
            if not bubble_visible and not thinking_visible:
                top_target = None
            _ocr_text_panel_class()._position_interaction_seam_cover(self, top_cover, top_target)

            editor_target = editor_container if thinking_visible else None
            _ocr_text_panel_class()._position_interaction_seam_cover(self, editor_cover, editor_target)
        except Exception:
            for cover in (top_cover, editor_cover):
                if cover is not None:
                    try:
                        cover.hide()
                    except Exception:
                        pass

    def _refresh_interaction_surface(self, *, defer: bool = False) -> None:
        """刷新回答区与思考卡片交界面，清除控件缩放后遗留的旧像素。"""
        if bool(defer):
            if bool(getattr(self, "_interaction_surface_refresh_pending", False)):
                return
            self._interaction_surface_refresh_pending = True

            def refresh_later() -> None:
                self._interaction_surface_refresh_pending = False
                _ocr_text_panel_class()._refresh_interaction_surface(self, defer=False)

            single_shot_scoped(0, self, refresh_later)
            return

        container = getattr(self, "_interaction_container", None)
        if container is None:
            return
        try:
            if not bool(container.isVisible()):
                return
        except Exception:
            pass

        try:
            container.update()
        except Exception:
            pass

        bubble_view = getattr(self, "_bubble_view", None)
        if bubble_view is not None:
            try:
                bubble_view.update()
            except Exception:
                pass
            try:
                viewport = bubble_view.viewport()
                if viewport is not None:
                    viewport.update()
            except Exception:
                pass

        thinking_card = getattr(self, "_thinking_card", None)
        if thinking_card is not None:
            try:
                thinking_card.update()
            except Exception:
                pass

        update_seams = getattr(self, "_update_interaction_seam_covers", None)
        if callable(update_seams):
            update_seams(defer=False)

    def _sync_bubble_view_after_geometry_change(
        self,
        *,
        keep_bottom: bool = True,
        scroll_anchor: Optional[dict] = None,
    ) -> None:
        try:
            if not self._bubble_view.isVisible():
                return
            refresh_for_resize = getattr(self._bubble_view, "refresh_for_viewport_resize", None)
            if callable(refresh_for_resize):
                anchor = dict(scroll_anchor or {})
                if not anchor:
                    capture_anchor = getattr(self._bubble_view, "capture_viewport_scroll_anchor", None)
                    if callable(capture_anchor):
                        anchor = dict(capture_anchor() or {})
                if bool(keep_bottom):
                    anchor = {"at_bottom": True, "distance_from_bottom": 0}
                refresh_for_resize(anchor)
                return
            self._bubble_view.refresh_layout(keep_bottom=bool(keep_bottom))
            if bool(keep_bottom):
                scroll_now = getattr(self._bubble_view, "scroll_to_bottom_now", None)
                if callable(scroll_now):
                    scroll_now()
                else:
                    self._bubble_view.scroll_to_bottom()
        except Exception:
            pass

    def toggle_maximize(self) -> None:
        try:
            self._bubble_view._block_refresh = True
        except Exception:
            pass

        is_maximized = bool(getattr(self, "_is_maximized", False))
        try:
            is_maximized = bool(is_maximized or self.isMaximized())
        except Exception:
            pass
        scroll_anchor = {"at_bottom": True, "distance_from_bottom": 0}
        try:
            capture_anchor = getattr(self._bubble_view, "capture_viewport_scroll_anchor", None)
            if callable(capture_anchor):
                scroll_anchor = dict(capture_anchor() or scroll_anchor)
        except Exception:
            pass
        suspend_panel_updates = bool(is_maximized)
        panel_updates_were_enabled = True
        if suspend_panel_updates:
            try:
                panel_updates_were_enabled = bool(self.updatesEnabled())
                if panel_updates_were_enabled:
                    self.setUpdatesEnabled(False)
            except Exception:
                panel_updates_were_enabled = False
        self._advance_window_mode_generation()

        if is_maximized:
            self._is_maximized = False
            self._pending_native_maximize_restore = False
            self._maximized_work_area_geometry = QRect()
            self._set_maximized_property(False)
            self._title_bar.btn_maximize.setIcon(OcrTitleBar._make_maximize_icon(self._title_bar._icon_size))
            self._title_bar.btn_maximize.setToolTip("最大化")
            target_geo = QRect()
            if (
                bool(getattr(self, "_preserve_cleared_output_area", False))
                and hasattr(self, "_cleared_output_restore_geometry")
                and self._cleared_output_restore_geometry.isValid()
            ):
                target_geo = QRect(self._cleared_output_restore_geometry)
            elif hasattr(self, "_pre_max_geometry") and self._pre_max_geometry.isValid():
                target_geo = QRect(self._pre_max_geometry)
            elif hasattr(self, "_initial_panel_geometry") and self._initial_panel_geometry.isValid():
                target_geo = QRect(self._initial_panel_geometry)

            self._release_panel_size_constraints(release_content=True)
            self._force_normal_window_state()

            if target_geo.isValid():
                if not getattr(self, "_chat_history", None) and not bool(getattr(self, "_history_showing", False)):
                    try:
                        self._bubble_view.hide()
                    except Exception:
                        pass

                if bool(getattr(self, "_preserve_cleared_output_area", False)):
                    try:
                        self._bubble_view.clear()
                        self._bubble_view.show()
                        self._bubble_view.setMinimumHeight(0)
                        self._bubble_view.setMaximumHeight(16777215)
                        self._lock_editor_container_for_output_area()
                    except Exception:
                        pass
                elif self._bubble_view.isVisible():
                    try:
                        pre_editor_h = int(getattr(self, "_pre_max_editor_height", 0) or 0)
                        self._editor_container.setFixedHeight(pre_editor_h if pre_editor_h > 0 else 120)
                        self._bubble_view.setMinimumHeight(0)
                        self._bubble_view.setMaximumHeight(16777215)
                    except Exception:
                        pass
                else:
                    try:
                        self._bubble_view.setMinimumHeight(0)
                        self._bubble_view.setMaximumHeight(16777215)
                    except Exception:
                        pass

                try:
                    self.setGeometry(target_geo)
                except Exception:
                    pass
                if not self._bubble_view.isVisible():
                    self._sync_compact_editor_height(target_geo.height())
                self._activate_panel_layouts()
                self._update_top_seam_cover()
                self._update_interaction_seam_covers(defer=True)
            else:
                self._reposition()
        else:
            pre_max_geometry = QRect(self.geometry())
            try:
                pre_max_editor_height = int(self._editor_container.height() or self._compact_editor_fill_height(self.height()))
            except Exception:
                pre_max_editor_height = 0
            try:
                pre_max_bubble_height = int(self._bubble_view.height() or 0)
            except Exception:
                pre_max_bubble_height = 0
            self._is_maximized = True
            self._pre_max_geometry = pre_max_geometry
            self._pre_max_editor_height = pre_max_editor_height
            self._pre_max_bubble_height = pre_max_bubble_height
            self._set_maximized_property(True)
            self._title_bar.btn_maximize.setIcon(OcrTitleBar._make_restore_icon(self._title_bar._icon_size))
            self._title_bar.btn_maximize.setToolTip("还原窗口")

            # 使用系统原生最大化，让 Windows 根据任务栏和当前显示器工作区决定客户区边界。
            self._pending_native_maximize_restore = True
            self._capture_maximized_work_area()
            self._force_native_maximized_window_state()
            self._schedule_native_maximized_state_confirmation()
            self._finish_native_maximize_layout(sync_bubbles=False)

        try:
            self._bubble_view._block_refresh = False
        except Exception:
            pass
        self._sync_bubble_view_after_geometry_change(
            keep_bottom=bool(scroll_anchor.get("at_bottom", True)),
            scroll_anchor=scroll_anchor,
        )
        if suspend_panel_updates and panel_updates_were_enabled:
            try:
                self.setUpdatesEnabled(True)
                self.update()
            except Exception:
                pass

    def _clamp_window_pos(
        self,
        pos: QPoint,
        geo: Optional[QRect] = None,
        *,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> QPoint:
        if geo is None:
            effective_w = int(width if width is not None else self.width())
            effective_h = int(height if height is not None else self.height())
            center = QPoint(int(pos.x() + effective_w / 2), int(pos.y() + effective_h / 2))
            screen = QGuiApplication.screenAt(center) or QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)
        effective_w = int(width if width is not None else self.width())
        effective_h = int(height if height is not None else self.height())

        # 允许窗口拖出屏幕外，但在屏幕内必须保留至少 40px 的可视与控制区域，防范完全拖出屏幕丢失
        visible_margin = 40
        min_x = int(geo.x() - effective_w + visible_margin)
        min_y = int(geo.y() - effective_h + visible_margin)
        max_x = int(geo.x() + geo.width() - visible_margin)
        max_y = int(geo.y() + geo.height() - visible_margin)

        if max_x < min_x:
            min_x = int(geo.x())
            max_x = int(geo.x())
        if max_y < min_y:
            min_y = int(geo.y())
            max_y = int(geo.y())
        return QPoint(max(min_x, min(max_x, int(pos.x()))), max(min_y, min(max_y, int(pos.y()))))
