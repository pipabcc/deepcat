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
QTimer = DynamicModuleAttribute("deepcat.ui.main_window.window", "QTimer")


class WindowEmbeddedSettingsMixin:
    def _connect_embedded_settings(self) -> None:
        self._img_browse.clicked.connect(lambda: self._browse_dir(self._img_dir))
        self._img_dir.editingFinished.connect(self._apply_dirs)
        self._autostart.stateChanged.connect(self._apply_autostart)
        self._notifications.stateChanged.connect(self._apply_notifications)
        self._app_log_switch.stateChanged.connect(self._apply_logging_settings)
        self._crash_log_switch.stateChanged.connect(self._apply_logging_settings)
        self._check_update_btn.clicked.connect(lambda: self._check_for_updates(manual=True))
        self._auto_update_switch.stateChanged.connect(self._apply_auto_update)
        self._auto_snap_enabled.stateChanged.connect(self._apply_auto_snap_enabled)
        self._feature_todo_switch.stateChanged.connect(self._apply_feature_visibility)
        self._feature_later_read_switch.stateChanged.connect(self._apply_feature_visibility)
        self._feature_clipboard_history_switch.stateChanged.connect(self._apply_feature_visibility)
        self._feature_table_notes_switch.stateChanged.connect(self._apply_feature_visibility)
        self._save_button_mode.currentIndexChanged.connect(self._apply_save_button_mode)
        self._previous_capture_action_combo.currentIndexChanged.connect(self._apply_previous_capture_action)
        self._post_capture_button_style_combo.currentIndexChanged.connect(self._apply_post_capture_button_style)
        self._hotkey.keySequenceChanged.connect(self._apply_hotkey)
        self._scroll_hotkey_edit.keySequenceChanged.connect(self._apply_scroll_hotkey)
        self._later_read_hotkey_edit.keySequenceChanged.connect(self._apply_later_read_hotkey)
        self._ai_qa_hotkey_edit.keySequenceChanged.connect(self._apply_ai_qa_hotkey)
        self._selection_translate_hotkey_edit.keySequenceChanged.connect(self._apply_selection_translate_hotkey)
        self._selection_popup_hotkey_edit.keySequenceChanged.connect(self._apply_selection_popup_hotkey)
        self._mode.currentIndexChanged.connect(self._on_capture_mode_ui_changed)
        self._save_mode.currentIndexChanged.connect(self._on_save_mode_ui_changed)
        self._translator_model.activated.connect(lambda idx: self._on_model_combo_activated(self._translator_model, idx, "translate"))
        self._qa_model.activated.connect(lambda idx: self._on_model_combo_activated(self._qa_model, idx, "qa"))
        self._translator_model.deleteRequested.connect(lambda model_name: self._delete_translator_model(model_name, "translate"))
        self._qa_model.deleteRequested.connect(lambda model_name: self._delete_translator_model(model_name, "qa"))
        self._translator_model.batchDeleteRequested.connect(lambda model_names: self._delete_translator_models(model_names, "translate"))
        self._qa_model.batchDeleteRequested.connect(lambda model_names: self._delete_translator_models(model_names, "qa"))
        if self._translator_model.lineEdit() is not None:
            self._translator_model.lineEdit().editingFinished.connect(lambda: self._on_model_edit_finished(self._translator_model, "translate"))
        if self._qa_model.lineEdit() is not None:
            self._qa_model.lineEdit().editingFinished.connect(lambda: self._on_model_edit_finished(self._qa_model, "qa"))
        self._translator_api_url.editingFinished.connect(self._apply_translator_settings)
        self._translator_model_name.editingFinished.connect(self._apply_translator_settings)
        self._translator_model_note.editingFinished.connect(self._apply_translator_settings)
        self._translator_api_key.editingFinished.connect(self._apply_translator_settings)
        self._translator_use_proxy.stateChanged.connect(self._apply_translator_settings)
        self._translator_proxy_url.editingFinished.connect(self._apply_translator_settings)
        self._fill_codex_config_switch.stateChanged.connect(self._apply_current_model_codex_config_enabled)
        self._translator_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._translator_test_btn.clicked.connect(self._test_translator_connection)
        self._selection_translate_switch.stateChanged.connect(self._apply_selection_translate_enabled)
        self._selection_popup_switch.stateChanged.connect(self._apply_selection_popup_enabled)
        self._ocr_translate_switch.stateChanged.connect(self._apply_ocr_translate_enabled)
        self._auto_copy_switch.stateChanged.connect(self._apply_auto_copy_answers)
        self._local_translation_service_switch.stateChanged.connect(self._apply_local_translation_service_enabled)
        self._codex_config_switch.stateChanged.connect(self._apply_codex_config_enabled)
        self._connect_todo_resource_controls()
        self._later_read_enabled.stateChanged.connect(self._apply_later_read_enabled)
        self._later_read_search.textChanged.connect(self._on_later_read_search_text_changed)
        self._later_read_filter_btn.clicked.connect(self._edit_later_read_filter_keywords)
        self._later_read_batch_select_all.stateChanged.connect(self._later_read_batch_select_all_items)
        self._later_read_batch_delete.clicked.connect(self._batch_delete_later_read)
        self._later_read_batch_done.clicked.connect(lambda *_: self._toggle_later_read_batch_mode(False))

    def _style_main_window(self) -> None:
        checkbox_check_icon_url = str((self._assets_dir() / "icon_checkbox_check.svg").as_posix())
        checkbox_check_disabled_icon_url = str((self._assets_dir() / "icon_checkbox_check_disabled.svg").as_posix())
        radio_dot_icon_url = str((self._assets_dir() / "icon_radio_dot.svg").as_posix())
        self.setStyleSheet("""
            QMainWindow {
                background: __MAIN_BACKGROUND__;
            }
            QDialog {
                background: #f8fafc;
            }
            QWidget {
                font-family: "Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 12px;
                color: #111827;
            }
            #Root {
                background: __MAIN_BACKGROUND__;
            }
            #MainContent, #ContentStack, #AppPage {
                background: __MAIN_BACKGROUND__;
            }
            #TitleBottomLine {
                background: __MAIN_BACKGROUND__;
                border: none;
                min-height: 1px;
                max-height: 1px;
            }
            #Sidebar {
                background: __MAIN_BACKGROUND__;
                border-right: 1px solid #f1f5f9;
            }
            QLineEdit#SuperSearch {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding-left: 8px;
                padding-right: 20px;
                font-size: 12px;
                color: #334155;
            }
            QLineEdit#SuperSearch:hover, QLineEdit#SuperSearch:focus {
                border-color: #94a3b8;
                background: #ffffff;
            }
            #WindowBrand {
                font-weight: 800;
                font-size: 14px;
                color: #1e293b;
                letter-spacing: 0.5px;
            }
            QPushButton#NavButton {
                text-align: left;
                padding: 0 16px 0 22px;
                border: none;
                border-radius: 8px;
                background: __MAIN_BACKGROUND__;
                color: #334155;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton#NavButton:hover {
                background: rgba(15, 23, 42, 0.035);
                color: #0f172a;
            }
            QPushButton#NavButton:checked {
                background: #e8eef2;
                color: #0f172a;
                border-radius: 8px;
                padding-left: 22px;
                font-weight: 700;
            }
            QPushButton#SidebarQuickActionBtn {
                background: transparent;
                border: none;
                border-radius: 8px;
                width: 32px;
                height: 32px;
                min-width: 32px;
                min-height: 32px;
                max-width: 32px;
                max-height: 32px;
                padding: 0px;
            }

            QPushButton#SidebarQuickActionBtn:hover {
                background: rgba(15, 23, 42, 0.035);
            }
            QPushButton#SidebarQuickActionBtn:pressed {
                background: rgba(15, 23, 42, 0.08);
            }
            #PageHeader {
                background: transparent;
            }
            #HeaderTitle {
                font-size: 22px;
                font-weight: 900;
                color: #111827;
            }
            #HeaderSubtitle {
                color: #6b7280;
                font-size: 12px;
            }
            #PageScroll,
            QWidget#PageContent,
            QWidget#PageScrollViewport {
                background: __MAIN_BACKGROUND__;
            }
            QGroupBox#SectionGroup, QGroupBox {
                background: __MODULE_BACKGROUND__;
                border: 1px solid #f1f5f9;
                border-radius: 12px;
                margin-top: 2px;
                padding: 4px 16px 4px 16px;
                font-weight: 800;
                font-size: 13px;
                color: #111827;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 16px;
                padding: 0 6px;
                background: transparent;
                color: #475569;
                font-weight: 700;
            }
            QLabel {
                color: #111827;
            }
            QLineEdit, QKeySequenceEdit, QComboBox, QSpinBox {
                min-height: 28px;
                max-height: 32px;
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 2px 10px;
                color: #111827;
            }
            QLineEdit:hover, QKeySequenceEdit:hover, QComboBox:hover, QSpinBox:hover {
                border: 1px solid #cbd5e1;
            }
            QLineEdit:focus, QKeySequenceEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 1px solid #cbd5e1;
                background: white;
            }
            QLineEdit[missingRequired="true"], QComboBox[missingRequired="true"] {
                border: 1px solid #ef4444;
                background: #fff7f7;
            }
            QLineEdit[missingRequired="true"]:focus, QComboBox[missingRequired="true"]:focus {
                border: 1px solid #dc2626;
                background: #fffafa;
            }
            QComboBox::drop-down {
                border: none;
                width: 28px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 0px;
                height: 0px;
                border: none;
            }
            QSpinBox::up-arrow, QSpinBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
            }
            QCheckBox {
                spacing: 9px;
                color: #111827;
            }
            QCheckBox:disabled, QLabel:disabled, QRadioButton:disabled, QComboBox:disabled {
                color: #9ca3af;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border-radius: 4px;
                border: 1px solid #cbd5e1;
                background: white;
            }
            QCheckBox::indicator:hover, QRadioButton::indicator:hover {
                border-color: #94a3b8;
            }
            QCheckBox::indicator:checked {
                border-color: #1e293b;
                background: #1e293b;
                image: url('__CHECKBOX_ICON__');
            }
            QCheckBox::indicator:disabled {
                border-color: #cbd5e1;
                background: #f8fafc;
            }
            QCheckBox::indicator:checked:disabled {
                border-color: #9ca3af;
                background: #f8fafc;
                image: url('__CHECKBOX_DISABLED_ICON__');
            }
            QRadioButton {
                spacing: 9px;
            }
            QRadioButton::indicator {
                width: 14px;
                height: 14px;
                border-radius: 7px;
                border: 1px solid #cbd5e1;
                background: white;
            }
            QRadioButton::indicator:checked {
                border: 1px solid #1e293b;
                background: white;
                image: url('__RADIO_DOT_ICON__');
            }
            QSlider {
                min-height: 20px;
            }
            QSlider::groove:horizontal {
                height: 4px;
                border-radius: 2px;
                background: #e5e7eb;
            }
            QSlider::sub-page:horizontal {
                height: 4px;
                border-radius: 2px;
                background: #1e293b;
            }
            QSlider::handle:horizontal {
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
                background: white;
                border: 1.5px solid #1e293b;
            }
            QTableWidget#NotesTable, QTableWidget#NotesSumTable {
                background: white;
                border: 1px solid #f1f5f9;
                border-radius: 8px;
                gridline-color: #f1f5f9;
                selection-background-color: #f1f5f9;
            }
            QTableWidget#NotesTable::item:selected, QTableWidget#NotesSumTable::item:selected {
                background-color: #f1f5f9;
            }
            QTableWidget#NotesSumTable {
                background: #f8fafc;
                font-weight: 800;
            }
            QTextEdit#NotesEditor {
                background: transparent;
                border: none;
                padding: 0px;
                font-size: 13px;
                font-family: "Microsoft YaHei";
            }
            QFrame#RoundedTextEditContainer {
                background: white;
                border: none;
                border-radius: 8px;
            }
            QFrame#RoundedTextEditContainer[hovered="true"] {
                border: none;
            }
            QFrame#RoundedTextEditContainer[focused="true"] {
                border: none;
            }
            QWidget#NoteToolbar, QWidget#TableToolbar {
                background: #f8fafc;
                border: 1px solid #f1f5f9;
                border-radius: 8px;
            }
            QWidget#NoteToolbar QToolButton, QWidget#TableToolbar QToolButton {
                min-width: 28px;
                min-height: 26px;
                border-radius: 5px;
                border: none;
                background: transparent;
                color: #1f2937;
            }
            QWidget#NoteToolbar QToolButton:hover, QWidget#TableToolbar QToolButton:hover {
                background: rgba(15, 23, 42, 0.045);
                color: #0f172a;
            }
            QWidget#NoteToolbar QToolButton:checked, QWidget#TableToolbar QToolButton:checked {
                background: rgba(59, 130, 246, 0.09);
                color: #0f172a;
            }
            QPushButton#BtnPrimary {
                background: #1e293b;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 0 10px;
                min-height: 42px;
                min-width: 112px;
                max-width: 132px;
                font-size: 15px;
                font-weight: 800;
            }
            QPushButton#BtnPrimary:hover { background: #334155; }
            QPushButton#BtnPrimary:pressed { background: #0f172a; }
            QPushButton#BtnSidebarFolder {
                text-align: left;
                background: transparent;
                color: #475569;
                border: none;
                border-radius: 8px;
                padding: 0 16px 0 22px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton#BtnSidebarFolder:hover {
                background: rgba(15, 23, 42, 0.035);
                color: #0f172a;
            }
            QPushButton#BtnSidebarFolder:pressed {
                background: rgba(15, 23, 42, 0.06);
            }
            QPushButton#BtnSmallPrimary, QPushButton {
                background: #1e293b;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 5px 12px;
                font-weight: 700;
            }
            QPushButton#BtnSmallSecondary {
                background: #f1f5f9;
                color: #334155;
                border: none;
                border-radius: 8px;
                padding: 5px 12px;
                font-weight: 700;
            }
            QPushButton#BtnSmallSecondary:hover { background: #e2e8f0; color: #0f172a; }
            QPushButton#BtnSmallDanger {
                background: #fef2f2;
                color: #dc2626;
                border: none;
                border-radius: 8px;
                padding: 5px 12px;
                font-weight: 700;
            }
            QPushButton#BtnSmallDanger:hover { background: #fee2e2; }
            QPushButton:hover { background: #334155; }
            QPushButton:pressed { background: #0f172a; }
            QPushButton:disabled { background: #cbd5e1; color: rgba(255,255,255,0.82); }
            QWidget#TabBar, QScrollArea#TabScrollArea, QWidget#TabScrollWidget, QScrollArea#TabScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QPushButton#TabButton, QPushButton#TabButtonActive {
                border: none;
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 13px;
                font-weight: 600;
                max-width: 150px;
            }
            QPushButton#TabButton {
                background: transparent;
                color: #6b7280;
            }
            QPushButton#TabButton:hover {
                background: #f3f4f6;
                color: #374151;
            }
            QPushButton#TabButtonActive {
                background: #e5e7eb;
                color: #1f2937;
            }
            QToolButton#TabAddButton, QToolButton#TabListButton {
                background: transparent;
                color: #6b7280;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }
            QToolButton#TabAddButton {
                font-size: 20px;
                padding-bottom: 3px;
            }
            QToolButton#TabAddButton:hover, QToolButton#TabListButton:hover {
                background: #f3f4f6;
                color: #374151;
                border: none;
            }
            QToolButton#TabArrowButton {
                background: transparent;
                color: #6b7280;
                border: none;
                border-radius: 6px;
                font-size: 10px;
                font-weight: 700;
            }
            QToolButton#TabArrowButton:hover {
                background: #f3f4f6;
                color: #374151;
            }
            QLabel#KeyChip {
                min-width: 68px;
                min-height: 28px;
                border-radius: 7px;
                border: 1px solid #cbd5e1;
                background: #f8fafc;
                color: #22324c;
                font-weight: 800;
            }
            #InfoTip {
                background: __MODULE_BACKGROUND__;
                border: 1px solid #f1f5f9;
                border-radius: 12px;
                padding: 12px;
            }
            #InfoDot {
                border-radius: 16px;
                background: rgba(30, 41, 59, 0.08);
                color: #1e293b;
                border: none;
                font-size: 18px;
                font-weight: 900;
            }
            QLabel#DriveCleanerStatus,
            QLabel#DriveCleanerSummary {
                color: #64748b;
                font-size: 12px;
                font-weight: 600;
            }
            QTableWidget#DriveCleanerTable {
                background: #ffffff;
                border: 1px solid #f1f5f9;
                border-radius: 10px;
                gridline-color: #f1f5f9;
                alternate-background-color: #f8fafc;
                selection-background-color: rgba(30, 41, 59, 0.08);
                selection-color: #111827;
            }
            QTableWidget#DriveCleanerTable QHeaderView::section {
                background: #f8fafc;
                color: #475569;
                border: none;
                border-bottom: 1px solid #e2e8f0;
                padding: 5px 8px;
                font-weight: 800;
            }
            QTextEdit#DriveCleanerDetails {
                background: #ffffff;
                border: 1px solid #f1f5f9;
                border-radius: 10px;
                padding: 8px;
                color: #334155;
                font-size: 12px;
            }
            QProgressBar {
                background: #e5e7eb;
                border: none;
                border-radius: 6px;
                text-align: center;
                color: transparent;
            }
            QProgressBar::chunk {
                background: #1e293b;
                border-radius: 6px;
            }
            QWidget#ResourceToolsGrid {
                background: transparent;
            }
            QToolButton#ResourceToolTile {
                background: transparent;
                border: none;
                border-radius: 12px;
                color: #111827;
                font-size: 13px;
                font-weight: 700;
                padding: 8px 4px;
            }
            QToolButton#ResourceToolTile:hover {
                background: rgba(15, 23, 42, 0.045);
            }
            QToolButton#ResourceToolTile:pressed {
                background: rgba(15, 23, 42, 0.08);
            }
            QToolButton#ResourceShortcutAddButton {
                background: #f1f5f9;
                border: none;
                border-radius: 24px;
                color: #111827;
                font-size: 20px;
                font-weight: normal;
                padding: 0px;
                padding-bottom: 2px;
            }
            QToolButton#ResourceShortcutAddButton:hover {
                background: #e2e8f0;
            }
            QToolButton#ResourceShortcutAddButton:pressed {
                background: #cbd5e1;
            }
            #TodoArea {
                background: transparent;
                border: none;
                padding: 0px;
            }
            QCalendarWidget#TodoCalendar {
                background: __MODULE_BACKGROUND__;
                border: 1px solid #f1f5f9;
                border-radius: 12px;
                color: #1f2937;
                padding: 8px;
            }
            #TodoSide {
                background: __MODULE_BACKGROUND__;
                border: 1px solid #f1f5f9;
                border-radius: 12px;
            }
            QWidget#TodoOverview {
                background: __MODULE_BACKGROUND__;
                border: 1px solid #f1f5f9;
                border-radius: 12px;
            }
            QLabel#TodoOverviewTitle {
                color: #111827;
                font-size: 17px;
                font-weight: 900;
            }
            QLabel#TodoOverviewSubtitle {
                color: #475569;
                font-size: 13px;
                font-weight: 800;
                padding-top: 2px;
            }
            QLabel#TodoNextLabel, QLabel#TodoNextReminderLabel {
                color: #475569;
                background: #f8fafc;
                border: 1px solid #f1f5f9;
                border-radius: 8px;
                padding: 10px;
                font-size: 12px;
                font-weight: 700;
                line-height: 1.4;
            }
            QFrame#TodoStatChip {
                background: #f8fafc;
                border: 1px solid #f1f5f9;
                border-radius: 8px;
            }
            QFrame#TodoStatChip[active="true"] {
                background: #eff6ff;
                border: 1px solid #93c5fd;
            }
            QLabel#TodoStatValue {
                color: #111827;
                font-size: 22px;
                font-weight: 900;
            }
            QLabel#TodoStatTitle {
                color: #64748b;
                font-size: 12px;
                font-weight: 700;
            }
            QCalendarWidget#TodoCalendar QToolButton {
                background: transparent;
                color: #1f2937;
                border: none;
                padding: 3px 6px;
                font-size: 14px;
                font-weight: 900;
            }
            QCalendarWidget#TodoCalendar QToolButton::menu-indicator {
                image: none;
                width: 0px;
            }
            QCalendarWidget#TodoCalendar QWidget#qt_calendar_navigationbar {
                background: __MODULE_BACKGROUND__;
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
            }
            QCalendarWidget#TodoCalendar QAbstractItemView {
                outline: none;
                selection-background-color: transparent;
                selection-color: #1f2937;
                border: none;
                background: __MODULE_BACKGROUND__;
                color: #1f2937;
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
                font-size: 14px;
            }
            QCalendarWidget#TodoCalendar QAbstractItemView::item,
            QCalendarWidget#TodoCalendar QAbstractItemView::item:selected,
            QCalendarWidget#TodoCalendar QAbstractItemView::item:focus {
                outline: none;
                border: none;
                background: transparent;
            }
            QCalendarWidget#TodoCalendar QSpinBox {
                min-height: 20px;
                max-height: 24px;
                padding: 1px 4px;
                border-radius: 6px;
                background: white;
                border: 1px solid #e2e8f0;
                margin: 3px 2px;
                font-size: 14px;
            }
            QLabel#TodoDateTitle {
                color: #111827;
                font-size: 17px;
                font-weight: 900;
            }
            QLabel#TodoOverdueBanner {
                color: #b91c1c;
                background: #fff7f7;
                border: 1px solid #fecaca;
                border-radius: 8px;
                padding: 5px 10px;
                font-size: 12px;
                font-weight: 900;
            }
            QLabel#TodoCalendarLegend {
                color: #64748b;
                background: transparent;
                font-size: 11px;
                font-weight: 800;
            }
            QToolButton#TodoAddButton {
                min-width: 32px;
                min-height: 32px;
                max-width: 32px;
                max-height: 32px;
                background: #f1f5f9;
                border: 1px solid #e2e8f0;
                border-radius: 16px;
                color: #1e293b;
                font-size: 22px;
                font-weight: 700;
                padding: 0px;
            }
            QToolButton#TodoAddButton:hover {
                color: #0f172a;
                background: #e2e8f0;
                border-radius: 16px;
            }
            QToolButton#TodoAddButton:pressed {
                background: #cbd5e1;
            }
            QListWidget#TodoList {
                background: transparent;
                border: none;
                color: #111827;
                font-size: 13px;
            }
            QListWidget#TodoList::item {
                border-bottom: 1px solid #f1f5f9;
                padding: 0px;
                font-size: 13px;
            }
            QListWidget#TodoList::item:selected {
                color: #111827;
                background: rgba(30, 41, 59, 0.06);
            }
            QWidget#TodoListItemWidget {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
            }
            QWidget#TodoListItemWidget[important="true"] {
                background: #fff7f7;
                border: 1px solid #fecaca;
                border-radius: 8px;
            }
            QLabel#TodoItemTitle {
                color: #111827;
                font-size: 13px;
                font-weight: 800;
            }
            QLabel#TodoItemBody {
                color: #64748b;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#TodoItemStatus {
                color: #64748b;
                font-size: 11px;
                font-weight: 800;
            }
            QLabel#TodoImportantBadge {
                color: #b91c1c;
                background: #fee2e2;
                border: 1px solid #fecaca;
                border-radius: 9px;
                padding: 0px 7px;
                font-size: 11px;
                font-weight: 900;
            }
            QLabel#TodoRepeatBadge {
                color: #475569;
                background: #eef2ff;
                border: 1px solid #c7d2fe;
                border-radius: 9px;
                padding: 0px 7px;
                font-size: 11px;
                font-weight: 900;
            }
            QCheckBox#TodoCompleteCheck::indicator {
                width: 15px;
                height: 15px;
                border-radius: 5px;
                border: 1px solid #cbd5e1;
                background: #ffffff;
            }
            QCheckBox#TodoCompleteCheck::indicator:hover {
                border-color: #94a3b8;
            }
            QCheckBox#TodoCompleteCheck::indicator:checked {
                border-color: #16a34a;
                background: #16a34a;
                image: url('__CHECKBOX_ICON__');
            }
            QToolButton#TodoItemIconButton {
                background: rgba(255,255,255,0.58);
                border: 1px solid rgba(226, 232, 240, 0.72);
                border-radius: 6px;
                color: rgba(30, 41, 59, 0.58);
            }
            QWidget#TodoListItemWidget[actionsActive="true"] QToolButton#TodoItemIconButton,
            QToolButton#TodoItemIconButton:hover {
                background: rgba(255,255,255,0.92);
                border-color: rgba(30, 41, 59, 0.3);
                color: #1e293b;
            }
            QCheckBox#LaterReadSwitch {
                spacing: 0px;
            }
            QCheckBox#LaterReadSwitch::indicator {
                width: 40px;
                height: 22px;
                border-radius: 11px;
                background: #cbd5e1;
                border: 1px solid #e2e8f0;
            }
            QCheckBox#LaterReadSwitch::indicator:checked {
                background: #1e293b;
                border-color: #1e293b;
            }
            QLineEdit#LaterReadSearch {
                min-height: 30px;
                max-height: 30px;
                padding: 0 10px;
                border: 1px solid #e2e8f0;
                border-radius: 7px;
                background: white;
                color: #1f2937;
                font-size: 13px;
            }
            QToolButton#LaterReadFilterButton,
            QToolButton#LaterReadSortButton {
                min-height: 30px;
                max-height: 30px;
                padding: 0 12px;
                border-radius: 7px;
                border: 1px solid #e2e8f0;
                background: #ffffff;
                color: #22324c;
                font-size: 13px;
                font-weight: 800;
            }
            QToolButton#LaterReadSortButton::menu-indicator {
                image: none;
                width: 0px;
            }
            QToolButton#LaterReadFilterButton:hover,
            QToolButton#LaterReadSortButton:hover {
                background: rgba(30, 41, 59, 0.06);
                border-color: rgba(30, 41, 59, 0.3);
            }
            QLabel#LaterReadStats {
                color: #6b7280;
                font-size: 12px;
                background: transparent;
            }
            QWidget#LaterReadBatchHotspot {
                background: transparent;
                border-radius: 7px;
            }
            QListWidget#LaterReadList {
                background: transparent;
                border: 1px solid #f1f5f9;
                border-radius: 12px;
                color: #111827;
                font-size: 13px;
            }
            QListWidget#LaterReadList::item {
                border-bottom: none;
                padding: 0px;
            }
            QListWidget#LaterReadList::item:selected {
                color: #111827;
                background: rgba(30, 41, 59, 0.06);
            }
            QWidget#LaterReadItemWidget {
                background: transparent;
                border-bottom: none;
            }
            QPushButton#LaterReadTitleButton {
                text-align: left;
                border: none;
                background: transparent;
                color: #111827;
                font-size: 13px;
                font-weight: 400;
                padding: 0px;
            }
            QPushButton#LaterReadTitleButton:hover {
                color: #1368e8;
            }
            QLabel#LaterReadSite {
                color: rgba(100, 116, 139, 0.48);
                font-size: 12px;
            }
            QLabel#LaterReadTime {
                color: #64748b;
                font-size: 12px;
            }
            QToolButton#LaterReadIconButton {
                background: rgba(255,255,255,0.86);
                border: 1px solid #d8e0ec;
                border-radius: 6px;
            }
            QToolButton#LaterReadIconButton:disabled {
                background: rgba(255,255,255,0.86);
                border-color: #d8e0ec;
            }
            QToolButton#LaterReadIconButton:hover {
                background: #eef4ff;
                border-color: #b8cdf5;
            }
            QScrollBar:vertical {
                border: none;
                background: transparent;
                width: 4px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(100, 116, 139, 0.25);
                border-radius: 2px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(100, 116, 139, 0.45);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
                background: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
            QScrollBar:horizontal {
                border: none;
                background: transparent;
                height: 4px;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background: rgba(100, 116, 139, 0.25);
                border-radius: 2px;
                min-width: 20px;
            }
            QScrollBar::handle:horizontal:hover {
                background: rgba(100, 116, 139, 0.45);
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
                background: none;
            }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: none;
            }
            QTextEdit {
                background: white;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 8px;
                color: #111827;
                font-size: 13px;
            }
            QTextEdit:focus {
                border: 1px solid #cbd5e1;
            }
            QMessageBox {
                background: #ffffff;
            }
            QMessageBox QLabel {
                color: #111827;
                background: transparent;
                font-size: 13px;
            }
            QMessageBox QLabel#qt_msgbox_label {
                font-weight: 700;
                font-size: 14px;
            }
            QMessageBox QPushButton {
                background: #1e293b;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 6px 18px;
                min-width: 72px;
                font-weight: 700;
                font-size: 13px;
            }
            QMessageBox QPushButton:hover { background: #334155; }
            QMessageBox QPushButton:pressed { background: #0f172a; }
            QMessageBox QTextEdit {
                background: rgba(255,255,255,0.96);
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 6px;
                color: #111827;
            }
        """
        .replace("__MAIN_BACKGROUND__", MAIN_WINDOW_BACKGROUND)
        .replace("__MODULE_BACKGROUND__", MODULE_BACKGROUND)
        .replace("__CHECKBOX_ICON__", checkbox_check_icon_url)
        .replace("__CHECKBOX_DISABLED_ICON__", checkbox_check_disabled_icon_url)
        .replace("__RADIO_DOT_ICON__", radio_dot_icon_url)
        )

    def _build_ui(self) -> None:
        self.setFixedSize(self._DEFAULT_WINDOW_WIDTH, self._DEFAULT_WINDOW_HEIGHT)
        root = QWidget()
        root.setObjectName("Root")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 客户区空白控件统一继承箭头，避免弹窗消失后残留上一个文本控件的 I 形光标。
        root.setCursor(Qt.CursorShape.ArrowCursor)
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self._title_line = QFrame()
        self._title_line.setObjectName("TitleBottomLine")
        self._title_line.setFixedHeight(1)
        root_layout.addWidget(self._title_line)
        content = QWidget()
        content.setObjectName("MainContent")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root_layout.addWidget(content, 1)
        main_layout = QHBoxLayout(content)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(158)
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._sidebar = sidebar
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 18, 14, 10)
        sidebar_layout.setSpacing(4)
        # 超级搜索输入框取代原本的品牌 Logo 区域
        self._super_search = QLineEdit()
        self._super_search.setObjectName("SuperSearch")
        self._super_search.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._super_search.setPlaceholderText("超级搜索...")
        self._super_search.setClearButtonEnabled(True)
        self._super_search.setFixedHeight(30)

        # 设置超级搜索框的信号
        self._super_search.textChanged.connect(self._on_super_search_changed)

        sidebar_layout.addWidget(self._super_search)
        sidebar_layout.addSpacing(10)
        sidebar_layout.addWidget(self._make_nav_button("复制记录", "icon_nav_clipboard.svg", 4))
        sidebar_layout.addWidget(self._make_nav_button("表格记事", "icon_nav_notes.svg", 5))
        sidebar_layout.addWidget(self._make_nav_button("稍后阅读", "icon_nav_read.svg", 3))
        sidebar_layout.addWidget(self._make_nav_button("模型管理", "icon_nav_ai.svg", 1))
        sidebar_layout.addWidget(self._make_nav_button("实用工具", "icon_nav_resources.svg", 2))
        sidebar_layout.addWidget(self._make_nav_button("截图设置", "icon_nav_camera.svg", 0))
        sidebar_layout.addWidget(self._make_nav_button("数据管理", "icon_nav_data.svg", self._DATA_MANAGEMENT_PAGE_INDEX))
        sidebar_layout.addWidget(self._make_nav_button("关于", "icon_nav_info.svg", self._ABOUT_PAGE_INDEX))
        sidebar_layout.addStretch(1)

        # 侧边栏底部快捷动作按钮区域
        self._quick_actions_widget = QWidget()
        self._quick_actions_widget.setObjectName("SidebarQuickActionsWidget")
        self._quick_actions_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._quick_actions_widget.setStyleSheet("background: transparent;")
        quick_layout = QHBoxLayout(self._quick_actions_widget)
        quick_layout.setContentsMargins(0, 4, 0, 4)
        quick_layout.setSpacing(10)
        quick_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 1. 开始截图按钮
        self._btn_quick_capture = QPushButton()
        self._btn_quick_capture.setObjectName("SidebarQuickActionBtn")
        self._btn_quick_capture.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_quick_capture.setProperty("tipText", "开始截图")
        self._set_asset_icon(self._btn_quick_capture, "icon_nav_camera.svg")
        self._btn_quick_capture.setIconSize(QSize(24, 24))
        self._btn_quick_capture.clicked.connect(lambda *_: self._start_capture_clicked(from_tray=False))

        # 2. 截图目录按钮
        self._btn_quick_folder = QPushButton()
        self._btn_quick_folder.setObjectName("SidebarQuickActionBtn")
        self._btn_quick_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_quick_folder.setProperty("tipText", "截图目录")
        self._set_asset_icon(
            self._btn_quick_folder,
            "icon_folder.svg",
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon),
        )
        self._btn_quick_folder.setIconSize(QSize(24, 24))
        self._btn_quick_folder.clicked.connect(self._open_files_dir)

        # 3. AI对话按钮
        self._btn_quick_ai = QPushButton()
        self._btn_quick_ai.setObjectName("SidebarQuickActionBtn")
        self._btn_quick_ai.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_quick_ai.setProperty("tipText", "AI对话")
        self._set_asset_icon(self._btn_quick_ai, "icon_nav_ai.svg")
        self._btn_quick_ai.setIconSize(QSize(24, 24))
        self._btn_quick_ai.clicked.connect(self._open_manual_ai_panel)

        # 4. 设置按钮
        self._btn_quick_settings = QPushButton()
        self._btn_quick_settings.setObjectName("SidebarQuickActionBtn")
        self._btn_quick_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_quick_settings.setProperty("tipText", "设置")
        self._set_asset_icon(self._btn_quick_settings, "icon_settings.svg")
        self._btn_quick_settings.setIconSize(QSize(24, 24))
        self._btn_quick_settings.clicked.connect(lambda *_: self._switch_page(self._SETTINGS_PAGE_INDEX))

        self._btn_quick_todo = None

        quick_layout.addWidget(self._btn_quick_capture)
        quick_layout.addWidget(self._btn_quick_folder)
        quick_layout.addWidget(self._btn_quick_ai)
        quick_layout.addWidget(self._btn_quick_settings)

        # 实例化 SmoothToolTip，供快捷按钮在 hover 时使用
        from deepcat.ui.post_capture_actions import SmoothToolTip
        self._sidebar_tooltip = SmoothToolTip()
        self._sidebar_hover_filter = _SidebarHoverFilter(self._sidebar_tooltip, self)

        self._btn_quick_capture.installEventFilter(self._sidebar_hover_filter)
        self._btn_quick_folder.installEventFilter(self._sidebar_hover_filter)
        self._btn_quick_ai.installEventFilter(self._sidebar_hover_filter)
        self._btn_quick_settings.installEventFilter(self._sidebar_hover_filter)

        sidebar_layout.addWidget(self._quick_actions_widget)

        self._stack = QStackedWidget()
        self._stack.setObjectName("ContentStack")
        self._stack.addWidget(self._build_capture_page())
        self._stack.addWidget(self._build_translator_page())
        self._stack.addWidget(self._build_resource_tools_page())
        self._stack.addWidget(self._build_later_read_page())
        self._stack.addWidget(self._build_clipboard_history_page())
        self._stack.addWidget(self._build_table_notes_page())
        self._stack.addWidget(self._build_about_page())
        self._stack.addWidget(self._build_search_page()) # index 7: 超级搜索页面
        self._stack.addWidget(self._build_data_management_page()) # index 8: 数据管理页面
        self._stack.addWidget(self._build_usage_guide_page()) # index 9: 使用指南页面
        self._stack.addWidget(self._build_settings_page()) # index 10: 设置页面
        main_layout.addWidget(sidebar)
        main_layout.addWidget(self._stack, 1)

        self._style_main_window()
        self._apply_solid_window_backgrounds()
        self._connect_embedded_settings()
        SettingsDialog._install_custom_text_context_menus(self, self)
        self._switch_page(0)
        self._on_speed_changed(self._speed.value())
        self._update_output_options()
        self._on_capture_mode_ui_changed()
        self._on_save_mode_ui_changed()
        self._refresh_hotkey_label()
        self._apply_feature_visibility_to_nav()

        # 初始化定时自动备份定时器：分钟级频率需要分钟级轮询，实际备份仍按配置间隔触发。
        self._auto_backup_timer = QTimer(self)
        self._auto_backup_timer.timeout.connect(self._check_and_run_auto_backup)
        self._auto_backup_timer.start(60000)

        # 延时 10 秒进行开机首次自动备份检测
        QTimer.singleShot(10000, self._check_and_run_auto_backup)

    def _build_settings_page(self) -> QWidget:
        page = self._make_page("设置")
        content_layout: QVBoxLayout = page._content_layout  # type: ignore[attr-defined]
        content_layout.setSpacing(10)

        # 1. 系统设置卡片
        sys_card, sys_layout = self._card("系统设置")
        sys_card.layout().setContentsMargins(16, 14, 16, 14)
        sys_layout.setSpacing(10)
        sys_title = QLabel("系统设置")
        sys_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #1e293b;")
        sys_desc = QLabel("管理开机自启动与应用系统级操作提示。")
        sys_desc.setStyleSheet("color: rgba(15, 23, 42, 0.62); font-size: 12px;")
        sys_desc.setWordWrap(True)
        sys_layout.addWidget(sys_title)
        sys_layout.addWidget(sys_desc)

        self._autostart = QCheckBox("开机启动")
        self._autostart.setChecked(bool(is_autostart_enabled()))
        self._autostart.setToolTip("开机时自动启动 DeepCat")
        self._notifications = QCheckBox("提示通知")
        self._notifications.setChecked(bool(getattr(self._cfg, "NOTIFICATIONS", True)))
        self._notifications.setToolTip("开启操作提示与系统通知")
        sys_layout.addWidget(self._row(self._autostart, self._notifications))
        content_layout.addWidget(sys_card)

        # 2. 功能开关卡片
        feature_card, feature_layout = self._card("功能开关")
        feature_card.layout().setContentsMargins(16, 14, 16, 14)
        feature_layout.setSpacing(10)
        feature_title = QLabel("功能开关")
        feature_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #1e293b;")
        feature_desc = QLabel("启用或隐藏侧边栏的扩展功能模块，关闭后对应模块将不在导航中显示。")
        feature_desc.setStyleSheet("color: rgba(15, 23, 42, 0.62); font-size: 12px;")
        feature_desc.setWordWrap(True)
        feature_layout.addWidget(feature_title)
        feature_layout.addWidget(feature_desc)

        features = getattr(self, "_feature_visibility", None)
        if not isinstance(features, dict):
            features = normalize_feature_visibility((getattr(self._app_settings, "ui", {}) or {}).get("feature_visibility"))
        self._feature_clipboard_history_switch = QCheckBox("复制记录")
        self._feature_clipboard_history_switch.setChecked(bool(features.get("clipboard_history", True)))
        self._feature_clipboard_history_switch.setToolTip("在侧边栏显示复制记录，支持记录剪贴板内容与搜索")

        self._feature_table_notes_switch = QCheckBox("表格记事")
        self._feature_table_notes_switch.setChecked(bool(features.get("table_notes", True)))
        self._feature_table_notes_switch.setToolTip("在侧边栏显示表格记事，管理表格、富文本笔记和分组")

        self._feature_later_read_switch = QCheckBox("稍后阅读")
        self._feature_later_read_switch.setChecked(bool(features.get("later_read", True)))
        self._feature_later_read_switch.setToolTip("在侧边栏显示稍后阅读，收集待读内容并过滤")

        self._feature_todo_switch = QCheckBox("实用工具")
        self._feature_todo_switch.setChecked(bool(features.get("todo", True)))
        self._feature_todo_switch.setToolTip("在侧边栏显示实用工具（包含网络检测、清理和辅助工具）")

        feature_layout.addWidget(self._row(
            self._feature_clipboard_history_switch,
            self._feature_table_notes_switch,
            self._feature_later_read_switch,
            self._feature_todo_switch,
        ))
        content_layout.addWidget(feature_card)

        # 3. 划词辅助卡片
        sel_card, sel_layout = self._card("划词辅助")
        sel_card.layout().setContentsMargins(16, 14, 16, 14)
        sel_layout.setSpacing(10)
        sel_title = QLabel("划词辅助")
        sel_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #1e293b;")
        sel_desc = QLabel("在屏幕任意位置划选文字后提供快捷功能浮窗或自动复制服务。")
        sel_desc.setStyleSheet("color: rgba(15, 23, 42, 0.62); font-size: 12px;")
        sel_desc.setWordWrap(True)
        sel_layout.addWidget(sel_title)
        sel_layout.addWidget(sel_desc)

        translator_cfg = getattr(self, "_translator", {}) or {}
        self._selection_translate_switch = QCheckBox("划词开关")
        self._selection_translate_switch.setChecked(bool(translator_cfg.get("selection_translate_enabled", False)))
        self._selection_translate_switch.setToolTip("勾选后启用划词功能")

        self._selection_popup_switch = QCheckBox("划词浮窗")
        self._selection_popup_switch.setChecked(bool(translator_cfg.get("selection_popup_enabled", False)))
        self._selection_popup_switch.setToolTip("勾选后在划词旁边弹出功能浮窗，不勾选可以通过快捷键实现相关功能")

        self._auto_copy_switch = QCheckBox("自动复制")
        self._auto_copy_switch.setChecked(bool(translator_cfg.get("auto_copy_answers", False)))
        self._auto_copy_switch.setToolTip("勾选后，自动复制翻译、回答、回复、优化等回答内容。")

        # 保持隐藏的识别翻译以保证底层兼容性
        self._ocr_translate_switch = QCheckBox("识别翻译")
        self._ocr_translate_switch.setChecked(bool(translator_cfg.get("ocr_translate_enabled", False)))
        self._ocr_translate_switch.setVisible(False)

        sel_layout.addWidget(self._row(
            self._selection_translate_switch,
            self._selection_popup_switch,
            self._auto_copy_switch,
            self._ocr_translate_switch,
        ))
        content_layout.addWidget(sel_card)

        content_layout.addStretch(1)
        return page

    def _format_hotkey_display(self, hk: str) -> str:
        s = str(hk or "").strip()
        parts = [p.strip() for p in s.split("+") if p.strip()]
        if not parts:
            return s
        out: list[str] = []
        for p in parts:
            t = p.strip().strip("<>").strip()
            tl = t.lower()
            if tl in {"ctrl", "control"}:
                out.append("Ctrl")
            elif tl == "shift":
                out.append("Shift")
            elif tl in {"alt", "option"}:
                out.append("Alt")
            elif tl in {"cmd", "win", "windows", "super"}:
                out.append("Win")
            elif tl == "space":
                out.append("Space")
            else:
                if len(t) == 1:
                    out.append(t.upper())
                else:
                    out.append(t[:1].upper() + t[1:])
        return "+".join(out)

    def _refresh_hotkey_label(self) -> None:
        hk_raw = str(getattr(self, "_start_hotkey_str", "<f1>"))
        hk = self._format_hotkey_display(hk_raw)
        scroll_raw = str(getattr(self, "_scroll_hotkey_str", "<f2>"))
        scroll_hk = self._format_hotkey_display(scroll_raw)
        ai_qa_raw = str(getattr(self, "_ai_qa_hotkey_str", "<alt>+<space>"))
        ai_qa_hk = self._format_hotkey_display(ai_qa_raw)
        if self._hotkeys_label is not None:
            self._hotkeys_label.setText(f"框选：{hk}\n滚动：{scroll_hk}\nAI问答：{ai_qa_hk}")

    def _on_hotkey_label_double_click(self, event, editor: QKeySequenceEdit, default: str) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            editor.setKeySequence(QKeySequence(pynput_to_qt(default)))
            editor.keySequenceChanged.emit(editor.keySequence())

    def _show_translator_test_message(self, *, success: bool, message: str, elapsed: float) -> None:
        SettingsDialog._show_feedback_message(
            self,
            success=bool(success),
            title="模型测试",
            summary="测试通过" if bool(success) else "测试失败",
            detail=f"{message}\n用时：{float(elapsed):.2f}秒",
        )

    def _on_translator_test_finished(self, success: bool, message: str, elapsed: float) -> None:
        self._translator_testing = False
        self._translator_test_btn.setText("模型测试")
        self._clear_translator_proxy_selection()
        self._show_translator_test_message(success=bool(success), message=str(message), elapsed=float(elapsed))
        self._clear_translator_proxy_selection()
        self._translator_test_worker = None

    def _with_blocked_signals(self, widget: QWidget, fn: Callable[[], None]) -> None:
        old = False
        try:
            old = bool(widget.blockSignals(True))
            fn()
        finally:
            try:
                widget.blockSignals(old)
            except Exception:
                pass

    def _sync_settings_after_change(self) -> None:
        self._app_settings = self._current
        try:
            self._apply_annotation_style_to_border()
            self._refresh_hotkey_label()
        except Exception:
            pass
        try:
            self._refresh_tray_later_read_action()
        except Exception:
            pass

    def _sync_embedded_settings_controls(self) -> None:
        try:
            self._current = load_settings()
            self._app_settings = self._current
            ui = dict(getattr(self._current, "ui", {}) or {})
            self._translator = normalize_translator_settings(ui.get("translator"))
            self._feature_visibility = normalize_feature_visibility(ui.get("feature_visibility"))
            resource_shortcuts = _normalize_resource_shortcuts(ui.get("resource_shortcuts"))
            if resource_shortcuts != getattr(self, "_resource_shortcuts", []):
                self._resource_shortcuts = resource_shortcuts
                self._refresh_resource_tools_grid()
            self._annotation_style = normalize_annotation_style(ui.get("annotation_style"))
            if hasattr(self, "_autostart"):
                self._with_blocked_signals(self._autostart, lambda: self._autostart.setChecked(bool(is_autostart_enabled())))
            if hasattr(self, "_notifications"):
                self._with_blocked_signals(self._notifications, lambda: self._notifications.setChecked(bool(getattr(self._current, "notifications_enabled", False))))
            log_settings = normalize_log_settings(ui.get("logging"))
            if hasattr(self, "_app_log_switch"):
                self._with_blocked_signals(self._app_log_switch, lambda: self._app_log_switch.setChecked(bool(log_settings.get("app_log_enabled", False))))
            if hasattr(self, "_crash_log_switch"):
                self._with_blocked_signals(self._crash_log_switch, lambda: self._crash_log_switch.setChecked(bool(log_settings.get("crash_log_enabled", False))))
            updater_settings = normalize_updater_settings(ui.get("updater"))
            if hasattr(self, "_auto_update_switch"):
                self._with_blocked_signals(self._auto_update_switch, lambda: self._auto_update_switch.setChecked(bool(updater_settings.get("auto_update_enabled", False))))
            if hasattr(self, "_auto_snap_enabled"):
                self._with_blocked_signals(self._auto_snap_enabled, lambda: self._auto_snap_enabled.setChecked(bool(ui.get("auto_snap_enabled", True))))
            for key, attr in [
                ("todo", "_feature_todo_switch"),
                ("later_read", "_feature_later_read_switch"),
                ("clipboard_history", "_feature_clipboard_history_switch"),
                ("table_notes", "_feature_table_notes_switch"),
            ]:
                if hasattr(self, attr):
                    switch = getattr(self, attr)
                    self._with_blocked_signals(switch, lambda sw=switch, k=key: sw.setChecked(bool(self._feature_visibility.get(k, True))))
            self._apply_feature_visibility_to_nav()
            reminder = normalize_cat_reminder_settings(ui.get("cat_reminder"))
            if hasattr(self, "_cat_reminder_enabled"):
                self._with_blocked_signals(self._cat_reminder_enabled, lambda: self._cat_reminder_enabled.setChecked(bool(reminder.get("enabled", False))))
            if hasattr(self, "_cat_reminder_voice"):
                self._with_blocked_signals(self._cat_reminder_voice, lambda: self._cat_reminder_voice.setChecked(bool(reminder.get("voice_enabled", False))))
            if hasattr(self, "_cat_reminder_exit"):
                self._with_blocked_signals(self._cat_reminder_exit, lambda: self._cat_reminder_exit.setChecked(bool(reminder.get("exit_enabled", True))))
            if hasattr(self, "_cat_reminder_pre_notify"):
                self._with_blocked_signals(self._cat_reminder_pre_notify, lambda: self._cat_reminder_pre_notify.setChecked(bool(reminder.get("pre_notify_enabled", False))))
            if hasattr(self, "_cat_reminder_pre_notify_seconds"):
                self._with_blocked_signals(self._cat_reminder_pre_notify_seconds, lambda: self._cat_reminder_pre_notify_seconds.setValue(int(reminder.get("pre_notify_seconds", 5))))
            if hasattr(self, "_cat_reminder_interval"):
                self._with_blocked_signals(self._cat_reminder_interval, lambda: self._cat_reminder_interval.setValue(int(reminder.get("interval_minutes", 45))))
            if hasattr(self, "_cat_reminder_duration"):
                self._with_blocked_signals(self._cat_reminder_duration, lambda: self._cat_reminder_duration.setValue(int(reminder.get("duration_seconds", 20))))
            if hasattr(self, "_cat_reminder_message"):
                self._with_blocked_signals(self._cat_reminder_message, lambda: self._cat_reminder_message.setText(str(reminder.get("message", ""))))
            if hasattr(self, "_cat_voice_status_label"):
                self._update_cat_voice_status_label()
            if hasattr(self, "_cat_reminder_pre_notify_seconds"):
                self._update_cat_pre_notify_controls()
            self._todo_items = normalize_todo_items(ui.get("todo_items"))
            if hasattr(self, "_todo_calendar") and hasattr(self, "_todo_list"):
                self._refresh_todo_list()
            self._later_read = normalize_later_read_settings(ui.get("later_read"))
            if hasattr(self, "_later_read_enabled"):
                self._with_blocked_signals(self._later_read_enabled, lambda: self._later_read_enabled.setChecked(bool(self._later_read.get("enabled", False))))
            if hasattr(self, "_later_read_list"):
                self._refresh_later_read_list()
            if hasattr(self, "_save_button_mode"):
                raw_save_mode = str(ui.get("save_button_mode", "auto")).strip().lower()
                if raw_save_mode == "manual":
                    save_mode = "手动保存"
                else:
                    save_mode = "自动保存"
                self._with_blocked_signals(self._save_button_mode, lambda: self._save_button_mode.setCurrentText(save_mode))
            if hasattr(self, "_previous_capture_action_combo"):
                previous_action = str(ui.get("previous_capture_action", "pin")).strip().lower()
                if previous_action == "save":
                    text = "保存前图"
                elif previous_action == "pin":
                    text = "置顶前图"
                elif previous_action == "stitch":
                    text = "拼接前图"
                elif previous_action == "stash":
                    text = "暂存前图"
                else:
                    text = "置顶前图"
                self._with_blocked_signals(self._previous_capture_action_combo, lambda: self._previous_capture_action_combo.setCurrentText(text))
            if hasattr(self, "_post_capture_button_style_combo"):
                text = "文字按钮" if str(ui.get("post_capture_button_style", "icon")).lower() == "text" else "图标按钮"
                self._with_blocked_signals(self._post_capture_button_style_combo, lambda: self._post_capture_button_style_combo.setCurrentText(text))
            if hasattr(self, "_img_dir"):
                self._with_blocked_signals(self._img_dir, lambda: self._img_dir.setText(str(self._current.image_output_dir or self._current.pdf_output_dir)))
            if hasattr(self, "_hotkey"):
                self._with_blocked_signals(self._hotkey, lambda: self._hotkey.setKeySequence(QKeySequence(pynput_to_qt(self._current.hotkey))))
            if hasattr(self, "_scroll_hotkey_edit"):
                scroll_hk = str(ui.get("scroll_hotkey", "<f2>") or "<f2>")
                self._with_blocked_signals(self._scroll_hotkey_edit, lambda: self._scroll_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt(scroll_hk))))
            if hasattr(self, "_later_read_hotkey_edit"):
                lr_hk = str(ui.get("later_read_hotkey", "<f3>") or "<f3>")
                self._with_blocked_signals(self._later_read_hotkey_edit, lambda: self._later_read_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt(lr_hk))))
            if hasattr(self, "_ai_qa_hotkey_edit"):
                p_hk = str(ui.get("ai_qa_hotkey", "<alt>+<space>") or "<alt>+<space>")
                self._with_blocked_signals(self._ai_qa_hotkey_edit, lambda: self._ai_qa_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt(p_hk))))
            if hasattr(self, "_selection_translate_hotkey_edit"):
                st_hk = str(ui.get("selection_translate_hotkey", "<ctrl>+<space>") or "<ctrl>+<space>")
                self._with_blocked_signals(self._selection_translate_hotkey_edit, lambda: self._selection_translate_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt(st_hk))))
            if hasattr(self, "_selection_popup_hotkey_edit"):
                sp_hk = str(ui.get("selection_popup_hotkey", "<ctrl>+b") or "<ctrl>+b")
                self._with_blocked_signals(self._selection_popup_hotkey_edit, lambda: self._selection_popup_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt(sp_hk))))
            if hasattr(self, "_line_style"):
                self._with_blocked_signals(self._line_style, lambda: self._line_style.setCurrentText("实线" if str(self._annotation_style.get("line_style")) == "solid" else "虚线"))
            if hasattr(self, "_translator_model") and self._translator_model is not None:
                current_model = str(self._translator.get("translate_model", self._translator.get("current_model", "gemini-3.5-flash-thinking")))
                self._add_model_to_combos(current_model)
                self._with_blocked_signals(self._translator_model, lambda: self._translator_model.setCurrentText(current_model))
            if hasattr(self, "_qa_model") and self._qa_model is not None:
                qa_model = SettingsDialog._initial_translator_detail_model(self._translator)
                self._add_model_to_combos(qa_model)
                self._with_blocked_signals(self._qa_model, lambda: self._qa_model.setCurrentText(qa_model))
                self._translator_current_role = "qa"
                self._load_translator_model_config(qa_model)
            if hasattr(self, "_network_probe_mode_buttons"):
                SettingsDialog._sync_network_probe_mode_buttons(
                    self,
                    SettingsDialog._network_probe_mode_from_settings(self),
                )
            if hasattr(self, "_local_translation_service_switch"):
                enabled = bool(self._translator.get("local_translation_service_enabled", False))
                self._with_blocked_signals(self._local_translation_service_switch, lambda: self._local_translation_service_switch.setChecked(enabled))
            if hasattr(self, "_codex_config_switch"):
                codex_enabled = bool(self._translator.get("codex_config_enabled", False))
                self._with_blocked_signals(self._codex_config_switch, lambda: self._codex_config_switch.setChecked(codex_enabled))
            if hasattr(self, "_selection_translate_switch"):
                self._with_blocked_signals(self._selection_translate_switch, lambda: self._selection_translate_switch.setChecked(bool(self._translator.get("selection_translate_enabled", False))))
            if hasattr(self, "_selection_popup_switch"):
                self._with_blocked_signals(self._selection_popup_switch, lambda: self._selection_popup_switch.setChecked(bool(self._translator.get("selection_popup_enabled", False))))
            if hasattr(self, "_auto_copy_switch"):
                self._with_blocked_signals(self._auto_copy_switch, lambda: self._auto_copy_switch.setChecked(bool(self._translator.get("auto_copy_answers", False))))

            for key in list(getattr(self, "_annotation_color_buttons", {}) or {}):
                self._refresh_color_button(key)
            self._start_hotkey_str = str(self._current.hotkey)
            self._scroll_hotkey_str = str(ui.get("scroll_hotkey", self._scroll_hotkey_str) or self._scroll_hotkey_str)
            self._later_read_hotkey_str = str(ui.get("later_read_hotkey", self._later_read_hotkey_str) or self._later_read_hotkey_str)
            self._ai_qa_hotkey_str = str(ui.get("ai_qa_hotkey", self._ai_qa_hotkey_str) or self._ai_qa_hotkey_str)
            self._selection_translate_hotkey_str = str(ui.get("selection_translate_hotkey", self._selection_translate_hotkey_str) or self._selection_translate_hotkey_str)
            self._selection_popup_hotkey_str = str(ui.get("selection_popup_hotkey", self._selection_popup_hotkey_str) or self._selection_popup_hotkey_str)
            self._refresh_hotkey_label()
        except Exception:
            get_logger().exception("同步主界面设置控件失败")

    def _change_start_hotkey_from_settings(self, new_hotkey: str) -> tuple[bool, str]:
        result = self._replace_global_hotkey(
            "_start_hotkey", "_start_hotkey_str", self._start_hotkey_triggered, new_hotkey
        )
        if result[0]:
            self._refresh_hotkey_label()
        return result

    def _apply_annotation_style(self) -> None:
        SettingsDialog._apply_annotation_style(self)
        self._sync_settings_after_change()

    def _apply_dirs(self) -> None:
        file_dir = normalize_output_dir(self._img_dir.text().strip())
        ok, msg = validate_output_dir(file_dir or "")
        if not ok:
            self._img_dir.setText(str(self._current.image_output_dir))
            self._show_capture_status(f"文件保存路径无效：{msg}", tone="error", auto_hide_ms=5200)
            return
        self._current = update_settings_fields(image_output_dir=str(file_dir), pdf_output_dir=str(file_dir))
        self._sync_settings_after_change()
        self._show_capture_status("文件保存路径已保存。", tone="success")

    def _apply_autostart(self) -> None:
        enabled = bool(self._autostart.isChecked())
        err = set_autostart(enabled)
        if err is not None:
            self._autostart.blockSignals(True)
            try:
                self._autostart.setChecked(bool(is_autostart_enabled()))
            finally:
                self._autostart.blockSignals(False)
            self._show_capture_status(f"开机启动设置失败：{err}", tone="error", auto_hide_ms=5200)
            return
        self._current = update_settings_fields(autostart=bool(enabled))
        self._sync_settings_after_change()
        self._show_capture_status("开机启动已开启。" if enabled else "开机启动已关闭。", tone="success")

    def _apply_notifications(self) -> None:
        SettingsDialog._apply_notifications(self)
        self._sync_settings_after_change()

    def _apply_logging_settings(self) -> None:
        SettingsDialog._apply_logging_settings(self)
        self._sync_settings_after_change()

    def _apply_auto_update(self) -> None:
        self._save_updater_settings(auto_update_enabled=bool(self._auto_update_switch.isChecked()))

    def _save_updater_settings(self, **updates: object) -> None:
        def _mut(s: AppSettings) -> AppSettings:
            ui = dict(s.ui)
            updater = normalize_updater_settings(ui.get("updater"))
            for key, value in updates.items():
                updater[str(key)] = value
            ui["updater"] = normalize_updater_settings(updater)
            return AppSettings(
                version=int(s.version),
                autostart=bool(s.autostart),
                auto_save=bool(getattr(s, "auto_save", True)),
                image_output_dir=str(s.image_output_dir),
                pdf_output_dir=str(s.pdf_output_dir),
                hotkey=str(s.hotkey),
                ui=ui,
                notifications_enabled=bool(getattr(s, "notifications_enabled", False)),
            )

        self._current = update_settings(_mut)
        self._sync_settings_after_change()

    def _set_update_controls_busy(self, busy: bool, text: str = "检查更新") -> None:
        try:
            self._check_update_btn.setEnabled(not bool(busy))
            self._check_update_btn.setText(str(text or "检查更新"))
        except Exception:
            pass

    def _auto_check_for_updates_if_due(self) -> None:
        try:
            ui = dict(getattr(self._current, "ui", {}) or {})
            updater = normalize_updater_settings(ui.get("updater"))
            if not bool(updater.get("auto_update_enabled", False)):
                return
            today = time.strftime("%Y-%m-%d")
            if str(updater.get("last_check_at", "") or "") == today:
                return
            self._check_for_updates(manual=False)
        except Exception:
            get_logger().exception("Auto update check failed before worker start")

    def _check_for_updates(self, manual: bool = False) -> None:
        worker = getattr(self, "_update_check_worker", None)
        if worker is not None and worker.isRunning():
            return
        self._save_updater_settings(last_check_at=time.strftime("%Y-%m-%d"))
        self._set_update_controls_busy(True, "检查中...")
        if manual:
            self._show_about_status("正在检查更新...", tone="info", auto_hide_ms=0)
        worker = UpdateCheckWorker(str(__version__))
        self._update_check_worker = worker
        worker.checked.connect(lambda info, error, m=bool(manual): self._on_update_checked(info, error, m))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_update_checked(self, info: object, error: str, manual: bool) -> None:
        self._update_check_worker = None
        self._set_update_controls_busy(False)
        if error:
            if manual:
                self._show_about_status(f"检查更新失败：{error}", tone="error", auto_hide_ms=5200)
            else:
                self._force_tray_notification("自动更新检查失败", f"无法检查更新：{error}", 5200)
            return
        if info is None:
            if manual:
                self._show_about_status("检查更新失败：没有收到更新信息。", tone="error", auto_hide_ms=5200)
            else:
                self._force_tray_notification("自动更新检查失败", "没有收到更新信息。", 4200)
            return
        if not bool(getattr(info, "available", False)):
            if manual:
                self._show_about_status(f"当前已是最新版本（{__version__}）。", tone="success")
            return
        latest_version = str(getattr(info, "latest_version", "") or getattr(info, "tag_name", "") or "")
        if not str(getattr(info, "download_url", "") or "").strip():
            if manual:
                self._show_about_status(f"发现新版本 {latest_version}，但 Release 中没有可下载的 ZIP 包。", tone="warning", auto_hide_ms=5200)
            else:
                self._force_tray_notification("发现新版本", f"发现新版本 {latest_version}，但没有可下载的 ZIP 包。", 5200)
            return
        if manual:
            self._show_about_status(f"发现新版本 {latest_version}，正在下载更新包...", tone="info", auto_hide_ms=0)
        self._download_update(info, manual=manual)

    def _download_update(self, info: object, manual: bool) -> None:
        worker = getattr(self, "_update_download_worker", None)
        if worker is not None and worker.isRunning():
            return
        self._update_download_info = info
        self._update_download_manual = bool(manual)
        self._set_update_controls_busy(True, "下载中...")
        self._show_about_status("正在下载更新...", tone="info", auto_hide_ms=0)
        worker = UpdateDownloadWorker(info)
        self._update_download_worker = worker
        worker.progress.connect(self._on_update_download_progress)
        worker.downloaded.connect(self._on_update_downloaded)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_update_download_progress(self, done: int, total: int) -> None:
        if int(total or 0) > 0:
            percent = int(max(0, min(100, int(done) * 100 / int(total))))
            self._set_update_controls_busy(True, f"下载 {percent}%")
            self._show_about_status(f"正在下载更新 {percent}%...", tone="info", auto_hide_ms=0)
        else:
            self._set_update_controls_busy(True, "下载中...")
            self._show_about_status("正在下载更新...", tone="info", auto_hide_ms=0)

    def _on_update_downloaded(self, path: object, error: str) -> None:
        self._update_download_worker = None
        self._set_update_controls_busy(False)
        manual = bool(getattr(self, "_update_download_manual", False))
        info = getattr(self, "_update_download_info", None)
        if error:
            if manual:
                self._show_about_status(f"下载更新失败：{error}", tone="error", auto_hide_ms=5200)
            else:
                get_logger().warning("Auto update download failed: %s", error)
                self._show_about_status(f"自动下载更新失败：{error}", tone="error", auto_hide_ms=5200)
                self._force_tray_notification("自动更新下载失败", f"无法下载更新包：{error}", 5200)
            return
        if path is None:
            if manual:
                self._show_about_status("更新包下载失败。", tone="error", auto_hide_ms=5200)
            else:
                self._force_tray_notification("自动更新下载失败", "更新包下载失败。", 4200)
            return
        latest_version = str(getattr(info, "latest_version", "") or "")
        if latest_version:
            self._save_updater_settings(last_downloaded_version=latest_version)
        self._show_about_status("更新包已下载完成，等待安装确认。", tone="success", auto_hide_ms=0)
        self._confirm_and_install_update(Path(str(path)), latest_version)

    def _confirm_and_install_update(self, zip_path: Path, latest_version: str) -> None:
        version_text = f" {latest_version}" if latest_version else ""
        ret = QMessageBox.question(
            self,
            "安装更新",
            f"更新包已下载完成。是否现在关闭旧版并安装新版{version_text}？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret != QMessageBox.StandardButton.Yes:
            self._show_about_status("已下载更新包，暂不安装。", tone="info")
            return
        try:
            from deepcat.updater import current_app_dir, launch_installer

            info = getattr(self, "_update_download_info", None)
            launch_installer(
                zip_path,
                os.getpid(),
                current_app_dir(),
                "deepcat.exe",
                asset_digest=str(getattr(info, "asset_digest", "") or ""),
            )
        except Exception as exc:
            self._show_about_status(f"安装更新失败：无法启动更新安装器：{exc}", tone="error", auto_hide_ms=5200)
            return
        self._allow_quit = True
        try:
            self.close()
        except Exception:
            pass
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _apply_feature_visibility(self) -> None:
        features = {
            "todo": bool(self._feature_todo_switch.isChecked()),
            "later_read": bool(self._feature_later_read_switch.isChecked()),
            "clipboard_history": bool(self._feature_clipboard_history_switch.isChecked()),
            "table_notes": bool(self._feature_table_notes_switch.isChecked()),
        }
        self._current = update_ui_settings(feature_visibility=normalize_feature_visibility(features))
        self._sync_settings_after_change()
        self._apply_feature_visibility_to_nav()

        # 1. 实用工具 (todo)
        if not bool(features.get("todo", True)):
            try:
                self._cat_reminder_timer.stop()
                self._todo_timer.stop()
                if self._todo_popup is not None:
                    self._todo_popup.close()
                    self._todo_popup = None
                if getattr(self, "_cat_pre_popup", None) is not None:
                    self._cat_pre_popup.close()
                    self._cat_pre_popup = None
                if getattr(self, "_cat_reminder_session", None) is not None:
                    self._cat_reminder_session.close()
                    self._cat_reminder_session = None
                self._in_pre_notify_stage = False
            except Exception:
                get_logger().debug("清理待办与猫咪提醒资源失败", exc_info=True)
        else:
            try:
                self._schedule_cat_reminder()
                self._schedule_todo_checks()
                if hasattr(self, "_todo_list") and self._todo_list is not None:
                    self._refresh_todo_list()
            except Exception:
                get_logger().debug("恢复待办与猫咪提醒资源失败", exc_info=True)

        # 2. 复制记录 (clipboard_history)
        if hasattr(self, "_clipboard_history_page") and self._clipboard_history_page is not None:
            if not bool(features.get("clipboard_history", True)):
                try:
                    self._clipboard_history_page.cleanup()
                except Exception:
                    pass
            else:
                try:
                    visible = bool(self._stack is not None and self._stack.currentIndex() == 4)
                    self._clipboard_history_page.initialize(refresh=visible)
                except Exception:
                    pass
        if not bool(features.get("clipboard_history", True)):
            try:
                compact_cb = getattr(self, "_compact_window_clipboard", None)
                if compact_cb is not None:
                    compact_cb.close()
            except Exception:
                pass

        # 3. 稍后阅读 (later_read)
        if not bool(features.get("later_read", True)):
            try:
                self._close_later_read_probe_overlay()
                compact_lr = getattr(self, "_compact_window_later_read", None)
                if compact_lr is not None:
                    compact_lr.close()
            except Exception:
                pass
        else:
            try:
                if hasattr(self, "_later_read_list") and self._later_read_list is not None:
                    self._refresh_later_read_list()
            except Exception:
                pass

        # 4. 表格记事 (table_notes)
        if not bool(features.get("table_notes", True)):
            try:
                save_timer = getattr(self, "_table_notes_save_timer", None)
                if save_timer is not None and save_timer.isActive():
                    save_timer.stop()
                    self._save_table_notes_settings()
                pinned_windows = getattr(self, "_pinned_tab_windows", None)
                if pinned_windows:
                    for win in list(pinned_windows):
                        try:
                            win.close()
                        except Exception:
                            pass
                    pinned_windows.clear()
            except Exception:
                pass
        else:
            try:
                if hasattr(self, "_table_notes_store") and self._table_notes_store is not None:
                    self._load_table_notes_settings()
            except Exception:
                pass

    def _apply_auto_snap_enabled(self) -> None:
        SettingsDialog._apply_auto_snap_enabled(self)
        self._sync_settings_after_change()

    def _apply_save_button_mode(self) -> None:
        SettingsDialog._apply_save_button_mode(self)
        self._sync_settings_after_change()

    def _apply_hotkey(self) -> None:
        qt_seq = self._hotkey.keySequence().toString()
        new_hotkey = qt_to_pynput(qt_seq)
        if not str(qt_seq).strip():
            self._show_capture_status("截图快捷键不能为空。", tone="warning")
            return
        if new_hotkey is None:
            new_hotkey = str(qt_seq).strip().lower()
        if new_hotkey == str(self._current.hotkey):
            return
        if self._on_hotkey_changed is not None:
            ok, msg = self._on_hotkey_changed(str(new_hotkey))
            if not ok:
                self._show_capture_status(f"截图快捷键保存失败：{msg or new_hotkey}", tone="error", auto_hide_ms=5200)
                return
        self._current = update_settings_fields(hotkey=str(new_hotkey))
        self._sync_settings_after_change()
        self._show_capture_status(f"截图快捷键已保存：{qt_seq}", tone="success")

    def _apply_ai_qa_hotkey(self) -> None:
        qt_seq = self._ai_qa_hotkey_edit.keySequence().toString()
        new_hotkey = qt_to_pynput(qt_seq)
        if not str(qt_seq).strip():
            self._show_capture_status("AI问答快捷键不能为空。", tone="warning")
            return
        if new_hotkey is None:
            new_hotkey = str(qt_seq).strip().lower()
        ui = dict(getattr(self._current, "ui", {}) or {})
        if str(new_hotkey) == str(ui.get("ai_qa_hotkey", "<alt>+<space>")):
            return
        ok, message = self._replace_global_hotkey(
            "_ai_qa_hotkey", "_ai_qa_hotkey_str", self._ai_qa_hotkey_signal.emit, str(new_hotkey)
        )
        if not ok:
            self._show_capture_status(f"AI问答快捷键保存失败：{message}", tone="error", auto_hide_ms=5200)
            return
        self._current = update_ui_settings(ai_qa_hotkey=str(new_hotkey))
        self._sync_settings_after_change()
        self._show_capture_status(f"AI问答快捷键已保存：{qt_seq}", tone="success")

    def _apply_selection_popup_hotkey(self) -> None:
        qt_seq = self._selection_popup_hotkey_edit.keySequence().toString()
        new_hotkey = qt_to_pynput(qt_seq)
        if not str(qt_seq).strip():
            self._show_capture_status("划词弹窗快捷键不能为空。", tone="warning")
            return
        if new_hotkey is None:
            new_hotkey = str(qt_seq).strip().lower()
        ui = dict(getattr(self._current, "ui", {}) or {})
        if str(new_hotkey) == str(ui.get("selection_popup_hotkey", "<ctrl>+b")):
            return
        self._current = update_ui_settings(selection_popup_hotkey=str(new_hotkey))
        self._selection_popup_hotkey_str = str(new_hotkey)
        if self._selection_translate_listener is not None:
            self._selection_translate_listener.set_hotkeys(str(self._selection_translate_hotkey_str), str(new_hotkey))
        self._sync_settings_after_change()
        self._show_capture_status(f"划词弹窗快捷键已保存：{qt_seq}", tone="success")

    def _on_save_mode_ui_changed(self) -> None:
        is_auto = self._save_mode.currentText() == "自动保存"
        self._previous_capture_action_combo.setEnabled(not is_auto)
        if hasattr(self, "_continuous_capture_label") and self._continuous_capture_label is not None:
            self._continuous_capture_label.setEnabled(not is_auto)

    def _apply_translator_settings(self) -> None:
        previous_proxy_url = self._network_probe_proxy_url()
        SettingsDialog._apply_translator_settings(self)
        self._sync_settings_after_change()
        if previous_proxy_url != self._network_probe_proxy_url():
            monitor = self._network_probe_monitor
            if monitor is not None and monitor.is_running():
                if (
                    monitor.preferred_route() == "proxy"
                    or monitor.status() == NetworkProbeStatus.UNAVAILABLE.value
                ):
                    monitor.request_immediate_probe(preferred_route="proxy")

    def _apply_selection_popup_enabled(self) -> None:
        SettingsDialog._apply_selection_popup_enabled(self)
        self._sync_settings_after_change()

    def _apply_auto_copy_answers(self) -> None:
        SettingsDialog._apply_auto_copy_answers(self)
        self._sync_settings_after_change()

    def _start_local_translation_service_from_settings(self) -> None:
        try:
            ok = bool(SettingsDialog._sync_local_translation_service_state(self, show_errors=False))
        except Exception as exc:
            try:
                get_logger().warning("本地 API 服务自动启动失败: %s", str(exc)[:400])
            except Exception:
                pass
            ok = False
        if not ok:
            try:
                self._sync_settings_after_change()
            except Exception:
                pass
            self._force_tray_notification(
                "本地 API 服务启动失败",
                "已关闭本地 API 开关，请检查模型服务配置后重试。",
                5200,
            )
            return
        # bind 成功不等于客户端连得上：防火墙丢包时用托盘通知把用户引到设置页的「一键允许」。
        report = getattr(self, "_local_translation_service_probe_report", None)
        if report is not None and not bool(getattr(report, "is_healthy", False)):
            self._force_tray_notification(
                "本地 API 服务自检未通过",
                str(getattr(report, "reason", "") or "本地 API 服务已启动，但客户端可能连不上。"),
                6000,
            )

    def _apply_local_translation_service_enabled(self) -> None:
        SettingsDialog._apply_local_translation_service_enabled(self)
        self._sync_settings_after_change()

    def _apply_codex_config_enabled(self) -> None:
        SettingsDialog._apply_codex_config_enabled(self)
        self._sync_settings_after_change()

    def _apply_current_model_codex_config_enabled(self, *_args) -> None:
        SettingsDialog._apply_current_model_codex_config_enabled(self, *_args)
        self._sync_settings_after_change()

    def _show_prompt_settings_popup(self) -> None:
        SettingsDialog._show_prompt_settings_popup(self)

    def _on_prompt_edit_action(self, action: str) -> None:
        if action == "reply":
            self._edit_translator_prompt(
                "reply_prompt",
                "回复提示词",
                DEFAULT_REPLY_PROMPT,
                "reply_prompt_button_name",
                DEFAULT_REPLY_PROMPT_BUTTON_NAME,
            )
        elif action == "optimize":
            self._edit_translator_prompt(
                "ai_search_prompt",
                "搜索提示词",
                DEFAULT_AI_SEARCH_PROMPT,
                "ai_search_prompt_button_name",
                DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
            )
        elif action == "explain":
            self._edit_translator_prompt(
                "explain_prompt",
                "解释提示词",
                DEFAULT_EXPLAIN_PROMPT,
                "explain_prompt_button_name",
                DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
            )
        elif action == "summarize":
            self._edit_translator_prompt(
                "summary_prompt",
                "总结提示词",
                DEFAULT_SUMMARY_PROMPT,
                "summary_prompt_button_name",
                DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
            )

    def _on_history_clicked(self) -> None:
        """打开翻译历史记录页面（统一采用划词弹窗面板 OcrTextPanel 内部的历史记录页面）。"""
        if hasattr(self, "_selection_translate_panel") and self._selection_translate_panel is not None:
            try:
                self._install_ai_panel_close_guard(self._selection_translate_panel)
                self._selection_translate_panel.showNormal()
                if getattr(self._selection_translate_panel, "_is_collapsed", False):
                    self._selection_translate_panel._toggle_collapse(False)
                self._selection_translate_panel._show_history()
                self._selection_translate_panel.raise_()
                self._selection_translate_panel.activateWindow()
                return
            except RuntimeError:
                self._selection_translate_panel = None

        try:
            from deepcat.ui.post_capture_actions import OcrTextPanel
            from PyQt6.QtWidgets import QApplication

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
            panel._history_only_mode = True

            # 将其在屏幕中央居中显示（w, h = 800, 520）
            screen = QApplication.primaryScreen().geometry()
            w, h = 800, 520
            x = (screen.width() - w) // 2
            y = (screen.height() - h) // 2
            panel.resize(w, h)
            panel.move(x, y)

            panel._show_history()
            panel.show()
            panel.raise_()
            panel.activateWindow()
            self._refresh_selection_popup_blocked()
        except Exception as e:
            print(f"Failed to show history via OcrTextPanel: {e}")
