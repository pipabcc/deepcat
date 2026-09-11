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
from deepcat.ui.post_capture_actions.text_panel_attachments import TextPanelAttachmentsMixin
from deepcat.ui.post_capture_actions.text_panel_context import TextPanelContextMixin
from deepcat.ui.post_capture_actions.text_panel_execution import TextPanelExecutionMixin
from deepcat.ui.post_capture_actions.text_panel_history import TextPanelHistoryMixin
from deepcat.ui.post_capture_actions.text_panel_window import TextPanelWindowMixin


class OcrTextPanel(
    TextPanelContextMixin,
    TextPanelAttachmentsMixin,
    TextPanelExecutionMixin,
    TextPanelHistoryMixin,
    TextPanelWindowMixin,
    QWidget,
):
    _last_pos: Optional[QPoint] = None
    _last_pos_user_moved = False
    _COMPACT_EDITOR_MIN_HEIGHT = 50
    _HISTORY_SIDEBAR_OPEN_UI_KEY = "ai_history_sidebar_open"

    @staticmethod
    def _load_last_pos_from_settings() -> Optional[QPoint]:
        try:
            settings = load_settings()
            ui = dict(getattr(settings, "ui", {}) or {})
            if not bool(ui.get("qa_window_pos_user_moved", False)):
                return None
            pos_list = ui.get("qa_window_pos")
            if isinstance(pos_list, list) and len(pos_list) == 2:
                from PyQt6.QtCore import QPoint
                return QPoint(int(pos_list[0]), int(pos_list[1]))
        except Exception:
            pass
        return None

    @staticmethod
    def _save_last_pos_to_settings() -> None:
        try:
            if OcrTextPanel._last_pos is not None and OcrTextPanel._last_pos_user_moved:
                update_ui_settings(
                    qa_window_pos=[int(OcrTextPanel._last_pos.x()), int(OcrTextPanel._last_pos.y())],
                    qa_window_pos_user_moved=True,
                )
            else:
                update_ui_settings(qa_window_pos=None, qa_window_pos_user_moved=False)
        except Exception:
            pass

    @staticmethod
    def _load_history_sidebar_open_from_settings() -> bool:
        try:
            settings = load_settings()
            ui = dict(getattr(settings, "ui", {}) or {})
            return bool(ui.get(OcrTextPanel._HISTORY_SIDEBAR_OPEN_UI_KEY, False))
        except Exception:
            return False

    @staticmethod
    def _save_history_sidebar_open_to_settings(opened: bool) -> None:
        try:
            update_ui_settings(**{OcrTextPanel._HISTORY_SIDEBAR_OPEN_UI_KEY: bool(opened)})
        except Exception:
            pass

    @staticmethod
    def _apply_premium_format(editor: QTextEdit) -> None:
        try:
            from PyQt6.QtGui import QTextBlockFormat, QTextCursor
            doc = editor.document()

            cursor = QTextCursor(doc)
            cursor.beginEditBlock()

            block = doc.begin()
            while block.isValid():
                fmt = block.blockFormat()

                # 强行拉开物理常规大行距，配置为 1.7 倍黄金比例行高
                fmt.setLineHeight(170, QTextBlockFormat.LineHeightTypes.ProportionalHeight)

                # 列表项与普通段落段距精细微调，营造极致的呼吸感
                if block.textList() is not None:
                    # 如果这一行属于列表项，则应用 6px 间距，彻底消除拥挤
                    fmt.setTopMargin(6)
                    fmt.setBottomMargin(6)
                else:
                    # 普通段落段后留白 12px
                    fmt.setTopMargin(4)
                    fmt.setBottomMargin(12)

                cursor.setPosition(block.position())
                cursor.setBlockFormat(fmt)
                block = block.next()

            cursor.endEditBlock()
        except Exception as e:
            from deepcat.utils.logger import get_logger
            get_logger().warning("划词弹窗高级物理排版优化失败: %s", e)

    def __init__(
        self,
        on_toast: Optional[Callable[[str, str, int], None]] = None,
        *,
        restore_history_sidebar: bool = True,
        quick_action_handler: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setWindowTitle("AI对话")
        self.setWindowIcon(create_app_icon())
        self.setObjectName("OcrTextPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        # 注意：不要设置 WA_ShowWithoutActivating，否则窗口在 show() 时
        # 不会自动获取操作系统级别的焦点，导致刚打开软件后快捷键打开
        # AI 对话窗口时无法输入、无法按 ESC 关闭、无法点击其它地方隐藏
        self._normal_geometry = None
        self._region = QRect(0, 0, 800, 600)
        self._dragging_window = False
        self._drag_offset = QPoint()
        self._drag_event_sources = []
        self._original_text = ""
        self._current_input_origin = "manual"
        self._center_on_first_show = False
        self._loading_text = False
        self._restore_history_sidebar_on_init = bool(restore_history_sidebar)
        self._text_selection_update_block_until = 0.0
        self._external_reuse_action_block_until = 0.0
        self._editor = PremiumTextEdit()
        self._editor.setObjectName("OcrTextPanelQuestionEditor")
        self._editor.document().setDocumentMargin(8)
        self._editor.setPlaceholderText(CHAT_INPUT_WATERMARK_TEXT)
        try:
            editor_palette = self._editor.palette()
            editor_palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#b8b8b8"))
            self._editor.setPalette(editor_palette)
        except Exception:
            pass
        self._editor.setAcceptRichText(False)
        self._editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._editor.verticalScrollBar().setCursor(Qt.CursorShape.ArrowCursor)
        self._editor.verticalScrollBar().setStyleSheet("""
            QScrollBar:vertical { background: transparent; width: 6px; margin: 0px; }
            QScrollBar::handle:vertical { background: #f1f5f9; min-height: 20px; border-radius: 3px; }
            QScrollBar::handle:vertical:hover { background: #94a3b8; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; width: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)
        self._editor_container = RoundedTextEditContainer(self._editor)
        self._editor_container.setMaximumHeight(16777215)

        self._bubble_view = BubbleListView()
        self._bubble_view.setObjectName("OcrAnswerBubbleView")
        self._bubble_view.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        try:
            self._bubble_view.viewport().setStyleSheet("background: #f8fafc;")
            bubble_container = getattr(self._bubble_view, "_container", None)
            if bubble_container is not None:
                bubble_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                bubble_container.setStyleSheet("QWidget#BubbleListContainer { background: #f8fafc; }")
        except Exception:
            pass

        # 注入全局排版美化高级 CSS 样式表，实现极致完美的常规行距与段距呼吸感，完美呈现画册般的高清排版
        css = """
        body {
            font-family: 'Microsoft YaHei', 'Segoe UI', system-ui;
            font-size: 14px;
            color: #1e293b;
            margin: 8px;
        }
        p {
            margin-top: 6px;
            margin-bottom: 12px;
            line-height: 165%;
        }
        h1 {
            font-size: 17px;
            font-weight: bold;
            color: #0f172a;
            margin-top: 22px;
            margin-bottom: 10px;
        }
        h2 {
            font-size: 15px;
            font-weight: bold;
            color: #0f172a;
            margin-top: 16px;
            margin-bottom: 8px;
        }
        h3, h4, h5, h6 {
            font-size: 14px;
            font-weight: bold;
            color: #1e293b;
            margin-top: 12px;
            margin-bottom: 6px;
        }
        ul, ol {
            margin-top: 8px;
            margin-bottom: 12px;
            padding-left: 20px;
        }
        li {
            margin-top: 6px;
            margin-bottom: 6px;
            line-height: 160%;
            list-style-type: circle;
        }
        strong, b {
            font-weight: bold;
            color: #0f172a;
        }
        .chat-container {
            padding: 4px;
            background-color: #f3f4f6;
        }
        .msg-row {
            margin-top: 10px;
            margin-bottom: 10px;
            width: 100%;
        }
        .msg-user {
            text-align: right;
        }
        .msg-ai {
            text-align: left;
        }
        .bubble {
            display: inline-block;
            max-width: 82%;
            padding: 12px 18px;
            font-family: 'Microsoft YaHei', 'Segoe UI', system-ui;
            font-size: 14px;
            line-height: 160%;
            text-align: left;
        }
        .bubble-user {
            background-color: #edf2fa;
            color: #1c2438;
            border-radius: 18px;
            border: none;
        }
        .bubble-ai {
            background-color: #ffffff;
            color: #1c2438;
            border-radius: 18px;
            border: none;
        }
        """
        self._editor.document().setDefaultStyleSheet(css)

        self._bubble_view.hide()
        self._translation_info = QLabel(self)
        self._translation_info.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._translation_info.setStyleSheet("color: rgba(128,128,128,0.35); font-size: 11px; background: transparent; border: none;")
        self._translation_info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._translation_info.hide()
        self._translation_worker: Optional[OcrTranslationWorker] = None
        self._ima_note_search_worker: Optional[ImaNoteSearchWorker] = None
        self._aborted_worker_ids: set[int] = set()
        self._translation_started_ts = 0.0
        self._translation_model_display = ""
        self._translation_task = "translate"
        self._translation_result_label = "翻译结果"
        self._on_toast = on_toast
        self._quick_action_handler = quick_action_handler
        self._prompt_settings_popup: Optional[OcrGenericMenuPopup] = None
        self._prompt_settings_popup_hide_timer = QTimer(self)
        self._prompt_settings_popup_hide_timer.setSingleShot(True)
        self._prompt_settings_popup_hide_timer.timeout.connect(self._hide_prompt_settings_popup_if_outside)
        self._chat_history = []
        self._current_session_record_id = None
        self._is_chatting = False
        self._active_assistant_msg_index = None
        self._context_manager_expanded = False
        self._context_recent_round_limit = None
        self._context_summary_text = ""
        self._context_summary_anchor_index = 0
        self._pending_context_summary_anchor_index = None
        self._context_warning_dismissed_snapshot = None
        self._context_warning_current_snapshot = None
        self._preserve_cleared_output_area = False
        self._suppress_reposition = False
        self._pending_attachments = []
        self._pending_attachment_previews = []
        self._pending_attachment_labels = []
        self._attachment_preview_paths = {}
        self._create_attachment_preview_bar()


        self._stream_markdown_text = ""
        OcrTextPanel._reset_stream_token_cache(self)
        self._stream_render_signature = None
        self._is_expanded = False
        self._panel_collapsed = False
        self._panel_collapse_restore_size = QSize()
        self._panel_collapse_restore_geometry = QRect()
        self._panel_collapse_restore_maximized = False
        self._pending_native_maximize_restore = False
        self._forcing_native_maximize = False
        self._forcing_normal_window_state = False
        self._maximized_work_area_geometry = QRect()
        self._window_mode_generation = 0
        self._panel_collapse_target_geometry = QRect()
        self._skip_uncollapse_reposition_once = False
        self._was_snapped = False
        self._last_snapped_pos = None
        self._last_expanded_pos = None
        self._initial_panel_geometry = QRect()
        self._pre_max_geometry = QRect()
        self._cleared_output_restore_geometry = QRect()
        self._pre_max_editor_height = 0
        self._ocr_input_auto_height_cap = 0
        self._pre_expand_snapshot = None
        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setSingleShot(True)
        self._stream_flush_timer.timeout.connect(self._flush_stream_markdown)
        self._source_lang = UpwardComboBox()
        self._source_lang.addItems(["自动检测", "中文", "英文", "日文", "韩文", "法文", "德文", "西班牙文"])
        self._target_lang = UpwardComboBox()
        self._target_lang.addItems(["中英互译", "中文", "英文", "日文", "韩文", "法文", "德文", "西班牙文"])
        for combo in (self._source_lang, self._target_lang):
            combo.setProperty(POPUP_EXACT_WIDTH_PROPERTY, True)
            combo.setProperty("centerText", True)
            combo.setProperty("activeIndicator", "background")
        self._load_language_settings()
        asset_dir = Path(__file__).resolve().parent.parent / "assets"
        self._btn_translate = HoverIconButton(
            "translate",
            str(asset_dir / "icon_chat_translate_outline.svg"),
            str(asset_dir / "icon_chat_translate_filled.svg"),
            "翻译",
            self
        )
        self._btn_prompt_settings = HoverIconButton(
            "prompt",
            str(asset_dir / "icon_chat_prompt_outline.svg"),
            str(asset_dir / "icon_chat_prompt_filled.svg"),
            "快捷",
            self
        )
        self._btn_qa = HoverIconButton(
            "qa",
            str(asset_dir / "icon_chat_qa_outline.svg"),
            str(asset_dir / "icon_chat_qa_filled.svg"),
            "发起问答",
            self
        )
        self._btn_copy = QPushButton("复制")
        self._btn_history = HoverIconButton(
            "history",
            str(asset_dir / "icon_chat_history_outline.svg"),
            str(asset_dir / "icon_chat_history_filled.svg"),
            "查看历史",
            self
        )
        self._btn_clear_chat = HoverIconButton(
            "clear",
            str(asset_dir / "icon_chat_clear_outline.svg"),
            str(asset_dir / "icon_chat_clear_filled.svg"),
            "清空会话",
            self
        )
        self._btn_attachment = HoverIconButton(
            "attachment",
            str(asset_dir / "icon_chat_attachment_outline.svg"),
            str(asset_dir / "icon_chat_attachment_filled.svg"),
            "上传附件",
            self
        )
        self._btn_minimize = QPushButton("最小化")
        self._btn_close = QPushButton("关闭")
        self._buttons = [self._btn_translate, self._btn_prompt_settings, self._btn_qa, self._btn_copy, self._btn_history, self._btn_clear_chat, self._btn_attachment, self._btn_minimize, self._btn_close]
        for btn in self._buttons:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            set_disable_global_tooltip(btn)

        self._btn_collapse = CollapseArrowButton(self)
        self._btn_collapse.clicked.connect(self._toggle_bottom_collapsed)

        self._source_label = QLabel("源:")
        self._target_label = QLabel("目标:")
        self._hover_translate_model_label = self._make_bottom_model_hover_label(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._hover_qa_model_label = self._make_bottom_model_hover_label(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # ---- 底部控制栏包裹组件 ----
        self._bottom_row_widget = QWidget()
        self._bottom_row_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._bottom_row_widget.setObjectName("BottomRowWidget")
        row = QHBoxLayout(self._bottom_row_widget)
        row.setContentsMargins(6, 6, 12, 6)
        row.setSpacing(6)
        self._bottom_row_layout = row
        row.addWidget(self._btn_collapse)
        row.addWidget(self._source_label)
        row.addWidget(self._source_lang)
        row.addWidget(self._target_label)
        row.addWidget(self._target_lang)
        row.addWidget(self._btn_translate)
        row.addWidget(self._hover_translate_model_label)
        self._btn_copy.hide()
        row.addStretch(1)
        row.addWidget(self._hover_qa_model_label)
        row.addWidget(self._btn_prompt_settings)
        row.addWidget(self._btn_history)
        row.addWidget(self._btn_clear_chat)
        row.addWidget(self._btn_attachment)
        row.addWidget(self._btn_qa)
        self._btn_minimize.hide()
        self._btn_close.hide()
        self._bottom_row_widget.setFixedHeight(44)

        try:
            collapsed = self._load_bottom_collapse_state()
            self._btn_collapse.set_collapsed(collapsed)
            show_val = not collapsed
            self._source_label.setVisible(show_val)
            self._source_lang.setVisible(show_val)
            self._target_label.setVisible(show_val)
            self._target_lang.setVisible(show_val)
            self._btn_translate.setVisible(show_val)
        except Exception:
            pass

        # 翻译与问答按钮的右键自定义上下文菜单切换模型策略
        self._btn_translate.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._btn_translate.customContextMenuRequested.connect(lambda pos: self._show_model_menu(self._btn_translate, "translate", pos))
        self._btn_qa.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._btn_qa.customContextMenuRequested.connect(lambda pos: self._show_model_menu(self._btn_qa, "qa", pos))
        # ---- 历史记录集成 ----
        try:
            self._history_store = TranslationHistoryStore()
        except Exception:
            self._history_store = None
        self._history_showing = False
        self._is_collapsed = False
        self._content_stack = QWidget()
        self._content_stack.setObjectName("OcrContentStack")
        self._content_stack.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content_layout = QVBoxLayout(self._content_stack)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self._title_bar = OcrTitleBar(self)
        self._title_bar_shell = QWidget()
        self._title_bar_shell.setObjectName("OcrTitleBarShell")
        self._title_bar_shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        title_shell_layout = QHBoxLayout(self._title_bar_shell)
        title_shell_layout.setContentsMargins(0, 0, 0, 0)
        title_shell_layout.setSpacing(0)
        title_shell_layout.addWidget(self._title_bar)
        content_layout.addWidget(self._title_bar_shell)

        self._panel_status_label = QLabel()
        self._panel_status_label.setObjectName("OcrPanelInlineStatus")
        self._panel_status_label.setWordWrap(True)
        self._panel_status_label.setVisible(False)
        self._panel_status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        content_layout.addWidget(self._panel_status_label)

        self._context_warning_dismissed = False
        self._context_warning_bar = QFrame(self._content_stack)
        self._context_warning_bar.setObjectName("OcrContextWarningBar")
        self._context_warning_bar.setVisible(False)
        self._context_warning_bar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        warning_layout = QVBoxLayout(self._context_warning_bar)
        warning_layout.setContentsMargins(10, 6, 10, 6)
        warning_layout.setSpacing(6)
        warning_top_row = QHBoxLayout()
        warning_top_row.setContentsMargins(0, 0, 0, 0)
        warning_top_row.setSpacing(8)
        self._context_warning_label = QLabel("上下文较长，可能影响速度/费用。")
        self._context_warning_label.setWordWrap(True)
        warning_top_row.addWidget(self._context_warning_label, 1)
        self._context_expand_btn = QPushButton("管理")
        self._context_expand_btn.setObjectName("OcrContextWarningAction")
        self._context_expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        warning_top_row.addWidget(self._context_expand_btn)
        self._context_close_btn = QToolButton(self._context_warning_bar)
        self._context_close_btn.setObjectName("OcrContextWarningClose")
        self._context_close_btn.setText("×")
        self._context_close_btn.setFixedSize(22, 22)
        self._context_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._context_close_btn.setToolTip("关闭")
        warning_top_row.addWidget(self._context_close_btn)
        warning_layout.addLayout(warning_top_row)
        self._context_details_label = QLabel("")
        self._context_details_label.setObjectName("OcrContextWarningDetails")
        self._context_details_label.setWordWrap(True)
        self._context_details_label.setVisible(False)
        warning_layout.addWidget(self._context_details_label)
        self._context_actions_row = QWidget()
        context_actions_layout = QHBoxLayout(self._context_actions_row)
        context_actions_layout.setContentsMargins(0, 0, 0, 0)
        context_actions_layout.setSpacing(8)
        self._context_recent_btn = QPushButton("只带最近 5 轮")
        self._context_recent_btn.setObjectName("OcrContextWarningAction")
        self._context_recent_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._context_summary_btn = QPushButton("总结前文并继续")
        self._context_summary_btn.setObjectName("OcrContextWarningAction")
        self._context_summary_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._context_new_chat_btn = QPushButton("新建会话")
        self._context_new_chat_btn.setObjectName("OcrContextWarningAction")
        self._context_new_chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        context_actions_layout.addWidget(self._context_recent_btn)
        context_actions_layout.addWidget(self._context_summary_btn)
        context_actions_layout.addWidget(self._context_new_chat_btn)
        context_actions_layout.addStretch(1)
        self._context_actions_row.setVisible(False)
        warning_layout.addWidget(self._context_actions_row)

        self._content_body = QWidget()
        self._content_body.setObjectName("OcrContentBody")
        self._content_body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body_layout = QHBoxLayout(self._content_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self._history_sidebar = AIChatHistorySidebar(self._history_store, self)
        self._history_sidebar.hide()
        self._history_sidebar.record_selected.connect(self._load_history_record_into_chat)
        self._history_sidebar.record_search_selected.connect(self._load_history_record_into_chat)
        self._history_sidebar.new_chat_requested.connect(self._start_new_chat_from_sidebar)
        self._history_sidebar.records_deleted.connect(self._on_history_sidebar_records_deleted)
        body_layout.addWidget(self._history_sidebar)

        self._interaction_container = QWidget()
        self._interaction_container.setObjectName("OcrInteractionContainer")
        self._interaction_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._interaction_container.setStyleSheet(
            "QWidget#OcrInteractionContainer { background: transparent; border: none; }"
            "QWidget#OcrInteractionContainer[panelMaximized=\"true\"] { background: transparent; border: none; }"
        )

        inter_layout = QVBoxLayout(self._interaction_container)
        # 只保留左右缩进；上下由标题栏、问题区、按钮栏直接拼接，避免透明缝隙透出底色。
        inter_layout.setContentsMargins(0, 0, 0, 0)
        inter_layout.setSpacing(0)

        inter_layout.addWidget(self._bubble_view, 4)
        self._output_quick_action_bar = OutputQuickActionBar(
            self._dispatch_output_quick_action,
            asset_dir,
            self._interaction_container,
        )
        self._output_quick_action_bar.hide()
        inter_layout.addWidget(self._output_quick_action_bar)

        # 实例化流式思考卡片并将其挂载在问题框与回答框之间
        self._thinking_card = _ThinkingCard(self)
        inter_layout.addWidget(self._thinking_card)
        inter_layout.addWidget(self._editor_container, 2)
        inter_layout.addWidget(self._bottom_row_widget)
        self._interaction_top_seam_cover = QFrame(self._interaction_container)
        self._interaction_top_seam_cover.setObjectName("OcrInteractionTopSeamCover")
        self._interaction_top_seam_cover.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._interaction_top_seam_cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._interaction_top_seam_cover.setStyleSheet(
            "QFrame#OcrInteractionTopSeamCover { background: #ffffff; border: none; }"
        )
        self._interaction_top_seam_cover.hide()
        self._editor_top_seam_cover = QFrame(self._interaction_container)
        self._editor_top_seam_cover.setObjectName("OcrEditorTopSeamCover")
        self._editor_top_seam_cover.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._editor_top_seam_cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._editor_top_seam_cover.setStyleSheet(
            "QFrame#OcrEditorTopSeamCover { background: #ffffff; border: none; }"
        )
        self._editor_top_seam_cover.hide()

        body_layout.addWidget(self._interaction_container, 1)
        content_layout.addWidget(self._content_body, 1)
        self._history_panel = None
        self._view_stack = QWidget()
        self._view_stack.setObjectName("OcrViewStack")
        self._view_stack.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._view_stack_layout = QVBoxLayout(self._view_stack)
        self._view_stack_layout.setContentsMargins(0, 0, 0, 0)
        self._view_stack_layout.setSpacing(0)
        self._view_stack_layout.addWidget(self._content_stack)

        view_shadow = QGraphicsDropShadowEffect(self._view_stack)
        view_shadow.setBlurRadius(16)
        view_shadow.setOffset(0, 4)
        view_shadow.setColor(QColor(0, 0, 0, 45))
        self._view_stack.setGraphicsEffect(view_shadow)

        self._collapsed_widget = QWidget()
        self._collapsed_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._collapsed_widget.setObjectName("OcrCollapsedWidget")
        collapsed_row = QHBoxLayout(self._collapsed_widget)
        collapsed_row.setContentsMargins(20, 0, 12, 0)
        collapsed_row.setSpacing(8)

        # 折叠状态下的软件猫头图标
        self.c_dot_owl = QLabel()
        self.c_dot_owl.setFixedSize(22, 22)
        self.c_dot_owl.setPixmap(_load_title_owl_pixmap(22))
        self.c_dot_owl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.c_dot_owl.setStyleSheet("background: transparent; border: none;")
        collapsed_row.addWidget(self.c_dot_owl)
        collapsed_row.addSpacing(-4)  # 拉近文字与图标的距离

        self._collapsed_title = QLabel("AI对话")
        self._collapsed_title.setObjectName("OcrCollapsedTitle")
        collapsed_row.addWidget(self._collapsed_title, 1)
        self._collapsed_restore_btn = QPushButton("+")
        self._collapsed_restore_btn.setObjectName("OcrCollapsedBtn")
        self._collapsed_restore_btn.setToolTip("还原")
        self._collapsed_restore_btn.setFixedSize(28, 28)
        self._collapsed_restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        collapsed_row.addWidget(self._collapsed_restore_btn)
        self._collapsed_close_btn = QPushButton("✕")
        self._collapsed_close_btn.setObjectName("OcrCollapsedBtnClose")
        self._collapsed_close_btn.setToolTip("关闭")
        self._collapsed_close_btn.setFixedSize(28, 28)
        self._collapsed_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        collapsed_row.addWidget(self._collapsed_close_btn)
        collapsed_shadow = QGraphicsDropShadowEffect(self._collapsed_widget)
        collapsed_shadow.setBlurRadius(16)
        collapsed_shadow.setOffset(0, 4)
        collapsed_shadow.setColor(QColor(0, 0, 0, 45))
        self._collapsed_widget.setGraphicsEffect(collapsed_shadow)
        self._collapsed_widget.hide()

        self._floating_minimize_btn = _OcrTitleBarButton(self)
        self._floating_minimize_btn.setObjectName("FloatingWindowBtn")
        self._floating_minimize_btn.setIcon(OcrTitleBar._make_minimize_icon(16))
        self._floating_minimize_btn.setIconSize(QSize(16, 16))
        self._floating_minimize_btn.setToolTip("最小化")
        self._floating_minimize_btn.setFixedSize(30, 30)
        self._floating_minimize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._floating_minimize_btn.clicked.connect(self._minimize_panel)
        self._floating_minimize_btn.hide()

        self._floating_close_btn = _OcrTitleBarButton(self)
        self._floating_close_btn.setObjectName("FloatingWindowBtnClose")
        self._floating_close_btn.setIcon(OcrTitleBar._make_close_icon(16))
        self._floating_close_btn.setIconSize(QSize(16, 16))
        self._floating_close_btn.setToolTip("关闭窗口")
        self._floating_close_btn.setFixedSize(30, 30)
        self._floating_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._floating_close_btn.clicked.connect(self.close)
        self._floating_close_btn.hide()

        # self._panel_background = QFrame(self)
        # self._panel_background.setObjectName("OcrPanelBackground")
        # self._panel_background.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # self._panel_background.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # self._panel_background.setStyleSheet(
        #     "QFrame#OcrPanelBackground { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 12px; }"
        # )
        # self._panel_background.lower()


        root = QVBoxLayout()
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(0)
        root.addWidget(self._view_stack, 1)
        root.addWidget(self._collapsed_widget)
        self._root_layout = root
        self.setLayout(root)
        self.setStyleSheet(
            "QWidget#OcrTextPanel { background: transparent; border: none; }"
            "QWidget#OcrTextPanel[panelMaximized=\"true\"] { background: transparent; border: none; }"
            "QWidget#OcrTextPanel[panelCollapsed=\"true\"] { background: transparent; border: none; }"
            "QWidget#OcrContentStack { background: #ffffff; border: none; border-radius: 8px; }"
            "QWidget#OcrViewStack, QWidget#OcrTitleBarShell, QWidget#OcrContentBody { background: transparent; border: none; }"
            "QWidget#OcrInteractionContainer { background: transparent; border: none; }"
            "QWidget#OcrContentStack[panelMaximized=\"true\"] { background: #ffffff; border: none; border-radius: 0px; }"
            "QWidget#OcrViewStack[panelMaximized=\"true\"], QWidget#OcrTitleBarShell[panelMaximized=\"true\"], QWidget#OcrContentBody[panelMaximized=\"true\"] { background: transparent; border: none; }"
            "QWidget#OcrInteractionContainer[panelMaximized=\"true\"] { background: transparent; border: none; }"
            "QWidget#OcrCollapsedWidget { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 12px; }"
            "QLabel#OcrCollapsedTitle { color: #475569; background: transparent; border: none; font-size: 13px; font-weight: 500; padding-left: 8px; }"
            "QPushButton#OcrCollapsedBtn, QPushButton#OcrCollapsedBtnClose { background: transparent; border: none; color: #64748b; font-weight: bold; font-size: 13px; padding: 0px; margin: 0px; min-width: 28px; }"
            "QPushButton#OcrCollapsedBtn:hover { color: #2563eb; background: transparent; }"
            "QPushButton#OcrCollapsedBtnClose:hover { color: #ef4444; background: transparent; }"
            "QLabel { background: transparent; border: none; color: #334155; }"
            "QTextEdit { background: transparent; border: none; padding: 0px; font-family: 'Microsoft YaHei', 'Segoe UI', system-ui; font-size: 14px; color: #111827; }"
            "QFrame#RoundedTextEditContainer { background: #ffffff; border-left: 1px solid #f1f5f9; border-right: 1px solid #f1f5f9; border-top: 1px solid #f1f5f9; border-bottom: none; border-radius: 0px; margin: 0px; margin-top: -1px; margin-bottom: -1px; }"
            "QFrame#RoundedTextEditContainer[hovered=\"true\"] { border-left: 1px solid #f1f5f9; border-right: 1px solid #f1f5f9; border-top: 1px solid #f1f5f9; border-bottom: none; margin: 0px; margin-top: -1px; margin-bottom: -1px; }"
            "QFrame#RoundedTextEditContainer[titleBarHidden=\"true\"] { background: #ffffff; border-left: 1px solid #f1f5f9; border-right: 1px solid #f1f5f9; border-top: 1px solid #f1f5f9; border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; border-bottom-left-radius: 0px; border-bottom-right-radius: 0px; margin: 0px; margin-top: 0px; margin-bottom: -1px; }"
            "QFrame#RoundedTextEditContainer[titleBarHidden=\"true\"][hovered=\"true\"] { background: #ffffff; border-left: 1px solid #f1f5f9; border-right: 1px solid #f1f5f9; border-top: 1px solid #f1f5f9; border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; border-bottom-left-radius: 0px; border-bottom-right-radius: 0px; margin: 0px; margin-top: 0px; margin-bottom: -1px; }"
            "QFrame#RoundedTextEditContainerTranslation { background: #ffffff; border-left: 1px solid #f1f5f9; border-right: 1px solid #f1f5f9; border-top: 1px solid #f1f5f9; border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; border-bottom-left-radius: 0px; border-bottom-right-radius: 0px; margin: 0px; margin-top: -1px; margin-bottom: -1px; }"
            "QFrame#RoundedTextEditContainerTranslation[hovered=\"true\"] { border-left: 1px solid #f1f5f9; border-right: 1px solid #f1f5f9; border-top: 1px solid #f1f5f9; border-bottom: none; margin: 0px; margin-top: -1px; margin-bottom: -1px; }"
            "QComboBox { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; padding: 1px 6px; color: #111827; min-width: 66px; min-height: 26px; max-height: 26px; }"
            "QComboBox[centerText=\"true\"] { padding-left: 4px; padding-right: 4px; }"
            "QComboBox:hover { border: 1px solid #cbd5e1; }"
            "QComboBox:focus { border: 1px solid #cbd5e1; }"
            "QComboBox::drop-down { border: none; width: 0px; }"
            "QComboBox::down-arrow { image: none; width: 0px; height: 0px; }"
            "QComboBox QAbstractItemView { background-color: #ffffff; border: 1px solid #dfe4ec; border-radius: 8px; padding: 4px; outline: none; }"
            "QComboBox QAbstractItemView::item { min-height: 28px; padding: 2px 10px; border-radius: 6px; color: #374151; font-size: 13px; font-weight: 500; }"
            "QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected { background-color: #f3f4f6; color: #111827; }"
            "QPushButton { background: #f1f5f9; color: #334155; border: none; border-radius: 8px; padding: 4px 10px; font-weight: 700; font-size: 12px; min-height: 24px; }"
            "QPushButton:hover { background: #e2e8f0; color: #0f172a; }"
            "QPushButton:pressed { background: #cbd5e1; }"
            "QPushButton:disabled { background: #cbd5e1; color: rgba(255,255,255,0.82); }"
            "QPushButton#BtnPrimaryAction { background: #1e293b; color: white; }"
            "QPushButton#BtnPrimaryAction:hover { background: #334155; }"
            "QPushButton#BtnPrimaryAction:pressed { background: #0f172a; }"
            "QPushButton[objectName^=\"HoverIconButton_\"] { background: transparent; border: none; border-radius: 6px; min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; padding: 0px; }"
            "QPushButton[objectName^=\"HoverIconButton_\"]:hover { background: #e2e8f0; }"
            "QPushButton[objectName^=\"HoverIconButton_\"]:pressed { background: #cbd5e1; }"
            "QPushButton[objectName^=\"HoverIconButton_\"]:disabled { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 0px; }"
            "QScrollBar::handle:vertical { background: #f1f5f9; min-height: 20px; border-radius: 3px; margin: 0px; }"
            "QScrollBar::handle:vertical:hover { background: #94a3b8; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; width: 0px; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
            "QScrollBar:horizontal { height: 0px; }"
            "QTextEdit QScrollBar:vertical { background: transparent; width: 6px; margin: 0px; }"
            "QTextEdit QScrollBar::handle:vertical { background: #f1f5f9; min-height: 20px; border-radius: 3px; margin: 0px; }"
            "QTextEdit QScrollBar::handle:vertical:hover { background: #94a3b8; }"
            "QTextEdit QScrollBar::add-line:vertical, QTextEdit QScrollBar::sub-line:vertical { height: 0px; width: 0px; }"
            "QTextEdit QScrollBar::add-page:vertical, QTextEdit QScrollBar::sub-page:vertical { background: transparent; }"
            "QWidget#BottomRowWidget { background: #ffffff; border-left: 1px solid #f1f5f9; border-right: 1px solid #f1f5f9; border-bottom: 1px solid #f1f5f9; border-top: 1px solid #ffffff; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; border-top-left-radius: 0px; border-top-right-radius: 0px; margin: 0px; }"
            "QWidget#BottomRowWidget[historyOpen=\"true\"] { border-bottom-left-radius: 0px; border-top-left-radius: 0px; }"
            "QWidget#BottomRowWidget[panelMaximized=\"true\"] { border-bottom-left-radius: 0px; border-bottom-right-radius: 0px; }"
            "QWidget#OcrTitleBar { background: #f6f8fb; border-left: 1px solid #f1f5f9; border-right: 1px solid #f1f5f9; border-top: 1px solid #f1f5f9; border-bottom: 1px solid #f1f5f9; border-top-left-radius: 8px; border-top-right-radius: 8px; border-bottom-left-radius: 0px; border-bottom-right-radius: 0px; margin: 0px; margin-bottom: -1px; }"
            "QWidget#OcrTitleBar[panelMaximized=\"true\"] { border-top-left-radius: 0px; border-top-right-radius: 0px; margin-bottom: -1px; }"
            "QScrollArea#OcrAnswerBubbleView { background: #f8fafc; border-left: 1px solid #f1f5f9; border-right: 1px solid #f1f5f9; border-bottom: 1px solid rgba(15,23,42,0.035); border-top: none; border-top-left-radius: 0px; border-top-right-radius: 0px; border-bottom-left-radius: 0px; border-bottom-right-radius: 0px; margin: 0px; margin-top: -1px; }"
            "QScrollArea#OcrAnswerBubbleView > QWidget, QWidget#BubbleListContainer { background: #f8fafc; }"
            "QPushButton#TitleBarBtn { background: #f6f8fb !important; border: none !important; color: #64748b !important; font-weight: bold !important; font-size: 13px !important; padding: 0px !important; margin-top: 1px !important; margin-bottom: 1px !important; margin-left: 0px !important; margin-right: 0px !important; min-width: 46px !important; max-width: 46px !important; min-height: 28px !important; max-height: 28px !important; }"
            "QPushButton#TitleBarBtnClose { background: #f6f8fb !important; border: none !important; border-radius: 0px !important; border-top-right-radius: 7px !important; border-bottom-right-radius: 0px !important; color: #64748b !important; font-weight: bold !important; font-size: 13px !important; padding: 0px !important; margin-top: 0px !important; margin-bottom: 1px !important; margin-left: 0px !important; margin-right: 0px !important; min-width: 46px !important; max-width: 46px !important; min-height: 29px !important; max-height: 29px !important; }"
            "QPushButton#TitleBarBtnClose[panelMaximized=\"true\"] { border-top-right-radius: 0px !important; }"
            "QPushButton#FloatingWindowBtn, QPushButton#FloatingWindowBtnClose { background: transparent; border: none; border-radius: 15px; padding: 0px; }"
            "QPushButton#FloatingWindowBtn:hover { background: #f1f5f9; border: none; border-radius: 15px; }"
            "QPushButton#FloatingWindowBtnClose:hover { background: #fee2e2; border: none; border-radius: 15px; }"


            "QPushButton#TitleBarBtn:hover { color: #2563eb !important; background: #eef2f7 !important; border-radius: 0px !important; }"
            "QPushButton#TitleBarBtnClose:hover { color: #ffffff !important; background: #c42b1c !important; border-radius: 0px !important; border-top-right-radius: 7px !important; border-bottom-right-radius: 0px !important; }"
            "QPushButton#TitleBarBtnClose[panelMaximized=\"true\"]:hover { border-top-right-radius: 0px !important; }"
            "QFrame#OcrContextWarningBar { background: rgba(255,251,235,245); border: 1px solid #fde68a; border-radius: 8px; margin: 0px; }"
            "QFrame#OcrContextWarningBar QLabel { color: #92400e; font-size: 12px; font-weight: 600; }"
            "QLabel#OcrContextWarningDetails { color: #92400e; font-size: 12px; font-weight: 400; line-height: 150%; }"
            "QPushButton#OcrContextWarningAction { background: #ffffff; color: #92400e; border: 1px solid #fcd34d; border-radius: 6px; padding: 3px 8px; min-height: 22px; font-size: 12px; }"
            "QPushButton#OcrContextWarningAction:hover { background: #fef3c7; color: #78350f; }"
            "QToolButton#OcrContextWarningClose { background: transparent; color: #92400e; border: none; border-radius: 11px; font-size: 14px; font-weight: 700; padding: 0px; }"
            "QToolButton#OcrContextWarningClose:hover { background: #fde68a; color: #78350f; }"

        )
        sync_title_bar = getattr(self, "_sync_title_bar_visibility", None)
        if callable(sync_title_bar):
            sync_title_bar()
        for b in self._buttons:
            b.installEventFilter(self)
        for c in [self._source_lang, self._target_lang]:
            c.setFixedHeight(26)
            c.setFixedWidth(86)
            c.currentIndexChanged.connect(self._persist_language_settings)

        # 拖拽变量初始化与事件过滤器安装
        self._editor_drag_start_pos = None
        self._editor_drag_window_active = False
        self._editor_drag_offset = QPoint()

        try:
            self._editor_container.installEventFilter(self)
            self._editor.viewport().installEventFilter(self)
        except Exception:
            pass
        self._text_edit_event_sources = [self._editor_container, self._editor]
        try:
            self._text_edit_event_sources.append(self._editor.viewport())
        except Exception:
            pass
        for floating_btn in (getattr(self, "_floating_minimize_btn", None), getattr(self, "_floating_close_btn", None)):
            try:
                if floating_btn is not None:
                    floating_btn.installEventFilter(self)
            except Exception:
                pass

        # 为 bubble_view 注册 Resize 事件以重算浮层位置
        self._bubble_view.installEventFilter(self)
        # 为 bubble_view.viewport() 注册拖动事件过滤器以支持拖移窗口
        try:
            self._bubble_view.viewport().installEventFilter(self)
        except Exception:
            pass

        self._drag_event_sources = [self._source_label, self._target_label, self._bottom_row_widget, self._collapsed_widget, self._collapsed_title]
        for w in self._drag_event_sources:
            try:
                w.installEventFilter(self)
            except Exception:
                pass

        self._btn_copy.clicked.connect(self._copy_text)
        self._btn_history.clicked.connect(self._toggle_history)
        self._btn_minimize.clicked.connect(self._minimize_panel)
        self._btn_close.clicked.connect(self.close)
        self._collapsed_restore_btn.clicked.connect(lambda: self._toggle_panel_collapse(False))
        self._collapsed_close_btn.clicked.connect(self.close)
        self._btn_prompt_settings.clicked.connect(self._show_prompt_settings_popup)
        self._btn_translate.clicked.connect(self._translate_text)
        self._btn_qa.clicked.connect(self._answer_question)
        self._btn_attachment.clicked.connect(self._upload_attachment)
        self._context_expand_btn.clicked.connect(self._toggle_context_manager)
        self._context_recent_btn.clicked.connect(self._use_recent_context_rounds)
        self._context_summary_btn.clicked.connect(self._summarize_current_chat_from_warning)
        self._context_new_chat_btn.clicked.connect(self._start_new_chat_from_context_warning)
        self._context_close_btn.clicked.connect(self._dismiss_context_warning_bar)
        self._btn_clear_chat.clicked.connect(
            lambda _checked=False: self._clear_chat_history(
                clear_editor=False,
                preserve_output_area=True,
            )
        )
        self._editor.textChanged.connect(self._on_source_text_changed)
        self._editor.exit_requested.connect(self.close)
        self._editor.files_pasted.connect(self._handle_pasted_files)
        self._editor.image_pasted.connect(self._handle_pasted_image)
        self._editor.at_triggered.connect(self._handle_at_notes)

        self._bubble_view.copy_requested.connect(self._on_bubble_copy)
        self._bubble_view.delete_requested.connect(self._on_bubble_delete)
        self._bubble_view.regenerate_requested.connect(self._on_bubble_regenerate)
        self._bubble_view.copy_error_requested.connect(self._on_bubble_copy_error)
        self._bubble_view.switch_model_retry_requested.connect(self._on_bubble_switch_model_retry)
        self._bubble_view.new_round_from_error_requested.connect(self._on_bubble_new_round_from_error)
        self._bubble_view.add_to_note_requested.connect(self._on_bubble_add_to_note)
        self._bubble_view.edit_from_requested.connect(self._on_bubble_edit_from)
        self._bubble_view.branch_from_requested.connect(self._on_bubble_branch_from)
        self._bubble_view.pin_context_requested.connect(self._on_bubble_pin_context)
        self._bubble_view.image_preview_requested.connect(self._show_attachment_preview)
        self._bubble_view.follow_up_requested.connect(self._on_follow_up_requested)
        self._bubble_view.placeholder_card_clicked.connect(self._on_placeholder_card_clicked)

        self._esc_shortcut = QShortcut(QKeySequence("Esc"), self)
        self._esc_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._esc_shortcut.activated.connect(self.close)
        self._history_shortcut = QShortcut(QKeySequence("Ctrl+H"), self)
        self._history_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._history_shortcut.activated.connect(self._toggle_history)
        self._watermark = QLabel(self)
        self._watermark.setText(CHAT_INPUT_WATERMARK_TEXT)
        self._watermark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._watermark.setStyleSheet("color: #b8b8b8; font-size: 14px; background: transparent; border: none;")
        self._watermark.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._watermark.hide()
        self._editor.textChanged.connect(self._sync_input_watermark_visibility)
        self._editor.installEventFilter(self)
        self._smooth_tooltip = SmoothToolTip()
        self._keep_bottom_y_on_next_reposition = None
        self._repositioning = False  # setGeometry 期间的防污染标志，moveEvent/resizeEvent 据此跳过底端锚定刷新
        # 几何冻结标志：流式渲染/清除对话等"本不该由 Qt 自动 resize"的窗口期置位，
        # moveEvent/resizeEvent 据此跳过 _last_stable_bottom_y 自更新，防止子控件可见性
        # 变化引起的瞬时膨胀（顶部固定、底端下推）污染底端锚定，从而消除上下跳动闪烁。
        self._geometry_frozen = False
        trace_now = time.perf_counter()
        self._ai_geometry_trace_seq = 0
        self._ai_geometry_trace_started_at = trace_now
        self._ai_geometry_last_event_at = trace_now
        self._ai_geometry_last_event_name = ""
        self._ai_geometry_last_logged_geometry = QRect(self.geometry())
        self._ai_geometry_last_logged_frame_geometry = QRect(self.frameGeometry())
        QTimer.singleShot(0, self._restore_history_sidebar_state_from_settings)
        self._log_ai_geometry("panel.init.completed")

    def _dispatch_output_quick_action(self, action_id: str) -> None:
        if action_id == "add_chat_card":
            self._on_add_chat_card_clicked()
            return

        if action_id == "switch_model":
            btn = self._output_quick_action_bar.find_button("switch_model")
            if btn is not None:
                global_pos = btn.mapToGlobal(QPoint(0, btn.height()))
                self._show_model_selection_menu(
                    btn,
                    "qa",
                    global_pos,
                    lambda model_name: self._switch_model("qa", model_name),
                )
            return

        handler = getattr(self, "_quick_action_handler", None)
        if not callable(handler):
            return
        try:
            handler(str(action_id or ""), self)
        except TypeError:
            handler(str(action_id or ""))

    def _on_add_chat_card_clicked(self) -> None:
        from deepcat.ui.settings_dialog.sub_dialogs import AddChatCardDialog
        from PyQt6.QtWidgets import QDialog
        import json
        import time

        dlg = AddChatCardDialog(self.window())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        title = dlg.card_title()
        icon_type = dlg.card_icon_type()
        prompt = dlg.card_prompt()
        if not title or not prompt:
            return

        try:
            from deepcat.prompt_store import PromptStore
            store = PromptStore()
            prompts = store.load_all_prompts()
            try:
                saved_cards = json.loads(prompts.get("chat_placeholder_cards", "[]"))
            except Exception:
                saved_cards = []
            if not isinstance(saved_cards, list):
                saved_cards = []

            card_id = f"custom_{int(time.time())}"
            new_card = {
                "id": card_id,
                "icon_type": icon_type,
                "title": title,
                "prompt": prompt
            }
            saved_cards.append(new_card)

            # 持久化到 SQLite (prompt.db)
            store.save_prompt("chat_placeholder_cards", json.dumps(saved_cards, ensure_ascii=False))

            # 同步内存设置
            from deepcat.settings_store import update_ui_settings
            update_ui_settings(chat_placeholder_cards=saved_cards)

            # 刷新当前的卡片占位视图
            if hasattr(self._bubble_view, "refresh_placeholder"):
                self._bubble_view.refresh_placeholder()
        except Exception as e:
            logger.error("Failed to add custom chat card: %s", e)

    def submit_ocr_text_for_qa(self, text: str) -> bool:
        clean_text = str(text or "").strip()
        if not clean_text:
            return False
        try:
            self.set_text_and_reposition(
                clean_text,
                QRect(getattr(self, "_region", QRect())),
                force_initial_view=True,
                ocr_input_auto_height=True,
                input_origin="ocr",
            )
            return True
        except Exception:
            logger.exception("OCR文本回填到AI对话输入框失败")
            return False

    def _clear_ocr_input_auto_height(self) -> None:
        """退出 OCR 输入自适应态，后续对话恢复标准输入区高度。"""

        self._ocr_input_auto_height_cap = 0

    def _sync_output_quick_action_bar_visibility(self) -> None:
        bar = getattr(self, "_output_quick_action_bar", None)
        if bar is None:
            return
        should_show = False
        try:
            bubble_view = getattr(self, "_bubble_view", None)
            has_history = bool(getattr(self, "_chat_history", None))
            output_area_visible = bool(bubble_view is not None and bubble_view.isVisible())
            output_area_empty = bool(bubble_view is not None and bubble_view.is_empty())
            should_show = bool(
                callable(getattr(self, "_quick_action_handler", None))
                and output_area_visible
                and output_area_empty
                and not has_history
                and not bool(getattr(self, "_is_chatting", False))
                and (
                    bool(getattr(self, "_preserve_cleared_output_area", False))
                    or bool(getattr(self, "_history_forced_output_area", False))
                )
            )
        except Exception:
            should_show = False
        bar.setVisible(should_show)

    def _restore_history_sidebar_state_from_settings(self) -> None:
        if not bool(getattr(self, "_restore_history_sidebar_on_init", True)):
            return
        if bool(getattr(self, "_history_only_mode", False)):
            return
        if bool(getattr(self, "_history_showing", False)):
            return
        if getattr(self, "_history_sidebar", None) is None:
            return
        if not OcrTextPanel._load_history_sidebar_open_from_settings():
            return
        self._show_history(persist_state=False)

    def _show_panel_status(
        self,
        text: str,
        *,
        tone: str = "info",
        auto_hide_ms: int = 2600,
    ) -> None:
        label = getattr(self, "_panel_status_label", None)
        message = str(text or "").strip()
        if isinstance(label, QLabel):
            label.hide()
        if not message:
            toast_label = getattr(self, "_panel_toast_label", None)
            if isinstance(toast_label, QLabel):
                toast_label.hide()
            return
        toast = getattr(self, "_on_toast", None)
        title = {
            "success": "操作成功",
            "error": "操作失败",
            "warning": "提示",
            "info": "提示",
        }.get(str(tone or "info"), "提示")
        if callable(toast):
            try:
                toast(title, message, int(auto_hide_ms or 2600))
                return
            except Exception:
                pass
        self._show_panel_floating_toast(message, tone=tone, auto_hide_ms=auto_hide_ms)

    def _show_panel_floating_toast(
        self,
        text: str,
        *,
        tone: str = "info",
        auto_hide_ms: int = 2600,
    ) -> None:
        message = str(text or "").strip()
        if not message:
            return
        label = getattr(self, "_panel_toast_label", None)
        if not isinstance(label, QLabel):
            label = QLabel(self)
            label.setObjectName("OcrPanelToast")
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._panel_toast_label = label
        palette = {
            "success": ("#ffffff", "rgba(22, 101, 52, 232)", "rgba(22, 101, 52, 245)"),
            "error": ("#ffffff", "rgba(153, 27, 27, 235)", "rgba(153, 27, 27, 245)"),
            "warning": ("#ffffff", "rgba(146, 64, 14, 235)", "rgba(146, 64, 14, 245)"),
            "info": ("#ffffff", "rgba(51, 65, 85, 232)", "rgba(51, 65, 85, 245)"),
        }
        color, background, border = palette.get(str(tone or "info"), palette["info"])
        token = int(label.property("statusToken") or 0) + 1
        label.setProperty("statusToken", token)
        label.setMinimumWidth(0)
        label.setMaximumWidth(16777215)
        label.setText(message)
        label.setStyleSheet(
            "QLabel#OcrPanelToast {"
            f" color: {color}; background: {background}; border: 1px solid {border};"
            " border-radius: 9px; padding: 8px 14px; font-size: 12px; font-weight: 700;"
            "}"
        )
        label.adjustSize()
        max_width = max(240, min(520, self.width() - 48))
        if label.width() > max_width:
            label.setFixedWidth(max_width)
            label.adjustSize()
        x = max(12, int((self.width() - label.width()) / 2))
        y = max(12, int(getattr(self, "_title_bar_shell", self).height()) + 8)
        label.move(x, y)
        label.raise_()
        label.show()
        if int(auto_hide_ms or 0) > 0:
            def hide_if_current() -> None:
                if not isinstance(label, QLabel):
                    return
                if int(label.property("statusToken") or 0) != token:
                    return
                label.hide()

            QTimer.singleShot(int(auto_hide_ms), hide_if_current)

    def _show_light_feedback(self, text: str, *, tone: str = "info", auto_hide_ms: int = 1800) -> None:
        message = str(text or "").strip()
        if not message:
            return
        toast = getattr(self, "_on_toast", None)
        if callable(toast):
            title = {
                "success": "操作成功",
                "error": "操作失败",
                "warning": "提示",
                "info": "提示",
            }.get(str(tone or "info"), "提示")
            try:
                toast(title, message, int(auto_hide_ms or 1800))
                return
            except Exception:
                pass
        self._show_panel_status(message, tone=tone, auto_hide_ms=auto_hide_ms)








































































    def _load_language_settings(self) -> None:
        try:
            settings = load_settings()
            translator = normalize_translator_settings((getattr(settings, "ui", {}) or {}).get("translator"))
            source_lang = str(translator.get("source_lang", "自动检测"))
            target_lang = str(translator.get("target_lang", "中英互译"))
            if target_lang in {"双向", "雙向"}:
                target_lang = "中英互译"
            if source_lang in [self._source_lang.itemText(i) for i in range(self._source_lang.count())]:
                self._source_lang.setCurrentText(source_lang)
            if target_lang in [self._target_lang.itemText(i) for i in range(self._target_lang.count())]:
                self._target_lang.setCurrentText(target_lang)
        except Exception:
            pass

    def _persist_language_settings(self) -> None:
        try:
            source_lang = self._source_lang.currentText()
            target_lang = self._target_lang.currentText()

            def _mut(s: AppSettings) -> AppSettings:
                ui = dict(getattr(s, "ui", {}) or {})
                translator = normalize_translator_settings(ui.get("translator"))
                translator["source_lang"] = source_lang
                translator["target_lang"] = target_lang
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
        except Exception:
            pass

    def _toggle_bottom_collapsed(self) -> None:
        try:
            collapsed = not self._btn_collapse._collapsed
            self._btn_collapse.set_collapsed(collapsed)

            show_val = not collapsed
            self._source_label.setVisible(show_val)
            self._source_lang.setVisible(show_val)
            self._target_label.setVisible(show_val)
            self._target_lang.setVisible(show_val)
            self._btn_translate.setVisible(show_val)
            self._hide_bottom_model_hover_label()

            self._persist_bottom_collapse_state(collapsed)
        except Exception:
            pass

    def _load_bottom_collapse_state(self) -> bool:
        try:
            settings = load_settings()
            return bool((getattr(settings, "ui", {}) or {}).get("bottom_collapsed", False))
        except Exception:
            return False

    def _persist_bottom_collapse_state(self, collapsed: bool) -> None:
        try:
            update_ui_settings(bottom_collapsed=bool(collapsed))
        except Exception:
            pass

    def _translator_runtime_config(self, purpose: str = "translate") -> tuple[dict[str, object], bool, str]:
        settings = load_settings()
        translator = normalize_translator_settings((getattr(settings, "ui", {}) or {}).get("translator"))
        if str(purpose) == "qa":
            current_model = str(translator.get("qa_model", "") or translator.get("current_model", "gemini-3.5-flash-thinking"))
        else:
            current_model = str(translator.get("translate_model", "") or translator.get("current_model", "Google翻译"))
        configs = dict(translator.get("model_configs") or {})
        cfg = dict(configs.get(current_model, {}) or {})
        cfg["model_type"] = infer_translator_model_type(current_model, cfg)
        cfg["display_name"] = current_model
        use_proxy = bool(cfg.get("use_proxy", False))
        proxy_url = str(translator.get("proxy_url", "socks5://127.0.0.1:1080"))
        return cfg, use_proxy, proxy_url

    def _translator_required_fields(self, cfg: dict[str, object]) -> list[str]:
        model_type = str(cfg.get("model_type", "glm") or "glm").lower()
        if model_type in {"microsoft_free", "google_free"}:
            return []
        if model_type == "deeplx":
            return ["base_url"]
        return ["base_url", "model_name", "api_key"]

    def _qa_required_fields(self, cfg: dict[str, object]) -> list[str]:
        model_type = str(cfg.get("model_type", "glm") or "glm").lower()
        if model_type in {"microsoft_free", "google_free", "deeplx"}:
            return ["__qa_model__"]
        return ["base_url", "model_name", "api_key"]

    def _swap_languages(self) -> None:
        src = self._source_lang.currentText()
        dst = self._target_lang.currentText()
        if src == "自动检测" or OcrTranslationWorker._is_bidirectional_target(dst):
            return
        self._source_lang.setCurrentText(dst)
        self._target_lang.setCurrentText(src)

    def _set_translation_message(
        self,
        text: str,
        *,
        markdown: bool = False,
        streaming: bool = False,
        model_name: Optional[str] = None,
        elapsed: Optional[float] = None,
        reply_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        start_time: Optional[str] = None,
    ) -> None:
        message_kwargs = {
            "is_markdown": bool(markdown),
            "model_name": model_name,
            "elapsed": elapsed,
            "reply_tokens": reply_tokens,
            "total_tokens": total_tokens,
            "start_time": start_time,
        }
        applied_stream = False
        if bool(streaming):
            apply_coalesced = getattr(self._bubble_view, "apply_coalesced_stream_last_ai", None)
            if callable(apply_coalesced):
                apply_coalesced(str(text or ""), **message_kwargs)
                applied_stream = True

        if not applied_stream:
            bubbles = list(getattr(self._bubble_view, "_bubbles", []) or [])
            last_ai = getattr(self._bubble_view, "_last_ai", None)
            can_update_single = bool(
                last_ai is not None
                and len(bubbles) == 1
                and getattr(last_ai, "role", lambda: "")() != "user"
            )
            if can_update_single:
                self._bubble_view.update_last_ai(
                    str(text or ""),
                    streaming=bool(streaming),
                    **message_kwargs,
                )
            else:
                self._bubble_view.show_single(
                    str(text or ""),
                    bool(markdown),
                    model_name=model_name,
                    elapsed=elapsed,
                    reply_tokens=reply_tokens,
                    total_tokens=total_tokens,
                    start_time=start_time,
                )

        self._bubble_view.show()
        sync_title_bar = getattr(self, "_sync_title_bar_visibility", None)
        if callable(sync_title_bar):
            sync_title_bar()
        if not bool(streaming):
            try:
                self._bubble_view.refresh_layout(keep_bottom=True)
            except Exception:
                pass
            self._reposition()
            self._bubble_view.scroll_to_bottom()

    def _show_translation_error(self, message: str) -> None:
        message = str(message)
        chat_history = getattr(self, "_chat_history", None)
        if bool(getattr(self, "_is_chatting", False)) and isinstance(chat_history, list) and chat_history:
            try:
                target_idx = OcrTextPanel._active_assistant_index(self)
                if target_idx < 0:
                    target_idx = len(chat_history) - 1
                error_msg = {
                    "role": "assistant",
                    "content": message,
                    "error_message": message,
                    "failed": True,
                }
                if 0 <= target_idx < len(chat_history) and chat_history[target_idx].get("role") == "assistant":
                    chat_history[target_idx].update(error_msg)
                else:
                    target_idx = len(chat_history)
                    chat_history.append(error_msg)
                if not OcrTextPanel._update_assistant_bubble_at(self, target_idx, message):
                    self._render_chat_history(is_streaming=False)
                self._active_assistant_msg_index = None
                return
            except Exception:
                pass
        self._set_translation_message(message)

    def _show_qa_error(self, message: str) -> None:
        message = str(message)
        chat_history = getattr(self, "_chat_history", None)
        if bool(getattr(self, "_is_chatting", False)) and isinstance(chat_history, list) and chat_history:
            try:
                target_idx = OcrTextPanel._active_assistant_index(self)
                if target_idx < 0:
                    target_idx = len(chat_history) - 1
                error_msg = {
                    "role": "assistant",
                    "content": message,
                    "error_message": message,
                    "failed": True,
                }
                if 0 <= target_idx < len(chat_history) and chat_history[target_idx].get("role") == "assistant":
                    chat_history[target_idx].update(error_msg)
                else:
                    target_idx = len(chat_history)
                    chat_history.append(error_msg)
                if not OcrTextPanel._update_assistant_bubble_at(self, target_idx, message):
                    self._render_chat_history(is_streaming=False)
                self._active_assistant_msg_index = None
                return
            except Exception:
                pass
        self._set_translation_message(message)

    def _translation_worker_is_running(self) -> bool:
        worker = getattr(self, "_translation_worker", None)
        if worker is None:
            return False
        try:
            return bool(worker.isRunning())
        except RuntimeError:
            return False
        except Exception:
            return False

    def _can_open_prompt_settings_popup_from_hover(self) -> bool:
        # 悬停只负责预览菜单；运行中按钮的“中止”语义必须只由真实点击触发。
        return not self._translation_worker_is_running()

    def _show_prompt_settings_popup(self) -> None:
        if self._translation_worker_is_running():
            self._abort_worker()
            return

        anchor = self._btn_prompt_settings
        try:
            self._prompt_settings_popup_hide_timer.stop()
        except Exception:
            pass
        popup = getattr(self, "_prompt_settings_popup", None)
        try:
            if popup is not None and popup.isVisible():
                return
        except RuntimeError:
            popup = None
            self._prompt_settings_popup = None
        popup = OcrGenericMenuPopup(
            self._prompt_action_menu_items(),
            parent=anchor,
            active_indicator="background",
        )
        popup.setFixedWidth(PROMPT_ACTION_POPUP_WIDTH)
        self._prompt_settings_popup = popup
        popup.destroyed.connect(lambda *_: setattr(self, "_prompt_settings_popup", None))
        popup.installEventFilter(self)

        btn_pos = anchor.mapToGlobal(QPoint(0, 0))
        popup_width = popup.width()
        popup_height = popup.height()

        x = btn_pos.x() + (anchor.width() - popup_width) // 2
        y = btn_pos.y() - popup_height - 8

        win_pos = self.mapToGlobal(QPoint(0, 0))
        win_w = self.width()
        max_x = win_pos.x() + win_w - popup_width - 8
        if x > max_x:
            x = max_x

        popup.show_at_pos(QPoint(x, y))
        self._schedule_prompt_settings_popup_hide(220)

    def _cursor_over_prompt_settings_popup_area(self) -> bool:
        for widget in (getattr(self, "_btn_prompt_settings", None), getattr(self, "_prompt_settings_popup", None)):
            if widget is None:
                continue
            try:
                if widget.isVisible() and widget.rect().contains(widget.mapFromGlobal(QCursor.pos())):
                    return True
            except RuntimeError:
                continue
            except Exception:
                pass
        return False

    def _schedule_prompt_settings_popup_hide(self, delay_ms: int = 180) -> None:
        try:
            self._prompt_settings_popup_hide_timer.start(max(0, int(delay_ms)))
        except Exception:
            pass

    def _hide_prompt_settings_popup_if_outside(self) -> None:
        popup = getattr(self, "_prompt_settings_popup", None)
        try:
            if popup is None or not popup.isVisible():
                self._prompt_settings_popup_hide_timer.stop()
                return
        except RuntimeError:
            self._prompt_settings_popup = None
            self._prompt_settings_popup_hide_timer.stop()
            return
        if self._cursor_over_prompt_settings_popup_area():
            self._schedule_prompt_settings_popup_hide(220)
            return
        try:
            if popup is not None:
                popup.close()
            self._prompt_settings_popup_hide_timer.stop()
        except RuntimeError:
            self._prompt_settings_popup = None
        except Exception:
            pass

    def _prompt_action_menu_items(self) -> list[tuple[str, Callable[[], None], bool]]:
        names = self._prompt_button_names()

        def schedule(action: str) -> Callable[[], None]:
            return lambda: QTimer.singleShot(0, lambda action=action: self._on_prompt_action_triggered(action))

        return [
            (names["reply"], schedule("reply"), True),
            (names["optimize"], schedule("optimize"), True),
            (names["explain"], schedule("explain"), True),
            (names["summarize"], schedule("summarize"), True),
        ]

    def _prompt_button_names(self) -> dict[str, str]:
        defaults = {
            "reply": DEFAULT_REPLY_PROMPT_BUTTON_NAME,
            "optimize": DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
            "explain": DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
            "summarize": DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
        }
        try:
            settings = load_settings()
            translator = normalize_translator_settings((getattr(settings, "ui", {}) or {}).get("translator"))
            return {
                "reply": str(translator.get("reply_prompt_button_name", defaults["reply"]) or defaults["reply"]),
                "optimize": str(translator.get("ai_search_prompt_button_name", defaults["optimize"]) or defaults["optimize"]),
                "explain": str(translator.get("explain_prompt_button_name", defaults["explain"]) or defaults["explain"]),
                "summarize": str(translator.get("summary_prompt_button_name", defaults["summarize"]) or defaults["summarize"]),
            }
        except Exception:
            return defaults

    def _on_prompt_action_triggered(self, action: str) -> None:
        if action == "reply":
            self.reply_prompt_current_text()
        elif action in {"optimize", "ai_search"}:
            self.ai_search_current_text()
        elif action == "explain":
            self.explain_current_text()
        elif action == "summarize":
            self.summarize_current_text()

    def _set_worker_buttons_busy(self, busy: bool, *, task: str = "translate") -> None:
        is_prompt_task = task in {"reply_prompt", "optimize_prompt", "ai_search", "explain_prompt", "summary_prompt", "explain", "summarize"}
        if bool(busy):
            self._btn_translate.setEnabled(task == "translate")
            self._btn_qa.setEnabled(task == "qa")
            self._btn_prompt_settings.setEnabled(is_prompt_task)

            self._btn_translate.setText("中止" if task == "translate" else "翻译")
            self._btn_qa.setText("中止" if task == "qa" else "问答")
            self._btn_prompt_settings.setText("中止" if is_prompt_task else "快捷")
        else:
            self._btn_translate.setEnabled(True)
            self._btn_qa.setEnabled(True)
            self._btn_prompt_settings.setEnabled(True)

            self._btn_translate.setText("翻译")
            self._btn_qa.setText("问答")
            self._btn_prompt_settings.setText("快捷")

    def _finish_preflight_prompt_failure(self, task: str = "qa") -> None:
        """启动 worker 前失败时统一收尾，避免思考卡片计时残留。"""
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
            self._set_worker_buttons_busy(False, task=str(task or "qa"))
        except Exception:
            pass
        self._translation_worker = None
























































































































    def _copy_finished_result(self, text: str, label: str) -> None:
        try:
            settings = load_settings()
            translator = normalize_translator_settings((getattr(settings, "ui", {}) or {}).get("translator"))
            if not bool(translator.get("auto_copy_answers", False)):
                return
        except Exception:
            return

        clean = self._clean_clipboard_text(str(text or ""))
        if not clean:
            return
        if not copy_markdown_to_clipboard(clean):
            return
        try:
            if self._on_toast is not None:
                self._on_toast("已复制到剪贴板", f"{str(label or '结果')}已自动复制", 1800)
        except Exception:
            pass

    def set_text_and_reposition(
        self,
        text: str,
        region: QRect,
        elapsed: float = 0.0,
        auto_ocr_translate: bool = False,
        force_initial_view: bool = False,
        ocr_input_auto_height: bool = False,
        input_origin: str = "manual",
        center_on_first_show: bool = False,
    ) -> None:
        # 拦截清洗单字符 "c" / "C"（由取词时延迟竞争漏键导致）
        original_text = str(text or "").strip()
        if original_text.lower() == "c":
            text = ""

        if self.isVisible() and self._text_selection_update_blocked():
            self._external_reuse_action_block_until = time.monotonic() + 0.25
            try:
                self.raise_()
            except Exception:
                pass
            return
        self._region = QRect(region)
        self._original_text = str(text or "")
        self._current_input_origin = "ocr" if str(input_origin or "").strip().lower() == "ocr" else "manual"
        self._center_on_first_show = bool(center_on_first_show)
        self._ocr_input_auto_height_cap = 800 if ocr_input_auto_height else 0
        if force_initial_view:
            self._reset_to_initial_input_view()
        self._loading_text = True
        try:
            self._editor.setPlainText(str(text))
        finally:
            self._loading_text = False
        self._bubble_view.clear()
        self._chat_history = []
        self._active_assistant_msg_index = None
        self._stream_render_signature = None
        self._current_session_record_id = None
        self._is_chatting = False
        self._bubble_view.hide()
        self._sync_output_quick_action_bar_visibility()

        self._bubble_view.setMinimumHeight(0)
        try:
            self._bubble_view.setMaximumHeight(16777215)
            self._editor_container.setMinimumHeight(0)
            self._editor_container.setMaximumHeight(16777215)
        except Exception:
            pass
        self._translation_info.clear()
        self._translation_info.hide()
        self._advance_window_mode_generation()
        self._is_maximized = False
        self._pending_native_maximize_restore = False
        self._maximized_work_area_geometry = QRect()
        self._set_maximized_property(False)

        # 快捷键/手动唤起 AI 对话会在窗口首次 show() 前直接完成完整历史侧栏布局。
        # 如果侧栏状态是打开的，同步在此完成历史侧栏加载与布局，从而避免窗口显示后再发生拉伸展开导致的闪烁。
        if not force_initial_view and bool(getattr(self, "_restore_history_sidebar_on_init", True)):
            if (
                not bool(getattr(self, "_history_only_mode", False))
                and not bool(getattr(self, "_history_showing", False))
                and getattr(self, "_history_sidebar", None) is not None
                and OcrTextPanel._load_history_sidebar_open_from_settings()
            ):
                self._show_history(persist_state=False)

        self._reposition()
        self._initial_panel_geometry = QRect(self.geometry())
        self._pre_max_geometry = QRect(self._initial_panel_geometry)
        try:
            self._pre_max_editor_height = int(self._editor_container.height() or self._compact_editor_fill_height(self.height()))
        except Exception:
            self._pre_max_editor_height = 0
        self._set_native_topmost(True)
        self.show()
        self.raise_()
        if ocr_input_auto_height:
            # 首次 show 后字体与换行宽度才稳定，再按 OCR 文本实际高度校准一次。
            QTimer.singleShot(0, self._reposition)
        self._activate_for_text_input(repeat=False)
        self._sync_input_watermark_visibility()
        if not original_text.strip():
            self._restore_chat_draft("new", show_feedback=False)
        self._sync_output_quick_action_bar_visibility()
        QTimer.singleShot(0, self._sync_output_quick_action_bar_visibility)
        self._schedule_transient_topmost_release(600)

        # 自动识别翻译逻辑
        try:
            import re
            settings = load_settings()
            translator = normalize_translator_settings((getattr(settings, "ui", {}) or {}).get("translator"))
            ocr_translate_enabled = bool(translator.get("ocr_translate_enabled", False))
            if ocr_translate_enabled and auto_ocr_translate:
                def should_auto_translate(t: str) -> bool:
                    if not t:
                        return False
                    cleaned = re.sub(r'[\s0-9，。！？；：（）“”‘’【】《》、—…\-.,!?()\[\]{}""\'\':;_+=\-*/\\&^%$#@~`|<>·]', '', t)
                    if not cleaned:
                        return False
                    chinese_chars = len(re.findall(r'[\u4e00-\u9fa5]', cleaned))
                    total_chars = len(cleaned)
                    non_chinese_chars = total_chars - chinese_chars
                    ratio = non_chinese_chars / total_chars
                    return ratio >= 0.5

                if should_auto_translate(self._original_text):
                    QTimer.singleShot(50, self._translate_text)
        except Exception:
            logger.exception("自动识别翻译失败")

    def set_input_text_preserving_answer(self, text: str, region: Optional[QRect] = None) -> None:
        original_text = str(text or "")
        if original_text.strip().lower() == "c":
            original_text = ""

        if self.isVisible() and self._text_selection_update_blocked():
            self._external_reuse_action_block_until = time.monotonic() + 0.25
            try:
                self.raise_()
            except Exception:
                pass
            return

        if region is not None:
            self._region = QRect(region)
        self._original_text = original_text
        self._current_input_origin = "manual"
        self._loading_text = True
        try:
            self._editor.setPlainText(original_text)
        finally:
            self._loading_text = False
        self._sync_input_watermark_visibility()
        self._activate_for_text_input(repeat=False)

    def _on_source_text_changed(self) -> None:
        if bool(getattr(self, "_loading_text", False)):
            return
        if (
            str(getattr(self, "_current_input_origin", "manual") or "manual") == "ocr"
            and not str(self._editor.toPlainText() or "").strip()
        ):
            self._current_input_origin = "manual"
        self._sync_pending_attachments_from_editor_text()
        if getattr(self, "_is_chatting", False):
            return
        if getattr(self, "_history_showing", False):
            self._ensure_history_output_area()
            return
        if bool(getattr(self, "_preserve_cleared_output_area", False)):
            try:
                self._bubble_view.clear()
                self._bubble_view.show()
                self._lock_editor_container_for_output_area()
                self._translation_info.clear()
                self._translation_info.hide()
                self._activate_panel_layouts()
                sync_title_bar = getattr(self, "_sync_title_bar_visibility", None)
                if callable(sync_title_bar):
                    sync_title_bar()
                self._sync_output_quick_action_bar_visibility()
            except Exception:
                pass
            return
        try:
            self._bubble_view.clear()
            self._bubble_view.hide()
            self._bubble_view.setMinimumHeight(0)
            self._translation_info.clear()
            self._translation_info.hide()
            self._sync_title_bar_visibility()
            self._sync_output_quick_action_bar_visibility()
        except Exception:
            pass






































    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        updated_last_pos = False
        updated_stable_bottom = False
        is_dragging = bool(getattr(self, "_dragging_window", False)) or bool(getattr(getattr(self, "_title_bar", None), "_dragging", False))
        if is_dragging:
            OcrTextPanel._last_pos = self.pos()
            OcrTextPanel._last_pos_user_moved = True
            updated_last_pos = True
        # 瞬间同步更新内存逻辑底端；但在 _reposition 主动 setGeometry 期间、或几何冻结期
        # （流式渲染/清除对话）跳过，避免被乱序上报或瞬时膨胀的几何污染底端锚定，从而消除抖动。
        if not getattr(self, "_repositioning", False) and not getattr(self, "_geometry_frozen", False):
            self._last_stable_bottom_y = self.frameGeometry().y() + self.frameGeometry().height()
            updated_stable_bottom = True
        _trace_ai_panel(
            self,
            "move_event",
            event_pos=event.pos(),
            event_old_pos=event.oldPos(),
            updated_last_pos=updated_last_pos,
            updated_stable_bottom=updated_stable_bottom,
            is_dragging=bool(getattr(self, "_dragging_window", False)),
            geometry_frozen=bool(getattr(self, "_geometry_frozen", False)),
        )

    def closeEvent(self, event) -> None:
        restore_main_window = getattr(self, "_restore_main_window_visibility_after_close", None)
        self._save_current_chat_draft(show_feedback=False)
        self._cancel_translation_worker_for_close()
        for worker in list(getattr(self, "_attachment_prepare_workers", set()) or set()):
            request_thread_cancel(worker, wait_ms=20)
        self._attachment_prepare_workers = set()
        image_worker = getattr(self, "_image_attachment_prepare_worker", None)
        if image_worker is not None:
            request_thread_cancel(image_worker, wait_ms=20)
            self._image_attachment_prepare_worker = None

        # 获取在热键触发时记录的外部前台窗口句柄（由 _trigger_ai_qa_hotkey 设置）
        # 仅当从外部程序切回时才有此值，用于关闭后将焦点还原给该外部程序
        prev_foreground_hwnd = getattr(self, "_prev_foreground_hwnd_before_open", 0)

        if callable(restore_main_window):
            try:
                restore_main_window()
            except Exception:
                pass
        self._save_last_pos_to_settings()
        super().closeEvent(event)
        if callable(restore_main_window):
            try:
                QTimer.singleShot(0, restore_main_window)
                QTimer.singleShot(80, restore_main_window)
            except Exception:
                pass

        # Windows 平台：关闭后将焦点还原给打开AI对话前的前台窗口，
        # 防止 Qt 默认的焦点转移行为将主窗口意外激活到桌面最前面
        if sys.platform == "win32" and prev_foreground_hwnd:
            try:
                import ctypes
                def _restore_prev_foreground() -> None:
                    try:
                        if ctypes.windll.user32.IsWindow(prev_foreground_hwnd):
                            ctypes.windll.user32.SetForegroundWindow(prev_foreground_hwnd)
                    except Exception:
                        pass
                QTimer.singleShot(50, _restore_prev_foreground)
                QTimer.singleShot(150, _restore_prev_foreground)
            except Exception:
                pass

    def keyPressEvent(self, event) -> None:
        if int(event.key()) == int(Qt.Key.Key_Escape):
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event) -> bool:
        t = event.type()
        if obj is getattr(self, "_prompt_settings_popup", None):
            if t == QEvent.Type.Enter:
                try:
                    self._prompt_settings_popup_hide_timer.stop()
                except Exception:
                    pass
            elif t in {QEvent.Type.Leave, QEvent.Type.Hide, QEvent.Type.Close}:
                self._schedule_prompt_settings_popup_hide()
            return super().eventFilter(obj, event)
        if obj in getattr(self, "_buttons", []):
            if t == QEvent.Type.Enter:
                if obj is getattr(self, "_btn_prompt_settings", None):
                    try:
                        self._smooth_tooltip.hide()
                    except Exception:
                        pass
                    try:
                        pos = obj.mapToGlobal(QPoint(int(obj.width() / 2), -6))
                        tooltip_text = str(obj.toolTip()).strip()
                        if tooltip_text:
                            self._smooth_tooltip.show_text(tooltip_text, pos, direction="above", padding=21)
                    except Exception:
                        pass
                    return super().eventFilter(obj, event)
                if obj is getattr(self, "_btn_translate", None):
                    self._show_bottom_model_hover_label("translate")
                    try:
                        self._smooth_tooltip.hide()
                    except Exception:
                        pass
                    return super().eventFilter(obj, event)
                if obj is getattr(self, "_btn_qa", None):
                    self._show_bottom_model_hover_label("qa")
                    try:
                        self._smooth_tooltip.hide()
                    except Exception:
                        pass
                    return super().eventFilter(obj, event)
                try:
                    pos = obj.mapToGlobal(QPoint(int(obj.width() / 2), -6))
                    tooltip_text = str(obj.toolTip()).strip()
                    if tooltip_text:
                        self._smooth_tooltip.show_text(tooltip_text, pos, direction="above", padding=21)
                except Exception:
                    pass
            elif t == QEvent.Type.Leave:
                try:
                    if obj.rect().contains(obj.mapFromGlobal(QCursor.pos())):
                        return False
                except Exception:
                    pass
                if obj is getattr(self, "_btn_prompt_settings", None):
                    pass
                if obj in {getattr(self, "_btn_translate", None), getattr(self, "_btn_qa", None)}:
                    self._hide_bottom_model_hover_label()
                try:
                    self._smooth_tooltip.hide()
                except Exception:
                    pass
            elif t == QEvent.Type.ToolTip:
                return True

        if obj in {getattr(self, "_floating_minimize_btn", None), getattr(self, "_floating_close_btn", None)}:
            if t in {QEvent.Type.Enter, QEvent.Type.MouseMove}:
                self._show_floating_window_buttons()
            elif t == QEvent.Type.Leave:
                single_shot_scoped(120, self, self._hide_floating_window_buttons_if_outside_input)

        if t in {QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride} and int(event.key()) == int(Qt.Key.Key_Escape):
            self.close()
            event.accept()
            return True
        if t == QEvent.Type.Resize and obj is self._editor:
            self._update_watermark_pos()
        if t == QEvent.Type.Resize and obj is self._bubble_view:
            self._update_translation_info_pos()
            refresh_surface = getattr(self, "_refresh_interaction_surface", None)
            if callable(refresh_surface):
                refresh_surface(defer=True)

        if obj in getattr(self, "_text_edit_event_sources", []):
            if t in {QEvent.Type.Enter, QEvent.Type.MouseMove}:
                self._show_floating_window_buttons()
            elif t == QEvent.Type.Leave:
                single_shot_scoped(120, self, self._hide_floating_window_buttons_if_outside_input)
            if t == QEvent.Type.KeyPress:
                try:
                    if int(event.key()) in {int(Qt.Key.Key_Return), int(Qt.Key.Key_Enter)}:
                        if obj is self._editor or obj is self._editor.viewport():
                            mods = event.modifiers()
                            if mods == Qt.KeyboardModifier.ControlModifier:
                                # 仅在按 Ctrl+Enter 时触发翻译
                                self._translate_text()
                                event.accept()
                                return True
                            elif mods == Qt.KeyboardModifier.NoModifier:
                                # 仅在单敲 Enter 时触发问答
                                self._answer_question()
                                event.accept()
                                return True
                except Exception:
                    pass
                try:
                    if int(event.key()) == int(Qt.Key.Key_A) and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                        self._mark_text_selection_interaction(duration=2.0)
                except Exception:
                    pass
            elif t == QEvent.Type.MouseButtonPress:
                try:
                    if event.button() == Qt.MouseButton.LeftButton:
                        self._mark_text_selection_interaction(duration=1.2)
                except Exception:
                    pass
            elif t == QEvent.Type.MouseMove:
                try:
                    if bool(event.buttons() & Qt.MouseButton.LeftButton):
                        self._mark_text_selection_interaction(duration=1.2)
                except Exception:
                    pass
            elif t == QEvent.Type.MouseButtonRelease:
                try:
                    if event.button() == Qt.MouseButton.LeftButton:
                        self._mark_text_selection_interaction(duration=2.0 if self._any_text_editor_has_selection() else 0.5)
                except Exception:
                    pass

        if obj in getattr(self, "_drag_event_sources", []):
            if t == QEvent.Type.MouseButtonPress and self._start_window_drag(event):
                return True
            if t == QEvent.Type.MouseMove and self._move_window_drag(event):
                return True
            if t == QEvent.Type.MouseButtonRelease and self._finish_window_drag(event, obj):
                return True
        if t in {QEvent.Type.Enter, QEvent.Type.Leave} and isinstance(obj, QPushButton):
            try:
                eff = obj.graphicsEffect()
                if isinstance(eff, QGraphicsDropShadowEffect):
                    if t == QEvent.Type.Enter:
                        eff.setBlurRadius(12)
                        eff.setOffset(0, 3)
                        eff.setColor(QColor(0, 0, 0, 72))
                    else:
                        eff.setBlurRadius(8)
                        eff.setOffset(0, 2)
                        eff.setColor(QColor(0, 0, 0, 52))
            except Exception:
                pass
        return super().eventFilter(obj, event)





















    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if bool(getattr(self, "_is_maximized", False)):
            self._sync_maximized_content_layout()
        else:
            self._sync_compact_editor_height(self.height())
        self._update_top_seam_cover()
        self._update_interaction_seam_covers()
        self._update_watermark_pos()
        self._update_translation_info_pos()
        self._position_floating_window_buttons()
        self._position_context_warning_bar()
        updated_stable_bottom = False
        # 瞬间同步更新内存逻辑底端；但在 _reposition 主动 setGeometry 期间、或几何冻结期
        # （流式渲染/清除对话）跳过，避免被乱序上报或瞬时膨胀的几何污染底端锚定，从而消除抖动。
        if not getattr(self, "_repositioning", False) and not getattr(self, "_geometry_frozen", False):
            self._last_stable_bottom_y = self.frameGeometry().y() + self.frameGeometry().height()
            updated_stable_bottom = True
        _trace_ai_panel(
            self,
            "resize_event",
            event_size=event.size(),
            event_old_size=event.oldSize(),
            updated_stable_bottom=updated_stable_bottom,
            geometry_frozen=bool(getattr(self, "_geometry_frozen", False)),
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._restore_title_bar_after_window_restore()
        self._schedule_title_bar_restore_check()
        self._sync_compact_editor_height(self.height())
        self._activate_panel_layouts()
        self._update_top_seam_cover()
        self._position_floating_window_buttons()
        self._position_context_warning_bar()
        self._set_native_topmost(True)
        self._schedule_transient_topmost_release(600)
        # 窗口显示后几何尺寸已稳定，更新记录初始几何尺寸，防范隐藏时获取 geometry() 被污染的情况
        if not getattr(self, "_is_maximized", False) and not getattr(self, "_panel_collapsed", False):
            self._initial_panel_geometry = QRect(self.geometry())
            self._pre_max_geometry = QRect(self._initial_panel_geometry)
            try:
                self._pre_max_editor_height = int(self._editor_container.height() or self._compact_editor_fill_height(self.height()))
            except Exception:
                self._pre_max_editor_height = 0
        _trace_ai_panel(self, "show_event")

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        try:
            if event.type() != QEvent.Type.WindowStateChange:
                return
            if self.isMinimized():
                # 记录最小化前的正常几何边界（仅当在正常显示区域时）
                if not self._preserve_native_maximized_geometry():
                    current_geo = self.geometry()
                    if current_geo.x() > -10000:
                        self._normal_geometry = current_geo
                # 移出屏幕以防范 Windows 连带渲染的幽灵死窗口 bug
                self.move(-20000, -20000)
                # 最小化时强行取消置顶，避免在 Windows 系统下激活其它程序时发生连带幽灵窗口问题
                self._set_native_topmost(False)
                return
            if bool(getattr(self, "_forcing_normal_window_state", False)):
                self._pending_native_maximize_restore = False
            elif (
                bool(getattr(self, "_is_maximized", False))
                and not bool(getattr(self, "_panel_collapsed", False))
            ):
                self._normal_geometry = None
                if self._has_effective_maximized_geometry():
                    self._pending_native_maximize_restore = False
                elif not bool(getattr(self, "_forcing_native_maximize", False)):
                    self._pending_native_maximize_restore = True
                    self._schedule_native_maximized_state_confirmation()
            # 从最小化恢复时，首先恢复几何尺寸到最小化前的位置，重新计算置顶状态
            elif getattr(self, "_normal_geometry", None) is not None:
                self.setGeometry(self._normal_geometry)
                self._normal_geometry = None
            self._restore_title_bar_after_window_restore()
            self._schedule_title_bar_restore_check()
            self._release_transient_topmost()
        except Exception:
            pass
