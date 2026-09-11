from __future__ import annotations
import copy
import logging
import threading
import time
import json
import math
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from PyQt6 import sip
from PyQt6.QtCore import QObject, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QBrush, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF, QShortcut
from PyQt6.QtWidgets import QApplication, QFileDialog, QAbstractButton, QPushButton, QHBoxLayout, QFrame, QWidget, QGraphicsDropShadowEffect, QLabel, QLineEdit, QMessageBox, QTextEdit, QVBoxLayout, QComboBox, QToolButton, QDialog, QMenu, QScrollArea, QSizePolicy, QToolTip, QStyle, QStyleOptionComboBox, QCheckBox, QListWidget, QListWidgetItem, QInputDialog, QStyledItemDelegate
from deepcat.settings_store import AppSettings, DEEPLX_INFO_ADDRESS, DEFAULT_EXPLAIN_PROMPT, DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME, DEFAULT_OPTIMIZE_PROMPT, DEFAULT_REPLY_PROMPT, DEFAULT_REPLY_PROMPT_BUTTON_NAME, DEFAULT_SUMMARY_PROMPT, DEFAULT_SUMMARY_PROMPT_BUTTON_NAME, DEFAULT_AI_SEARCH_PROMPT, DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME, infer_translator_model_provider, infer_translator_model_type, load_settings, normalize_anthropic_messages_url, normalize_openai_chat_base_url, normalize_openai_images_url, normalize_openai_responses_url, normalize_translator_provider, normalize_translator_settings, save_settings, update_settings, update_ui_settings
from deepcat.translation_history_store import TranslationHistoryStore
from deepcat.ui.app_icon import create_app_icon
from deepcat.ui.chat_bubbles import BubbleListView
from deepcat.ui.clipboard_formats import clean_clipboard_text, copy_markdown_to_clipboard, copy_plain_text_to_clipboard
from deepcat.ui.popup_behavior import (
    POPUP_EXACT_WIDTH_PROPERTY,
    find_parent_with_attr,
    is_global_tooltip_disabled,
    mark_anchor_popup_closed,
    set_disable_global_tooltip,
    should_skip_anchor_popup,
)
from deepcat.ui.tab_list_popup import RoundedListPopup
from deepcat.utils.logger import get_log_dir, get_logger
from deepcat.utils.ocr_runtime import configure_ocr_dll_search_paths
from PyQt6.QtWidgets import QFrame, QPushButton, QStyle, QStyleOptionButton
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame
from PyQt6.QtGui import QPainter, QPixmap, QIcon, QPen, QColor, QFont
from PyQt6.QtCore import QByteArray, QPointF, QRectF, QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtSvg import QSvgRenderer
from deepcat.ui.post_capture_actions._shared import (
    CHAT_INPUT_WATERMARK_TEXT,
    PROMPT_ACTION_POPUP_WIDTH,
    QA_EXPLAIN_PROMPT_TEMPLATE,
    _AI_CHAT_BATCH_SELECTED_ROLE,
    _AI_CHAT_CONTEXT_RECENT_ROUNDS,
    _AI_CHAT_CONTEXT_WARNING_MESSAGES,
    _AI_CHAT_CONTEXT_WARNING_MESSAGES_TOKENS,
    _AI_CHAT_CONTEXT_WARNING_TOKENS,
    _AI_CHAT_DRAFTS_UI_KEY,
    _AI_CHAT_GROUP_ROLE,
    _AI_CHAT_RECORD_ROLE,
    _AI_DOTS_ANIMATION_ENABLED,
    _AI_GEOMETRY_TRACE_ENABLED,
    _AI_THINKING_CARD_TEMP_DISABLED,
    _LOCAL_GEMINI_TEST_FAILURE_COOLDOWN_SECONDS,
    _LOCAL_GEMINI_TEST_SUCCESS_COOLDOWN_SECONDS,
    _MODEL_MENU_EXCLUDED_QA_MODELS,
    _ROUNDED_POPUP_MENU_MAX_HEIGHT,
    _STREAM_CITATION_MARKER_RE,
    _STREAM_ENTITY_MARKER_RE,
    _STREAM_INCOMPLETE_MARKER_RE,
    _STREAM_MANGLED_MARKER_RE,
    _STREAM_MARKER_REGEXES,
    _STREAM_URL_MARKER_RE,
    _ai_history_debug_logger,
    logger,
)
from deepcat.ui.post_capture_actions.helpers import (
    _ai_geometry_logger,
    _get_ai_geometry_logger,
    _group_translator_model_menu_items,
    _history_text_has_image,
    _log_ai_history_debug,
    _normalize_ai_log_value,
    _point_to_log_value,
    _preview_log_text,
    _rect_to_log_value,
    _size_to_log_value,
    _style_rounded_popup_menu,
    _trace_ai_panel,
    _translator_model_search_text,
    classify_ocr_error_message,
    wrap_error_message,
)
from deepcat.ui.post_capture_actions.model_menus import (
    GroupBatchTestButton,
    ModelClearButton,
    ModelDeleteButton,
    ModelTestButton,
    OcrGenericMenuPopup,
    _OcrModelMenuPopup,
    _local_gemini_test_next_at,
)
from deepcat.ui.post_capture_actions.combos import CollapseArrowButton, ModernMouseClickOnlyComboBox, ModernPopupComboBox, UpwardComboBox
from deepcat.ui.post_capture_actions.tooltips import RedDotLabel, SmoothToolTip, SmoothToolTipController, install_smooth_tooltips
from deepcat.ui.post_capture_actions.workers import (
    FileAttachmentPrepareWorker,
    ImageAttachmentPrepareWorker,
    ImaNoteSearchWorker,
    OcrExtractWorker,
    OcrTranslationWorker,
)
from deepcat.ui.post_capture_actions.annotation import AnnotationCanvasOverlay
from deepcat.ui.post_capture_actions.selection_popups import SelectionActionPopup, SelectionHoverTranslationPopup
from deepcat.ui.post_capture_actions.text_widgets import ExpandIconButton, PremiumTextEdit, RoundedTextEditContainer, _ThinkingCard
from deepcat.ui.post_capture_actions.title_bar import OcrTitleBar, _OcrTitleBarButton, _load_title_owl_pixmap
from deepcat.ui.post_capture_actions.quick_actions import HoverIconButton, OutputQuickActionBar, PromptSettingsPopup
from deepcat.ui.post_capture_actions.ai_history import (
    AIChatHistoryFilterButton,
    AIChatHistoryItemDelegate,
    AIChatHistorySidebar,
    AIChatNewButton,
    _ai_history_search_terms,
    _first_search_match,
)
from deepcat.ui.post_capture_actions.attachments import _AttachmentPreviewChip, _refresh_attachment_preview_bar_if_available
from deepcat.ui.post_capture_actions.note_integration import GroupedSmoothNoteIntegrationDialog, SmoothNoteIntegrationDialog
from deepcat.ui.post_capture_actions.text_panel import OcrTextPanel
from deepcat.ui.post_capture_actions.actions import PostCaptureActions, _ACTIVE_OCR_WORKERS
