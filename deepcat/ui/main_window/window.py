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
from deepcat.ui.main_window.window_content_pages import WindowContentPagesMixin
from deepcat.ui.main_window.window_embedded_settings import WindowEmbeddedSettingsMixin
from deepcat.ui.main_window.window_navigation import WindowNavigationMixin
from deepcat.ui.main_window.window_runtime_state import WindowRuntimeStateMixin
from deepcat.ui.main_window.window_search import WindowSearchMixin
from deepcat.ui.main_window.window_shell import WindowShellMixin


class MainWindow(
    WindowShellMixin,
    WindowNavigationMixin,
    WindowContentPagesMixin,
    WindowEmbeddedSettingsMixin,
    WindowRuntimeStateMixin,
    WindowSearchMixin,
    QMainWindow,
    CaptureMixin,
    TrayMixin,
    TranslatorMixin,
    TodoMixin,
    LaterReadMixin,
    NotesMixin,
    CatReminderMixin,
    ResourceShortcutsMixin,
):
    _RESIZABLE_PAGE_INDICES = {3, 4, 5, 7}
    _REMEMBERED_RESIZABLE_PAGE_INDICES = {3, 4, 5}
    _REMEMBERED_RESIZABLE_PAGE_KEYS = {
        3: "later_read",
        4: "clipboard_history",
        5: "table_notes",
    }
    _ABOUT_PAGE_INDEX = 6
    _SEARCH_PAGE_INDEX = 7
    _DATA_MANAGEMENT_PAGE_INDEX = 8
    _USAGE_GUIDE_PAGE_INDEX = 9
    _SETTINGS_PAGE_INDEX = 10
    _DEFAULT_WINDOW_WIDTH = 800
    _DEFAULT_WINDOW_HEIGHT = 520
    _MAX_WINDOW_EXTENT = 16777215
    _DEFAULT_TABLE_ROWS = 49
    _DEFAULT_TABLE_COLUMNS = 5
    _DEFAULT_TABLE_COLUMN_WIDTH = 115
    _IMA_AUTO_SYNC_DELAY_MS = 5000

    _capture_requested = pyqtSignal(bool)
    _scroll_capture_requested = pyqtSignal(bool)
    _retake_capture_requested = pyqtSignal(float, float)
    _todo_hotkey_signal = pyqtSignal()
    _later_read_hotkey_signal = pyqtSignal()
    _ai_qa_hotkey_signal = pyqtSignal()
    _pinned_hotkey_signal = pyqtSignal()
    _note_float_hotkey_signal = pyqtSignal()
    _clipboard_float_hotkey_signal = pyqtSignal()
    _later_read_float_hotkey_signal = pyqtSignal()

    def __init__(self) -> None:
        write_crash_breadcrumb("MainWindow.__init__.start")
        super().__init__(None)
        self._prime_startup_background()
        self.setWindowTitle("DeepCat - 日常效率工具箱")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setMinimumWidth(480)
        self.setWindowIcon(self._camera_icon())
        self._allow_quit = False

        self._cfg = Config()
        self._app_settings: AppSettings = load_settings()
        try:
            validate_settings_output_dirs_async(self._app_settings)
        except Exception:
            pass
        self._current: AppSettings = self._app_settings
        initial_ui = dict(getattr(self._app_settings, "ui", {}) or {})
        self._resource_shortcuts = _normalize_resource_shortcuts(initial_ui.get("resource_shortcuts"))
        self._remembered_resizable_page_sizes = self._load_remembered_resizable_page_sizes(initial_ui)
        self._table_notes_store = TableNotesStore()
        self._active_pinned_tables = {}
        self._active_pinned_notes = {}
        old_table_notes = initial_ui.get("table_notes")
        if isinstance(old_table_notes, dict) and self._table_notes_store.is_empty():
            self._table_notes_store.migrate_from_settings(old_table_notes)
        self._translator_loading: bool = False
        self._translator = normalize_translator_settings(initial_ui.get("translator"))
        self._feature_visibility = normalize_feature_visibility(initial_ui.get("feature_visibility"))
        self._translator_test_worker: Optional[TranslatorConnectionTestWorker] = None
        self._proxy_test_worker: Optional[ProxyConnectionTestWorker] = None
        self._update_check_worker: Optional[UpdateCheckWorker] = None
        self._update_download_worker: Optional[UpdateDownloadWorker] = None
        self._update_download_info = None
        self._update_download_manual: bool = False
        self._annotation_style = normalize_annotation_style(initial_ui.get("annotation_style"))
        self._todo_store = TodoStore()
        old_todo = initial_ui.get("todo_items")
        if isinstance(old_todo, list) and old_todo and self._todo_store.is_empty():
            self._todo_store.migrate_from_settings(normalize_todo_items(old_todo))
        self._todo_items: list[dict[str, Any]] = self._todo_store.load_items()
        self._later_read_store = LaterReadStore()
        old_later_read = initial_ui.get("later_read")
        if isinstance(old_later_read, dict) and self._later_read_store.is_empty():
            self._later_read_store.migrate_from_settings(old_later_read)
        self._later_read: dict[str, Any] = normalize_later_read_settings(self._later_read_store.load_settings())
        self._later_read_filter: str = "time"
        self._later_read_batch_mode: bool = False
        self._later_read_pinned_folded: bool = True
        self._later_read_pinned_toggle_item: Optional[QListWidgetItem] = None
        self._later_read_undo_snapshots: dict[str, list[dict[str, Any]]] = {}
        self._annotation_color_buttons: dict[str, QPushButton] = {}
        self._ui_restoring: bool = False
        self._ui_save_timer: Optional[QTimer] = None
        self._capture_region: Optional[tuple[int, int, int, int]] = None
        self._prev_page_index: Optional[int] = None

        self._thread: Optional[QThread] = None
        self._worker: Optional[CaptureWorker] = None
        self._stop_event: Optional[threading.Event] = None
        self._floating: Optional[FloatingBar] = None
        self._notify_state = CaptureNotificationState()
        self._task_feedback = TaskFeedback(self)
        self._scroll_prepare_workers: list[ScrollResultPrepareWorker] = []
        self._countdown: Optional[CountdownOverlay] = None
        self._region_overlay: Optional[RegionOverlay] = None
        self._later_read_probe_overlay: Optional[QWidget] = None
        self._later_read_probe_generation: int = 0
        self._later_read_probe_timeout_anchor_pos: Optional[QPoint] = None
        self._later_read_probe_timeout_extensions: int = 0
        self._border_overlay: Optional[SelectionBorderOverlay] = None
        self._selection_shade_overlay: Optional[SelectionShadeOverlay] = None
        self._selection_shade_bound_border: Optional[SelectionBorderOverlay] = None
        self._post_actions: Optional[PostCaptureActions] = None
        self._region_capture_shell_pending: bool = False
        self._region_capture_token: int = 0
        self._cancelled_region_capture_token: int = -1
        self._capture_keep_border_visible_once: bool = False
        self._capture_transition_pending: bool = False
        self._continuous_retake_suppressed: bool = False
        self._last_later_read_capture_key: str = ""
        self._capture_region_logical: Optional[tuple[int, int, int, int]] = None
        self._capture_frozen_screen_bgr = None
        self._capture_frozen_screen_origin_px: tuple[int, int] = (0, 0)
        self._cdp_mode: Optional[QCheckBox] = None
        self._cdp_port: Optional[QLineEdit] = None
        self._save_mode: Optional[QComboBox] = None
        self._adaptive_wait: Optional[QCheckBox] = None
        self._reverse_scroll: Optional[QCheckBox] = None
        self._copy_on_capture: Optional[QComboBox] = None
        self._boost_scroll: Optional[QCheckBox] = None
        self._merge_pdf: Optional[QCheckBox] = None
        self._merge_image: Optional[QCheckBox] = None
        self._dual_output: Optional[QCheckBox] = None
        self._updating_output_options: bool = False
        self._capture_started_ts: Optional[float] = None
        self._capture_context: Optional[dict] = None
        self._last_frame_index: int = 0
        self._stitch_buffer_bgr: Optional[Any] = None
        self._stitch_buffer_dpr: float = 0.0
        self._stashed_capture_items: list[dict[str, Any]] = []
        self._stashed_captures_dialog: Optional[QDialog] = None
        self._stashed_captures_show_pending: bool = False
        self._capture_manual_mode: bool = False
        self._capture_fullscreen_mode: bool = False
        self._right_click_cancel_listener = None
        self._escape_cancel_listener = None
        self._suppress_next_right_click_release: bool = False
        self._left_click_retake_listener = None
        self._left_click_retake_enabled_at = 0.0
        self._left_click_finish_deadline = 0.0
        self._tray_menu_visible = False
        self._was_minimized_before_tray_menu = False
        self._tray_menu: Optional[QMenu] = None
        self._tray_action_icon_names: dict[QAction, str] = {}
        self._tray_pinned_action: Optional[QAction] = None
        self._tray_network_probe_action: Optional[QAction] = None
        self._tray_later_read_action: Optional[QAction] = None
        self._later_read_list_dirty: bool = False
        self._tray_new_todo_action: Optional[QAction] = None
        self._network_probe_monitor: Optional[NetworkProbeMonitor] = None
        self._network_probe_notification_failure_count: int = 0
        self._network_probe_last_alert_monotonic: float = 0.0
        self._ocr_selfcheck_done = False
        self._selection_translate_listener = None
        self._selection_translate_panel = None
        self._selection_translate_action_popup = None
        self._selection_hover_translation_popup = None
        self._selection_translate_popup_text = ""
        self._selection_translate_popup_pos = None
        self._selection_translate_popup_pending_action = ""
        self._selection_translate_suspended = False
        self._selection_popup_blocked = False
        self._settings_dialog: Optional[SettingsDialog] = None
        self._pinned_tab_windows: list[QWidget] = []
        self._post_capture_ui_prewarmed = False
        self._post_capture_ui_warm_widget = None
        self._post_capture_ui_prewarm_generation = 0
        self._capture_tools_warmup_running = False
        self._capture_tools_warmup_done = False
        self._capture_tools_warmup_thread: Optional[threading.Thread] = None
        self._startup_services_started = False
        self._syncing_column_widths = False
        self._app_event_filter_installed = False
        self._tray: Optional[QSystemTrayIcon] = None
        self._cat_reminder_timer = QTimer(self)
        self._cat_reminder_timer.setSingleShot(True)
        self._cat_reminder_timer.timeout.connect(self._show_cat_reminder)
        self._cat_reminder_session: Optional[CatReminderSession] = None
        self._in_pre_notify_stage = False
        self._cat_pre_popup: Optional[_CatRestReminderPopup] = None
        self._todo_timer = QTimer(self)
        self._todo_timer.setInterval(30 * 1000)
        self._todo_timer.timeout.connect(self._check_due_todos)
        self._todo_popup: Optional[_TodoReminderPopup] = None
        self._todo_tile_widget = None
        self._todo_countdown_timer = QTimer(self)
        self._todo_countdown_timer.setInterval(1000)
        self._todo_countdown_timer.timeout.connect(self._update_todo_tile_countdown)
        self._todo_countdown_timer.start()

        self._capture_requested.connect(self._start_region_capture_from_hotkey)
        self._scroll_capture_requested.connect(self._start_scroll_capture_from_hotkey)
        self._retake_capture_requested.connect(self._retake_capture_via_left_click_at)
        self._todo_hotkey_signal.connect(self._open_todo_dialog)
        self._later_read_hotkey_signal.connect(self._start_later_read_probe_from_hotkey)
        self._ai_qa_hotkey_signal.connect(self._trigger_ai_qa_hotkey)
        self._pinned_hotkey_signal.connect(self._toggle_pinned_images_shortcut)
        self._note_float_hotkey_signal.connect(self._trigger_note_float)
        self._clipboard_float_hotkey_signal.connect(lambda: self._open_compact_list_window("clipboard"))
        self._later_read_float_hotkey_signal.connect(lambda: self._open_compact_list_window("later_read"))
        self._start_hotkey_str: str = str(self._app_settings.hotkey)
        self._start_hotkey: Optional[GlobalStartHotkey] = None
        ui = dict(getattr(self._app_settings, "ui", {}) or {})
        self._scroll_hotkey_str: str = str(ui.get("scroll_hotkey", "<f2>") or "<f2>")
        self._scroll_hotkey: Optional[GlobalStartHotkey] = None
        self._todo_hotkey: Optional[GlobalStartHotkey] = None
        self._later_read_hotkey_str: str = str(ui.get("later_read_hotkey", "<f3>") or "<f3>")
        self._later_read_hotkey: Optional[GlobalStartHotkey] = None
        self._ai_qa_hotkey_str: str = str(ui.get("ai_qa_hotkey", "<alt>+<space>") or "<alt>+<space>")
        self._ai_qa_hotkey: Optional[GlobalStartHotkey] = None
        self._pinned_hotkey: Optional[GlobalStartHotkey] = None
        self._note_float_hotkey: Optional[GlobalStartHotkey] = None
        self._clipboard_float_hotkey: Optional[GlobalStartHotkey] = None
        self._later_read_float_hotkey: Optional[GlobalStartHotkey] = None
        self._selection_translate_hotkey_str: str = str(ui.get("selection_translate_hotkey", "<ctrl>+<space>") or "<ctrl>+<space>")
        self._selection_popup_hotkey_str: str = str(ui.get("selection_popup_hotkey", "<ctrl>+b") or "<ctrl>+b")
        self._on_hotkey_changed = self._change_start_hotkey_from_settings
        self._on_scroll_hotkey_changed = self._change_scroll_hotkey_from_settings
        self._on_later_read_hotkey_changed = self._change_later_read_hotkey_from_settings
        self._on_selection_translate_changed = self._selection_translate_setting_changed
        self._nav_buttons: list[QPushButton] = []
        self._nav_button_by_index: dict[int, QPushButton] = {}
        self._stack: Optional[QStackedWidget] = None
        self._build_ui()
        self._apply_ui_state_from_settings()
        self._attach_ui_persistence()
        self._install_todo_shortcuts()
        self.setAcceptDrops(True)
        if bool(self._translator.get("local_translation_service_enabled", False)):
            QTimer.singleShot(0, self._start_local_translation_service_from_settings)
        try:
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.cleanup)
                app.applicationStateChanged.connect(self._on_application_state_changed_for_icons)
                app.installEventFilter(self)
                self._app_event_filter_installed = True
        except Exception:
            pass
        write_crash_breadcrumb("MainWindow.__init__.done", visible=self.isVisible())



    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self._hide_all_smooth_tooltips()
                # 最小化动画完成后禁用 DWM 还原过渡动画，
                # 防止还原时多帧渐变暴露 DPI 舍入差异导致的底部黑边和向上抖动
                QTimer.singleShot(250, self._suppress_restore_transition)
            else:
                self._hide_all_smooth_tooltips()
                # 冻结所有子组件更新，防止还原过程中多次独立重绘导致闪烁抖动
                self.setUpdatesEnabled(False)
                try:
                    self._apply_solid_window_backgrounds()
                finally:
                    self.setUpdatesEnabled(True)
                # 同步重绘当前窗口及所有子组件，让 backing store 立即生成最新的完整内容
                self.repaint()
                # 恢复不透明度，使瞬间绘制好的完整界面直接呈现给用户，彻底解决还原时的白屏闪烁问题
                self.setWindowOpacity(1.0)
                # 还原完成后恢复 DWM 过渡动画，不影响后续最小化体验
                QTimer.singleShot(100, self._restore_transition_after_show)
        elif event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow() and not self.isMinimized():
                self._hide_all_smooth_tooltips()
                self._schedule_hover_cursor_refresh()
        super().changeEvent(event)






    def showEvent(self, event) -> None:
        super().showEvent(event)
        write_crash_breadcrumb(
            "MainWindow.showEvent",
            visible=self.isVisible(),
            minimized=self.isMinimized(),
            startup_services_started=bool(getattr(self, "_startup_services_started", False)),
        )
        # 只在首次 show 时设置标题栏颜色和窗口类背景画刷，避免每次从最小化恢复时重复触发 DWM 帧重组引起内容跳跃
        if not bool(getattr(self, "_caption_color_applied", False)):
            self._caption_color_applied = True
            self._apply_caption_color()
            self._set_win32_background_brush()
            # 打开软件时，超级搜索框去掉焦点状态，避免显示输入状态的闪烁光标/鼠标样式
            if hasattr(self, "_super_search") and self._super_search:
                QTimer.singleShot(0, self._super_search.clearFocus)
        if not bool(getattr(self, "_startup_services_started", False)):
            QTimer.singleShot(120, self._start_deferred_startup_services)
        if hasattr(self, "_stack") and self._stack is not None:
            if self._stack.currentIndex() == 3 and getattr(self, "_later_read_list_dirty", False):
                try:
                    self._refresh_later_read_list()
                except Exception:
                    pass
        self._schedule_hover_cursor_refresh()





    def eventFilter(self, obj, event) -> bool:
        try:
            try:
                SettingsDialog._handle_prompt_settings_popup_hover_event(self, obj, event)
            except Exception:
                pass
            if event.type() == QEvent.Type.KeyPress and self._handle_todo_page_keypress(obj, event):
                return True
            if (
                hasattr(self, "_translator_provider")
                and self._translator_provider is not None
                and self._translator_provider.view() is not None
                and obj is self._translator_provider.view().viewport()
            ):
                view = self._translator_provider.view()
                try:
                    pos = event.position().toPoint()
                except Exception:
                    pos = event.pos() if hasattr(event, "pos") else None
                if event.type() == QEvent.Type.Leave:
                    view.viewport().unsetCursor()
                elif pos is not None and event.type() in {QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease}:
                    index = view.indexAt(pos)
                    in_action = False
                    if index.isValid():
                        action_rect = ProviderSwitchHintDelegate.action_rect(view.visualRect(index), view.fontMetrics())
                        in_action = action_rect.contains(pos)
                    if event.type() == QEvent.Type.MouseMove:
                        if in_action:
                            view.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
                        else:
                            view.viewport().unsetCursor()
                    elif event.type() == QEvent.Type.MouseButtonRelease:
                        try:
                            is_left = event.button() == Qt.MouseButton.LeftButton
                        except Exception:
                            is_left = True
                        if is_left and index.isValid():
                            provider = str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
                            self._translator_provider.hidePopup()
                            if in_action:
                                self._move_current_model_to_provider(provider)
                            else:
                                self._select_provider_for_new_model(provider)
                            return True
            if hasattr(self, "_translator_api_key") and obj is self._translator_api_key and event.type() == QEvent.Type.Resize:
                try:
                    self._position_translator_api_key_eye()
                except Exception:
                    pass
            if hasattr(self, "_translator_proxy_url") and obj is self._translator_proxy_url and event.type() == QEvent.Type.Resize:
                try:
                    self._position_translator_proxy_test_btn()
                except Exception:
                    pass
            if hasattr(self, "_translator_api_url") and obj is self._translator_api_url and event.type() == QEvent.Type.Resize:
                try:
                    self._position_translator_api_url_role_label()
                    self._position_translator_get_models_btn()
                except Exception:
                    pass
            if hasattr(self, "_translator_provider") and obj is self._translator_provider and event.type() == QEvent.Type.Resize:
                try:
                    self._position_translator_provider_btn()
                except Exception:
                    pass
            if hasattr(self, "_translator_model_name") and self._translator_model_name is not None and obj is self._translator_model_name and event.type() == QEvent.Type.Resize:
                try:
                    self._position_translator_model_name_btns()
                    self._sync_model_role_combo_widths()
                except Exception:
                    pass
            if self._region_overlay is not None:
                try:
                    active_selecting = not bool(getattr(self._region_overlay, "_confirmed", False))
                except RuntimeError:
                    self._region_overlay = None
                    active_selecting = False
                except Exception:
                    active_selecting = False
                if bool(active_selecting):
                    if event.type() == QEvent.Type.KeyPress and int(event.key()) == int(Qt.Key.Key_Escape):
                        if self._cancel_active_region_overlay():
                            event.accept()
                            return True
                    if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                        if self._cancel_active_region_overlay():
                            event.accept()
                            return True
            if event.type() in {QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride} and int(event.key()) == int(Qt.Key.Key_Escape):
                if self._handle_escape_close_targets():
                    event.accept()
                    return True
        except RuntimeError:
            pass
        except Exception:
            pass
        return super().eventFilter(obj, event)





























































































































































































    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._stack is not None and self._stack.currentIndex() == 2:
            mime = event.mimeData()
            if mime.hasUrls() or mime.hasFormat("application/x-deepcat-shortcut-id"):
                event.acceptProposedAction()
                return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._stack is not None and self._stack.currentIndex() == 2:
            mime = event.mimeData()
            if mime.hasUrls() or mime.hasFormat("application/x-deepcat-shortcut-id"):
                event.acceptProposedAction()
                return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if self._stack is not None and self._stack.currentIndex() == 2:
            mime = event.mimeData()
            if mime.hasFormat("application/x-deepcat-shortcut-id"):
                event.acceptProposedAction()
                shortcut_id = bytes(mime.data("application/x-deepcat-shortcut-id")).decode("utf-8")
                self._reorder_resource_shortcut(shortcut_id, event.position().toPoint())
                return
            urls = mime.urls()
            if urls:
                event.acceptProposedAction()
                for url in urls:
                    local_path = url.toLocalFile()
                    if local_path:
                        self._add_dragged_shortcut(local_path)
                    else:
                        url_str = url.toString()
                        if url_str:
                            self._add_dragged_url(url_str)
                return
        super().dropEvent(event)










    def _build_clipboard_history_page(self) -> QWidget:
        from deepcat.clipboard_history.clipboard_history_page import ClipboardHistoryPage
        self._clipboard_history_page = ClipboardHistoryPage()
        self._clipboard_history_page.capture_requested.connect(lambda *_: self._start_capture_clicked(from_tray=False))
        self._clipboard_history_page.pin_requested.connect(lambda *_: self._open_compact_list_window("clipboard"))
        return self._clipboard_history_page

    def _build_data_management_page(self) -> QWidget:
        from deepcat.ui.data_management_page import DataManagementPage
        self._data_management_page = DataManagementPage(self)
        return self._data_management_page


    def _check_and_run_auto_backup(self) -> None:
        try:
            existing_worker = getattr(self, "_auto_backup_worker", None)
            if existing_worker is not None and existing_worker.isRunning():
                return
            data_page = getattr(self, "_data_management_page", None)
            data_worker = getattr(data_page, "worker", None) if data_page is not None else None
            if data_worker is not None and data_worker.isRunning():
                return
            ui = dict(getattr(self._current, "ui", {}) or {})
            dm = dict(ui.get("data_management", {}) or {})
            if not dm.get("auto_backup_enabled", False):
                return
            last_time_str = dm.get("last_backup_time", "")
            interval_minutes = coerce_auto_backup_interval_minutes(
                dm.get("auto_backup_interval_minutes"),
                dm.get("auto_backup_interval_hours"),
            )
            import time
            current_time = time.time()
            need_backup = False
            if not last_time_str:
                need_backup = True
            else:
                try:
                    last_ts = time.mktime(time.strptime(last_time_str, "%Y-%m-%d %H:%M:%S"))
                    if current_time - last_ts >= interval_minutes * 60:
                        need_backup = True
                except Exception:
                    need_backup = True
            if not need_backup:
                return
            backup_dir = dm.get("auto_backup_dir", "").strip()
            if not backup_dir:
                backup_dir = str(get_app_dir() / "backups")
            backup_path = Path(backup_dir)
            backup_path.mkdir(parents=True, exist_ok=True)
            filename = f"deepcat_auto_backup_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            dest_zip = backup_path / filename
            from deepcat.ui.data_management_page import BackupTaskWorker
            opts = dict(dm.get("backup_options", {}) or {})
            params = {
                "local_path": str(dest_zip),
                "options": opts,
                "webdav_enabled": dm.get("webdav_enabled", False),
                "server": dm.get("webdav_server", ""),
                "user": dm.get("webdav_username", ""),
                "password": dm.get("webdav_password", ""),
                "backup_dir": dm.get("webdav_backup_dir", "DeepCatBackup"),
                "webdav_keep_count": dm.get("webdav_keep_count", 5)
            }
            worker = BackupTaskWorker("manual_backup", params)
            self._auto_backup_worker = worker
            def on_finished(success: bool, msg: str):
                try:
                    local_backup_saved = bool(success) or str(msg).startswith("本地备份已保存至:")
                    if local_backup_saved:
                        new_time_str = time.strftime("%Y-%m-%d %H:%M:%S")
                        try:
                            def _mut(s: Any) -> Any:
                                from dataclasses import replace as dataclass_replace

                                ui = dict(s.ui) if isinstance(s.ui, dict) else {}
                                current_dm = dict(ui.get("data_management", {}) or {})
                                current_dm["last_backup_time"] = new_time_str
                                ui["data_management"] = current_dm
                                return dataclass_replace(s, ui=ui)
                            from deepcat.settings_store import update_settings
                            self._current = update_settings(_mut)
                            if hasattr(self, "_data_management_page"):
                                self._data_management_page._load_config_to_ui()
                                if success:
                                    self._data_management_page.log("定时自动备份在后台成功完成。")
                                    self._data_management_page._set_inline_status("定时自动备份已在后台完成。", "success", auto_hide_ms=3500)
                                else:
                                    self._data_management_page.log(f"定时自动备份本地完成，但云同步失败: {msg}")
                                    self._data_management_page._set_inline_status(f"定时自动备份本地完成，但云同步失败：{msg}", "warning", auto_hide_ms=5200)
                            elif success:
                                self._send_tray_notification("自动备份完成", "定时自动备份已在后台完成。", 2600)
                            else:
                                self._force_tray_notification("自动备份云同步失败", str(msg or "本地备份已完成，但云端同步失败。"), 5200)
                        except Exception:
                            pass
                        try:
                            keep_count = max(1, int(dm.get("auto_backup_keep_count", 5) or 5))
                            auto_files = sorted(
                                [f for f in backup_path.glob("deepcat_auto_backup_*.zip")],
                                key=lambda x: x.name,
                                reverse=True
                            )
                            if len(auto_files) > keep_count:
                                for extra_file in auto_files[keep_count:]:
                                    extra_file.unlink()
                        except Exception:
                            get_logger().exception("Failed to rotate local auto backups")
                    if not success:
                        get_logger().error("Auto backup failed: %s", msg)
                        data_page = getattr(self, "_data_management_page", None)
                        if data_page is not None:
                            data_page._set_inline_status(f"定时自动备份失败：{msg}", "error", auto_hide_ms=5200)
                        else:
                            self._force_tray_notification("自动备份失败", str(msg or "定时自动备份失败。"), 5200)
                finally:
                    if getattr(self, "_auto_backup_worker", None) is worker:
                        self._auto_backup_worker = None
                    worker.deleteLater()
            worker.finished_signal.connect(on_finished)
            worker.start()
        except Exception:
            get_logger().exception("Auto backup scheduler trigger failed")
            self._force_tray_notification("自动备份失败", "定时自动备份启动失败，请检查备份配置。", 5200)
















    def _run_pending_ima_auto_sync(self) -> None:
        index = getattr(self, "_ima_auto_sync_pending_note_index", None)
        self._ima_auto_sync_pending_note_index = None
        if index is None or index < 0 or index >= len(getattr(self, "_note_tabs", [])):
            return
        config = dict(self._note_tabs[index].get("ima_config") or {})
        if not MainWindow._ima_auto_sync_config_ready(config):
            return
        worker = getattr(self, "_ima_sync_worker", None)
        if worker is not None:
            try:
                if worker.isRunning():
                    self._ima_auto_sync_pending_note_index = index
                    self._ima_auto_sync_timer.start(self._IMA_AUTO_SYNC_DELAY_MS)
                    return
            except Exception:
                return
        self._run_ima_tab_action(index, "append_note", silent=True)

    def _clear_pending_ima_auto_sync(self) -> None:
        self._ima_auto_sync_pending_note_index = None
        try:
            self._ima_auto_sync_timer.stop()
        except Exception:
            pass

    @staticmethod
    def _ima_auto_sync_config_ready(config: dict[str, Any]) -> bool:
        return (
            bool(config.get("enabled", False))
            and bool(config.get("auto_sync_enabled", False))
            and bool(str(config.get("client_id", "") or "").strip())
            and bool(str(config.get("api_key", "") or "").strip())
        )



    def _default_table_column_widths(self, col_count: Optional[int] = None) -> list[int]:
        count = self._DEFAULT_TABLE_COLUMNS if col_count is None else max(0, int(col_count))
        return [int(self._DEFAULT_TABLE_COLUMN_WIDTH)] * count

    def _normalize_table_column_widths(self, widths: Any, col_count: int) -> list[int]:
        normalized: list[int] = []
        if isinstance(widths, (list, tuple)):
            for value in widths[:col_count]:
                try:
                    width = int(value)
                except Exception:
                    width = int(self._DEFAULT_TABLE_COLUMN_WIDTH)
                normalized.append(max(1, min(width, 10000)))
        while len(normalized) < col_count:
            normalized.append(int(self._DEFAULT_TABLE_COLUMN_WIDTH))
        return normalized

    def _table_column_widths(self, table: QTableWidget) -> list[int]:
        return [int(table.columnWidth(c)) for c in range(table.columnCount())]

    def _remember_table_column_widths(self, tab_index: int, table: QTableWidget) -> None:
        if not hasattr(self, "_table_tabs") or tab_index < 0 or tab_index >= len(self._table_tabs):
            return
        column_widths = self._table_column_widths(table)
        if self._table_tabs[tab_index].get("column_widths") != column_widths:
            self._table_tabs[tab_index]["column_widths"] = column_widths
            self._mark_table_notes_tab_dirty("table", tab_index)

    def _apply_table_column_widths(self, table: QTableWidget, widths: Any, *, sync_sum: bool = False) -> None:
        col_count = int(table.columnCount())
        normalized = self._normalize_table_column_widths(widths, col_count)
        was_syncing = bool(getattr(self, "_syncing_column_widths", False))
        self._syncing_column_widths = True
        try:
            for c, width in enumerate(normalized):
                table.setColumnWidth(c, width)
            if sync_sum and getattr(self, "_notes_sum_table", None) is not None:
                self._notes_sum_table.setColumnCount(col_count)
                self._notes_sum_table.setHorizontalHeaderLabels([f"列 {i}" for i in range(1, col_count + 1)])
                for c, width in enumerate(normalized):
                    self._notes_sum_table.setColumnWidth(c, width)
        finally:
            self._syncing_column_widths = was_syncing



    def _on_pinned_table_section_resized(self, tab_index: int, table: QTableWidget, index: int, new_size: int) -> None:
        if bool(getattr(self, "_syncing_column_widths", False)):
            return
        self._syncing_column_widths = True
        try:
            if tab_index == self._active_table_tab:
                self._notes_table.setColumnWidth(index, new_size)
                if getattr(self, "_notes_sum_table", None) is not None:
                    self._notes_sum_table.setColumnWidth(index, new_size)
        finally:
            self._syncing_column_widths = False
        if not bool(getattr(self, "_loading_table_notes", False)):
            self._remember_table_column_widths(tab_index, table)
            self._schedule_table_notes_save()


    def _recalculate_table_sums(self) -> None:
        try:
            self._notes_sum_table.blockSignals(True)
            col_count = self._notes_table.columnCount()
            row_count = self._notes_table.rowCount()
            self._notes_sum_table.setColumnCount(col_count)
            for c in range(col_count):
                total = 0.0
                has_number = False
                for r in range(row_count):
                    item = self._notes_table.item(r, c)
                    text = str(item.text() if item is not None else "").strip().replace(",", "")
                    if not text:
                        continue
                    try:
                        total += float(text)
                        has_number = True
                    except Exception:
                        continue
                value = ""
                if has_number:
                    value = str(int(total)) if abs(total - int(total)) < 1e-9 else f"{total:.2f}".rstrip("0").rstrip(".")
                out = QTableWidgetItem(value)
                out.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._notes_sum_table.setItem(0, c, out)
                self._notes_sum_table.setColumnWidth(c, self._notes_table.columnWidth(c))
        finally:
            try:
                self._notes_sum_table.blockSignals(False)
            except Exception:
                pass


    def _table_copy_cells(self, table: QTableWidget) -> None:
        selected_ranges = table.selectedRanges()
        if not selected_ranges:
            return
        r_range = selected_ranges[0]
        text_lines = []
        for r in range(r_range.topRow(), r_range.bottomRow() + 1):
            row_texts = []
            for c in range(r_range.leftColumn(), r_range.rightColumn() + 1):
                item = table.item(r, c)
                row_texts.append(item.text() if item else "")
            text_lines.append("\t".join(row_texts))
        QApplication.clipboard().setText("\n".join(text_lines))

    def _table_paste_cells(self, table: QTableWidget) -> None:
        text = QApplication.clipboard().text()
        if not text:
            return
        current_row = table.currentRow()
        current_col = table.currentColumn()
        if current_row < 0 or current_col < 0:
            return
        rows_data = text.split("\n")
        table.blockSignals(True)
        try:
            for r_idx, row_text in enumerate(rows_data):
                r = current_row + r_idx
                if r >= table.rowCount():
                    break
                cols_data = row_text.split("\t")
                for c_idx, cell_text in enumerate(cols_data):
                    c = current_col + c_idx
                    if c >= table.columnCount():
                        break
                    item = table.item(r, c)
                    if not item:
                        item = QTableWidgetItem()
                        table.setItem(r, c, item)
                    item.setText(cell_text.strip())
        finally:
            table.blockSignals(False)
        self._trigger_table_sync_and_save(table)

    def _table_clear_cells_content(self, table: QTableWidget) -> None:
        indexes = table.selectedIndexes()
        if indexes:
            table.blockSignals(True)
            try:
                for index in indexes:
                    item = table.item(index.row(), index.column())
                    if item:
                        item.setText("")
                        item.setFont(QFont())
                        item.setForeground(QBrush())
                        item.setBackground(QBrush())
            finally:
                table.blockSignals(False)
            self._trigger_table_sync_and_save(table)

    def _table_insert_row_above(self, table: QTableWidget) -> None:
        r = table.currentRow()
        if r < 0:
            r = 0
        table.insertRow(r)
        self._trigger_table_sync_and_save(table)

    def _table_insert_row_below(self, table: QTableWidget) -> None:
        r = table.currentRow()
        if r < 0:
            r = table.rowCount()
        else:
            r = r + 1
        table.insertRow(r)
        self._trigger_table_sync_and_save(table)

    def _table_delete_row(self, table: QTableWidget) -> None:
        selected_ranges = table.selectedRanges()
        rows_to_delete = set()
        for r_range in selected_ranges:
            for r in range(r_range.topRow(), r_range.bottomRow() + 1):
                rows_to_delete.add(r)
        if not rows_to_delete:
            self._show_table_notes_status("请先选中要删除的行。", tone="warning")
            return
        count = len(rows_to_delete)
        try:
            table.blockSignals(True)
            for r in sorted(rows_to_delete, reverse=True):
                table.removeRow(r)
            table.blockSignals(False)
            self._trigger_table_sync_and_save(table)
            self._show_table_notes_status(f"已删除 {count} 行。", tone="success")
        except Exception as exc:
            table.blockSignals(False)
            self._show_table_notes_status(f"删除行失败：{exc}", tone="error", auto_hide_ms=5200)

    def _table_insert_col_left(self, table: QTableWidget) -> None:
        c = table.currentColumn()
        if c < 0:
            c = 0
        table.insertColumn(c)
        table.setColumnWidth(c, self._DEFAULT_TABLE_COLUMN_WIDTH)
        table.setHorizontalHeaderLabels([f"列 {i}" for i in range(1, table.columnCount() + 1)])
        self._trigger_table_sync_and_save(table)

    def _table_insert_col_right(self, table: QTableWidget) -> None:
        c = table.currentColumn()
        if c < 0:
            c = table.columnCount()
        else:
            c = c + 1
        table.insertColumn(c)
        table.setColumnWidth(c, self._DEFAULT_TABLE_COLUMN_WIDTH)
        table.setHorizontalHeaderLabels([f"列 {i}" for i in range(1, table.columnCount() + 1)])
        self._trigger_table_sync_and_save(table)

    def _table_delete_col(self, table: QTableWidget) -> None:
        selected_ranges = table.selectedRanges()
        cols_to_delete = set()
        for r_range in selected_ranges:
            for c in range(r_range.leftColumn(), r_range.rightColumn() + 1):
                cols_to_delete.add(c)
        if not cols_to_delete:
            self._show_table_notes_status("请先选中要删除的列。", tone="warning")
            return
        count = len(cols_to_delete)
        try:
            table.blockSignals(True)
            for c in sorted(cols_to_delete, reverse=True):
                table.removeColumn(c)
            table.blockSignals(False)
            table.setHorizontalHeaderLabels([f"列 {i}" for i in range(1, table.columnCount() + 1)])
            self._trigger_table_sync_and_save(table)
            self._show_table_notes_status(f"已删除 {count} 列。", tone="success")
        except Exception as exc:
            table.blockSignals(False)
            self._show_table_notes_status(f"删除列失败：{exc}", tone="error", auto_hide_ms=5200)

    def _table_resize(self, table: QTableWidget) -> None:
        rows, ok1 = QInputDialog.getInt(table, "调整表格大小", "请输入行数 (1-500):", table.rowCount(), 1, 500)
        if ok1:
            cols, ok2 = QInputDialog.getInt(table, "调整表格大小", "请输入列数 (1-50):", table.columnCount(), 1, 50)
            if ok2:
                old_widths = self._table_column_widths(table)
                table.blockSignals(True)
                table.setRowCount(rows)
                table.setColumnCount(cols)
                table.setHorizontalHeaderLabels([f"列 {i}" for i in range(1, cols + 1)])
                table.blockSignals(False)
                self._apply_table_column_widths(table, old_widths, sync_sum=(table == self._notes_table))
                self._trigger_table_sync_and_save(table)

    def _table_clear_all(self, table: QTableWidget) -> None:
        from deepcat.ui.main_window.compact import StyledMessageBox
        reply = StyledMessageBox.question(
            table, "提示", "您确定要清空整张表格的所有内容吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                table.blockSignals(True)
                table.clearContents()
                table.blockSignals(False)
                self._trigger_table_sync_and_save(table)
                self._show_table_notes_status("已清空当前表格。", tone="success")
            except Exception as exc:
                table.blockSignals(False)
                self._show_table_notes_status(f"清空表格失败：{exc}", tone="error", auto_hide_ms=5200)

    def _sync_table_widget(self, src: QTableWidget, dst: QTableWidget) -> None:
        dst.blockSignals(True)
        was_syncing = bool(getattr(self, "_syncing_column_widths", False))
        self._syncing_column_widths = True
        try:
            # 同步行列数与表头标签
            dst.setRowCount(src.rowCount())
            dst.setColumnCount(src.columnCount())
            dst.setHorizontalHeaderLabels([
                src.horizontalHeaderItem(i).text() if src.horizontalHeaderItem(i) else f"列 {i+1}"
                for i in range(src.columnCount())
            ])
            # 同步所有列宽
            for i in range(src.columnCount()):
                dst.setColumnWidth(i, src.columnWidth(i))
            # 同步所有单元格
            for r in range(src.rowCount()):
                for c in range(src.columnCount()):
                    src_item = src.item(r, c)
                    if src_item:
                        dst_item = dst.item(r, c)
                        if not dst_item:
                            dst_item = QTableWidgetItem()
                            dst.setItem(r, c, dst_item)
                        dst_item.setText(src_item.text())
                        dst_item.setFont(src_item.font())
                        dst_item.setForeground(src_item.foreground())
                        dst_item.setBackground(src_item.background())
                    else:
                        dst.setItem(r, c, None)
        finally:
            self._syncing_column_widths = was_syncing
            dst.blockSignals(False)

    def _trigger_table_sync_and_save(self, table: QTableWidget) -> None:
        if table == self._notes_table:
            pinned = self._active_pinned_tables.get(self._active_table_tab)
            if pinned:
                self._sync_table_widget(self._notes_table, pinned)
            self._on_notes_table_changed()
        else:
            tab_index = getattr(table, "_table_tab_index", None)
            if isinstance(tab_index, int) and tab_index != self._active_table_tab:
                self._on_pinned_table_changed(table, tab_index)
                return
            self._sync_table_widget(table, self._notes_table)
            self._on_notes_table_changed()

    def _get_table_selected_ranges_items(self, table: QTableWidget) -> list[QTableWidgetItem]:
        selected_ranges = table.selectedRanges()
        items = []
        table.blockSignals(True)
        try:
            for r_range in selected_ranges:
                for r in range(r_range.topRow(), r_range.bottomRow() + 1):
                    for c in range(r_range.leftColumn(), r_range.rightColumn() + 1):
                        item = table.item(r, c)
                        if not item:
                            item = QTableWidgetItem()
                            table.setItem(r, c, item)
                        items.append(item)
        finally:
            table.blockSignals(False)
        return items

    def _table_toggle_bold(self, table: QTableWidget) -> None:
        items = self._get_table_selected_ranges_items(table)
        if not items:
            return
        first_item = items[0]
        font = first_item.font()
        target_bold = not font.bold()

        table.blockSignals(True)
        try:
            for item in items:
                f = item.font()
                f.setBold(target_bold)
                item.setFont(f)
        finally:
            table.blockSignals(False)
        self._trigger_table_sync_and_save(table)

    def _table_toggle_italic(self, table: QTableWidget) -> None:
        items = self._get_table_selected_ranges_items(table)
        if not items:
            return
        first_item = items[0]
        font = first_item.font()
        target_italic = not font.italic()

        table.blockSignals(True)
        try:
            for item in items:
                f = item.font()
                f.setItalic(target_italic)
                item.setFont(f)
        finally:
            table.blockSignals(False)
        self._trigger_table_sync_and_save(table)

    def _table_toggle_underline(self, table: QTableWidget) -> None:
        items = self._get_table_selected_ranges_items(table)
        if not items:
            return
        first_item = items[0]
        font = first_item.font()
        target_underline = not font.underline()

        table.blockSignals(True)
        try:
            for item in items:
                f = item.font()
                f.setUnderline(target_underline)
                item.setFont(f)
        finally:
            table.blockSignals(False)
        self._trigger_table_sync_and_save(table)

    def _table_toggle_strike(self, table: QTableWidget) -> None:
        items = self._get_table_selected_ranges_items(table)
        if not items:
            return
        first_item = items[0]
        font = first_item.font()
        target_strike = not font.strikeOut()

        table.blockSignals(True)
        try:
            for item in items:
                f = item.font()
                f.setStrikeOut(target_strike)
                item.setFont(f)
        finally:
            table.blockSignals(False)
        self._trigger_table_sync_and_save(table)

    def _apply_last_table_text_color(self, table: QTableWidget) -> None:
        color = getattr(self, "_last_table_text_color", QColor("#dc2626"))
        items = self._get_table_selected_ranges_items(table)
        if items:
            table.blockSignals(True)
            try:
                for item in items:
                    item.setForeground(color)
            finally:
                table.blockSignals(False)
            self._trigger_table_sync_and_save(table)

    def _choose_table_text_color(self, table: QTableWidget) -> None:
        from PyQt6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(getattr(self, "_last_table_text_color", QColor("#dc2626")), self, "选择文字颜色")
        if color.isValid():
            self._last_table_text_color = color
            self._apply_last_table_text_color(table)

    def _apply_last_table_bg_color(self, table: QTableWidget) -> None:
        color = getattr(self, "_last_table_bg_color", QColor("#fef08a"))
        items = self._get_table_selected_ranges_items(table)
        if items:
            table.blockSignals(True)
            try:
                for item in items:
                    item.setBackground(color)
            finally:
                table.blockSignals(False)
            self._trigger_table_sync_and_save(table)

    def _choose_table_bg_color(self, table: QTableWidget) -> None:
        from PyQt6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(getattr(self, "_last_table_bg_color", QColor("#fef08a")), self, "选择背景颜色")
        if color.isValid():
            self._last_table_bg_color = color
            self._apply_last_table_bg_color(table)











    def _delete_tabs_by_indices(self, tab_type: str, indices: list[int]) -> bool:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        selected = sorted({int(index) for index in indices if 0 <= int(index) < len(tabs)})
        if not selected:
            return False

        for index in selected:
            hashed_pwd = str(tabs[index].get("password", "") or "")
            if hashed_pwd:
                dialog = _TabPasswordVerifyDialog(hashed_pwd, self)
                dialog.setWindowTitle("验证密码后删除")
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return False

        if tab_type == "table":
            self._save_current_table_tab_data()
            old_active = int(self._active_table_tab)
            remaining = [tab for index, tab in enumerate(self._table_tabs) if index not in selected]
            if remaining:
                self._table_tabs = remaining
                self._active_table_tab = self._active_index_after_deletion(old_active, selected, len(remaining))
            else:
                self._table_tabs = [{
                    "name": "表格1",
                    "data": [],
                    "column_widths": self._default_table_column_widths(),
                    "group_name": "",
                    "password": "",
                }]
                self._active_table_tab = 0
            self._ensure_active_tab_visible_in_current_group("table")
            self._loading_table_notes = True
            try:
                self._load_current_table_tab_data()
            finally:
                self._loading_table_notes = False
            self._recalculate_table_sums()
        else:
            self._save_current_note_tab_data()
            old_active = int(self._active_note_tab)
            remaining = [tab for index, tab in enumerate(self._note_tabs) if index not in selected]
            if remaining:
                self._note_tabs = remaining
                self._active_note_tab = self._active_index_after_deletion(old_active, selected, len(remaining))
            else:
                self._note_tabs = [{
                    "name": "记事本1",
                    "html": "",
                    "group_name": "",
                    "password": "",
                    "ima_config": self._inherited_ima_config_for_new_note(),
                }]
                self._active_note_tab = 0
            self._ensure_active_tab_visible_in_current_group("note")
            self._loading_table_notes = True
            try:
                self._load_current_note_tab_data()
            finally:
                self._loading_table_notes = False

        self._refresh_tab_bars()
        self._save_table_notes_settings()
        self._scroll_active_tab_into_view(tab_type)
        return True

    @staticmethod
    def _active_index_after_deletion(old_active: int, selected: list[int], remaining_count: int) -> int:
        if remaining_count <= 0:
            return 0
        if old_active in selected:
            return min(old_active, remaining_count - 1)
        deleted_before = sum(1 for index in selected if index < old_active)
        return max(0, min(old_active - deleted_before, remaining_count - 1))















    def _import_url_to_ima_knowledge_base(self, index: int) -> None:
        if index < 0 or index >= len(self._note_tabs):
            return
        url, ok = QInputDialog.getText(self, "导入网址到IMA知识库", "请输入网址:")
        if not ok or not url.strip():
            return
        self._run_ima_tab_action(index, "import_url_to_kb", extra={"url": url.strip()})

    def _search_ima_knowledge_base(self, index: int) -> None:
        if index < 0 or index >= len(self._note_tabs):
            return
        query, ok = QInputDialog.getText(self, "搜索IMA知识库", "请输入搜索关键词:")
        if not ok or not query.strip():
            return
        self._run_ima_tab_action(index, "search_kb", extra={"query": query.strip()})





    def _save_pinned_window_size(self, width: int, height: int) -> None:
        try:
            self._current = update_ui_settings(pinned_window_size=[width, height])
            self._sync_settings_after_change()
        except Exception:
            pass

    def _on_pinned_table_changed(self, table: QTableWidget, index: int) -> None:
        if getattr(self, "_loading_table_notes", False):
            return
        if index < 0 or index >= len(self._table_tabs):
            return
        rows = self._serialize_table_widget(table)
        if self._table_tabs[index].get("data") != rows:
            self._table_tabs[index]["data"] = rows
            self._mark_table_notes_tab_dirty("table", index)
        self._remember_table_column_widths(index, table)
        if self._active_table_tab == index:
            self._trigger_table_sync_and_save(table)
        else:
            self._schedule_table_notes_save()


    def _rename_tab(self, tab_type: str, index: int) -> None:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        if index < 0 or index >= len(tabs):
            return
        old_name = str(tabs[index].get("name", ""))
        dialog = StyledDialog(self)
        dialog.setWindowTitle("重命名")
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setSpacing(10)
        line_edit = QLineEdit(old_name)
        line_edit.selectAll()
        dialog_layout.addWidget(line_edit)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("BtnSmallPrimary")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(dialog.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("BtnSmallSecondary")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        dialog_layout.addLayout(btn_layout)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_name = line_edit.text().strip()
            if new_name:
                tabs[index]["name"] = new_name
                if tab_type == "note":
                    tabs[index]["_user_renamed"] = True
                self._mark_table_notes_tab_dirty(tab_type, index)
                self._refresh_tab_bars()
                self._save_table_notes_settings()
                if tab_type == "note" and index == getattr(self, "_active_note_tab", -1):
                    self._schedule_ima_auto_sync_for_active_note()


    def _on_add_tab(self, tab_type: str) -> None:
        if tab_type == "table":
            self._save_current_table_tab_data()
            new_index = len(self._table_tabs)
            current_group = getattr(self, "_current_table_group", "全部")
            group_name = "" if current_group == "全部" else current_group
            self._table_tabs.append({
                "name": f"表格{new_index + 1}",
                "data": [],
                "column_widths": self._default_table_column_widths(),
                "group_name": group_name,
            })
            self._active_table_tab = new_index
            self._table_tab_unlocked = True
            self._loading_table_notes = True
            try:
                self._reset_notes_table_to_default()
            finally:
                self._loading_table_notes = False
            self._recalculate_table_sums()
            self._load_table_notes_view_safely(self._load_current_table_tab_data)
        else:
            self._save_current_note_tab_data()
            new_index = len(self._note_tabs)
            current_group = getattr(self, "_current_note_group", "全部")
            group_name = "" if current_group == "全部" else current_group
            self._note_tabs.append({
                "name": f"记事本{new_index + 1}",
                "html": "",
                "group_name": group_name,
                "ima_config": self._inherited_ima_config_for_new_note(),
            })
            self._active_note_tab = new_index
            self._note_tab_unlocked = True
            self._load_table_notes_view_safely(self._load_current_note_tab_data)
        self._refresh_tab_bars()
        self._save_table_notes_settings()
        self._scroll_active_tab_into_view(tab_type)


    def _set_current_group(self, tab_type: str, group_name: str) -> None:
        if tab_type == "table":
            self._current_table_group = group_name
            btn = getattr(self._table_tab_bar, "_group_btn", None)
            if btn:
                display = "全部" if group_name == "全部" else (group_name if group_name else "未分组")
                btn.setText(f"分组: {display} ▾")

            tabs = self._table_tabs
            filtered_indices = [idx for idx, t in enumerate(tabs) if group_name == "全部" or t.get("group_name", "") == group_name]
            if filtered_indices:
                if self._active_table_tab not in filtered_indices:
                    self._on_tab_clicked("table", filtered_indices[0])
            else:
                new_tab = {
                    "name": "表格1",
                    "data": [],
                    "column_widths": self._default_table_column_widths(),
                    "group_name": "" if group_name == "全部" else group_name,
                    "password": ""
                }
                tabs.append(new_tab)
                self._active_table_tab = len(tabs) - 1
                self._table_tab_unlocked = True
                self._load_table_notes_view_safely(self._load_current_table_tab_data)
                self._save_table_notes_settings()
        else:
            self._current_note_group = group_name
            btn = getattr(self._note_tab_bar, "_group_btn", None)
            if btn:
                display = "全部" if group_name == "全部" else (group_name if group_name else "未分组")
                btn.setText(f"分组: {display} ▾")

            tabs = self._note_tabs
            filtered_indices = [idx for idx, t in enumerate(tabs) if group_name == "全部" or t.get("group_name", "") == group_name]
            if filtered_indices:
                if self._active_note_tab not in filtered_indices:
                    self._on_tab_clicked("note", filtered_indices[0])
            else:
                new_tab = {
                    "name": "记事本1",
                    "html": "",
                    "group_name": "" if group_name == "全部" else group_name,
                    "password": "",
                    "ima_config": self._inherited_ima_config_for_new_note(),
                }
                tabs.append(new_tab)
                self._active_note_tab = len(tabs) - 1
                self._note_tab_unlocked = True
                self._load_table_notes_view_safely(self._load_current_note_tab_data)
                self._save_table_notes_settings()
        self._refresh_tab_bars()
        self._scroll_active_tab_into_view(tab_type)

    def _create_new_group(self, tab_type: str) -> None:
        name, ok = StyledInputDialog.get_text(self, "新建分组", "请输入新分组名称:")
        if ok and name.strip():
            self._set_current_group(tab_type, name.strip())



    def _manage_groups(self, tab_type: str) -> None:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        dialog = _GroupManageDialog(tab_type, tabs, self)
        dialog.exec()

    def _serialize_table_widget(self, table: QTableWidget) -> list[list[Any]]:
        rows: list[list[Any]] = []
        row_count = table.rowCount()
        col_count = table.columnCount()
        from PyQt6.QtCore import Qt
        for r in range(row_count):
            row: list[Any] = []
            for c in range(col_count):
                item = table.item(r, c)
                if item is None:
                    row.append("")
                else:
                    text = item.text()
                    font = item.font()
                    fg_brush = item.foreground()
                    bg_brush = item.background()

                    has_style = False
                    cell_data = {"text": text}

                    if font.bold():
                        cell_data["bold"] = True
                        has_style = True
                    if font.italic():
                        cell_data["italic"] = True
                        has_style = True
                    if font.underline():
                        cell_data["underline"] = True
                        has_style = True
                    if font.strikeOut():
                        cell_data["strike"] = True
                        has_style = True

                    if fg_brush and fg_brush.style() != Qt.BrushStyle.NoBrush:
                        fg_color = fg_brush.color()
                        if fg_color.isValid():
                            cell_data["color"] = fg_color.name()
                            has_style = True

                    if bg_brush and bg_brush.style() != Qt.BrushStyle.NoBrush:
                        bg_color = bg_brush.color()
                        if bg_color.isValid():
                            cell_data["bg"] = bg_color.name()
                            has_style = True

                    if has_style:
                        row.append(cell_data)
                    else:
                        row.append(text)
            rows.append(row)
        return rows

    def _set_table_item_from_serialized_data(self, table: QTableWidget, r: int, c: int, val: Any) -> None:
        if isinstance(val, dict):
            text = str(val.get("text", "") or "")
            item = QTableWidgetItem(text)

            # 设置字体样式
            font = QFont()
            if val.get("bold"):
                font.setBold(True)
            if val.get("italic"):
                font.setItalic(True)
            if val.get("underline"):
                font.setUnderline(True)
            if val.get("strike"):
                font.setStrikeOut(True)
            item.setFont(font)

            # 设置前景色
            color_hex = val.get("color")
            if color_hex:
                item.setForeground(QBrush(QColor(color_hex)))

            # 设置背景色
            bg_hex = val.get("bg")
            if bg_hex:
                item.setBackground(QBrush(QColor(bg_hex)))

            table.setItem(r, c, item)
        else:
            table.setItem(r, c, QTableWidgetItem(str(val or "")))















    def _exec_color_dialog(self, initial: QColor, title: str) -> QColor:
        dialog = QColorDialog(initial, self)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        dialog.setWindowTitle(title)

        try:
            from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND, MAIN_WINDOW_BACKGROUND_COLORREF
            dialog.setStyleSheet(f"QColorDialog {{ background-color: {MAIN_WINDOW_BACKGROUND}; }}")

            import os
            import ctypes
            if os.name == "nt":
                dwmapi = ctypes.windll.dwmapi
                hwnd = int(dialog.winId())
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

        QTimer.singleShot(0, lambda dlg=dialog: self._localize_color_dialog(dlg))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return QColor()
        return dialog.selectedColor()

    def _colored_svg_icon(self, svg_name: str, color: QColor) -> QIcon:
        p = self._assets_dir() / svg_name
        if not p.exists():
            return QIcon()
        try:
            svg_text = p.read_text(encoding="utf-8")
            if svg_name == "icon_text_color.svg":
                svg_text = svg_text.replace('stroke="#ef4444"', f'stroke="{color.name()}"')
            elif svg_name == "icon_bg_color.svg":
                svg_text = svg_text.replace('fill="#fef08a"', f'fill="{color.name()}"')
            pixmap = QPixmap()
            if pixmap.loadFromData(svg_text.encode("utf-8"), b"SVG"):
                return QIcon(pixmap)
        except Exception:
            pass
        return QIcon(str(p))

    def _localize_color_dialog(self, dialog: QColorDialog) -> None:
        text_map = {
            "Basic colors": "基础颜色",
            "Custom colors": "自定义颜色",
            "Pick Screen Color": "吸取屏幕颜色",
            "Add to Custom Colors": "添加到自定义颜色",
            "Hue:": "色相:",
            "Sat:": "饱和度:",
            "Val:": "亮度:",
            "Red:": "红色:",
            "Green:": "绿色:",
            "Blue:": "蓝色:",
            "Alpha channel:": "透明度:",
            "HTML:": "色值:",
        }
        try:
            for label in dialog.findChildren(QLabel):
                raw = str(label.text() or "")
                key = raw.replace("&", "").strip()
                if key in text_map:
                    label.setText(text_map[key])
            for button in dialog.findChildren(QPushButton):
                raw = str(button.text() or "")
                key = raw.replace("&", "").strip()
                if key in text_map:
                    button.setText(text_map[key])
            box = dialog.findChild(QDialogButtonBox)
            if box is not None:
                box.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
                ok = box.button(QDialogButtonBox.StandardButton.Ok)
                cancel = box.button(QDialogButtonBox.StandardButton.Cancel)
                if ok is not None:
                    ok.setText("确定")
                if cancel is not None:
                    cancel.setText("取消")

                layout = box.layout()
                if layout is not None and ok is not None and cancel is not None:
                    idx_ok = layout.indexOf(ok)
                    idx_cancel = layout.indexOf(cancel)
                    if idx_ok != -1 and idx_cancel != -1 and idx_ok < idx_cancel:
                        layout.removeWidget(cancel)
                        layout.insertWidget(idx_ok, cancel)
        except Exception:
            pass




























    _build_annotation_style_group = SettingsDialog._build_annotation_style_group
    _refresh_color_button = SettingsDialog._refresh_color_button
    _choose_annotation_color = SettingsDialog._choose_annotation_color
    _localize_color_dialog = SettingsDialog._localize_color_dialog
    _row_dir = SettingsDialog._row_dir
    _browse_dir = SettingsDialog._browse_dir
    _position_translator_api_key_eye = SettingsDialog._position_translator_api_key_eye
    _position_translator_api_url_role_label = SettingsDialog._position_translator_api_url_role_label
    _update_api_url_role_label = SettingsDialog._update_api_url_role_label
    _toggle_translator_api_key_visibility = SettingsDialog._toggle_translator_api_key_visibility
    _INLINE_ACTION_BUTTON_HEIGHT = SettingsDialog._INLINE_ACTION_BUTTON_HEIGHT
    _TRANSLATOR_FORM_LABEL_WIDTH = SettingsDialog._TRANSLATOR_FORM_LABEL_WIDTH
    _MODEL_ROLE_LABEL_WIDTH = SettingsDialog._MODEL_ROLE_LABEL_WIDTH
    _MODEL_ROLE_COMBO_WIDTH = SettingsDialog._MODEL_ROLE_COMBO_WIDTH
    _MODEL_ID_COMBO_MIN_WIDTH = SettingsDialog._MODEL_ID_COMBO_MIN_WIDTH
    _MODEL_ID_COMBO_MAX_WIDTH = SettingsDialog._MODEL_ID_COMBO_MAX_WIDTH
    _model_combos = SettingsDialog._model_combos
    _current_translator_role = SettingsDialog._current_translator_role
    _adjacent_combo_value = SettingsDialog._adjacent_combo_value
    _add_model_to_combos = SettingsDialog._add_model_to_combos
    _refresh_model_combos_after_catalog_change = SettingsDialog._refresh_model_combos_after_catalog_change
    _sync_role_models_from_combos = SettingsDialog._sync_role_models_from_combos
    _repaint_model_combos = SettingsDialog._repaint_model_combos
    _save_current_translator_model_config = SettingsDialog._save_current_translator_model_config
    _load_translator_model_config = SettingsDialog._load_translator_model_config
    _on_translator_model_changed = SettingsDialog._on_translator_model_changed
    _on_model_edit_finished = SettingsDialog._on_model_edit_finished
    _on_add_model_clicked = SettingsDialog._on_add_model_clicked
    _broadcast_model_rename = SettingsDialog._broadcast_model_rename
    _translator_field_config = SettingsDialog._translator_field_config
    _translator_required_fields = SettingsDialog._translator_required_fields
    _sync_translator_config_controls = SettingsDialog._sync_translator_config_controls
    _translator_fields_complete = SettingsDialog._translator_fields_complete
    _remember_current_translator_model = SettingsDialog._remember_current_translator_model
    _delete_translator_model = SettingsDialog._delete_translator_model
    _delete_translator_models = SettingsDialog._delete_translator_models
    _prompt_button_names = SettingsDialog._prompt_button_names
    _edit_translator_prompt = SettingsDialog._edit_translator_prompt
    _current_translator_runtime_config = SettingsDialog._current_translator_runtime_config
    _clear_translator_proxy_selection = SettingsDialog._clear_translator_proxy_selection
    _set_current_model_codex_switch_checked = SettingsDialog._set_current_model_codex_switch_checked
    _current_model_codex_config_model = SettingsDialog._current_model_codex_config_model
    _ensure_current_model_codex_binding = SettingsDialog._ensure_current_model_codex_binding
    _is_current_model_codex_configured = SettingsDialog._is_current_model_codex_configured
    _sync_current_model_codex_ui = SettingsDialog._sync_current_model_codex_ui
    _sync_provider_popup_model_cache = SettingsDialog._sync_provider_popup_model_cache
    _provider_popup_models_by_provider = SettingsDialog._provider_popup_models_by_provider
    _provider_popup_current_model = SettingsDialog._provider_popup_current_model
    _codex_highlighted_model_names = SettingsDialog._codex_highlighted_model_names
    _claude_code_config_model = SettingsDialog._claude_code_config_model
    _is_current_model_claude_code_configured = SettingsDialog._is_current_model_claude_code_configured
    _claude_code_highlighted_model_names = SettingsDialog._claude_code_highlighted_model_names
    _provider_highlighted_model_colors = SettingsDialog._provider_highlighted_model_colors
    _codex_action_label_for_model = SettingsDialog._codex_action_label_for_model
    _claude_code_action_label_for_model = SettingsDialog._claude_code_action_label_for_model
    _apply_current_model_codex_config_for_model = SettingsDialog._apply_current_model_codex_config_for_model
    _apply_current_model_claude_code_config_for_model = SettingsDialog._apply_current_model_claude_code_config_for_model
    _local_translation_service_config = SettingsDialog._local_translation_service_config
    _set_local_translation_service_switch_checked = SettingsDialog._set_local_translation_service_switch_checked
    _sync_local_translation_service_state = SettingsDialog._sync_local_translation_service_state
    _test_translator_connection = SettingsDialog._test_translator_connection
    _position_translator_proxy_test_btn = SettingsDialog._position_translator_proxy_test_btn
    _test_proxy_connection = SettingsDialog._test_proxy_connection
    _on_proxy_test_finished = SettingsDialog._on_proxy_test_finished
    _is_tencent_model = SettingsDialog._is_tencent_model
    _on_download_mt15_clicked = SettingsDialog._on_download_mt15_clicked
    _on_download_mt2_clicked = SettingsDialog._on_download_mt2_clicked
    _start_silent_download = SettingsDialog._start_silent_download
    _on_download_progress = SettingsDialog._on_download_progress
    _on_download_finished = SettingsDialog._on_download_finished
    _on_download_cancel_clicked = SettingsDialog._on_download_cancel_clicked
    _on_add_provider_clicked = SettingsDialog._on_add_provider_clicked
    _on_edit_provider_clicked = SettingsDialog._on_edit_provider_clicked
    delete_provider_group = SettingsDialog.delete_provider_group
    clear_provider_models = SettingsDialog.clear_provider_models
    _on_edit_model_id_clicked = SettingsDialog._on_edit_model_id_clicked
    _clear_translator_fields_for_new_provider = SettingsDialog._clear_translator_fields_for_new_provider
    _on_get_models_clicked = SettingsDialog._on_get_models_clicked
    _on_new_api_model_clicked = SettingsDialog._on_new_api_model_clicked
    _on_fetch_models_finished = SettingsDialog._on_fetch_models_finished
    _show_fetched_model_id_popup = SettingsDialog._show_fetched_model_id_popup
    _on_model_list_plus_clicked = SettingsDialog._on_model_list_plus_clicked
    _fill_model_combos = SettingsDialog._fill_model_combos
    _set_combo_selected_value = SettingsDialog._set_combo_selected_value
    _combo_selected_value = SettingsDialog._combo_selected_value
    _combo_item_value = SettingsDialog._combo_item_value
    _normalized_api_url = SettingsDialog._normalized_api_url
    _position_translator_get_models_btn = SettingsDialog._position_translator_get_models_btn
    _position_translator_provider_btn = SettingsDialog._position_translator_provider_btn
    _position_translator_provider_hover_hint = SettingsDialog._position_translator_provider_hover_hint
    _position_translator_model_name_btns = SettingsDialog._position_translator_model_name_btns
    _sync_model_role_combo_widths = SettingsDialog._sync_model_role_combo_widths
    _on_model_combo_activated = SettingsDialog._on_model_combo_activated
    _is_current_fetched_model_id = SettingsDialog._is_current_fetched_model_id
    _sync_model_note_from_fetched_model_id = SettingsDialog._sync_model_note_from_fetched_model_id
    _on_model_id_activated = SettingsDialog._on_model_id_activated
    _select_model_id_from_list = SettingsDialog._select_model_id_from_list
    _first_model_for_provider = SettingsDialog._first_model_for_provider
    _set_translator_provider_value = SettingsDialog._set_translator_provider_value
    _select_provider_for_new_model = SettingsDialog._select_provider_for_new_model
    _move_current_model_to_provider = SettingsDialog._move_current_model_to_provider
    _save_current_model_with_provider = SettingsDialog._save_current_model_with_provider
    _on_provider_selected_for_new_model = SettingsDialog._on_provider_selected_for_new_model
    _on_provider_activated = SettingsDialog._on_provider_activated
    _fill_model_name_combobox_items = SettingsDialog._fill_model_name_combobox_items
    _style_translator_footer_button = SettingsDialog._style_translator_footer_button




















































































































































































    def moveEvent(self, event) -> None:
        try:
            if self._ui_save_timer is not None and not bool(self._ui_restoring):
                # 拖动期间每个 moveEvent 都会重置计时器，停止拖动 600ms 后才落盘一次，
                # 避免高频触发 load+save 全量设置 IO
                self._ui_save_timer.start(600)
        except Exception:
            pass
        super().moveEvent(event)

    def resizeEvent(self, event) -> None:
        try:
            if self._ui_save_timer is not None and not bool(self._ui_restoring):
                self._remember_current_resizable_page_size()
                self._ui_save_timer.start(600)
        except Exception:
            pass
        super().resizeEvent(event)
        self._sync_page_right_edge_guard()
        QTimer.singleShot(0, self._sync_page_right_edge_guard)



















































            # 进入标注微调阶段时，不要在此置空干净原图，直到最终 close_all 销毁整个截图工具时再释放，从而使拖拽松开的裁切能 100% 稳定进行内存零闪烁极速直切
            # self._capture_frozen_screen_bgr = None
            # self._capture_frozen_screen_origin_px = (0, 0)
















    _TOAST_INFO_DURATION_MS = 2600
    _TOAST_SAVE_DURATION_MS = 3200
    _TOAST_ERROR_DURATION_MS = 4200













    def set_output_format(self, fmt: str) -> None:
        fmt = (fmt or "png").lower()
        if fmt == "jpg":
            self._fmt_jpg.setChecked(True)
        elif fmt == "pdf":
            self._fmt_pdf.setChecked(True)
        else:
            self._fmt_png.setChecked(True)
