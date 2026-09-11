from __future__ import annotations
import json
import hashlib
import os
import re
import shutil
import stat
import sys
import tempfile
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
from PyQt6.QtCore import Qt, QThread, QTimer, QRect, QSize, QByteArray, QEvent, QEventLoop, QPoint, QDate, QDateTime, QTime, pyqtSignal, QPropertyAnimation, QEasingCurve, QUrl, QObject, QFileInfo, QMimeData
from PyQt6.QtGui import QAction, QGuiApplication, QIcon, QColor, QCursor, QPainter, QPen, QImage, QKeySequence, QPixmap, QStandardItemModel, QStandardItem, QDesktopServices, QTextCharFormat, QTextCursor, QTextListFormat, QTextDocument, QFontMetrics, QFont, QBrush, QPalette, QDragEnterEvent, QDragMoveEvent, QDropEvent, QDrag
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QAbstractItemDelegate,
    QAbstractItemView,
    QButtonGroup,
    QCalendarWidget,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDateTimeEdit,
    QFileDialog,
    QFileIconProvider,
    QFormLayout,
    QGraphicsDropShadowEffect,
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
    QScrollBar,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QMenu,
    QStyle,
    QProxyStyle,
    QProgressBar,
    QStyleOption,
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
    DEEPLX_INFO_ADDRESS,
    DEFAULT_CAT_REMINDER,
    DEFAULT_EXPLAIN_PROMPT,
    DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
    DEFAULT_OPTIMIZE_PROMPT,
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
    default_translator_settings,
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
from deepcat.ui.selection_border_overlay import SelectionBorderOverlay, SelectionShadeOverlay
from deepcat.ui.capture_worker import CaptureWorker
from deepcat.ui.region_overlay import RegionOverlay, SelectedRegion, logical_rect_to_physical_tuple
from deepcat.ui.post_capture_actions import PostCaptureActions, ModernPopupComboBox, ModernMouseClickOnlyComboBox, OcrTranslationWorker, install_smooth_tooltips
from deepcat.ui.tab_list_popup import GroupedNoteListPopup, RoundedListPopup
from deepcat.ui.task_feedback import TaskFeedback
from deepcat.table_notes_store import TableNotesStore, default_ima_config
from deepcat.translation_history_store import TranslationHistoryStore
from deepcat.ima_client import ImaApiError, ImaClient, ImaCredentials, note_html_to_markdown
from deepcat.todo_store import TodoStore
from deepcat.later_read_store import LaterReadStore
from deepcat.drive_cleaner import (
    AVOID,
    CONFIRM_REQUIRED,
    CONTENTS,
    SAFE,
    SELF,
    CleanupReport,
    CleanupItemResult,
    ScanReport,
    SoftwareEntry,
    SoftwareUninstallItemResult,
    SoftwareUninstallReport,
    cleanup_items,
    cleanup_report_to_markdown,
    enumerate_drives,
    enumerate_installed_software,
    enrich_software_entry,
    reset_software_caches,
    export_scan_report,
    format_bytes,
    move_path_to_recycle_bin,
    now_iso,
    request_llm_advice,
    scan_drives,
    software_entry_force_paths,
    software_entry_should_display,
    software_uninstall_report_to_text,
    uninstall_software,
)
from deepcat.utils.autostart import is_autostart_enabled, set_autostart
from deepcat.utils.crash_reporter import (
    install_crash_reporter,
    redact_mapping,
    write_crash_breadcrumb,
    write_runtime_snapshot,
)
from deepcat.utils.logger import get_log_dir, get_log_file_path, get_logger
from deepcat.utils.paths import get_app_dir, get_files_dir
from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionButton, QStyleOptionViewItem
from deepcat.ui.main_window._shared import (
    MAIN_WINDOW_BACKGROUND,
    MAIN_WINDOW_BACKGROUND_COLORREF,
    MODULE_BACKGROUND,
    _NOTE_AUTO_LINK_PATTERN,
    _NOTE_AUTO_LINK_TRAILING_CHARS,
    _RESOURCE_SHORTCUT_MAX_ITEMS,
)
from deepcat.ui.main_window.helpers import (
    _date_key,
    _normalize_resource_shortcut_kind,
    _normalize_resource_shortcut_target,
    _normalize_resource_shortcuts,
    _note_auto_link_parts,
    _resource_shortcut_default_title,
    _safe_copy,
)
from deepcat.ui.main_window.misc_widgets import (
    RoundedTextEditContainer,
    _DoubleClickLabel,
    _DraggableTile,
    _ModernComboStyle,
    _MouseClickOnlyComboBox,
    _MultiSelectComboBox,
    _SidebarHoverFilter,
    _SidebarNavButton,
    _SwitchCheckBox,
)
from deepcat.ui.main_window.todo import (
    _TodoCalendarWidget,
    _TodoComboPopup,
    _TodoEditDialog,
    _TodoListItemWidget,
    _TodoMonthPopup,
    _TodoPopupComboBox,
    _TodoReminderPopup,
    _style_todo_combo_popup_view,
    _todo_combo_popup_view_style,
)
from deepcat.ui.main_window.later_read import _LaterReadEditDialog, _LaterReadItemWidget, _LaterReadPinnedFoldToggleWidget
from deepcat.ui.main_window.notifications import NotificationPopup, _CatRestReminderPopup
from deepcat.ui.main_window.tab_password import _TabPasswordSetDialog, _TabPasswordVerifyDialog
from deepcat.ui.main_window.ima import _ImaSettingsDialog, _ImaSyncWorker
from deepcat.ui.main_window.notes import (
    ImagePreviewDialog,
    ScrollResultPrepareWorker,
    _NoFocusTableDelegate,
    _NotesEditor,
    _NotesTable,
    _ReturnDownDelegate,
)
from deepcat.ui.main_window.drive_cleaner import (
    _DriveCleanerAdvisorWorker,
    _DriveCleanerCleanupWorker,
    _DriveCleanerFileCleanupWorker,
    _DriveCleanerMoveWorker,
    _DriveCleanerScanWorker,
    _DriveCleanerTableItem,
    _DriveCleanerWindow,
    _SoftwareUninstallScanWorker,
    _SoftwareUninstallWorker,
)
from deepcat.ui.main_window.resource_shortcuts import _DeleteShortcutPopup, _ResourceShortcutDialog, _TodoResourceWindow
from deepcat.ui.main_window.stashed_captures import _StashedCapturesDialog
from deepcat.ui.main_window.compact import (
    _CompactClipboardItemWidget,
    _CompactLaterReadItemWidget,
    _CompactListWindow,
    _CompactToolTip,
    _GroupManageDialog,
)
from deepcat.ui.main_window.window import MainWindow
from deepcat.ui.main_window.app import run_app
from deepcat.ui.settings_dialog import FREE_TRANSLATOR_TYPES, ProviderSwitchHintDelegate, SettingsDialog, TranslatorConnectionTestWorker, ProxyConnectionTestWorker, UpdateCheckWorker, UpdateDownloadWorker
