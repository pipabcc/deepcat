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

OcrTranslationWorker = DynamicModuleAttribute(
    "deepcat.ui.post_capture_actions.text_panel", "OcrTranslationWorker"
)
QTimer = DynamicModuleAttribute("deepcat.ui.post_capture_actions.text_panel", "QTimer")
copy_plain_text_to_clipboard = DynamicModuleAttribute(
    "deepcat.ui.post_capture_actions.text_panel", "copy_plain_text_to_clipboard"
)
load_settings = DynamicModuleAttribute("deepcat.ui.post_capture_actions.text_panel", "load_settings")


def _ocr_text_panel_class():
    from deepcat.ui.post_capture_actions.text_panel import OcrTextPanel

    return OcrTextPanel


class TextPanelExecutionMixin:
    def _cancel_translation_worker_for_close(self) -> None:
        """关闭窗口时使当前请求失效，并协作式回收仍在运行的回答线程。"""
        worker = getattr(self, "_translation_worker", None)
        if worker is None:
            return

        aborted_worker_ids = getattr(self, "_aborted_worker_ids", None)
        if not isinstance(aborted_worker_ids, set):
            aborted_worker_ids = set()
            self._aborted_worker_ids = aborted_worker_ids
        aborted_worker_ids.add(id(worker))
        self._translation_request_id = int(getattr(self, "_translation_request_id", 0) or 0) + 1

        try:
            self._stream_flush_timer.stop()
        except Exception:
            pass

        request_thread_cancel(worker, wait_ms=80)
        if getattr(self, "_translation_worker", None) is worker:
            self._translation_worker = None

    def _abort_worker(self) -> None:
        abort_note_search = getattr(self, "_abort_ima_note_search_worker", None)
        if callable(abort_note_search) and abort_note_search():
            try:
                self._thinking_card.stop_thinking()
                self._stop_dots_animation()
                self._thinking_card.hide()
            except Exception:
                pass
            if getattr(self, "_is_chatting", False) and hasattr(self, "_chat_history") and self._chat_history:
                self._chat_history[-1]["content"] = "回答已中止。"
                try:
                    self._bubble_view.update_last_ai("回答已中止。")
                    self._bubble_view.show()
                    self._bubble_view.refresh_layout(keep_bottom=True)
                    self._bubble_view.scroll_to_bottom()
                except Exception:
                    self._render_chat_history()
            else:
                self._set_translation_message("回答已中止。")
            self._set_worker_buttons_busy(False, task="qa")
            return

        worker = getattr(self, "_translation_worker", None)
        if worker is None or not worker.isRunning():
            return

        if not hasattr(self, "_aborted_worker_ids"):
            self._aborted_worker_ids = set()
        self._aborted_worker_ids.add(id(worker))
        self._translation_request_id = int(getattr(self, "_translation_request_id", 0) or 0) + 1

        try:
            self._stream_flush_timer.stop()
        except Exception:
            pass
        # 中止会终止 worker，_on_translation_finished 会因 aborted_sender 提前返回而
        # 不会解冻几何；此处主动解冻并释放流式渲染期锁死的高度约束，避免遗留冻结态。
        if bool(getattr(self, "_geometry_frozen", False)):
            self._thaw_window_height()
            self._geometry_frozen = False
        previous_suppress_reposition = bool(getattr(self, "_suppress_reposition", False))
        self._suppress_reposition = True
        try:
            try:
                self._thinking_card.stop_thinking()
                try:
                    self._stop_dots_animation()
                except Exception:
                    pass
                self._thinking_card.hide()
            except Exception:
                pass
        finally:
            self._suppress_reposition = previous_suppress_reposition

        task = str(getattr(self, "_translation_task", "translate") or "translate")
        stopped_text = "翻译已中止。" if task == "translate" else "回答已中止。"

        if getattr(self, "_is_chatting", False) and hasattr(self, "_chat_history") and self._chat_history:
            self._chat_history[-1]["content"] = stopped_text
            try:
                self._bubble_view.update_last_ai(stopped_text)
                self._bubble_view.show()
                self._bubble_view.refresh_layout(keep_bottom=True)
                self._bubble_view.scroll_to_bottom()
            except Exception:
                self._render_chat_history()
        else:
            self._set_translation_message(stopped_text)
        self._set_worker_buttons_busy(False, task=task)
        self._translation_worker = None

        is_hy = False
        try:
            if hasattr(worker, "_model_config"):
                cfg = worker._model_config
                hints = " ".join(
                    str(cfg.get(key, "") or "").lower()
                    for key in ("display_name", "model_name", "base_url")
                )
                is_hy = any(token in hints for token in ("hy-mt", "hunyuan", "ggml-model-q4_k_m", "腾讯模型"))
        except Exception:
            pass

        request_thread_cancel(worker, wait_ms=80)

        if is_hy:
            try:
                from deepcat.local_hunyuan_server import stop_server_now
                stop_server_now()
            except Exception:
                pass

    def _discard_worker_for_clear_chat(self) -> None:
        abort_note_search = getattr(self, "_abort_ima_note_search_worker", None)
        note_search_aborted = bool(abort_note_search()) if callable(abort_note_search) else False
        worker = getattr(self, "_translation_worker", None)
        if worker is None:
            if note_search_aborted:
                try:
                    self._stream_flush_timer.stop()
                except Exception:
                    pass
                try:
                    self._thinking_card.stop_thinking()
                    self._thinking_card.hide()
                except Exception:
                    pass
                try:
                    self._stop_dots_animation()
                except Exception:
                    pass
                try:
                    self._set_worker_buttons_busy(False, task="qa")
                except Exception:
                    pass
            return

        if not hasattr(self, "_aborted_worker_ids"):
            self._aborted_worker_ids = set()
        self._aborted_worker_ids.add(id(worker))
        self._translation_request_id = int(getattr(self, "_translation_request_id", 0) or 0) + 1

        try:
            self._stream_flush_timer.stop()
        except Exception:
            pass
        task = str(getattr(self, "_translation_task", "") or "qa")
        self._stream_markdown_text = ""
        _ocr_text_panel_class()._reset_stream_token_cache(self)
        self._translation_task = ""

        previous_suppress_reposition = bool(getattr(self, "_suppress_reposition", False))
        self._suppress_reposition = True
        try:
            try:
                self._thinking_card.stop_thinking()
                self._thinking_card.hide()
            except Exception:
                pass
            try:
                self._stop_dots_animation()
            except Exception:
                pass
        finally:
            self._suppress_reposition = previous_suppress_reposition

        try:
            self._set_worker_buttons_busy(False, task=task)
        except Exception:
            pass
        self._translation_worker = None

        try:
            worker_running = bool(getattr(worker, "isRunning", lambda: False)())
        except Exception:
            worker_running = False
        if worker_running:
            request_thread_cancel(worker, wait_ms=80)

        _trace_ai_panel(self, "clear_chat.worker_discarded", worker_id=id(worker))

    @staticmethod
    def _make_bottom_model_hover_label(alignment: Qt.AlignmentFlag) -> QLabel:
        label = QLabel("")
        label.setObjectName("BottomModelHoverLabel")
        label.setAlignment(alignment)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        label.setMaximumWidth(220)
        label.setFixedHeight(24)
        label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        label.setStyleSheet(
            "QLabel#BottomModelHoverLabel {"
            " background: transparent;"
            " border: none;"
            " color: #64748b;"
            " font-size: 12px;"
            " font-weight: 600;"
            " padding: 0px 4px;"
            "}"
        )
        label.hide()
        return label

    @staticmethod
    def _model_name_for_hover(purpose: str) -> str:
        try:
            settings = load_settings()
            translator = normalize_translator_settings((getattr(settings, "ui", {}) or {}).get("translator"))
            if str(purpose or "").strip().lower() == "qa":
                return str(translator.get("qa_model", "") or translator.get("current_model", "") or "").strip()
            return str(translator.get("translate_model", "") or translator.get("current_model", "") or "").strip()
        except Exception:
            return ""

    def _show_bottom_model_hover_label(self, purpose: str) -> None:
        purpose = "qa" if str(purpose or "").strip().lower() == "qa" else "translate"
        model_name = self._model_name_for_hover(purpose)
        self._hide_bottom_model_hover_label()
        if not model_name:
            return
        label = self._hover_qa_model_label if purpose == "qa" else self._hover_translate_model_label
        metrics = QFontMetrics(label.font())
        max_width = max(32, int(label.maximumWidth()) - 8)
        label.setText(metrics.elidedText(model_name, Qt.TextElideMode.ElideMiddle, max_width))
        label.setToolTip(model_name)
        label.show()
        setattr(self, "_bottom_model_hover_purpose", purpose)

    def _hide_bottom_model_hover_label(self) -> None:
        for label in (
            getattr(self, "_hover_translate_model_label", None),
            getattr(self, "_hover_qa_model_label", None),
        ):
            if label is not None:
                label.hide()
        setattr(self, "_bottom_model_hover_purpose", "")

    def _show_model_selection_menu(
        self,
        anchor: QWidget,
        purpose: str,
        global_pos: QPoint,
        on_selected: Callable[[str], None],
    ) -> bool:
        # 如果当前正在忙碌生成中，则不允许切换模型
        worker = getattr(self, "_translation_worker", None)
        if worker is not None and worker.isRunning():
            return False
        try:
            settings = load_settings()
            ui = dict(getattr(settings, "ui", {}) or {})
            translator = normalize_translator_settings(ui.get("translator"))
            grouped_models = _group_translator_model_menu_items(translator, purpose)
            if not grouped_models:
                return False
            configs = dict(translator.get("model_configs") or {})
            search_texts = {}
            for provider, names in grouped_models:
                for name in names:
                    search_texts[str(name)] = _translator_model_search_text(str(name), configs.get(name, {}), provider)

            if purpose == "qa":
                current = str(translator.get("qa_model", "") or translator.get("current_model", "gemini-3.5-flash-thinking"))
            else:
                current = str(translator.get("translate_model", "") or translator.get("current_model", "Google翻译"))

            popup = _OcrModelMenuPopup(
                grouped_models,
                current,
                on_selected,
                parent=self,
                search_texts=search_texts,
                purpose=purpose,
            )
            self._model_menu_popup = popup
            popup.destroyed.connect(lambda *_: setattr(self, "_model_menu_popup", None))
            popup.show_at(anchor, global_pos)
            return True
        except Exception:
            return False

    def _show_model_menu(self, button: QPushButton, purpose: str, pos: QPoint) -> None:
        self._show_model_selection_menu(
            button,
            purpose,
            button.mapToGlobal(pos),
            lambda model_name: self._switch_model(purpose, model_name),
        )

    def _show_model_retry_menu(self, idx: int) -> None:
        if not hasattr(self, "_chat_history") or idx < 0 or idx >= len(self._chat_history):
            return
        worker = getattr(self, "_translation_worker", None)
        if worker is not None and worker.isRunning():
            self._show_panel_status("当前回答尚未结束，稍后再切换模型重试。", tone="warning")
            return
        msg = self._chat_history[idx]
        task_type = _ocr_text_panel_class()._message_task_type(msg)
        if task_type != "translate":
            for i in range(idx - 1, -1, -1):
                item = self._chat_history[i]
                if isinstance(item, dict) and item.get("role") == "user":
                    task_type = _ocr_text_panel_class()._message_task_type(item)
                    break
        purpose = "translate" if task_type == "translate" else "qa"

        def _switch_and_retry(model_name: str) -> None:
            self._switch_model(purpose, model_name)
            QTimer.singleShot(0, lambda: self._on_bubble_regenerate(idx))

        anchor = getattr(self, "_bubble_view", None) or self
        shown = self._show_model_selection_menu(anchor, purpose, QCursor.pos(), _switch_and_retry)
        if not shown:
            self._show_panel_status("没有可切换的模型，或当前正在生成中。", tone="warning")

    def _switch_model(self, purpose: str, model_name: str) -> None:
        try:
            def _mut(s: AppSettings) -> AppSettings:
                ui = dict(getattr(s, "ui", {}) or {})
                translator = normalize_translator_settings(ui.get("translator"))
                if purpose == "qa":
                    translator["qa_model"] = model_name
                else:
                    translator["translate_model"] = model_name
                    translator["current_model"] = model_name
                return AppSettings(
                    version=int(s.version),
                    autostart=bool(s.autostart),
                    auto_save=bool(s.auto_save),
                    image_output_dir=str(s.image_output_dir),
                    pdf_output_dir=str(s.pdf_output_dir),
                    hotkey=str(s.hotkey),
                    ui={**ui, "translator": translator},
                    notifications_enabled=bool(getattr(s, "notifications_enabled", False)),
                )

            update_settings(_mut)

            try:
                from PyQt6.QtWidgets import QApplication
                for widget in QApplication.topLevelWidgets():
                    if widget.__class__.__name__ in ("MainWindow", "SettingsDialog"):
                        widget_loading_flag = False
                        if hasattr(widget, "_translator_loading"):
                            widget_loading_flag = getattr(widget, "_translator_loading")
                        if hasattr(widget, "_translator_loading"):
                            setattr(widget, "_translator_loading", True)
                        try:
                            if hasattr(widget, "_fill_model_combos"):
                                try:
                                    widget._fill_model_combos()
                                except Exception:
                                    pass
                            if purpose == "qa":
                                if hasattr(widget, "_qa_model") and widget._qa_model is not None:
                                    widget._qa_model.blockSignals(True)
                                    if hasattr(widget, "_set_combo_selected_value"):
                                        widget._set_combo_selected_value(widget._qa_model, model_name)
                                    else:
                                        widget._qa_model.setCurrentText(model_name)
                                    widget._qa_model.blockSignals(False)
                            else:
                                if hasattr(widget, "_translator_model") and widget._translator_model is not None:
                                    widget._translator_model.blockSignals(True)
                                    if hasattr(widget, "_set_combo_selected_value"):
                                        widget._set_combo_selected_value(widget._translator_model, model_name)
                                    else:
                                        widget._translator_model.setCurrentText(model_name)
                                    widget._translator_model.blockSignals(False)
                            if hasattr(widget, "_load_translator_model_config"):
                                widget._load_translator_model_config(model_name)
                        finally:
                            if hasattr(widget, "_translator_loading"):
                                setattr(widget, "_translator_loading", widget_loading_flag)
            except Exception:
                pass

            purpose_zh = "问答" if purpose == "qa" else "翻译"
            toast = getattr(self, "_on_toast", None)
            if callable(toast):
                toast("模型已切换", f"{purpose_zh}：{model_name}", 1400)
            if str(getattr(self, "_bottom_model_hover_purpose", "") or "") == str(purpose or ""):
                self._show_bottom_model_hover_label(purpose)
        except Exception as e:
            self._show_panel_status(f"快速切换模型失败：{e or '未知错误'}", tone="error", auto_hide_ms=5200)

    @staticmethod
    def _message_value_has_attachments(value: object) -> bool:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and str(item.get("type", "") or "") in {"image_url", "file_url"}:
                    return True
                if _ocr_text_panel_class()._message_value_has_attachments(item):
                    return True
            return False
        if isinstance(value, dict):
            if str(value.get("type", "") or "") in {"image_url", "file_url"}:
                return True
            for key in ("attachments", "content", "image_url", "file_url", "parts"):
                if _ocr_text_panel_class()._message_value_has_attachments(value.get(key)):
                    return True
        return False

    @staticmethod
    def _messages_have_attachments(messages: object) -> bool:
        if not isinstance(messages, list):
            return False
        for msg in messages:
            if isinstance(msg, dict) and _ocr_text_panel_class()._message_value_has_attachments(msg.get("content")):
                return True
        return False

    @staticmethod
    def _trim_status_text(text: str) -> str:
        value = str(text or "").strip()
        while value.endswith(("...", "…", "。", ".")):
            value = value[:-1].strip()
        return value

    @staticmethod
    def _thinking_status_labels(
        *,
        runtime_purpose: str,
        button_task: str,
        loading_text: str,
        cfg: Optional[dict[str, object]] = None,
        has_attachments: bool = False,
        note_search: bool = False,
    ) -> tuple[str, str, str]:
        status_title = "思考中"
        done_status_title = "已思考"
        model_type = str((cfg or {}).get("model_type", "") or "").strip().lower()
        if str(runtime_purpose or "") == "translate":
            return status_title, done_status_title, "正在翻译..."
        if bool(note_search):
            return status_title, done_status_title, "正在搜索笔记..."
        if model_type == "openai_images":
            return status_title, done_status_title, "正在生成图片..."
        if bool(has_attachments):
            return status_title, done_status_title, "正在解析附件..."

        task = str(button_task or "").strip()
        if task == "ai_search":
            return status_title, done_status_title, "正在进行 AI 搜索..."
        if task == "explain":
            return status_title, done_status_title, "正在生成解释..."
        if task == "summarize":
            return status_title, done_status_title, "正在生成总结..."
        if task == "reply_prompt":
            return status_title, done_status_title, "正在生成回复..."

        title = _ocr_text_panel_class()._trim_status_text(loading_text)
        if not title or title == "正在回答":
            title = "正在生成回复"
        return status_title, done_status_title, f"{title}..."

    def _preview_runtime_config(self, purpose: str) -> dict[str, object]:
        try:
            cfg, _use_proxy, _proxy_url = self._translator_runtime_config(purpose)
            return dict(cfg or {})
        except Exception:
            return {}

    def _run_quick_command(
        self,
        prompt: str,
        loading_text: str,
        button_task: str,
        result_label: str,
        *,
        display_text: str = "",
        execution_text: str = "",
    ) -> None:
        if not prompt:
            return

        _trace_ai_panel(
            self,
            "quick_command.begin",
            prompt_len=len(str(prompt or "")),
            display_len=len(str(display_text or "")),
            execution_len=len(str(execution_text or "")),
            button_task=button_task,
            history_len=len(getattr(self, "_chat_history", []) or []),
        )
        if not hasattr(self, "_chat_history"):
            self._chat_history = []

        task_type = _ocr_text_panel_class()._history_task_type_for_button(button_task)
        display_val = str(display_text or prompt).strip()
        run_text = str(execution_text or prompt)
        runtime_purpose = "translate" if str(button_task or "") == "translate" else "qa"
        status_title, done_status_title, placeholder_text = _ocr_text_panel_class()._thinking_status_labels(
            runtime_purpose=runtime_purpose,
            button_task=str(button_task or ""),
            loading_text=str(loading_text or ""),
            cfg=_ocr_text_panel_class()._preview_runtime_config(self, runtime_purpose),
        )
        self._chat_history.append(
            {
                "role": "user",
                "content": prompt,
                "display_content": display_val,
                "regenerate_text": run_text,
                "task_type": task_type,
            }
        )
        self._chat_history.append(
            {
                "role": "assistant",
                "content": "",
                "task_type": task_type,
                "status_text": placeholder_text,
            }
        )
        self._active_assistant_msg_index = len(self._chat_history) - 1

        self._is_chatting = True
        self._current_input_origin = "manual"
        self._clear_editor_silently()

        if hasattr(self, "_btn_clear_chat"):
            self._btn_clear_chat.show()
        if hasattr(self, "_btn_qa"):
            self._btn_qa.setText("发送")
            self._btn_qa.setToolTip("发送追问")

        if self.isVisible():
            frame_geo = self.frameGeometry()
            self._keep_bottom_y_on_next_reposition = frame_geo.y() + frame_geo.height()
        _trace_ai_panel(
            self,
            "quick_command.keep_bottom_set",
            keep_bottom=self._keep_bottom_y_on_next_reposition,
            history_len=len(getattr(self, "_chat_history", []) or []),
        )

        _trace_ai_panel(self, "quick_command.before_render", history_len=len(getattr(self, "_chat_history", []) or []))
        self._render_chat_history(is_streaming=True)
        self._thinking_card.start_thinking(
            reposition=False,
            model_name=getattr(self, "_translation_model_display", ""),
            status_title=status_title,
            done_status_title=done_status_title,
        )
        self._reposition()
        _ocr_text_panel_class()._ensure_latest_chat_visible_after_layout(self)
        _trace_ai_panel(self, "quick_command.after_render", history_len=len(getattr(self, "_chat_history", []) or []))
        self._schedule_question_editor_ime_focus_reset()
        _trace_ai_panel(
            self,
            "quick_command.before_run_prompt",
            run_text_len=len(run_text),
            loading_text=loading_text,
            button_task=button_task,
        )
        self._run_qa_prompt(run_text, loading_text, button_task=button_task, result_label=result_label)
        _trace_ai_panel(
            self,
            "quick_command.after_run_prompt",
            run_text_len=len(run_text),
            loading_text=loading_text,
            button_task=button_task,
        )

    def _translate_text(self) -> None:
        worker = getattr(self, "_translation_worker", None)
        if worker is not None and worker.isRunning():
            self._abort_worker()
            return

        try:
            if hasattr(self, "_btn_collapse") and self._btn_collapse._collapsed:
                self._toggle_bottom_collapsed()
        except Exception:
            pass

        text = self._editor.toPlainText().strip()
        if not text:
            return
        _ocr_text_panel_class()._clear_ocr_input_auto_height(self)

        src = self._source_lang.currentText()
        tgt = self._target_lang.currentText()

        def is_chinese(t: str) -> bool:
            if not t:
                return False
            import re
            chinese_chars = re.findall(r"[\u4e00-\u9fff]", t)
            return len(chinese_chars) > len(t) * 0.3

        if src == "自动检测":
            if OcrTranslationWorker._is_bidirectional_target(tgt):
                actual_target = "英文" if is_chinese(text) else "中文"
            else:
                actual_target = tgt
            prompt = f"请将以下文本翻译成{actual_target}：\n{text}"
        else:
            if OcrTranslationWorker._is_bidirectional_target(tgt):
                actual_target = "英文" if src == "中文" else "中文"
            else:
                actual_target = tgt
            prompt = f"请将以下文本从{src}翻译成{actual_target}：\n{text}"

        self._run_quick_command(prompt, "正在翻译...", button_task="translate", result_label="翻译结果", display_text=text, execution_text=text)

    def _source_is_original_text(self, text: str) -> bool:
        return str(text or "").strip() == str(getattr(self, "_original_text", "") or "").strip()

    @staticmethod
    def _format_prompt_with_text(template: str, text: str) -> str:
        prompt = str(template or "").strip()
        content = str(text or "").strip()
        if not prompt:
            return content
        for token in ("[划词内容]", "[具体概念/现象/问题]", "{text}", "{{text}}"):
            if token in prompt:
                return prompt.replace(token, content)
        return f"{prompt}\n\n{content}".strip()

    def _translator_prompt(self, key: str, default: str) -> str:
        try:
            settings = load_settings()
            translator = normalize_translator_settings((getattr(settings, "ui", {}) or {}).get("translator"))
            return str(translator.get(str(key), default) or default)
        except Exception:
            return str(default)

    def translate_current_text(self) -> None:
        self._translate_text()

    def reply_prompt_current_text(self) -> None:
        text = self._editor.toPlainText().strip()
        prompt = self._format_prompt_with_text(self._translator_prompt("reply_prompt", DEFAULT_REPLY_PROMPT), text)
        self._run_quick_command(prompt, "正在生成回复...", button_task="reply_prompt", result_label="回复结果", display_text=text)

    def ai_search_current_text(self) -> None:
        text = self._editor.toPlainText().strip()
        prompt = self._format_prompt_with_text(self._translator_prompt("ai_search_prompt", DEFAULT_AI_SEARCH_PROMPT), text)
        self._run_quick_command(prompt, "正在进行AI搜索...", button_task="ai_search", result_label="搜索结果", display_text=text)

    def optimize_prompt_current_text(self) -> None:
        self.ai_search_current_text()

    def explain_current_text(self) -> None:
        text = self._editor.toPlainText().strip()
        prompt = self._format_prompt_with_text(self._translator_prompt("explain_prompt", DEFAULT_EXPLAIN_PROMPT), text)
        self._run_quick_command(prompt, "正在解释...", button_task="explain", result_label="解释结果", display_text=text)

    def summarize_current_text(self) -> None:
        text = self._editor.toPlainText().strip()
        prompt = self._format_prompt_with_text(self._translator_prompt("summary_prompt", DEFAULT_SUMMARY_PROMPT), text)
        self._run_quick_command(prompt, "正在总结...", button_task="summarize", result_label="总结结果", display_text=text)

    def _build_qa_prompt(self, text: str) -> str:
        text = str(text or "").strip()
        if self._source_is_original_text(text):
            return self._format_prompt_with_text(self._translator_prompt("explain_prompt", DEFAULT_EXPLAIN_PROMPT), text)
        return text

    @staticmethod
    def _ima_note_search_query(text: str) -> str:
        match = re.match(r"^\s*搜索笔记\s*[:：]\s*(.+?)\s*$", str(text or ""), flags=re.S)
        return match.group(1).strip() if match else ""

    def _load_ima_note_config_for_chat(self) -> dict[str, Any]:
        try:
            from deepcat.table_notes_store import TableNotesStore

            tabs, active = TableNotesStore().load_note_tabs()
        except Exception:
            tabs, active = [], 0
        candidates: list[dict[str, Any]] = []
        if 0 <= int(active or 0) < len(tabs):
            candidates.append(dict(tabs[int(active or 0)].get("ima_config") or {}))
        for tab in tabs:
            cfg = dict(tab.get("ima_config") or {})
            if cfg not in candidates:
                candidates.append(cfg)
        for cfg in candidates:
            if (
                bool(cfg.get("enabled", False))
                and str(cfg.get("client_id", "") or "").strip()
                and str(cfg.get("api_key", "") or "").strip()
            ):
                return cfg
        return {}

    def _build_ima_note_search_summary_prompt(self, user_text: str) -> str:
        query = self._ima_note_search_query(user_text)
        if not query:
            return str(user_text or "").strip()
        cfg = self._load_ima_note_config_for_chat()
        if not cfg:
            raise RuntimeError("未找到可用的 IMA 笔记配置。请先在记事本标签页右键“设置IMA”，启用 IMA 并保存 Client ID / API Key。")
        from deepcat.ima_client import ImaClient, ImaCredentials

        client = ImaClient(
            ImaCredentials(
                str(cfg.get("client_id", "") or ""),
                str(cfg.get("api_key", "") or ""),
            ),
            timeout=20.0,
        )
        return client.build_note_search_prompt(query)

    def _start_ima_note_search_worker(self, prompt_text: str) -> None:
        worker = ImaNoteSearchWorker(self, prompt_text)
        self._ima_note_search_worker = worker
        worker.search_finished.connect(self._on_ima_note_search_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _abort_ima_note_search_worker(self) -> bool:
        worker = getattr(self, "_ima_note_search_worker", None)
        if worker is None:
            return False
        self._ima_note_search_worker = None
        request_thread_cancel(worker, wait_ms=80)
        return True

    def _on_ima_note_search_finished(self, prompt_text: str, success: bool, error_msg: str) -> None:
        sender = self.sender()
        if sender is not None and sender is not getattr(self, "_ima_note_search_worker", None):
            return
        self._ima_note_search_worker = None
        if not bool(success):
            self._thinking_card.stop_thinking()
            try:
                self._stop_dots_animation()
            except Exception:
                pass
            self._set_worker_buttons_busy(False, task="qa")
            self._show_qa_error(f"IMA 笔记搜索失败：{error_msg}")
            return
        if getattr(self, "_chat_history", None):
            user_index = len(self._chat_history) - 2 if len(self._chat_history) >= 2 else len(self._chat_history) - 1
            if user_index >= 0:
                try:
                    user_message = self._chat_history[user_index]
                    original_content = user_message.get("content", "")
                    if isinstance(original_content, list) and original_content:
                        first_item = original_content[0]
                        if isinstance(first_item, dict) and first_item.get("type") == "text":
                            first_item["text"] = str(prompt_text or "")
                    else:
                        user_message["content"] = str(prompt_text or "")
                except Exception:
                    pass
        self._run_qa_prompt(str(prompt_text or ""), "正在回答...", button_task="qa", result_label="问答结果")

    def _on_follow_up_requested(self, query: str) -> None:
        query_text = str(query or "").strip()
        if not query_text:
            return

        worker = getattr(self, "_translation_worker", None)
        if worker is not None and worker.isRunning():
            try:
                self._show_panel_status("当前回答还在生成，稍后再追问。", tone="info", auto_hide_ms=1800)
            except Exception:
                pass
            return

        self._pending_attachments = []
        self._pending_attachment_previews = []
        self._pending_attachment_labels = []
        _refresh_attachment_preview_bar_if_available(self)

        old_loading = bool(getattr(self, "_loading_text", False))
        self._loading_text = True
        try:
            self._editor.setPlainText(query_text)
        finally:
            self._loading_text = old_loading

        self._answer_question()

    def _answer_question(self) -> None:
        _trace_ai_panel(
            self,
            "answer_question.begin",
            history_len=len(getattr(self, "_chat_history", []) or []),
            worker_running=bool(
                (
                    getattr(self, "_translation_worker", None) is not None
                    and getattr(self._translation_worker, "isRunning", lambda: False)()
                )
                or (
                    getattr(self, "_ima_note_search_worker", None) is not None
                    and getattr(self._ima_note_search_worker, "isRunning", lambda: False)()
                )
            ),
        )
        if self._translation_worker is not None and self._translation_worker.isRunning():
            self._abort_worker()
            _trace_ai_panel(self, "answer_question.abort_running_worker")
            return
        note_search_worker = getattr(self, "_ima_note_search_worker", None)
        if note_search_worker is not None and note_search_worker.isRunning():
            self._abort_ima_note_search_worker()
            _trace_ai_panel(self, "answer_question.abort_running_worker")
            return

        text = self._editor.toPlainText().strip()
        self._sync_pending_attachments_from_editor_text()
        attachments = getattr(self, "_pending_attachments", [])
        if not text and not attachments:
            _trace_ai_panel(self, "answer_question.empty_return")
            return
        _ocr_text_panel_class()._clear_ocr_input_auto_height(self)

        import re
        resolved_text, display_text = _ocr_text_panel_class()._resolve_placeholder_card_prompt(self, text)
        cleaned_text = re.sub(r"📎\s*\[已添加附件:\s*[^\]]+\]", "", resolved_text).strip()
        display_clean_text = re.sub(r"📎\s*\[已添加附件:\s*[^\]]+\]", "", display_text).strip()
        if not cleaned_text and attachments:
            cleaned_text = "请帮我分析一下这个文件。"
        if not display_clean_text and attachments:
            display_clean_text = cleaned_text
        self._maybe_show_context_length_warning(cleaned_text)

        self._is_append_action = False
        if getattr(self, "_associated_note_tab", None) is not None:
            cleaned_text_lstrip = cleaned_text.lstrip(" \t\n\r,.:;，。：；")
            if any(cleaned_text_lstrip.startswith(word) for word in ("加入", "添加", "增加")):
                self._is_append_action = True

        if not hasattr(self, "_chat_history"):
            self._chat_history = []

        if attachments:
            attachment_previews = list(getattr(self, "_pending_attachment_previews", []))
            display_attachments_str = "\n" + "\n".join(
                self._format_display_attachment(a, attachment_previews[idx] if idx < len(attachment_previews) else None)
                for idx, a in enumerate(attachments)
            )
        else:
            display_attachments_str = ""

        note_search_query = _ocr_text_panel_class()._ima_note_search_query(cleaned_text)
        use_note_search_worker = bool(note_search_query)
        prompt = str(cleaned_text or "")
        status_title, done_status_title, placeholder_text = _ocr_text_panel_class()._thinking_status_labels(
            runtime_purpose="qa",
            button_task="qa",
            loading_text="正在回答...",
            cfg=_ocr_text_panel_class()._preview_runtime_config(self, "qa"),
            has_attachments=bool(attachments),
            note_search=use_note_search_worker,
        )

        if not self._chat_history:
            if use_note_search_worker:
                display_prompt = prompt
            else:
                display_prompt = self._build_qa_prompt(prompt)
            display_val = display_clean_text + display_attachments_str
            if attachments:
                content = [{"type": "text", "text": display_prompt}] + attachments
            else:
                content = display_prompt
            self._chat_history.append({"role": "user", "content": content, "display_content": display_val, "task_type": "qa"})
            prompt = display_prompt
        else:
            display_val = display_clean_text + display_attachments_str
            if attachments:
                content = [{"type": "text", "text": prompt}] + attachments
            else:
                content = prompt
            self._chat_history.append({"role": "user", "content": content, "display_content": display_val, "task_type": "qa"})

        self._chat_history.append(
            {
                "role": "assistant",
                "content": "",
                "task_type": "qa",
                "status_text": placeholder_text,
            }
        )
        self._active_assistant_msg_index = len(self._chat_history) - 1

        self._is_chatting = True
        self._preserve_cleared_output_area = False
        self._cleared_output_restore_geometry = QRect()
        self._pending_attachments = []
        self._pending_attachment_previews = []
        self._pending_attachment_labels = []
        _refresh_attachment_preview_bar_if_available(self)

        if hasattr(self, "_btn_clear_chat"):
            self._btn_clear_chat.show()
        if hasattr(self, "_btn_qa"):
            self._btn_qa.setText("发送")
            self._btn_qa.setToolTip("发送追问")

        # 记录发送前的底部 y 坐标，保证窗口拉高时，底部不动，顶部自动往上舒展
        if self.isVisible():
            frame_geo = self.frameGeometry()
            self._keep_bottom_y_on_next_reposition = frame_geo.y() + frame_geo.height()
        _trace_ai_panel(
            self,
            "answer_question.keep_bottom_set",
            keep_bottom=self._keep_bottom_y_on_next_reposition,
            history_len=len(getattr(self, "_chat_history", []) or []),
            attachment_count=len(attachments or []),
        )

        self._current_input_origin = "manual"
        self._clear_editor_silently()
        _trace_ai_panel(self, "answer_question.after_clear_editor", history_len=len(getattr(self, "_chat_history", []) or []))
        self._remove_chat_draft()
        _trace_ai_panel(self, "answer_question.before_render", history_len=len(getattr(self, "_chat_history", []) or []))
        self._render_chat_history(is_streaming=True)
        self._thinking_card.start_thinking(
            reposition=False,
            model_name=getattr(self, "_translation_model_display", ""),
            status_title=status_title,
            done_status_title=done_status_title,
        )
        self._reposition()
        _ocr_text_panel_class()._ensure_latest_chat_visible_after_layout(self)
        _trace_ai_panel(self, "answer_question.after_render", history_len=len(getattr(self, "_chat_history", []) or []))
        self._schedule_question_editor_ime_focus_reset()
        _trace_ai_panel(
            self,
            "answer_question.before_run_prompt",
            cleaned_len=len(str(cleaned_text or "")),
            attachment_count=len(attachments or []),
        )
        if use_note_search_worker:
            self._set_worker_buttons_busy(True, task="qa")
            self._translation_task = "qa"
            self._translation_started_ts = float(time.time())
            self._translation_model_display = "IMA 笔记搜索"
            try:
                self._start_dots_animation()
            except Exception:
                pass
            try:
                self._start_ima_note_search_worker(cleaned_text)
            except Exception as exc:
                _ocr_text_panel_class()._finish_preflight_prompt_failure(self, "qa")
                self._ima_note_search_worker = None
                self._show_qa_error(f"IMA 笔记搜索启动失败：{exc}")
        else:
            self._run_qa_prompt(prompt, "正在回答...", button_task="qa", result_label="问答结果")
        _trace_ai_panel(
            self,
            "answer_question.after_run_prompt",
            cleaned_len=len(str(cleaned_text or "")),
            attachment_count=len(attachments or []),
        )

    def _on_bubble_edit_from(self, idx: int) -> None:
        try:
            if not hasattr(self, "_chat_history") or idx < 0 or idx >= len(self._chat_history):
                return
            if _ocr_text_panel_class()._message_action_worker_running(self):
                self._show_panel_status("当前回答尚未结束，稍后再编辑重发。", tone="warning")
                return

            user_idx = idx
            if str(self._chat_history[user_idx].get("role", "") or "") != "user":
                user_idx = -1
                for i in range(idx - 1, -1, -1):
                    if isinstance(self._chat_history[i], dict) and self._chat_history[i].get("role") == "user":
                        user_idx = i
                        break
            if user_idx < 0:
                self._show_panel_status("无法编辑重发：找不到关联的问题。", tone="warning")
                return

            user_msg = copy.deepcopy(self._chat_history[user_idx])
            self._save_current_chat_draft(show_feedback=False)
            attachment_count = _ocr_text_panel_class()._load_user_message_into_editor_for_resend(self, user_msg)
            self._chat_history = self._chat_history[:user_idx]
            self._is_chatting = bool(self._chat_history)
            self._stream_render_signature = None
            _ocr_text_panel_class()._reset_context_management_state(self)
            self._render_chat_history(is_streaming=False)
            self._reposition()
            suffix = "，原附件已带回待发送区" if attachment_count else ""
            self._show_panel_status(f"已载入该问题，可编辑后重新发送{suffix}。", tone="success")
        except Exception as exc:
            self._show_panel_status(f"编辑重发失败：{exc}", tone="error", auto_hide_ms=5200)

    def _on_bubble_branch_from(self, idx: int) -> None:
        try:
            if not hasattr(self, "_chat_history") or idx < 0 or idx >= len(self._chat_history):
                return
            if _ocr_text_panel_class()._message_action_worker_running(self):
                self._show_panel_status("当前回答尚未结束，稍后再新建分支。", tone="warning")
                return

            branch_messages = copy.deepcopy(self._chat_history[: idx + 1])
            while branch_messages:
                last_msg = branch_messages[-1]
                if not isinstance(last_msg, dict):
                    branch_messages.pop()
                    continue
                if str(last_msg.get("role", "") or "") == "assistant" and not str(last_msg.get("content", "") or "").strip():
                    branch_messages.pop()
                    continue
                break
            if not branch_messages:
                return

            self._save_current_chat_draft(show_feedback=False)
            self._discard_worker_for_clear_chat()
            self._chat_history = branch_messages
            self._current_session_record_id = None
            self._is_chatting = True
            self._stream_render_signature = None
            _ocr_text_panel_class()._reset_context_management_state(self)
            self._clear_editor_silently()
            self._clear_pending_attachments()
            self._history_forced_output_area = False
            try:
                self._history_sidebar.select_record(None)
            except Exception:
                pass
            if hasattr(self, "_btn_qa"):
                self._btn_qa.setText("发送")
                self._btn_qa.setToolTip("发送追问")
            self._render_chat_history(is_streaming=False)
            self._reposition()
            self._show_panel_status("已从此处新建分支会话。", tone="success")
        except Exception as exc:
            self._show_panel_status(f"新建分支失败：{exc}", tone="error", auto_hide_ms=5200)

    def _on_bubble_pin_context(self, idx: int) -> None:
        try:
            if not hasattr(self, "_chat_history") or idx < 0 or idx >= len(self._chat_history):
                return
            if _ocr_text_panel_class()._message_action_worker_running(self):
                self._show_panel_status("当前回答尚未结束，稍后再固定上下文。", tone="warning")
                return

            msg = self._chat_history[idx]
            if not isinstance(msg, dict) or str(msg.get("role", "") or "") not in {"user", "assistant"}:
                return
            if not _ocr_text_panel_class()._message_text_for_context_estimate(self, msg.get("content", "")).strip():
                self._show_panel_status("空消息无法固定到上下文。", tone="warning")
                return

            new_state = not bool(msg.get("context_pinned", False))
            if new_state:
                msg["context_pinned"] = True
            else:
                msg.pop("context_pinned", None)
            self._stream_render_signature = None
            self._context_warning_dismissed = False
            self._context_warning_dismissed_snapshot = None
            self._context_warning_current_snapshot = None
            self._render_chat_history(is_streaming=False)
            self._reposition()
            editor = getattr(self, "_editor", None)
            next_text = editor.toPlainText().strip() if editor is not None else ""
            _ocr_text_panel_class()._maybe_show_context_length_warning(self, next_text)
            self._show_panel_status("已固定到后续上下文。" if new_state else "已取消固定上下文。", tone="success")
        except Exception as exc:
            self._show_panel_status(f"固定上下文失败：{exc}", tone="error", auto_hide_ms=5200)

    def _on_bubble_copy(self, idx: int) -> None:
        try:
            if not hasattr(self, "_chat_history") or idx < 0 or idx >= len(self._chat_history):
                return
            msg = self._chat_history[idx]
            text = msg.get("content", "")
            if not isinstance(text, str):
                text = str(msg.get("display_content", "") or "")
            if msg.get("role") == "assistant":
                copied = copy_markdown_to_clipboard(str(text or ""))
            else:
                copied = copy_plain_text_to_clipboard(str(text or ""))
            if copied:
                self._show_light_feedback("气泡内容已复制到剪贴板。", tone="success", auto_hide_ms=1500)
            else:
                self._show_light_feedback("复制失败：剪贴板不可用。", tone="error", auto_hide_ms=2600)
        except Exception as exc:
            self._show_light_feedback(f"复制失败：{exc}", tone="error", auto_hide_ms=5200)

    def _on_bubble_copy_error(self, idx: int) -> None:
        try:
            if not hasattr(self, "_chat_history") or idx < 0 or idx >= len(self._chat_history):
                return
            msg = self._chat_history[idx]
            text = msg.get("content", "")
            if not isinstance(text, str):
                text = str(msg.get("display_content", "") or text or "")
            copied = copy_plain_text_to_clipboard(str(text or ""))
            if copied:
                self._show_light_feedback("错误信息已复制到剪贴板。", tone="success", auto_hide_ms=1500)
            else:
                self._show_light_feedback("复制失败：剪贴板不可用。", tone="error", auto_hide_ms=2600)
        except Exception as exc:
            self._show_light_feedback(f"复制错误信息失败：{exc}", tone="error", auto_hide_ms=5200)

    def _on_bubble_switch_model_retry(self, idx: int) -> None:
        try:
            self._show_model_retry_menu(idx)
        except Exception as exc:
            self._show_panel_status(f"切换模型重试失败：{exc}", tone="error", auto_hide_ms=5200)

    def _active_assistant_index(self) -> int:
        history = getattr(self, "_chat_history", None)
        if not isinstance(history, list) or not history:
            return -1
        try:
            active_value = getattr(self, "_active_assistant_msg_index", None)
            idx = int(active_value) if active_value is not None else -1
        except Exception:
            idx = -1
        if 0 <= idx < len(history) and isinstance(history[idx], dict) and str(history[idx].get("role", "") or "") == "assistant":
            return idx
        last_idx = len(history) - 1
        if isinstance(history[last_idx], dict) and str(history[last_idx].get("role", "") or "") == "assistant":
            return last_idx
        return -1

    def _update_assistant_bubble_at(
        self,
        index: int,
        text: str,
        *,
        streaming: bool = False,
        model_name: str = "",
        elapsed: float | None = None,
        reply_tokens: int | None = None,
        total_tokens: int | None = None,
        start_time: str = "",
    ) -> bool:
        view = getattr(self, "_bubble_view", None)
        if view is None:
            return False
        kwargs = {
            "is_markdown": True,
            "streaming": bool(streaming),
            "model_name": model_name or None,
            "elapsed": elapsed,
            "reply_tokens": reply_tokens,
            "total_tokens": total_tokens,
            "start_time": start_time or None,
        }
        if bool(streaming):
            apply_coalesced = getattr(view, "apply_coalesced_stream_message_at", None)
            if callable(apply_coalesced):
                try:
                    coalesced_kwargs = dict(kwargs)
                    coalesced_kwargs.pop("streaming", None)
                    return bool(apply_coalesced(int(index), str(text or ""), **coalesced_kwargs))
                except Exception:
                    pass
        update_target = getattr(view, "update_message_at", None)
        if callable(update_target):
            try:
                if update_target(int(index), str(text or ""), **kwargs):
                    return True
            except TypeError:
                pass
            except Exception:
                pass
        history = getattr(self, "_chat_history", []) or []
        if int(index) == len(history) - 1 and hasattr(view, "update_last_ai"):
            view.update_last_ai(str(text or ""), **kwargs)
            return True
        return False

    def _on_bubble_new_round_from_error(self, idx: int) -> None:
        try:
            if not hasattr(self, "_chat_history") or idx < 0 or idx >= len(self._chat_history):
                return
            worker = getattr(self, "_translation_worker", None)
            if worker is not None and worker.isRunning():
                self._show_panel_status("当前回答尚未结束，稍后再新开一轮。", tone="warning")
                return

            user_idx = -1
            for i in range(idx - 1, -1, -1):
                if self._chat_history[i].get("role") == "user":
                    user_idx = i
                    break
            if user_idx == -1:
                self._show_panel_status("无法新开一轮：找不到关联的问题。", tone="warning")
                return

            failed_msg = self._chat_history[idx]
            user_msg = self._chat_history[user_idx]
            task_type = (
                "translate"
                if _ocr_text_panel_class()._message_task_type(failed_msg) == "translate" or _ocr_text_panel_class()._message_task_type(user_msg) == "translate"
                else "qa"
            )
            prompt = _ocr_text_panel_class()._regenerate_text_for_user_message(user_msg, task_type)
            has_attachments = _ocr_text_panel_class()._message_value_has_attachments(user_msg.get("content"))
            status_title, done_status_title, placeholder_text = _ocr_text_panel_class()._thinking_status_labels(
                runtime_purpose=task_type,
                button_task=task_type,
                loading_text="正在翻译..." if task_type == "translate" else "正在回答...",
                cfg=_ocr_text_panel_class()._preview_runtime_config(self, task_type),
                has_attachments=has_attachments,
            )

            retry_user_msg = copy.deepcopy(user_msg)
            retry_user_msg["role"] = "user"
            retry_user_msg["task_type"] = task_type
            if not str(retry_user_msg.get("display_content", "") or "").strip():
                retry_user_msg["display_content"] = str(prompt or "")
            start_index = len(self._chat_history)
            self._chat_history.append(retry_user_msg)
            self._chat_history.append(
                {
                    "role": "assistant",
                    "content": "",
                    "task_type": task_type,
                    "status_text": placeholder_text,
                }
            )
            self._active_assistant_msg_index = len(self._chat_history) - 1

            self._is_chatting = True
            appended = False
            append_messages = getattr(getattr(self, "_bubble_view", None), "append_messages", None)
            if callable(append_messages):
                appended = bool(append_messages(self._chat_history, start_index=start_index, keep_bottom=True))
            if not appended:
                self._render_chat_history(is_streaming=True)
            self._thinking_card.start_thinking(
                reposition=False,
                model_name=getattr(self, "_translation_model_display", ""),
                status_title=status_title,
                done_status_title=done_status_title,
            )
            self._reposition()
            _ocr_text_panel_class()._ensure_latest_chat_visible_after_layout(self)

            if task_type == "translate":
                self._run_qa_prompt(prompt, "正在翻译...", button_task="translate", result_label="翻译结果")
            else:
                self._run_qa_prompt(prompt, "正在回答...", button_task="qa", result_label="问答结果")
        except Exception as exc:
            self._show_panel_status(f"新开一轮失败：{exc}", tone="error", auto_hide_ms=5200)

    def _on_placeholder_card_clicked(self, title: str, prompt_text: str = "") -> None:
        if hasattr(self, "_editor"):
            try:
                self._set_placeholder_card_editor(title, prompt_text)
                self._editor.setFocus(Qt.FocusReason.OtherFocusReason)
                cursor = self._editor.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                self._editor.setTextCursor(cursor)
            except Exception:
                pass

    def _on_bubble_delete(self, idx: int) -> None:
        try:
            if not hasattr(self, "_chat_history") or idx < 0 or idx >= len(self._chat_history):
                return

            role = str(self._chat_history[idx].get("role", "") or "")
            self._chat_history.pop(idx)
            self._stream_render_signature = None
            try:
                active_idx = int(getattr(self, "_active_assistant_msg_index", -1) or -1)
            except Exception:
                active_idx = -1
            if active_idx == idx:
                self._active_assistant_msg_index = None
            elif active_idx > idx:
                self._active_assistant_msg_index = active_idx - 1

            if not self._chat_history:
                self._clear_chat_history()
            else:
                removed = False
                remove_message = getattr(getattr(self, "_bubble_view", None), "remove_message_at", None)
                if callable(remove_message):
                    removed = bool(remove_message(idx, keep_bottom=False))
                if not removed:
                    self._render_chat_history(is_streaming=False)
                self._reposition()
            self._show_panel_status("已删除回答。" if role == "assistant" else "已删除问题。", tone="success")
        except Exception as exc:
            self._show_panel_status(f"删除失败：{exc}", tone="error", auto_hide_ms=5200)

    def _on_bubble_keep_partial(self, idx: int) -> None:
        try:
            if not hasattr(self, "_chat_history") or idx < 0 or idx >= len(self._chat_history):
                return
            msg = self._chat_history[idx]
            partial = str(msg.get("partial_content", "") or "").strip()
            if not partial:
                self._show_panel_status("没有可保留的部分回答。", tone="warning")
                return
            msg["content"] = partial
            for key in ("failed", "can_continue", "partial_content", "error_message"):
                msg.pop(key, None)
            updated = _ocr_text_panel_class()._update_assistant_bubble_at(self, idx, partial)
            if not updated:
                self._render_chat_history(is_streaming=False)
            self._show_panel_status("已保留部分回答。", tone="success")
        except Exception as exc:
            self._show_panel_status(f"保留部分回答失败：{exc}", tone="error", auto_hide_ms=5200)

    def _on_bubble_regenerate(self, idx: int) -> None:
        try:
            if not hasattr(self, "_chat_history") or idx < 0 or idx >= len(self._chat_history):
                return

            msg = self._chat_history[idx]
            if msg.get("role") == "user":
                # 问题气泡的重新生成：不截断旧回答，直接在末尾追加新的 AI 回答
                task_type = _ocr_text_panel_class()._message_task_type(msg)
                prompt = _ocr_text_panel_class()._regenerate_text_for_user_message(msg, task_type)
                has_attachments = _ocr_text_panel_class()._message_value_has_attachments(msg.get("content"))
                status_title, done_status_title, placeholder_text = _ocr_text_panel_class()._thinking_status_labels(
                    runtime_purpose=task_type,
                    button_task=task_type,
                    loading_text="正在翻译..." if task_type == "translate" else "正在回答...",
                    cfg=_ocr_text_panel_class()._preview_runtime_config(self, task_type),
                    has_attachments=has_attachments,
                )
                self._chat_history.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "task_type": task_type,
                        "status_text": placeholder_text,
                    }
                )
                start_index = len(self._chat_history) - 1
                self._active_assistant_msg_index = start_index
            else:
                # 回答气泡的重新生成：只替换目标回答，不重绘其他气泡
                user_idx = -1
                for i in range(idx - 1, -1, -1):
                    if self._chat_history[i].get("role") == "user":
                         user_idx = i
                         break

                if user_idx == -1:
                    self._show_panel_status("无法重新生成：找不到关联的问题。", tone="warning")
                    return

                user_msg = self._chat_history[user_idx]
                task_type = (
                    "translate"
                    if _ocr_text_panel_class()._message_task_type(msg) == "translate" or _ocr_text_panel_class()._message_task_type(user_msg) == "translate"
                    else "qa"
                )
                prompt = _ocr_text_panel_class()._regenerate_text_for_user_message(user_msg, task_type)
                has_attachments = _ocr_text_panel_class()._message_value_has_attachments(user_msg.get("content"))
                status_title, done_status_title, placeholder_text = _ocr_text_panel_class()._thinking_status_labels(
                    runtime_purpose=task_type,
                    button_task=task_type,
                    loading_text="正在翻译..." if task_type == "translate" else "正在回答...",
                    cfg=_ocr_text_panel_class()._preview_runtime_config(self, task_type),
                    has_attachments=has_attachments,
                )

                replacement_msg = {
                    "role": "assistant",
                    "content": "",
                    "task_type": task_type,
                    "status_text": placeholder_text,
                }
                self._chat_history[idx] = replacement_msg
                start_index = idx
                self._active_assistant_msg_index = idx

            self._is_chatting = True
            local_updated = False
            bubble_view = getattr(self, "_bubble_view", None)
            if msg.get("role") == "user":
                append_messages = getattr(bubble_view, "append_messages", None)
                if callable(append_messages):
                    local_updated = bool(append_messages(self._chat_history, start_index=start_index, keep_bottom=True))
            else:
                replace_message = getattr(bubble_view, "replace_message_at", None)
                if callable(replace_message):
                    local_updated = bool(replace_message(idx, replacement_msg, keep_bottom=True))
            if not local_updated:
                self._render_chat_history(is_streaming=True)
            self._thinking_card.start_thinking(
                reposition=False,
                model_name=getattr(self, "_translation_model_display", ""),
                status_title=status_title,
                done_status_title=done_status_title,
            )
            self._reposition()
            _ocr_text_panel_class()._ensure_latest_chat_visible_after_layout(self)

            if task_type == "translate":
                self._run_qa_prompt(prompt, "正在翻译...", button_task="translate", result_label="翻译结果")
            else:
                self._run_qa_prompt(prompt, "正在回答...", button_task="qa", result_label="问答结果")
        except Exception as e:
            self._show_panel_status(f"重新生成失败：{e}", tone="error", auto_hide_ms=5200)

    @staticmethod
    def _history_task_type_for_button(button_task: str) -> str:
        return "translate" if str(button_task or "").strip() == "translate" else "qa"

    @staticmethod
    def _message_task_type(message: dict) -> str:
        return "translate" if str(message.get("task_type", "") or "").strip() == "translate" else "qa"

    @staticmethod
    def _regenerate_text_for_user_message(message: dict, task_type: str) -> str:
        if task_type == "translate":
            for key in ("regenerate_text", "display_content", "content"):
                value = message.get(key, "")
                if isinstance(value, str) and value.strip():
                    return value
        value = message.get("regenerate_text", None)
        if isinstance(value, str) and value.strip():
            return value
        return message.get("content", "")

    def _run_qa_prompt(self, text: str, loading_text: str, *, button_task: str = "qa", result_label: str = "问答结果") -> None:
        _trace_ai_panel(
            self,
            "run_qa_prompt.begin",
            text_len=len(str(text or "")),
            loading_text=loading_text,
            button_task=button_task,
            result_label=result_label,
        )
        button_task_name = str(button_task or "qa")
        runtime_purpose = "translate" if button_task_name == "translate" else "qa"
        if self._external_reuse_action_blocked():
            _trace_ai_panel(self, "run_qa_prompt.external_reuse_blocked")
            _ocr_text_panel_class()._finish_preflight_prompt_failure(self, runtime_purpose)
            return

        if self._translation_worker is not None and self._translation_worker.isRunning():
            self._abort_worker()
            _trace_ai_panel(self, "run_qa_prompt.abort_running_worker")
            return
        try:
            cfg, use_proxy, proxy_url = self._translator_runtime_config(runtime_purpose)
            missing = []
            required_fields = self._translator_required_fields(cfg) if runtime_purpose == "translate" else self._qa_required_fields(cfg)
            for key in required_fields:
                if key == "__qa_model__":
                    missing.append("问答模型")
                elif not str(cfg.get(key, "") or "").strip():
                    missing.append(key)
            if missing:
                _trace_ai_panel(
                    self,
                    "run_qa_prompt.missing_config",
                    runtime_purpose=runtime_purpose,
                    missing=missing,
                )
                _ocr_text_panel_class()._finish_preflight_prompt_failure(self, runtime_purpose)
                if runtime_purpose == "translate":
                    self._show_translation_error("翻译模型配置错误，请检查模型管理中的翻译模型是否已正确选择并配置。")
                else:
                    self._show_qa_error("问答模型配置错误，请检查模型管理中的问答模型是否已正确选择并配置。")
                return
        except Exception as e:
            _trace_ai_panel(
                self,
                "run_qa_prompt.config_error",
                runtime_purpose=runtime_purpose,
                error=str(e),
            )
            _ocr_text_panel_class()._finish_preflight_prompt_failure(self, runtime_purpose)
            if runtime_purpose == "translate":
                self._show_translation_error(f"读取翻译设置失败：{e}")
            else:
                self._show_qa_error(f"读取问答设置失败：{e}")
            return

        self._set_worker_buttons_busy(True, task=button_task_name)
        self._translation_info.hide()
        self._translation_info.clear()
        self._translation_started_ts = float(time.time())
        self._translation_model_display = str(cfg.get("display_name", "") or cfg.get("model_name", "") or "")
        self._translation_task = "translate" if runtime_purpose == "translate" else "qa"
        self._translation_result_label = str(result_label or "问答结果")
        self._stream_markdown_text = ""
        _ocr_text_panel_class()._reset_stream_token_cache(self)
        self._stream_render_signature = None
        active_idx = _ocr_text_panel_class()._active_assistant_index(self)
        if active_idx < 0:
            active_idx = len(self._chat_history) - 1 if getattr(self, "_chat_history", None) else -1
        messages = None if runtime_purpose == "translate" else (list(self._chat_history[:active_idx]) if active_idx >= 0 else None)
        messages = _ocr_text_panel_class()._worker_messages_for_current_context(self, messages)
        has_message_attachments = _ocr_text_panel_class()._messages_have_attachments(messages)
        status_title, done_status_title, placeholder_text = _ocr_text_panel_class()._thinking_status_labels(
            runtime_purpose=runtime_purpose,
            button_task=button_task_name,
            loading_text=str(loading_text or ""),
            cfg=cfg,
            has_attachments=has_message_attachments,
        )
        if getattr(self, "_is_chatting", False) and getattr(self, "_chat_history", None):
            try:
                target_idx = active_idx if active_idx >= 0 else len(self._chat_history) - 1
                target_msg = self._chat_history[target_idx]
                if isinstance(target_msg, dict) and str(target_msg.get("role", "") or "") == "assistant" and not str(target_msg.get("content", "") or "").strip():
                    target_msg["status_text"] = placeholder_text
            except Exception:
                pass

        _trace_ai_panel(
            self,
            "run_qa_prompt.before_start_thinking",
            runtime_purpose=runtime_purpose,
            model_display=self._translation_model_display,
        )
        self._thinking_card.start_thinking(
            model_name=getattr(self, "_translation_model_display", ""),
            status_title=status_title,
            done_status_title=done_status_title,
        )
        _trace_ai_panel(
            self,
            "run_qa_prompt.after_start_thinking",
            runtime_purpose=runtime_purpose,
            model_display=self._translation_model_display,
        )
        try:
            self._start_dots_animation()
        except Exception:
            pass

        worker_task = "translate" if runtime_purpose == "translate" else "qa"

        # 仅 AI 对话窗口中的问答链路启用网页端会话追加；
        # 翻译、模型测试、磁盘清理等其他 ChatGPT Web2API 调用均保持关闭。
        enable_conversation_append = button_task_name == "qa"
        worker = None
        try:
            worker = OcrTranslationWorker(
                text,
                self._source_lang.currentText(),
                self._target_lang.currentText(),
                cfg,
                use_proxy=use_proxy,
                proxy_url=proxy_url,
                task=worker_task,
                prompt="",
                messages=messages,
                enable_conversation_append=enable_conversation_append,
            )
            self._translation_worker = worker
            request_id = int(getattr(self, "_translation_request_id", 0) or 0) + 1
            self._translation_request_id = request_id
            worker_id = id(worker)
            worker.reasoning_delta.connect(
                lambda chunk, rid=request_id, wid=worker_id: self._on_reasoning_delta_for_request(chunk, rid, wid)
            )
            worker.translation_delta.connect(
                lambda chunk, rid=request_id, wid=worker_id: self._on_translation_delta(chunk, rid, wid)
            )
            worker.translation_finished.connect(
                lambda result, success, error, rid=request_id, wid=worker_id: self._on_translation_finished(
                    result, success, error, rid, wid
                )
            )
            worker.finished.connect(worker.deleteLater)
            _trace_ai_panel(
                self,
                "run_qa_prompt.before_worker_start",
                runtime_purpose=runtime_purpose,
                worker_task=worker_task,
                has_messages=messages is not None,
            )
            worker.start()
            _trace_ai_panel(
                self,
                "run_qa_prompt.after_worker_start",
                runtime_purpose=runtime_purpose,
                worker_task=worker_task,
                has_messages=messages is not None,
            )
        except Exception as exc:
            _trace_ai_panel(
                self,
                "run_qa_prompt.worker_start_error",
                runtime_purpose=runtime_purpose,
                worker_task=worker_task,
                error=str(exc),
            )
            _ocr_text_panel_class()._finish_preflight_prompt_failure(self, runtime_purpose)
            try:
                if worker is not None:
                    worker.deleteLater()
            except Exception:
                pass
            if runtime_purpose == "translate":
                self._show_translation_error(f"翻译启动失败：{exc}")
            else:
                self._show_qa_error(f"问答启动失败：{exc}")

    def _stream_flush_interval_ms(self) -> int:
        text = str(getattr(self, "_stream_markdown_text", "") or "")
        if _ocr_text_panel_class()._stream_markdown_has_open_code_fence(text):
            return 130
        n = len(text)
        if n > 20000:
            interval = 340
        elif n > 12000:
            interval = 280
        elif n > 6000:
            interval = 220
        elif n > 2500:
            interval = 180
        else:
            interval = 140
        return int(interval)

    @staticmethod
    def _stream_markdown_has_open_code_fence(text: str) -> bool:
        in_fence = False
        fence_marker = ""
        for match in re.finditer(r"(?m)^\s*(```|~~~)", str(text or "")):
            marker = match.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
        return bool(in_fence)

    def _sync_stream_usage_status(
        self,
        model_name: str,
        elapsed: float,
        reply_tokens: int,
        total_tokens: int,
    ) -> None:
        now = time.monotonic()
        last_update = float(getattr(self, "_stream_usage_status_last_update_at", 0.0) or 0.0)
        min_interval = 0.35
        if last_update > 0 and now - last_update < min_interval:
            self._stream_usage_status_pending = (model_name, elapsed, reply_tokens, total_tokens)
            if not bool(getattr(self, "_stream_usage_status_flush_queued", False)):
                self._stream_usage_status_flush_queued = True
                delay_ms = max(1, int((min_interval - (now - last_update)) * 1000))

                def _flush_pending_usage_status() -> None:
                    self._stream_usage_status_flush_queued = False
                    pending = getattr(self, "_stream_usage_status_pending", None)
                    self._stream_usage_status_pending = None
                    if pending:
                        _ocr_text_panel_class()._sync_stream_usage_status(self, *pending)

                QTimer.singleShot(delay_ms, _flush_pending_usage_status)
            return
        self._stream_usage_status_last_update_at = now
        thinking_card = getattr(self, "_thinking_card", None)
        if thinking_card is None:
            return
        try:
            if not thinking_card.isVisible():
                return
            tokens_str = f" | tokens {int(reply_tokens)}/{int(total_tokens)}"
            thinking_card.set_model_and_elapsed(str(model_name or "Unknown"), max(0.0, float(elapsed)), tokens_str)
            info = getattr(self, "_translation_info", None)
            if info is not None:
                info.hide()
        except RuntimeError:
            return
        except Exception:
            pass

    def _reset_stream_token_cache(self) -> None:
        self._stream_reply_token_text_len = -1
        self._stream_reply_token_at = 0.0
        self._stream_reply_token_value = 0
        self._stream_prompt_token_key = None
        self._stream_prompt_token_value = 0
        self._stream_context_token_key = None
        self._stream_context_token_base = None

    def _request_signal_is_current(self, request_id: int | None, worker_id: int | None) -> bool:
        if worker_id is not None and int(worker_id) in getattr(self, "_aborted_worker_ids", set()):
            return False
        if request_id is None:
            return True
        return int(request_id) == int(getattr(self, "_translation_request_id", 0) or 0)

    def _on_reasoning_delta_for_request(self, chunk: str, request_id: int, worker_id: int) -> None:
        if not _ocr_text_panel_class()._request_signal_is_current(self, request_id, worker_id):
            return
        try:
            self._thinking_card.add_thinking_delta(str(chunk or ""))
        except RuntimeError:
            return

    def _on_translation_delta(
        self,
        chunk: str,
        request_id: int | None = None,
        worker_id: int | None = None,
    ) -> None:
        _trace_ai_panel(
            self,
            "translation_delta.begin",
            chunk_len=len(str(chunk or "")),
            thinking_active=bool(getattr(self, "_thinking_card", None) is not None and self._thinking_card.is_thinking()),
        )
        if not _ocr_text_panel_class()._request_signal_is_current(self, request_id, worker_id):
            _trace_ai_panel(self, "translation_delta.stale_request")
            return
        task = str(getattr(self, "_translation_task", "") or "")
        if task not in {"translate", "qa"}:
            _trace_ai_panel(self, "translation_delta.ignored_task", task=task)
            return
        if self._thinking_card.is_thinking():
            _trace_ai_panel(self, "translation_delta.before_stop_thinking", chunk_len=len(str(chunk or "")))
            self._thinking_card.stop_thinking()
            _trace_ai_panel(self, "translation_delta.after_stop_thinking", chunk_len=len(str(chunk or "")))

        chunk_text = str(chunk or "")
        marker = getattr(OcrTranslationWorker, "STREAM_REPLACE_MARKER", "DEEPCAT_STREAM_REPLACE::")
        if chunk_text.startswith(marker):
            _ocr_text_panel_class()._reset_stream_token_cache(self)
            self._stream_markdown_text = chunk_text[len(marker):]
        else:
            self._stream_markdown_text += chunk_text

        if _ocr_text_panel_class()._stream_markdown_has_open_code_fence(self._stream_markdown_text):
            try:
                if not self._stream_flush_timer.isActive():
                    self._stream_flush_timer.start(_ocr_text_panel_class()._stream_flush_interval_ms(self))
            except Exception:
                pass
            return

        if not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start(_ocr_text_panel_class()._stream_flush_interval_ms(self))

    def _flush_stream_markdown(self) -> None:
        task = str(getattr(self, "_translation_task", "") or "")
        if task not in {"translate", "qa"}:
            return
        text = str(getattr(self, "_stream_markdown_text", "") or "")
        if not text:
            return

        elapsed = time.time() - self._translation_started_ts
        model_name = self._translation_model_display
        reply_tokens = _ocr_text_panel_class()._cached_stream_reply_tokens(self, text)
        start_time_struct = time.localtime(self._translation_started_ts)
        start_time_str = time.strftime("%m/%d %H:%M", start_time_struct)

        if getattr(self, "_is_chatting", False) and hasattr(self, "_chat_history") and self._chat_history:
            target_idx = _ocr_text_panel_class()._active_assistant_index(self)
            if target_idx < 0:
                target_idx = len(self._chat_history) - 1
            if 0 <= target_idx < len(self._chat_history):
                self._chat_history[target_idx]["content"] = text
            total_tokens = _ocr_text_panel_class()._cached_stream_total_tokens(self, target_idx, reply_tokens)
            _ocr_text_panel_class()._update_assistant_bubble_at(
                self,
                target_idx,
                text,
                streaming=True,
                model_name=model_name,
                elapsed=elapsed,
                reply_tokens=reply_tokens,
                total_tokens=total_tokens,
                start_time=start_time_str,
            )
            _ocr_text_panel_class()._sync_stream_usage_status(self, model_name, elapsed, reply_tokens, total_tokens)
        else:
            prompt_text = self._editor.toPlainText().strip()
            total_tokens = _ocr_text_panel_class()._cached_stream_prompt_tokens(self, prompt_text) + reply_tokens
            self._set_translation_message(
                text,
                markdown=(task == "qa"),
                streaming=True,
                model_name=model_name,
                elapsed=elapsed,
                reply_tokens=reply_tokens,
                total_tokens=total_tokens,
                start_time=start_time_str,
            )
            _ocr_text_panel_class()._sync_stream_usage_status(self, model_name, elapsed, reply_tokens, total_tokens)

    def _cached_stream_reply_tokens(self, text: str) -> int:
        value = str(text or "")
        now = time.monotonic()
        last_text_len = int(getattr(self, "_stream_reply_token_text_len", -1) or -1)
        last_at = float(getattr(self, "_stream_reply_token_at", 0.0) or 0.0)
        cached = int(getattr(self, "_stream_reply_token_value", 0) or 0)
        if cached > 0 and abs(len(value) - last_text_len) < 1600 and now - last_at < 0.75:
            return cached
        tokens = self._estimate_tokens(value)
        self._stream_reply_token_text_len = len(value)
        self._stream_reply_token_at = now
        self._stream_reply_token_value = tokens
        return tokens

    def _cached_stream_prompt_tokens(self, prompt_text: str) -> int:
        value = str(prompt_text or "")
        cached_key = getattr(self, "_stream_prompt_token_key", None)
        cached_value = int(getattr(self, "_stream_prompt_token_value", 0) or 0)
        if cached_key == value and cached_value >= 0:
            return cached_value
        tokens = self._estimate_tokens(value)
        self._stream_prompt_token_key = value
        self._stream_prompt_token_value = tokens
        return tokens

    def _cached_stream_total_tokens(self, active_index: int, reply_tokens: int) -> int:
        history = getattr(self, "_chat_history", []) or []
        try:
            idx = int(active_index)
        except Exception:
            idx = -1
        key_parts: list[tuple[int, int]] = []
        for msg_index, msg in enumerate(history):
            if msg_index == idx or not isinstance(msg, dict):
                continue
            content = str(msg.get("content", "") or "")
            key_parts.append((len(content), hash(content)))
        key = (len(history), idx, tuple(key_parts))
        cached_key = getattr(self, "_stream_context_token_key", None)
        cached_base = getattr(self, "_stream_context_token_base", None)
        if cached_key != key or cached_base is None:
            cached_base = sum(
                self._estimate_tokens(str(msg.get("content", "") or ""))
                for msg_index, msg in enumerate(history)
                if msg_index != idx and isinstance(msg, dict)
            )
            self._stream_context_token_key = key
            self._stream_context_token_base = int(cached_base)
        return int(cached_base) + int(reply_tokens or 0)

    def _estimate_tokens(self, text: str) -> int:
        text = str(text or "")
        if not text.strip():
            return 0
        import re
        cnh = len(re.findall(r'[\u4e00-\u9fa5\u3000-\u303f\uff00-\uffef]', text))
        eng_words = len(re.findall(r'[a-zA-Z0-9]+', text))
        others = len(text) - cnh - (eng_words * 3)
        if others < 0:
            others = 0
        tokens = cnh * 1.5 + eng_words * 1.3 + others * 0.3
        return max(1, int(tokens))

    def _on_translation_finished(
        self,
        result: str,
        success: bool,
        error_msg: str,
        request_id: int | None = None,
        worker_id: int | None = None,
    ) -> None:
        _trace_ai_panel(
            self,
            "translation_finished.begin",
            success=bool(success),
            result_len=len(str(result or "")),
            error_len=len(str(error_msg or "")),
        )
        if not _ocr_text_panel_class()._request_signal_is_current(self, request_id, worker_id):
            if worker_id is not None:
                self._aborted_worker_ids.discard(int(worker_id))
                current_worker = getattr(self, "_translation_worker", None)
                if current_worker is not None and id(current_worker) == int(worker_id):
                    self._translation_worker = None
            _trace_ai_panel(self, "translation_finished.stale_request")
            return
        if worker_id is not None:
            current_worker = getattr(self, "_translation_worker", None)
            if current_worker is not None and id(current_worker) == int(worker_id):
                self._translation_worker = None
        try:
            self._stream_flush_timer.stop()
        except Exception:
            pass
        # 流式结束：解除渲染期冻结，并释放冻结期间锁死的高度约束（最小=最大），
        # 后续 stop_thinking / 最终 _reposition 走正常的几何流程，由 _reposition
        # 统一计算并应用目标几何。
        if bool(getattr(self, "_geometry_frozen", False)):
            self._thaw_window_height()
            self._geometry_frozen = False
            _trace_ai_panel(self, "translation_finished.thawed_geometry")
        _trace_ai_panel(self, "translation_finished.before_stop_thinking")
        self._thinking_card.stop_thinking()
        _trace_ai_panel(self, "translation_finished.after_stop_thinking")
        try:
            self._stop_dots_animation()
        except Exception:
            pass
        if not success:
            self._thinking_card.hide()
        task = str(getattr(self, "_translation_task", "translate") or "translate")
        self._set_worker_buttons_busy(False, task=task)
        elapsed = max(0.0, float(time.time()) - float(getattr(self, "_translation_started_ts", 0.0) or time.time()))
        if bool(success) and task == "qa" and not str(result or "").strip():
            success = False
            error_msg = "AI 服务这次没有返回内容，可能是 Gemini 连接中断、请求限流或网络/代理不稳定。请稍后重试。"

        if bool(success) and task == "qa":
            result = self._materialize_generated_images(str(result))

        if bool(success):
            model_name = str(getattr(self, "_translation_model_display", "") or "Unknown")
            reply_tokens = self._estimate_tokens(str(result))
            usage_status_synced = False

            # Start time formatting
            start_time_str = ""
            if hasattr(self, "_translation_started_ts") and self._translation_started_ts:
                start_time_struct = time.localtime(self._translation_started_ts)
                start_time_str = time.strftime("%m/%d %H:%M", start_time_struct)
                created_at_db = time.strftime("%Y-%m-%d %H:%M:%S", start_time_struct)
            else:
                start_time_str = time.strftime("%m/%d %H:%M")
                created_at_db = time.strftime("%Y-%m-%d %H:%M:%S")

            if getattr(self, "_is_chatting", False) and hasattr(self, "_chat_history") and self._chat_history:
                target_idx = _ocr_text_panel_class()._active_assistant_index(self)
                if target_idx < 0:
                    target_idx = len(self._chat_history) - 1
                previous_content = ""
                if 0 <= target_idx < len(self._chat_history) and isinstance(self._chat_history[target_idx], dict):
                    previous_content = str(self._chat_history[target_idx].get("content", "") or "")
                    self._chat_history[target_idx]["content"] = str(result)
                total_tokens = sum(self._estimate_tokens(msg.get("content", "")) for msg in self._chat_history)

                if 0 <= target_idx < len(self._chat_history) and isinstance(self._chat_history[target_idx], dict):
                    self._chat_history[target_idx]["model_name"] = model_name
                    self._chat_history[target_idx]["elapsed"] = elapsed
                    self._chat_history[target_idx]["reply_tokens"] = reply_tokens
                    self._chat_history[target_idx]["total_tokens"] = total_tokens
                    self._chat_history[target_idx]["created_at"] = created_at_db
                    self._chat_history[target_idx]["task_type"] = task

                tokens_str = f" | tokens {reply_tokens}/{total_tokens}"
                if hasattr(self, "_thinking_card") and self._thinking_card.isVisible():
                    self._thinking_card.set_model_and_elapsed(model_name, elapsed, tokens_str)
                    self._translation_info.hide()
                else:
                    self._translation_info.setText(f"{model_name} | {elapsed:.2f}秒{tokens_str}")
                    if not getattr(self, "_history_showing", False):
                        self._translation_info.show()
                        self._update_translation_info_pos()
                usage_status_synced = True

                # 检查是否需要自动追加到笔记末尾
                if getattr(self, "_is_append_action", False) and getattr(self, "_associated_note_tab", None) is not None:
                    self._append_ai_reply_to_note(str(result))

                if getattr(self, "_pending_context_summary_anchor_index", None) is not None:
                    finalize_context_summary = getattr(self, "_finalize_pending_context_summary", None)
                    if callable(finalize_context_summary):
                        finalize_context_summary(str(result))

                try:
                    should_follow_bottom = True
                    if hasattr(self._bubble_view, "is_at_bottom"):
                        try:
                            should_follow_bottom = bool(self._bubble_view.is_at_bottom(80))
                        except Exception:
                            should_follow_bottom = True
                    updated = False
                    if previous_content == str(result):
                        finalize_message = getattr(self._bubble_view, "finalize_message_at", None)
                        if callable(finalize_message):
                            updated = bool(
                                finalize_message(
                                    target_idx,
                                    model_name=model_name,
                                    elapsed=elapsed,
                                    reply_tokens=reply_tokens,
                                    total_tokens=total_tokens,
                                    start_time=start_time_str,
                                )
                            )
                    if not updated:
                        updated = _ocr_text_panel_class()._update_assistant_bubble_at(
                            self,
                            target_idx,
                            str(result),
                            model_name=model_name,
                            elapsed=elapsed,
                            reply_tokens=reply_tokens,
                            total_tokens=total_tokens,
                            start_time=start_time_str,
                        )
                    if not updated:
                        raise RuntimeError("target bubble update failed")
                    self._bubble_view.show()
                    try:
                        self._bubble_view.refresh_layout(keep_bottom=should_follow_bottom)
                    except Exception:
                        pass
                    if previous_content != str(result):
                        self._reposition()
                    if should_follow_bottom:
                        if hasattr(self._bubble_view, "scroll_to_bottom_now"):
                            self._bubble_view.scroll_to_bottom_now()
                        else:
                            self._bubble_view.scroll_to_bottom()
                    self._schedule_question_editor_ime_focus_reset()
                except Exception:
                    self._render_chat_history()
            else:
                prompt_text = self._editor.toPlainText().strip()
                total_tokens = self._estimate_tokens(prompt_text) + reply_tokens
                if task == "qa":
                    self._stream_markdown_text = str(result)
                    self._set_translation_message(
                        str(result),
                        markdown=True,
                        model_name=model_name,
                        elapsed=elapsed,
                        reply_tokens=reply_tokens,
                        total_tokens=total_tokens,
                        start_time=start_time_str,
                    )
                else:
                    self._set_translation_message(
                        str(result),
                        markdown=False,
                        model_name=model_name,
                        elapsed=elapsed,
                        reply_tokens=reply_tokens,
                        total_tokens=total_tokens,
                        start_time=start_time_str,
                    )

            if not usage_status_synced:
                tokens_str = f" | tokens {reply_tokens}/{total_tokens}"
                if hasattr(self, "_thinking_card") and self._thinking_card.isVisible():
                    self._thinking_card.set_model_and_elapsed(model_name, elapsed, tokens_str)
                    self._translation_info.hide()
                else:
                    self._translation_info.setText(f"{model_name} | {elapsed:.2f}秒{tokens_str}")
                    if not getattr(self, "_history_showing", False):
                        self._translation_info.show()
                        self._update_translation_info_pos()
            self._copy_finished_result(str(result), str(getattr(self, "_translation_result_label", "") or "结果"))
            self._save_to_history(task, str(result), model_name, elapsed, success=True)
        else:
            self._pending_context_summary_anchor_index = None
            self._translation_info.hide()
            if getattr(self, "_is_chatting", False) and hasattr(self, "_chat_history") and self._chat_history:
                target_idx = _ocr_text_panel_class()._active_assistant_index(self)
                if target_idx < 0:
                    target_idx = len(self._chat_history) - 1
                partial_text = ""
                if 0 <= target_idx < len(self._chat_history) and isinstance(self._chat_history[target_idx], dict):
                    partial_text = str(self._chat_history[target_idx].get("content", "") or "")
                if not partial_text.strip():
                    partial_text = str(getattr(self, "_stream_markdown_text", "") or "")
                if partial_text.strip():
                    failed_text = f"回答失败：{error_msg}\n\n已接收部分回答，可保留已生成内容或重试。"
                else:
                    failed_text = f"回答失败：{error_msg}"
                if 0 <= target_idx < len(self._chat_history) and isinstance(self._chat_history[target_idx], dict):
                    self._chat_history[target_idx]["content"] = failed_text
                    self._chat_history[target_idx]["task_type"] = task
                    self._chat_history[target_idx]["failed"] = True
                    self._chat_history[target_idx]["error_message"] = str(error_msg or "")
                    if partial_text.strip():
                        self._chat_history[target_idx]["can_continue"] = True
                        self._chat_history[target_idx]["partial_content"] = partial_text
                try:
                    should_follow_bottom = True
                    if hasattr(self._bubble_view, "is_at_bottom"):
                        try:
                            should_follow_bottom = bool(self._bubble_view.is_at_bottom(80))
                        except Exception:
                            should_follow_bottom = True
                    updated = _ocr_text_panel_class()._update_assistant_bubble_at(self, target_idx, failed_text)
                    if not updated:
                        raise RuntimeError("target bubble update failed")
                    self._bubble_view.show()
                    self._reposition()
                    if should_follow_bottom:
                        if hasattr(self._bubble_view, "scroll_to_bottom_now"):
                            self._bubble_view.scroll_to_bottom_now()
                        else:
                            self._bubble_view.scroll_to_bottom()
                    self._schedule_question_editor_ime_focus_reset()
                except Exception:
                    self._render_chat_history()
            else:
                if task == "qa":
                    self._show_qa_error(f"问答失败：{error_msg}")
                else:
                    self._show_translation_error(f"翻译失败：{error_msg}")
            model_name = str(getattr(self, "_translation_model_display", "") or "Unknown")
            self._save_to_history(task, str(error_msg), model_name, elapsed, success=False)
        self._active_assistant_msg_index = None
        self._translation_worker = None
