from __future__ import annotations

import os
import ctypes
from pathlib import Path
from typing import Callable, Optional
from PyQt6.QtCore import Qt, QTimer, QRect, QSize, QPoint, pyqtSignal, QPropertyAnimation, QEasingCurve, QUrl
from PyQt6.QtGui import QGuiApplication, QColor, QCursor, QDesktopServices
from PyQt6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget, QStyle, QFrame

from deepcat.ui.main_window.todo import _TodoPopupComboBox, _style_todo_combo_popup_view
from deepcat.ui.timer_scope import single_shot_scoped


class _CatRestReminderPopup(QWidget):
    snoozeRequested = pyqtSignal(int)
    exitRequested = pyqtSignal()

    def __init__(self, exit_enabled: bool = True, *, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CatRestReminderPopup")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(0)
        card = QFrame()
        card.setObjectName("CatRestReminderCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(15, 23, 42, 58))
        card.setGraphicsEffect(shadow)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(9)
        root.addWidget(card)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        headline = QLabel("休息预警提醒")
        headline.setObjectName("CatRestPopupHeadline")
        title_row.addWidget(headline)
        title_row.addStretch(1)
        card_layout.addLayout(title_row)

        title = QLabel("休息时间即将到来")
        title.setObjectName("CatRestPopupTitle")
        title.setWordWrap(True)
        card_layout.addWidget(title)

        self._body = QLabel("距离正式进入休息时间还有 5 秒")
        self._body.setObjectName("CatRestPopupBody")
        self._body.setWordWrap(True)
        card_layout.addWidget(self._body)

        self._countdown = 5
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._update_countdown)
        self._timer.start()

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self._snooze = _TodoPopupComboBox()
        self._snooze.setObjectName("TodoFilterCombo")
        for text, minutes in [("5 分钟后", 5), ("10 分钟后", 10), ("15 分钟后", 15), ("30 分钟后", 30)]:
            self._snooze.addItem(text, minutes)

        later = QPushButton("再休息")
        later.setObjectName("CatRestPopupSecondary")
        later.setCursor(Qt.CursorShape.PointingHandCursor)
        later.clicked.connect(self._emit_snooze)
        actions.addWidget(self._snooze, 1)
        actions.addWidget(later)

        if exit_enabled:
            done = QPushButton("本次退出")
            done.setObjectName("CatRestPopupPrimary")
            done.setCursor(Qt.CursorShape.PointingHandCursor)
            done.clicked.connect(self._emit_exit)
            actions.addWidget(done)

        card_layout.addLayout(actions)

        arrow_url = str((Path(__file__).resolve().parent.parent / "assets" / "icon_combo_arrow.svg").as_posix())
        self.setStyleSheet(
            """
            QWidget#CatRestReminderPopup {
                background: transparent;
            }
            QFrame#CatRestReminderCard {
                background: #ffffff;
                border: 1px solid #f1f5f9;
                border-radius: 14px;
            }
            QLabel#CatRestPopupHeadline {
                color: #1e293b;
                font-size: 13px;
                font-weight: 900;
            }
            QLabel#CatRestPopupTitle {
                color: #111827;
                font-size: 17px;
                font-weight: 900;
            }
            QLabel#CatRestPopupBody {
                color: #4b5563;
                font-size: 13px;
                line-height: 1.4;
            }
            QComboBox {
                min-height: 28px;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 3px 28px 3px 8px;
                color: #1f2937;
                background: white;
            }
            QComboBox:hover {
                border-color: #cbd5e1;
            }
            QComboBox:focus {
                border: 1px solid #cbd5e1;
            }
            QComboBox::drop-down {
                width: 24px;
                border: none;
                background: transparent;
            }
            QComboBox::down-arrow {
                image: url('__ARROW_ICON__');
                width: 12px;
                height: 12px;
            }
            QPushButton {
                min-height: 28px;
                border-radius: 8px;
                padding: 4px 14px;
                font-weight: 800;
            }
            QPushButton#CatRestPopupPrimary {
                color: white;
                background: #1e293b;
                border: none;
            }
            QPushButton#CatRestPopupPrimary:hover {
                background: #334155;
            }
            QPushButton#CatRestPopupPrimary:pressed {
                background: #0f172a;
            }
            QPushButton#CatRestPopupSecondary {
                color: #334155;
                background: #f1f5f9;
                border: none;
            }
            QPushButton#CatRestPopupSecondary:hover {
                background: #e2e8f0;
                color: #0f172a;
            }
            """.replace("__ARROW_ICON__", arrow_url)
        )
        _style_todo_combo_popup_view(self._snooze)

    def _emit_snooze(self) -> None:
        self.snoozeRequested.emit(int(self._snooze.currentData() or 5))
        self.close()

    def _emit_exit(self) -> None:
        self.exitRequested.emit()
        self.close()

    def _update_countdown(self) -> None:
        self._countdown -= 1
        if self._countdown >= 0:
            self._body.setText(f"距离正式进入休息时间还有 {self._countdown} 秒")
        else:
            self._timer.stop()


class NotificationPopup(QWidget):
    _active_popups: list[NotificationPopup] = []

    def __init__(
        self,
        title: str,
        message: str,
        duration_ms: int = 3000,
        parent: Optional[QWidget] = None,
        open_dir: Optional[str] = None,
        auto_close: bool = True,
        action_text: str = "",
        action_callback: Optional[Callable[[], None]] = None,
        anchor_pos: Optional[QPoint] = None,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._duration_ms = int(duration_ms)
        self._open_dir = str(open_dir) if open_dir else None
        self._auto_close = bool(auto_close)
        self._action_callback = action_callback
        self._anchor_pos = QPoint(anchor_pos) if anchor_pos is not None else None
        self._cursor_override_active = False
        self._cursor_override_shape: Optional[Qt.CursorShape] = None
        self._cursor_buttons: list[QWidget] = []

        container = QWidget(self)
        container.setObjectName("NotificationContainer")
        container.setCursor(Qt.CursorShape.ArrowCursor)
        container.setStyleSheet(
            """
            QWidget#NotificationContainer {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #f6faff, stop:1 #ffffff);
                border: 1px solid rgba(76,107,136,0.25);
                border-radius: 10px;
            }
            QLabel { background: transparent; border: none; }
            """
        )

        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_label = QLabel(str(title))
        title_label.setCursor(Qt.CursorShape.ArrowCursor)
        title_label.setStyleSheet("color: #4c6b88; font-size: 14px; font-weight: 700;")
        title_row.addWidget(title_label, 1)

        close_btn = QPushButton("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                color: #8898aa;
                font-size: 16px;
                font-weight: 700;
            }
            QPushButton:hover { color: #e53935; }
            """
        )
        close_btn.clicked.connect(self.close)
        title_row.addWidget(close_btn)
        self._cursor_buttons.append(close_btn)
        layout.addLayout(title_row)

        message_row = QHBoxLayout()
        message_row.setSpacing(8)
        msg_label = QLabel(str(message))
        msg_label.setCursor(Qt.CursorShape.ArrowCursor)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("color: rgba(0,0,0,0.78); font-size: 13px;")
        message_row.addWidget(msg_label, 1)

        if self._open_dir:
            folder_btn = QPushButton()
            folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            folder_btn.setFixedSize(22, 22)
            folder_btn.setToolTip("打开保存文件夹")
            folder_btn.setStyleSheet(
                """
                QPushButton {
                    background: transparent;
                    border: none;
                    padding: 2px;
                }
                QPushButton:hover {
                    background: rgba(76,107,136,0.12);
                    border-radius: 4px;
                }
                """
            )
            folder_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
            folder_btn.setIcon(folder_icon)
            folder_btn.setIconSize(QSize(18, 18))
            folder_btn.clicked.connect(self._open_folder)
            message_row.addWidget(folder_btn, 0, Qt.AlignmentFlag.AlignTop)
            self._cursor_buttons.append(folder_btn)

        if str(action_text).strip() and action_callback is not None:
            action_btn = QPushButton(str(action_text).strip())
            action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            action_btn.setFixedHeight(24)
            action_btn.setStyleSheet(
                """
                QPushButton {
                    background: rgba(19, 104, 232, 0.08);
                    border: 1px solid rgba(19, 104, 232, 0.24);
                    border-radius: 6px;
                    color: #1368e8;
                    padding: 0 10px;
                    font-size: 12px;
                    font-weight: 800;
                }
                QPushButton:hover {
                    background: rgba(19, 104, 232, 0.14);
                    border-color: rgba(19, 104, 232, 0.38);
                }
                """
            )
            action_btn.clicked.connect(self._trigger_action)
            message_row.addWidget(action_btn, 0, Qt.AlignmentFlag.AlignTop)
            self._cursor_buttons.append(action_btn)

        layout.addLayout(message_row)

        top_layout = QVBoxLayout(self)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(container)

        if self.layout() is not None:
            self.layout().activate()
        self.adjustSize()
        w = min(340, max(260, self.width()))
        self.setFixedWidth(int(w))
        if self.layout() is not None:
            self.layout().activate()
        self.adjustSize()
        final_h = max(int(self.height()), int(self.sizeHint().height()), int(self.minimumSizeHint().height()))
        self.resize(int(w), int(final_h))

        self.show()
        if self._anchor_pos is not None:
            self._position_near_point(self._anchor_pos)
        else:
            self._position_at_bottom_right()

        self.setWindowOpacity(1.0)
        self._raise_topmost()

        if self._auto_close and self._duration_ms > 0:
            QTimer.singleShot(self._duration_ms, self._fade_out)


        NotificationPopup._active_popups.append(self)

    def _position_at_bottom_right(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        margin = 18
        x = geo.right() - self.width() - margin
        offset = 0
        for popup in NotificationPopup._active_popups:
            if popup is not self and popup.isVisible():
                offset += popup.height() + 10
        y = geo.bottom() - self.height() - margin - offset
        if y < geo.top() + 10:
            y = geo.top() + 10
        self.move(int(x), int(y))

    def _position_near_point(self, global_pos: QPoint) -> None:
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        margin = 10
        gap = 16
        x = int(global_pos.x()) + gap
        y = int(global_pos.y()) + gap
        if x + self.width() > geo.right() + 1 - margin:
            x = int(global_pos.x()) - self.width() - gap
        if y + self.height() > geo.bottom() + 1 - margin:
            y = int(global_pos.y()) - self.height() - gap
        x = max(int(geo.left()) + margin, min(int(x), int(geo.right()) + 1 - self.width() - margin))
        y = max(int(geo.top()) + margin, min(int(y), int(geo.bottom()) + 1 - self.height() - margin))

        offset = 0
        for popup in NotificationPopup._active_popups:
            if popup is self or not popup.isVisible():
                continue
            try:
                if getattr(popup, "_anchor_pos", None) is not None and abs(popup.x() - x) < 24 and abs(popup.y() - y) < 24:
                    offset += popup.height() + 8
            except Exception:
                pass
        if offset:
            shifted_y = int(y) - int(offset)
            if shifted_y < geo.top() + margin:
                shifted_y = int(y) + int(offset)
            y = max(int(geo.top()) + margin, min(int(shifted_y), int(geo.bottom()) + 1 - self.height() - margin))
        self.move(int(x), int(y))

    def _open_folder(self) -> None:
        if not self._open_dir:
            return
        try:
            dir_path = Path(self._open_dir)
            if dir_path.exists() and dir_path.is_dir():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(dir_path)))
        except Exception:
            pass

    def _trigger_action(self) -> None:
        callback = self._action_callback
        if callback is not None:
            try:
                callback()
            except Exception:
                pass
        self.close()

    def _raise_topmost(self) -> None:
        try:
            self.show()
            self.raise_()
        except Exception:
            pass
        if os.name != "nt":
            return
        try:
            import ctypes

            hwnd = int(self.winId())
            HWND_TOPMOST = -1
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOACTIVATE = 0x0010
            SWP_SHOWWINDOW = 0x0040
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(hwnd),
                ctypes.c_void_p(HWND_TOPMOST),
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        except Exception:
            pass



    def _fade_out(self) -> None:
        try:
            if getattr(self, "_fade_started", False):
                return
            self._fade_started = True
            self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
            self._fade_anim.setDuration(200)
            self._fade_anim.setStartValue(1.0)
            self._fade_anim.setEndValue(0.0)
            self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
            self._fade_anim.finished.connect(self.close)
            self._fade_anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            single_shot_scoped(400, self, self.close)
        except Exception:
            self.close()

    def closeEvent(self, event) -> None:
        try:
            if self in NotificationPopup._active_popups:
                NotificationPopup._active_popups.remove(self)
            for popup in NotificationPopup._active_popups:
                if popup.isVisible():
                    anchor_pos = getattr(popup, "_anchor_pos", None)
                    if anchor_pos is not None:
                        popup._position_near_point(anchor_pos)
                    else:
                        popup._position_at_bottom_right()
        except Exception:
            pass
        super().closeEvent(event)

    @classmethod
    def show_notification(
        cls,
        title: str,
        message: str,
        duration_ms: int = 2000,
        open_dir: Optional[str] = None,
        auto_close: bool = True,
        action_text: str = "",
        action_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        cls(
            title,
            message,
            duration_ms,
            open_dir=open_dir,
            auto_close=auto_close,
            action_text=action_text,
            action_callback=action_callback,
        )

    @classmethod
    def show_notification_near(
        cls,
        title: str,
        message: str,
        global_pos: QPoint,
        duration_ms: int = 2000,
        auto_close: bool = True,
    ) -> None:
        cls(
            title,
            message,
            duration_ms,
            auto_close=auto_close,
            anchor_pos=QPoint(global_pos),
        )

    @classmethod
    def raise_active_popups(cls) -> None:
        for popup in list(cls._active_popups):
            try:
                if popup.isVisible():
                    popup._raise_topmost()
            except RuntimeError:
                try:
                    cls._active_popups.remove(popup)
                except ValueError:
                    pass
            except Exception:
                pass

    @classmethod
    def point_hits_active_popup(cls, point: QPoint) -> bool:
        for popup in list(cls._active_popups):
            try:
                if popup.isVisible() and popup.frameGeometry().contains(point):
                    return True
            except RuntimeError:
                try:
                    cls._active_popups.remove(popup)
                except ValueError:
                    pass
            except Exception:
                pass
        return False

    @classmethod
    def cursor_shape_at(cls, point: QPoint) -> Optional[Qt.CursorShape]:
        for popup in list(cls._active_popups):
            try:
                shape = popup.cursor_shape_for_global_point(point)
                if shape is not None:
                    return shape
            except RuntimeError:
                try:
                    cls._active_popups.remove(popup)
                except ValueError:
                    pass
            except Exception:
                pass
        return None


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



class CatReminderMixin:
    def _cat_label(self, size: int, *, mascot: bool = False) -> QLabel:
        label = QLabel()
        label.setFixedSize(size, size)
        path = self._assets_dir() / ("deepcat_mascot.png" if mascot else "deepcat_logo.svg")
        if path.exists():
            pix = QPixmap(str(path)).scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            label.setPixmap(pix)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def _build_cat_reminder_page(self) -> QWidget:
        todo_btn = QPushButton("新建待办")
        todo_btn.setObjectName("BtnPrimary")
        todo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        todo_btn.setIcon(self._asset_icon("icon_todo_add_white.svg"))
        todo_btn.setIconSize(QSize(22, 22))
        todo_btn.setMinimumSize(132, 42)
        todo_btn.setMaximumSize(132, 42)
        todo_btn.clicked.connect(lambda *_: self._open_todo_dialog())
        page = self._make_page("休息待办", action_widget=todo_btn)
        content_layout: QVBoxLayout = page._content_layout  # type: ignore[attr-defined]
        content_layout.setSpacing(14)
        ui = dict(getattr(self._app_settings, "ui", {}) or {})
        reminder = normalize_cat_reminder_settings(ui.get("cat_reminder"))

        settings_group, form = self._form_card("休息设置")
        form.setVerticalSpacing(10)
        self._cat_reminder_enabled = QCheckBox("启用提醒")
        self._cat_reminder_enabled.setChecked(bool(reminder.get("enabled", False)))
        self._cat_reminder_voice = QCheckBox("语音提醒")
        self._cat_reminder_voice.setChecked(bool(reminder.get("voice_enabled", False)))
        self._cat_voice_status_label = QLabel()
        self._cat_voice_status_label.setObjectName("CatVoiceStatusLabel")
        self._cat_voice_status_label.setStyleSheet("color:#64748b; font-size:12px; font-weight:700;")
        self._cat_reminder_exit = QCheckBox("允许退出")
        self._cat_reminder_exit.setChecked(bool(reminder.get("exit_enabled", True)))
        self._cat_reminder_pre_notify = QCheckBox("提前提醒")
        self._cat_reminder_pre_notify.setChecked(bool(reminder.get("pre_notify_enabled", False)))
        form.addRow("", self._row(self._cat_reminder_enabled, self._cat_reminder_voice, self._cat_voice_status_label, self._cat_reminder_exit))

        self._cat_reminder_interval = QSpinBox()
        self._cat_reminder_interval.setRange(1, 24 * 60)
        self._cat_reminder_interval.setSuffix(" 分钟")
        self._cat_reminder_interval.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._cat_reminder_interval.setFixedWidth(94)
        self._cat_reminder_interval.setValue(int(reminder.get("interval_minutes", 45)))

        self._cat_reminder_duration = QSpinBox()
        self._cat_reminder_duration.setRange(5, 10 * 60)
        self._cat_reminder_duration.setSuffix(" 秒")
        self._cat_reminder_duration.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._cat_reminder_duration.setFixedWidth(94)
        self._cat_reminder_duration.setValue(int(reminder.get("duration_seconds", 20)))
        self._cat_reminder_duration.setToolTip("休息提醒弹出后开始倒计时；倒计时结束前不能继续操作，结束后自动恢复。")
        self._cat_reminder_pre_notify_seconds = QSpinBox()
        self._cat_reminder_pre_notify_seconds.setRange(5, 5 * 60)
        self._cat_reminder_pre_notify_seconds.setSuffix(" 秒")
        self._cat_reminder_pre_notify_seconds.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._cat_reminder_pre_notify_seconds.setFixedWidth(94)
        self._cat_reminder_pre_notify_seconds.setValue(int(reminder.get("pre_notify_seconds", 5)))
        self._cat_reminder_pre_notify_seconds.setToolTip("例如 5 秒、60 秒（1 分钟）或 300 秒（5 分钟）。")
        timing_row = QWidget()
        timing_layout = QHBoxLayout(timing_row)
        timing_layout.setContentsMargins(0, 0, 0, 0)
        timing_layout.setSpacing(8)
        timing_layout.addWidget(QLabel("提醒间隔"))
        timing_layout.addWidget(self._cat_reminder_interval)
        timing_layout.addSpacing(12)
        duration_label = QLabel("接管倒计时")
        duration_label.setToolTip("休息提醒弹出后开始倒计时；倒计时结束前不能继续操作，结束后自动恢复。")
        timing_layout.addWidget(duration_label)
        timing_layout.addWidget(self._cat_reminder_duration)
        timing_layout.addSpacing(12)
        timing_layout.addWidget(self._cat_reminder_pre_notify)
        timing_layout.addWidget(self._cat_reminder_pre_notify_seconds)
        timing_layout.addWidget(QLabel("前先弹出轻提醒"))
        timing_layout.addStretch(1)
        form.addRow("", timing_row)

        self._cat_reminder_message = QLineEdit(str(reminder.get("message", "")))
        self._cat_reminder_message.setFixedHeight(32)
        self._cat_reminder_preview = QPushButton("预览")
        self._cat_reminder_preview.setObjectName("BtnSmallPrimary")
        self._cat_reminder_preview.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cat_reminder_preview.setFixedWidth(60)
        self._cat_reminder_preview.setFixedHeight(32)
        self._cat_reminder_message_reset = QPushButton("恢复默认")
        self._cat_reminder_message_reset.setObjectName("BtnSmallSecondary")
        self._cat_reminder_message_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cat_reminder_message_reset.setFixedWidth(82)
        self._cat_reminder_message_reset.setFixedHeight(32)
        message_row = QWidget()
        message_layout = QHBoxLayout(message_row)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.setSpacing(8)
        message_layout.addWidget(self._cat_reminder_message, 1)
        message_layout.addWidget(self._cat_reminder_message_reset)
        message_layout.addWidget(self._cat_reminder_preview)
        form.addRow("提醒文案", message_row)
        self._update_cat_voice_status_label()
        self._update_cat_pre_notify_controls()
        content_layout.addWidget(settings_group)
        self._todo_status_label = self._make_inline_status_label()
        content_layout.addWidget(self._todo_status_label)

        todo_group, todo_layout = self._card("日程工作台")
        todo_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        todo_layout.setContentsMargins(0, 12, 0, 10)
        todo_layout.setSpacing(12)
        todo_area = QWidget()
        todo_area.setObjectName("TodoArea")
        todo_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        todo_area_layout = QHBoxLayout(todo_area)
        todo_area_layout.setContentsMargins(0, 0, 0, 0)
        todo_area_layout.setSpacing(12)
        self._todo_calendar = _TodoCalendarWidget()
        self._todo_calendar.setObjectName("TodoCalendar")
        self._todo_calendar.setSelectedDate(QDate.currentDate())
        self._todo_calendar.setGridVisible(False)
        self._todo_calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self._todo_calendar.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self._todo_calendar.setMinimumWidth(344)
        self._todo_calendar.setMaximumWidth(400)
        self._todo_calendar.setMinimumHeight(430)
        self._todo_calendar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        todo_calendar_panel = QWidget()
        todo_calendar_panel.setMinimumWidth(344)
        todo_calendar_panel.setMaximumWidth(400)
        todo_calendar_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        todo_calendar_layout = QVBoxLayout(todo_calendar_panel)
        todo_calendar_layout.setContentsMargins(0, 0, 0, 0)
        todo_calendar_layout.setSpacing(6)
        todo_calendar_layout.addWidget(self._todo_calendar, 1)
        legend = QLabel(
            "<span style='color:#2f3d56;'>●</span> 已到点　"
            "<span style='color:#ef4444;'>●</span> 未完成　"
            "<span style='color:#16a34a;'>●</span> 已完成"
        )
        legend.setObjectName("TodoCalendarLegend")
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setAlignment(Qt.AlignmentFlag.AlignCenter)
        legend.setFixedHeight(22)
        todo_calendar_layout.addWidget(legend)

        todo_side = QWidget()
        todo_side.setObjectName("TodoSide")
        self._todo_side = todo_side
        todo_side.setMinimumWidth(360)
        todo_side.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        todo_side_layout = QVBoxLayout(todo_side)
        todo_side_layout.setContentsMargins(14, 12, 14, 12)
        todo_side_layout.setSpacing(10)
        self._todo_head = QWidget()
        self._todo_head.setToolTip("双击进入批量操作")
        self._todo_head.setFixedHeight(36)
        todo_head_layout = QHBoxLayout(self._todo_head)
        todo_head_layout.setContentsMargins(0, 0, 0, 0)
        todo_head_layout.setSpacing(8)
        self._todo_date_label = QLabel()
        self._todo_date_label.setObjectName("TodoDateTitle")
        self._todo_overdue_banner_item_id = ""
        self._todo_add_btn = QToolButton()
        self._todo_add_btn.setObjectName("TodoAddButton")
        self._todo_add_btn.setText("+")
        self._todo_add_btn.setToolTip("新建待办 (N)")
        self._todo_add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_add_btn.setFixedSize(32, 32)
        self._todo_batch_select_all = QCheckBox("全选")
        self._todo_batch_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_batch_select_all.setVisible(False)
        self._todo_batch_delete = QPushButton("删除")
        self._todo_batch_delete.setObjectName("BtnSmallDanger")
        self._todo_batch_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_batch_delete.setFixedSize(56, 28)
        self._todo_batch_delete.setVisible(False)
        self._todo_batch_strike = QPushButton("划线")
        self._todo_batch_strike.setObjectName("BtnSmallSecondary")
        self._todo_batch_strike.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_batch_strike.setFixedSize(56, 28)
        self._todo_batch_strike.setVisible(False)
        self._todo_batch_done = QPushButton("完成")
        self._todo_batch_done.setObjectName("BtnSmallPrimary")
        self._todo_batch_done.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_batch_done.setFixedSize(56, 28)
        self._todo_batch_done.setVisible(False)
        todo_head_layout.addWidget(self._todo_date_label, 1)
        todo_head_layout.addWidget(self._todo_batch_select_all)
        todo_head_layout.addStretch(1)
        todo_head_layout.addWidget(self._todo_batch_strike)
        todo_head_layout.addWidget(self._todo_batch_delete)
        todo_head_layout.addWidget(self._todo_batch_done)
        todo_head_layout.addWidget(self._todo_add_btn)

        def _on_todo_head_double_click(event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._toggle_todo_batch_mode(not getattr(self, "_todo_batch_mode", False))
            QWidget.mouseDoubleClickEvent(self._todo_head, event)

        self._todo_head.mouseDoubleClickEvent = _on_todo_head_double_click
        todo_side_layout.addWidget(self._todo_head)
        self._todo_overdue_banner = QLabel()
        self._todo_overdue_banner.setObjectName("TodoOverdueBanner")
        self._todo_overdue_banner.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_overdue_banner.setVisible(False)
        self._todo_overdue_banner.setFixedHeight(30)
        self._todo_overdue_banner.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._todo_overdue_banner.mousePressEvent = lambda event: self._locate_todo_overdue_banner_item()
        todo_side_layout.addWidget(self._todo_overdue_banner)

        todo_filter_bar = QWidget()
        todo_filter_layout = QHBoxLayout(todo_filter_bar)
        todo_filter_layout.setContentsMargins(0, 0, 0, 0)
        todo_filter_layout.setSpacing(8)
        self._todo_search = QLineEdit()
        self._todo_search.setObjectName("TodoSearchInput")
        self._todo_search.setPlaceholderText("搜索标题、内容、日期或状态")
        self._todo_search.setClearButtonEnabled(True)
        self._todo_search.setFixedHeight(32)
        self._todo_date_scope = ModernPopupComboBox()
        self._todo_date_scope.setObjectName("TodoFilterCombo")
        self._todo_date_scope.setFixedHeight(32)
        self._todo_date_scope.addItem("当前日期", "selected")
        self._todo_date_scope.addItem("今天", "today")
        self._todo_date_scope.addItem("未来 7 天", "next7")
        self._todo_date_scope.addItem("全部日期", "all")
        self._style_todo_filter_combo(self._todo_date_scope)
        self._todo_status_filter = ModernPopupComboBox()
        self._todo_status_filter.setObjectName("TodoFilterCombo")
        self._todo_status_filter.setFixedHeight(32)
        self._todo_status_filter.addItem("全部状态", "")
        self._todo_status_filter.addItem("未完成", "active")
        self._todo_status_filter.addItem("已到点", "overdue")
        self._todo_status_filter.addItem("重要", "important")
        self._todo_status_filter.addItem("已完成", "completed")
        self._style_todo_filter_combo(self._todo_status_filter)
        self._todo_filter_clear_btn = QToolButton()
        self._todo_filter_clear_btn.setObjectName("TodoFilterClearButton")
        self._todo_filter_clear_btn.setText("×")
        self._todo_filter_clear_btn.setToolTip("清除状态筛选")
        self._todo_filter_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_filter_clear_btn.setFixedSize(28, 28)
        self._todo_filter_clear_btn.setVisible(False)
        self._todo_filter_clear_btn.setStyleSheet(
            "QToolButton#TodoFilterClearButton { background:#f8fafc; color:#64748b;"
            " border:1px solid #e2e8f0; border-radius:8px; font-size:16px; font-weight:900; padding:0px; }"
            "QToolButton#TodoFilterClearButton:hover { background:#f1f5f9; color:#0f172a; border-color:#cbd5e1; }"
        )
        todo_filter_layout.addWidget(self._todo_search, 1)
        todo_filter_layout.addWidget(self._todo_date_scope)
        todo_filter_layout.addWidget(self._todo_status_filter)
        todo_filter_layout.addWidget(self._todo_filter_clear_btn)
        todo_side_layout.addWidget(todo_filter_bar)

        self._todo_list = QListWidget()
        self._todo_list.setObjectName("TodoList")
        self._todo_list.setFrameShape(QFrame.Shape.NoFrame)
        self._todo_list.setUniformItemSizes(False)
        self._todo_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        todo_side_layout.addWidget(self._todo_list, 1)

        todo_overview = QWidget()
        todo_overview.setObjectName("TodoOverview")
        todo_overview.setMinimumWidth(238)
        todo_overview.setMaximumWidth(280)
        todo_overview.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        overview_layout = QVBoxLayout(todo_overview)
        overview_layout.setContentsMargins(14, 10, 14, 10)
        overview_layout.setSpacing(8)
        self._todo_list_filter = ""
        self._todo_overview_item_id = ""
        self._todo_overview_item_date = QDate()
        self._todo_next_reminder_item_id = ""
        self._todo_next_reminder_item_date = QDate()

        overview_title = QLabel("今明概览")
        overview_title.setObjectName("TodoOverviewTitle")
        overview_layout.addWidget(overview_title)
        self._todo_next_label = QLabel("下一项：暂无")
        self._todo_next_label.setObjectName("TodoNextLabel")
        self._todo_next_label.setWordWrap(True)
        self._todo_next_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_next_label.mousePressEvent = lambda event: self._locate_todo_overview_item()
        overview_layout.addWidget(self._todo_next_label)
        self._todo_next_reminder_label = QLabel("下一次提醒：暂无")
        self._todo_next_reminder_label.setObjectName("TodoNextReminderLabel")
        self._todo_next_reminder_label.setWordWrap(True)
        self._todo_next_reminder_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_next_reminder_label.mousePressEvent = lambda event: self._locate_todo_overview_item(kind="reminder")
        overview_layout.addWidget(self._todo_next_reminder_label)

        stats_grid = QGridLayout()
        stats_grid.setContentsMargins(0, 0, 0, 0)
        stats_grid.setHorizontalSpacing(7)
        stats_grid.setVerticalSpacing(7)
        total_card, self._todo_today_count_label = self._make_todo_stat_chip("今日")
        tomorrow_card, self._todo_tomorrow_count_label = self._make_todo_stat_chip("明天")
        active_card, self._todo_active_count_label = self._make_todo_stat_chip("未完成")
        important_card, self._todo_important_count_label = self._make_todo_stat_chip("重要")
        overdue_card, self._todo_overdue_count_label = self._make_todo_stat_chip("已到点")
        completed_card, self._todo_completed_count_label = self._make_todo_stat_chip("已完成")
        self._todo_stat_cards = {
            "all": total_card,
            "tomorrow": tomorrow_card,
            "active": active_card,
            "important": important_card,
            "overdue": overdue_card,
            "completed": completed_card,
        }
        total_card.mousePressEvent = lambda event: self._activate_todo_overview_card(0, "")
        tomorrow_card.mousePressEvent = lambda event: self._activate_todo_overview_card(1, "")
        active_card.mousePressEvent = lambda event: self._activate_todo_overview_card(0, "active")
        important_card.mousePressEvent = lambda event: self._activate_todo_overview_card(0, "important")
        overdue_card.mousePressEvent = lambda event: self._activate_todo_overview_card(0, "overdue")
        completed_card.mousePressEvent = lambda event: self._activate_todo_overview_card(0, "completed")
        stats_grid.addWidget(total_card, 0, 0)
        stats_grid.addWidget(tomorrow_card, 0, 1)
        stats_grid.addWidget(important_card, 1, 0)
        stats_grid.addWidget(overdue_card, 1, 1)
        stats_grid.addWidget(completed_card, 2, 0)
        stats_grid.addWidget(active_card, 2, 1)
        overview_layout.addLayout(stats_grid)

        quick_title = QLabel("快捷操作")
        quick_title.setObjectName("TodoOverviewSubtitle")
        overview_layout.addWidget(quick_title)
        quick_row = QWidget()
        quick_row_layout = QHBoxLayout(quick_row)
        quick_row_layout.setContentsMargins(0, 0, 0, 0)
        quick_row_layout.setSpacing(8)
        self._todo_today_btn = QPushButton("今天")
        self._todo_today_btn.setObjectName("BtnSmallSecondary")
        self._todo_today_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_today_btn.setFixedHeight(30)
        self._todo_tomorrow_btn = QPushButton("明天")
        self._todo_tomorrow_btn.setObjectName("BtnSmallSecondary")
        self._todo_tomorrow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_tomorrow_btn.setFixedHeight(30)
        quick_row_layout.addWidget(self._todo_today_btn)
        quick_row_layout.addWidget(self._todo_tomorrow_btn)
        overview_layout.addWidget(quick_row)
        self._todo_batch_btn = QPushButton("批量整理")
        self._todo_batch_btn.setObjectName("BtnSmallPrimary")
        self._todo_batch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_batch_btn.setFixedHeight(32)
        overview_layout.addWidget(self._todo_batch_btn)
        overview_layout.addStretch(1)

        todo_area_layout.addWidget(todo_calendar_panel)
        todo_area_layout.addWidget(todo_side, 1)
        todo_area_layout.addWidget(todo_overview)
        todo_layout.addWidget(todo_area, 1)
        content_layout.addWidget(todo_group, 1)
        self._todo_page = page
        self._refresh_todo_list()
        SettingsDialog._install_custom_text_context_menus(self, page)
        return page

    def _current_cat_reminder_settings(self) -> dict:
        try:
            ui = dict(getattr(self._current, "ui", {}) or {})
            return normalize_cat_reminder_settings(ui.get("cat_reminder"))
        except Exception:
            return normalize_cat_reminder_settings(None)

    def _cat_effective_pre_notify_ms(self, total_interval_ms: int) -> int:
        reminder = self._current_cat_reminder_settings()
        configured_ms = max(5 * 1000, int(reminder.get("pre_notify_seconds", 5) or 5) * 1000)
        max_pre_ms = max(5 * 1000, int(total_interval_ms) - 1000)
        return int(min(configured_ms, max_pre_ms))

    def _update_cat_pre_notify_controls(self) -> None:
        if not hasattr(self, "_cat_reminder_pre_notify_seconds"):
            return
        try:
            enabled = bool(self._cat_reminder_pre_notify.isChecked())
            self._cat_reminder_pre_notify_seconds.setEnabled(enabled)
        except Exception:
            pass

    def _cat_voice_status(self) -> tuple[str, str, str]:
        if not hasattr(self, "_cat_reminder_voice") or not bool(self._cat_reminder_voice.isChecked()):
            return "语音状态：未授权", "#94a3b8", "勾选语音提醒后，应用会尝试调用本机语音组件。"
        if not sys.platform.startswith("win"):
            return "语音状态：不可用", "#dc2626", "当前系统不支持本地语音提醒。"
        try:
            import win32com.client  # type: ignore  # noqa: F401

            return "语音状态：可用", "#16a34a", "本机语音组件可用。"
        except Exception as exc:
            text = str(exc).lower()
            if any(token in text for token in ("access", "denied", "permission", "unauthorized", "拒绝", "权限")):
                return "语音状态：未授权", "#d97706", "系统拒绝访问语音组件，请检查权限或系统设置。"
            return "语音状态：不可用", "#dc2626", "未检测到可用的本机语音组件，预览时会尝试使用系统提示音。"

    def _update_cat_voice_status_label(self) -> None:
        label = getattr(self, "_cat_voice_status_label", None)
        if label is None:
            return
        try:
            text, color, tooltip = self._cat_voice_status()
            label.setText(text)
            label.setToolTip(tooltip)
            label.setStyleSheet(f"color:{color}; font-size:12px; font-weight:700;")
        except Exception:
            pass

    def _reset_cat_reminder_message(self) -> None:
        if not hasattr(self, "_cat_reminder_message"):
            return
        from deepcat.ui.main_window.compact import StyledMessageBox
        box = StyledMessageBox(self._todo_dialog_parent())
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("恢复默认提醒文案")
        box.setText("确定恢复默认提醒文案吗？")
        box.setInformativeText("当前提醒文案会被默认文案覆盖，此操作只影响休息提醒文案。")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setStyleSheet(self._message_box_style())
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self._cat_reminder_message.setText(str(DEFAULT_CAT_REMINDER["message"]))
        if self._apply_cat_reminder_settings():
            self._show_todo_status("提醒文案已恢复默认。", tone="success", auto_hide_ms=1800)

    def _apply_cat_reminder_settings(self, *_, notify: bool = True) -> bool:
        try:
            ui = dict(getattr(self._current, "ui", {}) or {})
            reminder = {
                "enabled": bool(self._cat_reminder_enabled.isChecked()),
                "interval_minutes": int(self._cat_reminder_interval.value()),
                "duration_seconds": int(self._cat_reminder_duration.value()),
                "voice_enabled": bool(self._cat_reminder_voice.isChecked()),
                "exit_enabled": bool(self._cat_reminder_exit.isChecked()),
                "pre_notify_enabled": bool(self._cat_reminder_pre_notify.isChecked()),
                "pre_notify_seconds": int(self._cat_reminder_pre_notify_seconds.value()),
                "message": str(self._cat_reminder_message.text()).strip(),
            }
            ui["cat_reminder"] = normalize_cat_reminder_settings(reminder)
            self._current = update_ui_settings(cat_reminder=ui["cat_reminder"])
            self._sync_settings_after_change()
            self._update_cat_pre_notify_controls()
            self._update_cat_voice_status_label()
            self._schedule_cat_reminder()
            if bool(notify):
                self._show_todo_status("提醒设置已保存。", tone="success", auto_hide_ms=1800)
            return True
        except Exception as exc:
            get_logger().exception("保存休息提醒设置失败")
            if bool(notify):
                self._show_todo_status(f"提醒设置保存失败：{exc}", tone="error", auto_hide_ms=5200)
            return False

    def _cat_voice_status_message(self) -> tuple[str, str]:
        if not hasattr(self, "_cat_reminder_voice") or not bool(self._cat_reminder_voice.isChecked()):
            return "已播放预览。", "success"
        if sys.platform.startswith("win"):
            try:
                import win32com.client  # type: ignore  # noqa: F401

                return "已播放预览。", "success"
            except Exception:
                try:
                    import winsound  # type: ignore  # noqa: F401

                    return "已播放预览，语音组件不可用，已改用系统提示音。", "warning"
                except Exception:
                    return "预览已打开，但语音组件不可用。", "warning"
        return "预览已打开，但当前系统不支持本地语音提醒。", "warning"

    def _schedule_cat_reminder(self) -> None:
        try:
            self._cat_reminder_timer.stop()
        except Exception:
            pass
        if not self._feature_enabled("todo"):
            return
        reminder = self._current_cat_reminder_settings()
        if not bool(reminder.get("enabled", False)):
            return
        interval_ms = int(reminder.get("interval_minutes", 45)) * 60 * 1000

        if bool(reminder.get("pre_notify_enabled", False)):
            pre_notify_ms = self._cat_effective_pre_notify_ms(interval_ms)
            self._in_pre_notify_stage = True
            timer_duration = max(1000, interval_ms - pre_notify_ms)
        else:
            self._in_pre_notify_stage = False
            timer_duration = max(60 * 1000, interval_ms)

        self._cat_reminder_timer.start(int(timer_duration))

    def _preview_cat_reminder(self) -> None:
        if not self._apply_cat_reminder_settings(notify=False):
            self._show_todo_status("预览失败：提醒设置保存失败。", tone="error", auto_hide_ms=4200)
            return
        if self._show_cat_reminder(preview=True):
            message, tone = self._cat_voice_status_message()
            self._show_todo_status(message, tone=tone, auto_hide_ms=3200 if tone == "success" else 5200)
        else:
            self._show_todo_status("预览失败：当前已有提醒窗口正在显示。", tone="warning", auto_hide_ms=4200)

    def _show_cat_reminder(self, *, preview: bool = False) -> bool:
        if not bool(preview) and not self._feature_enabled("todo"):
            return False
        reminder = self._current_cat_reminder_settings()
        if not bool(preview) and not bool(reminder.get("enabled", False)):
            return False

        if not bool(preview) and bool(reminder.get("pre_notify_enabled", False)) and getattr(self, "_in_pre_notify_stage", False):
            self._in_pre_notify_stage = False

            # 关闭可能残留的预警小弹窗
            if getattr(self, "_cat_pre_popup", None) is not None:
                try:
                    self._cat_pre_popup.close()
                except Exception:
                    pass
                self._cat_pre_popup = None

            # 弹出预警小弹窗
            try:
                popup = _CatRestReminderPopup(exit_enabled=bool(reminder.get("exit_enabled", True)))
                self._cat_pre_popup = popup
                popup.snoozeRequested.connect(self._snooze_cat_reminder)
                popup.exitRequested.connect(self._exit_cat_reminder)
                popup.destroyed.connect(lambda *_: setattr(self, "_cat_pre_popup", None))
                self._position_cat_pre_notify_popup(popup)
                popup.show()
                popup.raise_()
                popup.activateWindow()
            except Exception:
                get_logger().exception("显示休息预警弹窗失败")
                return False

            interval_ms = int(reminder.get("interval_minutes", 45)) * 60 * 1000
            self._cat_reminder_timer.start(self._cat_effective_pre_notify_ms(interval_ms))
            return True

        if self._cat_reminder_session is not None:
            return False

        # 关闭可能残留的预警小弹窗
        if getattr(self, "_cat_pre_popup", None) is not None:
            try:
                self._cat_pre_popup.close()
            except Exception:
                pass
            self._cat_pre_popup = None

        try:
            session = CatReminderSession(
                duration_seconds=int(reminder.get("duration_seconds", 20)),
                message=str(reminder.get("message", "")),
                voice_enabled=bool(reminder.get("voice_enabled", False)),
                icon_path=self._assets_dir() / "deepcat_logo.svg",
                exit_enabled=bool(reminder.get("exit_enabled", True)),
            )
            self._cat_reminder_session = session

            def done() -> None:
                if self._cat_reminder_session is session:
                    self._cat_reminder_session = None
                self._schedule_cat_reminder()

            session.finished.connect(done)
            session.start()
            return True
        except Exception:
            self._cat_reminder_session = None
            get_logger().exception("显示猫咪提醒失败")
            self._schedule_cat_reminder()
            return False

    def _snooze_cat_reminder(self, minutes: int) -> None:
        """延迟猫咪休息。"""
        try:
            self._cat_reminder_timer.stop()
        except Exception:
            pass
        self._in_pre_notify_stage = True
        interval_ms = minutes * 60 * 1000
        pre_notify_ms = self._cat_effective_pre_notify_ms(interval_ms)
        timer_duration = max(1000, interval_ms - pre_notify_ms)
        self._cat_reminder_timer.start(int(timer_duration))
        self._send_tray_notification("休息已推迟", f"将在 {minutes} 分钟后重新提醒您休息。", 2000)

    def _exit_cat_reminder(self) -> None:
        """本次退出猫咪休息，重置到下一个完整周期。"""
        try:
            self._cat_reminder_timer.stop()
        except Exception:
            pass
        self._schedule_cat_reminder()

    def _position_cat_pre_notify_popup(self, popup: QWidget) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        popup.adjustSize()
        width = min(380, max(320, popup.width()))
        height = popup.height()
        popup.resize(width, height)
        popup.move(geo.right() - popup.width() - 18, geo.bottom() - popup.height() - 18)
