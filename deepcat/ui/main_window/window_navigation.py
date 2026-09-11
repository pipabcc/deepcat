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
from deepcat.ui.stable_icons import clear_rendered_icon_cache, load_stable_icon

QTimer = DynamicModuleAttribute("deepcat.ui.main_window.window", "QTimer")


class WindowNavigationMixin:
    def _assets_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent / "assets"

    def _asset_icon(self, name: str, fallback: Optional[QIcon] = None) -> QIcon:
        path = self._assets_dir() / name
        return load_stable_icon(path, fallback)

    def _set_asset_icon(self, target: Any, name: str, fallback: Optional[QIcon] = None) -> None:
        target.setProperty("deepcatAssetIcon", name)
        target.setIcon(self._asset_icon(name, fallback))

    def _refresh_stable_icons(self) -> None:
        clear_rendered_icon_cache()
        for target in self.findChildren(QWidget):
            name = str(target.property("deepcatAssetIcon") or "").strip()
            if name and hasattr(target, "setIcon") and hasattr(target, "icon"):
                target.setIcon(self._asset_icon(name, target.icon()))
        refresh_tray = getattr(self, "_refresh_tray_action_icons", None)
        if callable(refresh_tray):
            refresh_tray()
        self.setWindowIcon(self._camera_icon())

    def _on_application_state_changed_for_icons(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            QTimer.singleShot(80, self._refresh_stable_icons)

    def _make_nav_button(self, text: str, icon_name: str, index: int) -> QPushButton:
        btn = _SidebarNavButton(text)
        btn.setObjectName("NavButton")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_asset_icon(btn, icon_name)
        btn.setIconSize(QSize(24, 24))
        btn.setMinimumHeight(44)
        btn._page_index = int(index)  # type: ignore[attr-defined]
        btn.clicked.connect(lambda _=False, i=index: self._switch_page(i))
        self._nav_buttons.append(btn)
        self._nav_button_by_index[int(index)] = btn
        return btn

    def _feature_enabled(self, key: str) -> bool:
        try:
            ui = dict(getattr(self._current, "ui", {}) or {})
            self._feature_visibility = normalize_feature_visibility(ui.get("feature_visibility"))
        except Exception:
            pass
        return bool((getattr(self, "_feature_visibility", {}) or {}).get(str(key), True))

    def _apply_feature_visibility_to_nav(self) -> None:
        mapping = {
            2: "todo",
            3: "later_read",
            4: "clipboard_history",
            5: "table_notes",
        }
        for index, key in mapping.items():
            btn = self._nav_button_by_index.get(index)
            if btn is not None:
                btn.setVisible(bool(self._feature_enabled(key)))
        try:
            if self._stack is not None:
                current = int(self._stack.currentIndex())
                key = mapping.get(current)
                if key and not self._feature_enabled(key):
                    self._switch_page(0)
        except Exception:
            pass

    def _coerce_remembered_resizable_page_size(self, value: Any) -> Optional[QSize]:
        try:
            if isinstance(value, QSize):
                w = int(value.width())
                h = int(value.height())
            elif isinstance(value, dict):
                w = int(value.get("width", value.get("w", 0)))
                h = int(value.get("height", value.get("h", 0)))
            elif isinstance(value, (list, tuple)) and len(value) >= 2:
                w = int(value[0])
                h = int(value[1])
            else:
                return None
        except Exception:
            return None
        if w <= 0 or h <= 0:
            return None
        w = max(self._DEFAULT_WINDOW_WIDTH, min(w, self._MAX_WINDOW_EXTENT))
        h = max(self._DEFAULT_WINDOW_HEIGHT, min(h, self._MAX_WINDOW_EXTENT))
        return QSize(w, h)

    def _load_remembered_resizable_page_sizes(self, ui: dict[str, Any]) -> dict[int, QSize]:
        raw = ui.get("resizable_page_sizes", {})
        raw_map = dict(raw) if isinstance(raw, dict) else {}
        shared_size: Optional[QSize] = None
        for index, key in self._REMEMBERED_RESIZABLE_PAGE_KEYS.items():
            shared_size = self._coerce_remembered_resizable_page_size(raw_map.get(key))
            if shared_size is None:
                shared_size = self._coerce_remembered_resizable_page_size(raw_map.get(str(index)))
            if shared_size is not None:
                break
        if shared_size is None:
            return {}
        return {
            int(index): QSize(int(shared_size.width()), int(shared_size.height()))
            for index in self._REMEMBERED_RESIZABLE_PAGE_KEYS
        }

    def _set_remembered_resizable_page_size(self, size: QSize) -> None:
        normalized = self._coerce_remembered_resizable_page_size(size)
        if normalized is None:
            return
        for index in self._REMEMBERED_RESIZABLE_PAGE_KEYS:
            self._remembered_resizable_page_sizes[int(index)] = QSize(
                int(normalized.width()),
                int(normalized.height()),
            )

    def _remembered_resizable_page_size(self, index: int) -> QSize:
        size = self._remembered_resizable_page_sizes.get(int(index))
        if size is None:
            return QSize(self._DEFAULT_WINDOW_WIDTH, self._DEFAULT_WINDOW_HEIGHT)
        return QSize(int(size.width()), int(size.height()))

    def _remember_current_resizable_page_size(self, index: Optional[int] = None) -> None:
        try:
            if index is None:
                index = self._stack.currentIndex() if self._stack is not None else 0
            index = int(index)
            if index not in self._REMEMBERED_RESIZABLE_PAGE_INDICES:
                return
            if self.isMaximized() or self.isMinimized():
                return
            size = self._coerce_remembered_resizable_page_size({"width": self.width(), "height": self.height()})
            if size is not None:
                self._set_remembered_resizable_page_size(size)
        except Exception:
            pass

    def _apply_remembered_resizable_page_size(self, index: int) -> None:
        try:
            index = int(index)
            if index not in self._REMEMBERED_RESIZABLE_PAGE_INDICES:
                return
            if self.isMaximized():
                return
            size = self._remembered_resizable_page_size(index)
            if self.width() != size.width() or self.height() != size.height():
                self.resize(size)
        except Exception:
            pass

    def _remembered_resizable_page_sizes_payload(self) -> dict[str, dict[str, int]]:
        payload: dict[str, dict[str, int]] = {}
        for index, key in self._REMEMBERED_RESIZABLE_PAGE_KEYS.items():
            size = self._coerce_remembered_resizable_page_size(
                self._remembered_resizable_page_sizes.get(index)
            )
            if size is None:
                continue
            payload[key] = {"width": int(size.width()), "height": int(size.height())}
        return payload

    def _is_resizable_page_index(self, index: Optional[int] = None) -> bool:
        try:
            if index is None:
                index = self._stack.currentIndex() if self._stack is not None else 0
            return int(index) in self._RESIZABLE_PAGE_INDICES
        except Exception:
            return False

    def _normal_persisted_page_index(self, index: int) -> int:
        index = int(index)
        if index == self._USAGE_GUIDE_PAGE_INDEX:
            return self._ABOUT_PAGE_INDEX
        return index

    def _paint_widget_background(self, widget: Optional[QWidget], color: str) -> None:
        if widget is None:
            return
        try:
            palette = widget.palette()
            qcolor = QColor(color)
            palette.setColor(QPalette.ColorRole.Window, qcolor)
            palette.setColor(QPalette.ColorRole.Base, qcolor)
            widget.setPalette(palette)
            widget.setAutoFillBackground(True)
            widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        except Exception:
            pass

    def _apply_solid_window_backgrounds(self) -> None:
        background = MAIN_WINDOW_BACKGROUND
        self._paint_widget_background(self, background)
        self._paint_widget_background(self.centralWidget(), background)
        self._paint_widget_background(getattr(self, "_sidebar", None), background) # 固化侧边栏实体背景，根除因重列表加载引发的侧边栏闪黑屏
        self._paint_widget_background(getattr(self, "_stack", None), background)
        try:
            current = self._stack.currentWidget() if self._stack is not None else None
        except Exception:
            current = None
        self._paint_widget_background(current, background)
        if current is None:
            return
        try:
            for area in current.findChildren(QScrollArea):
                self._paint_widget_background(area, background)
                self._paint_widget_background(area.viewport(), background)
                self._paint_widget_background(area.widget(), background)
            for stack in current.findChildren(QStackedWidget):
                self._paint_widget_background(stack, background)
                self._paint_widget_background(stack.currentWidget(), background)
        except Exception:
            pass

    def _sync_page_right_edge_guard(self) -> None:
        try:
            if self._stack is None or self._stack.currentIndex() not in self._RESIZABLE_PAGE_INDICES:
                return
            current = self._stack.currentWidget()
            if current is None:
                return
            page_scroll = None
            for area in current.findChildren(QScrollArea):
                if area.objectName() == "PageScroll" and area.isVisible():
                    page_scroll = area
                    break
            if page_scroll is None:
                return
            guard = getattr(current, "_page_right_edge_guard", None)
            if guard is None:
                guard = QWidget(current)
                guard.setObjectName("PageRightEdgeGuard")
                guard.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                guard.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                guard.setAutoFillBackground(True)
                guard.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                palette = guard.palette()
                palette.setColor(QPalette.ColorRole.Window, QColor(MAIN_WINDOW_BACKGROUND))
                guard.setPalette(palette)
                guard.setStyleSheet(f"QWidget#PageRightEdgeGuard {{ background-color: {MAIN_WINDOW_BACKGROUND}; border: none; }}")
                setattr(current, "_page_right_edge_guard", guard)
            top_left = page_scroll.mapTo(current, QPoint(max(0, page_scroll.width() - 1), 0))
            guard.setGeometry(top_left.x(), top_left.y(), 3, page_scroll.height())
            guard.raise_()
            guard.show()
            guard.update()
        except Exception:
            pass

    def _stabilize_window_backing_store(self) -> None:
        try:
            self._apply_solid_window_backgrounds()
            self._sync_page_right_edge_guard()
            for widget in (
                self,
                self.centralWidget(),
                getattr(self, "_sidebar", None),
                getattr(self, "_stack", None),
                self._stack.currentWidget() if self._stack is not None else None,
            ):
                if widget is None:
                    continue
                widget.updateGeometry()
                widget.update()
                widget.repaint()
        except Exception:
            pass

    def _set_window_resize_mode(self, resizable: bool, *, schedule_stabilize: bool = True) -> None:
        self._apply_solid_window_backgrounds()

        current_resizable = getattr(self, "_current_resize_mode", None)
        if current_resizable == resizable:
            if not resizable and self.width() == self._DEFAULT_WINDOW_WIDTH and self.height() == self._DEFAULT_WINDOW_HEIGHT:
                return
            if resizable:
                return

        self._current_resize_mode = resizable
        previous_updates_enabled = self.updatesEnabled()
        self.setUpdatesEnabled(False)
        try:
            if bool(resizable):
                self.setMinimumSize(self._DEFAULT_WINDOW_WIDTH, self._DEFAULT_WINDOW_HEIGHT)
                self.setMaximumSize(self._MAX_WINDOW_EXTENT, self._MAX_WINDOW_EXTENT)
                if self.width() < self._DEFAULT_WINDOW_WIDTH or self.height() < self._DEFAULT_WINDOW_HEIGHT:
                    self.resize(
                        max(self.width(), self._DEFAULT_WINDOW_WIDTH),
                        max(self.height(), self._DEFAULT_WINDOW_HEIGHT),
                    )
            else:
                if self.isVisible() and self.isMaximized():
                    self.showNormal()
                self.setFixedSize(self._DEFAULT_WINDOW_WIDTH, self._DEFAULT_WINDOW_HEIGHT)
        finally:
            if previous_updates_enabled:
                self.setUpdatesEnabled(True)
                self.update()

        if bool(schedule_stabilize) and self.isVisible() and not self.isMinimized():
            QTimer.singleShot(0, self._stabilize_window_backing_store)

    def _switch_page(self, index: int) -> None:
        if self._stack is None:
            return

        # 手动切去非搜索页时，静默清空搜索输入框
        if index != self._SEARCH_PAGE_INDEX and hasattr(self, "_super_search") and self._super_search is not None:
            self._is_clearing_search = True
            try:
                self._super_search.clear()
            finally:
                self._is_clearing_search = False

        old_index = self._stack.currentIndex()
        index = max(0, min(int(index), self._stack.count() - 1))
        self._remember_current_resizable_page_size(old_index)
        if old_index == 4 and hasattr(self, "_clipboard_history_page"):
            try:
                self._clipboard_history_page.on_page_hidden()
            except Exception:
                pass
        freeze_capture_first_paint = bool(index == 0 and old_index != index and self.isVisible())
        previous_updates_enabled = self.updatesEnabled()
        if freeze_capture_first_paint:
            self.setUpdatesEnabled(False)
        try:
            self._stack.setCurrentIndex(index)
            current_widget = self._stack.currentWidget()
            if current_widget is not None:
                current_widget.setFocus()
            if index == 0:
                self._stabilize_capture_page_layout()
            if index == 3 and getattr(self, "_later_read_list_dirty", False):
                try:
                    self._refresh_later_read_list()
                except Exception:
                    pass
            for i, btn in enumerate(self._nav_buttons):
                page_index = int(getattr(btn, "_page_index", i))
                btn.setChecked(page_index == index or (index == self._USAGE_GUIDE_PAGE_INDEX and page_index == self._ABOUT_PAGE_INDEX))
            if index == 4 and hasattr(self, "_clipboard_history_page"):
                try:
                    self._clipboard_history_page.on_page_shown()
                except Exception:
                    pass
            self._set_window_resize_mode(self._is_resizable_page_index(index))
            self._apply_remembered_resizable_page_size(index)
            if index == 0:
                self._stabilize_capture_page_layout()
            self._sync_page_right_edge_guard()
        finally:
            if freeze_capture_first_paint and previous_updates_enabled:
                self.setUpdatesEnabled(True)
                self.update()
        QTimer.singleShot(0, self._sync_page_right_edge_guard)
        if index == int(getattr(self, "_NOTE_OUTLINE_PAGE_INDEX", -1)):
            schedule_tab_visibility = getattr(self, "_schedule_active_table_notes_tab_visibility", None)
            if callable(schedule_tab_visibility):
                schedule_tab_visibility()
        self._schedule_hover_cursor_refresh()

    def _make_start_button(self) -> QPushButton:
        btn = QPushButton("开始截图")
        btn.setObjectName("BtnPrimary")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIcon(self._asset_icon("icon_nav_camera_white.svg", self._asset_icon("icon_nav_camera.svg")))
        btn.setIconSize(QSize(24, 24))
        btn.setMinimumSize(132, 42)
        btn.setMaximumSize(132, 42)
        btn.clicked.connect(lambda *_: self._start_capture_clicked(from_tray=False))
        return btn

    def _make_usage_guide_button(self) -> QPushButton:
        btn = QPushButton("使用指南")
        btn.setObjectName("BtnPrimary")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIcon(self._asset_icon("icon_nav_info_white.svg", self._asset_icon("icon_nav_info.svg")))
        btn.setIconSize(QSize(24, 24))
        btn.setMinimumSize(132, 42)
        btn.setMaximumSize(132, 42)
        btn.clicked.connect(self._open_usage_guide_page)
        return btn

    def _make_pin_button(self, page_type: str) -> QPushButton:
        btn = QPushButton("快捷浮窗")
        btn.setObjectName("BtnPrimary")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIcon(self._asset_icon("icon_pin_white.svg"))
        btn.setIconSize(QSize(20, 20))
        btn.setMinimumSize(132, 42)
        btn.setMaximumSize(132, 42)
        btn.clicked.connect(lambda *_: self._open_compact_list_window(page_type))
        return btn

    def _make_folder_button(self) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("BtnFolder")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("打开截图目录")
        btn.setIcon(self._asset_icon("icon_folder_white.svg", self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)))
        btn.setIconSize(QSize(25, 25))
        btn.clicked.connect(self._open_files_dir)
        return btn

    def _make_sidebar_folder_button(self) -> QPushButton:
        btn = QPushButton("截图目录")
        btn.setObjectName("BtnSidebarFolder")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIcon(self._asset_icon("icon_folder.svg", self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)))
        btn.setIconSize(QSize(20, 20))
        btn.setMinimumHeight(44)
        btn.clicked.connect(self._open_files_dir)
        return btn

    def _header_subtitle(self, title: str) -> str:
        subtitles = {
            "截图设置": "配置截图、滚动截屏、录屏和框选后动作",
            "设置": "管理系统启动、提示通知、功能模块与划词辅助选项",
            "基本设置": "管理启动、通知、功能入口和快捷键",
            "模型管理": "管理翻译、问答与外部工具模型配置",
            "实用工具": "打开网络检测、清理和本地辅助工具",
            "休息待办": "安排休息提醒和轻量待办事项",
            "稍后阅读": "收集待读内容并按关键词过滤",
            "表格记事": "管理表格、富文本笔记和分组资料",
            "关于": "查看版本、更新、日志和隐私信息",
            "使用指南": "按目录快速查找功能操作步骤",
            "全局超级搜索": "跨复制记录、笔记、稍后阅读和 AI 历史搜索",
            "数据管理": "备份、恢复、清理本地应用数据",
        }
        return subtitles.get(str(title or "").strip(), "多功能截图、OCR识别、AI助手和效率工具")

    def _make_header(self, title: str, *, brand: bool = False, title_extra: Optional[QWidget] = None, action_widget: Optional[QWidget] = None) -> QWidget:
        header = QWidget()
        header.setObjectName("PageHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        title_box = QWidget()
        title_layout = QVBoxLayout(title_box)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(2)

        if title == "截图设置":
            title_label = _DoubleClickLabel(title)
            title_label.setToolTip("双击恢复默认设置")
            title_label.setCursor(Qt.CursorShape.PointingHandCursor)
            title_label.doubleClicked.connect(self._restore_capture_settings_defaults)
        else:
            title_label = QLabel(title)

        title_label.setObjectName("HeaderTitle")
        subtitle = QLabel(self._header_subtitle(title))
        subtitle.setObjectName("HeaderSubtitle")
        subtitle.setWordWrap(False)
        if title_extra is not None:
            title_row = QWidget()
            title_row_layout = QHBoxLayout(title_row)
            title_row_layout.setContentsMargins(0, 0, 0, 0)
            title_row_layout.setSpacing(8)
            title_row_layout.addWidget(title_label)
            title_row_layout.addWidget(title_extra)
            title_row_layout.addStretch(1)
            title_layout.addWidget(title_row)
        else:
            # 左对齐添加：标题标签只占文字宽度，避免在 QVBoxLayout 中被横向拉伸到整行。
            # 否则 "截图设置" 标题（_DoubleClickLabel 带 PointingHandCursor）会铺满表头，
            # 导致标题文字右侧的空白区域也显示小手光标（应为箭头）。
            title_layout.addWidget(title_label, alignment=Qt.AlignmentFlag.AlignLeft)
        title_layout.addWidget(subtitle)
        layout.addWidget(title_box, 1)
        layout.addWidget(action_widget if action_widget is not None else self._make_start_button())
        return header

    def _make_page(self, title: str, *, brand: bool = False, title_extra: Optional[QWidget] = None, action_widget: Optional[QWidget] = None) -> QWidget:
        page = QWidget()
        page.setObjectName("AppPage")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        page.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)
        outer.addWidget(self._make_header(title, brand=brand, title_extra=title_extra, action_widget=action_widget))
        body = QScrollArea()
        body.setObjectName("PageScroll")
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body.setWidgetResizable(True)
        body.setFrameShape(QFrame.Shape.NoFrame)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.viewport().setObjectName("PageScrollViewport")
        body.viewport().setAutoFillBackground(True)
        body.viewport().setStyleSheet(f"QWidget#PageScrollViewport {{ background: {MAIN_WINDOW_BACKGROUND}; }}")
        content = QWidget()
        content.setObjectName("PageContent")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)
        body.setWidget(content)
        outer.addWidget(body, 1)
        page._content_layout = content_layout  # type: ignore[attr-defined]
        return page

    def _make_inline_status_label(self) -> QLabel:
        label = QLabel()
        label.setObjectName("InlinePageStatus")
        label.setWordWrap(False)
        label.setVisible(False)
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return label

    def _prepare_floating_status_label(self, label: QLabel) -> Optional[QWidget]:
        parent = label.parentWidget()
        if parent is None:
            return None
        if bool(label.property("floatingStatusPrepared")):
            return parent
        y = 10
        layout = parent.layout()
        if layout is not None:
            index = layout.indexOf(label)
            spacing = max(4, int(layout.spacing() if layout.spacing() >= 0 else 6))
            if index > 0:
                for i in range(index - 1, -1, -1):
                    item = layout.itemAt(i)
                    widget = item.widget() if item is not None else None
                    if widget is not None and widget.isVisible():
                        y = int(widget.geometry().bottom()) + 1 + spacing
                        break
            layout.removeWidget(label)
        label.setParent(parent)
        label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        label.setProperty("floatingStatusPrepared", True)
        label.setProperty("floatingStatusY", max(8, y))
        return parent

    def _show_inline_status(
        self,
        label: Optional[QLabel],
        text: str,
        *,
        tone: str = "info",
        auto_hide_ms: int = 3600,
    ) -> None:
        if not isinstance(label, QLabel):
            return
        message = str(text or "").strip()
        if not message:
            label.hide()
            return
        parent = self._prepare_floating_status_label(label)
        if parent is None:
            return
        palette = {
            "success": ("#166534", "#f0fdf4", "#bbf7d0"),
            "error": ("#991b1b", "#fef2f2", "#fecaca"),
            "warning": ("#92400e", "#fffbeb", "#fde68a"),
            "info": ("#334155", "#f8fafc", "#cbd5e1"),
        }
        color, background, border = palette.get(str(tone or "info"), palette["info"])
        token = int(label.property("statusToken") or 0) + 1
        label.setProperty("statusToken", token)
        label.setText(message)
        label.setStyleSheet(
            "QLabel#InlinePageStatus {"
            f" color: {color}; background: {background}; border: 1px solid {border};"
            " border-radius: 8px; padding: 7px 10px; font-size: 12px;"
            "}"
        )
        max_width = max(120, int(parent.width()) - 24)
        label.setMinimumSize(0, 0)
        label.setMaximumSize(16777215, 16777215)
        label.setMaximumWidth(max_width)
        label.setWordWrap(False)
        label.adjustSize()
        hint = label.sizeHint()
        if int(hint.width()) > max_width:
            label.setWordWrap(True)
            label.setFixedWidth(max_width)
            label.adjustSize()
            width = max_width
            height = max(28, int(label.sizeHint().height()))
        else:
            width = max(80, int(hint.width()))
            height = max(28, int(hint.height()))
            label.setFixedSize(width, height)
        x = max(8, int((parent.width() - width) / 2))
        y = int(label.property("floatingStatusY") or 10)
        if y + height > int(parent.height()) - 8:
            y = 10
        label.setGeometry(x, y, width, height)
        label.raise_()
        label.show()
        if int(auto_hide_ms or 0) > 0:
            QTimer.singleShot(
                int(auto_hide_ms),
                lambda current=token, target=label: target.hide()
                if isinstance(target, QLabel) and int(target.property("statusToken") or 0) == current
                else None,
            )

    def _show_about_status(self, text: str, *, tone: str = "info", auto_hide_ms: int = 3600) -> None:
        self._show_inline_status(getattr(self, "_about_status_label", None), text, tone=tone, auto_hide_ms=auto_hide_ms)

    def _show_general_status(self, text: str, tone: str = "info", *, auto_hide_ms: int = 3600) -> None:
        self._show_capture_status(text, tone=tone, auto_hide_ms=auto_hide_ms)

    def _show_update_status(self, text: str, tone: str = "info", *, auto_hide_ms: int = 3600) -> None:
        self._show_about_status(text, tone=tone, auto_hide_ms=auto_hide_ms)

    def _card(self, title: str) -> tuple[QGroupBox, QVBoxLayout]:
        card = QGroupBox()
        card.setObjectName("SectionGroup")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(5)
        return card, layout

    def _make_unified_empty_state(
        self,
        title: str,
        description: str,
        action_text: str,
        action_callback: Optional[Callable[[], None]] = None,
        *,
        compact: bool = False,
    ) -> QWidget:
        box = QWidget()
        box.setObjectName("UnifiedEmptyState")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        box.setMinimumHeight(48 if compact else 124)
        box.setStyleSheet("""
            QWidget#UnifiedEmptyState {
                background: transparent;
            }
            QLabel#UnifiedEmptyTitle {
                color: #1e293b;
                font-size: 14px;
                font-weight: 800;
            }
            QLabel#UnifiedEmptyDescription {
                color: #64748b;
                font-size: 12px;
            }
            QPushButton#UnifiedEmptyAction {
                background: #f1f5f9;
                color: #1e293b;
                border: none;
                border-radius: 8px;
                padding: 5px 14px;
                font-size: 12px;
                font-weight: 800;
                min-height: 26px;
            }
            QPushButton#UnifiedEmptyAction:hover {
                background: #e2e8f0;
                color: #0f172a;
            }
            QPushButton#UnifiedEmptyAction:pressed {
                background: #cbd5e1;
            }
        """)

        if compact:
            layout = QHBoxLayout(box)
            layout.setContentsMargins(12, 6, 12, 6)
            layout.setSpacing(8)
            layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        else:
            layout = QVBoxLayout(box)
            layout.setContentsMargins(12, 14, 12, 14)
            layout.setSpacing(7)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_label = QLabel()
        title_label.setObjectName("UnifiedEmptyTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter if compact else Qt.AlignmentFlag.AlignCenter)
        title_label.setWordWrap(not compact)
        if compact:
            title_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(title_label)

        desc_label = QLabel()
        desc_label.setObjectName("UnifiedEmptyDescription")
        desc_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter if compact else Qt.AlignmentFlag.AlignCenter)
        desc_label.setWordWrap(not compact)
        if compact:
            desc_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(desc_label, 1 if compact else 0)

        action_btn = QPushButton()
        action_btn.setObjectName("UnifiedEmptyAction")
        action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        action_btn.clicked.connect(lambda *_: self._run_unified_empty_state_action(action_btn))
        layout.addWidget(action_btn, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter if compact else Qt.AlignmentFlag.AlignCenter)

        box._empty_title_label = title_label  # type: ignore[attr-defined]
        box._empty_description_label = desc_label  # type: ignore[attr-defined]
        box._empty_action_btn = action_btn  # type: ignore[attr-defined]
        self._set_unified_empty_state(box, title, description, action_text, action_callback)
        return box

    def _set_unified_empty_state(
        self,
        box: QWidget,
        title: str,
        description: str,
        action_text: str,
        action_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        title_label = getattr(box, "_empty_title_label", None)
        desc_label = getattr(box, "_empty_description_label", None)
        action_btn = getattr(box, "_empty_action_btn", None)
        if isinstance(title_label, QLabel):
            title_label.setText(str(title or ""))
        if isinstance(desc_label, QLabel):
            desc_label.setText(str(description or ""))
        if isinstance(action_btn, QPushButton):
            action_btn.setText(str(action_text or ""))
            action_btn.setVisible(bool(action_text))
            action_btn._empty_action_callback = action_callback  # type: ignore[attr-defined]

    def _run_unified_empty_state_action(self, button: QPushButton) -> None:
        callback = getattr(button, "_empty_action_callback", None)
        if callable(callback):
            callback()

    def _form_card(self, title: str) -> tuple[QGroupBox, QFormLayout]:
        card, layout = self._card(title)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(form)
        return card, form

    def _row(self, *widgets: QWidget, stretch_last: bool = True) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for widget in widgets:
            layout.addWidget(widget)
        if stretch_last:
            layout.addStretch(1)
        return row

    def _compact_child_layouts(self, root: QWidget) -> None:
        try:
            if root.layout() is not None:
                root.layout().setContentsMargins(0, 0, 0, 0)
                root.layout().setSpacing(6)
            for group in root.findChildren(QGroupBox):
                layout = group.layout()
                if layout is not None:
                    layout.setContentsMargins(9, 8, 9, 7)
                    layout.setSpacing(5)
            for form in root.findChildren(QFormLayout):
                form.setHorizontalSpacing(8)
                form.setVerticalSpacing(5)
        except Exception:
            pass

    def _build_basic_page(self) -> QWidget:
        page = self._make_page("基本设置")
        content_layout: QVBoxLayout = page._content_layout  # type: ignore[attr-defined]
        ui = dict(getattr(self._app_settings, "ui", {}) or {})

        behavior, behavior_form = self._form_card("常规设置")
        behavior_form.setVerticalSpacing(12)
        content_layout.addWidget(behavior)
        return page

    def _drive_cleaner_qa_runtime_config(self) -> dict[str, Any]:
        try:
            settings = load_settings()
            translator = normalize_translator_settings((getattr(settings, "ui", {}) or {}).get("translator"))
            model_name = str(translator.get("qa_model") or translator.get("current_model") or "").strip()
            configs = dict(translator.get("model_configs") or {})
            cfg = dict(configs.get(model_name) or {})
            cfg["model_type"] = infer_translator_model_type(model_name, cfg)
            cfg["display_name"] = model_name
            model_type = str(cfg.get("model_type", "glm") or "glm").lower()
            if model_type in {"microsoft_free", "google_free", "deeplx"}:
                return {
                    "ok": False,
                    "error": "当前问答模型选择的是翻译专用模型，请在模型管理中选择可问答的大模型",
                    "model_name": model_name,
                    "cfg": cfg,
                    "use_proxy": False,
                    "proxy_url": "",
                }
            missing = [
                label
                for key, label in (("base_url", "API地址"), ("model_name", "模型名称"), ("api_key", "API密钥"))
                if not str(cfg.get(key, "") or "").strip()
            ]
            if missing:
                return {
                    "ok": False,
                    "error": f"问答模型「{model_name or '未选择'}」配置不完整，缺少：" + "、".join(missing),
                    "model_name": model_name,
                    "cfg": cfg,
                    "use_proxy": False,
                    "proxy_url": "",
                }
            return {
                "ok": True,
                "error": "",
                "model_name": model_name,
                "cfg": cfg,
                "use_proxy": bool(cfg.get("use_proxy", False)),
                "proxy_url": str(translator.get("proxy_url", "socks5://127.0.0.1:1080") or "socks5://127.0.0.1:1080"),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"读取模型管理中的问答模型失败：{exc}",
                "model_name": "",
                "cfg": {},
                "use_proxy": False,
                "proxy_url": "",
            }

    def _add_dragged_shortcut(self, local_path: str) -> None:
        local_path = str(local_path).strip()
        if not local_path:
            self._show_resource_status("拖入的路径无效。", tone="warning")
            return
        kind = "app"
        target = local_path
        if local_path.lower().endswith(".url"):
            try:
                import configparser
                config = configparser.ConfigParser()
                config.read(local_path, encoding="utf-8")
                if "InternetShortcut" in config and "URL" in config["InternetShortcut"]:
                    target = config["InternetShortcut"]["URL"]
                    kind = "url"
            except Exception:
                pass
        kind = _normalize_resource_shortcut_kind(kind)
        target = _normalize_resource_shortcut_target(kind, target)
        if not target:
            self._show_resource_status("拖入的路径无效，无法添加快捷方式。", tone="warning")
            return
        title = _resource_shortcut_default_title(kind, target)
        title = title[:40]
        shortcuts = _normalize_resource_shortcuts(getattr(self, "_resource_shortcuts", []))
        if any(str(item.get("target")).strip().lower() == target.lower() for item in shortcuts):
            self._show_resource_status("该快捷方式已存在。", tone="info")
            return
        if len(shortcuts) >= _RESOURCE_SHORTCUT_MAX_ITEMS:
            self._show_resource_status(f"工具快捷方式已达上限（最多 {_RESOURCE_SHORTCUT_MAX_ITEMS} 个）。", tone="warning")
            return
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
        self._resource_shortcuts = _normalize_resource_shortcuts([*shortcuts, shortcut])
        if self._persist_resource_shortcuts():
            self._refresh_resource_tools_grid()
            self._show_resource_status(f"已添加快捷方式「{title}」。", tone="success")
            if kind != "app":
                self._fetch_resource_shortcut_icon_async(shortcut_id, target)

    def _add_dragged_url(self, url_str: str) -> None:
        url_str = str(url_str).strip()
        if not url_str:
            self._show_resource_status("拖入的网址无效。", tone="warning")
            return
        kind = "url"
        target = _normalize_resource_shortcut_target(kind, url_str)
        if not target:
            self._show_resource_status("拖入的网址无效，无法添加快捷方式。", tone="warning")
            return
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            self._show_resource_status("请输入有效的网址 URL。", tone="warning")
            return
        title = _resource_shortcut_default_title(kind, target)
        title = title[:40]
        shortcuts = _normalize_resource_shortcuts(getattr(self, "_resource_shortcuts", []))
        if any(str(item.get("target")).strip().lower() == target.lower() for item in shortcuts):
            self._show_resource_status("该快捷方式已存在。", tone="info")
            return
        if len(shortcuts) >= _RESOURCE_SHORTCUT_MAX_ITEMS:
            self._show_resource_status(f"工具快捷方式已达上限（最多 {_RESOURCE_SHORTCUT_MAX_ITEMS} 个）。", tone="warning")
            return
        shortcut_id = hashlib.sha1(f"{kind}\0{target}\0{time.time_ns()}".encode("utf-8", "ignore")).hexdigest()[:16]
        shortcut = {
            "id": shortcut_id,
            "kind": kind,
            "title": title,
            "target": target,
        }
        self._resource_shortcuts = _normalize_resource_shortcuts([*shortcuts, shortcut])
        if self._persist_resource_shortcuts():
            self._refresh_resource_tools_grid()
            self._show_resource_status(f"已添加快捷方式「{title}」。", tone="success")
            self._fetch_resource_shortcut_icon_async(shortcut_id, target)

    def _open_drive_cleaner_window(self) -> None:
        window = getattr(self, "_drive_cleaner_window", None)
        try:
            if window is not None:
                window.show()
                window.raise_()
                window.activateWindow()
                return
        except RuntimeError:
            pass
        window = _DriveCleanerWindow(self)
        self._drive_cleaner_window = window
        window.show()
        window.raise_()
        window.activateWindow()
