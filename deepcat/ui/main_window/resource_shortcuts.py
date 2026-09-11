from __future__ import annotations

import os
import ctypes
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from deepcat.ui.main_window.window import MainWindow
from urllib.parse import urlparse
from PyQt6.QtCore import Qt, QEvent, QPoint
from PyQt6.QtGui import QGuiApplication, QColor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
    QLineEdit,
)
from deepcat.ui.settings_dialog import SettingsDialog

from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND, MAIN_WINDOW_BACKGROUND_COLORREF
from deepcat.ui.main_window.helpers import _normalize_resource_shortcut_target, _resource_shortcut_default_title


class _ResourceShortcutDialog(QDialog):
    def __init__(self, parent: QWidget, base_style_sheet: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("ResourceShortcutDialog")
        self.setStyleSheet(
            str(base_style_sheet or "")
            + f"\nQDialog#ResourceShortcutDialog {{ background-color: {MAIN_WINDOW_BACKGROUND}; }}"
        )
        self.setWindowTitle("添加工具快捷方式")
        self.setModal(True)
        self.setMinimumWidth(440)
        self._kind_value = "app"
        self._title_value = ""
        self._target_value = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)

        self._radio_url = QRadioButton("网址URL")
        self._radio_app = QRadioButton("快捷方式")
        self._radio_url.setCursor(Qt.CursorShape.PointingHandCursor)
        self._radio_app.setCursor(Qt.CursorShape.PointingHandCursor)
        self._radio_app.setChecked(True)

        self._kind_group = QButtonGroup(self)
        self._kind_group.addButton(self._radio_url)
        self._kind_group.addButton(self._radio_app)

        kind_layout = QHBoxLayout()
        kind_layout.setContentsMargins(0, 0, 0, 0)
        kind_layout.setSpacing(16)
        kind_layout.addWidget(self._radio_url)
        kind_layout.addWidget(self._radio_app)
        kind_layout.addStretch(1)

        kind_row = QWidget()
        kind_row.setLayout(kind_layout)
        form.addRow("类型", kind_row)

        self._title = QLineEdit()
        self._title.setPlaceholderText("留空将自动使用域名或文件名")
        form.addRow("名称", self._title)

        target_row = QWidget()
        target_layout = QHBoxLayout(target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.setSpacing(8)
        self._target = QLineEdit()
        self._target.setPlaceholderText("https://example.com")
        self._browse = QPushButton("浏览")
        self._browse.setObjectName("BtnSmallSecondary")
        self._browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse.setFixedWidth(64)
        target_layout.addWidget(self._target, 1)
        target_layout.addWidget(self._browse)
        form.addRow("目标", target_row)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_btn is not None:
            ok_btn.setText("添加")
            ok_btn.setObjectName("BtnSmallPrimary")
        if cancel_btn is not None:
            cancel_btn.setText("取消")
            cancel_btn.setObjectName("BtnSmallSecondary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        watermark = QLabel("提示：可拖拽快捷图标到窗口自动添加")
        watermark.setStyleSheet("color: #94a3b8; font-size: 11px;")
        bottom_row.addWidget(watermark)
        bottom_row.addStretch(1)
        bottom_row.addWidget(buttons)
        root.addLayout(bottom_row)

        self._radio_url.toggled.connect(self._sync_kind_controls)
        self._radio_app.toggled.connect(self._sync_kind_controls)
        self._browse.clicked.connect(self._browse_app_target)
        self._target.editingFinished.connect(self._fill_title_if_empty)
        self._sync_kind_controls()
        SettingsDialog._install_custom_text_context_menus(self, self)
        # 在进入模态事件循环前完成样式 polish、布局和尺寸计算，避免 Windows
        # 先显示原生空白窗口，再等待复杂主样式表完成首帧布局。
        self.ensurePolished()
        root.activate()
        self.adjustSize()
        hint = self.sizeHint()
        self.resize(max(440, hint.width()), hint.height())
        self._layout_prepared = True

    def values(self) -> tuple[str, str, str]:
        return self._kind_value, self._title_value, self._target_value

    def _current_kind(self) -> str:
        if self._radio_app.isChecked():
            return "app"
        return "url"

    def _sync_kind_controls(self) -> None:
        kind = self._current_kind()
        if kind == "app":
            self._target.setPlaceholderText("选择 .exe、.lnk 或其他可打开文件")
            self._browse.setEnabled(True)
            self._browse.show()
        else:
            self._target.setPlaceholderText("https://example.com")
            self._browse.setEnabled(False)
            self._browse.hide()

    def _browse_app_target(self) -> None:
        start_dir = str(Path.home())
        try:
            dialog = QFileDialog(
                self,
                "选择软件或快捷方式",
                start_dir,
                "程序和快捷方式 (*.exe *.lnk *.bat *.cmd);;所有文件 (*)",
            )
            dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
            # 原生文件对话框会在本进程内加载第三方 Shell 扩展，曾因 COM 套间冲突
            # （0x8001010e RPC_E_CHANGED_MODE）使主线程在对话框内直接崩溃退出
            # （见 logs/crash.log 2026-09-06 现场）。选择目录/颜色对话框已统一
            # 使用非原生对话框，此处保持一致。
            dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            files = dialog.selectedFiles()
            path = str(files[0]) if files else ""
        except Exception as exc:
            parent = self.parent()
            if parent is not None and hasattr(parent, "_show_resource_status"):
                parent._show_resource_status(f"选择文件失败：{exc}", tone="error", auto_hide_ms=4000)
            return
        if path:
            self._target.setText(path)
            self._fill_title_if_empty()

    def _fill_title_if_empty(self) -> None:
        if self._title.text().strip():
            return
        kind = self._current_kind()
        target = _normalize_resource_shortcut_target(kind, self._target.text())
        if target:
            self._title.setText(_resource_shortcut_default_title(kind, target))

    def accept(self) -> None:
        kind = self._current_kind()
        target = _normalize_resource_shortcut_target(kind, self._target.text())
        title = self._title.text().strip() or _resource_shortcut_default_title(kind, target)
        if not target:
            parent = self.parent()
            if parent is not None and hasattr(parent, "_show_resource_status"):
                parent._show_resource_status("请填写网址 URL 或选择软件快捷方式。", tone="warning")
            return
        if kind == "url":
            parsed = urlparse(target)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                parent = self.parent()
                if parent is not None and hasattr(parent, "_show_resource_status"):
                    parent._show_resource_status("请输入有效的网址 URL。", tone="warning")
                return
        elif not Path(target).exists():
            parent = self.parent()
            if parent is not None and hasattr(parent, "_show_resource_status"):
                parent._show_resource_status("选择的软件或快捷方式不存在。", tone="warning")
            return
        self._kind_value = kind
        self._title_value = title[:40]
        self._target_value = target
        super().accept()

    def _apply_caption_color(self) -> None:
        try:
            if os.name != "nt":
                return
            hwnd = int(self.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_caption_color()


class _TodoResourceWindow(QDialog):
    def __init__(self, parent: "MainWindow") -> None:
        super().__init__(None)
        self._main_window = parent
        self.setWindowTitle("休息待办")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setMinimumSize(1080, 720)
        self.resize(1108, 760)
        self.setWindowIcon(parent._asset_icon("icon_nav_todo.svg", parent.windowIcon()))
        self.setStyleSheet(parent.styleSheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        page = parent._build_cat_reminder_page()
        layout.addWidget(page)
        parent._connect_todo_resource_controls()

    def _apply_caption_color(self) -> None:
        try:
            if os.name != "nt":
                return
            hwnd = int(self.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_caption_color()


class _DeleteShortcutPopup(QWidget):
    """圆角删除快捷方式菜单，结构与翻译模型下拉 (_OcrModelMenuPopup) 一致：
    透明根窗口 + 内嵌圆角白底面板 + 投影，避免 QMenu 圆角的右下直角与左上残留。"""

    def __init__(self, title: str, on_delete: Callable[[], None], parent: QWidget | None = None) -> None:
        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        try:
            flags |= Qt.WindowType.NoDropShadowWindowHint
        except AttributeError:
            pass
        super().__init__(parent, flags)
        self.setObjectName("DeleteShortcutPopupRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._on_delete = on_delete
        self._anchor: QWidget | None = None
        self._global_pos = QPoint()

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(0)

        panel = QWidget(self)
        panel.setObjectName("DeleteShortcutPopupPanel")
        panel.setStyleSheet("""
            QWidget#DeleteShortcutPopupPanel {
                background: #ffffff;
                border: 1px solid #dfe4ec;
                border-radius: 8px;
            }
        """)
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(12)
        shadow.setColor(QColor(15, 23, 42, 38))
        shadow.setOffset(0, 3)
        panel.setGraphicsEffect(shadow)
        root_layout.addWidget(panel)

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(6, 6, 6, 6)
        panel_layout.setSpacing(0)

        item = QPushButton(panel)
        item.setObjectName("DeleteShortcutPopupItem")
        item.setText(f"删除快捷方式「{title}」")
        item.setCursor(Qt.CursorShape.PointingHandCursor)
        item.setMinimumHeight(32)
        item.setStyleSheet("""
            QPushButton#DeleteShortcutPopupItem {
                background: transparent;
                border: none;
                border-radius: 6px;
                padding: 0 12px;
                text-align: left;
                color: #374151;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton#DeleteShortcutPopupItem:hover {
                background-color: #f3f4f6;
                color: #111827;
            }
        """)
        item.clicked.connect(self._on_item_clicked)
        panel_layout.addWidget(item)

        self._item = item
        self.adjustSize()

    def _on_item_clicked(self) -> None:
        cb = self._on_delete
        self.close()
        if cb is not None:
            cb()

    def eventFilter(self, obj, event) -> bool:
        if obj is self and event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
            self.removeEventFilter(self)
        return super().eventFilter(obj, event)

    def _adjust_position(self) -> None:
        global_pos = self._global_pos
        margin = 6
        screen = QGuiApplication.screenAt(global_pos)
        screen_geo = screen.availableGeometry() if screen is not None else QGuiApplication.primaryScreen().availableGeometry()
        bounds = screen_geo
        parent = self.parentWidget()
        top_level = parent.window() if parent is not None else None
        if top_level is not None:
            parent_bounds = screen_geo.intersected(top_level.frameGeometry())
            if parent_bounds.width() >= self.width() + margin * 2 and parent_bounds.height() >= 80:
                bounds = parent_bounds

        x = int(global_pos.x())
        y = int(global_pos.y())
        if y + self.height() > bounds.bottom() + 1 - margin and self._anchor is not None:
            y = self._anchor.mapToGlobal(QPoint(0, 0)).y() - self.height()
        if x + self.width() > bounds.right() + 1 - margin:
            x = bounds.right() + 1 - margin - self.width()
        x = max(bounds.left() + margin, x)
        y = max(bounds.top() + margin, min(y, bounds.bottom() + 1 - margin - self.height()))
        self.move(QPoint(x, y))

    def show_at(self, anchor: QWidget, global_pos: QPoint) -> None:
        self.installEventFilter(self)
        self._anchor = anchor
        self._global_pos = global_pos
        self._adjust_position()
        self.show()
        self.raise_()
        self.activateWindow()


# --- Automatically Appended Mixin Imports and Class ---

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
from deepcat.ui.main_window.compact import _CompactListWindow, _GroupManageDialog



_ICON_FETCH_WORKERS: set = set()


def _download_resource_shortcut_logo_bytes(target: str) -> Optional[bytes]:
    """在任意线程抓取站点图标，返回原始图片字节；QPixmap 只能在 GUI 线程解码。"""
    parsed = urlparse(str(target or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [
        f"{origin}/favicon.ico",
        f"https://www.google.com/s2/favicons?domain_url={quote(target, safe='')}&sz=128",
    ]
    for candidate in candidates:
        try:
            request = urllib.request.Request(
                candidate,
                headers={"User-Agent": "DeepCat/1.0 (+https://deepcat.local)"},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                data = response.read(512 * 1024)
            if data:
                return data
        except Exception:
            continue
    return None


class _ResourceShortcutIconWorker(QThread):
    """后台抓取 URL 快捷方式图标，避免在 GUI 线程同步等待网络超时。"""

    icon_fetched = pyqtSignal(str, bytes)

    def __init__(self, shortcut_id: str, target: str) -> None:
        super().__init__()
        self.key = str(shortcut_id)
        self._target = str(target or "")

    def run(self) -> None:
        try:
            data = _download_resource_shortcut_logo_bytes(self._target)
        except Exception:
            return
        if data:
            self.icon_fetched.emit(self.key, bytes(data))


class ResourceShortcutsMixin:
    def _show_resource_status(self, text: str, *, tone: str = "info", auto_hide_ms: int = 3600) -> None:
        self._show_inline_status(getattr(self, "_resource_status_label", None), text, tone=tone, auto_hide_ms=auto_hide_ms)

    def _build_resource_tools_page(self) -> QWidget:
        if not hasattr(self, "_last_resource_tool"):
            self._last_resource_tool = "cleaner"

        action_btn = self._make_resource_tool_action_btn()
        page = self._make_page("实用工具", action_widget=action_btn)
        content_layout: QVBoxLayout = page._content_layout  # type: ignore[attr-defined]
        content_layout.setSpacing(18)

        self._resource_status_label = self._make_inline_status_label()
        content_layout.addWidget(self._resource_status_label)

        grid_host = QWidget()
        grid_host.setObjectName("ResourceToolsGrid")
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(18, 14, 18, 14)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(16)
        self._resource_tools_grid = grid
        self._refresh_resource_tools_grid()

        content_layout.addWidget(grid_host, 1)

        bottom_bar = QWidget()
        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(18, 0, 0, 0)
        bottom_layout.setSpacing(0)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self._make_resource_shortcut_add_btn(), 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        content_layout.addWidget(bottom_bar, 0)
        return page

    def _resource_builtin_tools(self) -> list[tuple[str, str, str, str, Callable[[], None]]]:
        return [
            ("磁盘清理", "常规清理 / 空间分析 / 大文件 / 重复文件", "icon_nav_cleaner.svg", "cleaner", self._open_drive_cleaner_window),
            ("休息待办", "提醒、待办和日程", "icon_nav_todo.svg", "todo", self._open_todo_resource_window),
        ]

    def _refresh_resource_tools_grid(self) -> None:
        grid: Optional[QGridLayout] = getattr(self, "_resource_tools_grid", None)
        if grid is None:
            return
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        tiles: list[QToolButton] = []
        for title, subtitle, icon, tool_type, callback in self._resource_builtin_tools():
            click_callback = lambda *_, t=tool_type, cb=callback: self._on_resource_tool_tile_clicked(t, cb)
            tile = self._resource_tool_tile(title, subtitle, icon, click_callback)
            if tool_type == "todo":
                self._todo_tile_widget = tile
            tiles.append(tile)

        self._resource_shortcuts = _normalize_resource_shortcuts(getattr(self, "_resource_shortcuts", []))
        for shortcut in self._resource_shortcuts:
            title = str(shortcut.get("title", ""))
            target = str(shortcut.get("target", ""))
            shortcut_id = str(shortcut.get("id", ""))
            subtitle = target
            btn = self._resource_tool_tile(
                title,
                subtitle,
                self._resource_shortcut_icon(shortcut),
                lambda *_, sc=dict(shortcut): self._open_resource_shortcut(sc),
                shortcut_id,
            )
            btn.setToolTip(f"{title}\n{target}")
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, sid=shortcut_id, button=btn: self._show_resource_shortcut_menu(sid, button.mapToGlobal(pos))
            )
            tiles.append(btn)

        for index, tile in enumerate(tiles):
            grid.addWidget(
                tile,
                index // 5,
                index % 5,
                alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
            )
        for column in range(5):
            grid.setColumnStretch(column, 1)
        grid.setRowStretch(max(3, (len(tiles) + 4) // 5), 1)
        self._grid_tiles = tiles

    def _on_resource_tool_tile_clicked(self, tool_type: str, callback: Callable[[], None]) -> None:
        self._last_resource_tool = tool_type
        self._update_resource_tool_action_btn()
        callback()

    def _make_resource_tool_action_btn(self) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("BtnPrimary")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIconSize(QSize(22, 22))
        btn.setMinimumSize(132, 42)
        btn.setMaximumSize(132, 42)
        self._resource_tool_action_btn = btn
        btn.clicked.connect(self._on_resource_tool_action_clicked)
        self._update_resource_tool_action_btn()
        return btn

    def _make_resource_shortcut_add_btn(self) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName("ResourceShortcutAddButton")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("添加工具快捷方式")
        btn.setAccessibleName("添加工具快捷方式")
        btn.setFixedSize(48, 48)
        btn.setText("+")
        btn.clicked.connect(self._show_add_resource_shortcut_dialog)
        return btn

    def _update_resource_tool_action_btn(self) -> None:
        if not hasattr(self, "_resource_tool_action_btn"):
            return
        last_tool = getattr(self, "_last_resource_tool", "cleaner")
        if last_tool == "todo":
            self._resource_tool_action_btn.setText("休息待办")
            self._resource_tool_action_btn.setIcon(self._asset_icon("icon_nav_todo_white.svg"))
        else:
            self._resource_tool_action_btn.setText("磁盘清理")
            self._resource_tool_action_btn.setIcon(self._asset_icon("icon_nav_cleaner_white.svg"))

    def _on_resource_tool_action_clicked(self) -> None:
        last_tool = getattr(self, "_last_resource_tool", "cleaner")
        if last_tool == "todo":
            self._open_todo_resource_window()
        else:
            self._open_drive_cleaner_window()

    def _resource_tool_tile(self, title: str, subtitle: str, icon: str | QIcon, callback: Callable[[], None], shortcut_id: str = "") -> QToolButton:
        btn = _DraggableTile(shortcut_id)
        btn.setObjectName("ResourceToolTile")
        btn.setText(str(title))
        btn.setToolTip(str(subtitle))
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        if isinstance(icon, QIcon):
            btn.setIcon(icon)
        else:
            self._set_asset_icon(btn, icon)
        btn.setIconSize(QSize(40, 40))
        btn.setFixedSize(92, 92)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda *_: callback())
        return btn

    def _resource_shortcut_icon_cache_dir(self) -> Path:
        path = get_app_dir() / "resource_tool_icons"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _resource_shortcut_icon(self, shortcut: dict[str, str]) -> QIcon:
        icon_path = str(shortcut.get("icon_path", "") or "").strip()
        target_size = 40
        if icon_path and Path(icon_path).exists():
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                if pixmap.width() != target_size or pixmap.height() != target_size:
                    canvas = QPixmap(target_size, target_size)
                    canvas.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(canvas)
                    try:
                        if pixmap.width() > target_size or pixmap.height() > target_size:
                            scaled_pix = pixmap.scaled(
                                target_size,
                                target_size,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation
                            )
                            x = (target_size - scaled_pix.width()) // 2
                            y = (target_size - scaled_pix.height()) // 2
                            painter.drawPixmap(x, y, scaled_pix)
                        else:
                            x = (target_size - pixmap.width()) // 2
                            y = (target_size - pixmap.height()) // 2
                            painter.drawPixmap(x, y, pixmap)
                    finally:
                        painter.end()
                    return QIcon(canvas)
                return QIcon(pixmap)
            return QIcon(icon_path)
        kind = str(shortcut.get("kind", "url"))
        target = str(shortcut.get("target", ""))
        if kind == "app" and target and Path(target).exists():
            try:
                return QFileIconProvider().icon(QFileInfo(target))
            except Exception:
                pass
        return self._asset_icon("icon_nav_resources.svg")

    def _cache_resource_shortcut_icon(self, shortcut_id: str, kind: str, target: str) -> str:
        pixmap: Optional[QPixmap] = None
        try:
            if kind == "app":
                icon = QFileIconProvider().icon(QFileInfo(target))
                candidate = icon.pixmap(96, 96)
                if not candidate.isNull():
                    pixmap = candidate
            else:
                data = _download_resource_shortcut_logo_bytes(target)
                if data:
                    pixmap = QPixmap()
                    if not pixmap.loadFromData(data) or pixmap.isNull():
                        pixmap = None
            if pixmap is None or pixmap.isNull():
                return ""
            path = self._resource_shortcut_icon_cache_dir() / f"{shortcut_id}.png"
            if pixmap.save(str(path), "PNG"):
                return str(path)
        except Exception:
            return ""
        return ""

    def _fetch_resource_shortcut_icon_async(self, shortcut_id: str, target: str) -> None:
        """在后台线程抓取 URL 图标；本地应用图标不涉及网络，仍走同步路径。"""
        key = str(shortcut_id or "")
        if not key:
            return
        pending = getattr(self, "_resource_icon_fetch_ids", None)
        if pending is None:
            pending = set()
            self._resource_icon_fetch_ids = pending
        if key in pending:
            return
        pending.add(key)
        worker = _ResourceShortcutIconWorker(key, str(target or ""))
        _ICON_FETCH_WORKERS.add(worker)
        worker.icon_fetched.connect(self._on_resource_shortcut_icon_fetched)
        worker.finished.connect(lambda w=worker: (_ICON_FETCH_WORKERS.discard(w), pending.discard(w.key)))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_resource_shortcut_icon_fetched(self, shortcut_id: str, data: bytes) -> None:
        try:
            pixmap = QPixmap()
            if not data or not pixmap.loadFromData(data) or pixmap.isNull():
                return
            path = self._resource_shortcut_icon_cache_dir() / f"{shortcut_id}.png"
            if not pixmap.save(str(path), "PNG"):
                return
            shortcuts = _normalize_resource_shortcuts(getattr(self, "_resource_shortcuts", []))
            updated = False
            for item in shortcuts:
                if str(item.get("id")) == str(shortcut_id):
                    item["icon_path"] = str(path)
                    updated = True
                    break
            if not updated:
                return
            self._resource_shortcuts = shortcuts
            if self._persist_resource_shortcuts():
                self._refresh_resource_tools_grid()
        except Exception:
            pass

    def _download_resource_shortcut_logo(self, target: str) -> Optional[QPixmap]:
        data = _download_resource_shortcut_logo_bytes(target)
        if not data:
            return None
        pixmap = QPixmap()
        if pixmap.loadFromData(data) and not pixmap.isNull():
            return pixmap
        return None

    def _show_add_resource_shortcut_dialog(self) -> None:
        dialog = _ResourceShortcutDialog(self, self.styleSheet())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        kind, title, target = dialog.values()
        shortcut_id = hashlib.sha1(f"{kind}\0{target}\0{time.time_ns()}".encode("utf-8", "ignore")).hexdigest()[:16]
        shortcut = {
            "id": shortcut_id,
            "kind": kind,
            "title": title,
            "target": target,
        }
        if kind == "app":
            # 本地图标来自系统 shell，无网络等待，保持同步设置
            icon_path = self._cache_resource_shortcut_icon(shortcut_id, kind, target)
            if icon_path:
                shortcut["icon_path"] = icon_path
        self._resource_shortcuts = _normalize_resource_shortcuts([*getattr(self, "_resource_shortcuts", []), shortcut])
        if self._persist_resource_shortcuts():
            self._refresh_resource_tools_grid()
            self._show_resource_status(f"已添加快捷方式「{title}」。", tone="success")
            if kind != "app":
                self._fetch_resource_shortcut_icon_async(shortcut_id, target)

    def _persist_resource_shortcuts(self) -> bool:
        shortcuts = _normalize_resource_shortcuts(getattr(self, "_resource_shortcuts", []))
        try:
            self._current = update_ui_settings(resource_shortcuts=shortcuts)
            self._app_settings = self._current
            self._resource_shortcuts = shortcuts
            return True
        except Exception as exc:
            self._show_resource_status(f"保存工具快捷方式失败：{exc}", tone="error", auto_hide_ms=5200)
            return False

    def _open_resource_shortcut(self, shortcut: dict[str, str]) -> None:
        kind = str(shortcut.get("kind", "url"))
        target = str(shortcut.get("target", "") or "")
        if not target:
            return
        if kind == "app":
            ok = QDesktopServices.openUrl(QUrl.fromLocalFile(target))
        else:
            ok = QDesktopServices.openUrl(QUrl.fromUserInput(target))
        if not ok:
            self._show_resource_status(f"打开失败：{target}", tone="error", auto_hide_ms=5200)
        else:
            title = str(shortcut.get("title", "") or "快捷方式")
            self._show_resource_status(f"已打开「{title}」。", tone="success", auto_hide_ms=1800)

    def _show_resource_shortcut_menu(self, shortcut_id: str, global_pos: QPoint) -> None:
        if not shortcut_id:
            return
        shortcuts = _normalize_resource_shortcuts(getattr(self, "_resource_shortcuts", []))
        target = next((item for item in shortcuts if str(item.get("id")) == str(shortcut_id)), None)
        if target is None:
            return
        title = str(target.get("title", "快捷方式"))
        popup = _DeleteShortcutPopup(title, lambda sid=shortcut_id: self._delete_resource_shortcut(sid), self)
        popup.show_at(self, global_pos)

    def _delete_resource_shortcut(self, shortcut_id: str) -> None:
        shortcuts = _normalize_resource_shortcuts(getattr(self, "_resource_shortcuts", []))
        target_shortcut = next((item for item in shortcuts if str(item.get("id")) == str(shortcut_id)), None)
        if target_shortcut is None:
            return
        title = str(target_shortcut.get("title", "快捷方式"))
        from deepcat.ui.main_window.compact import StyledMessageBox
        reply = StyledMessageBox.question(
            self,
            "删除快捷方式",
            f"确定删除「{title}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._resource_shortcuts = [item for item in shortcuts if str(item.get("id")) != str(shortcut_id)]
        if self._persist_resource_shortcuts():
            self._refresh_resource_tools_grid()
            self._show_resource_status(f"已删除快捷方式「{title}」。", tone="success")

    def _reorder_resource_shortcut(self, shortcut_id: str, drop_pos_in_main: QPoint) -> None:
        grid = getattr(self, "_resource_tools_grid", None)
        if grid is None or not shortcut_id:
            return
        grid_widget = grid.parentWidget()
        if grid_widget is None:
            return
        pos_in_grid = grid_widget.mapFrom(self, drop_pos_in_main)
        tiles = getattr(self, "_grid_tiles", [])
        if not tiles:
            return
        target_tile_idx = -1
        min_distance = 999999
        for i, tile in enumerate(tiles):
            if tile.isVisible():
                dist = (tile.geometry().center() - pos_in_grid).manhattanLength()
                if dist < min_distance:
                    min_distance = dist
                    target_tile_idx = i
        if target_tile_idx == -1:
            return
        shortcuts = _normalize_resource_shortcuts(getattr(self, "_resource_shortcuts", []))
        src_idx = -1
        for i, sc in enumerate(shortcuts):
            if str(sc.get("id")) == shortcut_id:
                src_idx = i
                break
        if src_idx == -1:
            return
        dragged_item = shortcuts.pop(src_idx)
        dest_idx = max(0, target_tile_idx - 2)
        shortcuts.insert(dest_idx, dragged_item)
        self._resource_shortcuts = shortcuts
        if self._persist_resource_shortcuts():
            self._refresh_resource_tools_grid()
            self._show_resource_status("快捷方式排序已更新。", tone="success")
