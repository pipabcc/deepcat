from __future__ import annotations

import copy
import time
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QPushButton,
    QHBoxLayout,
    QFrame,
    QWidget,
    QGraphicsDropShadowEffect,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    QToolButton,
    QScrollArea,
    QSizePolicy,
)
from deepcat.settings_store import (
    AppSettings,
    DEFAULT_EXPLAIN_PROMPT,
    DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
    DEFAULT_REPLY_PROMPT,
    DEFAULT_REPLY_PROMPT_BUTTON_NAME,
    DEFAULT_SUMMARY_PROMPT,
    DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
    DEFAULT_AI_SEARCH_PROMPT,
    DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
    infer_translator_model_type,
    load_settings,
    normalize_translator_settings,
    update_settings,
    update_ui_settings,
)
from deepcat.translation_history_store import TranslationHistoryStore
from deepcat.ui.app_icon import create_app_icon
from deepcat.ui.chat_bubbles import BubbleListView
from deepcat.ui.clipboard_formats import clean_clipboard_text, copy_markdown_to_clipboard, copy_plain_text_to_clipboard
from deepcat.ui.popup_behavior import POPUP_EXACT_WIDTH_PROPERTY, set_disable_global_tooltip
from deepcat.ui.timer_scope import single_shot_scoped
from deepcat.utils.logger import get_logger
from PyQt6.QtWidgets import QFrame, QPushButton
from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame
from PyQt6.QtGui import QIcon, QColor, QFont
from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton

from deepcat.ui.post_capture_actions._shared import (
    CHAT_INPUT_WATERMARK_TEXT,
    PROMPT_ACTION_POPUP_WIDTH,
    _AI_CHAT_CONTEXT_RECENT_ROUNDS,
    _AI_CHAT_CONTEXT_WARNING_MESSAGES,
    _AI_CHAT_CONTEXT_WARNING_MESSAGES_TOKENS,
    _AI_CHAT_CONTEXT_WARNING_TOKENS,
    _AI_CHAT_DRAFTS_UI_KEY,
    _AI_DOTS_ANIMATION_ENABLED,
    _AI_GEOMETRY_TRACE_ENABLED,
    _ai_history_debug_logger,
    logger,
)
from deepcat.ui.post_capture_actions.helpers import (
    _get_ai_geometry_logger,
    _group_translator_model_menu_items,
    _history_text_has_image,
    _log_ai_history_debug,
    _normalize_ai_log_value,
    _point_to_log_value,
    _preview_log_text,
    _rect_to_log_value,
    _size_to_log_value,
    _trace_ai_panel,
    _translator_model_search_text,
)
from deepcat.ui.post_capture_actions.model_menus import OcrGenericMenuPopup, _OcrModelMenuPopup
from deepcat.ui.post_capture_actions.combos import CollapseArrowButton, UpwardComboBox
from deepcat.ui.post_capture_actions.tooltips import SmoothToolTip
from deepcat.ui.post_capture_actions.workers import (
    FileAttachmentPrepareWorker,
    ImageAttachmentPrepareWorker,
    ImaNoteSearchWorker,
    OcrTranslationWorker,
)
from deepcat.ui.thread_utils import request_thread_cancel
from deepcat.ui.post_capture_actions.text_widgets import PremiumTextEdit, RoundedTextEditContainer, _ThinkingCard
from deepcat.ui.post_capture_actions.title_bar import OcrTitleBar, _OcrTitleBarButton, _load_title_owl_pixmap
from deepcat.ui.post_capture_actions.quick_actions import HoverIconButton, OutputQuickActionBar
from deepcat.ui.post_capture_actions.ai_history import AIChatHistorySidebar, _ai_history_search_terms, _first_search_match
from deepcat.ui.post_capture_actions.attachments import _AttachmentPreviewChip, _refresh_attachment_preview_bar_if_available
from deepcat.ui.post_capture_actions.note_integration import GroupedSmoothNoteIntegrationDialog


from deepcat.ui.module_compat import DynamicModuleAttribute

QTimer = DynamicModuleAttribute("deepcat.ui.post_capture_actions.text_panel", "QTimer")


def _ocr_text_panel_class():
    from deepcat.ui.post_capture_actions.text_panel import OcrTextPanel

    return OcrTextPanel


class TextPanelHistoryMixin:
    def _save_to_history(
        self,
        task: str,
        result_text: str,
        model_name: str,
        elapsed: float,
        *,
        success: bool = True,
    ) -> None:
        """将翻译/问答结果保存到历史数据库。"""
        store = getattr(self, "_history_store", None)
        if store is None:
            return
        try:
            # 映射 button_task 到 task_type
            task_type_map = {
                "translate": "translate",
                "qa": "qa",
                "reply_prompt": "reply_prompt",
                "optimize_prompt": "ai_search",
                "ai_search": "ai_search",
            }
            task_type = task_type_map.get(task, task)

            # 判断是否为多轮对话
            is_chat = bool(getattr(self, "_is_chatting", False) and hasattr(self, "_chat_history") and self._chat_history)

            if is_chat:
                import json
                # 获取第一轮问题的文本作为标题原文
                first_msg = self._chat_history[0]
                source_text = str(first_msg.get("display_content") or first_msg.get("content") or "").strip()
                if not source_text:
                    source_text = "多轮对话会话"

                # 最后一轮 AI 输出
                last_msg = self._chat_history[-1]
                res_text = str(last_msg.get("content") or result_text or "").strip()

                # 序列化整个对话历史
                prompt_text = json.dumps(self._chat_history, ensure_ascii=False)

                session_id = getattr(self, "_current_session_record_id", None)
                if session_id is not None:
                    # 更新已有记录
                    store.update_record(
                        record_id=session_id,
                        source_text=source_text,
                        result_text=res_text,
                        elapsed_secs=float(elapsed),
                        prompt_text=prompt_text,
                        is_success=bool(success),
                    )
                else:
                    # 首次交互，新增记录
                    new_id = store.add_record(
                        task_type=task_type,
                        source_text=source_text,
                        result_text=res_text,
                        model_name=str(model_name or ""),
                        source_lang=self._source_lang.currentText(),
                        target_lang=self._target_lang.currentText(),
                        elapsed_secs=float(elapsed),
                        prompt_text=prompt_text,
                        is_success=bool(success),
                    )
                    self._current_session_record_id = new_id
            else:
                # 常规单次记录
                source_text = str(self._editor.toPlainText() or "").strip()
                if not source_text:
                    return
                store.add_record(
                    task_type=task_type,
                    source_text=source_text,
                    result_text=str(result_text or ""),
                    model_name=str(model_name or ""),
                    source_lang=self._source_lang.currentText(),
                    target_lang=self._target_lang.currentText(),
                    elapsed_secs=float(elapsed),
                    prompt_text="",
                    is_success=bool(success),
                )

            # 自动清理旧记录
            store.auto_cleanup(500)

            # 如果历史侧栏可见，实时刷新同步它
            if hasattr(self, "_history_sidebar") and self._history_sidebar is not None and self._history_sidebar.isVisible():
                self._history_sidebar.refresh()
                self._history_sidebar.select_record(getattr(self, "_current_session_record_id", None))
        except Exception:
            pass

    def _toggle_history(self) -> None:
        """切换显示/隐藏内嵌历史侧栏。"""
        if getattr(self, "_history_sidebar", None) is None:
            return
        if self._history_showing:
            self._hide_history()
        else:
            self._show_history()

    def _show_history(self, *, persist_state: bool = True) -> None:
        """显示微信式左侧历史记录栏。"""
        sidebar = getattr(self, "_history_sidebar", None)
        if sidebar is None:
            return
        if (
            not getattr(self, "_history_showing", False)
            and not getattr(self, "_is_maximized", False)
            and self.isVisible()
        ):
            self._pre_history_geometry = QRect(self.geometry())
        elif not getattr(self, "_history_showing", False):
            self._pre_history_geometry = None
        if bool(getattr(self, "_panel_collapsed", False)):
            self._toggle_panel_collapse(False)
        self._history_transitioning = True
        self.setUpdatesEnabled(False)
        try:
            self._history_showing = True
            self._sync_history_layout_edges()
            self._watermark.hide()
            self._translation_info.hide()
            self._ensure_history_output_area()
            sidebar.show()
            sidebar.refresh()
            sidebar.select_record(getattr(self, "_current_session_record_id", None))
            self._reposition()
            self._sync_output_quick_action_bar_visibility()
            self._btn_history.setText("历史")
            self._btn_history.setToolTip("收起历史记录")
            if persist_state and not bool(getattr(self, "_history_only_mode", False)):
                _ocr_text_panel_class()._save_history_sidebar_open_to_settings(True)
        finally:
            self._history_transitioning = False
            self.setUpdatesEnabled(True)
            self.update()
            QTimer.singleShot(0, self._sync_output_quick_action_bar_visibility)

    def _hide_history(self, *, persist_state: bool = True) -> None:
        """隐藏内嵌历史记录栏，返回纯对话视图。"""
        if getattr(self, "_history_only_mode", False):
            self.close()
            return
        if getattr(self, "_is_collapsed", False):
            self._toggle_collapse(False)
        self._history_transitioning = True
        self.setUpdatesEnabled(False)
        try:
            self._history_showing = False
            sidebar = getattr(self, "_history_sidebar", None)
            if sidebar is not None:
                sidebar.hide()
            self._sync_history_layout_edges()
            self._restore_output_area_after_history()
            self._sync_input_watermark_visibility()
            if not self._bubble_view.is_empty() and not getattr(self, "_history_showing", False):
                self._translation_info.show()
            target_geo = getattr(self, "_pre_history_geometry", None)
            if target_geo is not None and target_geo.isValid() and not getattr(self, "_is_maximized", False):
                self._pre_history_geometry = None
                if getattr(self, "_chat_history", None):
                    # 从历史侧栏点开记录后，当前窗口已经是完整对话态。
                    # 收起侧栏只应缩窄宽度，不能再用打开侧栏前的小窗口底边做锚点。
                    self._reposition()
                else:
                    self._reposition(
                        keep_bottom_y=int(target_geo.y() + target_geo.height()),
                    )
            else:
                self._reposition()
            self._btn_history.setText("历史")
            self._btn_history.setToolTip("查看翻译/问答历史记录")
            self._sync_output_quick_action_bar_visibility()
            if persist_state:
                _ocr_text_panel_class()._save_history_sidebar_open_to_settings(False)
        finally:
            self._history_transitioning = False
            self.setUpdatesEnabled(True)
            self.update()

    def _should_show_title_bar(self) -> bool:
        """仅在输出区或历史侧边栏展开后显示标题栏。"""
        if bool(getattr(self, "_panel_collapsed", False)):
            return False
        if bool(getattr(self, "_history_showing", False)):
            return True
        bubble_view = getattr(self, "_bubble_view", None)
        try:
            return bool(bubble_view is not None and bubble_view.isVisible())
        except Exception:
            return False

    def _position_floating_window_buttons(self) -> None:
        minimize_btn = getattr(self, "_floating_minimize_btn", None)
        close_btn = getattr(self, "_floating_close_btn", None)
        if minimize_btn is None or close_btn is None:
            return
        try:
            margin = 12
            gap = 6
            y = 12
            close_x = max(margin, self.width() - margin - close_btn.width())
            min_x = max(margin, close_x - gap - minimize_btn.width())
            minimize_btn.move(min_x, y)
            close_btn.move(close_x, y)
            minimize_btn.raise_()
            close_btn.raise_()
        except Exception:
            pass

    def _show_floating_window_buttons(self) -> None:
        if bool(getattr(self, "_panel_collapsed", False)) or self._should_show_title_bar():
            self._hide_floating_window_buttons(force=True)
            return
        self._position_floating_window_buttons()
        for btn in (getattr(self, "_floating_minimize_btn", None), getattr(self, "_floating_close_btn", None)):
            if btn is None:
                continue
            try:
                btn.show()
                btn.raise_()
            except Exception:
                pass

    def _input_hover_region_contains_cursor(self) -> bool:
        widgets = [
            getattr(self, "_editor_container", None),
            getattr(self, "_editor", None),
            getattr(getattr(self, "_editor", None), "viewport", lambda: None)(),
            getattr(self, "_floating_minimize_btn", None),
            getattr(self, "_floating_close_btn", None),
        ]
        global_pos = QCursor.pos()
        for widget in widgets:
            if widget is None or not widget.isVisible():
                continue
            try:
                if widget.rect().contains(widget.mapFromGlobal(global_pos)):
                    return True
            except Exception:
                pass
        return False

    def _hide_floating_window_buttons(self, *, force: bool = False) -> None:
        if not force and self._input_hover_region_contains_cursor():
            return
        for btn in (getattr(self, "_floating_minimize_btn", None), getattr(self, "_floating_close_btn", None)):
            if btn is None:
                continue
            try:
                btn.hide()
            except Exception:
                pass

    def _hide_floating_window_buttons_if_outside_input(self) -> None:
        self._hide_floating_window_buttons(force=False)

    def _refresh_title_bar_dependent_style(self, widget: Optional[QWidget], *, title_bar_hidden: bool) -> None:
        if widget is None:
            return
        try:
            widget.setProperty("titleBarHidden", bool(title_bar_hidden))
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
        except Exception:
            pass

    def _sync_title_bar_visibility(self) -> None:
        if bool(getattr(self, "_history_record_switching", False)):
            return
        title_shell = getattr(self, "_title_bar_shell", None)
        if title_shell is None:
            return
        show_title = self._should_show_title_bar()
        try:
            title_shell.setVisible(show_title)
            if show_title:
                self._hide_floating_window_buttons(force=True)
            if show_title:
                title_bar = getattr(self, "_title_bar", None)
                title_label = getattr(title_bar, "title_label", None)
                for widget in (title_shell, title_bar, title_label):
                    if widget is not None:
                        widget.update()

                def repaint_title_area() -> None:
                    for widget in (title_shell, title_bar, title_label):
                        try:
                            if widget is not None:
                                widget.repaint()
                        except RuntimeError:
                            pass

                QTimer.singleShot(0, repaint_title_area)
        except Exception:
            pass

        title_bar_hidden = not show_title
        self._refresh_title_bar_dependent_style(
            getattr(self, "_editor_container", None),
            title_bar_hidden=title_bar_hidden,
        )
        self._refresh_title_bar_dependent_style(
            getattr(self, "_bottom_row_widget", None),
            title_bar_hidden=title_bar_hidden,
        )

        for layout in (
            getattr(self, "_content_stack", None).layout() if getattr(self, "_content_stack", None) is not None else None,
            getattr(self, "_view_stack", None).layout() if getattr(self, "_view_stack", None) is not None else None,
            self.layout(),
        ):
            if layout is None:
                continue
            try:
                layout.invalidate()
            except Exception:
                pass

    def _restore_title_bar_after_window_restore(self) -> None:
        """还原无边框窗口后，修复 Qt/DWM 偶发留下的内部标题栏坏状态。"""
        view_stack = getattr(self, "_view_stack", None)
        collapsed_widget = getattr(self, "_collapsed_widget", None)
        if bool(getattr(self, "_panel_collapsed", False)):
            try:
                if view_stack is not None:
                    view_stack.hide()
                if collapsed_widget is not None:
                    collapsed_widget.show()
                self._sync_title_bar_visibility()
                self._activate_panel_layouts()
            except Exception:
                pass
            return

        try:
            if view_stack is not None:
                view_stack.show()
        except Exception:
            pass

        show_title = False
        try:
            show_title = bool(self._should_show_title_bar())
        except Exception:
            show_title = False

        title_shell = getattr(self, "_title_bar_shell", None)
        title_bar = getattr(self, "_title_bar", None)
        if show_title:
            for widget in (title_shell, title_bar):
                if widget is None:
                    continue
                try:
                    if hasattr(widget, "maximumHeight") and hasattr(widget, "setMaximumHeight"):
                        try:
                            if int(widget.maximumHeight()) < 30:
                                widget.setMaximumHeight(16777215)
                        except Exception:
                            pass
                    if hasattr(widget, "setMinimumHeight"):
                        widget.setMinimumHeight(30)
                    if widget is title_bar and hasattr(widget, "setFixedHeight"):
                        widget.setFixedHeight(30)
                    widget.show()
                    if hasattr(widget, "updateGeometry"):
                        widget.updateGeometry()
                    widget.update()
                except Exception:
                    pass

        for callback_name in (
            "_sync_title_bar_visibility",
            "_activate_panel_layouts",
            "_update_top_seam_cover",
            "_update_interaction_seam_covers",
            "_position_floating_window_buttons",
        ):
            callback = getattr(self, callback_name, None)
            if not callable(callback):
                continue
            try:
                callback()
            except Exception:
                pass

    def _schedule_title_bar_restore_check(self) -> None:
        for delay_ms in (0, 80):
            def restore_later(panel=self) -> None:
                try:
                    panel._restore_title_bar_after_window_restore()
                except RuntimeError:
                    pass

            try:
                QTimer.singleShot(delay_ms, restore_later)
            except Exception:
                pass

    def _sync_history_layout_edges(self) -> None:
        history_open = bool(getattr(self, "_history_showing", False))
        self._sync_title_bar_visibility()
        title_shell_layout = self._title_bar_shell.layout() if hasattr(self, "_title_bar_shell") else None
        if title_shell_layout is not None:
            title_shell_layout.setContentsMargins(0, 0, 0, 0)
        inter_layout = self._interaction_container.layout() if hasattr(self, "_interaction_container") else None
        if inter_layout is not None:
            inter_layout.setContentsMargins(0, 0, 0, 0)
        bottom_row = getattr(self, "_bottom_row_widget", None)
        if bottom_row is not None:
            bottom_row.setProperty("historyOpen", bool(history_open))
            try:
                bottom_row.style().unpolish(bottom_row)
                bottom_row.style().polish(bottom_row)
                bottom_row.update()
            except Exception:
                pass
        bottom_layout = getattr(self, "_bottom_row_layout", None)
        if bottom_layout is not None:
            bottom_layout.setContentsMargins(0 if history_open else 6, 6, 12, 6)

    def _history_sidebar_width(self) -> int:
        sidebar = getattr(self, "_history_sidebar", None)
        if sidebar is None:
            return 280
        try:
            return int(sidebar.width() or sidebar.sizeHint().width() or 280)
        except Exception:
            return 280

    def _resize_for_history_sidebar(self, *, show: bool) -> None:
        return

    def _ensure_history_output_area(self) -> None:
        if self._bubble_view.isVisible():
            self._history_forced_output_area = False
            return
        self._history_forced_output_area = True
        try:
            self._bubble_view.clear()
            self._bubble_view.show()
            self._bubble_view.setMinimumHeight(360)
            self._editor_container.setMinimumHeight(120)
            self._editor_container.setMaximumHeight(120)
            sync_title_bar = getattr(self, "_sync_title_bar_visibility", None)
            if callable(sync_title_bar):
                sync_title_bar()
        except Exception:
            pass

    def _reset_to_initial_input_view(self) -> None:
        self._history_showing = False
        self._history_forced_output_area = False
        self._preserve_cleared_output_area = False
        sidebar = getattr(self, "_history_sidebar", None)
        if sidebar is not None:
            try:
                sidebar.hide()
            except Exception:
                pass
        try:
            self._bubble_view.clear()
            self._bubble_view.hide()
            self._bubble_view.setMinimumHeight(0)
            self._bubble_view.setMaximumHeight(16777215)
            self._editor_container.setMinimumHeight(0)
            self._editor_container.setMaximumHeight(16777215)
        except Exception:
            pass
        self._sync_history_layout_edges()
        self._sync_input_watermark_visibility()
        self._sync_output_quick_action_bar_visibility()
        if hasattr(self, "_btn_history"):
            self._btn_history.setText("历史")
            self._btn_history.setToolTip("查看翻译/问答历史记录")

    def _restore_output_area_after_history(self) -> None:
        if not bool(getattr(self, "_history_forced_output_area", False)):
            return
        if getattr(self, "_is_chatting", False) or not self._bubble_view.is_empty():
            self._history_forced_output_area = False
            return
        try:
            self._bubble_view.clear()
            self._bubble_view.hide()
            self._bubble_view.setMinimumHeight(0)
            self._editor_container.setMinimumHeight(0)
            self._editor_container.setMaximumHeight(16777215)
            self._sync_title_bar_visibility()
        except Exception:
            pass
        self._history_forced_output_area = False
        self._sync_output_quick_action_bar_visibility()

    def _start_new_chat_from_sidebar(self) -> None:
        self._save_current_chat_draft(show_feedback=True)
        self._clear_chat_history()
        self._restore_chat_draft("new", show_feedback=True)
        if getattr(self, "_history_showing", False):
            self._ensure_history_output_area()
            self._reposition()
        try:
            self._history_sidebar.select_record(None)
        except Exception:
            pass
        try:
            self._editor.setFocus(Qt.FocusReason.OtherFocusReason)
        except Exception:
            pass

    def _on_history_sidebar_records_deleted(self, record_ids: list[int]) -> None:
        try:
            deleted_ids = {int(record_id) for record_id in record_ids}
        except Exception:
            deleted_ids = set()
        current_id = getattr(self, "_current_session_record_id", None)
        if current_id is None or int(current_id) not in deleted_ids:
            return
        self._clear_chat_history()
        if getattr(self, "_history_showing", False):
            self._ensure_history_output_area()
            self._reposition()

    def _history_search_match_message_index(
        self,
        messages: list[dict],
        query: str,
        record: Optional[dict] = None,
    ) -> int:
        terms = _ai_history_search_terms(query)
        if not terms:
            return -1

        def matches(value: object) -> bool:
            text = _ocr_text_panel_class()._message_text_for_context_estimate(self, value)
            if not text:
                return False
            start, _length = _first_search_match(text, terms)
            return start >= 0

        def first_role_index(role_name: str) -> int:
            for idx, msg in enumerate(messages or []):
                if isinstance(msg, dict) and str(msg.get("role", "") or "") == role_name:
                    return idx
            return -1

        for idx, msg in enumerate(messages or []):
            if not isinstance(msg, dict):
                continue
            candidates: list[object] = [
                msg.get("display_content", ""),
                msg.get("content", ""),
                msg.get("regenerate_text", ""),
                msg.get("model_name", ""),
            ]
            if any(matches(candidate) for candidate in candidates):
                return idx

        if isinstance(record, dict):
            user_index = first_role_index("user")
            assistant_index = first_role_index("assistant")
            if matches(AIChatHistorySidebar._display_title(record)) or matches(record.get("source_text", "")):
                return user_index if user_index >= 0 else 0
            if matches(record.get("model_name", "")) or matches(record.get("result_text", "")):
                return assistant_index if assistant_index >= 0 else max(0, user_index)
            if matches(record.get("prompt_text", "")):
                return user_index if user_index >= 0 else 0
        return -1

    def _scroll_to_history_search_match(self, message_index: int) -> None:
        try:
            target_index = int(message_index)
        except Exception:
            return
        if target_index < 0:
            return
        view = getattr(self, "_bubble_view", None)
        if view is None or not hasattr(view, "scroll_to_message_index"):
            return

        def apply_scroll() -> None:
            try:
                view.scroll_to_message_index(target_index, center=True)
            except TypeError:
                try:
                    view.scroll_to_message_index(target_index)
                except Exception:
                    pass
            except RuntimeError:
                return
            except Exception:
                pass

        apply_scroll()
        if not isinstance(view, QWidget):
            return
        for delay_ms in (0, 80, 180, 420):
            QTimer.singleShot(int(delay_ms), apply_scroll)

    def _load_history_record_into_chat(self, record_id: int, search_query: str = "") -> None:
        store = getattr(self, "_history_store", None)
        if store is None:
            return
        search_query = " ".join(str(search_query or "").split())
        self._save_current_chat_draft(show_feedback=True)
        try:
            record = store.get_record(int(record_id))
        except Exception:
            record = None
        if not record:
            return

        _log_ai_history_debug(
            "[AIHistory] load_record.begin id=%s task=%s source_len=%s result_len=%s prompt_len=%s "
            "result_has_image=%s prompt_has_image=%s result_preview=%s prompt_preview=%s",
            record.get("id", record_id),
            record.get("task_type", ""),
            len(str(record.get("source_text", "") or "")),
            len(str(record.get("result_text", "") or "")),
            len(str(record.get("prompt_text", "") or "")),
            _history_text_has_image(record.get("result_text", "")),
            _history_text_has_image(record.get("prompt_text", "")),
            _preview_log_text(record.get("result_text", "")),
            _preview_log_text(record.get("prompt_text", "")),
        )
        messages = self._messages_from_history_record(record)
        _log_ai_history_debug(
            "[AIHistory] load_record.messages id=%s count=%s summary=%s",
            record.get("id", record_id),
            len(messages or []),
            [
                {
                    "role": str(msg.get("role", "") or ""),
                    "content_len": len(str(msg.get("content", "") or "")),
                    "display_len": len(str(msg.get("display_content", "") or "")),
                    "content_has_image": _history_text_has_image(msg.get("content", "")),
                    "display_has_image": _history_text_has_image(msg.get("display_content", "")),
                    "preview": _preview_log_text(msg.get("display_content") or msg.get("content") or "", 80),
                }
                for msg in (messages or [])
                if isinstance(msg, dict)
            ],
        )
        if not messages:
            return

        search_match_index = _ocr_text_panel_class()._history_search_match_message_index(self, messages, search_query, record)
        self._discard_worker_for_clear_chat()
        self._chat_history = messages
        self._current_session_record_id = int(record.get("id", record_id) or record_id)
        self._is_chatting = True
        _ocr_text_panel_class()._reset_context_management_state(self)
        self._history_forced_output_area = False
        self._clear_editor_silently()
        self._translation_info.clear()
        self._translation_info.hide()
        if hasattr(self, "_btn_qa"):
            self._btn_qa.setText("发送")
            self._btn_qa.setToolTip("发送追问")
        self._history_record_switching = True
        try:
            self._render_chat_history(is_streaming=False)
        finally:
            self._history_record_switching = False
        self._restore_chat_draft(str(self._current_session_record_id), show_feedback=True)
        try:
            self._history_sidebar.select_record(self._current_session_record_id)
        except Exception:
            pass
        if search_match_index >= 0:
            _ocr_text_panel_class()._scroll_to_history_search_match(self, search_match_index)

    def _messages_from_history_record(self, record: dict) -> list[dict]:
        record_task_type = "translate" if str(record.get("task_type", "") or "").strip() == "translate" else "qa"
        prompt_text = str(record.get("prompt_text", "") or "").strip()
        result_text = str(record.get("result_text", "") or "").strip()
        if prompt_text.startswith("[") and prompt_text.endswith("]"):
            try:
                loaded = json.loads(prompt_text)
                if isinstance(loaded, list):
                    messages: list[dict] = []
                    for item in loaded:
                        if not isinstance(item, dict):
                            continue
                        role = str(item.get("role", "") or "").strip()
                        if role not in {"user", "assistant"}:
                            continue
                        content = str(item.get("content", "") or "")
                        msg = {"role": role, "content": content, "task_type": str(item.get("task_type", "") or "").strip() or record_task_type}
                        if "display_content" in item:
                            msg["display_content"] = str(item.get("display_content", "") or "")
                        if "regenerate_text" in item:
                            msg["regenerate_text"] = str(item.get("regenerate_text", "") or "")
                        elif role == "user" and record_task_type == "translate":
                            msg["regenerate_text"] = str(item.get("display_content", "") or content)
                        for key in ("model_name", "created_at", "elapsed", "reply_tokens", "total_tokens"):
                            if key in item:
                                msg[key] = item.get(key)
                        pinned_value = item.get("context_pinned", False)
                        if pinned_value is True or str(pinned_value).strip().lower() in {"1", "true", "yes"}:
                            msg["context_pinned"] = True
                        messages.append(msg)
                    if messages:
                        assistant_messages = [msg for msg in messages if msg.get("role") == "assistant"]
                        _log_ai_history_debug(
                            "[AIHistory] parse_json_messages count=%s assistant_count=%s result_len=%s result_has_image=%s",
                            len(messages),
                            len(assistant_messages),
                            len(result_text),
                            _history_text_has_image(result_text),
                        )
                        if result_text and not assistant_messages:
                            messages.append(
                                {
                                    "role": "assistant",
                                    "content": result_text,
                                    "task_type": record_task_type,
                                    "model_name": str(record.get("model_name", "") or ""),
                                    "elapsed": float(record.get("elapsed_secs", 0.0) or 0.0),
                                    "created_at": str(record.get("created_at", "") or ""),
                                }
                            )
                        elif result_text and assistant_messages and not str(assistant_messages[-1].get("content", "") or "").strip():
                            assistant_messages[-1]["content"] = result_text
                            if not str(assistant_messages[-1].get("display_content", "") or "").strip():
                                assistant_messages[-1]["display_content"] = result_text
                        _log_ai_history_debug(
                            "[AIHistory] parse_json_messages.after count=%s summary=%s",
                            len(messages),
                            [
                                {
                                    "role": str(msg.get("role", "") or ""),
                                    "content_len": len(str(msg.get("content", "") or "")),
                                    "display_len": len(str(msg.get("display_content", "") or "")),
                                    "content_has_image": _history_text_has_image(msg.get("content", "")),
                                    "display_has_image": _history_text_has_image(msg.get("display_content", "")),
                                }
                                for msg in messages
                            ],
                        )
                        return messages
            except Exception:
                _ai_history_debug_logger.exception("[AIHistory] parse_json_messages.failed")
                pass

        source_text = str(record.get("source_text", "") or "").strip()
        messages = []
        if source_text:
            messages.append({"role": "user", "content": source_text, "display_content": source_text, "regenerate_text": source_text, "task_type": record_task_type})
        if result_text:
            messages.append(
                {
                    "role": "assistant",
                    "content": result_text,
                    "task_type": record_task_type,
                    "model_name": str(record.get("model_name", "") or ""),
                    "elapsed": float(record.get("elapsed_secs", 0.0) or 0.0),
                    "created_at": str(record.get("created_at", "") or ""),
                }
            )
        return messages

    @staticmethod
    def _stream_history_signature(history: object) -> tuple:
        items: list[tuple] = []
        if not isinstance(history, list):
            return tuple()
        last_index = len(history) - 1
        for idx, msg in enumerate(history):
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role", "") or "")
            task_type = str(msg.get("task_type", "") or "")
            if idx == last_index and role == "assistant":
                items.append((idx, role, task_type, "__streaming_last_ai__"))
                continue
            content = str(msg.get("display_content") or msg.get("content") or "")
            items.append((idx, role, task_type, len(content), hash(content)))
        return tuple(items)

    def _try_update_streaming_last_ai_from_cache(self) -> bool:
        history = getattr(self, "_chat_history", None)
        if not isinstance(history, list) or not history:
            return False
        signature = _ocr_text_panel_class()._stream_history_signature(history)
        if signature != getattr(self, "_stream_render_signature", None):
            return False
        bubble_view = getattr(self, "_bubble_view", None)
        if bubble_view is None:
            return False
        try:
            if not bubble_view.isVisible():
                return False
        except Exception:
            return False
        last_msg = history[-1] if isinstance(history[-1], dict) else {}
        if str(last_msg.get("role", "") or "") != "assistant":
            return False
        content = str(last_msg.get("content", "") or "")
        if not content.strip():
            return False
        try:
            bubble_view.update_last_ai(content, streaming=True)
            bubble_view.scroll_to_bottom()
            return True
        except Exception:
            return False

    def _try_append_streaming_tail_from_cache(self) -> bool:
        history = getattr(self, "_chat_history", None)
        if not isinstance(history, list) or not history:
            return False
        bubble_view = getattr(self, "_bubble_view", None)
        if bubble_view is None or not hasattr(bubble_view, "append_messages"):
            return False
        bubbles = getattr(bubble_view, "_bubbles", None)
        if not isinstance(bubbles, list):
            return False
        start_index = len(bubbles)
        if start_index >= len(history):
            return False
        try:
            appended = bool(bubble_view.append_messages(history, start_index=start_index, keep_bottom=True))
        except Exception:
            return False
        if not appended:
            return False
        try:
            bubble_view.show()
        except Exception:
            pass
        self._stream_render_signature = _ocr_text_panel_class()._stream_history_signature(history)
        try:
            bubble_view.scroll_to_bottom_now()
        except Exception:
            try:
                bubble_view.scroll_to_bottom()
            except Exception:
                pass
        self._sync_title_bar_visibility()
        _trace_ai_panel(
            self,
            "render_chat_history.append_stream_tail",
            history_len=len(history),
            start_index=int(start_index),
        )
        return True

    def _ensure_latest_chat_visible_after_layout(self, *, defer: bool = True) -> None:
        bubble_view = getattr(self, "_bubble_view", None)
        if bubble_view is None:
            return

        def _apply() -> None:
            view = getattr(self, "_bubble_view", None)
            if view is None:
                return
            try:
                if hasattr(view, "_invalidate_bubble_width_cache"):
                    view._invalidate_bubble_width_cache()
                elif isinstance(getattr(view, "_bubbles", None), list):
                    for bubble in list(getattr(view, "_bubbles", []) or []):
                        try:
                            bubble._natural_width = None
                        except Exception:
                            pass
                if hasattr(view, "refresh_layout"):
                    view.refresh_layout(keep_bottom=True)
                if hasattr(view, "scroll_to_bottom_now"):
                    view.scroll_to_bottom_now()
                elif hasattr(view, "scroll_to_bottom"):
                    view.scroll_to_bottom()
                viewport = view.viewport() if hasattr(view, "viewport") else None
                if viewport is not None and hasattr(viewport, "update"):
                    viewport.update()
            except RuntimeError:
                return
            except Exception:
                pass

        _apply()
        if not bool(defer):
            return
        if not isinstance(bubble_view, QWidget):
            return
        for delay_ms in (0, 16, 48):
            QTimer.singleShot(int(delay_ms), _apply)

    def _render_chat_history(self, is_streaming: bool = False) -> None:
        _log_ai_history_debug(
            "[AIHistory] render_chat_history.begin streaming=%s history_len=%s summary=%s",
            bool(is_streaming),
            len(getattr(self, "_chat_history", []) or []),
            [
                {
                    "role": str(msg.get("role", "") or ""),
                    "content_len": len(str(msg.get("content", "") or "")),
                    "display_len": len(str(msg.get("display_content", "") or "")),
                    "content_has_image": _history_text_has_image(msg.get("content", "")),
                    "display_has_image": _history_text_has_image(msg.get("display_content", "")),
                    "preview": _preview_log_text(msg.get("display_content") or msg.get("content") or "", 80),
                }
                for msg in (getattr(self, "_chat_history", []) or [])
                if isinstance(msg, dict)
            ],
        )
        _trace_ai_panel(
            self,
            "render_chat_history.begin",
            is_streaming=bool(is_streaming),
            history_len=len(getattr(self, "_chat_history", []) or []),
            bubble_visible_before=bool(getattr(self, "_bubble_view", None) is not None and self._bubble_view.isVisible()),
        )
        if not hasattr(self, "_chat_history") or not self._chat_history:
            self._stream_render_signature = None
            if getattr(self, "_history_showing", False):
                self._ensure_history_output_area()
            elif bool(getattr(self, "_preserve_cleared_output_area", False)):
                self._bubble_view.show()
                self._lock_editor_container_for_output_area()
            else:
                self._bubble_view.hide()
            self._sync_title_bar_visibility()
            self._translation_info.hide()
            self._sync_output_quick_action_bar_visibility()
            _trace_ai_panel(self, "render_chat_history.empty_end", is_streaming=bool(is_streaming))
            return

        self._preserve_cleared_output_area = False
        self._sync_output_quick_action_bar_visibility()
        if bool(is_streaming) and _ocr_text_panel_class()._try_update_streaming_last_ai_from_cache(self):
            self._sync_title_bar_visibility()
            self._sync_output_quick_action_bar_visibility()
            _trace_ai_panel(
                self,
                "render_chat_history.fast_stream_update",
                is_streaming=True,
                history_len=len(getattr(self, "_chat_history", []) or []),
            )
            return
        if bool(is_streaming) and _ocr_text_panel_class()._try_append_streaming_tail_from_cache(self):
            return
        if not bool(is_streaming):
            self._stream_render_signature = None
        # 流式渲染期间冻结窗口几何：同时锁死窗口的最小与最大高度为当前值，并置位
        # _geometry_frozen。仅靠 setMinimumHeight 挡不住 Qt 把窗口"放大"——而日志实测
        # 证明流式期间窗口正是被放大（h:194→232→302→396，顶部固定、底端下推）。
        # 必须连 setMaximumHeight 一起钉死，才能从物理上阻止 _bubble_view.show()/
        # 后续 thinking_card.show() 触发的非法膨胀，消除上下跳动闪烁。流式结束在
        # _on_translation_finished 解冻并执行一次最终 _reposition 收口。
        frozen_here = False
        if is_streaming and not getattr(self, "_geometry_frozen", False):
            self._freeze_window_height()
            self._geometry_frozen = True
            frozen_here = True
            _trace_ai_panel(self, "render_chat_history.froze_geometry", is_streaming=True, frozen_h=int(self.height()))

        initial_scroll_to_bottom = bool(getattr(self, "_history_record_switching", False)) and not bool(is_streaming)
        rendered_history = False
        previous_bubble_updates = True
        previous_viewport_updates = True
        previous_container_updates = True
        preserved_panel_geometry = QRect()
        previous_panel_min_h = None
        previous_panel_max_h = None
        bubble_container = None
        history_switch_effect = None
        history_switch_previous_effect = None
        history_switch_previous_opacity = None
        if initial_scroll_to_bottom:
            try:
                preserved_panel_geometry = QRect(self.geometry())
                previous_panel_min_h = int(self.minimumHeight())
                previous_panel_max_h = int(self.maximumHeight())
                frozen_h = int(max(1, preserved_panel_geometry.height() or self.height()))
                self.setMinimumHeight(frozen_h)
                self.setMaximumHeight(frozen_h)
                previous_bubble_updates = bool(self._bubble_view.updatesEnabled())
                previous_viewport_updates = bool(self._bubble_view.viewport().updatesEnabled())
                bubble_container = getattr(self._bubble_view, "_container", None)
                if bubble_container is not None:
                    previous_container_updates = bool(bubble_container.updatesEnabled())
                self._bubble_view.setUpdatesEnabled(False)
                self._bubble_view.viewport().setUpdatesEnabled(False)
                if bubble_container is not None:
                    bubble_container.setUpdatesEnabled(False)
                self._bubble_view._suppress_image_panel_reposition_until = time.monotonic() + 0.35
                self._bubble_view._force_keep_bottom_until = time.monotonic() + 0.65
                try:
                    history_switch_previous_effect = self._bubble_view.graphicsEffect()
                    if history_switch_previous_effect is None:
                        from PyQt6.QtWidgets import QGraphicsOpacityEffect

                        history_switch_effect = QGraphicsOpacityEffect(self._bubble_view)
                        history_switch_effect.setOpacity(0.0)
                        self._bubble_view.setGraphicsEffect(history_switch_effect)
                    elif hasattr(history_switch_previous_effect, "opacity") and hasattr(history_switch_previous_effect, "setOpacity"):
                        history_switch_previous_opacity = float(history_switch_previous_effect.opacity())
                        history_switch_previous_effect.setOpacity(0.0)
                except Exception:
                    history_switch_effect = None
                    history_switch_previous_effect = None
                    history_switch_previous_opacity = None
            except Exception:
                pass
        try:
            self._bubble_view.render_messages(
                self._chat_history,
                initial_scroll_to_bottom=initial_scroll_to_bottom,
            )
            if bool(is_streaming):
                self._stream_render_signature = _ocr_text_panel_class()._stream_history_signature(self._chat_history)
            _log_ai_history_debug(
                "[AIHistory] render_chat_history.after_render bubble_count=%s visible=%s",
                len(getattr(self._bubble_view, "_bubbles", []) or []),
                bool(self._bubble_view.isVisible()),
            )
            self._bubble_view.show()
            self._sync_output_quick_action_bar_visibility()
            sync_title_bar = getattr(self, "_sync_title_bar_visibility", None)
            if callable(sync_title_bar) and not initial_scroll_to_bottom:
                sync_title_bar()
            _trace_ai_panel(
                self,
                "render_chat_history.after_show",
                is_streaming=bool(is_streaming),
                history_len=len(getattr(self, "_chat_history", []) or []),
            )

            # 流式传输期间禁止 reposition 重定高度与位置，避免高频窗口闪烁；流式结束后执行一次最终 reposition
            if not is_streaming:
                if initial_scroll_to_bottom:
                    self._update_top_seam_cover()
                    _trace_ai_panel(self, "render_chat_history.history_switch_kept_geometry", is_streaming=False)
                else:
                    _trace_ai_panel(self, "render_chat_history.before_reposition", is_streaming=bool(is_streaming))
                    self._reposition()
                    _trace_ai_panel(self, "render_chat_history.after_reposition", is_streaming=bool(is_streaming))
            rendered_history = True
        finally:
            if initial_scroll_to_bottom and rendered_history:
                try:
                    if hasattr(self._bubble_view, "scroll_to_bottom_now"):
                        self._bubble_view.scroll_to_bottom_now()
                    else:
                        self._bubble_view.scroll_to_bottom()
                    self._bubble_view.refresh_layout(keep_bottom=True)
                except Exception:
                    pass
            if initial_scroll_to_bottom:
                try:
                    if bubble_container is not None:
                        bubble_container.setUpdatesEnabled(previous_container_updates)
                    self._bubble_view.setUpdatesEnabled(True)
                    self._bubble_view.viewport().setUpdatesEnabled(True)
                    self._bubble_view.setUpdatesEnabled(previous_bubble_updates)
                    self._bubble_view.viewport().setUpdatesEnabled(previous_viewport_updates)
                    if preserved_panel_geometry.isValid():
                        self.setGeometry(preserved_panel_geometry)
                except Exception:
                    pass
        if rendered_history:
            if not initial_scroll_to_bottom:
                self._bubble_view.scroll_to_bottom()
            else:
                try:
                    def _reveal_history_switch_content(
                        panel=self,
                        view=self._bubble_view,
                        effect=history_switch_effect,
                        previous_effect=history_switch_previous_effect,
                        previous_opacity=history_switch_previous_opacity,
                        geometry=QRect(preserved_panel_geometry),
                    ) -> None:
                        try:
                            setattr(view, "_force_keep_bottom_until", time.monotonic() + 0.25)
                        except Exception:
                            pass
                        try:
                            if hasattr(view, "scroll_to_bottom_now"):
                                view.scroll_to_bottom_now()
                            if hasattr(view, "refresh_layout"):
                                view.refresh_layout(keep_bottom=True)
                            if hasattr(view, "scroll_to_bottom_now"):
                                view.scroll_to_bottom_now()
                        except RuntimeError:
                            return
                        except Exception:
                            pass
                        try:
                            QApplication.processEvents()
                        except Exception:
                            pass
                        try:
                            if hasattr(view, "scroll_to_bottom_now"):
                                view.scroll_to_bottom_now()
                        except Exception:
                            pass
                        try:
                            if effect is not None and view.graphicsEffect() is effect:
                                view.setGraphicsEffect(None)
                            elif previous_effect is not None and previous_opacity is not None:
                                previous_effect.setOpacity(float(previous_opacity))
                        except RuntimeError:
                            return
                        except Exception:
                            pass
                        try:
                            if geometry.isValid():
                                panel.setGeometry(geometry)
                        except Exception:
                            pass
                        try:
                            view.viewport().update()
                            view.viewport().repaint()
                            view.update()
                            view.repaint()
                        except Exception:
                            pass

                    QTimer.singleShot(32, _reveal_history_switch_content)

                    def _release_history_switch_geometry(
                        panel=self,
                        view=self._bubble_view,
                        geometry=QRect(preserved_panel_geometry),
                        min_h=previous_panel_min_h,
                        max_h=previous_panel_max_h,
                    ) -> None:
                        try:
                            setattr(view, "_suppress_image_panel_reposition_until", 0.0)
                        except Exception:
                            pass
                        try:
                            if min_h is not None:
                                panel.setMinimumHeight(int(min_h))
                            if max_h is not None:
                                panel.setMaximumHeight(int(max_h))
                            if geometry.isValid():
                                panel.setGeometry(geometry)
                        except RuntimeError:
                            pass
                        except Exception:
                            pass

                    QTimer.singleShot(360, _release_history_switch_geometry)
                except Exception:
                    pass
        _trace_ai_panel(
            self,
            "render_chat_history.after_scroll",
            is_streaming=bool(is_streaming),
            history_len=len(getattr(self, "_chat_history", []) or []),
            geometry_frozen=bool(getattr(self, "_geometry_frozen", False)),
        )

    def _clear_chat_history(
        self,
        *,
        clear_editor: bool = True,
        preserve_output_area: bool = False,
    ) -> None:
        _trace_ai_panel(
            self,
            "clear_chat.begin",
            history_len=len(getattr(self, "_chat_history", []) or []),
            bubble_visible_before=bool(getattr(self, "_bubble_view", None) is not None and self._bubble_view.isVisible()),
        )
        history_showing = bool(getattr(self, "_history_showing", False))
        bubble_view = getattr(self, "_bubble_view", None)
        preserve_visible_output_area = bool(
            preserve_output_area
            and not history_showing
            and bubble_view is not None
            and bubble_view.isVisible()
        )
        preserved_geometry = None
        if preserve_visible_output_area:
            try:
                preserved_geometry = self.geometry()
            except Exception:
                preserved_geometry = None

        self._discard_worker_for_clear_chat()
        self._is_chatting = False
        self._chat_history = []
        self._active_assistant_msg_index = None
        self._stream_render_signature = None
        self._current_session_record_id = None
        self._preserve_cleared_output_area = False
        _ocr_text_panel_class()._reset_context_management_state(self)

        if hasattr(self, "_btn_clear_chat"):
            self._btn_clear_chat.show()
        if hasattr(self, "_btn_qa"):
            self._btn_qa.setText("问答")
            self._btn_qa.setToolTip("")

        # 获取当前的底部 y 坐标，用于后续 reposition 时保持底部位置不动
        if self.isVisible():
            frame_geo = self.frameGeometry()
            self._keep_bottom_y_on_next_reposition = frame_geo.y() + frame_geo.height()
        _trace_ai_panel(
            self,
            "clear_chat.keep_bottom_set",
            keep_bottom=self._keep_bottom_y_on_next_reposition,
        )

        if clear_editor:
            self._clear_editor_silently()
        # 清除对话前冻结窗口几何并锁死当前高度（最小=最大）：普通重置路径会隐藏
        # _bubble_view，导致窗口布局最小高度骤减；保留输出区路径也借此避免清空内容瞬间
        # 被 Qt 自动压缩。
        was_frozen = bool(getattr(self, "_geometry_frozen", False))
        self._geometry_frozen = True
        self._freeze_window_height()
        self._bubble_view.clear()
        if history_showing:
            self._history_forced_output_area = True
            self._bubble_view.show()
            self._bubble_view.setMinimumHeight(360)
        elif preserve_visible_output_area:
            self._bubble_view.show()
            self._preserve_cleared_output_area = True
            self._lock_editor_container_for_output_area()
        else:
            self._bubble_view.hide()
            self._bubble_view.setMinimumHeight(0)
            try:
                self._bubble_view.setMaximumHeight(16777215)
                self._editor_container.setMinimumHeight(0)
                self._editor_container.setMaximumHeight(16777215)
            except Exception:
                pass
        sync_title_bar = getattr(self, "_sync_title_bar_visibility", None)
        if callable(sync_title_bar):
            sync_title_bar()
        self._sync_output_quick_action_bar_visibility()
        self._translation_info.clear()
        self._translation_info.hide()

        # 直接隐藏思考卡片，避免 stop_thinking 中再次触发 _reposition 导致高度计算混乱
        if hasattr(self, "_thinking_card"):
            self._thinking_card.hide()

        if clear_editor:
            self._pending_attachments = []
            self._pending_attachment_previews = []
            self._pending_attachment_labels = []
            _refresh_attachment_preview_bar_if_available(self)
        if history_showing:
            try:
                self._history_sidebar.select_record(None)
            except Exception:
                pass
        if preserve_visible_output_area:
            _trace_ai_panel(self, "clear_chat.preserved_output_area", geometry=preserved_geometry)
            if preserved_geometry is not None:
                try:
                    self.setGeometry(preserved_geometry)
                    self._cleared_output_restore_geometry = QRect(preserved_geometry)
                except Exception:
                    pass
            try:
                self._activate_panel_layouts()
                self._bubble_view.refresh_layout(keep_bottom=False)
                self._bubble_view.viewport().update()
                self._bubble_view.update()
                self._editor_container.updateGeometry()
                self._bottom_row_widget.updateGeometry()
                self.update()
                self._sync_output_quick_action_bar_visibility()
            except Exception:
                pass
            self._keep_bottom_y_on_next_reposition = None
            self._activate_for_text_input()
            self._update_top_seam_cover()
        else:
            self._cleared_output_restore_geometry = QRect()
            _trace_ai_panel(self, "clear_chat.before_reposition")
            self._reposition()
            _trace_ai_panel(self, "clear_chat.after_reposition")
        # 清除对话不在流式渲染窗口期：恢复冻结态到调用前；若调用前未冻结，释放高度约束。
        self._geometry_frozen = was_frozen
        if not was_frozen:
            self._thaw_window_height()
