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
from deepcat.ui.post_capture_actions.context_logic import (
    context_message_identity,
    trim_messages_to_recent_rounds,
)

load_settings = DynamicModuleAttribute("deepcat.ui.post_capture_actions.text_panel", "load_settings")
update_ui_settings = DynamicModuleAttribute("deepcat.ui.post_capture_actions.text_panel", "update_ui_settings")


def _ocr_text_panel_class():
    from deepcat.ui.post_capture_actions.text_panel import OcrTextPanel

    return OcrTextPanel


class TextPanelContextMixin:
    def _current_draft_key(self) -> str:
        record_id = getattr(self, "_current_session_record_id", None)
        return str(int(record_id)) if record_id is not None else "new"

    def _load_chat_drafts(self) -> dict[str, str]:
        try:
            settings = load_settings()
            ui = dict(getattr(settings, "ui", {}) or {})
            drafts = ui.get(_AI_CHAT_DRAFTS_UI_KEY, {})
            if not isinstance(drafts, dict):
                return {}
            return {
                str(key): str(value)
                for key, value in drafts.items()
                if str(value or "").strip()
            }
        except Exception:
            return {}

    def _write_chat_drafts(self, drafts: dict[str, str]) -> None:
        cleaned = {
            str(key): str(value)[:20000]
            for key, value in (drafts or {}).items()
            if str(value or "").strip()
        }
        if len(cleaned) > 20:
            cleaned = dict(list(cleaned.items())[-20:])
        try:
            update_ui_settings(**{_AI_CHAT_DRAFTS_UI_KEY: cleaned})
        except Exception:
            pass

    def _save_current_chat_draft(self, *, show_feedback: bool = True) -> None:
        editor = getattr(self, "_editor", None)
        if editor is None:
            return
        if str(getattr(self, "_current_input_origin", "manual") or "manual") == "ocr":
            return
        text = str(editor.toPlainText() or "").strip()
        key = self._current_draft_key()
        drafts = self._load_chat_drafts()
        had_attachments = bool(getattr(self, "_pending_attachments", []) or [])
        if text:
            drafts[key] = text
        else:
            drafts.pop(key, None)
        self._write_chat_drafts(drafts)
        if show_feedback and (text or had_attachments):
            if had_attachments:
                self._show_light_feedback("草稿已保存，附件需重新添加。", tone="warning", auto_hide_ms=2200)
            else:
                self._show_light_feedback("草稿已自动保存。", tone="success", auto_hide_ms=1500)

    def _remove_chat_draft(self, key: Optional[str] = None) -> None:
        draft_key = str(key or self._current_draft_key())
        drafts = self._load_chat_drafts()
        if draft_key not in drafts:
            return
        drafts.pop(draft_key, None)
        self._write_chat_drafts(drafts)

    def _restore_chat_draft(self, key: Optional[str] = None, *, show_feedback: bool = True) -> None:
        editor = getattr(self, "_editor", None)
        if editor is None:
            return
        if str(editor.toPlainText() or "").strip() or bool(getattr(self, "_pending_attachments", []) or []):
            return
        draft_key = str(key or self._current_draft_key())
        text = str(self._load_chat_drafts().get(draft_key, "") or "").strip()
        if not text:
            return
        old_loading = bool(getattr(self, "_loading_text", False))
        self._loading_text = True
        try:
            editor.setPlainText(text)
        finally:
            self._loading_text = old_loading
        self._current_input_origin = "manual"
        self._sync_input_watermark_visibility()
        if show_feedback:
            self._show_light_feedback("已恢复未发送草稿。", tone="info", auto_hide_ms=1800)

    def _message_text_for_context_estimate(self, value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(_ocr_text_panel_class()._message_text_for_context_estimate(self, item) for item in value)
        if isinstance(value, dict):
            if "text" in value:
                return str(value.get("text") or "")
            if value.get("type") == "image_url":
                return "[图片附件]"
            if value.get("type") == "file_url":
                file_url = value.get("file_url")
                if isinstance(file_url, dict):
                    return f"[文件附件:{file_url.get('name', '未命名')}]"
                return "[文件附件]"
            return "\n".join(_ocr_text_panel_class()._message_text_for_context_estimate(self, v) for v in value.values())
        return str(value or "")

    def _chat_context_token_estimate(self, next_text: str = "") -> int:
        total_text: list[str] = []
        for msg in getattr(self, "_chat_history", []) or []:
            if not isinstance(msg, dict):
                continue
            total_text.append(_ocr_text_panel_class()._message_text_for_context_estimate(self, msg.get("content", "")))
            total_text.append(str(msg.get("display_content", "") or ""))
        if str(next_text or "").strip():
            total_text.append(str(next_text))
        return _ocr_text_panel_class()._estimate_tokens(self, "\n".join(total_text))

    def _estimate_context_messages_tokens(self, messages: list[dict]) -> int:
        total_text: list[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            total_text.append(_ocr_text_panel_class()._message_text_for_context_estimate(self, msg.get("content", "")))
            total_text.append(str(msg.get("display_content", "") or ""))
        return _ocr_text_panel_class()._estimate_tokens(self, "\n".join(total_text))

    def _trim_messages_to_recent_rounds(self, messages: list[dict], round_limit: Optional[int]) -> list[dict]:
        return trim_messages_to_recent_rounds(messages, round_limit)

    def _context_summary_system_message(self) -> Optional[dict]:
        summary_text = str(getattr(self, "_context_summary_text", "") or "").strip()
        if not summary_text:
            return None
        return {
            "role": "system",
            "content": "以下是此前对话摘要，后续回答应把它作为已压缩上下文：\n" + summary_text,
        }

    def _context_messages_after_summary(self, messages: list[dict]) -> list[dict]:
        message_list = list(messages)
        summary_message = _ocr_text_panel_class()._context_summary_system_message(self)
        if summary_message is None:
            return message_list
        try:
            anchor_index = int(getattr(self, "_context_summary_anchor_index", 0) or 0)
        except Exception:
            anchor_index = 0
        anchor_index = max(0, min(anchor_index, len(message_list)))
        return [summary_message] + message_list[anchor_index:]

    @staticmethod
    def _context_message_identity(message: dict) -> tuple[str, str]:
        return context_message_identity(message)

    def _context_pinned_messages(self) -> list[dict]:
        pinned_messages: list[dict] = []
        for msg in getattr(self, "_chat_history", []) or []:
            if not isinstance(msg, dict) or not bool(msg.get("context_pinned", False)):
                continue
            role = str(msg.get("role", "") or "")
            if role not in {"user", "assistant"}:
                continue
            content = copy.deepcopy(msg.get("content", ""))
            if not _ocr_text_panel_class()._message_text_for_context_estimate(self, content).strip():
                continue
            pinned_messages.append({"role": role, "content": content})
        return pinned_messages

    def _merge_pinned_context_messages(self, messages: list[dict]) -> list[dict]:
        message_list = [dict(msg) for msg in list(messages or []) if isinstance(msg, dict)]
        pinned_messages = _ocr_text_panel_class()._context_pinned_messages(self)
        if not pinned_messages:
            return message_list

        preamble: list[dict] = []
        while message_list:
            first = message_list[0]
            role = str(first.get("role", "") or "")
            if role not in {"system", "developer"}:
                break
            preamble.append(message_list.pop(0))

        seen = {
            _ocr_text_panel_class()._context_message_identity(msg)
            for msg in [*preamble, *message_list]
            if isinstance(msg, dict)
        }
        missing_pinned: list[dict] = []
        for msg in pinned_messages:
            identity = _ocr_text_panel_class()._context_message_identity(msg)
            if identity in seen:
                continue
            missing_pinned.append(msg)
            seen.add(identity)
        return preamble + missing_pinned + message_list

    def _trim_context_messages_to_recent_rounds(self, messages: list[dict], round_limit: Optional[int]) -> list[dict]:
        message_list = list(messages)
        if not round_limit or round_limit <= 0:
            return message_list
        preamble: list[dict] = []
        while message_list:
            first = message_list[0]
            role = str(first.get("role", "") or "") if isinstance(first, dict) else ""
            if role not in {"system", "developer"}:
                break
            preamble.append(message_list.pop(0))
        return preamble + _ocr_text_panel_class()._trim_messages_to_recent_rounds(self, message_list, round_limit)

    def _context_messages_for_estimate(self, next_text: str = "") -> list[dict]:
        messages = [dict(msg) for msg in (getattr(self, "_chat_history", []) or []) if isinstance(msg, dict)]
        next_value = str(next_text or "").strip()
        pending_attachments = list(getattr(self, "_pending_attachments", []) or [])
        if next_value or pending_attachments:
            content = next_value
            if pending_attachments:
                content = [{"type": "text", "text": next_value or "请帮我分析一下这个文件。"}] + pending_attachments
            messages.append({"role": "user", "content": content, "display_content": next_value})
        messages = _ocr_text_panel_class()._context_messages_after_summary(self, messages)
        messages = _ocr_text_panel_class()._trim_context_messages_to_recent_rounds(
            self,
            messages,
            getattr(self, "_context_recent_round_limit", None),
        )
        return _ocr_text_panel_class()._merge_pinned_context_messages(self, messages)

    def _context_management_snapshot(self, next_text: str = "") -> dict:
        all_history = [msg for msg in (getattr(self, "_chat_history", []) or []) if isinstance(msg, dict)]
        carrying_messages = _ocr_text_panel_class()._context_messages_for_estimate(self, next_text)
        tokens = _ocr_text_panel_class()._estimate_context_messages_tokens(self, carrying_messages)
        has_attachments = bool(getattr(self, "_pending_attachments", []) or []) or _ocr_text_panel_class()._messages_have_attachments(carrying_messages)
        pinned_count = len(_ocr_text_panel_class()._context_pinned_messages(self))
        pinned_suffix = f"，固定 {pinned_count} 条" if pinned_count else ""
        round_limit = getattr(self, "_context_recent_round_limit", None)
        try:
            round_limit_value = int(round_limit) if round_limit else None
        except Exception:
            round_limit_value = None
        summary_active = _ocr_text_panel_class()._context_summary_system_message(self) is not None
        if summary_active:
            new_message_count = max(0, len(carrying_messages) - 1)
            if round_limit_value:
                scope_text = f"摘要 + 最近 {new_message_count} 条 / {round_limit_value} 轮{pinned_suffix}"
            else:
                scope_text = f"摘要 + 新增 {new_message_count} 条{pinned_suffix}"
            strategy_text = "摘要 + 新增消息"
        elif round_limit_value:
            scope_text = f"最近 {len(carrying_messages)} 条 / {round_limit} 轮{pinned_suffix}"
            strategy_text = f"只带最近 {round_limit_value} 轮"
        else:
            next_count = 1 if str(next_text or "").strip() or bool(getattr(self, "_pending_attachments", []) or []) else 0
            scope_text = f"最近 {len(all_history) + next_count} 条 / 全部上下文{pinned_suffix}"
            strategy_text = "全部上下文"
        strategy_key = f"summary:{1 if summary_active else 0};recent:{round_limit_value or 'all'};pin:{pinned_count}"
        return {
            "tokens": int(tokens),
            "message_count": int(len(carrying_messages)),
            "has_attachments": bool(has_attachments),
            "scope_text": scope_text,
            "round_limit": round_limit_value,
            "has_summary": bool(summary_active),
            "pinned_count": int(pinned_count),
            "strategy_text": strategy_text,
            "strategy_key": strategy_key,
        }

    def _context_details_text(self, snapshot: dict) -> str:
        tokens = int((snapshot or {}).get("tokens", 0) or 0)
        attachment_text = "包含" if bool((snapshot or {}).get("has_attachments", False)) else "不含"
        return "\n".join(
            [
                f"本轮携带：{(snapshot or {}).get('scope_text', '上下文')}",
                f"估算上下文 tokens：{tokens}",
                f"附件：{attachment_text}",
                f"策略：{(snapshot or {}).get('strategy_text', '全部上下文')}",
            ]
        )

    def _context_snapshot_significantly_changed(self, old_snapshot: Optional[dict], new_snapshot: dict) -> bool:
        if not old_snapshot:
            return True
        try:
            old_tokens = int((old_snapshot or {}).get("tokens", 0) or 0)
            new_tokens = int((new_snapshot or {}).get("tokens", 0) or 0)
        except Exception:
            old_tokens = 0
            new_tokens = 0
        token_delta = new_tokens - old_tokens
        if token_delta >= 10000 and (token_delta / max(old_tokens, 1)) >= 0.15:
            return True
        try:
            old_message_count = int((old_snapshot or {}).get("message_count", 0) or 0)
            new_message_count = int((new_snapshot or {}).get("message_count", 0) or 0)
        except Exception:
            old_message_count = 0
            new_message_count = 0
        if new_message_count - old_message_count >= 4:
            return True
        if bool((old_snapshot or {}).get("has_attachments", False)) != bool((new_snapshot or {}).get("has_attachments", False)):
            return True
        old_strategy = (old_snapshot or {}).get("strategy_key")
        new_strategy = (new_snapshot or {}).get("strategy_key")
        if old_strategy is not None or new_strategy is not None:
            return old_strategy != new_strategy
        return (
            (old_snapshot or {}).get("round_limit") != (new_snapshot or {}).get("round_limit")
            or bool((old_snapshot or {}).get("has_summary", False)) != bool((new_snapshot or {}).get("has_summary", False))
        )

    def _set_context_manager_expanded(self, expanded: bool) -> None:
        self._context_manager_expanded = bool(expanded)
        actions_row = getattr(self, "_context_actions_row", None)
        expand_btn = getattr(self, "_context_expand_btn", None)
        details_label = getattr(self, "_context_details_label", None)
        if actions_row is not None:
            actions_row.setVisible(bool(expanded))
        if details_label is not None:
            details_label.setVisible(bool(expanded))
        if expand_btn is not None:
            expand_btn.setText("收起" if expanded else "管理")
        _ocr_text_panel_class()._position_context_warning_bar(self)

    def _toggle_context_manager(self) -> None:
        _ocr_text_panel_class()._set_context_manager_expanded(self, not bool(getattr(self, "_context_manager_expanded", False)))
        _ocr_text_panel_class()._position_context_warning_bar(self)

    def _position_context_warning_bar(self) -> None:
        bar = getattr(self, "_context_warning_bar", None)
        bubble_view = getattr(self, "_bubble_view", None)
        content_stack = getattr(self, "_content_stack", None)
        if bar is None or bubble_view is None or content_stack is None or not bar.isVisible():
            return
        try:
            width = max(260, int(bubble_view.width()) - 20)
            bar.setFixedWidth(width)
            bar.adjustSize()
            bar_h = max(1, int(bar.sizeHint().height() or bar.height()))
            bubble_top_left = bubble_view.mapTo(content_stack, QPoint(0, 0))
            x = int(bubble_top_left.x() + 10)
            y = int(bubble_top_left.y() + bubble_view.height() - bar_h - 10)
            if y < bubble_top_left.y() + 8:
                y = int(bubble_top_left.y() + 8)
            bar.move(x, y)
            bar.raise_()
        except Exception:
            pass

    def _hide_context_warning_bar(self, *, dismissed: bool = False) -> None:
        if dismissed:
            self._context_warning_dismissed = True
        bar = getattr(self, "_context_warning_bar", None)
        if bar is not None:
            bar.hide()

    def _dismiss_context_warning_bar(self) -> None:
        snapshot = getattr(self, "_context_warning_current_snapshot", None)
        if not isinstance(snapshot, dict):
            try:
                editor = getattr(self, "_editor", None)
                next_text = editor.toPlainText().strip() if editor is not None else ""
                snapshot = _ocr_text_panel_class()._context_management_snapshot(self, next_text)
            except Exception:
                snapshot = None
        self._context_warning_dismissed_snapshot = dict(snapshot) if isinstance(snapshot, dict) else None
        _ocr_text_panel_class()._hide_context_warning_bar(self, dismissed=True)

    def _use_recent_context_rounds(self) -> None:
        self._context_recent_round_limit = _AI_CHAT_CONTEXT_RECENT_ROUNDS
        self._context_summary_text = ""
        self._context_summary_anchor_index = 0
        self._pending_context_summary_anchor_index = None
        self._context_warning_dismissed = False
        self._context_warning_dismissed_snapshot = None
        self._context_warning_current_snapshot = None
        _ocr_text_panel_class()._set_context_manager_expanded(self, False)
        _ocr_text_panel_class()._maybe_show_context_length_warning(self, self._editor.toPlainText().strip())
        self._show_light_feedback("后续追问将只携带最近 5 轮。", tone="info", auto_hide_ms=1800)

    def _worker_messages_for_current_context(self, messages: Optional[list[dict]]) -> Optional[list[dict]]:
        if messages is None:
            return None
        carried_messages = _ocr_text_panel_class()._context_messages_after_summary(self, list(messages))
        carried_messages = _ocr_text_panel_class()._trim_context_messages_to_recent_rounds(
            self,
            carried_messages,
            getattr(self, "_context_recent_round_limit", None),
        )
        return _ocr_text_panel_class()._merge_pinned_context_messages(self, carried_messages)

    def _maybe_show_context_length_warning(self, next_text: str = "") -> None:
        if bool(getattr(self, "_suppress_next_context_warning", False)):
            self._suppress_next_context_warning = False
            return
        history = list(getattr(self, "_chat_history", []) or [])
        snapshot = _ocr_text_panel_class()._context_management_snapshot(self, next_text)
        tokens = int(snapshot.get("tokens", 0) or 0)
        round_limit_active = bool(getattr(self, "_context_recent_round_limit", None))
        summary_active = bool(snapshot.get("has_summary", False))
        long_enough = (
            tokens >= _AI_CHAT_CONTEXT_WARNING_TOKENS
            or (
                len(history) >= _AI_CHAT_CONTEXT_WARNING_MESSAGES
                and tokens >= _AI_CHAT_CONTEXT_WARNING_MESSAGES_TOKENS
            )
            or round_limit_active
            or summary_active
        )
        bar = getattr(self, "_context_warning_bar", None)
        label = getattr(self, "_context_warning_label", None)
        if bar is None or label is None:
            return
        if not long_enough:
            self._context_warning_dismissed = False
            self._context_warning_dismissed_snapshot = None
            self._context_warning_current_snapshot = None
            _ocr_text_panel_class()._hide_context_warning_bar(self)
            return
        if bool(getattr(self, "_context_warning_dismissed", False)):
            dismissed_snapshot = getattr(self, "_context_warning_dismissed_snapshot", None)
            if not _ocr_text_panel_class()._context_snapshot_significantly_changed(self, dismissed_snapshot, snapshot):
                return
            self._context_warning_dismissed = False
            self._context_warning_dismissed_snapshot = None
        attachment_text = "包含附件" if bool(snapshot.get("has_attachments", False)) else "不含附件"
        label.setText(
            f"上下文管理：本轮将携带{snapshot.get('scope_text', '上下文')}，"
            f"约 {tokens} tokens，{attachment_text}。"
        )
        details_label = getattr(self, "_context_details_label", None)
        if details_label is not None:
            details_label.setText(_ocr_text_panel_class()._context_details_text(self, snapshot))
        recent_btn = getattr(self, "_context_recent_btn", None)
        if recent_btn is not None:
            recent_btn.setText("已限制最近 5 轮" if round_limit_active else "只带最近 5 轮")
        self._context_warning_current_snapshot = dict(snapshot)
        bar.show()
        _ocr_text_panel_class()._set_context_manager_expanded(self, bool(getattr(self, "_context_manager_expanded", False)))
        _ocr_text_panel_class()._position_context_warning_bar(self)

    def _reset_context_management_state(self) -> None:
        self._context_manager_expanded = False
        self._context_recent_round_limit = None
        self._context_summary_text = ""
        self._context_summary_anchor_index = 0
        self._pending_context_summary_anchor_index = None
        self._context_warning_dismissed = False
        self._context_warning_dismissed_snapshot = None
        self._context_warning_current_snapshot = None
        self._suppress_next_context_warning = False
        _ocr_text_panel_class()._hide_context_warning_bar(self)
        _ocr_text_panel_class()._set_context_manager_expanded(self, False)

    def _finalize_pending_context_summary(self, result: str) -> None:
        pending_anchor = getattr(self, "_pending_context_summary_anchor_index", None)
        if pending_anchor is None:
            return
        self._pending_context_summary_anchor_index = None
        summary_text = str(result or "").strip()
        if not summary_text:
            feedback = getattr(self, "_show_light_feedback", None)
            if callable(feedback):
                feedback("总结为空，仍将沿用原上下文。", tone="warning", auto_hide_ms=2200)
            return

        self._context_summary_text = summary_text
        self._context_summary_anchor_index = len(getattr(self, "_chat_history", []) or [])
        self._context_recent_round_limit = None
        self._context_warning_dismissed = False
        self._context_warning_dismissed_snapshot = None
        self._context_warning_current_snapshot = None
        _ocr_text_panel_class()._set_context_manager_expanded(self, False)
        _ocr_text_panel_class()._maybe_show_context_length_warning(self, "")
        feedback = getattr(self, "_show_light_feedback", None)
        if callable(feedback):
            feedback("已总结前文，后续请求将携带摘要和新增消息。", tone="success", auto_hide_ms=2200)

    def _summarize_current_chat_from_warning(self) -> None:
        if getattr(self, "_translation_worker", None) is not None and self._translation_worker.isRunning():
            self._show_light_feedback("当前回答还在生成，稍后再总结。", tone="info")
            return
        if not getattr(self, "_chat_history", None):
            return
        _ocr_text_panel_class()._hide_context_warning_bar(self)
        self._context_recent_round_limit = None
        _ocr_text_panel_class()._set_context_manager_expanded(self, False)
        self._suppress_next_context_warning = True
        self._pending_context_summary_anchor_index = len(getattr(self, "_chat_history", []) or [])
        old_loading = bool(getattr(self, "_loading_text", False))
        self._loading_text = True
        try:
            self._editor.setPlainText(
                "请总结当前对话，生成一份后续可继续使用的上下文摘要。"
                "保留关键结论、待办事项、重要约束、已做决定和下一步线索。"
            )
        finally:
            self._loading_text = old_loading
        self._answer_question()
        if getattr(self, "_pending_context_summary_anchor_index", None) is not None:
            worker = getattr(self, "_translation_worker", None)
            note_worker = getattr(self, "_ima_note_search_worker", None)
            worker_running = bool(worker is not None and getattr(worker, "isRunning", lambda: False)())
            note_worker_running = bool(note_worker is not None and getattr(note_worker, "isRunning", lambda: False)())
            if not worker_running and not note_worker_running:
                self._pending_context_summary_anchor_index = None

    def _start_new_chat_from_context_warning(self) -> None:
        _ocr_text_panel_class()._reset_context_management_state(self)
        self._start_new_chat_from_sidebar()
