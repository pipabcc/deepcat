from __future__ import annotations

import logging
import re
from PyQt6.QtCore import Qt
from deepcat.settings_store import DEFAULT_EXPLAIN_PROMPT
from deepcat.utils.logger import get_logger
from PyQt6.QtCore import Qt
from PyQt6.QtCore import Qt


logger = get_logger()


_ai_history_debug_logger = get_logger(
    "deepcat.ai_history_debug",
    level=logging.WARNING,
    enable_console=False,
    enable_file=False,
)


_AI_GEOMETRY_TRACE_ENABLED = False


CHAT_INPUT_WATERMARK_TEXT = "随时聊点什么，按回车发送"


_AI_CHAT_DRAFTS_UI_KEY = "ai_chat_drafts"


_AI_CHAT_CONTEXT_WARNING_TOKENS = 100000


_AI_CHAT_CONTEXT_WARNING_MESSAGES = 20


_AI_CHAT_CONTEXT_WARNING_MESSAGES_TOKENS = 50000


_AI_CHAT_CONTEXT_RECENT_ROUNDS = 5


_AI_THINKING_CARD_TEMP_DISABLED = False


# 猫头呼吸动画在流式回答期间会显著拉高 CPU，占用收益不成比例，保持永久关闭。
_AI_DOTS_ANIMATION_ENABLED = False


PROMPT_ACTION_POPUP_WIDTH = 70


_MODEL_MENU_EXCLUDED_QA_MODELS = {"微软翻译", "Google翻译", "DeepLX"}


_ROUNDED_POPUP_MENU_MAX_HEIGHT = 450


_LOCAL_GEMINI_TEST_SUCCESS_COOLDOWN_SECONDS = 20.0


_LOCAL_GEMINI_TEST_FAILURE_COOLDOWN_SECONDS = 20.0


_STREAM_CITATION_MARKER_RE = re.compile(r"\ue200cite\ue202[^\ue201]+\ue201")


_STREAM_ENTITY_MARKER_RE = re.compile(r"\ue200entity\ue202.*?\ue201")


_STREAM_URL_MARKER_RE = re.compile(r"\ue200url\ue202.*?\ue201")


_STREAM_MANGLED_MARKER_RE = re.compile(r"■(?:cite|entity|url)☆.*?(?:↩|\u21a9)")


_STREAM_INCOMPLETE_MARKER_RE = re.compile(
    r"(?:"
    r"\ue200(?:cite|entity|url)\ue202[^\ue201]*|"
    r"\ue200[a-zA-Z0-9_\ue202]*|"
    r"■(?:cite|entity|url)☆[^↩\u21a9]*|"
    r"■[a-zA-Z0-9_☆]*|"
    r"\b(?:cite|entity|url)\b"
    r")$",
    re.IGNORECASE,
)


_STREAM_MARKER_REGEXES = (
    _STREAM_CITATION_MARKER_RE,
    _STREAM_ENTITY_MARKER_RE,
    _STREAM_URL_MARKER_RE,
    _STREAM_MANGLED_MARKER_RE,
)


QA_EXPLAIN_PROMPT_TEMPLATE = DEFAULT_EXPLAIN_PROMPT


_AI_CHAT_RECORD_ROLE = Qt.ItemDataRole.UserRole.value + 1


_AI_CHAT_BATCH_SELECTED_ROLE = Qt.ItemDataRole.UserRole.value + 2


_AI_CHAT_GROUP_ROLE = Qt.ItemDataRole.UserRole.value + 3
