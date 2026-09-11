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
from deepcat.core.scroll_capture_status import ScrollCaptureOutcome
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



class CaptureMixin:
    def _cancel_active_region_overlay(self) -> bool:
        overlay = self._region_overlay
        if overlay is None:
            return False
        self._cancelled_region_capture_token = int(self._region_capture_token)
        self._region_capture_shell_pending = False
        self._capture_transition_pending = False
        try:
            if bool(getattr(overlay, "_confirmed", False)):
                return False
        except RuntimeError:
            self._region_overlay = None
            return False
        except Exception:
            pass
        try:
            overlay.canceled.emit()
        except Exception:
            pass
        try:
            overlay.close()
        except Exception:
            pass
        self._region_overlay = None
        self._capture_region = None
        self._capture_region_logical = None
        self._capture_frozen_screen_bgr = None
        self._capture_frozen_screen_origin_px = (0, 0)
        self._stop_right_click_cancel_listener()
        self._stop_left_click_retake_listener()
        self._set_selection_translate_suspended(False)
        self._finalize_stitch_buffer()
        self._restore_normal_cursor()
        return True

    def _cancel_current_region_capture(self) -> bool:
        active = bool(
            self._region_capture_shell_pending
            or self._capture_region is not None
            or self._capture_region_logical is not None
            or self._border_overlay is not None
            or self._region_overlay is not None
        )
        if not bool(active):
            return False

        self._cancelled_region_capture_token = int(self._region_capture_token)
        self._region_capture_shell_pending = False
        self._capture_transition_pending = False

        post_actions = self._post_actions
        if post_actions is not None:
            try:
                post_actions.close_all()
            except Exception:
                try:
                    post_actions.close()
                except Exception:
                    pass
            self._post_actions = None

        for attr in ("_border_overlay", "_region_overlay"):
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            try:
                widget.close()
            except Exception:
                pass
            setattr(self, attr, None)
        self._close_selection_shade_overlay()

        self._capture_region = None
        self._capture_region_logical = None
        self._capture_frozen_screen_bgr = None
        self._capture_frozen_screen_origin_px = (0, 0)
        self._stop_right_click_cancel_listener()
        self._stop_left_click_retake_listener()
        self._set_selection_translate_suspended(False)
        self._finalize_stitch_buffer()
        self._restore_normal_cursor()
        return True

    def _on_border_escape_pressed(self) -> None:
        self._handle_escape_close_targets()

    def _connect_border_escape(self) -> None:
        if self._border_overlay is None:
            return
        try:
            self._border_overlay.escape_pressed.disconnect()
        except Exception:
            pass
        try:
            self._border_overlay.escape_pressed.connect(self._on_border_escape_pressed)
        except Exception:
            pass

    def _close_selection_shade_overlay(self) -> None:
        shade = getattr(self, "_selection_shade_overlay", None)
        self._selection_shade_overlay = None
        self._selection_shade_bound_border = None
        if shade is None:
            return
        try:
            shade.close()
        except Exception:
            pass

    def _selection_rect_from_border(self) -> Optional[QRect]:
        border = self._border_overlay
        if border is None:
            return None
        try:
            if hasattr(border, "selection_rect"):
                return QRect(border.selection_rect())
        except Exception:
            pass
        try:
            return QRect(border.geometry())
        except Exception:
            return None

    def _bind_selection_shade_to_border(self, border) -> None:
        if self._selection_shade_bound_border is border:
            return
        self._selection_shade_bound_border = border

        def on_border_destroyed(*_, owner=self, border_ref=border) -> None:
            if getattr(owner, "_selection_shade_bound_border", None) is border_ref:
                owner._close_selection_shade_overlay()

        def on_border_rect_changed(rect_obj, owner=self, border_ref=border) -> None:
            if getattr(owner, "_border_overlay", None) is not border_ref:
                return
            try:
                owner._sync_selection_shade_for_border(QRect(rect_obj))
            except Exception:
                pass

        try:
            border.destroyed.connect(on_border_destroyed)
        except Exception:
            pass
        try:
            border.rect_changed.connect(on_border_rect_changed)
        except Exception:
            pass
        try:
            border.rect_released.connect(on_border_rect_changed)
        except Exception:
            pass

    def _sync_selection_shade_for_border(self, rect: Optional[QRect] = None) -> None:
        border = self._border_overlay
        if border is None:
            self._close_selection_shade_overlay()
            return
        shade_rect = QRect(rect) if rect is not None else self._selection_rect_from_border()
        if shade_rect is None or shade_rect.isEmpty():
            self._close_selection_shade_overlay()
            return
        region_overlay = getattr(self, "_region_overlay", None)
        if region_overlay is not None:
            try:
                transition_pending = bool(getattr(self, "_capture_transition_pending", False))
                region_overlay.set_selection_rect(shade_rect)
                region_overlay.set_selection_visual_visible(transition_pending)
                if region_overlay.isHidden():
                    region_overlay.show()
            except Exception:
                pass
            if getattr(self, "_selection_shade_overlay", None) is not None:
                self._close_selection_shade_overlay()
            self._bind_selection_shade_to_border(border)
            try:
                if border.isHidden():
                    border.show()
                border.raise_()
                if self._post_actions is not None:
                    if self._post_actions.isHidden():
                        self._post_actions.show()
                    self._post_actions.raise_()
            except Exception:
                pass
            return

        shade = self._selection_shade_overlay
        is_new_shade = False
        if shade is None:
            shade = SelectionShadeOverlay(shade_rect)
            self._selection_shade_overlay = shade
            shade._on_first_paint = self._on_shade_first_paint
            shade.destroyed.connect(lambda *_, owner=self: setattr(owner, "_selection_shade_overlay", None))
            is_new_shade = True
        else:
            shade.set_selection_rect(shade_rect)
        self._bind_selection_shade_to_border(border)
        try:
            if is_new_shade or not shade.isVisible():
                shade.show()
                shade.raise_()
                border.raise_()
                if self._post_actions is not None:
                    self._post_actions.raise_()
            else:
                if border.isHidden():
                    border.show()
                border.raise_()
                if self._post_actions is not None:
                    if self._post_actions.isHidden():
                        self._post_actions.show()
                    self._post_actions.raise_()
        except Exception:
            pass

    def _set_selection_shade_visible(self, visible: bool) -> None:
        shade = getattr(self, "_selection_shade_overlay", None)
        if shade is None:
            if bool(visible):
                self._sync_selection_shade_for_border()
            return
        try:
            shade.setVisible(bool(visible))
            if bool(visible):
                self._sync_selection_shade_for_border()
        except Exception:
            pass

    def _post_capture_toolbar_size_hint(self) -> QSize:
        for widget in (getattr(self, "_post_actions", None), getattr(self, "_post_capture_ui_warm_widget", None)):
            if widget is None:
                continue
            try:
                hint = widget.sizeHint()
                if int(hint.width()) > 0 and int(hint.height()) > 0:
                    return QSize(hint)
            except RuntimeError:
                continue
            except Exception:
                continue
        # 预热控件尚未准备好时，用当前布局的保守高度预判工具栏是否会上翻。
        return QSize(820 if self._post_capture_button_style() == "text" else 620, 60)

    def _post_capture_toolbar_above_region(self, rect: QRect) -> bool:
        region = QRect(rect)
        try:
            screen = QGuiApplication.screenAt(region.center()) or QGuiApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else QRect(0, 0, 800, 600)
            toolbar_geo = PostCaptureActions.toolbar_geometry_for_region(region, self._post_capture_toolbar_size_hint(), geo)
            return int(toolbar_geo.top()) < int(region.top())
        except Exception:
            return False

    def _sync_border_dimension_tip_for_toolbar(self, rect: Optional[QRect] = None) -> None:
        border = getattr(self, "_border_overlay", None)
        if border is None:
            return
        try:
            region = QRect(rect) if rect is not None else self._selection_rect_from_border()
            if region is None or region.isEmpty():
                return
            post_actions = getattr(self, "_post_actions", None)
            if post_actions is not None and post_actions.isVisible():
                toolbar_above = int(post_actions.geometry().top()) < int(region.top())
            else:
                toolbar_above = self._post_capture_toolbar_above_region(region)
            border.set_dimension_tip_inside(bool(toolbar_above))
        except Exception:
            pass

    def _hide_region_overlay_visual_if_current(self, overlay: Optional[QWidget]) -> None:
        if overlay is None:
            return
        if getattr(self, "_region_overlay", None) is not overlay:
            return
        self._hide_overlay_visual_safe(overlay)

    def _schedule_region_overlay_visual_hide(self, overlay: Optional[QWidget], delay_ms: int = 32) -> None:
        if overlay is None:
            return
        try:
            QTimer.singleShot(
                int(max(0, delay_ms)),
                lambda overlay_ref=overlay: self._hide_region_overlay_visual_if_current(overlay_ref),
            )
        except Exception:
            self._hide_region_overlay_visual_if_current(overlay)

    def _on_shade_first_paint(self) -> None:
        overlay = self._region_overlay
        if overlay is not None:
            self._schedule_region_overlay_visual_hide(overlay, 120)

    def _prepare_selection_shade_handoff(self, overlay: Optional[QWidget]) -> None:
        if getattr(self, "_border_overlay", None) is None:
            return
        try:
            self._sync_selection_shade_for_border()
        except Exception:
            pass
        shade = getattr(self, "_selection_shade_overlay", None)
        if shade is not None:
            try:
                shade._on_first_paint = self._on_shade_first_paint
            except Exception:
                pass
        for widget in (
            shade,
            getattr(self, "_border_overlay", None),
        ):
            if widget is None:
                continue
            try:
                widget.repaint()
            except Exception:
                pass
        if overlay is not None and getattr(self, "_region_overlay", None) is not overlay:
            self._schedule_region_overlay_visual_hide(overlay, 120)
        flush = getattr(self, "_flush_fast_ui", None)
        if callable(flush):
            try:
                flush(1)
            except Exception:
                pass

    def _release_capture_transition_overlay(self) -> None:
        self._capture_transition_pending = False
        overlay = self._region_overlay
        if overlay is None:
            if getattr(self, "_border_overlay", None) is not None:
                self._sync_selection_shade_for_border()
            return
        try:
            if not bool(getattr(overlay, "_confirmed", False)):
                return
        except RuntimeError:
            self._region_overlay = None
            return
        except Exception:
            pass
        if getattr(self, "_border_overlay", None) is not None:
            try:
                overlay.set_selection_visual_visible(False)
            except Exception:
                pass
            self._sync_selection_shade_for_border()
            return
        self._prepare_selection_shade_handoff(overlay)
        try:
            overlay.close()
        except Exception:
            pass
        if self._region_overlay is overlay:
            self._region_overlay = None
        if getattr(self, "_border_overlay", None) is not None:
            self._sync_selection_shade_for_border()

    def _arm_capture_transition_release(self) -> None:
        if not bool(getattr(self, "_capture_transition_pending", False)):
            return
        post_actions = self._post_actions
        if post_actions is None:
            QTimer.singleShot(0, self._release_capture_transition_overlay)
            return
        try:
            if bool(getattr(post_actions, "_first_frame_ready_emitted", False)):
                QTimer.singleShot(0, self._release_capture_transition_overlay)
                return
        except Exception:
            pass
        try:
            post_actions.first_frame_ready.connect(self._release_capture_transition_overlay)
        except Exception:
            QTimer.singleShot(0, self._release_capture_transition_overlay)
        QTimer.singleShot(160, self._release_capture_transition_overlay)

    def _scroll_hotkey_triggered(self) -> None:
        self._scroll_capture_requested.emit(True)

    def _start_region_capture_from_hotkey(self, from_tray: bool = True) -> None:
        if self._thread is not None:
            return
        self._start_capture_clicked(from_tray=bool(from_tray), mode_override="框选截图")

    def _start_scroll_capture_from_hotkey(self, from_tray: bool = True) -> None:
        if self._thread is not None:
            return
        self._start_capture_clicked(from_tray=bool(from_tray), mode_override="滚动截图")

    def _start_capture_via_hotkey(self) -> None:
        if self._thread is not None:
            return
        self._start_region_capture_from_hotkey(True)

    @staticmethod
    def _warm_capture_backend_modules() -> None:
        import mss  # noqa: F401
        import numpy  # noqa: F401
        from deepcat.core import capturer  # noqa: F401

    def _start_capture_warmup(self) -> None:
        if self._capture_busy_for_warmup():
            try:
                QTimer.singleShot(2500, self._start_capture_warmup)
            except Exception:
                pass
            return
        if bool(getattr(self, "_capture_tools_warmup_done", False)) or bool(getattr(self, "_capture_tools_warmup_running", False)):
            return

        def warm() -> None:
            try:
                self._warm_capture_backend_modules()
            except Exception:
                pass
            finally:
                self._capture_tools_warmup_done = True
                self._capture_tools_warmup_running = False

        try:
            self._capture_tools_warmup_running = True
            t = threading.Thread(target=warm, daemon=True)
            self._capture_tools_warmup_thread = t
            t.start()
        except Exception:
            self._capture_tools_warmup_running = False
            pass

    def _capture_busy_for_warmup(self) -> bool:
        return bool(
            self._thread is not None
            or self._region_overlay is not None
            or self._border_overlay is not None
            or self._post_actions is not None
            or bool(getattr(self, "_region_capture_shell_pending", False))
            or bool(getattr(self, "_capture_transition_pending", False))
        )

    def _flush_hide_for_capture(self) -> None:
        try:
            flags = (
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                | QEventLoop.ProcessEventsFlag.ExcludeSocketNotifiers
            )
            QApplication.processEvents(flags, 2)
        except Exception:
            try:
                QApplication.processEvents()
            except Exception:
                pass

    def _hide_for_capture_without_animation(self) -> None:
        try:
            if self.isMinimized():
                self._flush_hide_for_capture()
                return
        except Exception:
            pass
        try:
            self.showMinimized()
        except Exception:
            return
        self._flush_hide_for_capture()

    def _hide_settings_for_capture(self) -> None:
        dlg = self._settings_dialog
        if dlg is None:
            return
        try:
            if not dlg.isVisible():
                return
            try:
                focus = dlg.focusWidget()
                if focus is not None:
                    focus.clearFocus()
            except Exception:
                pass
            dlg.hide()
            self._flush_hide_for_capture()
        except RuntimeError:
            self._settings_dialog = None
        except Exception:
            pass

    def _resume_selection_translate_if_capture_idle(self) -> None:
        if self._thread is not None:
            return
        if self._region_overlay is not None or self._post_actions is not None or self._border_overlay is not None:
            return
        self._set_selection_translate_suspended(False)

    def _send_capture_to_ai_dialog(self, image_bgr) -> bool:
        if image_bgr is None:
            return False
        try:
            self._open_manual_ai_panel()
            panel = self._selection_translate_panel
            if panel is None:
                return False
            try:
                _ = panel.width()
            except (RuntimeError, AttributeError):
                self._selection_translate_panel = None
                return False

            self._activate_existing_ai_panel(panel)
            if not hasattr(panel, "submit_image_attachment_for_qa"):
                return False
            return bool(panel.submit_image_attachment_for_qa(image_bgr))
        except Exception:
            get_logger().exception("发送截图到 AI 对话窗口失败")
            return False

    def _cancel_post_capture_actions_prewarm(self, *, close_widget: bool = False) -> None:
        self._post_capture_ui_prewarm_generation = int(getattr(self, "_post_capture_ui_prewarm_generation", 0) or 0) + 1
        if not bool(close_widget):
            return
        widget = getattr(self, "_post_capture_ui_warm_widget", None)
        self._post_capture_ui_warm_widget = None
        self._post_capture_ui_prewarmed = False
        if widget is None:
            return
        try:
            widget.close()
        except Exception:
            pass

    def _schedule_post_capture_actions_prewarm(self, delay_ms: int = 80) -> None:
        if self._post_actions is not None or self._post_capture_ui_warm_widget is not None:
            return
        self._post_capture_ui_prewarm_generation = int(getattr(self, "_post_capture_ui_prewarm_generation", 0) or 0) + 1
        generation = int(self._post_capture_ui_prewarm_generation)
        QTimer.singleShot(
            max(1, int(delay_ms)),
            lambda generation=generation: self._prewarm_post_capture_actions_ui(generation),
        )

    def _prewarm_post_capture_actions_ui(self, generation: Optional[int] = None) -> None:
        if generation is not None and int(generation) != int(getattr(self, "_post_capture_ui_prewarm_generation", 0) or 0):
            return
        if bool(self._post_capture_ui_prewarmed):
            return
        if (
            self._post_actions is not None
            or self._post_capture_ui_warm_widget is not None
            or self._thread is not None
            or self._region_overlay is not None
            or self._border_overlay is not None
            or bool(getattr(self, "_region_capture_shell_pending", False))
        ):
            return
        self._post_capture_ui_prewarmed = True
        try:
            import numpy as np
            from deepcat.ui.post_capture_actions import PostCaptureActions

            img = np.zeros((1, 1, 3), dtype=np.uint8)
            w = PostCaptureActions(
                region_rect=QRect(0, 0, 120, 80),
                image_bgr=img,
                default_dir=str(get_image_output_dir()),
                default_format=str(self._current_format()),
                jpg_quality=int(self._cfg.JPG_QUALITY),
                capture_frames=1,
                region_px=(0, 0, 120, 80),
                on_close=lambda: None,
                on_toast=None,
                auto_show=False,
                button_style=self._post_capture_button_style(),
            )
            w.ensurePolished()
            try:
                int(w.winId())
            except Exception:
                pass
            self._post_capture_ui_warm_widget = w
        except Exception:
            self._post_capture_ui_prewarmed = False
            self._post_capture_ui_warm_widget = None
            pass

    def _start_left_click_retake_listener(self) -> None:
        self._stop_left_click_retake_listener()
        if self._post_actions is None and self._border_overlay is None:
            return
        if bool(self._continuous_retake_suppressed):
            return
        if self._post_actions is not None and bool(getattr(self._post_actions, "_recording", False)):
            return
        self._left_click_retake_enabled_at = float(time.time()) + 0.08
        try:
            from pynput import mouse

            def _on_click(x, y, button, pressed) -> bool:
                try:
                    if not bool(pressed) or button != mouse.Button.left:
                        return True
                    self._retake_capture_requested.emit(float(x), float(y))
                except Exception:
                    return True
                return True

            listener = mouse.Listener(on_click=_on_click)
            listener.start()
            self._left_click_retake_listener = listener
        except Exception:
            self._left_click_retake_listener = None
            self._left_click_retake_enabled_at = 0.0

    def _stop_left_click_retake_listener(self) -> None:
        try:
            if self._left_click_retake_listener is not None:
                self._left_click_retake_listener.stop()
                self._left_click_retake_listener.join(timeout=0.2)
        except Exception:
            pass
        self._left_click_retake_listener = None
        self._left_click_retake_enabled_at = 0.0

    def _post_capture_hit_widgets(self) -> list[QWidget]:
        widgets: list[QWidget] = []
        for widget in (self._post_actions, self._border_overlay, self._selection_translate_panel):
            if widget is not None:
                widgets.append(widget)
        post_actions = self._post_actions
        if post_actions is not None:
            try:
                overlay = getattr(post_actions, "_annotation_overlay", None)
                if overlay is not None:
                    widgets.append(overlay)
            except Exception:
                pass
            try:
                live_panel = getattr(post_actions, "_live_ocr_panel", None)
                if callable(live_panel):
                    panel = live_panel()
                    if panel is not None:
                        widgets.append(panel)
            except RuntimeError:
                pass
            except Exception:
                pass
        return widgets

    def _point_hits_post_capture_ui(self, point: QPoint) -> bool:
        widgets = self._post_capture_hit_widgets()
        for widget in widgets:
            try:
                if not widget.isVisible():
                    continue
                geo = QRect(widget.frameGeometry())
                geo.adjust(-4, -4, 4, 4)
                if geo.contains(point):
                    return True
            except RuntimeError:
                continue
            except Exception:
                continue
        try:
            hit = QApplication.widgetAt(point)
        except Exception:
            hit = None
        while hit is not None:
            for widget in widgets:
                try:
                    if hit is widget or widget.isAncestorOf(hit) or hit.window() is widget.window():
                        return True
                except RuntimeError:
                    continue
                except Exception:
                    continue
            hit = hit.parentWidget()
        return False

    def _post_capture_click_points(self, x: float, y: float) -> list[QPoint]:
        points: list[QPoint] = [QPoint(int(round(x)), int(round(y)))]
        try:
            for screen in QGuiApplication.screens():
                dpr = float(screen.devicePixelRatio() if screen is not None else 1.0)
                if dpr <= 0.01:
                    continue
                p = QPoint(int(round(float(x) / dpr)), int(round(float(y) / dpr)))
                if p not in points:
                    points.append(p)
        except Exception:
            pass
        return points

    def _retake_capture_via_left_click_at(self, x: float, y: float) -> None:
        if self._left_click_retake_listener is None:
            return
        if float(time.time()) < float(self._left_click_retake_enabled_at):
            return
        if self._thread is not None or bool(self._region_capture_shell_pending):
            return
        if self._post_actions is None and self._border_overlay is None:
            self._stop_left_click_retake_listener()
            return
        if bool(self._continuous_retake_suppressed) or (self._post_actions is not None and bool(getattr(self._post_actions, "_recording", False))):
            self._stop_left_click_retake_listener()
            return
        for point in self._post_capture_click_points(float(x), float(y)):
            if self._point_hits_post_capture_ui(point):
                return
        self._stop_left_click_retake_listener()
        self._start_capture_clicked(from_tray=True, mode_override="框选截图")

    def _finish_capture_via_left_click_if_recent(self) -> None:
        if self._post_actions is not None:
            return
        if float(time.time()) > float(self._left_click_finish_deadline):
            return
        self._left_click_finish_deadline = 0.0
        if self._thread is not None:
            self._request_stop()

    def _cancel_capture_via_escape_key(self) -> None:
        try:
            if self._thread is not None:
                self._request_stop()
                self._restore_normal_cursor()
                return

            if self._region_overlay is not None:
                try:
                    active_selecting = not bool(getattr(self._region_overlay, "_confirmed", False))
                except RuntimeError:
                    self._region_overlay = None
                    active_selecting = False
                except Exception:
                    active_selecting = False
                if bool(active_selecting) and self._cancel_active_region_overlay():
                    return
            if self._handle_escape_close_targets():
                return
        except Exception:
            get_logger().exception("Esc 取消截图失败")

    def _request_cancel_capture(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._worker is not None:
            try:
                self._worker.request_cancel()
            except Exception:
                self._worker.request_stop()

    def _cancel_capture_via_right_click(self) -> None:
        if self._is_ocr_panel_active():
            return
        if self._post_actions is not None:
            self._finalize_or_clear_stitch_buffer_for_post_close()
            try:
                self._post_actions.close_all()
            except Exception:
                try:
                    self._post_actions.close()
                except Exception:
                    pass
            self._post_actions = None
            self._stop_right_click_cancel_listener()
            self._stop_left_click_retake_listener()
            self._restore_normal_cursor()
            return
        if self._thread is not None:
            self._request_cancel_capture()
            self._restore_normal_cursor()
            return
        self._capture_region = None
        self._capture_region_logical = None
        if self._region_overlay is not None:
            try:
                self._region_overlay.close()
            except Exception:
                pass
            self._region_overlay = None
        if self._border_overlay is not None:
            try:
                self._border_overlay.close()
            except Exception:
                pass
            self._border_overlay = None
        self._close_selection_shade_overlay()
        self._stop_right_click_cancel_listener()
        self._stop_left_click_retake_listener()
        self._finalize_stitch_buffer()
        self._restore_normal_cursor()

    def _stabilize_capture_page_layout(self) -> None:
        try:
            if self._stack is None or self._stack.count() <= 0:
                return
            page = self._stack.widget(0)
            if page is None:
                return
            for widget in (
                page,
                getattr(self, "_capture_top_controls", None),
                getattr(self, "_capture_settings_group", None),
            ):
                if isinstance(widget, QWidget):
                    widget.ensurePolished()
                    widget.updateGeometry()
                    if widget.layout() is not None:
                        widget.layout().activate()
            for area in page.findChildren(QScrollArea):
                if area.objectName() != "PageScroll":
                    continue
                area.ensurePolished()
                area.updateGeometry()
                content = area.widget()
                if content is not None:
                    content.ensurePolished()
                    content.updateGeometry()
                    if content.layout() is not None:
                        content.layout().activate()
                area.verticalScrollBar().setValue(0)
                area.horizontalScrollBar().setValue(0)
                area.viewport().update()
                break
            if page.layout() is not None:
                page.layout().activate()
            page.updateGeometry()
            page.update()
        except Exception:
            pass

    def _show_capture_status(self, text: str, *, tone: str = "info", auto_hide_ms: int = 3600) -> None:
        self._show_inline_status(getattr(self, "_capture_status_label", None), text, tone=tone, auto_hide_ms=auto_hide_ms)

    def _restore_capture_settings_defaults(self) -> None:
        try:
            autostart = getattr(self, "_autostart", None)
            if autostart is not None:
                autostart.setChecked(False)

            notifications = getattr(self, "_notifications", None)
            if notifications is not None:
                notifications.setChecked(True)

            feature_todo_switch = getattr(self, "_feature_todo_switch", None)
            if feature_todo_switch is not None:
                feature_todo_switch.setChecked(True)

            feature_later_read_switch = getattr(self, "_feature_later_read_switch", None)
            if feature_later_read_switch is not None:
                feature_later_read_switch.setChecked(True)

            feature_clipboard_history_switch = getattr(self, "_feature_clipboard_history_switch", None)
            if feature_clipboard_history_switch is not None:
                feature_clipboard_history_switch.setChecked(True)

            feature_table_notes_switch = getattr(self, "_feature_table_notes_switch", None)
            if feature_table_notes_switch is not None:
                feature_table_notes_switch.setChecked(True)

            auto_snap_enabled = getattr(self, "_auto_snap_enabled", None)
            if auto_snap_enabled is not None:
                auto_snap_enabled.setChecked(True)

            mode = getattr(self, "_mode", None)
            if mode is not None:
                mode.setCurrentText("框选截图")

            save_mode = getattr(self, "_save_mode", None)
            if save_mode is not None:
                save_mode.setCurrentText("手动保存")

            previous_capture_action_combo = getattr(self, "_previous_capture_action_combo", None)
            if previous_capture_action_combo is not None:
                previous_capture_action_combo.setCurrentText("置顶前图")

            adaptive_wait = getattr(self, "_adaptive_wait", None)
            if adaptive_wait is not None:
                adaptive_wait.setChecked(False)

            reverse_scroll = getattr(self, "_reverse_scroll", None)
            if reverse_scroll is not None:
                reverse_scroll.setChecked(False)

            copy_on_capture = getattr(self, "_copy_on_capture", None)
            if copy_on_capture is not None:
                self._set_copy_on_capture_combo_mode(DEFAULT_COPY_ON_CAPTURE_MODE)

            boost_scroll = getattr(self, "_boost_scroll", None)
            if boost_scroll is not None:
                boost_scroll.setChecked(True)

            speed = getattr(self, "_speed", None)
            if speed is not None:
                speed.setValue(95)

            fmt_png = getattr(self, "_fmt_png", None)
            if fmt_png is not None:
                fmt_png.setChecked(True)

            merge_pdf = getattr(self, "_merge_pdf", None)
            if merge_pdf is not None:
                merge_pdf.setChecked(True)

            merge_image = getattr(self, "_merge_image", None)
            if merge_image is not None:
                merge_image.setChecked(True)

            dual_output = getattr(self, "_dual_output", None)
            if dual_output is not None:
                dual_output.setChecked(False)

            line_style = getattr(self, "_line_style", None)
            if line_style is not None:
                line_style.setCurrentIndex(1)

            post_capture_button_style_combo = getattr(self, "_post_capture_button_style_combo", None)
            if post_capture_button_style_combo is not None:
                post_capture_button_style_combo.setCurrentText("图标按钮")

            save_button_mode = getattr(self, "_save_button_mode", None)
            if save_button_mode is not None:
                save_button_mode.setCurrentText("自动保存")

            self._annotation_style = dict(normalize_annotation_style({}))
            self._apply_annotation_style()

            cdp_mode = getattr(self, "_cdp_mode", None)
            if cdp_mode is not None:
                cdp_mode.setChecked(False)

            cdp_port = getattr(self, "_cdp_port", None)
            if cdp_port is not None:
                cdp_port.setText("9888")

            hotkey = getattr(self, "_hotkey", None)
            if hotkey is not None:
                hotkey.setKeySequence(QKeySequence(pynput_to_qt("<f1>")))
                hotkey.keySequenceChanged.emit(hotkey.keySequence())

            scroll_hotkey_edit = getattr(self, "_scroll_hotkey_edit", None)
            if scroll_hotkey_edit is not None:
                scroll_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt("<f2>")))
                scroll_hotkey_edit.keySequenceChanged.emit(scroll_hotkey_edit.keySequence())

            later_read_hotkey_edit = getattr(self, "_later_read_hotkey_edit", None)
            if later_read_hotkey_edit is not None:
                later_read_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt("<f3>")))
                later_read_hotkey_edit.keySequenceChanged.emit(later_read_hotkey_edit.keySequence())

            todo_hotkey_edit = getattr(self, "_todo_hotkey_edit", None)
            if todo_hotkey_edit is not None:
                todo_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt("<alt>+n")))
                todo_hotkey_edit.keySequenceChanged.emit(todo_hotkey_edit.keySequence())

            ai_qa_hotkey_edit = getattr(self, "_ai_qa_hotkey_edit", None)
            if ai_qa_hotkey_edit is not None:
                ai_qa_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt("<alt>+<space>")))
                ai_qa_hotkey_edit.keySequenceChanged.emit(ai_qa_hotkey_edit.keySequence())

            selection_translate_hotkey_edit = getattr(self, "_selection_translate_hotkey_edit", None)
            if selection_translate_hotkey_edit is not None:
                selection_translate_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt("<ctrl>+<space>")))
                selection_translate_hotkey_edit.keySequenceChanged.emit(selection_translate_hotkey_edit.keySequence())

            selection_popup_hotkey_edit = getattr(self, "_selection_popup_hotkey_edit", None)
            if selection_popup_hotkey_edit is not None:
                selection_popup_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt("<ctrl>+b")))
                selection_popup_hotkey_edit.keySequenceChanged.emit(selection_popup_hotkey_edit.keySequence())

            self._show_capture_status("已恢复截图设置页的全部默认设置。", tone="success")
        except Exception as e:
            get_logger().exception("恢复默认设置时出错")
            self._show_capture_status(f"恢复默认设置失败：{e}", tone="error", auto_hide_ms=5200)

    def _build_capture_page(self) -> QWidget:
        page = self._make_page("截图设置")
        content_layout: QVBoxLayout = page._content_layout  # type: ignore[attr-defined]
        content_layout.setSpacing(14)
        self._capture_status_label = self._make_inline_status_label()
        content_layout.addWidget(self._capture_status_label)

        self._auto_snap_enabled = QCheckBox("自动吸附")
        self._auto_snap_enabled.setChecked(bool(dict(getattr(self._app_settings, "ui", {}) or {}).get("auto_snap_enabled", True)))

        general, layout = self._card("截图设置")
        self._capture_settings_group = general
        general.layout().setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)
        top_controls = QWidget()
        top_controls.setObjectName("CaptureTopControls")
        top_controls.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        top_controls.setMinimumHeight(78)
        top_grid = QGridLayout()
        top_controls.setLayout(top_grid)
        top_grid.setContentsMargins(0, 0, 0, 0)
        top_grid.setHorizontalSpacing(8)
        top_grid.setVerticalSpacing(14)
        self._mode = _MouseClickOnlyComboBox()
        self._mode.addItems(["滚动截图", "框选截图"])
        self._mode.setCurrentText("框选截图")
        self._mode.setStyle(_ModernComboStyle(self._mode))
        self._mode.currentIndexChanged.connect(self._on_mode_changed)
        self._save_mode = _MouseClickOnlyComboBox()
        self._save_mode.addItems(["自动保存", "手动保存"])
        self._save_mode.setCurrentText("自动保存" if bool(getattr(self._app_settings, "auto_save", False)) else "手动保存")
        self._save_mode.setStyle(_ModernComboStyle(self._save_mode))
        self._previous_capture_action_combo = ModernPopupComboBox()
        self._previous_capture_action_combo.addItems(["置顶前图", "保存前图", "拼接前图", "暂存前图"])
        ui = dict(getattr(self._app_settings, "ui", {}) or {})
        previous_action = str(ui.get("previous_capture_action", "pin")).strip().lower()
        if previous_action == "save":
            pa_text = "保存前图"
        elif previous_action == "pin":
            pa_text = "置顶前图"
        elif previous_action == "stitch":
            pa_text = "拼接前图"
        elif previous_action == "stash":
            pa_text = "暂存前图"
        else:
            pa_text = "置顶前图"
        self._previous_capture_action_combo.setCurrentText(pa_text)
        for combo in (self._mode, self._save_mode, self._previous_capture_action_combo):
            combo.setMinimumWidth(96)
            combo.setFixedHeight(32)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        mode_label = QLabel("截图模式:")
        mode_label.setObjectName("CaptureModeLabel")
        mode_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        mode_label.setFixedWidth(56)
        mode_label.setMinimumHeight(32)
        mode_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        save_label = QLabel("保存模式:")
        save_label.setObjectName("CaptureSaveModeLabel")
        save_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._continuous_capture_label = QLabel("连续截图模式:")
        self._continuous_capture_label.setObjectName("CaptureContinuousModeLabel")
        self._continuous_capture_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._continuous_capture_label.setToolTip("框选后点击鼠标确认截图，再次点击鼠标连续截图。")
        self._previous_capture_action_combo.setToolTip("框选后点击鼠标确认截图，再次点击鼠标连续截图。")
        for label, width in (
            (save_label, 64),
            (self._continuous_capture_label, 96),
        ):
            label.setFixedWidth(width)
            label.setMinimumHeight(32)
            label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        top_grid.addWidget(mode_label, 0, 0)
        top_grid.addWidget(self._mode, 0, 1)
        top_grid.addWidget(save_label, 0, 2)
        top_grid.addWidget(self._save_mode, 0, 3)
        top_grid.addWidget(self._continuous_capture_label, 0, 4)
        top_grid.addWidget(self._previous_capture_action_combo, 0, 5)
        top_grid.setColumnStretch(1, 1)
        top_grid.setColumnStretch(3, 1)
        top_grid.setColumnStretch(5, 1)
        self._adaptive_wait = QCheckBox("自动配速")
        self._boost_scroll = QCheckBox("加速滚动")
        self._boost_scroll.setChecked(True)
        self._reverse_scroll = QCheckBox("反向滚动")
        self._reverse_scroll.setToolTip("从底部向上滚动截图")
        self._copy_on_capture = ModernPopupComboBox()
        self._copy_on_capture.addItem("不自动复制", COPY_ON_CAPTURE_MODE_OFF)
        self._copy_on_capture.addItem("复制后关闭", COPY_ON_CAPTURE_MODE_COPY_CLOSE)
        self._copy_on_capture.addItem("复制后保留", COPY_ON_CAPTURE_MODE_COPY_KEEP)
        self._copy_on_capture.setToolTip("选择框选截图完成后的剪贴板复制方式")
        self._copy_on_capture.setProperty(POPUP_EXACT_WIDTH_PROPERTY, True)
        self._copy_on_capture.setCurrentIndex(0)
        self._copy_on_capture.setFixedWidth(116)
        self._copy_on_capture.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        copy_action_label = QLabel("框选后动作：")
        copy_action_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        copy_action_row = QWidget()
        copy_action_layout = QHBoxLayout(copy_action_row)
        copy_action_layout.setContentsMargins(0, 0, 0, 0)
        copy_action_layout.setSpacing(6)
        copy_action_layout.addWidget(copy_action_label)
        copy_action_layout.addWidget(self._copy_on_capture)

        scroll_opts_layout = QHBoxLayout()
        scroll_opts_layout.setContentsMargins(0, 0, 0, 0)
        scroll_opts_layout.setSpacing(18)
        scroll_opts_layout.addWidget(self._adaptive_wait)
        scroll_opts_layout.addWidget(self._boost_scroll)
        scroll_opts_layout.addWidget(self._reverse_scroll)
        scroll_opts_layout.addWidget(self._auto_snap_enabled)
        scroll_opts_layout.addWidget(copy_action_row)
        scroll_opts_layout.addStretch(1)

        top_grid.addLayout(scroll_opts_layout, 1, 0, 1, 6)
        self._capture_top_controls = top_controls
        layout.addWidget(top_controls)

        speed_row = QWidget()
        speed_layout = QHBoxLayout(speed_row)
        speed_layout.setContentsMargins(0, 0, 0, 0)
        speed_layout.setSpacing(8)
        self._speed_label = QLabel("滚动速度：极快 (0.03s)")
        self._speed_label.setMinimumWidth(145)
        self._speed = QSlider(Qt.Orientation.Horizontal)
        self._speed.setMinimum(0)
        self._speed.setMaximum(100)
        self._speed.setValue(95)
        self._speed.valueChanged.connect(self._on_speed_changed)
        speed_layout.addWidget(self._speed_label)
        speed_layout.addWidget(self._speed, 1)
        speed_row.hide()
        self._speed_row = speed_row

        self._fmt_group = QButtonGroup(self)
        self._fmt_png = QRadioButton("PNG")
        self._fmt_jpg = QRadioButton("JPG")
        self._fmt_pdf = QRadioButton("PDF")
        self._fmt_png.setChecked(True)
        for i, w in enumerate([self._fmt_png, self._fmt_jpg, self._fmt_pdf]):
            self._fmt_group.addButton(w, i)
            w.toggled.connect(lambda *_: self._update_output_options())

        self._merge_pdf = QCheckBox("合并PDF")
        self._merge_pdf.setChecked(True)
        self._merge_pdf.setEnabled(False)
        self._merge_pdf.setToolTip("分段PDF合并")
        self._merge_pdf.toggled.connect(lambda *_: self._update_output_options())
        self._merge_image = QCheckBox("拼接图片")
        self._merge_image.setChecked(True)
        self._merge_image.setEnabled(False)
        self._merge_image.setToolTip("分段长图拼接")
        self._merge_image.toggled.connect(lambda *_: self._update_output_options())
        self._dual_output = QCheckBox("同时保存")
        self._dual_output.setToolTip("同时保存图片和PDF格式")
        self._dual_output.toggled.connect(lambda *_: self._update_output_options())
        format_row = self._row(QLabel("输出格式:"), self._fmt_png, self._fmt_jpg, self._fmt_pdf, self._merge_pdf, self._merge_image, self._dual_output)
        layout.addWidget(format_row)
        file_dir = str(self._app_settings.image_output_dir or self._app_settings.pdf_output_dir)
        self._img_dir = QLineEdit(file_dir)
        self._pdf_dir = self._img_dir
        self._img_browse = QPushButton("浏览")
        self._img_browse.setObjectName("BtnSmallPrimary")
        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(8)
        path_layout.addWidget(QLabel("图片保存路径:"))
        path_layout.addWidget(self._img_dir, 1)
        path_layout.addWidget(self._img_browse)
        layout.addWidget(path_row)
        general.setCursor(Qt.CursorShape.ArrowCursor)
        annotation_style_group = self._build_annotation_style_group()
        annotation_style_group.setObjectName("AnnotationStyleGroup")
        annotation_style_group.setTitle("")
        annotation_style_group.setFlat(True)
        annotation_style_group.setStyleSheet("QGroupBox#AnnotationStyleGroup { border: none; background: transparent; padding: 0px; margin: 0px; }")
        # 从标注组中移除线型单元格
        grid = annotation_style_group.layout()
        if grid is not None:
            style_cell = grid.itemAtPosition(0, 7)
            if style_cell is not None:
                style_cell_widget = style_cell.widget()
                if style_cell_widget is not None:
                    grid.removeWidget(style_cell_widget)
                    style_cell_widget.deleteLater()
        self._line_style.setParent(None)
        self._line_style.setObjectName("")
        self._line_style.setStyleSheet("")
        self._line_style.setCursor(Qt.CursorShape.ArrowCursor)
        self._line_style.setMinimumSize(0, 0)
        self._line_style.setMaximumSize(16777215, 16777215)
        layout.addWidget(annotation_style_group)
        self._save_button_mode = ModernPopupComboBox()
        self._save_button_mode.addItems(["自动保存", "手动保存"])
        save_button_mode = str(ui.get("save_button_mode", "auto")).strip().lower()
        if save_button_mode == "manual":
            self._save_button_mode.setCurrentText("手动保存")
        else:
            self._save_button_mode.setCurrentText("自动保存")
        self._post_capture_button_style_combo = ModernPopupComboBox()
        self._post_capture_button_style_combo.addItems(["图标按钮", "文字按钮"])
        self._post_capture_button_style_combo.setCurrentText("文字按钮" if str(ui.get("post_capture_button_style", "icon")).lower() == "text" else "图标按钮")
        for combo in (self._line_style, self._post_capture_button_style_combo, self._save_button_mode):
            combo.setProperty("matchPopupWidthToParent", True)
            combo.setFixedWidth(120)
        style_row = QWidget()
        style_row_layout = QHBoxLayout(style_row)
        style_row_layout.setContentsMargins(0, 0, 0, 0)
        style_row_layout.setSpacing(8)
        style_row_layout.addWidget(QLabel("线型:"))
        style_row_layout.addWidget(self._line_style)
        style_row_layout.addWidget(QLabel("按钮组样式:"))
        style_row_layout.addWidget(self._post_capture_button_style_combo)
        style_row_layout.addWidget(QLabel("点击保存按钮:"))
        style_row_layout.addWidget(self._save_button_mode)
        style_row_layout.addStretch(1)
        layout.addWidget(style_row)
        content_layout.addWidget(general)

        shortcuts, shortcut_layout = self._card("快捷键设置")
        shortcuts.layout().setContentsMargins(16, 14, 16, 14)
        self._shortcut_tiles = []
        edit_grid = QGridLayout()
        edit_grid.setContentsMargins(0, 0, 0, 0)
        edit_grid.setHorizontalSpacing(10)
        edit_grid.setVerticalSpacing(14)

        self._hotkey = QKeySequenceEdit()
        self._hotkey.setKeySequence(QKeySequence(pynput_to_qt(self._app_settings.hotkey)))
        self._scroll_hotkey_edit = QKeySequenceEdit()
        self._scroll_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt(str(ui.get("scroll_hotkey", "<f2>") or "<f2>"))))
        self._later_read_hotkey_edit = QKeySequenceEdit()
        self._later_read_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt(str(ui.get("later_read_hotkey", "<f3>") or "<f3>"))))
        self._ai_qa_hotkey_edit = QKeySequenceEdit()
        self._ai_qa_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt(str(ui.get("ai_qa_hotkey", "<alt>+<space>") or "<alt>+<space>"))))
        self._selection_translate_hotkey_edit = QKeySequenceEdit()
        self._selection_translate_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt(str(ui.get("selection_translate_hotkey", "<ctrl>+<space>") or "<ctrl>+<space>"))))
        self._selection_popup_hotkey_edit = QKeySequenceEdit()
        self._selection_popup_hotkey_edit.setKeySequence(QKeySequence(pynput_to_qt(str(ui.get("selection_popup_hotkey", "<ctrl>+b") or "<ctrl>+b"))))

        shortcut_items = [
            ("框选截图:", self._hotkey, "<f1>"),
            ("滚动截图:", self._scroll_hotkey_edit, "<f2>"),
            ("稍后阅读:", self._later_read_hotkey_edit, "<f3>"),
            ("智能问答:", self._ai_qa_hotkey_edit, "<alt>+<space>"),
            ("划词翻译:", self._selection_translate_hotkey_edit, "<ctrl>+<space>"),
            ("划词自选:", self._selection_popup_hotkey_edit, "<ctrl>+b"),
        ]
        for i, (label, editor, default) in enumerate(shortcut_items):
            lbl = QLabel(label)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl.setToolTip(f"双击文字可恢复默认快捷键")
            lbl.mouseDoubleClickEvent = lambda event, ed=editor, d=default: self._on_hotkey_label_double_click(event, ed, d)
            editor.setMaximumWidth(120)
            edit_grid.addWidget(lbl, i // 3, (i % 3) * 2)
            edit_grid.addWidget(editor, i // 3, (i % 3) * 2 + 1)
        shortcut_layout.addLayout(edit_grid)
        self._hotkeys_label = QLabel("")
        self._hotkeys_label.setObjectName("LblShortcuts")
        self._hotkeys_label.setVisible(False)
        shortcut_layout.addWidget(self._hotkeys_label)
        content_layout.addWidget(shortcuts)
        content_layout.addStretch(1)

        if page.layout() is not None:
            page.layout().activate()
        content_layout.activate()

        return page

    def _scroll_to_active_tab(self, bar: QWidget, active_btn: QPushButton) -> None:
        try:
            scroll: QScrollArea = getattr(bar, "_scroll")
            scroll_widget = getattr(bar, "_scroll_widget", None)
            scroll_layout = getattr(bar, "_scroll_layout", None)
            # Ensure layout geometry is up-to-date before computing positions,
            # otherwise newly-added buttons may still report stale (0,0) geometry.
            if scroll_layout is not None:
                scroll_layout.activate()
            if scroll_widget is not None:
                scroll_widget.adjustSize()
            hbar = scroll.horizontalScrollBar()
            btn_x = active_btn.mapTo(scroll.widget(), QPoint(0, 0)).x()
            btn_w = active_btn.width()
            if btn_w <= 0:
                btn_w = active_btn.sizeHint().width()
            vp_w = scroll.viewport().width()
            vp_left = hbar.value()
            vp_right = vp_left + vp_w
            if btn_x + btn_w > vp_right:
                target = btn_x + btn_w - vp_w + 4
                # Clamp to valid range so a not-yet-updated scrollbar maximum
                # doesn't prevent us from scrolling far enough.
                hbar.setValue(max(0, min(target, hbar.maximum())))
            elif btn_x < vp_left:
                hbar.setValue(max(0, btn_x - 4))
            self._update_tab_arrows(bar)
        except Exception:
            pass

    def _scroll_to_usage_guide_section(self, section_id: str) -> None:
        section_id = str(section_id or "")
        for entry in getattr(self, "_usage_guide_sections", []):
            if str(entry.get("id", "")) != section_id:
                continue
            widget = entry.get("widget")
            if isinstance(widget, QWidget) and widget.isVisible():
                QTimer.singleShot(0, lambda w=widget: self._usage_guide_scroll.ensureWidgetVisible(w, 0, 12))
            return

    def _change_scroll_hotkey_from_settings(self, new_hotkey: str) -> tuple[bool, str]:
        result = self._replace_global_hotkey(
            "_scroll_hotkey", "_scroll_hotkey_str", self._scroll_hotkey_triggered, new_hotkey
        )
        if result[0]:
            self._refresh_hotkey_label()
        return result

    def _apply_previous_capture_action(self) -> None:
        text = str(self._previous_capture_action_combo.currentText())
        if text == "置顶前图":
            action = "pin"
        elif text == "保存前图":
            action = "save"
        elif text == "拼接前图":
            action = "stitch"
        elif text == "暂存前图":
            action = "stash"
        else:
            action = "pin"
        self._current = update_ui_settings(previous_capture_action=action)
        self._sync_settings_after_change()

    def _apply_post_capture_button_style(self) -> None:
        text = str(self._post_capture_button_style_combo.currentText())
        self._current = update_ui_settings(post_capture_button_style="text" if text.startswith("文字") else "icon")
        self._sync_settings_after_change()

    def _apply_scroll_hotkey(self) -> None:
        qt_seq = self._scroll_hotkey_edit.keySequence().toString()
        new_hotkey = qt_to_pynput(qt_seq)
        if not str(qt_seq).strip():
            self._show_capture_status("滚动截图快捷键不能为空。", tone="warning")
            return
        if new_hotkey is None:
            new_hotkey = str(qt_seq).strip().lower()
        ui = dict(getattr(self._current, "ui", {}) or {})
        if str(new_hotkey) == str(ui.get("scroll_hotkey", "<ctrl>+<f1>")):
            return
        if self._on_scroll_hotkey_changed is not None:
            ok, msg = self._on_scroll_hotkey_changed(str(new_hotkey))
            if not ok:
                self._show_capture_status(f"滚动截图快捷键保存失败：{msg or new_hotkey}", tone="error", auto_hide_ms=5200)
                return
        self._current = update_ui_settings(scroll_hotkey=str(new_hotkey))
        self._sync_settings_after_change()
        self._show_capture_status(f"滚动截图快捷键已保存：{qt_seq}", tone="success")

    def _on_capture_mode_ui_changed(self) -> None:
        is_scroll = self._mode.currentText() == "滚动截图"
        if getattr(self, "_speed", None) is not None:
            self._speed.setEnabled(is_scroll)
        if getattr(self, "_speed_label", None) is not None:
            self._speed_label.setEnabled(is_scroll)
        self._adaptive_wait.setEnabled(is_scroll)
        self._reverse_scroll.setEnabled(is_scroll)
        self._boost_scroll.setEnabled(is_scroll)
        self._merge_pdf.setEnabled(is_scroll and bool(self._fmt_pdf.isChecked() or self._dual_output.isChecked()))
        self._merge_image.setEnabled(bool(self._fmt_png.isChecked() or self._fmt_jpg.isChecked() or self._dual_output.isChecked()))
        self._dual_output.setEnabled(is_scroll)
        if is_scroll:
            self._update_output_options()

    def _ensure_fullscreen_border(self) -> None:
        screen = QGuiApplication.primaryScreen()
        geo = screen.geometry() if screen is not None else QRect(0, 0, 800, 600)
        rect = QRect(int(geo.x()), int(geo.y()), int(geo.width()), int(geo.height()))
        if self._border_overlay is None:
            self._border_overlay = SelectionBorderOverlay(rect)
        else:
            self._border_overlay.setGeometry(rect)
        self._apply_annotation_style_to_border()
        self._connect_border_escape()
        self._border_overlay.show()
        self._border_overlay.raise_()
        self._sync_selection_shade_for_border()
        self._arm_left_click_finish_window()

    def _apply_annotation_style_to_border(self) -> None:
        if self._border_overlay is None:
            return
        try:
            self._border_overlay.set_annotation_style(self._current_annotation_style())
        except Exception:
            pass

    def _select_region(
        self,
        on_confirmed: Optional[Callable[[], None]] = None,
        *,
        show_border: bool = True,
        border_interactive: bool = False,
        start_cancel_listener: bool = True,
        arm_finish_click: bool = True,
        confirm_delay_ms: int = 250,
        close_on_confirm: bool = True,
        freeze_on_start: bool = False,
        show_frozen_background: bool = False,
        immediate_on_confirmed: bool = False,
        auto_snap: bool = False,
        is_scroll_capture: bool = False,
    ) -> None:
        from deepcat.ui.region_overlay import RegionOverlay

        overlay = RegionOverlay(
            confirm_delay_ms=confirm_delay_ms,
            close_on_confirm=close_on_confirm,
            freeze_on_start=freeze_on_start,
            show_frozen_background=show_frozen_background,
            auto_snap=auto_snap,
            annotation_style=self._current_annotation_style(),
            cursor_shape_provider=NotificationPopup.cursor_shape_at,
            link_probe_enabled=False,
            is_scroll_capture=is_scroll_capture,
        )
        overlay.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._region_overlay = overlay
        owner = self
        overlay.destroyed.connect(lambda *_, owner=owner: setattr(owner, "_region_overlay", None))
        def on_ok(r: SelectedRegion) -> None:
            try:
                self._capture_region = r.as_tuple()
                self._capture_region_logical = r.logical_tuple()
                self._capture_frozen_screen_bgr = getattr(r, "frozen_screen_bgr", None)
                self._capture_frozen_screen_origin_px = (
                    int(getattr(r, "frozen_screen_left", 0) or 0),
                    int(getattr(r, "frozen_screen_top", 0) or 0),
                )
                self._capture_transition_pending = not close_on_confirm
                if self._border_overlay is not None:
                    self._border_overlay.close()
                    self._border_overlay = None
                self._close_selection_shade_overlay()
                if bool(show_border):
                    l, t, w, h = self._capture_region_logical
                    border_rect = QRect(int(l), int(t), int(w), int(h))
                    self._border_overlay = SelectionBorderOverlay(border_rect, interactive=bool(border_interactive))
                    self._apply_annotation_style_to_border()
                    self._connect_border_escape()
                    app_settings = getattr(self, "_app_settings", None)
                    if not bool(getattr(app_settings, "auto_save", True)) or bool(is_scroll_capture):
                        self._sync_border_dimension_tip_for_toolbar(border_rect)
                    self._border_overlay.show()
                    self._border_overlay.raise_()
                    if bool(getattr(self, "_capture_transition_pending", False)):
                        self._prepare_selection_shade_handoff(overlay)
                    else:
                        try:
                            self._border_overlay.repaint()
                        except Exception:
                            pass
                        if not bool(immediate_on_confirmed):
                            self._flush_fast_ui(1)
                        self._sync_selection_shade_for_border()
                if bool(freeze_on_start) and not bool(close_on_confirm) and not bool(getattr(self, "_capture_transition_pending", False)):
                    try:
                        overlay.set_selection_visual_visible(False)
                        if not bool(immediate_on_confirmed):
                            self._flush_fast_ui(1)
                    except Exception:
                        pass
                if bool(start_cancel_listener):
                    self._start_right_click_cancel_listener()
                if bool(arm_finish_click):
                    self._arm_left_click_finish_window()
                if on_confirmed is not None:
                    if bool(immediate_on_confirmed):
                        on_confirmed()
                        self._flush_fast_ui(1)
                    else:
                        def safe_confirmed() -> None:
                            try:
                                on_confirmed()
                            except Exception:
                                self._cleanup_failed_region_selection()
                                get_logger().exception("选区确认后执行截图失败")

                        QTimer.singleShot(0, safe_confirmed)
            except Exception:
                self._cleanup_failed_region_selection()
                get_logger().exception("选区确认后执行截图失败")

        def on_cancel() -> None:
            self._cancelled_region_capture_token = int(self._region_capture_token)
            self._region_capture_shell_pending = False
            self._capture_region = None
            self._capture_region_logical = None
            self._capture_frozen_screen_bgr = None
            self._capture_frozen_screen_origin_px = (0, 0)
            self._capture_transition_pending = False
            if self._border_overlay is not None:
                self._border_overlay.close()
                self._border_overlay = None
            self._close_selection_shade_overlay()
            self._stop_right_click_cancel_listener()
            self._stop_left_click_retake_listener()
            self._set_selection_translate_suspended(False)
            return

        overlay.confirmed.connect(on_ok)
        overlay.canceled.connect(on_cancel)
        self._start_right_click_cancel_listener()
        overlay.show_capture_overlay()
        overlay.activateWindow()
        overlay.raise_()
        NotificationPopup.raise_active_popups()
        QTimer.singleShot(0, NotificationPopup.raise_active_popups)
        QTimer.singleShot(80, NotificationPopup.raise_active_popups)

    def _cleanup_failed_region_selection(self) -> None:
        self._cancelled_region_capture_token = int(self._region_capture_token)
        self._region_capture_shell_pending = False
        self._capture_region = None
        self._capture_region_logical = None
        self._capture_frozen_screen_bgr = None
        self._capture_frozen_screen_origin_px = (0, 0)
        for attr in ("_region_overlay", "_border_overlay"):
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            try:
                widget.close()
            except Exception:
                pass
            setattr(self, attr, None)
        self._close_selection_shade_overlay()
        self._stop_right_click_cancel_listener()
        self._stop_left_click_retake_listener()
        self._set_selection_translate_suspended(False)
        self._finalize_stitch_buffer()
        self._restore_normal_cursor()
        try:
            self._show_from_tray()
        except Exception:
            pass

    def _set_copy_on_capture_combo_mode(self, mode: str) -> None:
        combo = getattr(self, "_copy_on_capture", None)
        if combo is None:
            return
        normalized = normalize_copy_on_capture_mode(mode, default=DEFAULT_COPY_ON_CAPTURE_MODE)
        for index in range(combo.count()):
            if str(combo.itemData(index) or "") == normalized:
                combo.setCurrentIndex(index)
                return
        combo.setCurrentIndex(0)

    def _current_copy_on_capture_mode(self) -> str:
        combo = getattr(self, "_copy_on_capture", None)
        if combo is not None:
            try:
                return normalize_copy_on_capture_mode(combo.currentData(), default=DEFAULT_COPY_ON_CAPTURE_MODE)
            except Exception:
                pass
        try:
            ui = dict(getattr(self._app_settings, "ui", {}) or {})
            return normalize_copy_on_capture_mode(
                ui.get("copy_on_capture_mode"),
                ui.get("copy_on_capture") if "copy_on_capture" in ui else None,
                DEFAULT_COPY_ON_CAPTURE_MODE,
            )
        except Exception:
            return DEFAULT_COPY_ON_CAPTURE_MODE

    def _copy_on_capture_enabled(self) -> bool:
        return self._current_copy_on_capture_mode() != COPY_ON_CAPTURE_MODE_OFF

    def _copy_on_capture_closes_region_ui(self) -> bool:
        return self._current_copy_on_capture_mode() == COPY_ON_CAPTURE_MODE_COPY_CLOSE

    def _previous_capture_action(self) -> str:
        ui = dict(getattr(self._app_settings, "ui", {}) or {})
        action = str(ui.get("previous_capture_action", "pin")).strip().lower()
        if action in {"pin", "pinned", "top", "topmost", "置顶", "自动置顶"}:
            return "pin"
        if action in {"save", "保存", "保存前图", "auto_save_previous", "autosaveprevious"}:
            return "save"
        if action in {"stitch", "拼接", "拼接前图"}:
            return "stitch"
        if action in {"stash", "暂存", "暂存前图"}:
            return "stash"
        return "pin"

    def _post_actions_capture_device_pixel_ratio(self, post_actions: Optional[PostCaptureActions], image_bgr: Any = None) -> float:
        if post_actions is None:
            return self._capture_image_device_pixel_ratio()
        try:
            region = getattr(post_actions, "_region", None)
            logical = self._rect_tuple_from_qrect_like(region)
            if logical is not None:
                _, _, logical_w, logical_h = logical
                shape = getattr(image_bgr, "shape", None)
                if shape is not None and len(shape) >= 2:
                    dpr = self._device_pixel_ratio_from_sizes(int(shape[1]), int(shape[0]), logical_w, logical_h)
                    if dpr > 0:
                        return dpr
                region_px = getattr(post_actions, "_region_px", None)
                if region_px is not None:
                    _, _, px_w, px_h = [int(x) for x in region_px]
                    dpr = self._device_pixel_ratio_from_sizes(px_w, px_h, logical_w, logical_h)
                    if dpr > 0:
                        return dpr
        except RuntimeError:
            return self._capture_image_device_pixel_ratio()
        except Exception:
            pass
        return self._capture_image_device_pixel_ratio()

    def _capture_image_device_pixel_ratio(self) -> float:
        try:
            region_px = self._capture_region
            region_logical = self._capture_region_logical
            if region_px is not None and region_logical is not None:
                dpr = self._device_pixel_ratio_from_sizes(
                    region_px[2],
                    region_px[3],
                    region_logical[2],
                    region_logical[3],
                )
                if dpr > 0:
                    return dpr
        except Exception:
            pass
        try:
            dpr = float(self.devicePixelRatioF())
            if 0.25 <= dpr <= 8.0:
                return dpr
        except Exception:
            pass
        return 1.0

    def _clear_stitch_buffer(self) -> None:
        self._stitch_buffer_bgr = None
        self._stitch_buffer_dpr = 0.0

    def _clear_stashed_captures(self) -> None:
        self._stashed_capture_items = []
        self._stashed_captures_show_pending = False

    def _stash_post_actions_capture(self, post_actions: Optional[PostCaptureActions] = None) -> bool:
        post_actions = post_actions or getattr(self, "_post_actions", None)
        if post_actions is None:
            return False
        try:
            if bool(getattr(post_actions, "_continuous_stash_captured", False)):
                return False
        except RuntimeError:
            return False
        except Exception:
            pass
        try:
            image_bgr = post_actions.export_image_bgr()
        except Exception:
            get_logger().exception("暂存前图提取失败")
            return False
        if image_bgr is None:
            return False
        try:
            stored_bgr = image_bgr.copy()
        except Exception:
            stored_bgr = image_bgr
        try:
            setattr(post_actions, "_continuous_stash_captured", True)
        except Exception:
            pass
        post_region_px = getattr(post_actions, "_region_px", None)
        post_region_logical = self._rect_tuple_from_qrect_like(getattr(post_actions, "_region", None))
        item = {
            "image_bgr": stored_bgr,
            "dpr": self._post_actions_capture_device_pixel_ratio(post_actions, image_bgr),
            "created_at": datetime.now(),
            "region_px": tuple(post_region_px) if post_region_px is not None else (tuple(getattr(self, "_capture_region", None)) if getattr(self, "_capture_region", None) is not None else None),
            "region_logical": post_region_logical if post_region_logical is not None else (tuple(getattr(self, "_capture_region_logical", None)) if getattr(self, "_capture_region_logical", None) is not None else None),
        }
        self._stashed_capture_items.append(item)
        return True

    def _defer_show_stashed_captures(self) -> None:
        if not getattr(self, "_stashed_capture_items", None):
            return
        if bool(getattr(self, "_stashed_captures_show_pending", False)):
            return
        self._stashed_captures_show_pending = True
        # 等截图工具条与覆盖层完成原生窗口销毁，避免后续失活事件把暂存窗口压回后台。
        QTimer.singleShot(80, lambda: self._show_stashed_captures_if_any())

    def _pin_stashed_capture(self, image_bgr, dpr: float, capture_frames: int = 1) -> None:
        try:
            from deepcat.utils.image_utils import bgr_to_rgb
            from deepcat.ui.pinned_image_window import PinnedImageWindow

            pinned_bgr = image_bgr.copy()
            ratio = float(dpr or 1.0)
            if not (0.25 <= ratio <= 8.0):
                ratio = 1.0
            rgb = bgr_to_rgb(pinned_bgr)
            h, w0 = int(rgb.shape[0]), int(rgb.shape[1])
            qimg = QImage(rgb.data, w0, h, int(rgb.strides[0]), QImage.Format.Format_RGB888).copy()
            qimg.setDevicePixelRatio(ratio)
            win = PinnedImageWindow(
                qimg,
                image_bgr=pinned_bgr,
                default_dir=str(get_image_output_dir()),
                default_format=str(self._current_format()),
                jpg_quality=int(self._cfg.JPG_QUALITY),
                anchor_rect=None,
                capture_frames=int(capture_frames),
                on_toast=self._send_tray_notification,
            )
            win.show()
            win.raise_()
            try:
                win.activateWindow()
            except Exception:
                pass
        except Exception:
            get_logger().exception("置顶暂存截图失败")

    def _show_stashed_captures_if_any(self) -> bool:
        self._stashed_captures_show_pending = False
        items = list(getattr(self, "_stashed_capture_items", []) or [])
        if not items:
            return False
        self._stashed_capture_items = []
        try:
            old_dialog = getattr(self, "_stashed_captures_dialog", None)
            if old_dialog is not None:
                old_dialog.close()
        except Exception:
            pass
        dialog = _StashedCapturesDialog(
            items,
            default_format=str(self._current_format()),
            jpg_quality=int(self._cfg.JPG_QUALITY),
            on_pin=lambda image_bgr, dpr, frames: self._pin_stashed_capture(image_bgr, dpr, frames),
            on_toast=self._send_tray_notification,
            parent=None,
        )
        try:
            dialog.setWindowIcon(self.windowIcon())
        except Exception:
            pass

        def on_destroyed(*_) -> None:
            if getattr(self, "_stashed_captures_dialog", None) is dialog:
                self._stashed_captures_dialog = None

        dialog.destroyed.connect(on_destroyed)
        self._stashed_captures_dialog = dialog
        try:
            cursor_pos = QCursor.pos()
            screen = QGuiApplication.screenAt(cursor_pos) or QGuiApplication.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                x = geo.left() + max(0, (geo.width() - dialog.width()) // 2)
                y = geo.top() + max(0, (geo.height() - dialog.height()) // 2)
                dialog.move(x, y)
        except Exception:
            pass
        dialog.present()
        return True

    def _finalize_or_clear_stitch_buffer_for_post_close(self) -> None:
        try:
            action = self._previous_capture_action()
        except Exception:
            action = ""
        if self._post_actions_has_manual_pin():
            self._clear_stitch_buffer()
            self._defer_show_stashed_captures()
            return
        if action == "stash":
            self._stash_post_actions_capture()
        self._finalize_stitch_buffer()

    def _finalize_stitch_buffer(self) -> None:
        bgr = self._stitch_buffer_bgr
        dpr = float(getattr(self, "_stitch_buffer_dpr", 0.0) or 0.0)
        if not (0.25 <= dpr <= 8.0):
            dpr = self._capture_image_device_pixel_ratio()
        self._clear_stitch_buffer()
        if bgr is None:
            self._defer_show_stashed_captures()
            return
        try:
            from deepcat.utils.image_utils import bgr_to_rgb
            from deepcat.ui.pinned_image_window import PinnedImageWindow
            import numpy as np

            if not isinstance(bgr, np.ndarray):
                return
            rgb = bgr_to_rgb(bgr)
            h, w0 = int(rgb.shape[0]), int(rgb.shape[1])
            qimg = QImage(rgb.data, w0, h, int(rgb.strides[0]), QImage.Format.Format_RGB888).copy()
            qimg.setDevicePixelRatio(float(dpr))
            win = PinnedImageWindow(
                qimg,
                image_bgr=bgr,
                default_dir=str(get_image_output_dir()),
                default_format=str(self._current_format()),
                jpg_quality=int(self._cfg.JPG_QUALITY),
                anchor_rect=None,
                capture_frames=1,
                on_toast=self._send_tray_notification,
            )
            win.show()
            win.raise_()
            try:
                win.activateWindow()
            except Exception:
                pass
            if self._border_overlay is not None:
                try:
                    self._border_overlay.close()
                except Exception:
                    pass
                self._border_overlay = None
            self._close_selection_shade_overlay()
            if self._region_overlay is not None:
                try:
                    self._region_overlay.close()
                except Exception:
                    pass
                self._region_overlay = None
            self.close()
        except Exception:
            pass
        finally:
            self._defer_show_stashed_captures()

    def _post_capture_button_style(self) -> str:
        ui = dict(getattr(self._app_settings, "ui", {}) or {})
        style = str(ui.get("post_capture_button_style", "icon")).strip().lower()
        return "text" if style in {"text", "文字", "文字按钮"} else "icon"

    def _handle_previous_capture_before_new_capture(self) -> None:
        self._stop_left_click_retake_listener()
        self._continuous_retake_suppressed = False
        if self._post_actions is None and self._border_overlay is None:
            return
        action = self._previous_capture_action()
        handled = False
        if action == "pin" and self._post_actions is not None:
            try:
                handled = bool(self._post_actions.pin_and_close())
            except Exception:
                handled = False
        elif action == "save" and self._post_actions is not None:
            try:
                image_bgr = self._post_actions.export_image_bgr()
                if image_bgr is not None:
                    saved_result = self._save_single_capture_outputs(image_bgr)
                    saved_paths = saved_result if isinstance(saved_result, list) else [saved_result]
                    open_dir = None
                    for saved_path in saved_paths:
                        try:
                            open_dir = str(Path(str(saved_path)).parent)
                            break
                        except Exception:
                            pass
                    self._send_tray_notification("已保存前图", "已自动保存上一张截图", 1500, open_dir=open_dir)
                    handled = True
            except Exception:
                get_logger().exception("连续截图前保存上一张截图失败")
                handled = False
        elif action == "stitch" and self._post_actions is not None:
            try:
                image_bgr = self._post_actions.export_image_bgr()
                if image_bgr is not None:
                    self._stitch_buffer_bgr = image_bgr.copy()
                    self._stitch_buffer_dpr = self._capture_image_device_pixel_ratio()
            except Exception:
                get_logger().exception("拼接前图提取失败")
            if self._post_actions is not None:
                try:
                    self._post_actions._on_close = lambda: None
                    self._post_actions.close()
                except Exception:
                    pass
                self._post_actions = None
            if self._border_overlay is not None:
                try:
                    self._border_overlay.close()
                except Exception:
                    pass
                self._border_overlay = None
            self._close_selection_shade_overlay()
            if self._region_overlay is not None:
                try:
                    self._region_overlay.close()
                except Exception:
                    pass
                self._region_overlay = None
            handled = True
        elif action == "stash" and self._post_actions is not None:
            self._stash_post_actions_capture(self._post_actions)
            if self._post_actions is not None:
                try:
                    self._post_actions._on_close = lambda: None
                    self._post_actions.close()
                except Exception:
                    pass
                self._post_actions = None
            if self._border_overlay is not None:
                try:
                    self._border_overlay.close()
                except Exception:
                    pass
                self._border_overlay = None
            self._close_selection_shade_overlay()
            if self._region_overlay is not None:
                try:
                    self._region_overlay.close()
                except Exception:
                    pass
                self._region_overlay = None
            handled = True
        if not bool(handled):
            if self._post_actions is not None:
                try:
                    self._post_actions.close_all()
                except Exception:
                    try:
                        self._post_actions.close()
                    except Exception:
                        pass
                    self._post_actions = None
            if self._border_overlay is not None:
                try:
                    self._border_overlay.close()
                except Exception:
                    pass
                self._border_overlay = None
            self._close_selection_shade_overlay()
            if self._region_overlay is not None:
                try:
                    self._region_overlay.close()
                except Exception:
                    pass
                self._region_overlay = None
            self._capture_frozen_screen_bgr = None
            self._capture_frozen_screen_origin_px = (0, 0)
        try:
            QApplication.processEvents()
        except Exception:
            pass
        if bool(handled) and self._post_actions is not None:
            try:
                self._post_actions.close_all()
            except Exception:
                self._post_actions = None
        self._restore_normal_cursor()

    def _start_capture_clicked(self, from_tray: bool = False, mode_override: Optional[str] = None) -> None:
        write_crash_breadcrumb(
            "MainWindow.start_capture.enter",
            from_tray=bool(from_tray),
            mode_override=str(mode_override or ""),
            thread_busy=bool(self._thread is not None),
        )
        if self._thread is not None:
            return
        if self._cat_reminder_session is not None:
            return
        try:
            modal = QApplication.activeModalWidget()
            if modal is not None and modal is not self:
                modal.close()
        except Exception:
            pass
        try:
            if self._todo_popup is not None:
                self._todo_popup.close()
                self._todo_popup = None
        except Exception:
            pass
        close_later_read_probe = getattr(self, "_close_later_read_probe_overlay", None)
        if getattr(self, "_later_read_probe_overlay", None) is not None and callable(close_later_read_probe):
            close_later_read_probe(restore_cursor=False)
        cancel_prewarm = getattr(self, "_cancel_post_capture_actions_prewarm", None)
        if callable(cancel_prewarm):
            cancel_prewarm(close_widget=True)
        self._stop_left_click_retake_listener()
        self._continuous_retake_suppressed = False
        self._last_later_read_capture_key = ""
        self._hide_settings_for_capture()
        self._handle_previous_capture_before_new_capture()
        previous_action = self._previous_capture_action()
        if previous_action != "stitch":
            self._stitch_buffer_bgr = None
            self._stitch_buffer_dpr = 0.0
        if previous_action != "stash":
            self._clear_stashed_captures()
        if self._region_overlay is not None:
            try:
                self._region_overlay.close()
            except Exception:
                pass
            self._region_overlay = None
        self._restore_normal_cursor()
        self._set_selection_translate_suspended(True)
        self._hide_for_capture_without_animation()
        if mode_override is None and self._cdp_mode is not None and self._cdp_mode.isChecked():
            self._start_cdp_capture()
            return
        mode = str(mode_override or self._mode.currentText())
        self._pending_capture_mode = mode
        if mode in {"滚动截屏", "滚动截图"}:
            def begin() -> None:
                QTimer.singleShot(0, self._start_capture_after_countdown)

            self._select_region(
                begin,
                is_scroll_capture=True,
                close_on_confirm=False,
            )
            return
        if mode in {"框选截屏", "框选截图"}:
            self._region_capture_token += 1
            self._cancelled_region_capture_token = -1
            region_capture_token = int(self._region_capture_token)
            write_crash_breadcrumb(
                "MainWindow.region_capture.begin",
                auto_snap=bool((getattr(self._app_settings, "ui", {}) or {}).get("auto_snap_enabled", True)),
                token=region_capture_token,
            )

            def begin_region_capture() -> None:
                self._capture_keep_border_visible_once = True
                QTimer.singleShot(40, lambda token=region_capture_token: self._capture_selected_region_once(token))

            self._select_region(
                begin_region_capture,
                show_border=True,
                border_interactive=True,
                start_cancel_listener=True,
                arm_finish_click=False,
                confirm_delay_ms=0,
                close_on_confirm=False,
                freeze_on_start=True,
                show_frozen_background=True,
                immediate_on_confirmed=True,
                auto_snap=bool((getattr(self._app_settings, "ui", {}) or {}).get("auto_snap_enabled", True)),
            )
            QTimer.singleShot(80, self._preload_region_capture_tools)
            return
        self._capture_fullscreen_once()

    def _start_cdp_capture(self) -> None:
        from deepcat.ui.cdp_worker import CDPSettings, CDPWorker

        port = 9888
        if self._cdp_port is not None:
            try:
                port = int(self._cdp_port.text().strip())
            except Exception:
                self._show_styled_message_box(
                    QMessageBox.Icon.Warning,
                    "Chrome截图设置错误",
                    "CDP 端口无效",
                    "请输入有效端口号，例如 9888",
                )
                self._set_selection_translate_suspended(False)
                return
        settings = CDPSettings(port=int(port), url_contains=None, content_only=False)
        worker = CDPWorker(settings)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(lambda *_: thread.quit())
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._worker = worker  # type: ignore[assignment]
        self._stop_event = None
        self._thread = thread
        self._capture_manual_mode = False
        self._capture_fullscreen_mode = False
        self._notify_state.reset()

        self._floating = FloatingBar()
        self._floating.set_status("CDP 截图中...")
        self._floating.stop_clicked.connect(worker.request_stop)
        self._floating.show()
        self._hide_for_capture_without_animation()
        self._capture_started_ts = time.time()
        self._last_frame_index = 0
        self._capture_context = {
            "mode": "CDP",
            "format": "png",
            "scroll_method": "cdp",
        }
        self._task_feedback.start("capture", "CDP capture started", self._capture_context)
        thread.start()

    def _save_single_capture_outputs(self, image_bgr):
        from deepcat.output.saver import save_image

        fmt = str(self._current_format())
        dual_output = bool(self._dual_output.isChecked()) if self._dual_output is not None else False
        image_fmt = fmt if fmt in {"png", "jpg"} else "png"
        want_image = bool(dual_output) or fmt in {"png", "jpg"}
        want_pdf = bool(dual_output) or fmt == "pdf"
        results: list[str] = []

        if want_image:
            results.append(save_image(image_bgr, str(get_image_output_dir(validate_writable=True)), image_fmt, int(self._cfg.JPG_QUALITY)))
        if want_pdf:
            results.append(save_image(image_bgr, str(get_pdf_output_dir(validate_writable=True)), "pdf", int(self._cfg.JPG_QUALITY)))

        if len(results) == 1:
            return results[0]
        return results

    def _preload_region_capture_tools(self) -> None:
        if self._capture_busy_for_warmup():
            return
        if bool(getattr(self, "_capture_tools_warmup_done", False)) or bool(getattr(self, "_capture_tools_warmup_running", False)):
            return

        def warm() -> None:
            try:
                self._warm_capture_backend_modules()
            except Exception:
                pass
            finally:
                self._capture_tools_warmup_done = True
                self._capture_tools_warmup_running = False

        try:
            self._capture_tools_warmup_running = True
            t = threading.Thread(target=warm, daemon=True)
            self._capture_tools_warmup_thread = t
            t.start()
        except Exception:
            self._capture_tools_warmup_running = False
            pass

    def _resolve_region_capture_mode(self) -> str:
        """框选/区域截图的真实模式。

        该上下文始终代表单帧框选截图（scroll_method="region"），绝不能继承
        设置里的"滚动截图"，否则 _on_finished 会误判为滚动截图而丢弃框选结果。
        优先取本次触发写入的 _pending_capture_mode（F1/retake 经 mode_override
        强制为"框选截图"），其次取设置下拉框中的框选模式，最终兜底为"框选截图"。
        """
        region_modes = {"框选截屏", "框选截图", "区域选取"}
        pending = str(getattr(self, "_pending_capture_mode", "") or "")
        if pending in region_modes:
            return pending
        settings_mode = str(self._mode.currentText()) if self._mode is not None else ""
        if settings_mode in region_modes:
            return settings_mode
        return "框选截图"

    def _prepare_region_capture_context(self) -> None:
        self._notify_state.reset()
        self._capture_started_ts = time.time()
        self._last_frame_index = 1
        self._capture_manual_mode = False
        self._capture_fullscreen_mode = False
        self._capture_context = {
            "mode": self._resolve_region_capture_mode(),
            "format": str(self._current_format()),
            "scroll_method": "region",
            "adaptive_wait": False,
            "boost_scroll": False,
            "merge_pdf": False,
            "merge_image": False,
            "dual_output": bool(self._dual_output.isChecked()) if self._dual_output is not None else False,
            "region": list(self._capture_region) if self._capture_region is not None else None,
        }

    def _region_placeholder_bgr(self):
        import numpy as np

        return np.zeros((1, 1, 3), dtype=np.uint8)

    def _crop_frozen_region_bgr(self, region_px: Optional[tuple[int, int, int, int]]):
        if region_px is None or self._capture_frozen_screen_bgr is None:
            return None
        try:
            import numpy as np  # noqa: F401

            left, top, width, height = [int(x) for x in region_px]
            origin_left, origin_top = self._capture_frozen_screen_origin_px
            x1 = int(left - int(origin_left))
            y1 = int(top - int(origin_top))
            x2 = int(x1 + max(1, width))
            y2 = int(y1 + max(1, height))
            frame = self._capture_frozen_screen_bgr
            h, w = int(frame.shape[0]), int(frame.shape[1])
            cx1 = max(0, min(w, x1))
            cy1 = max(0, min(h, y1))
            cx2 = max(0, min(w, x2))
            cy2 = max(0, min(h, y2))
            if cx2 <= cx1 or cy2 <= cy1:
                return None
            return frame[cy1:cy2, cx1:cx2, :3].copy()
        except Exception:
            return None

    def _show_pending_region_capture_ui(self) -> None:
        if self._capture_region is None:
            return
        if bool(getattr(self._app_settings, "auto_save", True)):
            return
        if self._post_actions is not None:
            return
        self._prepare_region_capture_context()
        self._region_capture_shell_pending = True
        try:
            self._on_finished(self._region_placeholder_bgr())
            if self._post_actions is not None:
                try:
                    self._post_actions.set_image_pending(True)
                    self._post_actions.show()
                    self._post_actions.raise_()
                except Exception:
                    pass
            if self._border_overlay is not None:
                try:
                    self._border_overlay.show()
                    self._border_overlay.raise_()
                    if not bool(getattr(self, "_capture_transition_pending", False)):
                        self._sync_selection_shade_for_border()
                except Exception:
                    pass
            QApplication.processEvents()
        except Exception:
            self._region_capture_shell_pending = False
            raise

    def _finish_pending_region_capture_ui(self, image_bgr) -> bool:
        if self._post_actions is None:
            self._region_capture_shell_pending = False
            return False
        try:
            if self._border_overlay is not None:
                self._border_overlay.set_annotation_image(image_bgr)
                self._border_overlay.show()
                self._border_overlay.raise_()
                if not bool(getattr(self, "_capture_transition_pending", False)):
                    self._sync_selection_shade_for_border()
                try:
                    self._border_overlay.repaint()
                except Exception:
                    pass
            self._post_actions.set_image(image_bgr)
            self._post_actions.set_image_pending(False)
            self._post_actions.show()
            self._post_actions.raise_()
            self._start_right_click_cancel_listener()
            self._start_left_click_retake_listener()
            return True
        except Exception:
            return False
        finally:
            self._region_capture_shell_pending = False

    def _should_notify_region_clipboard_copy(self) -> bool:
        return self._previous_capture_action() not in {"pin", "save", "stitch", "stash"}

    def _copy_region_capture_to_clipboard(self, image_bgr) -> bool:
        if not self._copy_on_capture_enabled():
            return False
        try:
            from deepcat.output.qt_clipboard import copy_bgr_image

            copied = copy_bgr_image(image_bgr)
        except Exception:
            copied = False
        if bool(copied):
            if self._copy_on_capture_enabled() or self._should_notify_region_clipboard_copy():
                self._send_tray_notification("复制成功", "区域截图已复制到剪贴板", 1500)
            return True
        try:
            get_logger().warning("区域截图复制到剪切板失败")
        except Exception:
            pass
        return False

    def _finish_region_capture_copy_close(self) -> None:
        self._stitch_buffer_bgr = None
        self._stitch_buffer_dpr = 0.0
        self._region_capture_shell_pending = False
        if self._post_actions is not None:
            try:
                self._post_actions.close()
            except Exception:
                pass
            self._post_actions = None
        self._stop_right_click_cancel_listener()
        self._stop_left_click_retake_listener()
        if self._border_overlay is not None:
            try:
                self._border_overlay.close()
            except Exception:
                pass
            self._border_overlay = None
        self._close_selection_shade_overlay()
        if self._region_overlay is not None:
            try:
                self._region_overlay.close()
            except Exception:
                pass
            self._region_overlay = None
        self._capture_region = None
        self._capture_region_logical = None
        self._capture_frozen_screen_bgr = None
        self._capture_frozen_screen_origin_px = (0, 0)
        self._post_capture_ui_prewarmed = False
        self._resume_selection_translate_if_capture_idle()
        self._schedule_post_capture_actions_prewarm(80)
        self._restore_normal_cursor()
        self._set_selection_translate_suspended(False)
        self.close()

    def _capture_selected_region_once(self, expected_token: Optional[int] = None) -> None:
        if expected_token is not None:
            token = int(expected_token)
            if token != int(self._region_capture_token) or token == int(self._cancelled_region_capture_token):
                self._region_capture_shell_pending = False
                return
        if self._capture_region is None:
            self._region_capture_shell_pending = False
            return
        from deepcat.core.capturer import capture_screen

        self._stop_right_click_cancel_listener()
        self._start_escape_cancel_listener()
        keep_border_visible = bool(getattr(self, "_capture_keep_border_visible_once", False))
        self._capture_keep_border_visible_once = False
        hid_border = False
        if (
            self._border_overlay is not None
            and not bool(self._region_capture_shell_pending)
            and not bool(keep_border_visible)
        ):
            try:
                self._border_overlay.setVisible(False)
                self._set_selection_shade_visible(False)
                hid_border = True
            except Exception:
                pass
        if bool(hid_border):
            QApplication.processEvents()

        if not bool(self._region_capture_shell_pending):
            self._prepare_region_capture_context()

        try:
            image_bgr = self._crop_frozen_region_bgr(self._capture_region)
            if image_bgr is None:
                image_bgr = capture_screen(self._capture_region)

            if self._stitch_buffer_bgr is not None:
                try:
                    import numpy as np
                    import cv2

                    prev = self._stitch_buffer_bgr
                    curr = image_bgr
                    max_w = max(prev.shape[1], curr.shape[1])
                    main_img = prev if prev.shape[1] >= curr.shape[1] else curr
                    c1 = main_img[0, 0]
                    c2 = main_img[0, -1]
                    c3 = main_img[-1, 0]
                    c4 = main_img[-1, -1]
                    t1 = tuple(map(int, c1))
                    t2 = tuple(map(int, c2))
                    t3 = tuple(map(int, c3))
                    t4 = tuple(map(int, c4))
                    freqs = {}
                    for t in [t1, t2, t3, t4]:
                        freqs[t] = freqs.get(t, 0) + 1
                    most_common = max(freqs, key=freqs.get)
                    bg_color = np.array(most_common, dtype=np.uint8)
                    if prev.shape[1] < max_w:
                        pad_w = max_w - prev.shape[1]
                        pad = np.zeros((prev.shape[0], pad_w, 3), dtype=np.uint8)
                        pad[:, :] = bg_color
                        prev = np.hstack([prev, pad])
                    if curr.shape[1] < max_w:
                        pad_w = max_w - curr.shape[1]
                        pad = np.zeros((curr.shape[0], pad_w, 3), dtype=np.uint8)
                        pad[:, :] = bg_color
                        curr = np.hstack([curr, pad])
                    self._stitch_buffer_bgr = np.vstack([prev, curr])
                    if not (0.25 <= float(getattr(self, "_stitch_buffer_dpr", 0.0) or 0.0) <= 8.0):
                        self._stitch_buffer_dpr = self._capture_image_device_pixel_ratio()
                    image_bgr = self._stitch_buffer_bgr
                except Exception:
                    get_logger().exception("拼接截图失败")

            if bool(getattr(self._app_settings, "auto_save", True)):
                self._on_finished(self._save_single_capture_outputs(image_bgr))
                QTimer.singleShot(50, lambda img=image_bgr: self._copy_region_capture_to_clipboard(img))
                return

            if self._copy_on_capture_closes_region_ui():
                self._copy_region_capture_to_clipboard(image_bgr)
                self._finish_region_capture_copy_close()
                return

            if not self._finish_pending_region_capture_ui(image_bgr):
                self._on_finished(image_bgr)
            QTimer.singleShot(50, lambda img=image_bgr: self._copy_region_capture_to_clipboard(img))
        except Exception as e:
            self._region_capture_shell_pending = False
            self._on_failed(str(e) or repr(e), traceback.format_exc())
        finally:
            if self._region_overlay is not None:
                if bool(getattr(self, "_capture_transition_pending", False)) and self._post_actions is not None:
                    self._arm_capture_transition_release()
                else:
                    self._release_capture_transition_overlay()

    def _capture_fullscreen_once(self) -> None:
        from deepcat.core.capturer import capture_screen

        rect = self._fullscreen_logical_rect()
        self._capture_region = None
        self._capture_region_logical = (int(rect.left()), int(rect.top()), int(rect.width()), int(rect.height()))
        self._capture_frozen_screen_bgr = None
        self._capture_frozen_screen_origin_px = (0, 0)
        self._capture_manual_mode = False
        self._capture_fullscreen_mode = True
        self._notify_state.reset()
        self._capture_started_ts = time.time()
        self._last_frame_index = 1
        self._capture_context = {
            "mode": "全屏截图",
            "format": str(self._current_format()),
            "scroll_method": "fullscreen",
            "adaptive_wait": False,
            "boost_scroll": False,
            "merge_pdf": False,
            "merge_image": False,
            "dual_output": bool(self._dual_output.isChecked()) if self._dual_output is not None else False,
            "region": None,
        }
        try:
            QApplication.processEvents()
        except Exception:
            pass
        try:
            image_bgr = capture_screen(None)
            if bool(getattr(self._app_settings, "auto_save", True)):
                self._on_finished(self._save_single_capture_outputs(image_bgr))
            else:
                self._on_finished(image_bgr)
        except Exception as e:
            self._on_failed(str(e) or repr(e), traceback.format_exc())

    def _start_capture_after_countdown(self) -> None:
        from deepcat.ui.capture_worker import CaptureSettings, CaptureWorker

        self._countdown = None
        self._start_right_click_cancel_listener()
        if self._border_overlay is not None:
            self._arm_left_click_finish_window()
        method = str(self._cfg.SCROLL_METHOD)
        self._capture_manual_mode = False
        capture_mode = str(getattr(self, "_pending_capture_mode", "") or self._mode.currentText())
        self._pending_capture_mode = ""
        self._capture_fullscreen_mode = capture_mode in {"全屏", "全屏截图"}
        self._notify_state.reset()
        is_scroll_capture = capture_mode in {"滚动截图", "滚动截屏"}
        settings = CaptureSettings(
            region=self._capture_region,
            scroll_delay=float(self._cfg.SCROLL_DELAY),
            scroll_amount=int(self._cfg.SCROLL_AMOUNT),
            scroll_method=method,
            max_frames=int(self._cfg.MAX_FRAMES),
            adaptive_wait=bool(self._adaptive_wait.isChecked()) if self._adaptive_wait is not None else False,
            reverse_scroll=bool(self._reverse_scroll.isChecked()) if self._reverse_scroll is not None else False,
            boost_scroll=bool(self._boost_scroll.isChecked()) if self._boost_scroll is not None else False,
            merge_pdf=bool(self._merge_pdf.isChecked()) if self._merge_pdf is not None else False,
            merge_image=bool(self._merge_image.isChecked()) if self._merge_image is not None else False,
            dual_output=bool(self._dual_output.isChecked()) if self._dual_output is not None else False,
            strip_height=int(self._cfg.MATCH_STRIP_HEIGHT),
            min_confidence=float(self._cfg.MATCH_CONFIDENCE),
            bottom_diff_mean_threshold=float(self._cfg.BOTTOM_DIFF_MEAN_THRESHOLD),
            bottom_confirm_count=int(self._cfg.BOTTOM_CONFIRM_COUNT),
            detect_fixed_header=bool(self._cfg.DETECT_FIXED_HEADER),
            detect_fixed_footer=bool(self._cfg.DETECT_FIXED_FOOTER),
            output_format=str(self._current_format()),
            jpg_quality=int(self._cfg.JPG_QUALITY),
            auto_save=False if bool(is_scroll_capture) else bool(getattr(self._app_settings, "auto_save", True)),
            pause_hotkey="<ctrl>+<space>",
        )
        self._capture_started_ts = time.time()
        self._last_frame_index = 0
        self._capture_context = {
            "mode": str(capture_mode),
            "format": str(self._current_format()),
            "scroll_method": str(method),
            "adaptive_wait": bool(settings.adaptive_wait),
            "reverse_scroll": bool(settings.reverse_scroll),
            "boost_scroll": bool(settings.boost_scroll),
            "merge_pdf": bool(settings.merge_pdf),
            "merge_image": bool(settings.merge_image),
            "dual_output": bool(settings.dual_output),
            "region": list(settings.region) if settings.region is not None else None,
        }

        self._stop_event = threading.Event()
        worker = CaptureWorker(settings, stop_event=self._stop_event)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.metrics.connect(self._on_metrics)
        worker.status.connect(self._on_status)
        worker.attention.connect(self._on_attention)
        worker.toggle_overlay.connect(self._set_border_visible, Qt.ConnectionType.BlockingQueuedConnection)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.stopped.connect(self._on_stopped)
        worker.finished.connect(thread.quit)
        worker.failed.connect(lambda *_: thread.quit())
        worker.stopped.connect(lambda _: None)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._worker = worker
        self._thread = thread

        self._floating = FloatingBar()
        self._floating.stop_clicked.connect(self._request_stop)
        self._floating.set_frame_index(0)
        self._floating.set_stop_enabled(True)
        if bool(is_scroll_capture):
            self._floating.set_stop_text("取消")
            self._floating.set_status("滚动截图中，点击取消可停止")
        else:
            self._floating.set_stop_text("停止")
            self._floating.set_status("截图中，点击停止可取消")
        self._hide_for_capture_without_animation()
        self._floating.show()
        self._task_feedback.start("capture", "capture started", self._capture_context)

        thread.start()

    def _set_border_visible(self, visible: bool) -> None:
        if self._border_overlay is not None:
            self._border_overlay.setVisible(bool(visible))
        self._set_selection_shade_visible(bool(visible) and self._border_overlay is not None)
        if self._floating is not None:
            if bool(self._capture_fullscreen_mode):
                self._floating.setWindowOpacity(1.0 if bool(visible) else 0.0)
            else:
                self._floating.setWindowOpacity(1.0)

    def _capture_failure_message(self, msg: str, tb: str, *, is_cdp_capture: bool) -> str:
        text = f"{msg}\n{tb or ''}"
        lower = text.lower()
        region = self._capture_region or self._capture_region_logical
        try:
            if region is not None:
                width = int(region[2])
                height = int(region[3])
                if width < 8 or height < 8:
                    return "区域过小：请扩大截图区域后重试。"
        except Exception:
            pass
        if is_cdp_capture:
            return "Chrome 截图失败：请确认浏览器已开启调试端口，且目标页面可见。"
        if "permission" in lower or "access denied" in lower or "winerror 5" in lower or "拒绝访问" in text:
            return "权限不足：无法读取屏幕或写入截图文件，请检查权限后重试。"
        if any(token in lower for token in ("not visible", "invisible", "minimized", "window is hidden")) or any(token in text for token in ("窗口不可见", "最小化", "不可见")):
            return "窗口不可见：请确认目标窗口未最小化并处于可截图状态。"
        if any(token in lower for token in ("region too small", "width <= 0", "height <= 0")) or "区域过小" in text:
            return "区域过小：请扩大截图区域后重试。"
        if any(token in lower for token in ("invalid path", "no such file", "not a directory", "filename")) or any(token in text for token in ("路径无效", "目录不存在", "文件名")):
            return "保存路径无效：请在截图设置里重新选择可用保存目录。"
        if "ocr" in lower and ("not found" in lower or "no module" in lower or "不可用" in text):
            return "OCR 组件不可用：请检查 OCR 依赖是否安装完整。"
        clean = str(msg or "").strip()
        if clean:
            return f"截图失败：{clean[:180]}"
        return "截图失败：请稍后重试，详情已写入日志。"

    def _on_region_link_hovered(self, payload: object) -> None:
        if not self._later_read_enabled_now():
            return
        toast_pos = QCursor.pos()
        if not isinstance(payload, dict):
            self._show_later_read_capture_status(
                "链接解析失败：未读取到有效链接。",
                tone="error",
                auto_hide_ms=5200,
                toast_pos=toast_pos,
            )
            return
        url = str(payload.get("url", "") or "").strip()
        if not url:
            self._show_later_read_capture_status(
                "链接解析失败：未读取到有效链接。",
                tone="error",
                auto_hide_ms=5200,
                toast_pos=toast_pos,
            )
            return
        title = str(payload.get("title", "") or "").strip() or url
        key = f"{title}\n{url}"
        if key == str(getattr(self, "_last_later_read_capture_key", "")):
            return
        self._last_later_read_capture_key = key
        self._add_later_read_link_at(title, url, toast_pos=toast_pos)
        close_probe = getattr(self, "_close_later_read_probe_overlay", None)
        if callable(close_probe):
            close_probe()

    def _start_scroll_result_prepare(self, image_bgr, copy_on_capture_enabled: bool) -> bool:
        try:
            anchor = None
            if self._capture_region_logical is not None:
                l, t, w0, h0 = self._capture_region_logical
                anchor = QRect(int(l), int(t), int(w0), int(h0))
            worker = ScrollResultPrepareWorker(image_bgr, bool(copy_on_capture_enabled), anchor)
            self._scroll_prepare_workers.append(worker)
            worker.prepared.connect(self._on_scroll_result_prepared)
            worker.failed.connect(self._on_scroll_result_prepare_failed)
            worker.finished.connect(lambda w=worker: self._scroll_prepare_workers.remove(w) if w in self._scroll_prepare_workers else None)
            worker.finished.connect(worker.deleteLater)
            worker.start()
            return True
        except Exception:
            return False

    def _on_scroll_result_prepare_failed(self, msg: str) -> None:
        logger = get_logger()
        logger.warning("scroll result background prepare failed: %s", str(msg))
        self._send_tray_notification("原图预览失败", str(msg), 3500)
        self._finish_scroll_capture_cleanup()

    def _on_scroll_result_prepared(self, payload: object) -> None:
        try:
            data = dict(payload or {}) if isinstance(payload, dict) else {}
            image_bgr = data.get("image_bgr")
            if image_bgr is None:
                raise RuntimeError("没有可显示的完整截图")
            qimg = data.get("qimage")
            if qimg is None:
                h, w = image_bgr.shape[:2]
                qimg = QImage(image_bgr.data, w, h, int(image_bgr.strides[0]), QImage.Format.Format_BGR888)
                qimg = qimg.convertToFormat(QImage.Format.Format_RGB32)
            if qimg.isNull():
                raise RuntimeError("无法显示完整截图")
            if bool(data.get("copy_on_capture", True)):
                try:
                    QGuiApplication.clipboard().setImage(qimg)
                    self._send_tray_notification("复制成功", "滚动截图已复制到剪贴板", 1500)
                except Exception:
                    get_logger().warning("复制完整滚动截图失败", exc_info=True)
            try:
                dpr = float(self.devicePixelRatioF())
                if dpr > 0:
                    qimg.setDevicePixelRatio(dpr)
            except Exception:
                pass

            from deepcat.ui.pinned_image_window import PinnedImageWindow

            win = PinnedImageWindow(
                qimg,
                image_bgr=image_bgr,
                default_dir=str(get_image_output_dir()),
                default_format=str(self._current_format()),
                jpg_quality=int(self._cfg.JPG_QUALITY),
                anchor_rect=data.get("anchor_rect"),
                capture_frames=int(self._last_frame_index),
                blur_mode=False,
                on_image_changed=None,
                on_toast=self._send_tray_notification,
            )
            win.show()
            win.raise_()
            try:
                win.activateWindow()
            except Exception:
                pass
        except Exception as e:
            get_logger().exception("show prepared scroll result failed: %s", str(e))
            self._send_tray_notification("提示", "滚动截图已完成，但贴图窗口打开失败", 2200)
        finally:
            self._finish_scroll_capture_cleanup()

    def _finish_scroll_capture_cleanup(self) -> None:
        self._finalize_stitch_buffer()
        self._post_actions = None
        self._stop_right_click_cancel_listener()
        self._stop_left_click_retake_listener()
        if self._border_overlay is not None:
            try:
                self._border_overlay.close()
            except Exception:
                pass
            self._border_overlay = None
        self._close_selection_shade_overlay()
        if self._region_overlay is not None:
            try:
                self._region_overlay.close()
            except Exception:
                pass
            self._region_overlay = None
        self._capture_region = None
        self._capture_region_logical = None
        self._capture_frozen_screen_bgr = None
        self._capture_frozen_screen_origin_px = (0, 0)
        self._post_capture_ui_prewarmed = False
        self._resume_selection_translate_if_capture_idle()
        self._schedule_post_capture_actions_prewarm(80)
        self._restore_normal_cursor()
        self._set_selection_translate_suspended(False)


    def _on_finished(self, result) -> None:
        scroll_outcome: Optional[ScrollCaptureOutcome] = None
        if isinstance(result, ScrollCaptureOutcome):
            scroll_outcome = result
            result = scroll_outcome.result
            self._notify_state.on_completion(scroll_outcome.status)
            self._task_feedback.status(
                "capture",
                scroll_outcome.message,
                {
                    "status": scroll_outcome.status.value,
                    "frames": scroll_outcome.frame_count,
                    "captured_height": scroll_outcome.captured_height,
                    "estimated_peak_bytes": scroll_outcome.estimated_peak_bytes,
                    "chunked": scroll_outcome.chunked,
                    "skipped_frames": scroll_outcome.skipped_frames,
                },
            )
        is_scroll_capture = bool(
            isinstance(self._capture_context, dict)
            and str(self._capture_context.get("mode", "")) in {"滚动截图", "滚动截屏"}
        )
        if isinstance(result, dict) and bool(result.get("cancelled")):
            self._task_feedback.status("capture", "capture cancelled", result)
            if bool(is_scroll_capture):
                # 滚动截图取消时，绝对不要重建选取框和工具条，直接销毁并隐藏，退出截图状态
                self._cleanup_thread(close_border_overlay=True)
                self._capture_region = None
                self._capture_region_logical = None
                self._set_selection_translate_suspended(False)
                return

            rect = None
            if self._capture_region_logical is not None:
                l, t, w0, h0 = self._capture_region_logical
                rect = QRect(int(l), int(t), int(w0), int(h0))
            elif self._border_overlay is not None:
                if hasattr(self._border_overlay, "selection_rect"):
                    rect = QRect(self._border_overlay.selection_rect())
                else:
                    rect = QRect(self._border_overlay.geometry())

            self._cleanup_thread(close_border_overlay=True)

            if rect is not None:
                self._border_overlay = SelectionBorderOverlay(QRect(rect), interactive=True)
                self._apply_annotation_style_to_border()
                self._connect_border_escape()
                self._border_overlay.show()
                self._border_overlay.raise_()
                self._sync_selection_shade_for_border()

                def logical_to_px(r: QRect) -> tuple[int, int, int, int]:
                    return logical_rect_to_physical_tuple(QRect(r))

                def on_region_changed(new_rect_obj) -> None:
                    try:
                        new_rect = QRect(new_rect_obj)
                    except Exception:
                        return
                    self._capture_region_logical = (int(new_rect.left()), int(new_rect.top()), int(new_rect.width()), int(new_rect.height()))
                    self._capture_region = logical_to_px(new_rect)

                self._border_overlay.rect_changed.connect(on_region_changed)
            else:
                self._capture_region = None
                self._capture_region_logical = None

            self._set_selection_translate_suspended(False)
            self._show_from_tray()
            return
        is_scroll_capture = bool(
            isinstance(self._capture_context, dict)
            and str(self._capture_context.get("mode", "")) in {"滚动截图", "滚动截屏"}
        )
        keep_border = not bool(getattr(self._app_settings, "auto_save", True))
        if bool(is_scroll_capture):
            keep_border = False
        self._cleanup_thread(close_border_overlay=not keep_border)
        logger = get_logger()
        end_ts = time.time()
        if self._capture_started_ts is not None:
            elapsed = float(max(0.0, end_ts - float(self._capture_started_ts)))
        else:
            elapsed = 0.0
        success_payload = {"result": result, "elapsed": elapsed}
        if scroll_outcome is not None:
            success_payload.update(
                {
                    "scroll_status": scroll_outcome.status.value,
                    "captured_height": scroll_outcome.captured_height,
                    "estimated_peak_bytes": scroll_outcome.estimated_peak_bytes,
                    "chunked": scroll_outcome.chunked,
                    "skipped_frames": scroll_outcome.skipped_frames,
                }
            )
        self._task_feedback.succeed("capture", "capture finished", success_payload)
        is_region_capture = bool(
            isinstance(self._capture_context, dict)
            and (
                str(self._capture_context.get("mode", "")) == "区域选取"
                or str(self._capture_context.get("mode", "")) in {"框选截屏", "框选截图"}
                or str(self._capture_context.get("scroll_method", "")) == "region"
            )
        )
        if bool(is_scroll_capture) and (isinstance(result, str) or isinstance(result, list)):
            copy_on_capture_enabled = self._copy_on_capture_enabled()
            preview_source = scroll_outcome.preview_image_bgr if scroll_outcome is not None else None
            if preview_source is None:
                preview_source = result
            if self._start_scroll_result_prepare(preview_source, copy_on_capture_enabled):
                return
            if isinstance(result, str) or isinstance(result, list):
                info = {
                    "event": "scroll_capture_saved_without_preview",
                    "elapsed_s": round(elapsed, 3),
                    "frames": int(self._last_frame_index),
                    "files": [result] if isinstance(result, str) else [str(x) for x in result],
                }
                if self._capture_context is not None:
                    info.update(self._capture_context)
                logger.info(json.dumps(info, ensure_ascii=False))
                note = self._notify_state.build_saved_notification(result)
                if note is not None:
                    self._send_tray_notification(note.title, note.message, note.duration_ms)
                self._finalize_stitch_buffer()
                self._stop_right_click_cancel_listener()
                self._stop_left_click_retake_listener()
                self._capture_region = None
                self._capture_region_logical = None
                self._capture_frozen_screen_bgr = None
                self._capture_frozen_screen_origin_px = (0, 0)
                self._post_capture_ui_prewarmed = False
                self._resume_selection_translate_if_capture_idle()
                self._schedule_post_capture_actions_prewarm(80)
                self._restore_normal_cursor()
                self._set_selection_translate_suspended(False)
                return

        if isinstance(result, str):
            info = {
                "event": "capture_saved",
                "elapsed_s": round(elapsed, 3),
                "frames": int(self._last_frame_index),
                "files": [result],
            }
            if self._capture_context is not None:
                info.update(self._capture_context)
            logger.info(json.dumps(info, ensure_ascii=False))
            n = self._notify_state.build_saved_notification(result)
            if n is not None:
                self._send_tray_notification(n.title, n.message, n.duration_ms)
            self._set_selection_translate_suspended(False)
            return
        if isinstance(result, list):
            n = len(result)
            info = {
                "event": "capture_saved",
                "elapsed_s": round(elapsed, 3),
                "frames": int(self._last_frame_index),
                "files": [str(x) for x in result],
            }
            if self._capture_context is not None:
                info.update(self._capture_context)
            logger.info(json.dumps(info, ensure_ascii=False))
            note = self._notify_state.build_saved_notification([str(x) for x in result])
            if note is not None:
                self._send_tray_notification(note.title, note.message, note.duration_ms)
            self._set_selection_translate_suspended(False)
            return

        image_bgr = result
        if bool(is_scroll_capture):
            try:
                copy_on_capture_enabled = self._copy_on_capture_enabled()
                if self._start_scroll_result_prepare(image_bgr, copy_on_capture_enabled):
                    return

                if copy_on_capture_enabled:
                    try:
                        from deepcat.output.qt_clipboard import copy_bgr_image
                        copied = copy_bgr_image(image_bgr)
                        if copied:
                            self._send_tray_notification("复制成功", "最新滚动截图已自动复制到剪贴板", 1500)
                    except Exception:
                        pass

                rgb = image_bgr[:, :, ::-1].copy()
                h, w = int(rgb.shape[0]), int(rgb.shape[1])
                qimg = QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format.Format_RGB888).copy()
                pix = QPixmap.fromImage(qimg)
                try:
                    dpr = float(self.devicePixelRatioF())
                    if dpr > 0:
                        pix.setDevicePixelRatio(dpr)
                except Exception:
                    pass

                anchor = None
                if self._capture_region_logical is not None:
                    l, t, w0, h0 = self._capture_region_logical
                    anchor = QRect(int(l), int(t), int(w0), int(h0))

                from deepcat.ui.pinned_image_window import PinnedImageWindow
                win = PinnedImageWindow(
                    pix,
                    image_bgr=image_bgr.copy(),
                    default_dir=str(get_image_output_dir()),
                    default_format=str(self._current_format()),
                    jpg_quality=int(self._cfg.JPG_QUALITY),
                    anchor_rect=anchor,
                    capture_frames=int(self._last_frame_index),
                    blur_mode=False,
                    on_image_changed=None,
                    on_toast=self._send_tray_notification,
                )
                win.show()
                win.raise_()
                try:
                    win.activateWindow()
                except Exception:
                    pass

                self._finalize_stitch_buffer()
                self._post_actions = None
                self._stop_right_click_cancel_listener()
                self._stop_left_click_retake_listener()
                if self._border_overlay is not None:
                    try:
                        self._border_overlay.close()
                    except Exception:
                        pass
                    self._border_overlay = None
                self._close_selection_shade_overlay()
                if self._region_overlay is not None:
                    try:
                        self._region_overlay.close()
                    except Exception:
                        pass
                    self._region_overlay = None
                self._capture_frozen_screen_bgr = None
                self._capture_frozen_screen_origin_px = (0, 0)
                self._post_capture_ui_prewarmed = False
                self._resume_selection_translate_if_capture_idle()
                self._schedule_post_capture_actions_prewarm(80)
                self._restore_normal_cursor()
                self._set_selection_translate_suspended(False)
                return
            except Exception as e:
                logger.exception("滚动截图完成自动弹出置顶图片失败: %s", str(e))
                self._send_tray_notification("提示", "滚动截图已完成，但贴图窗口打开失败", 2200)
                self._finalize_stitch_buffer()
                self._post_actions = None
                self._stop_right_click_cancel_listener()
                self._stop_left_click_retake_listener()
                if self._border_overlay is not None:
                    try:
                        self._border_overlay.close()
                    except Exception:
                        pass
                    self._border_overlay = None
                self._close_selection_shade_overlay()
                if self._region_overlay is not None:
                    try:
                        self._region_overlay.close()
                    except Exception:
                        pass
                    self._region_overlay = None
                self._capture_region = None
                self._capture_region_logical = None
                self._capture_frozen_screen_bgr = None
                self._capture_frozen_screen_origin_px = (0, 0)
                self._post_capture_ui_prewarmed = False
                self._resume_selection_translate_if_capture_idle()
                self._schedule_post_capture_actions_prewarm(80)
                self._restore_normal_cursor()
                self._set_selection_translate_suspended(False)
                return

        is_manual_or_scroll = not bool(getattr(self._app_settings, "auto_save", True)) or bool(is_scroll_capture)
        if is_manual_or_scroll:
            rect = None
            if self._capture_region_logical is not None:
                l, t, w0, h0 = self._capture_region_logical
                rect = QRect(int(l), int(t), int(w0), int(h0))
            elif self._border_overlay is not None:
                if hasattr(self._border_overlay, "selection_rect"):
                    rect = QRect(self._border_overlay.selection_rect())
                else:
                    rect = QRect(self._border_overlay.geometry())
            if rect is None:
                rect = QRect(0, 0, 800, 600)
            if self._border_overlay is not None and not bool(getattr(self._border_overlay, "_interactive", False)):
                try:
                    self._border_overlay.close()
                except Exception:
                    pass
                self._border_overlay = None
                self._close_selection_shade_overlay()
            if self._border_overlay is None:
                self._border_overlay = SelectionBorderOverlay(QRect(rect), interactive=True)
            else:
                self._border_overlay.set_selection_rect(QRect(rect))
            self._apply_annotation_style_to_border()
            self._connect_border_escape()
            try:
                self._border_overlay.set_annotation_image(image_bgr)
            except Exception:
                pass
            self._sync_border_dimension_tip_for_toolbar(QRect(rect))
            self._border_overlay.show()
            self._border_overlay.raise_()
            if not bool(getattr(self, "_capture_transition_pending", False)):
                self._sync_selection_shade_for_border()
                try:
                    self._border_overlay.repaint()
                except Exception:
                    pass
            region_state: dict[str, object] = {"rect": QRect(rect)}

            def logical_to_px(r: QRect) -> tuple[int, int, int, int]:
                return logical_rect_to_physical_tuple(QRect(r))

            def sync_dimension_tip_placement() -> None:
                try:
                    self._sync_border_dimension_tip_for_toolbar(QRect(region_state["rect"]))
                except Exception:
                    pass

            def on_region_changed(new_rect_obj) -> None:
                try:
                    new_rect = QRect(new_rect_obj)
                except Exception:
                    return
                region_state["rect"] = QRect(new_rect)
                self._capture_region_logical = (int(new_rect.left()), int(new_rect.top()), int(new_rect.width()), int(new_rect.height()))
                self._capture_region = logical_to_px(new_rect)
                if self._post_actions is not None:
                    try:
                        self._post_actions.set_region(QRect(new_rect), self._capture_region)
                        sync_dimension_tip_placement()
                    except Exception:
                        pass

            self._border_overlay.rect_changed.connect(on_region_changed)

            def recapture_region_image(r: QRect):
                frozen = self._crop_frozen_region_bgr(logical_to_px(r))
                if frozen is not None:
                    return frozen
                try:
                    import mss
                    import numpy as np

                    px = logical_to_px(r)
                    mon = {"left": int(px[0]), "top": int(px[1]), "width": int(px[2]), "height": int(px[3])}
                    if int(mon["width"]) <= 0 or int(mon["height"]) <= 0:
                        return None
                    with mss.mss() as sct:
                        raw = np.array(sct.grab(mon))
                    return raw[:, :, :3].copy()
                except Exception:
                    return None

            def on_region_released(new_rect_obj) -> None:
                try:
                    new_rect = QRect(new_rect_obj)
                except Exception:
                    return
                on_region_changed(new_rect)

                fresh = recapture_region_image(new_rect)
                if fresh is None:
                    return
                if self._post_actions is not None:
                    try:
                        self._post_actions.set_image(fresh)
                    except Exception:
                        pass
                if self._border_overlay is not None:
                    try:
                        self._border_overlay.set_annotation_image(fresh)
                    except Exception:
                        pass

                self._copy_region_capture_to_clipboard(fresh)

            self._border_overlay.rect_released.connect(on_region_released)

            def close_all() -> None:
                self._finalize_or_clear_stitch_buffer_for_post_close()
                self._post_actions = None
                self._stop_right_click_cancel_listener()
                self._stop_left_click_retake_listener()
                if self._border_overlay is not None:
                    try:
                        self._border_overlay.close()
                    except Exception:
                        pass
                    self._border_overlay = None
                self._close_selection_shade_overlay()
                if self._region_overlay is not None:
                    try:
                        self._region_overlay.close()
                    except Exception:
                        pass
                    self._region_overlay = None
                self._capture_frozen_screen_bgr = None
                self._capture_frozen_screen_origin_px = (0, 0)
                self._post_capture_ui_prewarmed = False
                self._resume_selection_translate_if_capture_idle()
                self._schedule_post_capture_actions_prewarm(80)
                self._restore_normal_cursor()

            def on_recording_changed(recording: bool) -> None:
                try:
                    if bool(recording):
                        self._continuous_retake_suppressed = True
                        self._stop_left_click_retake_listener()
                        if self._region_overlay is not None:
                            try:
                                self._region_overlay.close()
                            except Exception:
                                pass
                            self._region_overlay = None
                        self._capture_frozen_screen_bgr = None
                        self._capture_frozen_screen_origin_px = (0, 0)
                    if self._border_overlay is not None:
                        r = QRect(region_state["rect"])
                        self._border_overlay.set_selection_rect(r)
                        self._border_overlay.setVisible(True)
                        self._border_overlay.setWindowOpacity(1.0)
                        self._border_overlay.raise_()
                        self._sync_selection_shade_for_border(r)
                except Exception:
                    pass

            def on_annotation_mode_changed(active: bool) -> None:
                try:
                    if self._border_overlay is not None:
                        self._border_overlay.setVisible(True)
                        self._border_overlay.raise_()
                        self._sync_selection_shade_for_border()
                    if self._post_actions is not None:
                        self._post_actions.raise_()
                except Exception:
                    pass

            def on_annotation_tool_changed(mode: str) -> None:
                try:
                    if self._border_overlay is not None:
                        self._border_overlay.set_annotation_mode(str(mode))
                        self._border_overlay.setVisible(True)
                        self._border_overlay.raise_()
                        self._sync_selection_shade_for_border()
                    if self._post_actions is not None:
                        self._post_actions.raise_()
                except Exception:
                    pass

            def on_annotation_undo() -> None:
                try:
                    if self._border_overlay is not None:
                        self._border_overlay.undo_annotation()
                except Exception:
                    pass

            def on_annotation_redo() -> None:
                try:
                    if self._border_overlay is not None:
                        self._border_overlay.redo_annotation()
                except Exception:
                    pass

            def on_annotation_clear() -> None:
                try:
                    if self._border_overlay is not None:
                        self._border_overlay.clear_annotations()
                except Exception:
                    pass

            def on_annotation_commit() -> None:
                try:
                    if self._border_overlay is not None:
                        self._border_overlay.commit_pending_annotation()
                except Exception:
                    pass

            render_overlay = self._border_overlay

            def on_render_annotations(base_bgr, overlay=render_overlay):
                if base_bgr is None:
                    return None
                if overlay is not None:
                    return overlay.render_annotations_to_bgr(base_bgr)
                return base_bgr.copy()

            def on_dialog_show() -> None:
                self._stop_left_click_retake_listener()
                self._stop_right_click_cancel_listener()

            def on_dialog_close() -> None:
                self._start_right_click_cancel_listener()
                if not bool(self._region_capture_shell_pending):
                    self._start_left_click_retake_listener()

            from deepcat.ui.post_capture_actions import PostCaptureActions

            post_kwargs = {
                "region_rect": QRect(region_state["rect"]),
                "image_bgr": image_bgr,
                "default_dir": str(get_image_output_dir()),
                "default_format": str(self._current_format()),
                "jpg_quality": int(self._cfg.JPG_QUALITY),
                "capture_frames": int(self._last_frame_index),
                "region_px": logical_to_px(QRect(region_state["rect"])),
                "on_close": close_all,
                "on_toast": self._send_tray_notification,
                "on_recording_changed": on_recording_changed,
                "on_annotation_mode_changed": on_annotation_mode_changed,
                "on_annotation_tool_changed": on_annotation_tool_changed,
                "on_annotation_undo": on_annotation_undo,
                "on_annotation_redo": on_annotation_redo,
                "on_annotation_clear": on_annotation_clear,
                "on_annotation_commit": on_annotation_commit,
                "on_render_annotations": on_render_annotations,
                "save_button_auto": self._save_button_auto_enabled(),
                "save_button_mode": self._save_button_mode_value(),
                "on_auto_save": lambda bgr: self._save_single_capture_outputs(bgr),
                "on_ai_recognize": lambda bgr: self._send_capture_to_ai_dialog(bgr),
                "on_ocr_panel_visibility_changed": self._on_ocr_panel_visibility_changed,
                "quick_action_handler": self._handle_output_quick_action,
                "on_ocr_text_result": self._handle_output_quick_ocr_text_result,
                "button_style": self._post_capture_button_style(),
                "on_dialog_show": on_dialog_show,
                "on_dialog_close": on_dialog_close,
            }
            warm_widget = self._post_capture_ui_warm_widget
            self._post_capture_ui_warm_widget = None
            if warm_widget is not None:
                self._post_actions = warm_widget
                self._post_actions.prepare_for_capture(**post_kwargs)
            else:
                self._post_actions = PostCaptureActions(**post_kwargs)
            sync_dimension_tip_placement()

            def on_post_actions_destroyed(*_) -> None:
                self._post_actions = None
                self._post_capture_ui_prewarmed = False
                self._resume_selection_translate_if_capture_idle()
                self._schedule_post_capture_actions_prewarm(80)

            self._post_actions.destroyed.connect(on_post_actions_destroyed)
            self._start_right_click_cancel_listener()
            if not bool(self._region_capture_shell_pending):
                self._start_left_click_retake_listener()

            # 反向滚动且在3秒内结束的快捷提示
            if bool(is_scroll_capture) and elapsed < 3.0:
                is_reverse = False
                if isinstance(self._capture_context, dict):
                    is_reverse = bool(self._capture_context.get("reverse_scroll", False))
                if is_reverse:
                    def show_reverse_toast() -> None:
                        try:
                            if self._post_actions is not None:
                                from PyQt6.QtGui import QColor
                                geo = self._post_actions.geometry()
                                pos = QPoint(geo.x() + geo.width() // 2, geo.y() + geo.height() + 8)
                                self._post_actions._icon_tooltip.show_text("反向滚动截图模式", pos, direction="below", text_color=QColor(229, 57, 53))
                        except Exception:
                            pass
                    QTimer.singleShot(150, show_reverse_toast)

            try:
                if self._border_overlay is not None and self._post_actions is not None:
                    self._border_overlay.annotation_changed.connect(self._post_actions._apply_annotations_to_image)
                    self._border_overlay.annotation_history_changed.connect(self._post_actions._update_annotation_history)
                    self._border_overlay.pin_requested.connect(self._post_actions._pin)
                    self._border_overlay.save_requested.connect(self._post_actions._save_as)
                    self._border_overlay.copy_requested.connect(self._post_actions._copy)
            except Exception:
                pass
            return


        fmt = self._current_format()

        def retake():
            self._show_from_tray()

        from deepcat.ui.preview_window import PreviewWindow

        w = PreviewWindow(
            image_bgr,
            on_retake=retake,
            on_toast=self._send_tray_notification,
            default_format=fmt,
            jpg_quality=int(self._cfg.JPG_QUALITY),
        )
        w.show()
        self._set_selection_translate_suspended(False)

    def _on_failed(self, msg: str, tb: str) -> None:
        self._task_feedback.fail("capture", str(msg), {"traceback": str(tb or "")})
        self._notify_state.on_failed()
        is_cdp_capture = bool(
            isinstance(self._capture_context, dict)
            and (
                str(self._capture_context.get("mode", "")).upper() == "CDP"
                or str(self._capture_context.get("scroll_method", "")).lower() == "cdp"
            )
        )
        rect = None
        if self._capture_region_logical is not None:
            l, t, w0, h0 = self._capture_region_logical
            rect = QRect(int(l), int(t), int(w0), int(h0))
        elif self._border_overlay is not None:
            if hasattr(self._border_overlay, "selection_rect"):
                rect = QRect(self._border_overlay.selection_rect())
            else:
                rect = QRect(self._border_overlay.geometry())

        self._cleanup_thread(close_border_overlay=True)

        if rect is not None:
            self._border_overlay = SelectionBorderOverlay(QRect(rect), interactive=True)
            self._apply_annotation_style_to_border()
            self._connect_border_escape()
            self._border_overlay.show()
            self._border_overlay.raise_()
            self._sync_selection_shade_for_border()

            def logical_to_px(r: QRect) -> tuple[int, int, int, int]:
                return logical_rect_to_physical_tuple(QRect(r))

            def on_region_changed(new_rect_obj) -> None:
                try:
                    new_rect = QRect(new_rect_obj)
                except Exception:
                    return
                self._capture_region_logical = (int(new_rect.left()), int(new_rect.top()), int(new_rect.width()), int(new_rect.height()))
                self._capture_region = logical_to_px(new_rect)

            self._border_overlay.rect_changed.connect(on_region_changed)
        else:
            self._capture_region = None
            self._capture_region_logical = None

        logger = get_logger()
        log_path = str(get_log_file_path())
        logger.error("截图失败: %s", msg)
        if tb:
            logger.error(tb)
        logger.error("日志文件: %s", log_path)
        failure_message = self._capture_failure_message(str(msg), str(tb or ""), is_cdp_capture=is_cdp_capture)
        self._force_tray_notification("截图失败", f"{failure_message}\n日志文件：{log_path}", 4800)
        self._set_selection_translate_suspended(False)
        self._show_from_tray()

    def _on_stopped(self, reason: str) -> None:
        if self._floating is not None:
            self._floating.set_status(str(reason))
        self._task_feedback.status("capture", str(reason))
        self._notify_state.on_stopped(str(reason))
        if str(reason or "") == "用户取消截图":
            self._force_tray_notification("截图已取消", "已取消当前截图任务。", 2200)
