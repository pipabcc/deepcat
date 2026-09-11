from __future__ import annotations

import logging

from PyQt6.QtCore import Qt

logger = logging.getLogger("deepcat.ui.settings_dialog")

FREE_TRANSLATOR_TYPES = {"microsoft_free", "google_free", "deeplx"}
PROTECTED_TRANSLATOR_MODEL_NAMES = {"Google翻译", "DeepLX", "自定义模型"}
QA_EXCLUDED_TRANSLATOR_MODEL_NAMES = {"微软翻译", "Google翻译", "DeepLX"}
TRANSLATOR_MODEL_SEARCH_ROLE = Qt.ItemDataRole(Qt.ItemDataRole.UserRole.value + 1)
TRANSLATOR_MODEL_CODEX_BOUND_ROLE = Qt.ItemDataRole(Qt.ItemDataRole.UserRole.value + 2)
TRANSLATOR_MODEL_HIGHLIGHT_COLOR_ROLE = Qt.ItemDataRole(Qt.ItemDataRole.UserRole.value + 3)
CODEX_CURRENT_MODEL_CHECK_COLOR = "#2563eb"
CLAUDE_CODE_CURRENT_MODEL_CHECK_COLOR = "#d97757"
CLAUDE_CODE_PROVIDER_NAME = "Claude Code"
