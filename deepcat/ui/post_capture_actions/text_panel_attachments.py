from __future__ import annotations

import copy
import time
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from PyQt6.QtCore import QThread
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


def _ocr_text_panel_class():
    from deepcat.ui.post_capture_actions.text_panel import OcrTextPanel

    return OcrTextPanel


class TextPanelAttachmentsMixin:
    def _create_attachment_preview_bar(self) -> None:
        self._attachment_preview_bar = QFrame(self._editor_container)
        self._attachment_preview_bar.setObjectName("AttachmentPreviewBar")
        self._attachment_preview_bar.setAutoFillBackground(False)
        self._attachment_preview_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._attachment_preview_bar.setFixedHeight(52)
        self._attachment_preview_bar.setStyleSheet(
            "QFrame#AttachmentPreviewBar {"
            " background:transparent;"
            " border:none;"
            "}"
        )

        bar_layout = QHBoxLayout(self._attachment_preview_bar)
        bar_layout.setContentsMargins(8, 0, 8, 2)
        bar_layout.setSpacing(6)

        self._attachment_preview_scroll = QScrollArea(self._attachment_preview_bar)
        self._attachment_preview_scroll.setObjectName("AttachmentPreviewScroll")
        self._attachment_preview_scroll.setAutoFillBackground(False)
        self._attachment_preview_scroll.setWidgetResizable(True)
        self._attachment_preview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._attachment_preview_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._attachment_preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._attachment_preview_scroll.setFixedHeight(50)
        self._attachment_preview_scroll.setStyleSheet(
            "QScrollArea#AttachmentPreviewScroll { background:transparent; border:none; }"
            "QScrollArea#AttachmentPreviewScroll > QWidget { background:transparent; }"
            "QScrollArea#AttachmentPreviewScroll viewport { background:transparent; }"
            "QWidget#AttachmentPreviewViewport { background:transparent; border:none; }"
            "QWidget#AttachmentPreviewItems { background:transparent; border:none; }"
            "QScrollBar:horizontal { background:transparent; height:4px; margin:0px; }"
            "QScrollBar::handle:horizontal { background:#cbd5e1; border-radius:2px; min-width:24px; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0px; height:0px; }"
            "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background:transparent; }"
        )
        viewport = self._attachment_preview_scroll.viewport()
        if viewport is not None:
            viewport.setObjectName("AttachmentPreviewViewport")
            viewport.setAutoFillBackground(False)
            viewport.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            viewport.setStyleSheet("QWidget#AttachmentPreviewViewport { background:transparent; border:none; }")

        self._attachment_preview_items = QWidget(self._attachment_preview_scroll)
        self._attachment_preview_items.setObjectName("AttachmentPreviewItems")
        self._attachment_preview_items.setAutoFillBackground(False)
        self._attachment_preview_items.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._attachment_preview_items.setStyleSheet("QWidget#AttachmentPreviewItems { background:transparent; border:none; }")
        self._attachment_preview_items_layout = QHBoxLayout(self._attachment_preview_items)
        self._attachment_preview_items_layout.setContentsMargins(0, 0, 0, 0)
        self._attachment_preview_items_layout.setSpacing(6)
        self._attachment_preview_scroll.setWidget(self._attachment_preview_items)
        bar_layout.addWidget(self._attachment_preview_scroll, 1)

        container_layout = self._editor_container.layout()
        if container_layout is not None:
            container_layout.insertWidget(0, self._attachment_preview_bar)
        self._attachment_preview_bar.hide()

    def _attachment_preview_path(self, preview: Optional[dict]) -> str:
        if not isinstance(preview, dict):
            return ""
        preview_id = str(preview.get("id", "") or "").strip()
        if not preview_id:
            return ""
        return str(getattr(self, "_attachment_preview_paths", {}).get(preview_id, "") or "")

    def _refresh_attachment_preview_bar(self) -> None:
        bar = getattr(self, "_attachment_preview_bar", None)
        layout = getattr(self, "_attachment_preview_items_layout", None)
        if bar is None or layout is None:
            return

        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        attachments = list(getattr(self, "_pending_attachments", []) or [])
        previews = list(getattr(self, "_pending_attachment_previews", []) or [])
        labels = list(getattr(self, "_pending_attachment_labels", []) or [])
        while len(previews) < len(attachments):
            previews.append(None)
        while len(labels) < len(attachments):
            labels.append("附件")

        for idx, _attachment in enumerate(attachments):
            label = str(labels[idx] or "附件")
            chip = _AttachmentPreviewChip(
                idx,
                label,
                self._attachment_preview_path(previews[idx]),
                _attachment,
                parent=self._attachment_preview_items,
            )
            chip.delete_requested.connect(self._remove_pending_attachment)
            layout.addWidget(chip)
        layout.addStretch(1)

        has_attachments = bool(attachments)
        bar.setVisible(has_attachments)
        self._sync_input_watermark_visibility()
        if self.isVisible() and not bool(getattr(self, "_geometry_frozen", False)):
            try:
                self._reposition()
            except Exception:
                pass

    def _remove_pending_attachment(self, index: int) -> None:
        attachments = list(getattr(self, "_pending_attachments", []) or [])
        if not (0 <= int(index) < len(attachments)):
            return
        previews = list(getattr(self, "_pending_attachment_previews", []) or [])
        labels = list(getattr(self, "_pending_attachment_labels", []) or [])
        while len(previews) < len(attachments):
            previews.append(None)
        while len(labels) < len(attachments):
            labels.append("附件")

        del attachments[int(index)]
        del previews[int(index)]
        del labels[int(index)]
        self._pending_attachments = attachments
        self._pending_attachment_previews = previews
        self._pending_attachment_labels = labels
        self._refresh_attachment_preview_bar()

    def _clear_pending_attachments(self) -> None:
        self._pending_attachments = []
        self._pending_attachment_previews = []
        self._pending_attachment_labels = []
        _refresh_attachment_preview_bar_if_available(self)

    def _clear_editor_silently(self) -> None:
        old_loading = bool(getattr(self, "_loading_text", False))
        self._loading_text = True
        try:
            self._editor.clear()
            _ocr_text_panel_class()._clear_pending_placeholder_card(self)
        finally:
            self._loading_text = old_loading

    def _clear_pending_placeholder_card(self) -> None:
        self._pending_placeholder_card_title = ""
        self._pending_placeholder_card_prompt = ""

    def _set_placeholder_card_editor(self, title: str, prompt_text: str) -> None:
        from PyQt6.QtGui import QColor, QFont, QTextCharFormat

        display_title = str(title or "").strip()
        prompt = str(prompt_text or "").strip()
        if not display_title or not prompt:
            return
        self._pending_placeholder_card_title = display_title
        self._pending_placeholder_card_prompt = prompt

        old_loading = bool(getattr(self, "_loading_text", False))
        self._loading_text = True
        try:
            self._editor.clear()
            cursor = self._editor.textCursor()

            title_format = QTextCharFormat()
            title_format.setForeground(QColor("#1d4ed8"))
            title_format.setBackground(QColor("#dbeafe"))
            title_format.setFontWeight(QFont.Weight.DemiBold)
            cursor.insertText(display_title, title_format)

            normal_format = QTextCharFormat()
            normal_format.setForeground(QColor("#1f2937"))
            normal_format.setBackground(QColor("transparent"))
            normal_format.setFontWeight(QFont.Weight.Normal)
            cursor.insertText(" ", normal_format)
            cursor.movePosition(cursor.MoveOperation.End)
            self._editor.setTextCursor(cursor)
            self._editor.setCurrentCharFormat(normal_format)
        finally:
            self._loading_text = old_loading

    def _resolve_placeholder_card_prompt(self, visible_text: str) -> tuple[str, str]:
        display_text = str(visible_text or "").strip()
        title = str(getattr(self, "_pending_placeholder_card_title", "") or "").strip()
        prompt = str(getattr(self, "_pending_placeholder_card_prompt", "") or "").strip()
        if not title or not prompt or not display_text.startswith(title):
            _ocr_text_panel_class()._clear_pending_placeholder_card(self)
            return display_text, display_text

        suffix = display_text[len(title):].lstrip(" \t\r\n:：")
        if suffix:
            return f"{prompt.rstrip()}\n{suffix}", display_text
        return prompt, title

    def _message_action_worker_running(self) -> bool:
        worker = getattr(self, "_translation_worker", None)
        if worker is not None and getattr(worker, "isRunning", lambda: False)():
            return True
        note_worker = getattr(self, "_ima_note_search_worker", None)
        return bool(note_worker is not None and getattr(note_worker, "isRunning", lambda: False)())

    @staticmethod
    def _strip_display_attachment_lines(text: str) -> str:
        lines = []
        for line in str(text or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("📎 [图片:") or stripped.startswith("📎 [附件:"):
                continue
            if stripped.startswith("📎 [已添加附件:"):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _editable_text_for_user_message(message: dict) -> str:
        display_text = _ocr_text_panel_class()._strip_display_attachment_lines(str(message.get("display_content", "") or ""))
        if display_text:
            return display_text
        task_type = _ocr_text_panel_class()._message_task_type(message)
        regenerate_text = _ocr_text_panel_class()._regenerate_text_for_user_message(message, task_type)
        if isinstance(regenerate_text, str) and regenerate_text.strip():
            return regenerate_text.strip()
        content = message.get("content", "")
        if isinstance(content, list):
            text_parts = [
                str(item.get("text", "") or "")
                for item in content
                if isinstance(item, dict) and str(item.get("type", "") or "") == "text"
            ]
            text = "\n".join(part for part in text_parts if part.strip()).strip()
            if text:
                return text
        return str(content or "").strip()

    @staticmethod
    def _message_attachments_for_resend(message: dict) -> list[dict]:
        content = message.get("content", "")
        if not isinstance(content, list):
            return []
        attachments: list[dict] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "") or "") in {"image_url", "file_url"} or (str(item.get("type", "") or "") == "text" and item.get("is_notebook")):
                attachments.append(copy.deepcopy(item))
        return attachments

    @staticmethod
    def _attachment_label_from_payload(attachment: dict) -> str:
        if not isinstance(attachment, dict):
            return "附件"
        if str(attachment.get("type", "") or "") == "file_url":
            file_url = attachment.get("file_url", {})
            if isinstance(file_url, dict):
                return str(file_url.get("name", "") or "附件")
        elif str(attachment.get("type", "") or "") == "text" and attachment.get("is_notebook"):
            return str(attachment.get("name", "") or "附件")
        return "图片"

    def _load_user_message_into_editor_for_resend(self, message: dict) -> int:
        edit_text = _ocr_text_panel_class()._editable_text_for_user_message(message)
        attachments = _ocr_text_panel_class()._message_attachments_for_resend(message)
        old_loading = bool(getattr(self, "_loading_text", False))
        self._loading_text = True
        try:
            self._editor.setPlainText(edit_text)
        finally:
            self._loading_text = old_loading
        self._pending_attachments = attachments
        self._pending_attachment_previews = [None for _ in attachments]
        self._pending_attachment_labels = [
            _ocr_text_panel_class()._attachment_label_from_payload(item)
            for item in attachments
        ]
        _refresh_attachment_preview_bar_if_available(self)
        self._sync_input_watermark_visibility()
        try:
            self._editor.setFocus()
        except Exception:
            pass
        return len(attachments)

    def _register_attachment_preview(self, image_path: str, filename: str) -> dict[str, str]:
        preview_id = f"img_{int(time.time() * 1000)}_{len(getattr(self, '_attachment_preview_paths', {}))}"
        if not hasattr(self, "_attachment_preview_paths"):
            self._attachment_preview_paths = {}
        self._attachment_preview_paths[preview_id] = str(image_path)
        return {"id": preview_id, "name": str(filename or "图片")}

    def _materialize_generated_images(self, text: str) -> str:
        """把 worker 带回的生图标记行转换为可点击预览的图片链接。"""
        marker = OcrTranslationWorker.GENERATED_IMAGE_MARKER_PREFIX
        if marker not in str(text or ""):
            return str(text or "")
        from pathlib import Path as _Path

        lines: list[str] = []
        for line in str(text).splitlines():
            stripped = line.strip()
            if not stripped.startswith(marker):
                lines.append(line)
                continue
            image_path = stripped[len(marker):].strip()
            if not image_path:
                continue
            name = _Path(image_path).name
            preview = self._register_attachment_preview(image_path, name)
            lines.append(f"🖼️ [图片: {name}](deepcat-image-preview:{preview['id']})（点击查看）")
            lines.append("")
            lines.append(f"已保存到：`{image_path}`")
        return "\n".join(lines)

    def _format_display_attachment(self, attachment: dict, preview: Optional[dict]) -> str:
        if preview and str(preview.get("id", "")).strip():
            label = str(preview.get("name", "") or "图片").strip()
            return f"📎 [图片: {label}](deepcat-image-preview:{preview['id']})"
        if attachment.get("type") == "file_url":
            name = attachment.get("file_url", {}).get("name", "未命名")
        elif attachment.get("type") == "text" and attachment.get("is_notebook"):
            name = attachment.get("name", "未命名")
        else:
            name = "图片"
        return f"📎 [附件: {name}]"

    def _append_pending_attachment(self, attachment: dict, label: str, preview: Optional[dict] = None) -> None:
        if not hasattr(self, "_pending_attachments"):
            self._pending_attachments = []
        if not hasattr(self, "_pending_attachment_previews"):
            self._pending_attachment_previews = []
        if not hasattr(self, "_pending_attachment_labels"):
            self._pending_attachment_labels = []
        self._pending_attachments.append(attachment)
        self._pending_attachment_previews.append(preview)
        self._pending_attachment_labels.append(str(label or "附件"))
        _refresh_attachment_preview_bar_if_available(self)

    def _sync_pending_attachments_from_editor_text(self) -> None:
        attachments = list(getattr(self, "_pending_attachments", []) or [])
        if not attachments:
            bar = getattr(self, "_attachment_preview_bar", None)
            if bar is not None and bar.isVisible():
                _refresh_attachment_preview_bar_if_available(self)
            return

        try:
            import re
            text = str(self._editor.toPlainText() or "")
            visible_labels = [m.strip() for m in re.findall(r"📎\s*\[已添加附件:\s*([^\]]+)\]", text)]
        except Exception:
            return

        if len(visible_labels) >= len(attachments):
            return

        previews = list(getattr(self, "_pending_attachment_previews", []) or [])
        labels = list(getattr(self, "_pending_attachment_labels", []) or [])
        while len(previews) < len(attachments):
            previews.append(None)
        while len(labels) < len(attachments):
            labels.append("")

        if not visible_labels:
            if not hasattr(self, "_attachment_preview_bar"):
                self._pending_attachments = []
                self._pending_attachment_previews = []
                self._pending_attachment_labels = []
            return

        used_indexes: set[int] = set()
        kept_attachments: list[dict] = []
        kept_previews: list[Optional[dict]] = []
        kept_labels: list[str] = []
        for visible_label in visible_labels:
            match_index = -1
            for idx, pending_label in enumerate(labels):
                if idx in used_indexes:
                    continue
                if pending_label == visible_label:
                    match_index = idx
                    break
            if match_index < 0:
                for idx in range(len(attachments)):
                    if idx not in used_indexes:
                        match_index = idx
                        break
            if match_index < 0:
                continue
            used_indexes.add(match_index)
            kept_attachments.append(attachments[match_index])
            kept_previews.append(previews[match_index])
            kept_labels.append(labels[match_index] or visible_label)

        self._pending_attachments = kept_attachments
        self._pending_attachment_previews = kept_previews
        self._pending_attachment_labels = kept_labels
        _refresh_attachment_preview_bar_if_available(self)

    def _show_attachment_preview(self, preview_id: str) -> None:
        preview_id = str(preview_id or "").strip()
        image_path = str(getattr(self, "_attachment_preview_paths", {}).get(preview_id, "") or "")
        if not image_path:
            return
        try:
            from pathlib import Path
            if not Path(image_path).exists():
                return
            from deepcat.ui.main_window import ImagePreviewDialog
            dialog = ImagePreviewDialog(image_path, parent=self)
            dialog.exec()
        except Exception:
            pass

    def _add_single_file_attachment(self, file_path: str) -> None:
        import os

        if not file_path or not os.path.exists(file_path):
            self._show_panel_status("附件上传失败：文件不存在。", tone="error", auto_hide_ms=5200)
            return
        worker = FileAttachmentPrepareWorker(file_path)
        workers = getattr(self, "_attachment_prepare_workers", None)
        if not isinstance(workers, set):
            workers = set()
            self._attachment_prepare_workers = workers
        workers.add(worker)
        worker.prepared.connect(lambda payload, worker_ref=worker: self._on_file_attachment_prepared(worker_ref, payload))
        worker.failed.connect(lambda message, worker_ref=worker: self._on_file_attachment_failed(worker_ref, message))
        worker.finished.connect(lambda worker_ref=worker: self._forget_attachment_prepare_worker(worker_ref))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _forget_attachment_prepare_worker(self, worker: QThread) -> None:
        workers = getattr(self, "_attachment_prepare_workers", None)
        if isinstance(workers, set):
            workers.discard(worker)

    def _on_file_attachment_prepared(self, worker: QThread, payload: object) -> None:
        self._forget_attachment_prepare_worker(worker)
        if not isinstance(payload, dict):
            self._show_panel_status("附件上传失败：后台返回的数据无效。", tone="error", auto_hide_ms=5200)
            return
        filename = str(payload.get("filename", "附件") or "附件")
        file_path = str(payload.get("file_path", "") or "")
        attachment = payload.get("attachment")
        if not isinstance(attachment, dict):
            self._show_panel_status("附件上传失败：后台未生成附件内容。", tone="error", auto_hide_ms=5200)
            return
        preview = self._register_attachment_preview(file_path, filename) if bool(payload.get("is_image")) else None
        self._append_pending_attachment(attachment, filename, preview)
        self._editor.setFocus()
        self._show_panel_status(f"已添加附件：{filename}", tone="success", auto_hide_ms=1800)

    def _on_file_attachment_failed(self, worker: QThread, message: str) -> None:
        self._forget_attachment_prepare_worker(worker)
        self._show_panel_status(f"附件上传失败：{message}", tone="error", auto_hide_ms=5200)

    def _upload_attachment(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择附件",
            "",
            "所有文件 (*);;图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;文档 (*.pdf *.txt *.doc *.docx *.xls *.xlsx *.ppt *.pptx);;代码与网页 (*.html *.htm *.md *.markdown *.py *.js *.ts *.css *.json *.xml *.yaml *.yml *.sh *.bat *.rs *.go *.c *.cpp *.h *.java *.kt)"
        )
        for file_path in file_paths or []:
            self._add_single_file_attachment(file_path)

    def _handle_pasted_files(self, paths: list[str]) -> None:
        if not paths:
            self._show_panel_status("附件上传失败：未读取到文件路径。", tone="warning", auto_hide_ms=3200)
            return
        for p in paths:
            self._add_single_file_attachment(p)

    def _handle_pasted_image(self, b64_data: str, *, show_toast: bool = True) -> None:
        import time
        import base64
        import tempfile
        if not str(b64_data or "").strip():
            self._show_panel_status("图片粘贴失败：未读取到图片数据。", tone="error", auto_hide_ms=4200)
            return
        filename = f"pasted_image_{int(time.time())}.png"
        attachment_obj = {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{b64_data}"
            }
        }
        preview = None
        try:
            preview_path = Path(tempfile.gettempdir()) / f"deepcat_{filename}"
            preview_path.write_bytes(base64.b64decode(str(b64_data or "")))
            preview = self._register_attachment_preview(str(preview_path), filename)
        except Exception as exc:
            if bool(show_toast):
                self._show_panel_status(f"图片预览生成失败，附件仍已添加：{exc}", tone="warning", auto_hide_ms=4200)
            preview = None
        self._append_pending_attachment(attachment_obj, filename, preview)

        self._editor.setFocus()
        if bool(show_toast):
            self._show_panel_status("已粘贴截图图片附件。", tone="success", auto_hide_ms=1800)

    def submit_image_attachment_for_qa(self, image_bgr, prompt: str = "请识别并分析这张图片。") -> bool:
        if image_bgr is None:
            self._show_qa_error("截图发送失败：没有可用图片。")
            return False
        if self._translation_worker is not None and self._translation_worker.isRunning():
            self._show_qa_error("AI 正在回答中，请稍后再试。")
            return False
        current_worker = getattr(self, "_image_attachment_prepare_worker", None)
        if current_worker is not None and current_worker.isRunning():
            self._show_qa_error("正在准备上一张截图，请稍后再试。")
            return False
        worker = ImageAttachmentPrepareWorker(image_bgr, prompt)
        self._image_attachment_prepare_worker = worker
        worker.prepared.connect(self._on_image_attachment_prepared)
        worker.failed.connect(self._on_image_attachment_prepare_failed)
        worker.finished.connect(lambda worker_ref=worker: self._on_image_attachment_prepare_finished(worker_ref))
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self._show_panel_status("正在后台准备截图附件...", tone="info", auto_hide_ms=1800)
        return True

    def _on_image_attachment_prepared(self, b64_data: str, prompt: str) -> None:
        if self._translation_worker is not None and self._translation_worker.isRunning():
            self._show_qa_error("截图已准备完成，但 AI 正在回答中，请稍后重新发送。")
            return
        old_loading = bool(getattr(self, "_loading_text", False))
        self._loading_text = True
        try:
            self._editor.clear()
            if str(prompt or "").strip():
                self._editor.setPlainText(f"{str(prompt).strip()} ")
        finally:
            self._loading_text = old_loading
        self._handle_pasted_image(str(b64_data or ""), show_toast=False)
        self._answer_question()

    def _on_image_attachment_prepare_failed(self, message: str) -> None:
        self._show_qa_error(f"截图发送失败：{message}")

    def _on_image_attachment_prepare_finished(self, worker: QThread) -> None:
        if getattr(self, "_image_attachment_prepare_worker", None) is worker:
            self._image_attachment_prepare_worker = None

    def _handle_at_notes(self) -> None:
        try:
            from deepcat.table_notes_store import TableNotesStore
            store = TableNotesStore()
            tabs, _ = store.load_note_tabs()
        except Exception:
            tabs = []

        items = []
        for i, tab in enumerate(tabs):
            name = str(tab.get("name", "未命名") or "未命名")
            items.append({
                "text": name,
                "index": i,
                "name": name,
                "html": tab.get("html", ""),
                "password": tab.get("password", ""),
                "group_name": tab.get("group_name", ""),
            })

        old_popup = getattr(self, "_note_attach_popup", None)
        if old_popup is not None:
            try:
                old_popup.close()
            except RuntimeError:
                pass

        def select_note(data: dict[str, Any]) -> None:
            hashed_pwd = data.get("password", "")
            if hashed_pwd:
                try:
                    from deepcat.ui.main_window import _TabPasswordVerifyDialog
                    dialog = _TabPasswordVerifyDialog(str(hashed_pwd), self)
                    if dialog.exec() != _TabPasswordVerifyDialog.DialogCode.Accepted:
                        return
                except Exception:
                    pass

            note_index = data.get("index")
            note_name = str(data.get("name", "笔记") or "笔记")
            html_content = str(data.get("html", "") or "")
            self._associated_note_tab = {"index": note_index, "name": note_name}

            from PyQt6.QtGui import QTextDocument
            doc = QTextDocument()
            doc.setHtml(html_content)
            plain_text = doc.toPlainText()

            attachment_obj = {
                "type": "text",
                "text": f"\n\n[关联笔记本附件: {note_name}]\n--- {note_name} 内容开始 ---\n{plain_text}\n--- {note_name} 内容结束 ---\n",
                "is_notebook": True,
                "name": f"{note_name}.txt",
            }

            self._append_pending_attachment(attachment_obj, note_name, None)

            self._editor.setFocus()
            self._show_panel_status(f"已导入笔记为附件：{note_name}", tone="success", auto_hide_ms=1800)

        from deepcat.ui.tab_list_popup import GroupedNoteListPopup
        popup = GroupedNoteListPopup(
            items,
            select_note,
            empty_text="暂无笔记本",
            max_height=450,
            parent=self,
        )
        self._note_attach_popup = popup
        popup.destroyed.connect(
            lambda *_: setattr(self, "_note_attach_popup", None)
            if getattr(self, "_note_attach_popup", None) is popup else None
        )
        anchor_widget = getattr(self, "_bottom_row_widget", self._editor)
        popup.show_for_anchor(
            anchor_widget,
            align_right=False,
            prefer_above=True,
            prefer_above_from_bottom=True,
            offset=QPoint(10, 0),
            bounds_widget=self,
        )
        return

    def _append_ai_reply_to_note(self, reply_text: str) -> None:
        associated = getattr(self, "_associated_note_tab", None)
        if associated is None:
            return

        try:
            tab_index = associated.get("index")
            note_name = associated.get("name")

            from deepcat.table_notes_store import TableNotesStore
            store = TableNotesStore()
            tabs, _ = store.load_note_tabs()

            if tab_index < 0 or tab_index >= len(tabs):
                return

            old_html = tabs[tab_index].get("html", "")

            from PyQt6.QtGui import QTextDocument, QTextCursor
            doc = QTextDocument()
            doc.setHtml(old_html)

            # 使用 QTextCursor 移动至末尾插入
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.MoveOperation.End)

            import datetime
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 将模型的 markdown 转换为 html
            from deepcat.ui.markdown_renderer import MarkdownRenderer
            reply_html = MarkdownRenderer.to_html(reply_text)

            # 插入分割线、修改时间和模型回答
            divider_html = f"<hr/><p style='color: #64748b; font-size: 11px; margin-top: 8px; margin-bottom: 8px;'>追加回答时间: {current_time}</p>"
            cursor.insertHtml(divider_html + reply_html)

            new_html = doc.toHtml()
            store.save_note_tab(tab_index, note_name, new_html)

            self._notify_note_saved(tab_index)

            self._show_panel_status(f"已自动将回答追加至笔记：{note_name}", tone="success")
        except Exception as e:
            self._show_panel_status(f"同步笔记失败：自动追加到笔记本失败：{e}", tone="error", auto_hide_ms=5200)
        finally:
            self._is_append_action = False
            self._associated_note_tab = None

    def _broadcast_notes_refresh(self) -> None:
        self._notify_note_saved(-1)

    def _notify_note_saved(self, tab_index: int, *, inherit_ima_config: bool = False) -> None:
        try:
            from PyQt6.QtWidgets import QApplication
            for widget in QApplication.topLevelWidgets():
                if widget.__class__.__name__ == "MainWindow":
                    if hasattr(widget, "_load_table_notes_settings"):
                        widget._load_table_notes_settings()
                    if tab_index >= 0 and hasattr(widget, "_sync_external_note_to_ima"):
                        widget._sync_external_note_to_ima(tab_index, inherit_config=inherit_ima_config)
        except Exception:
            pass

    def _on_bubble_add_to_note(self, idx: int) -> None:
        import datetime

        if not hasattr(self, "_chat_history") or idx < 0 or idx >= len(self._chat_history):
            return

        ai_reply = str(self._chat_history[idx].get("content", "")).strip()
        if not ai_reply:
            return

        from deepcat.table_notes_store import TableNotesStore
        from deepcat.ui.markdown_renderer import MarkdownRenderer

        store = TableNotesStore()
        tabs, _ = store.load_note_tabs()

        default_name = f"AI问答_{datetime.datetime.now().strftime('%m%d_%H%M')}"

        from deepcat.ui.post_capture_actions import GroupedSmoothNoteIntegrationDialog
        action, title, group_name, append_index, ok = GroupedSmoothNoteIntegrationDialog.get_integration_result(
            "添加到笔记本",
            default_name,
            tabs,
            parent=self
        )
        if not ok or not action or not title.strip():
            return

        title = title.strip()

        if action == "new":
            try:
                next_index = len(tabs)
                html_content = MarkdownRenderer.to_html(ai_reply)
                store.save_note_tab(next_index, title, html_content, group_name)

                # 跨窗体瞬时广播刷新
                self._notify_note_saved(next_index, inherit_ima_config=True)

                self._show_panel_status(f"已新建并保存笔记：{title}", tone="success")
            except Exception as e:
                self._show_panel_status(f"同步笔记失败：无法新建笔记本：{e}", tone="error", auto_hide_ms=5200)

        elif action == "append":
            try:
                tab_index = append_index if 0 <= append_index < len(tabs) else -1
                if tab_index == -1:
                    for i, t in enumerate(tabs):
                        if t["name"] == title and str(t.get("group_name", "") or "") == group_name:
                            tab_index = i
                            break

                if tab_index == -1:
                    return

                target_tab = tabs[tab_index]
                title = str(target_tab.get("name", title) or title)
                old_html = str(target_tab.get("html", "") or "")
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                reply_html = MarkdownRenderer.to_html(ai_reply)

                from PyQt6.QtGui import QTextDocument, QTextCursor
                doc = QTextDocument()
                doc.setHtml(old_html)
                cursor = QTextCursor(doc)
                cursor.movePosition(QTextCursor.MoveOperation.End)

                divider_html = f"<hr/><p style='color: #64748b; font-size: 11px; margin-top: 8px; margin-bottom: 8px;'>追加回答时间: {current_time}</p>"
                cursor.insertHtml(divider_html + reply_html)

                new_html = doc.toHtml()
                store.save_note_tab(tab_index, title, new_html)

                # 跨窗体瞬时广播刷新
                self._notify_note_saved(tab_index)

                self._show_panel_status(f"已追加回答至笔记本：{title}", tone="success")
            except Exception as e:
                self._show_panel_status(f"同步笔记失败：追加到笔记本失败：{e}", tone="error", auto_hide_ms=5200)
