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
from deepcat.ui.main_window.search_logic import (
    first_search_match,
    highlight_search_html,
    search_display_model_name,
    search_highlight_terms,
    search_match_snippet,
)

QApplication = DynamicModuleAttribute("deepcat.ui.main_window.window", "QApplication")


class WindowSearchMixin:
    def _open_compact_list_window(self, page_type: str) -> None:
        feature_key = "clipboard_history" if page_type == "clipboard" else page_type
        if hasattr(self, "_feature_enabled") and not self._feature_enabled(feature_key):
            return
        if page_type == "later_read" and hasattr(self, "_later_read_enabled_now") and not self._later_read_enabled_now():
            return
        prop_name = f"_compact_window_{page_type}"
        compact_win = getattr(self, prop_name, None)
        if compact_win is not None:
            try:
                if compact_win.isVisible():
                    compact_win.hide()
                    return
                else:
                    try:
                        compact_win.load_data()
                    except Exception:
                        pass
                    compact_win.show()
                    compact_win.raise_()
                    compact_win.activateWindow()
                    return
            except (RuntimeError, AttributeError):
                compact_win = None

        from deepcat.ui.main_window.compact import _CompactListWindow
        compact_win = _CompactListWindow(self, page_type=page_type)
        setattr(self, prop_name, compact_win)
        compact_win.show()
        compact_win.raise_()
        compact_win.activateWindow()

    def _on_super_search_changed(self, text: str) -> None:
        if getattr(self, "_is_clearing_search", False):
            return
        keyword = text.strip()
        if not keyword:
            if hasattr(self, "_prev_page_index") and self._prev_page_index is not None:
                prev_idx = self._prev_page_index
                self._prev_page_index = None
                self._switch_page(prev_idx)
            else:
                self._switch_page(0)
            return

        current_idx = self._stack.currentIndex()
        if current_idx != self._SEARCH_PAGE_INDEX:
            self._prev_page_index = current_idx
            self._switch_page(self._SEARCH_PAGE_INDEX)

        self._update_super_search_results(keyword)

    def _update_super_search_results(self, keyword: str) -> None:
        if hasattr(self, "_search_title_label") and self._search_title_label is not None:
            self._search_title_label.setText(f"🔍 全局超级搜索结果 - '{keyword}'")
        self._search_clipboard(keyword)
        self._search_table_notes(keyword)
        self._search_later_read(keyword)
        self._search_ai_chat_history(keyword)

    def _on_ai_search_clicked(self) -> None:
        keyword = ""
        if hasattr(self, "_super_search") and self._super_search is not None:
            keyword = self._super_search.text().strip()

        if not keyword:
            self._send_tray_notification("提示", "请输入搜索词后再发起AI搜索", 1500)
            return

        btn = self._btn_ai_search
        pos = btn.mapToGlobal(QPoint(0, btn.height()))
        x = pos.x()
        y = pos.y()
        self._open_selection_text_panel(keyword, x, y, "ai_search")

    def _build_search_page(self) -> QWidget:
        # 创建一个 "AI 搜索" 按钮作为动作按钮传入 _make_page，保持和其它标签页按钮大小与风格一致
        self._btn_ai_search = QPushButton("AI搜索")
        self._btn_ai_search.setObjectName("BtnPrimary")
        self._btn_ai_search.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_ai_search.setIcon(self._asset_icon("icon_search_white.svg"))
        self._btn_ai_search.setIconSize(QSize(20, 20))
        self._btn_ai_search.setMinimumSize(132, 42)
        self._btn_ai_search.setMaximumSize(132, 42)
        self._btn_ai_search.clicked.connect(self._on_ai_search_clicked)

        # 使用统一的 _make_page 页面构造器，传入 action_widget
        page = self._make_page("全局超级搜索", action_widget=self._btn_ai_search)
        content_layout: QVBoxLayout = page._content_layout

        # 获取大标题 Label 并存入 self._search_title_label
        self._search_title_label = page.findChild(QLabel, "HeaderTitle")

        self._search_card_clipboard = self._create_search_category_card("📋 复制记录", lambda: self._switch_page(4))
        self._search_card_notes = self._create_search_category_card("📓 表格记事", lambda: self._switch_page(5))
        self._search_card_later = self._create_search_category_card("📖 稍后阅读", lambda: self._switch_page(3))
        self._search_card_ai_chat = self._create_search_category_card("🤖 AI 对话记录", self._open_manual_ai_panel)
        self._search_card_ai_chat.view_all_btn.setText("打开AI对话 →")

        content_layout.addWidget(self._search_card_clipboard)
        content_layout.addWidget(self._search_card_notes)
        content_layout.addWidget(self._search_card_later)
        content_layout.addWidget(self._search_card_ai_chat)
        content_layout.addStretch(1)
        return page

    def _create_search_category_card(self, title: str, on_view_all_clicked) -> QFrame:
        card = QFrame()
        card.setObjectName("SearchCard")
        card.setFrameShape(QFrame.Shape.NoFrame)
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet("""
            QFrame#SearchCard {
                background-color: #ffffff;
                border: none;
                border-radius: 12px;
            }
        """)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(10)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #1e293b;")
        header_layout.addWidget(title_lbl)
        header_layout.addStretch(1)

        view_all_btn = QPushButton("查看全部 →")
        view_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        view_all_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #3b82f6;
                font-size: 12px;
                font-weight: bold;
                padding: 0;
            }
            QPushButton:hover {
                color: #2563eb;
            }
        """)
        view_all_btn.clicked.connect(on_view_all_clicked)
        header_layout.addWidget(view_all_btn)
        card_layout.addWidget(header)

        items_widget = QWidget()
        items_layout = QVBoxLayout(items_widget)
        items_layout.setContentsMargins(0, 4, 0, 0)
        items_layout.setSpacing(6)

        card_layout.addWidget(items_widget)
        card.items_layout = items_layout
        card.view_all_btn = view_all_btn
        return card

    def _clear_super_search_from_empty(self) -> None:
        if hasattr(self, "_super_search") and self._super_search is not None:
            self._super_search.clear()

    def _add_search_empty_state(self, layout: QLayout, title: str, description: str) -> None:
        layout.addWidget(
            self._make_unified_empty_state(
                title,
                description,
                "清空搜索",
                self._clear_super_search_from_empty,
                compact=True,
            )
        )

    def _clear_layout(self, layout) -> None:
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            else:
                self._clear_layout(item.layout())

    def _search_clipboard(self, keyword: str) -> None:
        layout = self._search_card_clipboard.items_layout
        self._clear_layout(layout)

        records = []
        if hasattr(self, "_clipboard_history_page") and getattr(self._clipboard_history_page, "_database", None) is not None:
            db = self._clipboard_history_page._database
            try:
                records = db.search_records(keyword, limit=8)
            except Exception:
                pass

        if not records:
            self._search_card_clipboard.view_all_btn.setVisible(False)
            self._add_search_empty_state(
                layout,
                "没有匹配的复制记录",
                "换个关键词，或清空搜索。",
            )
            return

        self._search_card_clipboard.view_all_btn.setVisible(True)
        for r in records:
            try:
                content = r["content"]
                rec_id = r["id"]
                c_type = r["content_type"]
            except Exception:
                try:
                    content = r[1]
                    rec_id = r[0]
                    c_type = r[2]
                except Exception:
                    continue
            item_widget = self._create_clipboard_search_item_widget(content, rec_id, c_type)
            layout.addWidget(item_widget)

    def _create_clipboard_search_item_widget(self, content: str, rec_id: int, c_type: str) -> QWidget:
        w = QWidget()
        w.setObjectName("SearchItem")
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setStyleSheet("""
            QWidget#SearchItem {
                border-radius: 6px;
                background-color: #f8fafc;
            }
            QWidget#SearchItem:hover {
                background-color: rgba(59, 130, 246, 0.05);
            }
        """)
        layout = QHBoxLayout(w)
        layout.setContentsMargins(12, 8, 12, 8)

        txt_lbl = QLabel()
        clean_text = str(content).replace("\n", " ").replace("\r", " ").strip()
        if len(clean_text) > 75:
            clean_text = clean_text[:75] + "..."
        txt_lbl.setText(clean_text)
        txt_lbl.setStyleSheet("font-size: 13px; color: #334155;")
        txt_lbl.setToolTip(str(content)[:1000])
        txt_lbl.setMinimumWidth(10)
        layout.addWidget(txt_lbl, 1)

        badge_lbl = QLabel(c_type.upper() if c_type else "TEXT")
        badge_lbl.setStyleSheet("""
            background-color: #e2e8f0;
            color: #475569;
            font-size: 10px;
            font-weight: bold;
            border-radius: 4px;
            padding: 2px 6px;
        """)
        layout.addWidget(badge_lbl)

        copy_btn = QToolButton()
        copy_btn.setIcon(self._asset_icon("icon_action_copy.svg"))
        copy_btn.setToolTip("复制此内容")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet("QToolButton { border: none; background: transparent; } QToolButton:hover { background: rgba(15, 23, 42, 0.05); border-radius: 4px; }")
        copy_btn.clicked.connect(lambda: self._copy_text_from_search(content))

        jump_btn = QToolButton()
        jump_btn.setIcon(self._asset_icon("icon_action_paste.svg"))
        jump_btn.setToolTip("跳转到剪贴板页")
        jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        jump_btn.setStyleSheet("QToolButton { border: none; background: transparent; } QToolButton:hover { background: rgba(15, 23, 42, 0.05); border-radius: 4px; }")
        jump_btn.clicked.connect(lambda: self._jump_to_clipboard(content))

        layout.addWidget(copy_btn)
        layout.addWidget(jump_btn)
        return w

    def _copy_text_from_search(self, content: str) -> None:
        try:
            QApplication.clipboard().setText(str(content))
            self._send_tray_notification("已复制到剪贴板", "记录内容已成功复制！", 1500)
        except Exception:
            pass

    def _jump_to_clipboard(self, content: str) -> None:
        self._switch_page(4)
        if hasattr(self, "_clipboard_history_page") and getattr(self._clipboard_history_page, "search_input", None) is not None:
            clean_word = content.replace("\n", " ").strip()[:15]
            self._clipboard_history_page.search_input.setText(clean_word)

    def _create_later_search_item_widget(self, item: dict) -> QWidget:
        w = QWidget()
        w.setObjectName("SearchItem")
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setStyleSheet("""
            QWidget#SearchItem {
                border-radius: 6px;
                background-color: #f8fafc;
            }
            QWidget#SearchItem:hover {
                background-color: rgba(59, 130, 246, 0.05);
            }
        """)
        layout = QHBoxLayout(w)
        layout.setContentsMargins(12, 8, 12, 8)

        title = item.get("title", "")
        url = item.get("url", "")
        display_text = title if title and title != url else url
        if len(display_text) > 75:
            display_text = display_text[:75] + "..."

        txt_lbl = QLabel()
        txt_lbl.setText(display_text)
        txt_lbl.setStyleSheet("font-size: 13px; color: #334155;")
        txt_lbl.setToolTip(f"{title}\n{url}")
        txt_lbl.setMinimumWidth(10)
        layout.addWidget(txt_lbl, 1)

        open_btn = QToolButton()
        open_btn.setIcon(self._asset_icon("icon_action_play.svg"))
        open_btn.setToolTip("用默认浏览器打开链接")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setStyleSheet("QToolButton { border: none; background: transparent; } QToolButton:hover { background: rgba(15, 23, 42, 0.05); border-radius: 4px; }")
        open_btn.clicked.connect(lambda: self._open_url_from_search(url))

        copy_btn = QToolButton()
        copy_btn.setIcon(self._asset_icon("icon_action_copy.svg"))
        copy_btn.setToolTip("复制链接")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet("QToolButton { border: none; background: transparent; } QToolButton:hover { background: rgba(15, 23, 42, 0.05); border-radius: 4px; }")
        copy_btn.clicked.connect(lambda: self._copy_text_from_search(url))

        jump_btn = QToolButton()
        jump_btn.setIcon(self._asset_icon("icon_action_paste.svg"))
        jump_btn.setToolTip("跳转到稍后阅读列表")
        jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        jump_btn.setStyleSheet("QToolButton { border: none; background: transparent; } QToolButton:hover { background: rgba(15, 23, 42, 0.05); border-radius: 4px; }")
        jump_btn.clicked.connect(lambda: self._switch_page(3))

        layout.addWidget(open_btn)
        layout.addWidget(copy_btn)
        layout.addWidget(jump_btn)
        return w

    def _open_url_from_search(self, url: str) -> None:
        if not url:
            return
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def _search_ai_chat_history(self, keyword: str) -> None:
        layout = self._search_card_ai_chat.items_layout
        self._clear_layout(layout)

        records = []
        store = None
        try:
            store = TranslationHistoryStore()
            records = store.get_record_summaries(
                limit=8,
                offset=0,
                task_type_filter=None,
                search_query=keyword,
                pinned_first=True,
                text_limit=120,
            )
        except Exception:
            records = []
        finally:
            try:
                if store is not None:
                    store.close()
            except Exception:
                pass

        if not records:
            self._search_card_ai_chat.view_all_btn.setVisible(False)
            self._add_search_empty_state(
                layout,
                "没有匹配的 AI 对话记录",
                "换个关键词，或清空搜索。",
            )
            return

        self._search_card_ai_chat.view_all_btn.setVisible(True)
        for record in records:
            layout.addWidget(self._create_ai_chat_search_item_widget(record, keyword))

    @staticmethod
    def _search_highlight_terms(keyword: str) -> list[str]:
        return search_highlight_terms(keyword)

    @staticmethod
    def _first_search_match(text: str, terms: list[str]) -> tuple[int, int]:
        return first_search_match(text, terms)

    @classmethod
    def _search_match_snippet(cls, record: dict, keyword: str, limit: int = 64) -> str:
        return search_match_snippet(record, keyword, limit)

    @classmethod
    def _highlight_search_html(cls, text: str, keyword: str) -> str:
        return highlight_search_html(text, keyword)

    @staticmethod
    def _search_display_model_name(record: dict) -> str:
        return search_display_model_name(record)

    def _create_ai_chat_search_item_widget(self, record: dict, keyword: str = "") -> QWidget:
        w = QWidget()
        w.setObjectName("SearchItem")
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setStyleSheet("""
            QWidget#SearchItem {
                border-radius: 6px;
                background-color: #f8fafc;
            }
            QWidget#SearchItem:hover {
                background-color: rgba(59, 130, 246, 0.05);
            }
        """)
        layout = QHBoxLayout(w)
        layout.setContentsMargins(12, 8, 12, 8)

        title = str(record.get("title") or record.get("source_text") or "未命名对话").strip()
        title = " ".join(title.split())
        if len(title) > 42:
            title = title[:42] + "..."

        model_name = self._search_display_model_name(record)
        snippet = self._search_match_snippet(record, keyword, limit=64)

        task_type = str(record.get("task_type") or "").strip()
        task_label = "翻译" if task_type == "translate" else "对话"
        meta_parts = []
        if model_name:
            meta_parts.append(
                "<span style='color: #64748b;'>模型: "
                f"{self._highlight_search_html(model_name, keyword)}</span>"
            )
        meta_parts.append(
            "<span style='color: #64748b;'>"
            f"{self._highlight_search_html(snippet, keyword)}</span>"
        )
        display_html = (
            f"<b>[{html.escape(task_label)}] {self._highlight_search_html(title, keyword)}</b>"
            f"  {'  '.join(meta_parts)}"
        )

        txt_lbl = QLabel()
        txt_lbl.setText(display_html)
        txt_lbl.setTextFormat(Qt.TextFormat.RichText)
        txt_lbl.setStyleSheet("font-size: 13px; color: #334155;")
        txt_lbl.setMinimumWidth(10)
        tooltip_parts = [title]
        if model_name:
            tooltip_parts.append(f"模型: {model_name}")
        tooltip_parts.append(snippet)
        txt_lbl.setToolTip("\n".join(tooltip_parts))
        layout.addWidget(txt_lbl, 1)

        copy_btn = QToolButton()
        copy_btn.setIcon(self._asset_icon("icon_action_copy.svg"))
        copy_btn.setToolTip("复制 AI 回复")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet("QToolButton { border: none; background: transparent; } QToolButton:hover { background: rgba(15, 23, 42, 0.05); border-radius: 4px; }")
        copy_btn.clicked.connect(
            lambda: self._copy_ai_chat_result(
                int(record.get("id", 0) or 0),
                str(record.get("result_text") or record.get("source_text") or ""),
            )
        )

        jump_btn = QToolButton()
        jump_btn.setIcon(self._asset_icon("icon_action_paste.svg"))
        jump_btn.setToolTip("打开这条 AI 对话")
        jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        jump_btn.setStyleSheet("QToolButton { border: none; background: transparent; } QToolButton:hover { background: rgba(15, 23, 42, 0.05); border-radius: 4px; }")
        jump_btn.clicked.connect(lambda: self._jump_to_ai_chat_history(int(record.get("id", 0) or 0)))

        layout.addWidget(copy_btn)
        layout.addWidget(jump_btn)
        return w

    def _copy_ai_chat_result(self, record_id: int, fallback_text: str) -> None:
        text = str(fallback_text or "")
        store = None
        if record_id > 0:
            try:
                store = TranslationHistoryStore()
                record = store.get_record(int(record_id))
                if record:
                    text = str(record.get("result_text") or record.get("source_text") or text)
            except Exception:
                pass
            finally:
                try:
                    if store is not None:
                        store.close()
                except Exception:
                    pass
        self._copy_text_from_search(text)

    def _jump_to_ai_chat_history(self, record_id: int) -> None:
        if record_id <= 0:
            return
        self._open_manual_ai_panel()
        panel = getattr(self, "_selection_translate_panel", None)
        if panel is None:
            return
        try:
            if hasattr(panel, "_load_history_record_into_chat"):
                panel._load_history_record_into_chat(int(record_id))
        except Exception:
            pass
