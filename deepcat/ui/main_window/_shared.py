from __future__ import annotations

import re


_NOTE_AUTO_LINK_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_@])((?:https?://|ftp://|file://|mailto:|www\.)[^\s<>'\"]+)"
)


_NOTE_AUTO_LINK_TRAILING_CHARS = ".,;:!?)]}，。！？、；：）】》"


_RESOURCE_SHORTCUT_MAX_ITEMS = 50


MAIN_WINDOW_BACKGROUND = "#f4faff"


MAIN_WINDOW_BACKGROUND_COLORREF = 0x00FFFAF4


MODULE_BACKGROUND = "#fdfeff"
