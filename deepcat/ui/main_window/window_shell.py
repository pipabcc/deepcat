from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import sys
import threading
import time
import ctypes
import html
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote, urlparse
from PyQt6.QtCore import (
    Qt,
    QThread,
    QTimer,
    QRect,
    QSize,
    QByteArray,
    QEvent,
    QEventLoop,
    QPoint,
    QDate,
    QDateTime,
    QTime,
    pyqtSignal,
    QUrl,
    QObject,
    QFileInfo,
)
from PyQt6.QtGui import (
    QAction,
    QGuiApplication,
    QIcon,
    QColor,
    QCursor,
    QPainter,
    QPen,
    QImage,
    QKeySequence,
    QPixmap,
    QDesktopServices,
    QTextCharFormat,
    QTextCursor,
    QTextListFormat,
    QTextDocument,
    QFont,
    QBrush,
    QPalette,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QAbstractItemView,
    QButtonGroup,
    QCalendarWidget,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileIconProvider,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QMenu,
    QStyle,
    QFrame,
    QCheckBox,
    QLineEdit,
)
from deepcat import __version__
from deepcat.config import Config
from deepcat.input.hotkey_format import pynput_to_qt, qt_to_pynput
from deepcat.input.hotkey_listener import GlobalStartHotkey
from deepcat.settings_store import (
    AppSettings,
    DEFAULT_CAT_REMINDER,
    DEFAULT_EXPLAIN_PROMPT,
    DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
    DEFAULT_AI_SEARCH_PROMPT,
    DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
    DEFAULT_REPLY_PROMPT,
    DEFAULT_REPLY_PROMPT_BUTTON_NAME,
    DEFAULT_SUMMARY_PROMPT,
    DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
    COPY_ON_CAPTURE_MODE_COPY_CLOSE,
    COPY_ON_CAPTURE_MODE_COPY_KEEP,
    COPY_ON_CAPTURE_MODE_OFF,
    DEFAULT_COPY_ON_CAPTURE_MODE,
    decode_qbytearray,
    encode_qbytearray,
    coerce_auto_backup_interval_minutes,
    get_image_output_dir,
    get_pdf_output_dir,
    infer_translator_model_type,
    load_settings,
    normalize_cat_reminder_settings,
    normalize_later_read_settings,
    normalize_feature_visibility,
    normalize_copy_on_capture_mode,
    normalize_todo_items,
    normalize_annotation_style,
    normalize_log_settings,
    normalize_output_dir,
    normalize_translator_settings,
    normalize_updater_settings,
    update_settings,
    update_settings_fields,
    update_ui_settings,
    validate_settings_output_dirs_async,
    validate_output_dir,
)
from deepcat.ui.app_icon import create_app_icon
from deepcat.ui.cat_reminder import CatReminderSession
from deepcat.ui.countdown_overlay import CountdownOverlay
from deepcat.ui.floating_bar import FloatingBar
from deepcat.ui.network_probe import NetworkProbeMonitor
from deepcat.ui.notifications import CaptureNotificationState
from deepcat.ui.popup_behavior import POPUP_EXACT_WIDTH_PROPERTY
from deepcat.ui.settings_dialog import (
    ProviderSwitchHintDelegate,
    SettingsDialog,
    TranslatorConnectionTestWorker,
    ProxyConnectionTestWorker,
    UpdateCheckWorker,
    UpdateDownloadWorker,
)
from deepcat.ui.selection_border_overlay import SelectionBorderOverlay, SelectionShadeOverlay
from deepcat.ui.capture_worker import CaptureWorker
from deepcat.ui.region_overlay import RegionOverlay, SelectedRegion, logical_rect_to_physical_tuple
from deepcat.ui.post_capture_actions import PostCaptureActions, ModernPopupComboBox
from deepcat.ui.tab_list_popup import GroupedNoteListPopup, RoundedListPopup
from deepcat.ui.task_feedback import TaskFeedback
from deepcat.table_notes_store import TableNotesStore, default_ima_config
from deepcat.translation_history_store import TranslationHistoryStore
from deepcat.todo_store import TodoStore
from deepcat.later_read_store import LaterReadStore
from deepcat.drive_cleaner import now_iso
from deepcat.utils.autostart import is_autostart_enabled, set_autostart
from deepcat.utils.crash_reporter import write_crash_breadcrumb
from deepcat.utils.logger import get_log_dir, get_log_file_path, get_logger
from deepcat.utils.paths import get_app_dir

from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND, MAIN_WINDOW_BACKGROUND_COLORREF, MODULE_BACKGROUND, _RESOURCE_SHORTCUT_MAX_ITEMS
from deepcat.ui.main_window.helpers import (
    _date_key,
    _normalize_resource_shortcut_kind,
    _normalize_resource_shortcut_target,
    _normalize_resource_shortcuts,
    _resource_shortcut_default_title,
)
from deepcat.ui.main_window.misc_widgets import (
    RoundedTextEditContainer,
    _DoubleClickLabel,
    _DraggableTile,
    _ModernComboStyle,
    _MouseClickOnlyComboBox,
    _SidebarHoverFilter,
    _SidebarNavButton,
    _SwitchCheckBox,
)
from deepcat.ui.main_window.todo import _TodoCalendarWidget, _TodoEditDialog, _TodoListItemWidget, _TodoReminderPopup, _style_todo_combo_popup_view
from deepcat.ui.main_window.later_read import _LaterReadEditDialog, _LaterReadItemWidget, _LaterReadPinnedFoldToggleWidget
from deepcat.ui.main_window.notifications import NotificationPopup, _CatRestReminderPopup
from deepcat.ui.main_window.tab_password import _TabPasswordSetDialog, _TabPasswordVerifyDialog
from deepcat.ui.main_window.ima import _ImaSettingsDialog, _ImaSyncWorker
from deepcat.ui.main_window.notes import ImagePreviewDialog, ScrollResultPrepareWorker, _NotesEditor, _NotesTable, _ReturnDownDelegate
from deepcat.ui.main_window.drive_cleaner import _DriveCleanerWindow
from deepcat.ui.main_window.resource_shortcuts import _DeleteShortcutPopup, _ResourceShortcutDialog, _TodoResourceWindow
from deepcat.ui.main_window.stashed_captures import _StashedCapturesDialog
from deepcat.ui.main_window.compact import _CompactListWindow, _GroupManageDialog, StyledDialog, StyledInputDialog


# --- Mixin Imports for Physical Splitting ---
from deepcat.ui.main_window.capture import CaptureMixin
from deepcat.ui.main_window.tray import TrayMixin
from deepcat.ui.main_window.translator import TranslatorMixin
from deepcat.ui.main_window.todo import TodoMixin
from deepcat.ui.main_window.later_read import LaterReadMixin
from deepcat.ui.main_window.notes import NotesMixin
from deepcat.ui.main_window.notifications import CatReminderMixin
from deepcat.ui.main_window.resource_shortcuts import ResourceShortcutsMixin


from deepcat.ui.module_compat import DynamicModuleAttribute

QApplication = DynamicModuleAttribute("deepcat.ui.main_window.window", "QApplication")
QCursor = DynamicModuleAttribute("deepcat.ui.main_window.window", "QCursor")
QTimer = DynamicModuleAttribute("deepcat.ui.main_window.window", "QTimer")


class WindowShellMixin:
    def _prime_startup_background(self) -> None:
        try:
            background = QColor(MAIN_WINDOW_BACKGROUND)
            palette = self.palette()
            palette.setColor(QPalette.ColorRole.Window, background)
            palette.setColor(QPalette.ColorRole.Base, background)
            self.setPalette(palette)
            self.setAutoFillBackground(True)
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        except Exception:
            pass

    def _repaint_all_on_restore(self) -> None:
        try:
            sidebar = getattr(self, "_sidebar", None)
            if sidebar is not None:
                sidebar.repaint()
            stack = getattr(self, "_stack", None)
            if stack is not None:
                stack.repaint()
            title_line = getattr(self, "_title_line", None)
            if title_line is not None:
                title_line.repaint()
            # 同步让当前子页面及内部的滚动区域、视口与卡片也执行一次强力 repaint 重绘，消灭所有黑边残留
            try:
                current_page = self._stack.currentWidget() if self._stack is not None else None
                if current_page is not None:
                    current_page.repaint()
                    from PyQt6.QtWidgets import QScrollArea, QGroupBox
                    for area in current_page.findChildren(QScrollArea):
                        area.repaint()
                        if area.viewport() is not None:
                            area.viewport().repaint()
                        if area.widget() is not None:
                            area.widget().repaint()
                    for group in current_page.findChildren(QGroupBox):
                        group.repaint()
            except Exception:
                pass
            self.repaint()
        except Exception:
            pass

    def _hide_all_smooth_tooltips(self) -> None:
        try:
            app = QApplication.instance()
            if app is None:
                return
            for widget in app.topLevelWidgets():
                if type(widget).__name__ == "SmoothToolTip" and widget.isVisible():
                    widget.hide()
        except Exception:
            pass

    def _prepare_startup_show(self) -> None:
        """在首个可见帧之前准备窗口背景和原生窗口状态。"""
        self._prime_startup_background()
        try:
            self.ensurePolished()
        except Exception:
            pass
        try:
            self._apply_solid_window_backgrounds()
        except Exception:
            pass
        try:
            self._stabilize_window_backing_store()
        except Exception:
            pass
        try:
            int(self.winId())
        except Exception:
            pass
        try:
            self._apply_caption_color()
            self._set_win32_background_brush()
        except Exception:
            pass
        try:
            self._set_dwm_transitions_enabled(False)
        except Exception:
            pass
        # 启动期隐藏优先用 DWM cloak（合成器级隐藏）：uncloak 是原子操作，不改变窗口样式，
        # 不触发 WM_ERASEBKGND，因此不存在"类画刷先擦出一片纯色、内容后到"的白屏间隙。
        # 旧的 setWindowOpacity(0.0) 方案在恢复为 1.0 时会移除 WS_EX_LAYERED 样式，Windows
        # 会用类背景画刷（#f4faff 近白）重擦客户区再等 Qt 重绘——冷启动时消息队列拥挤，
        # 这个间隙被拉长为肉眼可见的"先白屏后主界面"。cloak 不可用时才退回 opacity 方案。
        self._startup_cloak_active = False
        try:
            self._startup_cloak_active = self._set_dwm_cloak(True)
        except Exception:
            self._startup_cloak_active = False
        if not self._startup_cloak_active:
            try:
                self.setWindowOpacity(0.0)
            except Exception:
                pass

    def _set_dwm_cloak(self, cloaked: bool) -> bool:
        """通过 DWMWA_CLOAK 在合成器层面隐藏/显示窗口，返回是否设置成功。"""
        if os.name != "nt":
            return False
        try:
            dwmapi = ctypes.windll.dwmapi
            hwnd = int(self.winId())
            DWMWA_CLOAK = 13
            value = ctypes.c_int(1 if cloaked else 0)
            result = dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CLOAK),
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
            return int(result) == 0
        except Exception:
            return False

    def _reveal_startup_window(self) -> None:
        """首帧内容就绪后原子揭示窗口，保证内容与窗口同帧出现。"""
        # 先把揭示前可能积累的 dirty 区域同步补画，确保呈现瞬间 backing store 完整
        try:
            self.repaint()
        except Exception:
            pass
        if bool(getattr(self, "_startup_cloak_active", False)):
            self._startup_cloak_active = False
            if not self._set_dwm_cloak(False):
                # 极端情况下 uncloak 失败：重试一次，仍失败则记录以便诊断
                if not self._set_dwm_cloak(False):
                    write_crash_breadcrumb("MainWindow.reveal_startup.uncloak_failed")
        else:
            try:
                self.setWindowOpacity(1.0)
            except Exception:
                pass
            # 立即同步 blit，最小化移除 WS_EX_LAYERED 后类画刷擦除到内容重绘之间的白屏间隙
            try:
                self.repaint()
            except Exception:
                pass
        QTimer.singleShot(80, self._restore_transition_after_show)

    def _start_deferred_startup_services(self) -> None:
        if bool(getattr(self, "_startup_services_started", False)):
            return
        write_crash_breadcrumb("MainWindow.deferred_startup.start")
        self._startup_services_started = True
        self._sync_autostart_if_enabled()
        self._ensure_tray()
        self._start_global_hotkeys()
        self._apply_selection_translate_listener()
        self._schedule_cat_reminder()
        self._schedule_todo_checks()
        self._refresh_tray_tooltip()
        QTimer.singleShot(5000, self._start_capture_warmup)
        QTimer.singleShot(5000, self._auto_check_for_updates_if_due)
        write_crash_breadcrumb("MainWindow.deferred_startup.done", tray=bool(self._tray is not None))

    def _sync_autostart_if_enabled(self) -> None:
        # 成品测试使用临时副本，不能把正式开机启动项改到测试目录。
        if os.environ.get("DEEPCAT_SKIP_AUTOSTART_SYNC") == "1":
            return
        try:
            if not (bool(getattr(self._app_settings, "autostart", False)) or bool(is_autostart_enabled())):
                return
            from deepcat.utils.autostart import set_autostart

            err = set_autostart(True)
            if err:
                get_logger().error("同步开机启动失败：%s", err)
                self._show_general_status(f"开机自启校正失败：{err}", tone="error", auto_hide_ms=5200)
        except Exception as exc:
            get_logger().exception("同步开机启动失败")
            self._show_general_status(f"开机自启校正失败：{exc}", tone="error", auto_hide_ms=5200)

    def _replace_global_hotkey(
        self, listener_attribute: str, key_attribute: str, callback, new_hotkey: str
    ) -> tuple[bool, str]:
        previous = getattr(self, listener_attribute, None)
        if previous is not None and str(new_hotkey) == str(getattr(self, key_attribute, "")):
            return True, ""
        replacement = GlobalStartHotkey(callback, hotkey=str(new_hotkey))
        try:
            replacement.start()
        except Exception as error:
            replacement.cleanup()
            return False, str(error)
        setattr(self, listener_attribute, replacement)
        setattr(self, key_attribute, str(new_hotkey))
        if previous is not None:
            previous.cleanup()
        return True, ""

    def _start_global_hotkeys(self) -> None:
        bindings = (
            ("_start_hotkey", self._start_hotkey_str, self._start_hotkey_triggered, "框选截图"),
            ("_scroll_hotkey", self._scroll_hotkey_str, self._scroll_hotkey_triggered, "滚动截图"),
            ("_todo_hotkey", "<alt>+n", self._todo_hotkey_signal.emit, "新建待办"),
            ("_later_read_hotkey", self._later_read_hotkey_str, self._later_read_hotkey_signal.emit, "稍后阅读"),
            ("_ai_qa_hotkey", self._ai_qa_hotkey_str, self._ai_qa_hotkey_signal.emit, "智能问答"),
            ("_pinned_hotkey", "<alt>+t", self._pinned_hotkey_signal.emit, "显示贴图"),
            ("_note_float_hotkey", "<alt>+j", self._note_float_hotkey_signal.emit, "记事浮窗"),
            ("_clipboard_float_hotkey", "<alt>+f", self._clipboard_float_hotkey_signal.emit, "复制记录浮窗"),
            ("_later_read_float_hotkey", "<alt>+s", self._later_read_float_hotkey_signal.emit, "稍后阅读浮窗"),
        )
        failures = []
        for attribute, hotkey, callback, label in bindings:
            if getattr(self, attribute, None) is not None:
                continue
            listener = GlobalStartHotkey(callback, hotkey=str(hotkey))
            try:
                listener.start()
                setattr(self, attribute, listener)
            except Exception as error:
                listener.cleanup()
                failures.append(f"{label}：{error}")
                get_logger().warning("%s 快捷键 %s 启用失败：%s", label, hotkey, error)
        if failures:
            self._show_general_status("部分快捷键未启用。" + "；".join(failures), tone="error", auto_hide_ms=8000)

    def _handle_escape_close_targets(self) -> bool:
        panel = self._selection_translate_panel
        try:
            if panel is not None and panel.isVisible():
                panel.close()
                return True
        except RuntimeError:
            self._selection_translate_panel = None
        except Exception:
            pass

        popup = self._selection_translate_action_popup
        try:
            if popup is not None and popup.isVisible():
                popup.close()
                return True
        except RuntimeError:
            self._selection_translate_action_popup = None
        except Exception:
            pass

        popup = getattr(self, "_selection_hover_translation_popup", None)
        try:
            if popup is not None and popup.isVisible():
                popup.close()
                return True
        except RuntimeError:
            self._selection_hover_translation_popup = None
        except Exception:
            pass

        post_actions = self._post_actions
        if post_actions is not None:
            try:
                if post_actions.close_ocr_panel_if_visible():
                    return True
            except Exception:
                pass
            try:
                if not bool(getattr(post_actions, "_recording", False)) and self._cancel_current_region_capture():
                    return True
            except Exception:
                pass
            try:
                if post_actions.isVisible() or self._border_overlay is not None:
                    post_actions.handle_escape()
                    return True
            except RuntimeError:
                self._post_actions = None
            except Exception:
                pass

        if self._border_overlay is not None:
            if self._cancel_current_region_capture():
                return True
        if self._post_actions is not None:
            try:
                self._post_actions.close_all()
            except Exception:
                try:
                    self._post_actions.close()
                except Exception:
                    pass
            self._post_actions = None
        self._finalize_stitch_buffer()
        self._restore_normal_cursor()
        return False

    def _hide_overlay_visual_safe(self, overlay: QWidget) -> None:
        try:
            overlay.set_selection_visual_visible(False)  # type: ignore[attr-defined]
            overlay.repaint()
        except Exception:
            pass

    def _camera_icon(self) -> QIcon:
        return create_app_icon()

    def _draw_network_status_bar(self, pixmap: QPixmap, status: object) -> QPixmap:
        canvas = QPixmap(pixmap)
        width = max(1, canvas.width())
        height = max(1, canvas.height())
        margin = max(1, int(round(min(width, height) * 0.06)))
        bar_width = min(width - margin * 2, max(8, int(round(width * 0.68))))
        bar_height = max(3, int(round(height * 0.14)))
        x = max(margin, (width - bar_width) // 2)
        y = max(0, height - bar_height - margin)
        radius = max(1, bar_height // 2)

        painter = QPainter(canvas)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if status is True or str(status) == "healthy":
                color = QColor("#16a34a")
            else:
                color = QColor("#dc2626")
            painter.setPen(Qt.PenStyle.NoPen)
            backing_color = QColor("#ffffff")
            backing_color.setAlpha(230)
            painter.setBrush(QBrush(backing_color))
            painter.drawRoundedRect(
                QRect(
                    max(0, x - margin),
                    max(0, y - margin),
                    min(width, bar_width + margin * 2),
                    min(height, bar_height + margin * 2),
                ),
                radius + margin,
                radius + margin,
            )
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(QRect(x, y, bar_width, bar_height), radius, radius)
        finally:
            painter.end()
        return canvas

    def _apply_caption_color(self) -> None:
        try:
            if os.name != "nt":
                return
            dwmapi = ctypes.windll.dwmapi
            hwnd = int(self.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
        except Exception:
            pass

    def _set_win32_background_brush(self) -> None:
        """通过 Win32 API 设置窗口类的背景画刷为主界面底色。

        深层原因：DWM 在执行最小化还原动画时完全不会通知 Qt 重绘（paintEvent 不会被调用）。
        DWM 用窗口类注册时的背景画刷（HBRBACKGROUND）填充新增的客户区像素。
        Qt 注册窗口类时默认画刷为 NULL，Windows 遇到 NULL 画刷就用黑色填充，
        这就是底部闪出黑边的根本原因。将画刷设置为面板底色后，DWM 动画期间
        新增区域会被正确颜色填充，从根源消除黑边闪烁。
        """
        try:
            if os.name != "nt":
                return
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            hwnd = int(self.winId())
            brush = gdi32.CreateSolidBrush(MAIN_WINDOW_BACKGROUND_COLORREF)
            if brush:
                GCLP_HBRBACKGROUND = -10
                if ctypes.sizeof(ctypes.c_void_p) == 8:
                    user32.SetClassLongPtrW(hwnd, GCLP_HBRBACKGROUND, brush)
                else:
                    user32.SetClassLongW(hwnd, GCLP_HBRBACKGROUND, brush)
        except Exception:
            pass

    def _suppress_restore_transition(self) -> None:
        """最小化动画完成后禁用 DWM 还原过渡动画。

        深层原因：在非整数 DPI 缩放（如 108%）下，Windows WINDOWPLACEMENT 中
        保存的物理像素大小与 Qt setFixedSize 的逻辑像素经 DPI 换算后存在舍入差异。
        DWM 还原动画的多帧渐变会暴露这种差异——先以稍大的尺寸显示（底部出现黑色
        未渲染区域），然后 Qt 的 fixedSize 约束触发二次调整，窗口缩回，产生向上抖动。
        禁用 DWM 还原过渡后，窗口瞬间以正确尺寸出现，从根源消除闪烁和抖动。
        """
        if not self.isMinimized():
            return
        self._set_dwm_transitions_enabled(False)
        self.setWindowOpacity(0.0)

    def _restore_transition_after_show(self) -> None:
        """还原完成后重新启用 DWM 过渡动画，保留后续最小化时的正常动画体验。"""
        if self.isMinimized():
            return
        self._set_dwm_transitions_enabled(True)

    def _set_dwm_transitions_enabled(self, enabled: bool) -> None:
        """通过 DwmSetWindowAttribute 控制 DWM 窗口过渡动画的启用/禁用。"""
        try:
            if os.name != "nt":
                return
            dwmapi = ctypes.windll.dwmapi
            hwnd = int(self.winId())
            DWMWA_TRANSITIONS_FORCEDISABLED = 3
            # 1 = 禁用过渡动画, 0 = 启用过渡动画
            value = ctypes.c_int(0 if enabled else 1)
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_TRANSITIONS_FORCEDISABLED),
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except Exception:
            pass

    def cleanup(self) -> None:
        write_crash_breadcrumb(
            "MainWindow.cleanup.start",
            tray=bool(self._tray is not None),
            tray_menu=bool(self._tray_menu is not None),
            thread=bool(self._thread is not None),
            post_actions=bool(self._post_actions is not None),
        )
        self._stop_right_click_cancel_listener()
        self._stop_left_click_retake_listener()
        try:
            self._cat_reminder_timer.stop()
        except Exception:
            pass
        try:
            self._todo_timer.stop()
        except Exception:
            pass
        try:
            if self._cat_reminder_session is not None:
                self._cat_reminder_session.close()
        except Exception:
            pass
        self._cat_reminder_session = None
        try:
            if self._todo_popup is not None:
                self._todo_popup.close()
        except Exception:
            pass
        self._todo_popup = None
        try:
            if self._selection_translate_listener is not None:
                self._selection_translate_listener.cleanup()
        except Exception:
            pass
        self._selection_translate_listener = None
        try:
            if self._selection_translate_panel is not None:
                self._selection_translate_panel.close()
        except Exception:
            pass
        self._selection_translate_panel = None
        try:
            if self._selection_translate_action_popup is not None:
                self._selection_translate_action_popup.close()
        except Exception:
            pass
        self._selection_translate_action_popup = None
        try:
            if self._selection_hover_translation_popup is not None:
                self._selection_hover_translation_popup.close()
        except Exception:
            pass
        self._selection_hover_translation_popup = None
        try:
            app = QApplication.instance()
            if app is not None and bool(getattr(self, "_app_event_filter_installed", False)):
                app.removeEventFilter(self)
                self._app_event_filter_installed = False
        except Exception:
            pass
        try:
            if self._post_capture_ui_warm_widget is not None:
                self._post_capture_ui_warm_widget.close()
        except Exception:
            pass
        self._post_capture_ui_warm_widget = None
        try:
            if self._later_read_probe_overlay is not None:
                self._later_read_probe_overlay.close()
        except Exception:
            pass
        self._later_read_probe_overlay = None
        try:
            if self._network_probe_monitor is not None:
                self._network_probe_monitor.shutdown(wait_ms=2500)
                self._network_probe_monitor.deleteLater()
        except Exception:
            pass
        self._network_probe_monitor = None
        self._network_probe_notification_failure_count = 0
        self._network_probe_last_alert_monotonic = 0.0
        tray = self._tray
        self._tray = None
        self._tray_pinned_action = None
        self._tray_network_probe_action = None
        self._tray_later_read_action = None
        self._tray_new_todo_action = None
        self._tray_menu = None
        try:
            if tray is not None:
                write_crash_breadcrumb("MainWindow.cleanup.tray.start")
                tray.setVisible(False)
                tray.hide()
                tray.deleteLater()
                write_crash_breadcrumb("MainWindow.cleanup.tray.done")
        except Exception:
            get_logger().exception("System tray cleanup failed")
            write_crash_breadcrumb("MainWindow.cleanup.tray.failed")
            pass
        # 安全清理哑窗口 dummy widget 资源
        try:
            dummy = getattr(self, "_tray_dummy_widget", None)
            self._tray_dummy_widget = None
            if dummy is not None:
                dummy.deleteLater()
        except Exception:
            pass
        if self._start_hotkey is not None:
            self._start_hotkey.cleanup()
        if self._scroll_hotkey is not None:
            self._scroll_hotkey.cleanup()
        if self._todo_hotkey is not None:
            self._todo_hotkey.cleanup()
        if self._later_read_hotkey is not None:
            self._later_read_hotkey.cleanup()
        if self._ai_qa_hotkey is not None:
            self._ai_qa_hotkey.cleanup()
        if self._pinned_hotkey is not None:
            self._pinned_hotkey.cleanup()
        if self._note_float_hotkey is not None:
            try:
                self._note_float_hotkey.cleanup()
            except Exception:
                pass
        if self._clipboard_float_hotkey is not None:
            try:
                self._clipboard_float_hotkey.cleanup()
            except Exception:
                pass
        if self._later_read_float_hotkey is not None:
            try:
                self._later_read_float_hotkey.cleanup()
            except Exception:
                pass
        try:
            if hasattr(self, "_clipboard_history_page") and self._clipboard_history_page is not None:
                self._clipboard_history_page.cleanup()
        except Exception:
            pass
        for page_type in ("clipboard", "later_read"):
            prop_name = f"_compact_window_{page_type}"
            compact_win = getattr(self, prop_name, None)
            if compact_win is not None:
                try:
                    compact_win.close()
                except Exception:
                    pass
                setattr(self, prop_name, None)
        try:
            from deepcat.translation_server import stop_background_translation_server

            stop_background_translation_server()
        except Exception:
            pass
        try:
            from deepcat.ocr_worker_client import shutdown_ocr_worker

            shutdown_ocr_worker()
        except Exception:
            pass
        write_crash_breadcrumb("MainWindow.cleanup.done")

    def _start_hotkey_triggered(self) -> None:
        self._capture_requested.emit(True)

    @staticmethod




















    def _open_log_dir(self) -> None:
        try:
            path = str(get_log_dir())
            os.makedirs(path, exist_ok=True)
            os.startfile(path)
        except Exception as e:
            QMessageBox.warning(self, "无法打开日志目录", str(e))

    def _open_feedback_url(self) -> None:
        if not QDesktopServices.openUrl(QUrl("https://github.com/pipabcc/deepcat/issues/new")):
            self._show_about_status("无法打开反馈/问题上报页面。", tone="error", auto_hide_ms=4200)

    def _open_files_dir(self) -> None:
        try:
            path = str(get_image_output_dir())
            os.startfile(path)
        except Exception as e:
            QMessageBox.warning(self, "无法打开文件目录", str(e))

    def _open_settings(self) -> None:
        if self._is_tray_menu_click_throttled():
            return
        self._switch_page(0)
        self._sync_embedded_settings_controls()
        self._show_from_tray()
        try:
            self.activateWindow()
        except Exception:
            pass

    def _selection_text_panel_visible(self) -> bool:
        panel = self._selection_translate_panel
        try:
            return bool(panel is not None and panel.isVisible())
        except RuntimeError:
            self._selection_translate_panel = None
            return False
        except Exception:
            return False

    def _selection_popup_should_be_blocked(self) -> bool:
        # 只在截图 OCR 面板可见时阻塞划词；划词面板可见时保持绿色放行，以便支持快捷键及无缝复用更新
        return bool(self._ocr_text_panel_visible())

    def _refresh_selection_popup_blocked(self) -> None:
        blocked = bool(self._selection_popup_should_be_blocked())
        self._selection_popup_blocked = bool(blocked)
        listener = self._selection_translate_listener
        try:
            if listener is not None:
                listener.set_selection_popup_blocked(bool(blocked))
        except Exception:
            pass
        if bool(blocked):
            self._selection_translate_popup_pending_action = ""
            try:
                if self._selection_translate_action_popup is not None:
                    self._selection_translate_action_popup.close()
            except Exception:
                pass
            self._selection_translate_action_popup = None
            try:
                if self._selection_hover_translation_popup is not None:
                    self._selection_hover_translation_popup.close()
            except Exception:
                pass
            self._selection_hover_translation_popup = None

    def _on_selection_popup_requested(self, x: int, y: int) -> None:
        if self._selection_popup_should_be_blocked():
            self._refresh_selection_popup_blocked()
            return
        self._show_selection_action_popup("", int(x), int(y))

    def _on_selection_lookup_failed(self, x: int, y: int, message: str) -> None:
        self._selection_translate_popup_pending_action = ""
        try:
            if self._selection_translate_action_popup is not None:
                self._selection_translate_action_popup.close()
        except Exception:
            pass

    def _on_selection_cancelled(self, force: bool = False) -> None:
        """划词选中被取消时，立即关闭浮窗"""
        try:
            if self._selection_translate_action_popup is not None and self._selection_translate_action_popup.isVisible():
                popup = self._selection_translate_action_popup
                # 若不是强制关闭，且点击位置在浮窗内部（如点击浮窗按钮），则不关闭
                if not force:
                    from PyQt6.QtGui import QCursor
                    if popup.geometry().contains(QCursor.pos()):
                        return
                popup.close()
        except Exception:
            pass

    def _show_selection_action_popup(self, text: str, x: int, y: int) -> None:
        if self._selection_popup_should_be_blocked():
            self._refresh_selection_popup_blocked()
            return
        try:
            if self._selection_translate_action_popup is not None:
                self._selection_translate_action_popup.close()
        except Exception:
            pass
        self._selection_translate_action_popup = None
        try:
            if self._selection_hover_translation_popup is not None:
                self._selection_hover_translation_popup.close()
        except Exception:
            pass
        self._selection_hover_translation_popup = None
        try:
            from deepcat.ui.post_capture_actions import SelectionActionPopup

            popup = SelectionActionPopup(text=text)
            self._selection_translate_action_popup = popup
            self._selection_translate_popup_text = str(text or "")
            self._selection_translate_popup_pos = (int(x), int(y))
            self._selection_translate_popup_pending_action = ""
            owner = self
            popup.destroyed.connect(lambda *_, owner=owner: setattr(owner, "_selection_translate_action_popup", None))
            popup.actionSelected.connect(self._on_selection_popup_action_selected)
            popup.set_ready(True)
            popup.show_at(int(x), int(y))
        except Exception as e:
            get_logger().warning("显示划词功能按钮失败: %s", e)

    def _set_selection_action_popup_text(self, text: str) -> None:
        self._selection_translate_popup_text = str(text or "").strip()
        popup = self._selection_translate_action_popup
        try:
            if popup is not None:
                if self._selection_translate_popup_text:
                    pending = str(getattr(self, "_selection_translate_popup_pending_action", "") or "")
                    if pending:
                        self._selection_translate_popup_pending_action = ""
                        self._on_selection_popup_action_selected(pending)
                        return
                    popup.set_ready(True)
                else:
                    popup.close()
        except Exception:
            pass

    def _on_selection_popup_action_selected(self, action: str) -> None:
        action_name = str(action or "")
        text = str(getattr(self, "_selection_translate_popup_text", "") or "").strip()
        if not text:
            self._selection_translate_popup_pending_action = action_name
            return

        if action_name == "hover_translate":
            pos = getattr(self, "_selection_translate_popup_pos", None)
            if isinstance(pos, tuple) and len(pos) == 2:
                x, y = int(pos[0]), int(pos[1])
            else:
                x, y = QCursor.pos().x(), QCursor.pos().y()
            anchor_rect = QRect()
            try:
                if self._selection_translate_action_popup is not None:
                    anchor_rect = QRect(self._selection_translate_action_popup.geometry())
                    self._selection_translate_action_popup.close()
            except Exception:
                pass
            self._selection_translate_action_popup = None
            self._open_selection_hover_translation_popup(text, x, y, anchor_rect)
            return

        # 快速处理划词复制逻辑，将划词文本写入系统剪切板，弹出通知并自我销毁
        if action_name == "copy":
            try:
                QApplication.clipboard().setText(text)
                self._send_tray_notification("已复制到剪贴板", "划词内容已成功复制", 1500)
            except Exception:
                pass
            try:
                if self._selection_translate_action_popup is not None:
                    self._selection_translate_action_popup.close()
            except Exception:
                pass
            return

        if action_name == "add_to_note":
            try:
                if self._selection_translate_action_popup is not None:
                    self._selection_translate_action_popup.close()
            except Exception:
                pass
            try:
                import datetime
                from deepcat.table_notes_store import TableNotesStore
                from deepcat.ui.markdown_renderer import MarkdownRenderer
                from deepcat.ui.post_capture_actions import GroupedSmoothNoteIntegrationDialog
                from PyQt6.QtWidgets import QMessageBox

                store = TableNotesStore()
                tabs, _ = store.load_note_tabs()

                default_name = f"划词记事_{datetime.datetime.now().strftime('%m%d_%H%M')}"

                action, title, group_name, append_index, ok = GroupedSmoothNoteIntegrationDialog.get_integration_result(
                    "添加到笔记本",
                    default_name,
                    tabs,
                    parent=None
                )
                if not ok or not action or not title.strip():
                    return

                title = title.strip()

                # 优先提取缓存的划词 HTML 富文本（保留段落、图片、字体颜色等）
                listener = getattr(self, "_selection_translate_listener", None)
                sel_html = ""
                if listener is not None:
                    sel_html = str(getattr(listener, "_last_selection_html", "") or "").strip()

                if sel_html:
                    html_content = sel_html
                else:
                    html_content = MarkdownRenderer.to_html(text)

                if action == "new":
                    next_index = len(tabs)
                    store.save_note_tab(next_index, title, html_content, group_name)
                    self._load_table_notes_settings()
                    self._sync_external_note_to_ima(next_index, inherit_config=True)
                    self._send_tray_notification("提示", f"已成功新建并保存笔记: {title}", 1500)
                elif action == "append":
                    tab_index = append_index if 0 <= append_index < len(tabs) else -1
                    if tab_index == -1:
                        for i, t in enumerate(tabs):
                            if t["name"] == title and str(t.get("group_name", "") or "") == group_name:
                                tab_index = i
                                break

                    if tab_index == -1:
                        return

                    target_tab = tabs[tab_index]
                    title = str(target_tab.get("name", title) or title)
                    old_html = str(target_tab.get("html", "") or "")
                    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    from PyQt6.QtGui import QTextDocument, QTextCursor
                    doc = QTextDocument()
                    doc.setHtml(old_html)
                    cursor = QTextCursor(doc)
                    cursor.movePosition(QTextCursor.MoveOperation.End)

                    divider_html = f"<hr/><p style='color: #64748b; font-size: 11px; margin-top: 8px; margin-bottom: 8px;'>追加划词时间: {current_time}</p>"
                    cursor.insertHtml(divider_html + html_content)

                    new_html = doc.toHtml()
                    store.save_note_tab(tab_index, title, new_html)
                    self._load_table_notes_settings()
                    self._sync_external_note_to_ima(tab_index)
                    self._send_tray_notification("提示", f"已成功追加划词至笔记本: {title}", 1500)
            except Exception as e:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "错误", f"操作笔记本失败：{e}")
            return

        pos = getattr(self, "_selection_translate_popup_pos", None)
        if isinstance(pos, tuple) and len(pos) == 2:
            x, y = int(pos[0]), int(pos[1])
        else:
            x, y = QCursor.pos().x(), QCursor.pos().y()
        try:
            if self._selection_translate_action_popup is not None:
                self._selection_translate_action_popup.close()
        except Exception:
            pass
        panel = self._selection_translate_panel
        panel_exists = False
        try:
            if panel is not None:
                _ = panel.width()
                panel_exists = True
        except (RuntimeError, AttributeError):
            self._selection_translate_panel = None
            panel_exists = False
        if panel_exists:
            try:
                panel.set_text_and_reposition(text, QRect(int(x), int(y), 1, 1))
                self._activate_existing_ai_panel(panel)
                if action_name == "translate":
                    panel.translate_current_text()
                elif action_name == "reply_prompt":
                    panel.reply_prompt_current_text()
                elif action_name in {"optimize_prompt", "ai_search"}:
                    panel.ai_search_current_text()
                elif action_name == "explain":
                    panel.explain_current_text()
                elif action_name in {"summary", "summarize"}:
                    panel.summarize_current_text()
                return
            except Exception as e:
                get_logger().warning("鏃犵紳鏇存柊鍒掕瘝绐楀彛澶辫触: %s", e)
        self._open_selection_text_panel(text, x, y, action_name)

    def _open_selection_hover_translation_popup(self, text: str, x: int, y: int, anchor_rect: Optional[QRect] = None) -> None:
        text = str(text or "").strip()
        if not text:
            return
        try:
            if self._selection_hover_translation_popup is not None:
                self._selection_hover_translation_popup.close()
        except Exception:
            pass
        self._selection_hover_translation_popup = None
        try:
            from deepcat.ui.post_capture_actions import SelectionHoverTranslationPopup

            popup = SelectionHoverTranslationPopup(text=text)
            self._selection_hover_translation_popup = popup
            _popup_local = popup
            owner = self

            def on_popup_destroyed(*_) -> None:
                if owner._selection_hover_translation_popup is _popup_local:
                    owner._selection_hover_translation_popup = None

            popup.destroyed.connect(on_popup_destroyed)
            popup.show_near(anchor_rect if anchor_rect is not None else QRect(), int(x), int(y))
        except Exception as e:
            get_logger().warning("显示划词悬浮翻译卡片失败: %s", e)

    def _open_selection_text_panel(self, text: str, x: int, y: int, action_name: str = "translate") -> None:
        text = str(text or "").strip()
        try:
            if self._selection_translate_panel is not None:
                self._selection_translate_panel.close()
        except Exception:
            pass
        self._selection_translate_panel = None
        try:
            from deepcat.ui.post_capture_actions import OcrTextPanel

            panel_kwargs = {"on_toast": self._send_tray_notification}
            quick_action_handler = getattr(self, "_handle_output_quick_action", None)
            if callable(quick_action_handler):
                panel_kwargs["quick_action_handler"] = quick_action_handler
            panel = OcrTextPanel(**panel_kwargs)
            self._install_ai_panel_close_guard(panel)
            self._selection_translate_panel = panel
            _panel_local = panel
            owner = self
            restore_after_close = getattr(panel, "_restore_main_window_visibility_after_close", None)

            def on_panel_destroyed(*_, restore=restore_after_close) -> None:
                if owner._selection_translate_panel is _panel_local:
                    owner._selection_translate_panel = None
                owner._refresh_selection_popup_blocked()
                try:
                    if callable(restore):
                        restore()
                        QTimer.singleShot(0, restore)
                        QTimer.singleShot(80, restore)
                except Exception:
                    pass

            panel.destroyed.connect(on_panel_destroyed)
            panel.set_text_and_reposition(
                text,
                QRect(int(x), int(y), 1, 1),
                center_on_first_show=bool(action_name == "manual" and not text),
            )
            if hasattr(panel, "_activate_for_text_input"):
                panel._activate_for_text_input()
            self._refresh_selection_popup_blocked()
            if action_name == "translate":
                panel.translate_current_text()
            elif action_name == "reply_prompt":
                panel.reply_prompt_current_text()
            elif action_name in {"optimize_prompt", "ai_search"}:
                panel.ai_search_current_text()
            elif action_name == "explain":
                panel.explain_current_text()
            elif action_name in {"summary", "summarize"}:
                panel.summarize_current_text()
        except Exception as e:
            get_logger().warning("显示划词功能窗口失败: %s", e)

    def _activate_existing_ai_panel(self, panel) -> None:
        self._install_ai_panel_close_guard(panel)
        # 还原折叠/吸附状态
        if getattr(panel, "_panel_collapsed", False):
            try:
                panel._toggle_panel_collapse(False)
            except Exception:
                pass
        # 还原最小化状态
        if panel.isMinimized():
            try:
                panel.showNormal()
            except Exception:
                pass
        # 置前并聚焦，使用短暂置顶机制突破前台焦点保护限制
        try:
            if hasattr(panel, "_activate_for_text_input"):
                panel._activate_for_text_input()
            else:
                if hasattr(panel, "_set_native_topmost"):
                    panel._set_native_topmost(True)
                panel.show()
                panel.raise_()
                panel.activateWindow()
                if hasattr(panel, "_editor") and panel._editor is not None:
                    panel._editor.setFocus()
                if hasattr(panel, "_schedule_transient_topmost_release"):
                    panel._schedule_transient_topmost_release(600)
                elif hasattr(panel, "_set_native_topmost"):
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(600, lambda: panel._set_native_topmost(False))
        except Exception:
            pass

    def _main_window_visibility_snapshot(self) -> dict[str, bool]:
        try:
            visible = bool(self.isVisible())
        except Exception:
            visible = False
        try:
            minimized = bool(self.isMinimized())
        except Exception:
            minimized = False
        try:
            active = bool(self.isActiveWindow())
        except Exception:
            active = False
        return {"visible": visible, "minimized": minimized, "active": active}

    def _restore_main_window_visibility_snapshot(self, state: Any) -> None:
        if not isinstance(state, dict):
            return
        try:
            if bool(state.get("minimized", False)):
                self.showMinimized()
            elif not bool(state.get("visible", False)):
                self.hide()
            elif not bool(state.get("active", False)):
                # 主窗口在打开AI对话前虽然可见但不是活跃窗口（在后台），
                # 关闭AI对话后不应该将其激活到前台，调用 lower() 保持后台状态
                self.lower()
        except RuntimeError:
            pass
        except Exception:
            pass

    def _install_ai_panel_close_guard(self, panel) -> None:
        if panel is None:
            return
        state = self._main_window_visibility_snapshot()
        should_refresh_cursor = bool(
            state.get("visible", False)
            and not state.get("minimized", False)
            and state.get("active", False)
        )

        def restore_main_window_state() -> None:
            self._restore_main_window_visibility_snapshot(state)
            if not should_refresh_cursor:
                return
            try:
                if self.isActiveWindow():
                    self._restore_and_refresh_hover_cursor()
            except RuntimeError:
                return

        try:
            panel._restore_main_window_visibility_after_close = restore_main_window_state
        except Exception:
            pass

    def _handle_output_quick_action(self, action_id: str, source_panel: Optional[QWidget] = None) -> None:
        action = str(action_id or "").strip()
        try:
            if action == "clipboard_window":
                self._open_compact_list_window("clipboard")
            elif action == "later_read_window":
                self._open_compact_list_window("later_read")
            elif action == "region_capture":
                if source_panel is not None:
                    self._pending_output_quick_ocr_panel = source_panel
                    try:
                        minimize = getattr(source_panel, "_minimize_panel", None)
                        if callable(minimize):
                            minimize()
                        else:
                            source_panel.showMinimized()
                    except Exception:
                        pass
                self._start_capture_clicked(from_tray=False, mode_override="框选截图")
            elif action == "new_todo":
                self._open_todo_dialog()
            elif action == "rest_todo":
                self._open_todo_resource_window()
            elif action == "drive_cleaner":
                self._open_drive_cleaner_window()
            elif action == "main_window":
                self._show_from_tray()
            else:
                self._send_tray_notification("快捷动作", "暂不支持该快捷动作。", 1800)
        except Exception as exc:
            get_logger().exception("输出区快捷动作处理失败: %s", action)
            self._send_tray_notification("快捷动作失败", str(exc) or "动作执行失败。", 2600)

    def _open_manual_ai_panel(self) -> None:
        panel = self._selection_translate_panel
        panel_exists = False
        try:
            if panel is not None:
                # 检查 panel 是否已被 Qt 垃圾回收或销毁，只要尝试获取其属性不报错即可
                _ = panel.width()
                panel_exists = True
        except (RuntimeError, AttributeError):
            panel_exists = False

        if panel_exists:
            self._activate_existing_ai_panel(panel)
            return

        try:
            pos = self.geometry().center()
            global_pos = self.mapToGlobal(pos)
            self._open_selection_text_panel("", int(global_pos.x()), int(global_pos.y()), "manual")
        except Exception:
            self._open_selection_text_panel("", int(QCursor.pos().x()), int(QCursor.pos().y()), "manual")

    def _trigger_ai_qa_hotkey(self) -> None:
        # 在热键触发时（用户正在使用其他程序），记录当前的前台窗口句柄，
        # 以便AI对话关闭后将焦点还原给它，而非意外激活本应用主窗口。
        # 如果当前前台窗口就是本应用主窗口，则不记录（置 0），
        # 这样关闭AI对话后不会尝试激活主窗口。
        prev_foreground_hwnd = 0
        if os.name == "nt":
            try:
                import ctypes
                fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
                main_hwnd = int(self.winId())
                # 仅记录外部程序的窗口句柄，排除本应用主窗口
                if fg_hwnd and fg_hwnd != main_hwnd:
                    prev_foreground_hwnd = fg_hwnd
            except Exception:
                pass

        panel = self._selection_translate_panel
        panel_exists = False
        try:
            if panel is not None:
                # 检查 panel 是否已被 Qt 垃圾回收或销毁，只要尝试获取其属性不报错即可
                _ = panel.width()
                panel_exists = True
        except (RuntimeError, AttributeError):
            panel_exists = False

        if panel_exists:
            # 探测窗口是否已经在最前面且属于活动操作焦点
            is_active = panel.isActiveWindow() and panel.isVisible() and not panel.isMinimized() and not getattr(panel, "_panel_collapsed", False)
            if is_active:
                try:
                    if hasattr(panel, "_minimize_panel"):
                        panel._minimize_panel()
                    else:
                        panel.showMinimized()
                except Exception:
                    try:
                        panel.showMinimized()
                    except Exception:
                        pass
            else:
                self._activate_existing_ai_panel(panel)
        else:
            self._open_manual_ai_panel()

        # 从全局热键打开的AI对话，关闭时不应激活主窗口：
        # 1. 重新安装关闭守卫，强制标记主窗口为"非活跃"
        # 2. 记录热键触发前的外部前台窗口句柄，供 closeEvent 还原焦点
        try:
            p = self._selection_translate_panel
            if p is not None:
                # 强制覆盖 close guard，使关闭时总是执行 lower() 阻止主窗口被置顶
                state = self._main_window_visibility_snapshot()
                state["active"] = False  # 从热键打开时，关闭后不应激活主窗口
                def restore_main_window_state(s=state) -> None:
                    self._restore_main_window_visibility_snapshot(s)
                p._restore_main_window_visibility_after_close = restore_main_window_state
                # 记录外部前台窗口句柄
                if prev_foreground_hwnd:
                    p._prev_foreground_hwnd_before_open = prev_foreground_hwnd
        except Exception:
            pass

    def _flush_fast_ui(self, max_ms: int = 1) -> None:
        try:
            flags = (
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                | QEventLoop.ProcessEventsFlag.ExcludeSocketNotifiers
            )
            QApplication.processEvents(flags, int(max(1, max_ms)))
        except Exception:
            pass

    def _toggle_pinned_images_shortcut(self) -> None:
        from deepcat.ui.pinned_image_window import _PINNED_ALIVE, toggle_all_pinned

        if not _PINNED_ALIVE:
            try:
                self._force_tray_notification(
                    "贴图提示",
                    "当前无可显示的活动贴图。\n提示：贴图仅在本次运行期间有效，关闭软件后将自动失效。",
                    4000,
                )
            except Exception:
                QMessageBox.information(self, "提示", "当前无可显示的活动贴图。贴图仅在本次运行期间有效。")
            return

        toggle_all_pinned()
        self._refresh_tray_pinned_action()

    def _right_click_cancel_context_active(self) -> bool:
        if self._is_ocr_panel_active():
            return False
        return bool(
            self._thread is not None
            or self._countdown is not None
            or self._region_capture_shell_pending
            or self._region_overlay is not None
            or self._border_overlay is not None
            or self._post_actions is not None
        )

    def _start_right_click_cancel_listener(self) -> None:
        self._stop_right_click_cancel_listener(force=True)
        try:
            from pynput import mouse

            def _on_click(x, y, button, pressed) -> bool:
                try:
                    if not self._right_click_cancel_context_active():
                        return True
                    if not bool(pressed):
                        return True
                    if button == mouse.Button.right:
                        QTimer.singleShot(0, self._cancel_capture_via_right_click)
                    elif button == mouse.Button.left:
                        QTimer.singleShot(0, self._finish_capture_via_left_click_if_recent)
                except Exception:
                    return True
                return True

            listener_holder: dict[str, object] = {"listener": None}

            def _suppress_mouse_event() -> bool:
                listener_obj = listener_holder.get("listener") or self._right_click_cancel_listener
                suppress = getattr(listener_obj, "suppress_event", None)
                if callable(suppress):
                    suppress()
                return False

            def _event_filter(msg, data) -> bool:
                try:
                    message = int(msg)
                except Exception:
                    return True
                if message == 0x0204:
                    if self._right_click_cancel_context_active():
                        self._suppress_next_right_click_release = True
                        QTimer.singleShot(0, self._cancel_capture_via_right_click)
                        return _suppress_mouse_event()
                    return True
                if message == 0x0205 and bool(self._suppress_next_right_click_release):
                    self._suppress_next_right_click_release = False
                    return _suppress_mouse_event()
                return True

            listener = mouse.Listener(on_click=_on_click, win32_event_filter=_event_filter)
            listener_holder["listener"] = listener
            listener.start()
            self._right_click_cancel_listener = listener
        except Exception:
            self._right_click_cancel_listener = None
        self._start_escape_cancel_listener()

    def _start_escape_cancel_listener(self) -> None:
        self._stop_escape_cancel_listener()
        try:
            from pynput import keyboard

            def _on_press(key) -> bool:
                try:
                    if key == keyboard.Key.esc:
                        QTimer.singleShot(0, self._cancel_capture_via_escape_key)
                except Exception:
                    return True
                return True

            listener = keyboard.Listener(on_press=_on_press)
            listener.start()
            self._escape_cancel_listener = listener
        except Exception:
            self._escape_cancel_listener = None

    def _stop_escape_cancel_listener(self) -> None:
        try:
            if self._escape_cancel_listener is not None:
                self._escape_cancel_listener.stop()
                self._escape_cancel_listener.join(timeout=0.2)
        except Exception:
            pass
        self._escape_cancel_listener = None

    def _stop_right_click_cancel_listener(self, *, force: bool = False) -> None:
        if bool(self._suppress_next_right_click_release) and not bool(force) and self._right_click_cancel_listener is not None:
            self._stop_right_click_cancel_listener_later()
            return
        try:
            if self._right_click_cancel_listener is not None:
                self._right_click_cancel_listener.stop()
                self._right_click_cancel_listener.join(timeout=0.2)
        except Exception:
            pass
        self._right_click_cancel_listener = None
        self._suppress_next_right_click_release = False
        self._stop_escape_cancel_listener()
        self._left_click_finish_deadline = 0.0

    def _stop_right_click_cancel_listener_later(self, delay_ms: int = 350) -> None:
        listener = self._right_click_cancel_listener
        escape_listener = self._escape_cancel_listener

        def stop_if_unchanged() -> None:
            if self._right_click_cancel_listener is listener and self._escape_cancel_listener is escape_listener:
                self._stop_right_click_cancel_listener(force=True)

        QTimer.singleShot(max(1, int(delay_ms)), stop_if_unchanged)

    def _arm_left_click_finish_window(self) -> None:
        self._left_click_finish_deadline = float(time.time()) + 2.0

    def _quit_app(self) -> None:
        if self._is_tray_menu_click_throttled():
            return
        write_crash_breadcrumb(
            "MainWindow.quit_app",
            visible=self.isVisible(),
            minimized=self.isMinimized(),
            tray_menu_visible=bool(self._tray_menu_visible),
        )
        self._allow_quit = True
        self.close()

    def closeEvent(self, event) -> None:
        write_crash_breadcrumb(
            "MainWindow.closeEvent.start",
            allow_quit=bool(self._allow_quit),
            visible=self.isVisible(),
            minimized=self.isMinimized(),
            tray=bool(self._tray is not None),
        )
        if not bool(self._allow_quit):
            event.ignore()
            self._ensure_tray()
            self.hide()
            write_crash_breadcrumb("MainWindow.closeEvent.hide_to_tray.done", visible=self.isVisible())
            return
        try:
            timer = getattr(self, "_table_notes_save_timer", None)
            if timer is not None:
                timer.stop()
            self._save_table_notes_settings()
        except Exception:
            pass
        try:
            self._persist_ui_state()
        except Exception:
            pass
        try:
            self._table_notes_store.close()
        except Exception:
            pass
        try:
            self._todo_store.close()
        except Exception:
            pass
        try:
            self._later_read_store.close()
        except Exception:
            pass
        try:
            self.cleanup()
        except Exception:
            pass
        event.accept()
        write_crash_breadcrumb("MainWindow.closeEvent.accepted")
        app = QApplication.instance()
        if app is not None:
            app.quit()
