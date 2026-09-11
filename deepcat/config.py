from __future__ import annotations


class Config:
    SCROLL_AMOUNT = -24
    SCROLL_DELAY = 0.03
    START_DELAY = 3
    MAX_FRAMES = 0
    SCROLL_METHOD = "mouse_wheel"

    CAPTURE_REGION = None

    MATCH_STRIP_HEIGHT = 100
    MATCH_CONFIDENCE = 0.95

    BOTTOM_DIFF_MEAN_THRESHOLD = 1.0
    BOTTOM_CONFIRM_COUNT = 2

    OUTPUT_FORMAT = "png"
    JPG_QUALITY = 95
    OUTPUT_DIR = None
    AUTO_COPY_CLIPBOARD = True

    DETECT_FIXED_HEADER = True
    DETECT_FIXED_FOOTER = True

    STOP_KEY = "esc"
