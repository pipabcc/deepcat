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
    normalize_network_probe_settings,
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
from deepcat.ui.network_probe import NetworkProbeMonitor, NetworkProbeStatus
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
from deepcat.ui.main_window.compact import _CompactListWindow, _GroupManageDialog



class TrayMixin:
    _NETWORK_PROBE_NOTIFICATION_FAILURE_THRESHOLD = 2
    _NETWORK_PROBE_NOTIFICATION_COOLDOWN_SECONDS = 60.0

    def _ensure_tray(self) -> Optional[QSystemTrayIcon]:
        if self._tray is not None:
            return self._tray
        try:
            write_crash_breadcrumb("MainWindow.ensure_tray.create.start")
            self._tray = self._init_tray()
        except Exception:
            get_logger().exception("System tray initialization failed")
            write_crash_breadcrumb("MainWindow.ensure_tray.create.failed")
            self._tray = None
        else:
            write_crash_breadcrumb("MainWindow.ensure_tray.create.done", tray=bool(self._tray is not None))
        return self._tray

    def _network_probe_tray_icon(self) -> QIcon:
        connected = self._network_probe_tray_icon_state()
        if connected is None:
            return self._camera_icon()

        base = self._camera_icon()
        icon = QIcon()
        for size in (16, 20, 24, 32, 40, 48, 64):
            pixmap = base.pixmap(size, size)
            if pixmap.isNull():
                continue
            icon.addPixmap(self._draw_network_status_bar(pixmap, connected))
        return icon if not icon.isNull() else self._camera_icon()

    def _network_probe_tray_icon_state(self) -> Optional[str]:
        monitor = self._network_probe_monitor
        if monitor is None or not monitor.is_running():
            return None
        status = monitor.status()
        return status if status in {
            NetworkProbeStatus.HEALTHY.value,
            NetworkProbeStatus.UNAVAILABLE.value,
        } else None

    def _refresh_tray_icon(self) -> None:
        tray = self._tray
        if tray is None:
            return
        try:
            tray.setIcon(self._network_probe_tray_icon())
        except RuntimeError:
            pass

    def _style_tray_menu(self, menu: QMenu) -> None:
        # 冻结后的 EXE 与源码运行可能选择不同的系统 QStyle，导致托盘菜单
        # 项目高度、内边距和圆角裁剪不一致。托盘菜单单独固定为 Fusion，
        # 不影响应用其余控件，也不依赖具体模型或打包环境。
        try:
            from PyQt6.QtWidgets import QStyleFactory

            fusion_style = QStyleFactory.create("Fusion")
            if fusion_style is not None:
                menu.setStyle(fusion_style)
                menu._deepcat_tray_style = fusion_style  # type: ignore[attr-defined]
        except Exception:
            pass
        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        try:
            flags |= Qt.WindowType.NoDropShadowWindowHint
        except AttributeError:
            pass
        menu.setWindowFlags(flags)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        menu.setStyleSheet("""
            QMenu {
                background-color: #ffffff;
                border: 1px solid #dfe4ec;
                border-radius: 8px;
                padding: 6px;
                margin: 0px;
                min-width: 196px;
            }
            QMenu::item {
                min-height: 34px;
                padding: 0 12px 0 28px;
                border-radius: 6px;
                color: #374151;
                font-size: 13px;
                font-weight: 500;
            }
            QMenu::item:selected {
                background-color: #f3f4f6;
                color: #111827;
            }
            QMenu::icon {
                left: 8px;
            }
            QMenu::separator {
                height: 1px;
                background-color: #e2e8f0;
                margin: 4px 6px;
            }
        """)

    def _refresh_tray_action_icons(self) -> None:
        for action, icon_name in dict(getattr(self, "_tray_action_icon_names", {}) or {}).items():
            try:
                action.setIcon(self._asset_icon(icon_name, action.icon()))
            except RuntimeError:
                continue
        self._refresh_tray_icon()

    def _init_tray(self) -> QSystemTrayIcon:
        write_crash_breadcrumb(
            "MainWindow.tray.init.start",
            system_tray_available=QSystemTrayIcon.isSystemTrayAvailable(),
            supports_messages=QSystemTrayIcon.supportsMessages(),
        )

        # 创建一个隐形、无任务栏占位、无边框的 dummy 窗口作为托盘 and 菜单的 parent
        # 彻底在操作系统 and 焦点交互层面隔离并保护主窗口 (self) 避开任何强行激活
        self._tray_dummy_widget = QWidget()
        self._tray_dummy_widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._tray_dummy_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) # 完全透明，防黑色填充
        self._tray_dummy_widget.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self._tray_dummy_widget.setGeometry(-10, -10, 0, 0) # 物理尺寸零化且移出屏幕，双重防漏出

        tray = QSystemTrayIcon(self._tray_dummy_widget)
        icon = self._network_probe_tray_icon()
        tray.setIcon(icon)
        tray.setToolTip(self._tray_tooltip_text())

        # 将哑窗口设为菜单 parent，避免 Windows 弹出右键菜单时自动激活主窗口
        menu = QMenu(self._tray_dummy_widget)
        self._style_tray_menu(menu)
        self._tray_menu = menu
        menu.setProperty("showIconsInMenu", True)
        act_show = QAction("开始截图", self)
        act_note_float = QAction("随手记(Alt+J)", self)
        act_clipboard_float = QAction("复制记录(Alt+F)", self)
        act_later_read_float = QAction("稍后阅读(Alt+S)", self)
        act_new_todo = QAction("新建待办", self)
        act_later_read = QAction("稍后阅读", self)
        act_ai_qa = QAction("AI对话", self)
        act_clipboard = QAction("复制记录", self)
        act_notes = QAction("表格记事", self)
        act_pinned = QAction("显示贴图", self)
        act_network_probe = QAction("节点探测", self)
        act_quit = QAction("退出", self)

        # 设置图标
        self._tray_action_icon_names = {
            act_show: "icon_nav_camera.svg",
            act_note_float: "icon_nav_notes.svg",
            act_clipboard_float: "icon_nav_clipboard.svg",
            act_later_read_float: "icon_nav_read.svg",
            act_new_todo: "icon_nav_todo.svg",
            act_later_read: "icon_nav_read.svg",
            act_ai_qa: "icon_nav_ai.svg",
            act_clipboard: "icon_nav_clipboard.svg",
            act_notes: "icon_nav_notes.svg",
            act_pinned: "icon_action_pin.svg",
            act_network_probe: "icon_network_probe.svg",
            act_quit: "icon_action_close.svg",
        }
        for action in self._tray_action_icon_names:
            action.setIconVisibleInMenu(True)
        self._refresh_tray_action_icons()
        act_network_probe.setCheckable(True)
        act_network_probe.setChecked(False)

        # 设置初始可见性
        act_new_todo.setVisible(self._feature_enabled("todo"))
        act_later_read.setVisible(self._feature_enabled("later_read") and self._later_read_enabled_now())
        act_clipboard.setVisible(self._feature_enabled("clipboard_history"))
        act_notes.setVisible(self._feature_enabled("table_notes"))
        act_note_float.setVisible(self._feature_enabled("table_notes"))
        act_clipboard_float.setVisible(self._feature_enabled("clipboard_history"))
        act_later_read_float.setVisible(self._feature_enabled("later_read") and self._later_read_enabled_now())

        self._tray_show_action = act_show
        self._tray_ai_qa_action = act_ai_qa
        self._tray_pinned_action = act_pinned
        self._tray_network_probe_action = act_network_probe
        self._tray_later_read_action = act_later_read
        self._tray_new_todo_action = act_new_todo
        self._tray_clipboard_action = act_clipboard
        self._tray_notes_action = act_notes
        self._tray_note_float_action = act_note_float
        self._tray_clipboard_float_action = act_clipboard_float
        self._tray_later_read_float_action = act_later_read_float

        self._refresh_tray_later_read_action()
        self._refresh_tray_pinned_action()

        act_show.triggered.connect(lambda *_: self._start_capture_clicked(from_tray=True, mode_override="框选截图"))
        act_note_float.triggered.connect(self._trigger_note_float)
        act_clipboard_float.triggered.connect(lambda: self._open_compact_list_window("clipboard"))
        act_later_read_float.triggered.connect(lambda: self._open_compact_list_window("later_read"))
        act_new_todo.triggered.connect(self._open_todo_dialog_from_tray)
        act_later_read.triggered.connect(self._open_later_read_from_tray)
        act_ai_qa.triggered.connect(self._open_ai_qa_from_tray)
        act_clipboard.triggered.connect(self._open_clipboard_from_tray)
        act_notes.triggered.connect(self._open_table_notes_from_tray)
        act_pinned.triggered.connect(self._toggle_pinned_images_from_tray)
        act_network_probe.toggled.connect(self._set_network_probe_enabled)
        act_quit.triggered.connect(self._quit_app)

        menu.addAction(act_show)
        menu.addAction(act_note_float)
        menu.addAction(act_clipboard_float)
        menu.addAction(act_later_read_float)
        menu.addAction(act_new_todo)
        menu.addAction(act_ai_qa)
        menu.addAction(act_pinned)
        tray_separator = menu.addSeparator()
        menu.insertAction(tray_separator, act_network_probe)
        menu.addAction(act_quit)

        if self._network_probe_enabled_from_settings():
            self._set_network_probe_enabled(True, persist=False)

        menu.aboutToShow.connect(self._on_tray_menu_about_to_show)
        menu.aboutToHide.connect(self._on_tray_menu_about_to_hide)
        # 使用原生 setContextMenu 彻底杜绝 COM 异常 0x8001010d。
        # 配合我们在 _tray_dummy_widget 上的完美焦点隔离和 aboutToHide/aboutToShow
        # 中的最小化状态自愈检查，这能同时保证不激活主窗口与程序极其稳定运行。
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        write_crash_breadcrumb(
            "MainWindow.tray.init.done",
            tray_visible=tray.isVisible(),
            menu_actions=len(menu.actions()),
            menu_action_texts=[str(action.text()) for action in menu.actions()],
        )
        return tray

    def _ensure_network_probe_monitor(self) -> NetworkProbeMonitor:
        monitor = self._network_probe_monitor
        if monitor is None:
            probe_settings = normalize_network_probe_settings(
                (getattr(self._app_settings, "ui", {}) or {}).get("network_probe")
            )
            monitor = NetworkProbeMonitor(
                self,
                proxy_url_provider=self._network_probe_proxy_url,
                probe_kind=probe_settings["mode"],
            )
            monitor.status_changed.connect(self._on_network_probe_status_changed)
            self._network_probe_monitor = monitor
        return monitor

    def _network_probe_enabled_from_settings(self) -> bool:
        data = normalize_network_probe_settings(
            (getattr(self._app_settings, "ui", {}) or {}).get("network_probe")
        )
        return bool(data["enabled"])

    def _save_network_probe_enabled(self, enabled: bool) -> None:
        raw = (getattr(self._app_settings, "ui", {}) or {}).get("network_probe")
        data = normalize_network_probe_settings(raw)
        if bool(data.get("enabled", False)) == bool(enabled):
            return
        data["enabled"] = bool(enabled)
        try:
            self._current = update_ui_settings(network_probe=data)
            self._app_settings = self._current
        except Exception:
            pass

    def _set_network_probe_enabled(self, enabled: bool, persist: bool = True) -> None:
        enabled = bool(enabled)
        self._reset_network_probe_notification_state()
        monitor = self._ensure_network_probe_monitor()
        if enabled:
            monitor.start()
        else:
            monitor.stop()
        if persist:
            self._save_network_probe_enabled(enabled)
        self._refresh_network_probe_action()
        self._refresh_tray_icon()
        self._refresh_tray_tooltip()

    def _refresh_network_probe_action(self) -> None:
        action = self._tray_network_probe_action
        if action is None:
            return
        monitor = self._network_probe_monitor
        running = bool(monitor is not None and monitor.is_running())
        status = monitor.status() if running and monitor is not None else NetworkProbeStatus.STOPPED.value
        action.blockSignals(True)
        action.setChecked(running)
        if status == NetworkProbeStatus.UNAVAILABLE.value:
            action.setText("节点探测：外网不通")
            action.setIcon(self._network_probe_menu_icon(status))
        elif status == NetworkProbeStatus.HEALTHY.value:
            action.setText(f"节点探测：连通 · {self._network_probe_route_summary(monitor)}")
            action.setIcon(self._network_probe_menu_icon(status))
        elif running:
            action.setText("节点探测：检测中")
            action.setIcon(self._network_probe_menu_icon(None))
        else:
            action.setText("节点探测：点击检测")
            action.setIcon(self._network_probe_menu_icon(None))
        action.blockSignals(False)

    @staticmethod
    def _network_probe_route_summary(monitor: Optional[NetworkProbeMonitor]) -> str:
        result = monitor.last_http_result() if monitor is not None else None
        if result is None:
            return "检测完成"
        route = "配置代理" if bool(getattr(result, "via_proxy", False)) else "直连/TUN"
        elapsed_ms = max(0, int(getattr(result, "route_elapsed_ms", 0) or getattr(result, "elapsed_ms", 0)))
        return f"{route} {elapsed_ms} ms"

    def _network_probe_menu_icon(self, status: Optional[str]) -> QIcon:
        base = self._asset_icon("icon_network_probe.svg")
        if status is None:
            return base

        size = 24
        pixmap = base.pixmap(size, size)
        if pixmap.isNull():
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            colors = {
                NetworkProbeStatus.HEALTHY.value: QColor("#16a34a"),
                NetworkProbeStatus.UNAVAILABLE.value: QColor("#dc2626"),
            }
            color = colors.get(status, QColor("#64748b"))
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(QRect(7, 1, 10, 5), 2, 2)
        finally:
            painter.end()
        return QIcon(pixmap)

    def _on_network_probe_status_changed(self, status: str, result: object) -> None:
        self._refresh_network_probe_action()
        self._refresh_tray_icon()
        self._refresh_tray_tooltip()

        if status == NetworkProbeStatus.HEALTHY.value:
            self._network_probe_notification_failure_count = 0
            return
        if status != NetworkProbeStatus.UNAVAILABLE.value:
            return

        failure_count = int(getattr(self, "_network_probe_notification_failure_count", 0)) + 1
        self._network_probe_notification_failure_count = failure_count
        if failure_count < self._NETWORK_PROBE_NOTIFICATION_FAILURE_THRESHOLD:
            return

        now = time.monotonic()
        last_alert = float(getattr(self, "_network_probe_last_alert_monotonic", 0.0))
        if last_alert > 0.0 and now - last_alert < self._NETWORK_PROBE_NOTIFICATION_COOLDOWN_SECONDS:
            return

        self._network_probe_last_alert_monotonic = now
        target_name = str(getattr(getattr(result, "target", None), "name", "当前探针") or "当前探针")
        self._force_tray_notification(
            "节点探测",
            f"连续 {failure_count} 轮检测失败：直连/TUN 与配置代理均未连通外网（探测目标：{target_name}）",
            4200,
        )

    def _reset_network_probe_notification_state(self) -> None:
        self._network_probe_notification_failure_count = 0
        self._network_probe_last_alert_monotonic = 0.0

    def _open_clipboard_from_tray(self) -> None:
        if self._is_tray_menu_click_throttled():
            return
        if not self._feature_enabled("clipboard_history"):
            return
        self._switch_page(4)
        self._show_from_tray()
        try:
            self.activateWindow()
        except Exception:
            pass

    def _open_ai_qa_from_tray(self) -> None:
        if self._is_tray_menu_click_throttled():
            return
        self._open_manual_ai_panel()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        write_crash_breadcrumb(
            "MainWindow.tray.activated",
            reason=getattr(reason, "name", str(reason)),
            window_visible=self.isVisible(),
            minimized=self.isMinimized(),
            tray_menu_visible=bool(self._tray_menu_visible),
            thread_active=bool(self._thread is not None),
        )
        try:
            if reason == QSystemTrayIcon.ActivationReason.Context:
                # 右键菜单已由原生 setContextMenu 完美托管，此处直接拦截忽略，杜绝 Hack 定时器调用
                return
            if bool(self._tray_menu_visible):
                return
            if reason not in {
                QSystemTrayIcon.ActivationReason.Trigger,
                QSystemTrayIcon.ActivationReason.DoubleClick,
            }:
                return
            self._sync_embedded_settings_controls()
            self._show_from_tray()
            try:
                self.activateWindow()
            except Exception:
                pass
        except Exception:
            get_logger().exception("System tray activation failed")
            write_crash_breadcrumb("MainWindow.tray.activated.failed", reason=getattr(reason, "name", str(reason)))
            return

    def _show_tray_context_menu(self) -> None:
        """手动弹出托盘右键菜单，避免 Windows 自动激活主窗口。"""
        if self._tray_menu is None:
            return
        try:
            # 如果菜单正在显示，不重复弹出
            if self._tray_menu_visible:
                return
            # 记录菜单显示前窗口是否最小化（结合 Windows API IsIconic 确保准确性，防范 Qt windowState 延迟）
            is_iconic = False
            if os.name == "nt":
                try:
                    hwnd = int(self.winId())
                    if hwnd:
                        import ctypes
                        is_iconic = bool(ctypes.windll.user32.IsIconic(hwnd))
                except Exception:
                    pass
            was_minimized = self.isMinimized() or is_iconic
            self._was_minimized_before_tray_menu = was_minimized
            # 获取鼠标位置并在该位置弹出菜单
            # aboutToShow 信号会在 popup 时自动触发，不需要手动调用 _on_tray_menu_about_to_show
            cursor_pos = QCursor.pos()
            self._tray_menu.popup(cursor_pos)
        except RuntimeError:
            # QMenu 对象可能已被删除
            self._tray_menu_visible = False
            self._tray_menu = None
        except Exception:
            get_logger().exception("Tray context menu popup failed")
            self._tray_menu_visible = False

    def _show_from_tray(self) -> None:
        was_hidden = not self.isVisible()
        write_crash_breadcrumb(
            "MainWindow.show_from_tray.start",
            was_hidden=was_hidden,
            minimized=self.isMinimized(),
        )
        self._apply_solid_window_backgrounds()
        self._set_window_resize_mode(self._is_resizable_page_index(), schedule_stabilize=False)
        if was_hidden:
            self.setWindowOpacity(0.0)
            self.showNormal()
            self.raise_()
            self.activateWindow()

            def _delayed_fade_in():
                try:
                    self.setWindowOpacity(1.0)
                    self.repaint()
                except Exception:
                    pass
            QTimer.singleShot(50, _delayed_fade_in)
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        self._schedule_hover_cursor_refresh()
        write_crash_breadcrumb("MainWindow.show_from_tray.done", visible=self.isVisible())

    def _show_pinned_images_from_tray(self) -> None:
        from deepcat.ui.pinned_image_window import restore_all_pinned

        restore_all_pinned()

    def _toggle_pinned_images_from_tray(self) -> None:
        if self._is_tray_menu_click_throttled():
            return
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

    def _format_hotkey_for_tray(self, hotkey_str: str) -> str:
        if not hotkey_str:
            return ""
        parts = []
        for part in hotkey_str.split("+"):
            cleaned = part.replace("<", "").replace(">", "").strip()
            if cleaned:
                parts.append(cleaned.capitalize())
        return "+".join(parts)

    def _refresh_tray_pinned_action(self) -> None:
        try:
            from deepcat.ui.pinned_image_window import has_visible_pinned

            if self._tray_pinned_action is not None:
                prefix = "隐藏贴图" if has_visible_pinned() else "显示贴图"
                self._tray_pinned_action.setText(f"{prefix} (Alt+T)")
        except Exception:
            pass

    def _is_tray_menu_click_throttled(self) -> bool:
        """检查托盘菜单项触发是否过于频繁（防御快速连击托盘导致的误触退出/闪退）"""
        import time
        now = time.time()
        # 如果距离菜单弹出（aboutToShow）的时间小于 250 毫秒，则认为可能是连击产生的误触发，直接拦截
        shown_time = getattr(self, "_tray_menu_shown_time", 0.0)
        if now - shown_time < 0.25:
            write_crash_breadcrumb(
                "MainWindow.tray_menu.throttled",
                delta=now - shown_time
            )
            return True
        return False

    def _on_tray_menu_about_to_show(self) -> None:
        self._tray_menu_shown_time = time.time()
        try:
            is_minimized = self.isMinimized()
        except RuntimeError:
            return

        # 结合 Windows API 检测真实最小化状态，防范 Qt 状态延迟同步导致的误判定
        is_iconic = False
        if os.name == "nt":
            try:
                hwnd = int(self.winId())
                if hwnd:
                    import ctypes
                    is_iconic = bool(ctypes.windll.user32.IsIconic(hwnd))
            except Exception:
                pass
        is_minimized_real = is_minimized or is_iconic

        try:
            self._refresh_tray_pinned_action()
            self._refresh_network_probe_action()
        except RuntimeError:
            pass

        write_crash_breadcrumb(
            "MainWindow.tray_menu.about_to_show",
            window_visible=self.isVisible(),
            minimized=is_minimized_real,
            tray=bool(self._tray is not None),
            menu_action_texts=[
                str(action.text())
                for action in self._tray_menu.actions()
            ] if self._tray_menu is not None else [],
        )
        self._tray_menu_visible = True
        self._was_minimized_before_tray_menu = is_minimized_real
        # 将哑窗口临时 show 出来并强行设为前台窗口，使得 Windows 能正常处理输入焦点注销，
        # 从而保证当用户点击菜单外部时，菜单能极其平滑顺畅地自动消失；同时绝对不影响主窗口的最小化隐藏。
        if os.name == "nt":
            try:
                dummy = getattr(self, "_tray_dummy_widget", None)
                if dummy is not None:
                    dummy.show()
                    hwnd = int(dummy.winId())
                    if hwnd:
                        import ctypes
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

        def adjust_menu_pos() -> None:
            try:
                if self._tray_menu is not None and self._tray_menu.isVisible():
                    self._tray_menu.move(self._tray_menu.x(), self._tray_menu.y() - 24)
            except Exception:
                pass
        QTimer.singleShot(0, adjust_menu_pos)

    def _on_tray_menu_about_to_hide(self) -> None:
        try:
            write_crash_breadcrumb(
                "MainWindow.tray_menu.about_to_hide",
                window_visible=self.isVisible(),
                minimized=self.isMinimized(),
            )
        except RuntimeError:
            pass
        self._tray_menu_visible = False
        # 隐藏临时显示的哑窗口
        try:
            dummy = getattr(self, "_tray_dummy_widget", None)
            if dummy is not None:
                dummy.hide()
        except Exception:
            pass
        # 如果菜单显示前窗口是最小化状态，菜单关闭后确保窗口不会意外被激活到前台
        if self._was_minimized_before_tray_menu:
            try:
                # 结合 Windows API 检测真实最小化状态
                is_iconic = False
                if os.name == "nt":
                    try:
                        hwnd = int(self.winId())
                        if hwnd:
                            import ctypes
                            is_iconic = bool(ctypes.windll.user32.IsIconic(hwnd))
                    except Exception:
                        pass
                is_minimized_real = self.isMinimized() or is_iconic

                if not is_minimized_real and self.isVisible():
                    # 窗口被意外恢复了，重新最小化
                    self.showMinimized()
            except RuntimeError:
                pass
            except Exception:
                pass
            self._was_minimized_before_tray_menu = False

    def _tray_tooltip_text(self) -> str:
        try:
            probe_lines: list[str] = []
            monitor = self._network_probe_monitor
            if monitor is not None and monitor.is_running():
                status = monitor.status()
                if status == NetworkProbeStatus.UNAVAILABLE.value:
                    probe_lines.append("节点探测：外网不通")
                elif status == NetworkProbeStatus.HEALTHY.value:
                    probe_lines.append(f"节点探测：连通 · {self._network_probe_route_summary(monitor)}")
                else:
                    probe_lines.append("节点探测：检测中")
            today = [x for x in self._todo_items_for_date(QDate.currentDate()) if not bool(x.get("struck_off", False))]
            if not today:
                lines = ["DeepCat", *probe_lines, "今日没有待办"]
                return "\n".join(lines)
            lines = ["DeepCat", "今日待办"]
            lines.extend(probe_lines)
            for item in today[:6]:
                flag = "!" if bool(item.get("important", False)) else "-"
                lines.append(f"{flag} {self._todo_display_time(item)} {str(item.get('title', '未命名待办'))}")
            if len(today) > 6:
                lines.append(f"... 还有 {len(today) - 6} 项")
            return "\n".join(lines)
        except Exception:
            return "DeepCat"

    def _refresh_tray_tooltip(self) -> None:
        try:
            tray = self._tray
            if tray is not None:
                tray.setToolTip(self._tray_tooltip_text())
        except Exception:
            pass

    def _send_tray_notification(
        self,
        title: str,
        message: str,
        duration_ms: int,
        open_dir: Optional[str] = None,
        action_text: str = "",
        action_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        if not bool(getattr(self._app_settings, "notifications_enabled", False)):
            return
        msg = str(message)
        t = str(title)
        if open_dir is None and ("已保存" in t or "已保存" in msg or "录制已保存" in msg):
            if msg.startswith("已保存到："):
                path = msg[len("已保存到："):].strip()
                if path:
                    try:
                        open_dir = str(Path(path).parent)
                    except Exception:
                        pass
            elif "录制已保存：" in msg:
                raw_path = msg.split("录制已保存：", 1)[-1].strip()
                if raw_path:
                    first_token = raw_path.split(" (", 1)[0].strip() if " (" in raw_path else raw_path
                    try:
                        p = Path(first_token)
                        if p.parent.exists():
                            open_dir = str(p.parent)
                        else:
                            open_dir = str(Path(raw_path).parent)
                    except Exception:
                        pass
            elif "个文件：" in msg:
                path = msg.split("个文件：", 1)[-1].strip()
                if path:
                    try:
                        open_dir = str(Path(path).parent)
                    except Exception:
                        pass
        t, msg, duration = self._normalize_toast_feedback(t, msg, open_dir=open_dir)
        try:
            NotificationPopup.show_notification(
                t,
                msg,
                duration,
                open_dir=open_dir,
                action_text=action_text,
                action_callback=action_callback,
            )
        except Exception as exc:
            get_logger().exception("显示通知失败")
            self._show_notification_failure_status(f"通知显示失败：{exc}")

    def _force_tray_notification(self, title: str, message: str, duration_ms: int) -> None:
        try:
            t, msg, duration = self._normalize_toast_feedback(str(title), str(message))
            NotificationPopup.show_notification(t, msg, duration)
        except Exception as exc:
            get_logger().exception("显示通知失败")
            self._show_notification_failure_status(f"通知显示失败：{exc}")
