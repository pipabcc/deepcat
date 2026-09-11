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
from deepcat.ui.main_window.compact import _CompactListWindow, _GroupManageDialog



class TranslatorMixin:
    def _startup_ocr_selfcheck(self) -> None:
        # OCR 严格按需在一次性子进程中加载，启动阶段不得导入 ONNX Runtime。
        self._ocr_selfcheck_done = True

    def _network_probe_proxy_url(self) -> str:
        translator = normalize_translator_settings((getattr(self._app_settings, "ui", {}) or {}).get("translator"))
        return str(translator.get("proxy_url", "") or "").strip()

    def _selection_translate_enabled(self) -> bool:
        try:
            translator = normalize_translator_settings((getattr(self._app_settings, "ui", {}) or {}).get("translator"))
            return bool(translator.get("selection_translate_enabled", False))
        except Exception:
            return False

    def _apply_selection_translate_listener(self, enabled: Optional[bool] = None) -> None:
        enabled = self._selection_translate_enabled() if enabled is None else bool(enabled)
        translator = normalize_translator_settings((getattr(self._app_settings, "ui", {}) or {}).get("translator"))
        popup_enabled = bool(translator.get("selection_popup_enabled", False))
        if not bool(enabled):
            try:
                if self._selection_translate_listener is not None:
                    self._selection_translate_listener.stop()
                    self._selection_translate_listener = None
            except Exception:
                pass
            return
        try:
            if self._selection_translate_listener is None:
                from deepcat.ui.selection_translate import GlobalSelectionTranslateListener

                listener = GlobalSelectionTranslateListener(self)
                listener.popupRequested.connect(self._on_selection_popup_requested)
                listener.translateRequested.connect(self._on_selection_translate_requested)
                listener.lookupFailed.connect(self._on_selection_lookup_failed)
                listener.selectionCancelled.connect(self._on_selection_cancelled)
                listener.forceSelectionCancelled.connect(lambda: self._on_selection_cancelled(force=True))
                listener.errorOccurred.connect(lambda msg: get_logger().warning(str(msg)))
                listener.set_hotkeys(self._selection_translate_hotkey_str, self._selection_popup_hotkey_str)
                self._selection_translate_listener = listener
            try:
                self._selection_translate_listener.set_selection_popup_enabled(bool(popup_enabled))
            except Exception:
                pass
            try:
                self._selection_translate_listener.set_selection_popup_blocked(bool(self._selection_popup_should_be_blocked()))
            except Exception:
                pass
            try:
                self._selection_translate_listener.set_suspended(bool(getattr(self, "_selection_translate_suspended", False)))
            except Exception:
                pass
            self._selection_translate_listener.start()
        except Exception as e:
            get_logger().warning("启用划词功能失败: %s", e)

    def _set_selection_translate_suspended(self, suspended: bool) -> None:
        self._selection_translate_suspended = bool(suspended)
        listener = self._selection_translate_listener
        try:
            if listener is not None:
                listener.set_suspended(bool(suspended))
        except Exception:
            pass
        if bool(suspended):
            self._selection_translate_popup_pending_action = ""
            try:
                if self._selection_translate_action_popup is not None:
                    self._selection_translate_action_popup.close()
            except Exception:
                pass
            self._selection_translate_action_popup = None
            try:
                if self._selection_hover_translation_popup is not None:
                    self._selection_hover_translation_popup.close()
            except Exception:
                pass
            self._selection_hover_translation_popup = None

    def _ocr_text_panel_visible(self) -> bool:
        post_actions = self._post_actions
        if post_actions is None:
            return False
        try:
            live_panel = getattr(post_actions, "_live_ocr_panel", None)
            panel = live_panel() if callable(live_panel) else None
            return bool(panel is not None and panel.isVisible())
        except RuntimeError:
            return False
        except Exception:
            return False

    def _on_selection_translate_requested(self, text: str, x: int, y: int, action: object = "translate") -> None:
        text = str(text or "").strip()
        if not text:
            return
        if isinstance(action, bool):
            action_name = "translate" if bool(action) else "manual"
        else:
            action_name = str(action or "translate").strip().lower()

        if action_name in {"popup", "popup_ready"}:
            if self._selection_popup_should_be_blocked():
                self._refresh_selection_popup_blocked()
                return
            if action_name == "popup":
                self._show_selection_action_popup(text, int(x), int(y))
            else:
                self._set_selection_action_popup_text(text)
            return

        # 【核心无缝更新体验】如果划词弹窗已经打开，再次划词时直接更新当前弹窗的文本，并自动触发相应操作，绝不销毁和重建窗口
        if self._selection_text_panel_visible():
            try:
                panel = self._selection_translate_panel
                if action_name == "manual" and hasattr(panel, "set_input_text_preserving_answer"):
                    panel.set_input_text_preserving_answer(text, QRect(int(x), int(y), 1, 1))
                    return
                panel.set_text_and_reposition(text, QRect(int(x), int(y), 1, 1))
                if action_name == "translate":
                    panel.translate_current_text()
                elif action_name == "reply_prompt":
                    panel.reply_prompt_current_text()
                elif action_name in {"optimize_prompt", "ai_search"}:
                    panel.ai_search_current_text()
                elif action_name == "explain":
                    panel.explain_current_text()
                elif action_name in {"summary", "summarize"}:
                    panel.summarize_current_text()
                return
            except Exception as e:
                get_logger().warning("无缝更新划词窗口失败: %s", e)

        if self._selection_popup_should_be_blocked():
            self._refresh_selection_popup_blocked()
            return
        if action_name == "popup":
            self._show_selection_action_popup(text, int(x), int(y))
            return
        if action_name == "popup_ready":
            self._set_selection_action_popup_text(text)
            return
        self._open_selection_text_panel(text, int(x), int(y), action_name)

    def _handle_output_quick_ocr_text_result(self, text: str, elapsed: float = 0.0) -> bool:
        panel = getattr(self, "_pending_output_quick_ocr_panel", None)
        self._pending_output_quick_ocr_panel = None
        if panel is None:
            return False
        try:
            _ = panel.width()
        except (RuntimeError, AttributeError):
            return False
        submit = getattr(panel, "submit_ocr_text_for_qa", None)
        if not callable(submit):
            return False
        filled = bool(submit(str(text or "")))
        if filled:
            try:
                self._send_tray_notification("OCR识别完成", "识别结果已填入当前AI对话输入框。", 1800)
            except Exception:
                pass
        return filled

    def _on_ocr_panel_visibility_changed(self, visible: bool) -> None:
        try:
            QTimer.singleShot(0, self._refresh_selection_popup_blocked)
        except Exception:
            self._refresh_selection_popup_blocked()

        if bool(visible):
            # OCR 结果窗口显示时，去掉遮罩，方便用户拖动和查看
            try:
                self._close_selection_shade_overlay()
            except Exception:
                pass
            try:
                if self._region_overlay is not None:
                    self._region_overlay.hide()
            except Exception:
                pass
        else:
            # OCR 结果窗口关闭时，如果选区边框依然存在，则恢复遮罩
            if self._border_overlay is not None:
                try:
                    self._sync_selection_shade_for_border()
                except Exception:
                    pass

    def _is_ocr_panel_active(self) -> bool:
        if self._post_actions is not None:
            try:
                panel = self._post_actions._live_ocr_panel()
                if panel is not None and panel.isVisible():
                    return True
            except Exception:
                pass
        return False

    def _build_translator_page(self) -> QWidget:
        ai_btn = QPushButton("AI对话")
        ai_btn.setObjectName("BtnPrimary")
        ai_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ai_btn.setIcon(self._asset_icon("icon_ai_qa_white.svg"))
        ai_btn.setIconSize(QSize(22, 22))
        ai_btn.setMinimumSize(132, 42)
        ai_btn.setMaximumSize(132, 42)
        ai_btn.clicked.connect(self._open_manual_ai_panel)
        page = self._make_page("模型管理", action_widget=ai_btn)
        content_layout: QVBoxLayout = page._content_layout  # type: ignore[attr-defined]
        content_layout.setSpacing(10)
        translator_body = SettingsDialog._build_translator_tab(self)
        self._compact_child_layouts(translator_body)
        if translator_body.layout() is not None:
            translator_body.layout().setSpacing(10)
        for form in translator_body.findChildren(QFormLayout):
            form.setVerticalSpacing(12)
        content_layout.addWidget(translator_body)
        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 10, 0, 0)
        footer_layout.setSpacing(10)
        self._local_translation_service_switch = QCheckBox("本地API")
        self._local_translation_service_switch.setChecked(bool(self._translator.get("local_translation_service_enabled", False)))
        self._local_translation_service_switch.setToolTip("勾选后启动 DeepCat 本地 API 服务，提供翻译和问答的 OpenAI 兼容接口。")

        self._translator_footer_active = True
        self._local_translation_firewall_btn = SettingsDialog._create_local_translation_firewall_button(self)

        self._codex_config_switch = QCheckBox("配置Codex")
        self._codex_config_switch.setChecked(bool(self._translator.get("codex_config_enabled", False)))
        self._codex_config_switch.setToolTip("勾选后将自动配置本地API接口到Codex")

        self._prompt_settings_btn = QPushButton("提示词")
        self._prompt_settings_btn.setObjectName("BtnSmallPrimary")
        self._prompt_settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prompt_settings_btn.setStyleSheet(self._prompt_settings_btn.styleSheet() + " QPushButton::menu-indicator { image: none; width: 0px; }")

        SettingsDialog._style_translator_footer_button(self._prompt_settings_btn, 70)

        self._prompt_settings_btn.installEventFilter(self)
        self._prompt_settings_btn.clicked.connect(self._show_prompt_settings_popup)

        footer_layout.addWidget(self._local_translation_service_switch)
        footer_layout.addWidget(self._local_translation_firewall_btn)
        footer_layout.addWidget(self._codex_config_switch)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self._prompt_settings_btn)
        footer_layout.addSpacing(26)
        content_layout.addWidget(footer)
        content_layout.addStretch(1)
        return page

    def _reload_translator_page(self) -> None:
        if self._stack is None:
            return
        try:
            old_page = self._stack.widget(1)
            new_page = self._build_translator_page()
            self._stack.removeWidget(old_page)
            self._stack.insertWidget(1, new_page)
            old_page.deleteLater()
            self._sync_embedded_settings_controls()
        except Exception:
            get_logger().exception("Failed to reload translator page")

    def _position_translator_api_key_eye(self) -> None:
        if not hasattr(self, "_translator_api_key_eye"):
            return
        try:
            self._translator_api_key.setTextMargins(0, 0, 26, 0)
            h = max(1, int(self._translator_api_key.height()))
            size = max(18, min(22, h - 8))
            self._translator_api_key_eye.setGeometry(
                int(self._translator_api_key.width() - size - 6),
                int((h - size) / 2),
                int(size),
                int(size),
            )
        except Exception:
            pass

    def _selection_translate_setting_changed(self, enabled: bool) -> None:
        self._app_settings = self._current
        self._apply_selection_translate_listener(bool(enabled))

    def _apply_selection_translate_hotkey(self) -> None:
        qt_seq = self._selection_translate_hotkey_edit.keySequence().toString()
        new_hotkey = qt_to_pynput(qt_seq)
        if not str(qt_seq).strip():
            self._show_capture_status("划词翻译快捷键不能为空。", tone="warning")
            return
        if new_hotkey is None:
            new_hotkey = str(qt_seq).strip().lower()
        ui = dict(getattr(self._current, "ui", {}) or {})
        if str(new_hotkey) == str(ui.get("selection_translate_hotkey", "<ctrl>+<space>")):
            return
        self._current = update_ui_settings(selection_translate_hotkey=str(new_hotkey))
        self._selection_translate_hotkey_str = str(new_hotkey)
        if self._selection_translate_listener is not None:
            self._selection_translate_listener.set_hotkeys(str(new_hotkey), str(self._selection_popup_hotkey_str))
        self._sync_settings_after_change()
        self._show_capture_status(f"划词翻译快捷键已保存：{qt_seq}", tone="success")

    def _apply_selection_translate_enabled(self) -> None:
        SettingsDialog._apply_selection_translate_enabled(self)
        self._sync_settings_after_change()

    def _apply_ocr_translate_enabled(self) -> None:
        SettingsDialog._apply_ocr_translate_enabled(self)
        self._sync_settings_after_change()
