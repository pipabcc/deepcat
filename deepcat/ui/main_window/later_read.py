from __future__ import annotations

from typing import Any
from PyQt6.QtCore import Qt, QEvent, QPoint, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QFrame,
    QCheckBox,
    QLineEdit,
)


class _LaterReadItemWidget(QWidget):
    openRequested = pyqtSignal(str)
    copyRequested = pyqtSignal(str)
    pinRequested = pyqtSignal(str, bool)
    editRequested = pyqtSignal(str)
    deleteRequested = pyqtSignal(str)
    selectionChanged = pyqtSignal(str, bool)

    def __init__(
        self,
        item: dict[str, Any],
        *,
        time_text: str,
        copy_icon: QIcon,
        pin_icon: QIcon,
        edit_icon: QIcon,
        delete_icon: QIcon,
        batch_mode: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._item_id = str(item.get("id", ""))
        self._copy_icon = copy_icon
        self._pin_icon = pin_icon
        self._edit_icon = edit_icon
        self._delete_icon = delete_icon
        self._is_pinned = bool(item.get("is_pinned", item.get("pinned", False)))
        self._batch_mode = bool(batch_mode)
        title = str(item.get("title", "") or item.get("url", "") or "未命名链接")
        url = str(item.get("url", "") or "").strip()
        self._tooltip_text = f"{title}\n{url}" if url and url != title else (url or title)
        from deepcat.ui.post_capture_actions import SmoothToolTip
        self._tooltip = SmoothToolTip()
        self.setObjectName("LaterReadItemWidget")
        self.setMouseTracking(True)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 3, 3, 3)
        root.setSpacing(5)

        if self._batch_mode:
            self._checkbox = QCheckBox()
            self._checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
            self._checkbox.stateChanged.connect(lambda state: self.selectionChanged.emit(self._item_id, bool(state)))
            root.addWidget(self._checkbox)
        else:
            self._checkbox = None

        self._dot = QLabel("•")
        self._dot.setObjectName("LaterReadDot")
        self._dot.setFixedWidth(12)
        self._dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dot_color = "#f59e0b" if self._is_pinned else ("#9fb1c8" if bool(item.get("read", False)) else "#173a67")
        self._dot.setStyleSheet(f"color: {dot_color}; font-size: 18px; font-weight: 900;")
        self._dot.setMouseTracking(True)
        self._dot.installEventFilter(self)
        root.addWidget(self._dot)

        self._title_btn = QPushButton(title)
        self._title_btn.setObjectName("LaterReadTitleButton")
        self._title_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title_btn.setMinimumWidth(0)
        self._title_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._title_btn.setToolTip("")
        self._title_btn.clicked.connect(lambda *_: self.openRequested.emit(self._item_id))
        root.addWidget(self._title_btn, 1)

        site = str(item.get("site", "") or "").strip()
        self._site = QLabel(site)
        self._site.setObjectName("LaterReadSite")
        self._site.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._site.setToolTip("")
        self._site.setMinimumWidth(0)
        self._site.setMaximumWidth(120)
        root.addWidget(self._site)
        root.addSpacing(8)

        self._right_box = QWidget()
        self._right_box.setObjectName("LaterReadRightBox")
        self._right_box.setMinimumHeight(30)
        self._right_box.setFixedWidth(120)
        root.addWidget(self._right_box)

        right_layout = QHBoxLayout(self._right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        self._time = QLabel(str(time_text))
        self._time.setObjectName("LaterReadTime")
        self._time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._time.setFixedWidth(78)
        right_layout.addWidget(self._time)

        self._action_box = QWidget(self._right_box)
        action_layout = QHBoxLayout(self._action_box)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(4)

        self._copy_btn = QToolButton()
        self._copy_btn.setObjectName("LaterReadIconButton")
        self._copy_btn.setToolTip("复制")
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.setFixedSize(22, 22)
        self._copy_btn.clicked.connect(lambda *_: self.copyRequested.emit(self._item_id))
        action_layout.addWidget(self._copy_btn)

        self._pin_btn = QToolButton()
        self._pin_btn.setObjectName("LaterReadIconButton")
        self._pin_btn.setToolTip("取消置顶" if self._is_pinned else "置顶")
        self._pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pin_btn.setFixedSize(22, 22)
        self._pin_btn.clicked.connect(lambda *_: self.pinRequested.emit(self._item_id, not self._is_pinned))
        action_layout.addWidget(self._pin_btn)

        self._edit_btn = QToolButton()
        self._edit_btn.setObjectName("LaterReadIconButton")
        self._edit_btn.setToolTip("编辑")
        self._edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_btn.setFixedSize(22, 22)
        self._edit_btn.clicked.connect(lambda *_: self.editRequested.emit(self._item_id))
        action_layout.addWidget(self._edit_btn)

        self._delete_btn = QToolButton()
        self._delete_btn.setObjectName("LaterReadIconButton")
        self._delete_btn.setToolTip("删除")
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.setFixedSize(22, 22)
        self._delete_btn.clicked.connect(lambda *_: self.deleteRequested.emit(self._item_id))
        action_layout.addWidget(self._delete_btn)

        self._action_box.setFixedSize(self._action_box.sizeHint())
        self._set_actions_visible(False)

        self._separator = QFrame(self)
        self._separator.setObjectName("LaterReadSeparator")
        self._separator.setFixedHeight(1)
        self._separator.setStyleSheet("background: #f1f5f9; border: none;")
        self._separator.raise_()

    def item_id(self) -> str:
        return self._item_id

    def setChecked(self, checked: bool) -> None:
        if self._checkbox is not None:
            self._checkbox.setChecked(bool(checked))

    def isChecked(self) -> bool:
        if self._checkbox is not None:
            return bool(self._checkbox.isChecked())
        return False

    def _set_actions_visible(self, visible: bool) -> None:
        if self._batch_mode:
            visible = False
        self._position_action_box()
        self._copy_btn.setIcon(self._copy_icon if visible else QIcon())
        self._pin_btn.setIcon(self._pin_icon if visible else QIcon())
        self._edit_btn.setIcon(self._edit_icon if visible else QIcon())
        self._delete_btn.setIcon(self._delete_icon if visible else QIcon())
        self._copy_btn.setEnabled(bool(visible))
        self._pin_btn.setEnabled(bool(visible))
        self._edit_btn.setEnabled(bool(visible))
        self._delete_btn.setEnabled(bool(visible))
        self._action_box.setVisible(bool(visible))
        if visible:
            self._action_box.raise_()

    def _position_action_box(self) -> None:
        try:
            self._action_box.move(
                max(0, self._right_box.width() - self._action_box.width()),
                max(0, int((self._right_box.height() - self._action_box.height()) / 2)),
            )
        except Exception:
            pass

    def _position_separator(self) -> None:
        try:
            self._separator.setVisible(self.property("last") is not True)
            self._separator.setGeometry(0, max(0, self.height() - 1), self.width(), 1)
            self._separator.raise_()
        except Exception:
            pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_action_box()
        self._position_separator()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._dot:
            if event.type() in {QEvent.Type.HoverEnter, QEvent.Type.Enter}:
                tip_text = str(getattr(self, "_tooltip_text", "") or "").strip()
                if tip_text:
                    title_top = self._dot.mapToGlobal(QPoint(0, 0))
                    title_bottom = self._dot.mapToGlobal(QPoint(0, self._dot.height()))
                    anchor_x = max(0, int(self._dot.width() / 2))
                    pos = self._dot.mapToGlobal(QPoint(anchor_x, self._dot.height() + 6))
                    direction = "below"
                    parent_widget = self.parentWidget()
                    max_width = max(120, parent_widget.width() - 20) if parent_widget is not None else 360
                    max_height = max(60, parent_widget.height() - 20) if parent_widget is not None else 220
                    screen = QGuiApplication.screenAt(title_bottom) or QGuiApplication.primaryScreen()
                    if screen is not None:
                        geo = screen.availableGeometry()
                        below_space = int(geo.bottom() - title_bottom.y() - 10)
                        above_space = int(title_top.y() - geo.top() - 10)
                        if below_space < 120 and above_space > below_space:
                            direction = "above"
                            pos = self._dot.mapToGlobal(QPoint(anchor_x, -6))
                            max_height = min(max_height, max(40, above_space))
                        else:
                            max_height = min(max_height, max(40, below_space))
                    self._tooltip.show_text(tip_text, pos, direction=direction, max_width=max_width, max_height=max_height)
            elif event.type() in {QEvent.Type.HoverLeave, QEvent.Type.Leave}:
                self._tooltip.hide()
        return super().eventFilter(watched, event)

    def enterEvent(self, event) -> None:
        self._set_actions_visible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._set_actions_visible(False)
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        self._position_separator()


class _LaterReadPinnedFoldToggleWidget(QWidget):
    clicked = pyqtSignal()

    def __init__(self, total_count: int, folded: bool, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("LaterReadPinnedFoldToggleWidget")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(0)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(self._label, 1)
        self.setStyleSheet("""
            QWidget#LaterReadPinnedFoldToggleWidget {
                background: transparent;
                border-bottom: 1px solid #f1f5f9;
            }
            QWidget#LaterReadPinnedFoldToggleWidget:hover {
                background: rgba(30, 41, 59, 0.04);
            }
        """)
        self.update_state(total_count, folded)

    def update_state(self, total_count: int, folded: bool) -> None:
        hidden_count = max(0, int(total_count) - 3)
        if folded:
            self._label.setText(f"展开其余 {hidden_count} 项置顶稍后阅读... ∨")
        else:
            self._label.setText("折叠多余置顶稍后阅读... ∧")

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _LaterReadEditDialog(QDialog):
    def __init__(self, item: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
        self.setWindowTitle("编辑稍后阅读")
        self.setModal(True)
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)

        self._title_edit = QLineEdit(str(item.get("title", "") or ""))
        self._title_edit.setPlaceholderText("标题")
        self._url_edit = QLineEdit(str(item.get("url", "") or ""))
        self._url_edit.setPlaceholderText("https://example.com")
        form.addRow("标题:", self._title_edit)
        form.addRow("网址:", self._url_edit)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.setStyleSheet(f"""
            QDialog {{
                background: {MAIN_WINDOW_BACKGROUND};
            }}
            QLineEdit {{
                min-height: 30px;
                padding: 0 10px;
                border: 1px solid #d8e0ec;
                border-radius: 7px;
                color: #111827;
                background: #ffffff;
            }}
            QLineEdit:focus {{
                border-color: #94a3b8;
            }}
        """)

    def _accept_if_valid(self) -> None:
        url = self.url()
        if not url.startswith(("http://", "https://")):
            QMessageBox.warning(self, "网址无效", "稍后阅读仅支持 http:// 或 https:// 链接。")
            return
        self.accept()

    def title(self) -> str:
        return str(self._title_edit.text() or "").strip()

    def url(self) -> str:
        return str(self._url_edit.text() or "").strip()

    def _apply_caption_color(self) -> None:
        try:
            import os
            import ctypes
            if os.name != "nt":
                return
            from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND_COLORREF
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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_caption_color()


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



class LaterReadMixin:
    def _start_later_read_probe_from_hotkey(self) -> None:
        if not self._feature_enabled("later_read"):
            return
        if not self._later_read_enabled_now():
            return
        if self._thread is not None or self._region_overlay is not None or self._cat_reminder_session is not None:
            return
        if self._later_read_probe_overlay is not None:
            try:
                self._later_read_probe_overlay.raise_()
                self._later_read_probe_overlay.activateWindow()
            except Exception:
                pass
            return
        self._last_later_read_capture_key = ""
        self._later_read_probe_timeout_anchor_pos = QPoint(QCursor.pos())
        self._later_read_probe_timeout_extensions = 0
        try:
            from deepcat.ui.region_overlay import RegionOverlay

            overlay = RegionOverlay(
                confirm_delay_ms=0,
                close_on_confirm=False,
                freeze_on_start=False,
                auto_snap=False,
                annotation_style=self._current_annotation_style(),
                cursor_shape_provider=NotificationPopup.cursor_shape_at,
                link_probe_enabled=True,
                link_probe_only=True,
                show_magnifier=False,
                cursor_color="#16A34A",
            )
            overlay.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self._later_read_probe_overlay = overlay
            owner = self
            overlay.destroyed.connect(
                lambda *_, owner=owner, expected=overlay: owner._on_later_read_probe_destroyed(expected)
            )
            overlay.linkHovered.connect(self._on_region_link_hovered)
            generation = int(getattr(self, "_later_read_probe_generation", 0)) + 1
            self._later_read_probe_generation = generation
            self._schedule_later_read_probe_timeout(generation)
            overlay.show_capture_overlay()
            overlay.activateWindow()
            overlay.raise_()
            NotificationPopup.raise_active_popups()
        except Exception:
            self._later_read_probe_overlay = None
            self._later_read_probe_timeout_anchor_pos = None
            self._later_read_probe_timeout_extensions = 0
            get_logger().exception("启动稍后阅读链接提取失败")

    def _schedule_later_read_probe_timeout(self, generation: int) -> None:
        QTimer.singleShot(
            3000,
            lambda generation=generation: self._close_later_read_probe_after_timeout(generation),
        )

    def _close_later_read_probe_overlay(self, *, restore_cursor: bool = True) -> None:
        overlay = getattr(self, "_later_read_probe_overlay", None)
        self._later_read_probe_generation = int(getattr(self, "_later_read_probe_generation", 0)) + 1
        self._later_read_probe_timeout_anchor_pos = None
        self._later_read_probe_timeout_extensions = 0
        if overlay is not None:
            try:
                overlay.close()
            except RuntimeError:
                pass
            except Exception:
                get_logger().debug("关闭稍后阅读链接探测层失败", exc_info=True)
            finally:
                if getattr(self, "_later_read_probe_overlay", None) is overlay:
                    self._later_read_probe_overlay = None
        if not restore_cursor:
            return
        restore_cursor_fn = getattr(self, "_restore_later_read_probe_cursor", None)
        if callable(restore_cursor_fn):
            restore_cursor_fn()
            try:
                QTimer.singleShot(0, restore_cursor_fn)
                QTimer.singleShot(80, restore_cursor_fn)
            except Exception:
                pass

    def _on_later_read_probe_destroyed(self, expected: QWidget) -> None:
        """旧探测层延迟销毁时，不得误清除刚创建的新探测层引用。"""
        if getattr(self, "_later_read_probe_overlay", None) is not expected:
            return
        self._later_read_probe_overlay = None
        restore_cursor = getattr(self, "_restore_later_read_probe_cursor", None)
        if callable(restore_cursor):
            restore_cursor()

    def _restore_later_read_probe_cursor(self) -> None:
        # 截图/探测层接管光标时，迟到的还原不得清除接管层的合法光标覆盖。
        blocked = getattr(self, "_cursor_refresh_blocked", None)
        if callable(blocked):
            try:
                if blocked():
                    return
            except Exception:
                pass
        restore_normal = getattr(self, "_restore_normal_cursor", None)
        if callable(restore_normal):
            restore_normal()
        refresh_hover = getattr(self, "_refresh_hover_cursor", None)
        if callable(refresh_hover):
            refresh_hover()

    def _later_read_probe_cursor_moved_for_timeout_extension(self) -> bool:
        anchor = getattr(self, "_later_read_probe_timeout_anchor_pos", None)
        if not isinstance(anchor, QPoint):
            return False
        try:
            pos = QPoint(QCursor.pos())
            dx = int(pos.x()) - int(anchor.x())
            dy = int(pos.y()) - int(anchor.y())
            return dx * dx + dy * dy >= 16 * 16
        except Exception:
            return False

    def _extend_later_read_probe_timeout_once(self, generation: int) -> bool:
        if int(getattr(self, "_later_read_probe_timeout_extensions", 0) or 0) >= 1:
            return False
        if not self._later_read_probe_cursor_moved_for_timeout_extension():
            return False
        self._later_read_probe_timeout_extensions = int(getattr(self, "_later_read_probe_timeout_extensions", 0) or 0) + 1
        try:
            self._later_read_probe_timeout_anchor_pos = QPoint(QCursor.pos())
        except Exception:
            self._later_read_probe_timeout_anchor_pos = None
        overlay = getattr(self, "_later_read_probe_overlay", None)
        burst = getattr(overlay, "_schedule_link_probe_burst", None)
        if callable(burst):
            try:
                burst()
            except Exception:
                pass
        self._schedule_later_read_probe_timeout(generation)
        return True

    def _close_later_read_probe_after_timeout(self, generation: int) -> None:
        if int(generation) != int(getattr(self, "_later_read_probe_generation", 0)):
            return
        if getattr(self, "_later_read_probe_overlay", None) is None:
            return
        if self._extend_later_read_probe_timeout_once(int(generation)):
            return
        self._show_later_read_capture_status(
            "3 秒内未识别到链接，已退出稍后阅读采集。",
            tone="info",
            auto_hide_ms=2600,
        )
        self._close_later_read_probe_overlay()

    def _open_later_read_from_tray(self) -> None:
        if self._is_tray_menu_click_throttled():
            return
        if not self._feature_enabled("later_read"):
            return
        self._switch_page(3)
        self._show_from_tray()

    def _refresh_tray_later_read_action(self) -> None:
        if self._tray_new_todo_action is not None:
            self._tray_new_todo_action.setVisible(self._feature_enabled("todo"))
            self._tray_new_todo_action.setText("新建待办 (Alt+N)")
        if self._tray_later_read_action is not None:
            self._tray_later_read_action.setVisible(self._feature_enabled("later_read") and self._later_read_enabled_now())
            hk_str = self._format_hotkey_for_tray(getattr(self, "_later_read_hotkey_str", ""))
            self._tray_later_read_action.setText(f"稍后阅读 ({hk_str})" if hk_str else "稍后阅读")
        if getattr(self, "_tray_clipboard_action", None) is not None:
            self._tray_clipboard_action.setVisible(self._feature_enabled("clipboard_history"))
        if getattr(self, "_tray_notes_action", None) is not None:
            self._tray_notes_action.setVisible(self._feature_enabled("table_notes"))
        if getattr(self, "_tray_note_float_action", None) is not None:
            self._tray_note_float_action.setVisible(self._feature_enabled("table_notes"))
        if getattr(self, "_tray_clipboard_float_action", None) is not None:
            self._tray_clipboard_float_action.setVisible(self._feature_enabled("clipboard_history"))
        if getattr(self, "_tray_later_read_float_action", None) is not None:
            self._tray_later_read_float_action.setVisible(self._feature_enabled("later_read") and self._later_read_enabled_now())
        if getattr(self, "_tray_show_action", None) is not None:
            hk_str = self._format_hotkey_for_tray(getattr(self, "_start_hotkey_str", ""))
            self._tray_show_action.setText(f"开始截图 ({hk_str})" if hk_str else "开始截图")
        if getattr(self, "_tray_ai_qa_action", None) is not None:
            hk_str = self._format_hotkey_for_tray(getattr(self, "_ai_qa_hotkey_str", ""))
            self._tray_ai_qa_action.setText(f"AI对话 ({hk_str})" if hk_str else "AI对话")

    def _show_later_read_status(self, text: str, *, tone: str = "info", auto_hide_ms: int = 3600) -> None:
        self._show_inline_status(getattr(self, "_later_read_status_label", None), text, tone=tone, auto_hide_ms=auto_hide_ms)

    def _show_later_read_capture_status(
        self,
        text: str,
        *,
        tone: str = "info",
        auto_hide_ms: int = 2600,
        toast_pos: Optional[QPoint] = None,
    ) -> None:
        self._show_later_read_status(text, tone=tone, auto_hide_ms=auto_hide_ms)
        if toast_pos is None:
            return
        title_map = {
            "success": "稍后阅读",
            "error": "稍后阅读失败",
            "warning": "稍后阅读提醒",
            "info": "稍后阅读",
        }
        try:
            NotificationPopup.show_notification_near(
                title_map.get(str(tone), "稍后阅读"),
                str(text),
                QPoint(toast_pos),
                int(auto_hide_ms or 2600),
            )
        except Exception as exc:
            get_logger().exception("显示稍后阅读鼠标位置通知失败")
            self._show_notification_failure_status(f"通知显示失败：{exc}")

    def _build_later_read_page(self) -> QWidget:
        self._later_read_debounce_timer = QTimer(self)
        self._later_read_debounce_timer.setSingleShot(True)
        self._later_read_debounce_timer.timeout.connect(self._refresh_later_read_list)
        self._later_read_items_cache = []
        self._later_read_offset = 0
        self._later_read_has_more = True
        self._later_read_loading = False

        self._later_read_enabled = _SwitchCheckBox()
        self._later_read_enabled.setObjectName("LaterReadSwitch")
        self._later_read_enabled.setToolTip("开启后，按 F2 进入绿色十字鼠标采集网页链接")
        self._later_read_enabled.setChecked(bool(self._later_read.get("enabled", False)))
        page = self._make_page("稍后阅读", title_extra=self._later_read_enabled, action_widget=self._make_pin_button("later_read"))
        content_layout: QVBoxLayout = page._content_layout  # type: ignore[attr-defined]

        group, layout = self._card("稍后阅读列表")
        layout.setContentsMargins(10, 12, 10, 9)
        layout.setSpacing(8)

        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(8)

        self._later_read_search = QLineEdit()
        self._later_read_search.setObjectName("LaterReadSearch")
        self._later_read_search.setPlaceholderText("搜索标题或网址")
        self._later_read_search.setClearButtonEnabled(True)
        self._later_read_search.setFixedWidth(180)
        self._later_read_search.setFixedHeight(30)
        toolbar_layout.addWidget(self._later_read_search)

        self._later_read_batch_hotspot = QWidget()
        self._later_read_batch_hotspot.setObjectName("LaterReadBatchHotspot")
        self._later_read_batch_hotspot.setToolTip("双击该处空白位置打开批量管理")
        self._later_read_batch_hotspot.setMinimumHeight(30)
        self._later_read_batch_hotspot.setMinimumWidth(0)
        hotspot_layout = QHBoxLayout(self._later_read_batch_hotspot)
        hotspot_layout.setContentsMargins(0, 0, 0, 0)
        hotspot_layout.setSpacing(6)
        self._later_read_stats = QLabel("显示 0 / 共 0 条")
        self._later_read_stats.setToolTip(self._later_read_batch_hotspot.toolTip())
        self._later_read_stats.setObjectName("LaterReadStats")
        self._later_read_stats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hotspot_layout.addWidget(self._later_read_stats, 1)
        self._later_read_batch_sel_count_label = QLabel("已选择 0 条")
        self._later_read_batch_sel_count_label.setObjectName("LaterReadBatchSelCount")
        self._later_read_batch_sel_count_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        self._later_read_batch_sel_count_label.setVisible(False)
        hotspot_layout.addWidget(self._later_read_batch_sel_count_label, 1)
        self._later_read_batch_select_all = QCheckBox("全选")
        self._later_read_batch_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._later_read_batch_select_all.setFixedWidth(52)
        self._later_read_batch_delete = QPushButton("删除")
        self._later_read_batch_delete.setObjectName("BtnSmallDanger")
        self._later_read_batch_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self._later_read_batch_delete.setFixedSize(52, 28)
        self._later_read_batch_done = QPushButton("完成")
        self._later_read_batch_done.setObjectName("BtnSmallPrimary")
        self._later_read_batch_done.setCursor(Qt.CursorShape.PointingHandCursor)
        self._later_read_batch_done.setFixedSize(56, 28)
        hotspot_layout.addWidget(self._later_read_batch_select_all)
        hotspot_layout.addWidget(self._later_read_batch_delete)
        hotspot_layout.addWidget(self._later_read_batch_done)

        def _on_later_read_hotspot_double_click(event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._toggle_later_read_batch_mode(not getattr(self, "_later_read_batch_mode", False))
            QWidget.mouseDoubleClickEvent(self._later_read_batch_hotspot, event)

        self._later_read_batch_hotspot.mouseDoubleClickEvent = _on_later_read_hotspot_double_click
        toolbar_layout.addWidget(self._later_read_batch_hotspot, 1)

        self._later_read_filter_btn = QToolButton()
        self._later_read_filter_btn.setObjectName("LaterReadFilterButton")
        self._later_read_filter_btn.setText("过滤")
        self._later_read_filter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._later_read_filter_btn.setToolTip(self._later_read_filter_tooltip())
        self._later_read_filter_btn.setFixedHeight(30)
        toolbar_layout.addWidget(self._later_read_filter_btn)

        self._later_read_sort_btn = QToolButton()
        self._later_read_sort_btn.setObjectName("LaterReadSortButton")
        self._later_read_sort_btn.setText("时间")
        self._later_read_sort_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._later_read_sort_btn.setFixedWidth(76)
        self._later_read_sort_btn.setFixedHeight(30)
        self._later_read_sort_btn.clicked.connect(self._show_later_read_sort_menu)
        toolbar_layout.addWidget(self._later_read_sort_btn)
        layout.addWidget(toolbar)

        self._later_read_status_label = self._make_inline_status_label()
        layout.addWidget(self._later_read_status_label)

        self._later_read_list = QListWidget()
        self._later_read_list.setObjectName("LaterReadList")
        self._later_read_list.setFrameShape(QFrame.Shape.NoFrame)
        self._later_read_list.setUniformItemSizes(False)
        self._later_read_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._later_read_list.verticalScrollBar().valueChanged.connect(self._on_later_read_scroll)
        layout.addWidget(self._later_read_list, 1)
        self._toggle_later_read_batch_mode(False)

        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        group.setMinimumHeight(340)
        content_layout.addWidget(group, 1)
        self._refresh_later_read_list()
        return page

    def _change_later_read_hotkey_from_settings(self, new_hotkey: str) -> tuple[bool, str]:
        result = self._replace_global_hotkey(
            "_later_read_hotkey", "_later_read_hotkey_str", self._later_read_hotkey_signal.emit, new_hotkey
        )
        if result[0]:
            self._refresh_hotkey_label()
        return result

    def _apply_later_read_hotkey(self) -> None:
        qt_seq = self._later_read_hotkey_edit.keySequence().toString()
        new_hotkey = qt_to_pynput(qt_seq)
        if not str(qt_seq).strip():
            self._show_capture_status("稍后阅读快捷键不能为空。", tone="warning")
            return
        if new_hotkey is None:
            new_hotkey = str(qt_seq).strip().lower()
        ui = dict(getattr(self._current, "ui", {}) or {})
        if str(new_hotkey) == str(ui.get("later_read_hotkey", "<f3>")):
            return
        if self._on_later_read_hotkey_changed is not None:
            ok, msg = self._on_later_read_hotkey_changed(str(new_hotkey))
            if not ok:
                self._show_capture_status(f"稍后阅读快捷键保存失败：{msg or new_hotkey}", tone="error", auto_hide_ms=5200)
                return
        self._current = update_ui_settings(later_read_hotkey=str(new_hotkey))
        self._sync_settings_after_change()
        self._show_capture_status(f"稍后阅读快捷键已保存：{qt_seq}", tone="success")

    def _current_later_read_settings(self) -> dict[str, Any]:
        """从 SQLite 读取 later_read 完整设置。"""
        try:
            return normalize_later_read_settings(self._later_read_store.load_settings())
        except Exception:
            return normalize_later_read_settings(None)

    def _current_later_read_items(self) -> list[dict[str, Any]]:
        try:
            return self._later_read_store.load_items()
        except Exception:
            return []

    def _save_later_read_settings(self, later_read: dict[str, Any], *, refresh: bool = True) -> None:
        normalized = normalize_later_read_settings(later_read)
        self._later_read = normalized
        try:
            self._later_read_store.save_full(normalized)
        except Exception:
            pass
        if hasattr(self, "_later_read_list"):
            is_page_active = False
            if hasattr(self, "_stack") and self._stack is not None:
                is_page_active = self.isVisible() and self._stack.currentIndex() == 3

            if refresh or is_page_active:
                if is_page_active:
                    # 如果当前稍后阅读页面处于活动显示状态：
                    # 为了消灭对 Toast 弹窗的卡顿干扰，先让事件循环渲染 Toast 弹窗，
                    # 随后在事件循环尾部非阻塞地更新稍后阅读列表。
                    try:
                        QTimer.singleShot(15, lambda: self._refresh_later_read_list() if hasattr(self, "_later_read_list") else None)
                    except Exception:
                        pass
                else:
                    self._refresh_later_read_list()
                self._later_read_list_dirty = False
            else:
                self._later_read_list_dirty = True
        # 实时同步刷新置顶快捷浮窗中的稍后阅读内容
        compact_win = getattr(self, "_compact_window_later_read", None)
        if compact_win is not None:
            try:
                compact_win.load_data()
            except Exception:
                pass

    def _save_later_read_items(self, items: list[dict[str, Any]], *, refresh: bool = True) -> None:
        later_read = self._current_later_read_settings()
        later_read["items"] = list(items)
        self._save_later_read_settings(later_read, refresh=refresh)

    def _apply_later_read_enabled(self) -> None:
        later_read = self._current_later_read_settings()
        later_read["enabled"] = bool(self._later_read_enabled.isChecked())
        self._save_later_read_settings(later_read, refresh=False)
        self._refresh_tray_later_read_action()
        if not bool(later_read["enabled"]) and self._later_read_probe_overlay is not None:
            self._close_later_read_probe_overlay()

    def _start_later_read_capture_from_empty(self) -> None:
        if hasattr(self, "_later_read_enabled") and not self._later_read_enabled.isChecked():
            self._later_read_enabled.setChecked(True)
        if not self._later_read_enabled_now():
            later_read = self._current_later_read_settings()
            later_read["enabled"] = True
            self._save_later_read_settings(later_read, refresh=False)
            self._refresh_tray_later_read_action()
        self._start_later_read_probe_from_hotkey()

    def _clear_later_read_filters_from_empty(self) -> None:
        if hasattr(self, "_later_read_search"):
            self._later_read_search.clear()
        self._set_later_read_filter("time")

    def _later_read_enabled_now(self) -> bool:
        return bool(self._current_later_read_settings().get("enabled", False))

    def _later_read_filter_keywords(self) -> list[str]:
        settings = self._current_later_read_settings()
        raw = settings.get("filter_keywords", [])
        if not isinstance(raw, list):
            raw = []
        return [str(x).strip() for x in raw if str(x).strip()]

    def _later_read_filter_tooltip(self) -> str:
        return "过滤关键词"

    def _edit_later_read_filter_keywords(self) -> None:
        current = "，".join(self._later_read_filter_keywords())
        dialog = QDialog(self)
        dialog.setWindowTitle("过滤关键词")
        dialog.setModal(True)
        dialog.setMinimumWidth(440)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        tip = QLabel("包含关键词的标题或网址不再记录到稍后阅读，使用逗号分割关键词")
        tip.setWordWrap(True)
        root.addWidget(tip)
        editor = QTextEdit()
        editor.setAcceptRichText(False)
        editor.setPlainText(current)
        line_h = max(18, int(editor.fontMetrics().lineSpacing()))
        editor.setMinimumHeight(line_h * 10 + 18)
        editor.setPlaceholderText("纯水，人工智能，tag，快问快答")
        root.addWidget(editor)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("BtnSmallSecondary")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("BtnSmallPrimary")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(dialog.reject)
        save_btn.clicked.connect(dialog.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        later_read = self._current_later_read_settings()
        later_read["filter_keywords"] = str(editor.toPlainText() or "")
        self._save_later_read_settings(later_read, refresh=False)
        if hasattr(self, "_later_read_filter_btn"):
            self._later_read_filter_btn.setToolTip(self._later_read_filter_tooltip())

    def _later_read_should_skip_link(self, title: str, url: str) -> bool:
        haystack = f"{title}\n{url}".lower()
        for keyword in self._later_read_filter_keywords():
            if keyword.lower() in haystack:
                return True
        return False

    def _later_read_site_from_url(self, url: str) -> str:
        try:
            host = str(urlparse(str(url).strip()).netloc or "").strip().lower()
            return host[4:] if host.startswith("www.") else host
        except Exception:
            return ""

    def _later_read_now_iso(self) -> str:
        now = QDateTime.currentDateTime()
        return QDateTime(now.date(), QTime(now.time().hour(), now.time().minute())).toString(Qt.DateFormat.ISODate)

    def _later_read_time_value(self, item: dict[str, Any]) -> int:
        dt = QDateTime.fromString(str(item.get("created_at", "") or ""), Qt.DateFormat.ISODate)
        return int(dt.toSecsSinceEpoch()) if dt.isValid() else 0

    @staticmethod
    def _later_read_item_is_pinned(item: dict[str, Any]) -> bool:
        if not isinstance(item, dict):
            return False
        return bool(item.get("is_pinned", item.get("pinned", False)))

    def _sort_later_read_items_for_display(self, items: list[dict[str, Any]], mode: str = "time") -> list[dict[str, Any]]:
        mode = str(mode or "time")
        pinned = [item for item in items if self._later_read_item_is_pinned(item)]
        normal = [item for item in items if not self._later_read_item_is_pinned(item)]

        if mode == "site":
            def site_key(item: dict[str, Any]) -> tuple[str, str, int]:
                return (
                    str(item.get("site", "") or "").lower(),
                    str(item.get("url", "") or "").lower(),
                    -self._later_read_time_value(item),
                )

            pinned.sort(key=site_key)
            normal.sort(key=site_key)
        else:
            pinned.sort(key=self._later_read_time_value, reverse=True)
            normal.sort(key=self._later_read_time_value, reverse=True)
        return [*pinned, *normal]

    def _later_read_display_time(self, item: dict[str, Any]) -> str:
        text = str(item.get("created_at", "") or "").strip()
        dt = QDateTime.fromString(text, Qt.DateFormat.ISODate) if text else QDateTime()
        if dt.isValid():
            return dt.toString("MM/dd HH:mm")
        fallback = text.replace("-", "/").replace("T", " ")
        return fallback[5:16] if len(fallback) >= 16 else fallback

    def _show_later_read_sort_menu(self) -> None:
        keys = ["time", "site", "unread", "read"]
        active_index = 0
        current_filter = getattr(self, "_later_read_filter", "time")
        if current_filter in keys:
            active_index = keys.index(current_filter)

        items = [
            ("时间", lambda: self._set_later_read_filter("time"), True),
            ("网址", lambda: self._set_later_read_filter("site"), True),
            ("未读", lambda: self._set_later_read_filter("unread"), True),
            ("已读", lambda: self._set_later_read_filter("read"), True),
        ]

        from deepcat.ui.post_capture_actions import OcrGenericMenuPopup
        popup = OcrGenericMenuPopup(
            items,
            parent=self._later_read_sort_btn,
            active_index=active_index,
            match_parent_width=True,
            match_parent_width_exact=True,
            active_indicator="background",
        )

        global_pos = self._later_read_sort_btn.mapToGlobal(QPoint(0, self._later_read_sort_btn.height() + 2))
        popup.show_at_pos(global_pos)

    def _set_later_read_filter(self, mode: str) -> None:
        mode = str(mode or "time")
        self._later_read_filter = mode if mode in {"time", "site", "read", "unread"} else "time"
        labels = {"time": "时间", "site": "网址", "read": "已读", "unread": "未读"}
        if hasattr(self, "_later_read_sort_btn"):
            self._later_read_sort_btn.setText(labels.get(self._later_read_filter, "排序筛选"))
        self._refresh_later_read_list()

    def _filtered_later_read_items(self) -> list[dict[str, Any]]:
        items = self._current_later_read_items()
        query = ""
        if hasattr(self, "_later_read_search"):
            query = str(self._later_read_search.text() or "").strip().lower()
        if query:
            keywords = [k for k in query.split() if k]
            if keywords:
                filtered_items = []
                for item in items:
                    title = str(item.get("title", "")).lower()
                    url = str(item.get("url", "")).lower()
                    site = str(item.get("site", "")).lower()
                    if all(k in title or k in url or k in site for k in keywords):
                        filtered_items.append(item)
                items = filtered_items
        mode = str(getattr(self, "_later_read_filter", "time"))
        if mode == "read":
            items = [item for item in items if bool(item.get("read", False))]
        elif mode == "unread":
            items = [item for item in items if not bool(item.get("read", False))]
        return self._sort_later_read_items_for_display(items, mode)

    def _hide_later_read_tooltips(self) -> None:
        if not hasattr(self, "_later_read_list"):
            return
        try:
            for i in range(self._later_read_list.count()):
                row = self._later_read_list.item(i)
                widget = self._later_read_list.itemWidget(row)
                if isinstance(widget, _LaterReadItemWidget):
                    widget.hide_tooltip()
        except Exception:
            pass

    def _later_read_database_size(self) -> str:
        try:
            from deepcat.later_read_store import _get_db_path
            db_path = _get_db_path()
            size = 0
            for suffix in ("", "-wal", "-shm"):
                path = Path(f"{db_path}{suffix}")
                if path.exists():
                    size += path.stat().st_size
            size = max(0, int(size))
            if size < 1024:
                return f"{size} B"
            if size < 1024 * 1024:
                return f"{size / 1024:.1f} KB"
            return f"{size / (1024 * 1024):.1f} MB"
        except Exception:
            return "0 KB"

    def _update_later_read_stats(self, current_count: int, total_count: int) -> None:
        if hasattr(self, "_later_read_stats"):
            is_empty = int(current_count) == 0 and int(total_count) == 0
            size_str = "0 KB" if is_empty else self._later_read_database_size()
            self._later_read_stats.setText(f"{int(current_count)} / {int(total_count)} 条 | {size_str}")

    def _update_later_read_batch_selected_count(self) -> None:
        if not hasattr(self, "_later_read_batch_sel_count_label"):
            return
        selected_count = len(self._selected_later_read_ids())
        self._later_read_batch_sel_count_label.setText(f"{selected_count} 条")

    def _toggle_later_read_batch_mode(self, enabled: bool) -> None:
        self._later_read_batch_mode = bool(enabled)
        is_batch = bool(enabled)
        if hasattr(self, "_later_read_stats"):
            self._later_read_stats.setVisible(not is_batch)
        if hasattr(self, "_later_read_batch_sel_count_label"):
            self._later_read_batch_sel_count_label.setVisible(is_batch)
        if hasattr(self, "_later_read_batch_select_all"):
            self._later_read_batch_select_all.setVisible(is_batch)
            self._later_read_batch_select_all.setChecked(False)
        if hasattr(self, "_later_read_batch_delete"):
            self._later_read_batch_delete.setVisible(is_batch)
        if hasattr(self, "_later_read_batch_done"):
            self._later_read_batch_done.setVisible(is_batch)
        self._update_later_read_batch_selected_count()
        if hasattr(self, "_later_read_list"):
            self._later_read_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            self._refresh_later_read_list()

    def _later_read_batch_select_all_items(self, state: int) -> None:
        checked = bool(state)
        if not hasattr(self, "_later_read_list"):
            return
        for i in range(self._later_read_list.count()):
            row = self._later_read_list.item(i)
            widget = self._later_read_list.itemWidget(row)
            if isinstance(widget, _LaterReadItemWidget):
                widget.setChecked(checked)
        self._update_later_read_batch_selected_count()

    def _selected_later_read_ids(self) -> list[str]:
        selected_ids: list[str] = []
        if not hasattr(self, "_later_read_list"):
            return selected_ids
        for i in range(self._later_read_list.count()):
            row = self._later_read_list.item(i)
            widget = self._later_read_list.itemWidget(row)
            if isinstance(widget, _LaterReadItemWidget) and widget.isChecked():
                selected_ids.append(widget.item_id())
        return selected_ids

    def _toggle_later_read_pinned_fold(self) -> None:
        self._later_read_pinned_folded = not bool(getattr(self, "_later_read_pinned_folded", True))
        self._apply_later_read_pinned_fold_visibility()

    def _add_later_read_pinned_fold_toggle_item(self, total_count: int) -> None:
        if not hasattr(self, "_later_read_list"):
            return
        row = QListWidgetItem()
        row.setFlags(Qt.ItemFlag.NoItemFlags)
        row.setSizeHint(QSize(0, 30))
        self._later_read_list.addItem(row)
        self._later_read_pinned_toggle_item = row
        widget = _LaterReadPinnedFoldToggleWidget(
            total_count,
            bool(getattr(self, "_later_read_pinned_folded", True)),
            self._later_read_list,
        )
        widget.clicked.connect(self._toggle_later_read_pinned_fold)
        self._later_read_list.setItemWidget(row, widget)

    def _apply_later_read_pinned_fold_visibility(self) -> None:
        if not hasattr(self, "_later_read_list"):
            return
        pinned_count = 0
        for i in range(self._later_read_list.count()):
            row = self._later_read_list.item(i)
            widget = self._later_read_list.itemWidget(row)
            if isinstance(widget, _LaterReadItemWidget) and bool(getattr(widget, "_is_pinned", False)):
                pinned_count += 1
                continue
            break
        if pinned_count <= 3:
            for i in range(pinned_count):
                self._later_read_list.setRowHidden(i, False)
            return

        hide_excess = bool(getattr(self, "_later_read_pinned_folded", True))
        for i in range(3, pinned_count):
            self._later_read_list.setRowHidden(i, hide_excess)

        toggle_item = getattr(self, "_later_read_pinned_toggle_item", None)
        if toggle_item is not None:
            widget = self._later_read_list.itemWidget(toggle_item)
            if isinstance(widget, _LaterReadPinnedFoldToggleWidget):
                widget.update_state(pinned_count, hide_excess)

    def _refresh_later_read_list(self) -> None:
        if not hasattr(self, "_later_read_list"):
            return
        try:
            self._hide_later_read_tooltips()
            self._later_read_list.clear()
            self._later_read_pinned_toggle_item = None
            self._later_read_pinned_folded = True

            items = self._filtered_later_read_items()
            total_count = len(self._current_later_read_items())
            self._update_later_read_stats(len(items), total_count)

            if not items:
                query_text = str(self._later_read_search.text() or "").strip() if hasattr(self, "_later_read_search") else ""
                has_filter = bool(query_text) or str(getattr(self, "_later_read_filter", "time")) != "time"
                if total_count > 0 and has_filter:
                    title = "未找到匹配条目"
                    description = "换个关键词，或清空筛选。"
                    action_text = "清空筛选"
                    action_callback = self._clear_later_read_filters_from_empty
                else:
                    title = "还没有稍后阅读"
                    description = "采集网页链接后，会在这里集中整理和快速打开。"
                    action_text = ""
                    action_callback = None
                empty = QListWidgetItem()
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                empty.setSizeHint(QSize(0, 132))
                self._later_read_list.addItem(empty)
                self._later_read_list.setItemWidget(
                    empty,
                    self._make_unified_empty_state(
                        title,
                        description,
                        action_text,
                        action_callback,
                    ),
                )
                self._fit_later_read_list_to_content()
                self._later_read_items_cache = []
                self._later_read_offset = 0
                self._later_read_has_more = False
                return

            self._later_read_copy_icon = self._asset_icon("icon_action_copy.svg", self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
            self._later_read_pin_icon = self._asset_icon("icon_action_pin.svg")
            self._later_read_edit_icon = self._asset_icon("icon_todo_edit.svg")
            self._later_read_delete_icon = self._asset_icon("icon_todo_delete.svg", self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))

            self._later_read_items_cache = list(items)
            self._later_read_offset = 0
            self._later_read_has_more = True
            self._later_read_loading = False
            self._later_read_pinned_count = sum(1 for item in items if self._later_read_item_is_pinned(item))

            self._load_more_later_read_items()
        except Exception:
            get_logger().exception("刷新稍后阅读列表失败")

    def _on_later_read_search_text_changed(self, text: str) -> None:
        if not hasattr(self, "_later_read_debounce_timer"):
            self._refresh_later_read_list()
            return
        self._later_read_debounce_timer.stop()
        if not text.strip():
            self._refresh_later_read_list()
        else:
            self._later_read_debounce_timer.start(300)

    def _on_later_read_scroll(self, value: int) -> None:
        self._hide_later_read_tooltips()
        if not getattr(self, "_later_read_has_more", False) or getattr(self, "_later_read_loading", False):
            return
        scrollbar = self._later_read_list.verticalScrollBar()
        if scrollbar.maximum() > 0 and value >= scrollbar.maximum() - 4:
            self._load_more_later_read_items()

    def _load_more_later_read_items(self) -> None:
        if not hasattr(self, "_later_read_list"):
            return
        if not getattr(self, "_later_read_has_more", False) or getattr(self, "_later_read_loading", False):
            return
        self._later_read_loading = True
        try:
            offset = self._later_read_offset
            limit = 50
            items_cache = getattr(self, "_later_read_items_cache", [])
            page_items = items_cache[offset:offset + limit]

            if not page_items:
                self._later_read_has_more = False
                return

            copy_icon = getattr(self, "_later_read_copy_icon", None)
            pin_icon = getattr(self, "_later_read_pin_icon", None)
            edit_icon = getattr(self, "_later_read_edit_icon", None)
            delete_icon = getattr(self, "_later_read_delete_icon", None)
            batch_mode = bool(getattr(self, "_later_read_batch_mode", False))
            pinned_count = getattr(self, "_later_read_pinned_count", 0)

            for index_in_page, item in enumerate(page_items):
                global_index = offset + index_in_page
                if global_index == pinned_count and pinned_count > 3:
                    self._add_later_read_pinned_fold_toggle_item(pinned_count)
                row = QListWidgetItem()
                row.setData(Qt.ItemDataRole.UserRole, str(item.get("id", "")))
                row.setSizeHint(QSize(0, 34))
                self._later_read_list.addItem(row)
                widget = _LaterReadItemWidget(
                    item,
                    time_text=self._later_read_display_time(item),
                    copy_icon=copy_icon,
                    pin_icon=pin_icon,
                    edit_icon=edit_icon,
                    delete_icon=delete_icon,
                    batch_mode=batch_mode,
                )
                widget.setProperty("last", global_index == len(items_cache) - 1)
                widget._position_separator()
                widget.openRequested.connect(self._open_later_read_item)
                widget.copyRequested.connect(self._copy_later_read_item)
                widget.pinRequested.connect(self._set_later_read_item_pinned)
                widget.editRequested.connect(self._edit_later_read_item)
                widget.deleteRequested.connect(self._delete_later_read_item)
                widget.selectionChanged.connect(lambda *_: self._update_later_read_batch_selected_count())
                self._later_read_list.setItemWidget(row, widget)

            self._later_read_offset += len(page_items)
            if self._later_read_offset >= len(items_cache):
                self._later_read_has_more = False

            if pinned_count == len(items_cache) and pinned_count > 3:
                self._add_later_read_pinned_fold_toggle_item(pinned_count)
            self._apply_later_read_pinned_fold_visibility()
            self._fit_later_read_list_to_content()
        except Exception:
            get_logger().exception("加载更多稍后阅读列表失败")
        finally:
            self._later_read_loading = False

    def _fit_later_read_list_to_content(self) -> None:
        if hasattr(self, "_later_read_list"):
            self._later_read_list.setMaximumHeight(16777215)

    def _open_later_read_item(self, item_id: str) -> None:
        item_id = str(item_id or "")
        items = self._current_later_read_items()
        changed = False
        opened = False
        for item in items:
            if str(item.get("id", "")) != item_id:
                continue
            url = str(item.get("url", "") or "").strip()
            if url:
                try:
                    QDesktopServices.openUrl(QUrl(url))
                    opened = True
                except Exception:
                    self._show_later_read_status("打开链接失败。", tone="error", auto_hide_ms=5200)
            if not bool(item.get("read", False)):
                item["read"] = True
                changed = True
            break
        if changed:
            self._save_later_read_items(items)
            self._show_later_read_status("已标记为已读。", tone="success")
        elif opened:
            self._show_later_read_status("已打开链接。", tone="success")

    def _copy_later_read_item(self, item_id: str) -> None:
        item = next((x for x in self._current_later_read_items() if str(x.get("id", "")) == str(item_id or "")), None)
        if item is None:
            return
        title = str(item.get("title", "") or "").strip()
        url = str(item.get("url", "") or "").strip()
        text = url if not title or title == url else f"{title}\n{url}"
        try:
            QApplication.clipboard().setText(text)
        except Exception as exc:
            self._show_later_read_status(f"复制链接失败：{exc}", tone="error", auto_hide_ms=5200)
            return
        self._show_later_read_status("链接已复制。", tone="success", auto_hide_ms=1500)

    def _set_later_read_item_pinned(self, item_id: str, is_pinned: bool) -> None:
        item_id = str(item_id or "")
        if not item_id:
            return
        items = self._current_later_read_items()
        changed = False
        for item in items:
            if str(item.get("id", "")) != item_id:
                continue
            item["is_pinned"] = bool(is_pinned)
            item.pop("pinned", None)
            changed = True
            break
        if not changed:
            return
        self._save_later_read_items(items)
        self._show_later_read_status("已置顶该稍后阅读。" if is_pinned else "已取消置顶该稍后阅读。", tone="success")

    def _edit_later_read_item(self, item_id: str) -> None:
        item_id = str(item_id or "")
        if not item_id:
            return
        items = self._current_later_read_items()
        item = next((x for x in items if str(x.get("id", "")) == item_id), None)
        if item is None:
            return
        dialog = _LaterReadEditDialog(item, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        title = dialog.title()
        url = dialog.url()
        if not url.startswith(("http://", "https://")):
            self._show_later_read_status("编辑失败：仅支持 http:// 或 https:// 链接。", tone="error", auto_hide_ms=5200)
            return
        item["url"] = url[:2000]
        item["title"] = (title or url)[:240]
        item["site"] = self._later_read_site_from_url(url)
        self._save_later_read_items(items)
        self._show_later_read_status("已更新稍后阅读。", tone="success")

    def _delete_later_read_item(self, item_id: str) -> None:
        item_id = str(item_id or "")
        if not item_id:
            return
        before_items = self._current_later_read_items()
        self._save_later_read_items([x for x in before_items if str(x.get("id", "")) != item_id])
        self._show_later_read_status("已删除 1 条稍后阅读。", tone="success")

    def _batch_delete_later_read(self) -> None:
        if not hasattr(self, "_later_read_list"):
            return
        selected_ids = self._selected_later_read_ids()
        if not selected_ids:
            self._show_later_read_status("请先选择要删除的稍后阅读。", tone="warning")
            return
        from deepcat.ui.main_window.compact import StyledMessageBox
        box = StyledMessageBox(self)
        box.setWindowTitle("批量删除")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"确定删除 {len(selected_ids)} 条稍后阅读吗？")
        no_btn = box.addButton("否", QMessageBox.ButtonRole.NoRole)
        yes_btn = box.addButton("是", QMessageBox.ButtonRole.YesRole)
        box.setDefaultButton(no_btn)
        box.exec()
        if box.clickedButton() != yes_btn:
            return
        ids = set(selected_ids)
        self._save_later_read_items([x for x in self._current_later_read_items() if str(x.get("id", "")) not in ids])
        self._toggle_later_read_batch_mode(False)
        self._show_later_read_status(f"已删除 {len(ids)} 条稍后阅读。", tone="success")

    def _add_later_read_link(self, title: str, url: str) -> None:
        self._add_later_read_link_at(title, url, toast_pos=None)

    def _add_later_read_link_at(self, title: str, url: str, *, toast_pos: Optional[QPoint] = None) -> None:
        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")):
            self._show_later_read_capture_status(
                "链接解析失败：仅支持 http:// 或 https:// 链接。",
                tone="error",
                auto_hide_ms=5200,
                toast_pos=toast_pos,
            )
            return
        title = str(title or "").strip() or url
        if self._later_read_should_skip_link(title, url):
            self._show_later_read_capture_status("该链接已被过滤规则忽略。", tone="info", toast_pos=toast_pos)
            return
        now_iso = self._later_read_now_iso()
        today_key = now_iso[:10]
        before_items = self._current_later_read_items()
        for item in before_items:
            if str(item.get("url", "")).strip() == url and str(item.get("created_at", ""))[:10] == today_key:
                self._show_later_read_capture_status("今日已保存过该链接。", tone="info", toast_pos=toast_pos)
                return
        item_id = f"read-{int(time.time() * 1000)}"
        new_item = {
            "id": item_id,
            "title": title[:240],
            "url": url[:2000],
            "site": self._later_read_site_from_url(url),
            "created_at": now_iso,
            "read": False,
        }
        after_items = [dict(x) for x in before_items]
        after_items.insert(0, new_item)
        self._later_read_undo_snapshots[item_id] = [dict(x) for x in before_items]
        self._save_later_read_items(after_items, refresh=False)
        message = title if len(title) <= 80 else f"{title[:77]}..."
        self._show_later_read_capture_status(f"保存成功：{message}", tone="success", toast_pos=toast_pos)

    def _undo_later_read_add(self, item_id: str) -> None:
        snapshot = self._later_read_undo_snapshots.pop(str(item_id or ""), None)
        if snapshot is None:
            return
        self._last_later_read_capture_key = ""
        self._save_later_read_items(snapshot, refresh=False)
        self._show_later_read_status("已撤销保存链接。", tone="success")

    def _search_later_read(self, keyword: str) -> None:
        layout = self._search_card_later.items_layout
        self._clear_layout(layout)

        matched_later = []
        items = self._sort_later_read_items_for_display(self._current_later_read_items(), "time")
        for item in items:
            title = str(item.get("title", "") or "")
            url = str(item.get("url", "") or "")
            if keyword.lower() in title.lower() or keyword.lower() in url.lower():
                matched_later.append(item)

        if not matched_later:
            self._search_card_later.view_all_btn.setVisible(False)
            self._add_search_empty_state(
                layout,
                "没有匹配的稍后阅读",
                "换个关键词，或清空搜索。",
            )
            return

        self._search_card_later.view_all_btn.setVisible(True)
        for item in matched_later[:8]:
            item_widget = self._create_later_search_item_widget(item)
            layout.addWidget(item_widget)
