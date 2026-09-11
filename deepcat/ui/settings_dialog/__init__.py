from __future__ import annotations

import ctypes
import json
import logging
import os
import time
from pathlib import Path
from types import FunctionType
from typing import Callable, Optional

from PyQt6.QtCore import QByteArray, QEvent, QRect, Qt, QThread, QTimer, pyqtSignal, QPoint, QSize, QUrl
from PyQt6.QtGui import QBrush, QColor, QCursor, QDesktopServices, QFont, QGuiApplication, QIcon, QKeySequence, QPainter, QPen, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QProgressBar,
    QGraphicsDropShadowEffect,
    QStyle,
)

from deepcat import __version__
from deepcat.input.hotkey_format import pynput_to_qt, qt_to_pynput
from deepcat.utils.codex_config import (
    apply_codex_config,
    has_codex_client_process,
    launch_codex_client,
    restart_codex_client,
    stop_codex_client,
)
from deepcat.utils.claude_code_config import apply_claude_code_config, restore_claude_code_config
from deepcat.settings_store import (
    AppSettings,
    DEEPLX_INFO_ADDRESS,
    DEFAULT_EXPLAIN_PROMPT,
    DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
    DEFAULT_OPTIMIZE_PROMPT,
    DEFAULT_AI_SEARCH_PROMPT,
    DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
    DEFAULT_REPLY_PROMPT,
    DEFAULT_REPLY_PROMPT_BUTTON_NAME,
    DEFAULT_SUMMARY_PROMPT,
    DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
    PROMPT_BUTTON_NAME_MAX_LENGTH,
    default_translator_settings,
    decode_qbytearray,
    encode_qbytearray,
    infer_translator_model_type,
    infer_translator_model_provider,
    normalize_anthropic_messages_url,
    normalize_openai_chat_base_url,
    normalize_openai_responses_url,
    normalize_annotation_style,
    normalize_log_settings,
    normalize_output_dir,
    normalize_translator_provider,
    normalize_translator_settings,
    normalize_updater_settings,
    update_settings,
    update_settings_fields,
    update_ui_settings,
    validate_output_dir,
)
from deepcat.utils.autostart import is_autostart_enabled, set_autostart
from deepcat.utils.logger import get_log_dir


logger = logging.getLogger(__name__)
from deepcat.ui.post_capture_actions import ModernPopupComboBox, _OcrModelMenuPopup, OcrGenericMenuPopup, PROMPT_ACTION_POPUP_WIDTH, _translator_model_search_text

from deepcat.ui.settings_dialog._shared import (
    FREE_TRANSLATOR_TYPES,
    PROTECTED_TRANSLATOR_MODEL_NAMES,
    QA_EXCLUDED_TRANSLATOR_MODEL_NAMES,
    TRANSLATOR_MODEL_SEARCH_ROLE,
    TRANSLATOR_MODEL_CODEX_BOUND_ROLE,
    TRANSLATOR_MODEL_HIGHLIGHT_COLOR_ROLE,
    CODEX_CURRENT_MODEL_CHECK_COLOR,
    CLAUDE_CODE_CURRENT_MODEL_CHECK_COLOR,
    CLAUDE_CODE_PROVIDER_NAME,
)
from deepcat.ui.settings_dialog.delegates import DeletableModelItemDelegate, ProviderSwitchHintDelegate
from deepcat.ui.settings_dialog.workers import FetchModelsWorker, ProxyConnectionTestWorker, TranslatorConnectionTestWorker, UpdateCheckWorker, UpdateDownloadWorker, ModelDownloadWorker
from deepcat.ui.settings_dialog.field_widgets import PasteAwareLineEdit, PasteAwareComboBox, TranslatorModelComboBox, ApiKeyVisibilityButton, ProxyTestButton, _FetchedModelIdPopup
from deepcat.ui.settings_dialog.dialog import SettingsDialog
from deepcat.ui.settings_dialog.sub_dialogs import _AddProviderDialog, _AddModelIdDialog, PromptEditDialog, PromptSettingsEditPopup
