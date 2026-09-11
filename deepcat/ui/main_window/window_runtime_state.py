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


class WindowRuntimeStateMixin:
    def _replace_current_ui(self, ui: dict) -> None:
        # 整体替换 ui 字典（仅限调用方刚从最新设置构建 ui 的场景；新代码请改用 update_ui_settings 增量写入）
        incoming = dict(ui)

        def _mut(s: AppSettings) -> AppSettings:
            return AppSettings(
                version=int(s.version),
                autostart=bool(s.autostart),
                auto_save=bool(getattr(s, "auto_save", True)),
                image_output_dir=str(s.image_output_dir),
                pdf_output_dir=str(s.pdf_output_dir),
                hotkey=str(s.hotkey),
                ui=incoming,
                notifications_enabled=bool(getattr(s, "notifications_enabled", False)),
            )

        self._current = update_settings(_mut)
        self._sync_settings_after_change()

    def _is_widget_inside(self, widget: Optional[QWidget], ancestor: Optional[QWidget]) -> bool:
        while widget is not None and ancestor is not None:
            if widget is ancestor:
                return True
            widget = widget.parentWidget()
        return False

    def _batch_select_all(self, state: int) -> None:
        checked = bool(state)
        for i in range(self._todo_list.count()):
            row = self._todo_list.item(i)
            widget = self._todo_list.itemWidget(row)
            if isinstance(widget, _TodoListItemWidget):
                widget.setChecked(checked)

    def _batch_delete(self) -> None:
        selected_ids: list[str] = []
        for i in range(self._todo_list.count()):
            row = self._todo_list.item(i)
            widget = self._todo_list.itemWidget(row)
            if isinstance(widget, _TodoListItemWidget) and widget.isChecked():
                selected_ids.append(widget.item_id())
        if not selected_ids:
            self._show_todo_status("请先选择要删除的待办。", tone="warning", auto_hide_ms=2400)
            return
        box = QMessageBox(self._todo_dialog_parent())
        box.setWindowTitle("批量删除")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"确定删除选中的 {len(selected_ids)} 项待办吗？")
        no_btn = box.addButton("否", QMessageBox.ButtonRole.NoRole)
        yes_btn = box.addButton("是", QMessageBox.ButtonRole.YesRole)
        box.setDefaultButton(no_btn)
        box.exec()
        if box.clickedButton() != yes_btn:
            return
        ids = set(selected_ids)
        if self._save_todo_items([x for x in self._current_todo_items() if str(x.get("id", "")) not in ids]):
            self._show_todo_status(f"已删除 {len(ids)} 项待办。", tone="success", auto_hide_ms=2200)

    def _batch_strike(self) -> None:
        selected_ids: list[str] = []
        for i in range(self._todo_list.count()):
            row = self._todo_list.item(i)
            widget = self._todo_list.itemWidget(row)
            if isinstance(widget, _TodoListItemWidget) and widget.isChecked():
                selected_ids.append(widget.item_id())
        if not selected_ids:
            self._show_todo_status("请先选择要划线的待办。", tone="warning", auto_hide_ms=2400)
            return
        items = self._current_todo_items()
        ids = set(selected_ids)
        changed = False
        for item in items:
            if str(item.get("id", "")) in ids:
                item["struck_off"] = True
                changed = True
        if changed:
            if self._save_todo_items(items):
                self._show_todo_status(f"已划线 {len(ids)} 项待办。", tone="success", auto_hide_ms=2200)

    def _persist_translator_settings(self) -> None:
        SettingsDialog._persist_translator_settings(self)
        self._sync_settings_after_change()

    def _apply_ui_state_from_settings(self) -> None:
        ui = dict(getattr(self._app_settings, "ui", {}) or {})
        self._current = self._app_settings
        self._scroll_hotkey_str = str(ui.get("scroll_hotkey", getattr(self, "_scroll_hotkey_str", "<ctrl>+<f1>")) or "<ctrl>+<f1>")
        self._ui_restoring = True
        try:
            g = str(ui.get("window_geometry_b64", "") or "")
            restored_window_geometry = False
            if g:
                try:
                    restored_window_geometry = bool(self.restoreGeometry(QByteArray(decode_qbytearray(g))))
                except Exception:
                    pass
            if restored_window_geometry:
                try:
                    last_page_index = int(ui.get("last_page_index", -1))
                    if (
                        last_page_index in self._REMEMBERED_RESIZABLE_PAGE_INDICES
                        and last_page_index not in self._remembered_resizable_page_sizes
                    ):
                        size = self._coerce_remembered_resizable_page_size(
                            {"width": self.width(), "height": self.height()}
                        )
                        if size is not None:
                            self._set_remembered_resizable_page_size(size)
                except Exception:
                    pass
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMaximized)
            self.setFixedSize(self._DEFAULT_WINDOW_WIDTH, self._DEFAULT_WINDOW_HEIGHT)
            self.resize(self._DEFAULT_WINDOW_WIDTH, self._DEFAULT_WINDOW_HEIGHT)

            try:
                mode = str(ui.get("mode", "") or "")
                if mode:
                    if mode in {"自动", "自动滚动"}:
                        mode = "滚动截图"
                    elif mode in {"手动", "手动滚动", "区域", "区域选取"}:
                        mode = "框选截图"
                    elif mode == "全屏":
                        mode = "全屏截图"
                    elif mode == "滚动截屏":
                        mode = "滚动截图"
                    elif mode == "框选截屏":
                        mode = "框选截图"
                    self._mode.setCurrentText(mode)
            except Exception:
                pass
            try:
                if self._save_mode is not None:
                    sm = str(ui.get("save_mode", "") or "")
                    if sm:
                        self._save_mode.setCurrentText("手动保存" if sm.startswith("手动") else "自动保存")
                    else:
                        self._save_mode.setCurrentText("自动保存" if bool(getattr(self._app_settings, "auto_save", False)) else "手动保存")
            except Exception:
                pass
            try:
                if self._adaptive_wait is not None:
                    self._adaptive_wait.setChecked(bool(ui.get("adaptive_wait", self._adaptive_wait.isChecked())))
            except Exception:
                pass
            try:
                if self._reverse_scroll is not None:
                    self._reverse_scroll.setChecked(bool(ui.get("reverse_scroll", self._reverse_scroll.isChecked())))
            except Exception:
                pass
            try:
                if self._copy_on_capture is not None:
                    mode = normalize_copy_on_capture_mode(
                        ui.get("copy_on_capture_mode"),
                        ui.get("copy_on_capture") if "copy_on_capture" in ui else None,
                        DEFAULT_COPY_ON_CAPTURE_MODE,
                    )
                    self._set_copy_on_capture_combo_mode(mode)
            except Exception:
                pass
            try:
                if self._boost_scroll is not None:
                    self._boost_scroll.setChecked(bool(ui.get("boost_scroll", self._boost_scroll.isChecked())))
            except Exception:
                pass
            try:
                if self._speed is not None:
                    self._speed.setValue(int(ui.get("speed", self._speed.value())))
            except Exception:
                pass

            try:
                fmt = str(ui.get("output_format", "") or "").lower()
                if fmt == "jpg":
                    self._fmt_jpg.setChecked(True)
                elif fmt == "pdf":
                    self._fmt_pdf.setChecked(True)
                else:
                    self._fmt_png.setChecked(True)
            except Exception:
                pass

            try:
                if self._merge_pdf is not None:
                    self._merge_pdf.setChecked(bool(ui.get("merge_pdf", self._merge_pdf.isChecked())))
            except Exception:
                pass
            try:
                if self._merge_image is not None:
                    self._merge_image.setChecked(bool(ui.get("merge_image", self._merge_image.isChecked())))
            except Exception:
                pass
            try:
                if self._dual_output is not None:
                    self._dual_output.setChecked(bool(ui.get("dual_output", self._dual_output.isChecked())))
            except Exception:
                pass

            try:
                if self._cdp_mode is not None:
                    self._cdp_mode.setChecked(bool(ui.get("cdp_enabled", self._cdp_mode.isChecked())))
            except Exception:
                pass
            try:
                if self._cdp_port is not None:
                    self._cdp_port.setText(str(int(ui.get("cdp_port", int(self._cdp_port.text().strip() or 9888)))))
            except Exception:
                pass
        except Exception:
            pass
        try:
            if "last_page_index" in ui:
                self._switch_page(self._normal_persisted_page_index(int(ui["last_page_index"])))
            if "last_table_notes_mode" in ui and getattr(self, "_table_notes_stack", None) is not None:
                self._switch_table_notes_mode(str(ui["last_table_notes_mode"]), verify_password=False)
        except Exception:
            pass
        finally:
            self._ui_restoring = False
        try:
            self._on_speed_changed(self._speed.value())
        except Exception:
            pass
        try:
            self._update_output_options()
        except Exception:
            pass

    def _attach_ui_persistence(self) -> None:
        def save_now() -> None:
            if bool(self._ui_restoring):
                return
            self._persist_ui_state()

        def schedule_save() -> None:
            if bool(self._ui_restoring):
                return
            QTimer.singleShot(0, save_now)

        if self._mode is not None:
            self._mode.currentIndexChanged.connect(lambda *_: schedule_save())
        if self._save_mode is not None:
            self._save_mode.currentIndexChanged.connect(lambda *_: schedule_save())
        if self._adaptive_wait is not None:
            self._adaptive_wait.toggled.connect(lambda *_: schedule_save())
        if self._reverse_scroll is not None:
            self._reverse_scroll.toggled.connect(lambda *_: schedule_save())
        if self._copy_on_capture is not None:
            self._copy_on_capture.currentIndexChanged.connect(lambda *_: schedule_save())
        if self._boost_scroll is not None:
            self._boost_scroll.toggled.connect(lambda *_: schedule_save())
        if self._speed is not None:
            self._speed.valueChanged.connect(lambda *_: schedule_save())
        if self._fmt_png is not None:
            self._fmt_png.toggled.connect(lambda checked: checked and schedule_save())
        if self._fmt_jpg is not None:
            self._fmt_jpg.toggled.connect(lambda checked: checked and schedule_save())
        if self._fmt_pdf is not None:
            self._fmt_pdf.toggled.connect(lambda checked: checked and schedule_save())
        if self._merge_pdf is not None:
            self._merge_pdf.toggled.connect(lambda *_: schedule_save())
        if self._merge_image is not None:
            self._merge_image.toggled.connect(lambda *_: schedule_save())
        if self._dual_output is not None:
            self._dual_output.toggled.connect(lambda *_: schedule_save())
        if self._cdp_mode is not None:
            self._cdp_mode.stateChanged.connect(lambda *_: schedule_save())
        if self._cdp_port is not None:
            self._cdp_port.editingFinished.connect(schedule_save)

        if getattr(self, "_stack", None) is not None:
            self._stack.currentChanged.connect(lambda *_: schedule_save())
        if getattr(self, "_table_notes_stack", None) is not None:
            self._table_notes_stack.currentChanged.connect(lambda *_: schedule_save())

        self._ui_save_timer = QTimer(self)
        self._ui_save_timer.setSingleShot(True)
        self._ui_save_timer.timeout.connect(save_now)

    def _persist_ui_state(self) -> None:
        self._remember_current_resizable_page_size()
        entries: dict[str, Any] = {}
        entries["mode"] = str(self._mode.currentText())
        entries["save_mode"] = str(self._save_mode.currentText()) if self._save_mode is not None else ""
        entries["adaptive_wait"] = bool(self._adaptive_wait.isChecked()) if self._adaptive_wait is not None else False
        entries["reverse_scroll"] = bool(self._reverse_scroll.isChecked()) if self._reverse_scroll is not None else False
        copy_mode = self._current_copy_on_capture_mode()
        entries["copy_on_capture_mode"] = copy_mode
        entries["copy_on_capture"] = copy_mode != COPY_ON_CAPTURE_MODE_OFF
        entries["boost_scroll"] = bool(self._boost_scroll.isChecked()) if self._boost_scroll is not None else False
        entries["speed"] = int(self._speed.value()) if self._speed is not None else 0
        entries["output_format"] = str(self._current_format())
        entries["merge_pdf"] = bool(self._merge_pdf.isChecked()) if self._merge_pdf is not None else False
        entries["merge_image"] = bool(self._merge_image.isChecked()) if self._merge_image is not None else False
        entries["dual_output"] = bool(self._dual_output.isChecked()) if self._dual_output is not None else False
        entries["cdp_enabled"] = bool(self._cdp_mode.isChecked()) if self._cdp_mode is not None else False
        if self._cdp_port is not None:
            try:
                entries["cdp_port"] = int(self._cdp_port.text().strip())
            except Exception:
                pass
        try:
            if self._stack is not None:
                entries["last_page_index"] = self._normal_persisted_page_index(int(self._stack.currentIndex()))
        except Exception:
            pass
        try:
            if getattr(self, "_table_notes_stack", None) is not None:
                entries["last_table_notes_mode"] = "note" if self._table_notes_stack.currentIndex() == 1 else "table"
        except Exception:
            pass
        try:
            entries["resizable_page_sizes"] = self._remembered_resizable_page_sizes_payload()
        except Exception:
            pass
        try:
            entries["window_geometry_b64"] = encode_qbytearray(bytes(self.saveGeometry()))
        except Exception:
            pass
        try:
            entries["window_state"] = {"maximized": bool(self.windowState() & Qt.WindowState.WindowMaximized)}
        except Exception:
            pass
        auto_save_override: Optional[bool] = None
        try:
            if self._save_mode is not None:
                auto_save_override = str(self._save_mode.currentText()) == "自动保存"
        except Exception:
            pass

        def _mut(s: AppSettings) -> AppSettings:
            return AppSettings(
                version=int(s.version),
                autostart=bool(s.autostart),
                auto_save=bool(auto_save_override if auto_save_override is not None else getattr(s, "auto_save", True)),
                image_output_dir=str(s.image_output_dir),
                pdf_output_dir=str(s.pdf_output_dir),
                hotkey=str(s.hotkey),
                ui={**dict(s.ui), **entries},
                notifications_enabled=bool(getattr(s, "notifications_enabled", False)),
            )

        try:
            s = update_settings(_mut)
        except Exception:
            return
        self._app_settings = s
        self._current = s

    def _update_output_options(self) -> None:
        if self._merge_pdf is None or self._merge_image is None or self._dual_output is None:
            return
        if bool(getattr(self, "_updating_output_options", False)):
            return
        self._updating_output_options = True
        try:
            is_scroll = self._mode.currentText() == "滚动截图"
            if self._cdp_mode is not None and self._cdp_mode.isChecked():
                self._merge_pdf.setEnabled(False)
                self._merge_image.setEnabled(False)
                self._dual_output.setEnabled(False)
                return

            is_pdf = bool(self._fmt_pdf.isChecked())
            is_img = bool(self._fmt_png.isChecked() or self._fmt_jpg.isChecked())
            self._dual_output.setEnabled(is_scroll)

            pdf_available = is_scroll and (bool(is_pdf) or (bool(is_img) and bool(self._dual_output.isChecked())))
            img_available = is_scroll and (bool(is_img) or (bool(is_pdf) and bool(self._dual_output.isChecked())))

            self._merge_pdf.setEnabled(bool(pdf_available))
            self._merge_image.setEnabled(bool(img_available))

            if not bool(pdf_available):
                self._merge_pdf.setChecked(False)
            if not bool(img_available):
                self._merge_image.setEnabled(False)
            else:
                self._merge_image.setEnabled(True)
        finally:
            self._updating_output_options = False

    def _on_mode_changed(self) -> None:
        if self._mode.currentText() in {"滚动截屏", "框选截屏", "滚动截图", "框选截图"}:
            self._capture_region = None
            self._capture_region_logical = None
            if self._border_overlay is not None:
                self._border_overlay.close()
                self._border_overlay = None
            self._close_selection_shade_overlay()
            self._set_selection_translate_suspended(False)
            return

        self._capture_region = None
        self._capture_region_logical = None
        if self._border_overlay is not None:
            self._border_overlay.close()
            self._border_overlay = None
        self._close_selection_shade_overlay()
        return

    def _current_annotation_style(self) -> dict:
        return normalize_annotation_style((getattr(self._app_settings, "ui", {}) or {}).get("annotation_style"))

    def _on_speed_changed(self, value: int) -> None:
        v = max(0, min(100, int(value)))
        delay = 0.03 if v == 95 else (0.18 - (v / 100.0) * 0.16)
        self._cfg.SCROLL_DELAY = float(delay)
        label = "慢" if delay >= 0.14 else ("中" if delay >= 0.08 else ("快" if delay >= 0.04 else "极快"))
        if getattr(self, "_speed_label", None) is not None:
            self._speed_label.setText(f"滚动速度：{label} ({delay:.2f}s)")

    def _current_format(self) -> str:
        if self._fmt_jpg.isChecked():
            return "jpg"
        if self._fmt_pdf.isChecked():
            return "pdf"
        return "png"

    @staticmethod
    def _device_pixel_ratio_from_sizes(px_w: Any, px_h: Any, logical_w: Any, logical_h: Any) -> float:
        try:
            physical_w = float(px_w)
            physical_h = float(px_h)
            logical_w = float(logical_w)
            logical_h = float(logical_h)
        except Exception:
            return 0.0
        ratios: list[float] = []
        if physical_w > 0 and logical_w > 0:
            ratios.append(physical_w / logical_w)
        if physical_h > 0 and logical_h > 0:
            ratios.append(physical_h / logical_h)
        if not ratios:
            return 0.0
        dpr = ratios[0]
        if len(ratios) > 1 and abs(ratios[0] - ratios[1]) <= 0.05:
            dpr = sum(ratios) / len(ratios)
        if 0.25 <= dpr <= 8.0:
            return float(dpr)
        return 0.0

    @staticmethod
    def _rect_tuple_from_qrect_like(rect: Any) -> Optional[tuple[int, int, int, int]]:
        if rect is None:
            return None
        try:
            return (int(rect.left()), int(rect.top()), int(rect.width()), int(rect.height()))
        except Exception:
            return None

    def _post_actions_has_manual_pin(self) -> bool:
        post_actions = getattr(self, "_post_actions", None)
        if post_actions is None:
            return False
        try:
            return getattr(post_actions, "_last_pinned", None) is not None
        except RuntimeError:
            return False
        except Exception:
            return False

    def _restore_normal_cursor(self) -> None:
        try:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        except Exception:
            pass
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.ArrowCursor)
            QApplication.restoreOverrideCursor()
        except Exception:
            pass

    def _cursor_refresh_blocked(self) -> bool:
        """截图或链接探测层接管光标时，不清理其合法的应用级覆盖。"""
        for attr_name in ("_region_overlay", "_later_read_probe_overlay"):
            overlay = getattr(self, attr_name, None)
            if overlay is None:
                continue
            try:
                if not overlay.isVisible() or bool(getattr(overlay, "_overlay_closed", False)):
                    continue
                default_enabled = bool(
                    getattr(overlay, "_link_probe_only", False)
                    or not bool(getattr(overlay, "_confirmed", False))
                )
                if bool(getattr(overlay, "_cursor_updates_enabled", default_enabled)):
                    return True
            except RuntimeError:
                continue
            except Exception:
                continue
        return False

    def _restore_and_refresh_hover_cursor(self) -> None:
        try:
            if not self.isVisible() or self.isMinimized() or self._cursor_refresh_blocked():
                return
        except RuntimeError:
            return
        except Exception:
            return
        self._restore_normal_cursor()
        self._refresh_hover_cursor()

    def _schedule_hover_cursor_refresh(self) -> None:
        """等待窗口/页面完成原生显示后，分两次刷新静止鼠标下方的光标。"""
        try:
            QTimer.singleShot(0, self._restore_and_refresh_hover_cursor)
            QTimer.singleShot(80, self._restore_and_refresh_hover_cursor)
        except Exception:
            pass

    def _reapply_hovered_widget_cursor(self, global_pos: QPoint) -> bool:
        """重放鼠标下方控件的有效光标，同时保持控件原有的光标属性状态。"""
        try:
            target = QApplication.widgetAt(global_pos)
            if target is None:
                return False
            if target is not self and not self.isAncestorOf(target):
                return False

            had_explicit_cursor = target.testAttribute(Qt.WidgetAttribute.WA_SetCursor)
            effective_cursor = QCursor(target.cursor())
            if had_explicit_cursor:
                target.unsetCursor()
                target.setCursor(effective_cursor)
            else:
                target.setCursor(effective_cursor)
                target.unsetCursor()
            return True
        except RuntimeError:
            return False
        except Exception:
            return False

    def _refresh_hover_cursor(self) -> None:
        """窗口弹出在静止的鼠标下方时，Windows 不会重发 WM_SETCURSOR，
        会残留旧控件的光标形状。优先重放鼠标下方控件的有效光标；找不到
        Qt 控件时再原地触发鼠标事件作为兜底。"""
        try:
            pos = QCursor.pos()
            if self.isVisible() and self.frameGeometry().contains(pos):
                if not self._reapply_hovered_widget_cursor(pos):
                    QCursor.setPos(pos)
        except Exception:
            pass

    def _save_button_mode_value(self) -> str:
        ui = dict(getattr(self._app_settings, "ui", {}) or {})
        mode = str(ui.get("save_button_mode", "auto")).strip().lower()
        if mode in {"manual", "manual_save", "手动", "手动保存", "手动保存（自己选路径）"}:
            return "manual"
        return "auto"

    def _save_button_auto_enabled(self) -> bool:
        return self._save_button_mode_value() == "auto"

    def _fullscreen_logical_rect(self) -> QRect:
        screen = QGuiApplication.primaryScreen()
        geo = screen.geometry() if screen is not None else QRect(0, 0, 800, 600)
        return QRect(int(geo.x()), int(geo.y()), int(geo.width()), int(geo.height()))

    def _request_stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._worker is not None:
            self._worker.request_stop()

    def _cleanup_thread(self, close_border_overlay: bool = True) -> None:
        self._stop_right_click_cancel_listener()
        self._stop_left_click_retake_listener()
        self._thread = None
        self._worker = None
        self._stop_event = None
        self._capture_manual_mode = False
        self._capture_fullscreen_mode = False
        if self._floating is not None:
            self._floating.close()
            self._floating = None
        if self._post_actions is not None:
            try:
                self._post_actions.close()
            except Exception:
                pass
            self._post_actions = None
        if bool(close_border_overlay):
            if self._border_overlay is not None:
                self._border_overlay.close()
                self._border_overlay = None
            self._close_selection_shade_overlay()

    def _on_status(self, text: str) -> None:
        self._task_feedback.status("capture", str(text))
        if self._floating is not None:
            self._floating.set_status(str(text))

    def _on_attention(self, text: str) -> None:
        try:
            self._show_styled_message_box(QMessageBox.Icon.Information, "提示", str(text))
        except Exception:
            pass
        if self._worker is not None:
            try:
                self._worker.resume()
            except Exception:
                pass

    def _message_box_style(self) -> str:
        return """
            QMessageBox {
                background: #ffffff;
                font-family: "Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 13px;
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

    def _show_styled_message_box(
        self,
        icon: QMessageBox.Icon,
        title: str,
        text: str,
        informative: str = "",
        detailed: str = "",
    ) -> None:
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle(str(title))
        box.setText(str(text))
        if informative:
            box.setInformativeText(str(informative))
        if detailed:
            box.setDetailedText(str(detailed))
        box.setStyleSheet(self._message_box_style())
        box.exec()

    def _on_progress(self, n: int) -> None:
        self._task_feedback.progress("capture", int(n), 0, "frame captured")
        return

    def _on_metrics(self, frame_index: int, captured_height: int, elapsed_seconds: int) -> None:
        self._last_frame_index = int(frame_index)
        self._task_feedback.progress(
            "capture",
            int(frame_index),
            0,
            f"captured_height={int(captured_height)}, elapsed={int(elapsed_seconds)}",
        )
        if self._floating is not None:
            self._floating.set_metrics(int(frame_index), int(captured_height), int(elapsed_seconds))

    @staticmethod
    def _feedback_text(text: str) -> str:
        value = str(text or "").strip()
        value = value.replace("剪切板", "剪贴板")
        if not value:
            return value
        if "\n" in value or value.endswith(("。", "！", "？", ".", "!", "?", "…", "）", ")")):
            return value
        if value.startswith(("http://", "https://")) or re.search(r"^[A-Za-z]:[\\/]", value):
            return value
        return f"{value}。"

    @staticmethod
    def _feedback_title(text: str) -> str:
        return str(text or "").strip().replace("剪切板", "剪贴板")

    def _normalize_toast_feedback(
        self,
        title: str,
        message: str,
        *,
        open_dir: Optional[str] = None,
    ) -> tuple[str, str, int]:
        raw_title = str(title or "").strip()
        raw_message = str(message or "").strip()
        title_text = self._feedback_title(raw_title)
        message_text = self._feedback_text(raw_message)
        title_key = raw_title.lower()
        message_key = raw_message.lower()
        combined = f"{raw_title}\n{raw_message}"

        if raw_title in {"鎻愮ず"}:
            title_text = "提示"

        copied = (
            raw_title in {"Copied"}
            or "copied to clipboard" in message_key
            or "已复制" in raw_title
            or "复制成功" in raw_title
            or "剪贴板" in raw_title
            or "剪切板" in raw_title
        )
        saved = (
            "已保存" in raw_title
            or "保存成功" in raw_title
            or "已保存到" in raw_message
            or "录制已保存" in raw_message
        )
        config_written = "配置" in combined and ("已写入" in combined or "写入成功" in combined)
        failed = (
            "失败" in raw_title
            or "错误" in raw_title
            or raw_title in {"错误"}
            or "failed" in title_key
            or any(word in combined for word in ("失败", "错误", "无法", "异常", "未连通", "不可用"))
        )

        if copied and not failed:
            title_text = "复制成功"
            if "链接" in combined:
                message_text = "链接已复制到剪贴板。"
            elif "截图" in combined or "capture" in message_key:
                message_text = "截图已复制到剪贴板。"
            elif "识别" in combined:
                message_text = "识别结果已复制到剪贴板。"
            elif "记录" in combined:
                message_text = "记录内容已复制到剪贴板。"
            else:
                message_text = "内容已复制到剪贴板。"
        elif saved and not failed:
            title_text = "录制保存成功" if "录制" in combined else "保存成功"
            if not message_text:
                message_text = "内容已保存。"
        elif config_written and not failed:
            title_text = "配置写入成功"
            if not message_text:
                message_text = "配置已写入。"
        elif failed:
            if "节点探测" in raw_title:
                title_text = "节点探测失败"
            elif "测试" in combined or "连通" in combined:
                title_text = "测试失败"
            elif not any(word in raw_title for word in ("失败", "错误")):
                title_text = "操作失败"

        if failed:
            duration = self._TOAST_ERROR_DURATION_MS
        elif saved or open_dir:
            duration = self._TOAST_SAVE_DURATION_MS
        else:
            duration = self._TOAST_INFO_DURATION_MS
        return title_text, message_text, duration

    def _show_notification_failure_status(self, text: str) -> None:
        message = str(text or "").strip()
        if not message:
            return
        try:
            page = getattr(self, "_todo_page", None)
            if isinstance(page, QWidget) and page.isVisible():
                self._show_todo_status(message, tone="error", auto_hide_ms=5200)
                return
        except Exception:
            pass
        self._show_capture_status(message, tone="error", auto_hide_ms=5200)
