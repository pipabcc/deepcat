"""划词问答回答区的圆角气泡控件。

替代原先“单个 QTextEdit + setHtml”的渲染方式：每条消息是一个独立的圆角 QFrame
（真圆角来自 QFrame 的 QSS —— QSS 由 QStyle 绘制，不受富文本引擎不支持 border-radius
的限制），内含一个富文本 QLabel 显示 Markdown 渲染结果。多条气泡垂直排布在一个
QScrollArea 里，user 右对齐淡蓝、assistant 左对齐白色，形成微信式对话气泡。
"""

from __future__ import annotations

import base64
import hashlib
import html as _html
import logging
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QMimeData, QPoint, QPointF, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QCursor, QDesktopServices, QDrag, QFont, QGuiApplication, QIcon, QImage, QLinearGradient, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QTextCursor
from PyQt6.QtWidgets import (
    QFileDialog,
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from deepcat.ui.markdown_renderer import MarkdownRenderer
from deepcat.ui.markdown_images import has_markdown_image, iter_markdown_images
from deepcat.ui.chat_image_loader import ChatImageLoader, decode_thumbnail
from deepcat.ui.popup_behavior import set_disable_global_tooltip
from deepcat.ui.timer_scope import single_shot_scoped
from deepcat.utils.logger import get_log_dir, get_logger

logger = get_logger(
    "deepcat.ai_history_debug",
    level=logging.WARNING,
    enable_console=False,
    enable_file=False,
)


@dataclass
class _ConversationNavItem:
    question: str
    answer: str
    target_y: int
    marker_y: int = 0
    time_str: str = ""


@dataclass
class _StreamRenderPerf:
    window_started_at: float
    delta_events: int = 0
    ui_flushes: int = 0
    markdown_renders: int = 0
    markdown_ms: float = 0.0
    code_updates: int = 0
    code_update_ms: float = 0.0
    layout_flushes: int = 0
    scroll_to_bottom_calls: int = 0
    paint_events: int = 0

    def reset(self, now: float) -> None:
        self.window_started_at = float(now)
        self.delta_events = 0
        self.ui_flushes = 0
        self.markdown_renders = 0
        self.markdown_ms = 0.0
        self.code_updates = 0
        self.code_update_ms = 0.0
        self.layout_flushes = 0
        self.scroll_to_bottom_calls = 0
        self.paint_events = 0

_PLAIN_SPAN = (
    "font-family:'Microsoft YaHei','Segoe UI',system-ui; font-size:14px;"
    " line-height:160%; color:#1c2438;"
)
_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_ICON_CACHE: dict[str, QIcon] = {}
_CODE_VIEW_COLLAPSED_HEIGHT = 160
_CHAT_CARD_DRAG_MIME = "application/x-deepcat-chat-card-id"
_LONG_ANSWER_COLLAPSE_CHARS = 12000
_LONG_ANSWER_PREVIEW_CHARS = 7000
_IMAGE_PREVIEW_LINK_RE = re.compile(r"📎\s*\[图片:\s*([^\]]+)\]\((deepcat-image-preview:[^)\s]+)\)")
_IMAGE_PREVIEW_LINK_STYLE = (
    "color:#2563eb; text-decoration:none;"
)
_FOLLOW_UP_LINK_PREFIX = "deepcat-follow-up:"
_FOLLOW_UP_LINK_STYLE = (
    "color:#2563eb; text-decoration:none; font-weight:600;"
)
_FOLLOW_UP_LINE_RE = re.compile(
    r"^(?P<indent>\s*)"
    r"(?P<marker>(?:[-*+]\s+|\d{1,3}[.)、]\s+)?)"
    r"(?P<label>[^：:\n]{2,180}?)"
    r"(?P<sep>[：:])"
    r"(?P<query>\s*\S.*)$"
)
_FOLLOW_UP_LABEL_PREFIXES = (
    "想深入了解",
    "深入了解",
    "详细了解",
    "进一步了解",
    "继续了解",
    "想了解",
    "了解",
    "看一看",
    "看看",
    "查看",
    "详细分析",
    "详细介绍",
    "展开了解",
    "追问",
    "聊聊",
)


def _cached_icon(name: str) -> QIcon:
    """按文件名缓存 QIcon，避免每个气泡构造时重复做磁盘 stat 与图标实例化。"""
    icon = _ICON_CACHE.get(name)
    if icon is None:
        path = _ASSETS_DIR / name
        icon = QIcon(str(path)) if path.exists() else QIcon()
        _ICON_CACHE[name] = icon
    return icon


def _verbose_log_enabled() -> bool:
    return logger.isEnabledFor(logging.INFO)


def _preview_log_text(value: object, limit: int = 160) -> str:
    text = str(value or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


_CJK_TOKEN_RE = re.compile(r'[\u4e00-\u9fa5\u3000-\u303f\uff00-\uffef]')
_ENG_TOKEN_RE = re.compile(r'[a-zA-Z0-9]+')


def _estimate_tokens(text: str) -> int:
    text = str(text or "")
    if not text.strip():
        return 0
    cnh = len(_CJK_TOKEN_RE.findall(text))
    eng_words = len(_ENG_TOKEN_RE.findall(text))
    others = len(text) - cnh - (eng_words * 3)
    if others < 0:
        others = 0
    tokens = cnh * 1.5 + eng_words * 1.3 + others * 0.3
    return max(1, int(tokens))


def _record_stream_perf(widget: Optional[QWidget], metric: str, *, elapsed_ms: float = 0.0, count: int = 1) -> None:
    parent = widget
    while parent is not None:
        recorder = getattr(parent, "_record_stream_perf", None)
        if callable(recorder):
            try:
                recorder(metric, elapsed_ms=elapsed_ms, count=count)
            except Exception:
                pass
            return
        try:
            parent = parent.parentWidget()
        except Exception:
            return


def _escape_plain_fragment(text: str) -> str:
    return _html.escape(str(text or ""), quote=False).replace("\n", "<br>")


def _render_plain_text_with_preview_links(text: str) -> str:
    """纯文本气泡仍按字面显示，仅把内部图片预览链接转为可点击入口。"""
    raw_text = str(text or "")
    parts: list[str] = []
    last_end = 0
    for match in _IMAGE_PREVIEW_LINK_RE.finditer(raw_text):
        parts.append(_escape_plain_fragment(raw_text[last_end:match.start()]))
        label = _escape_plain_fragment(match.group(1).strip() or "图片")
        href = _html.escape(match.group(2), quote=True)
        parts.append(f'📎 <a href="{href}" style="{_IMAGE_PREVIEW_LINK_STYLE}">图片: {label}</a>')
        last_end = match.end()
    parts.append(_escape_plain_fragment(raw_text[last_end:]))
    return "".join(parts)


class SelectableAutoScrollLabel(QLabel):
    """支持在鼠标划选文本时，如果鼠标超出所属 QScrollArea 的视口，能自动滚动的富文本 QLabel。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.timeout.connect(self._do_auto_scroll)
        self._scroll_speed = 0
        self._scroll_area: Optional[QScrollArea] = None
        self._last_global_pos = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._scroll_area = self._find_scroll_area()
            self._last_global_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        if self._scroll_area and event.buttons() & Qt.MouseButton.LeftButton:
            self._last_global_pos = event.globalPosition().toPoint()
            self._check_auto_scroll()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._stop_auto_scroll()
        super().mouseReleaseEvent(event)

    def _find_scroll_area(self) -> Optional[QScrollArea]:
        p = self.parentWidget()
        while p is not None:
            if isinstance(p, QScrollArea):
                return p
            p = p.parentWidget()
        return None

    def _check_auto_scroll(self) -> None:
        if not self._scroll_area or not self._last_global_pos:
            return

        viewport = self._scroll_area.viewport()
        if viewport is None:
            return

        local_pos = viewport.mapFromGlobal(self._last_global_pos)

        margin = 15  # 触发滚动的边缘感应距离（像素）
        vy = local_pos.y()
        vh = viewport.height()

        speed = 0
        if vy < margin:
            diff = margin - vy
            speed = -int(max(2, diff // 2))
        elif vy > vh - margin:
            diff = vy - (vh - margin)
            speed = int(max(2, diff // 2))

        if speed != 0:
            self._scroll_speed = max(-25, min(25, speed))
            if not self._scroll_timer.isActive():
                self._scroll_timer.start(30)  # 每 30ms 滚动并更新一次
        else:
            self._stop_auto_scroll()

    def _do_auto_scroll(self) -> None:
        if not self._scroll_area or self._scroll_speed == 0:
            self._stop_auto_scroll()
            return

        bar = self._scroll_area.verticalScrollBar()
        if bar:
            old_val = bar.value()
            new_val = old_val + self._scroll_speed
            bar.setValue(max(bar.minimum(), min(bar.maximum(), new_val)))

            # 如果实际滚动了，就向 QLabel 模拟发送 mouseMoveEvent，使文本划选能跟随滚动更新选区
            if bar.value() != old_val and self._last_global_pos:
                local_pos = self.mapFromGlobal(self._last_global_pos)
                evt = QMouseEvent(
                    QEvent.Type.MouseMove,
                    QPointF(local_pos),
                    QPointF(self._last_global_pos),
                    Qt.MouseButton.NoButton,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier
                )
                super().mouseMoveEvent(evt)

        self._check_auto_scroll()

    def _stop_auto_scroll(self) -> None:
        self._scroll_timer.stop()
        self._scroll_speed = 0


class CodeBlockWidget(QFrame):
    """真实 Qt 代码块：折叠时内部滚动，展开时按代码全文高度显示。"""

    copy_requested = pyqtSignal(str)
    download_requested = pyqtSignal(str, str)
    preview_requested = pyqtSignal(str)
    height_changed = pyqtSignal()

    def __init__(
        self,
        code_text: str,
        lang: str = "",
        *,
        streaming: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._code_text = str(code_text or "")
        self._lang = str(lang or "").strip()
        self._streaming = bool(streaming)
        self._expanded = False
        self._tooltips_ready = False
        self._hovered_tooltip_button: Optional[QToolButton] = None
        self._smooth_tooltip = None
        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.setSingleShot(True)
        self._tooltip_timer.timeout.connect(self._show_pending_tooltip)

        self.setObjectName("CodeBlockWidget")
        self.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed))
        self.setStyleSheet(
            "QFrame#CodeBlockWidget {"
            "  background:#f8fafc;"
            "  border:1px solid #e2e8f0;"
            "  border-radius:8px;"
            "}"
            "QFrame#CodeBlockHeader {"
            "  background:#f1f5f9;"
            "  border:none;"
            "  border-bottom:1px solid #e2e8f0;"
            "  border-top-left-radius:8px;"
            "  border-top-right-radius:8px;"
            "}"
            "QToolButton {"
            "  border:none;"
            "  background:transparent;"
            "  border-radius:4px;"
            "  padding:2px;"
            "}"
            "QToolButton:hover {"
            "  background:rgba(15, 23, 42, 0.08);"
            "}"
            "QPlainTextEdit {"
            "  background:#f8fafc;"
            "  color:#0f172a;"
            "  border:none;"
            "  selection-background-color:#bfdbfe;"
            "  selection-color:#0f172a;"
            "}"
            "QScrollBar:vertical { background:transparent; width:6px; margin:0px; }"
            "QScrollBar::handle:vertical { background:#cbd5e1; border-radius:3px; min-height:24px; }"
            "QScrollBar::handle:vertical:hover { background:#94a3b8; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0px; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }"
            "QScrollBar:horizontal { background:transparent; height:6px; margin:0px; }"
            "QScrollBar::handle:horizontal { background:#cbd5e1; border-radius:3px; min-width:24px; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0px; }"
            "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background:transparent; }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame(self)
        header.setObjectName("CodeBlockHeader")
        header.setFixedHeight(34)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(12, 0, 8, 0)
        header_lay.setSpacing(4)

        self._title_label = QLabel(self._lang_display(), header)
        self._title_label.setStyleSheet(
            "QLabel { color:#0f172a; font-size:13px; font-weight:600;"
            " font-family:'Microsoft YaHei','Segoe UI',system-ui; background:transparent; }"
        )
        self._title_label.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred))
        header_lay.addWidget(self._title_label, 1)

        self._copy_btn = self._make_icon_button("icon_action_copy.svg", "复制代码", self._copy_code)
        self._save_btn = self._make_icon_button("icon_action_save.svg", "保存代码", self._download_code)
        self._preview_btn = self._make_icon_button("icon_action_play.svg", "预览 HTML", self._preview_code)
        self._expand_btn = QToolButton(header)
        self._expand_btn.setFixedSize(24, 24)
        self._expand_btn.setToolTip("")
        self._expand_btn.setAccessibleName("展开代码")
        self._expand_btn.setProperty("smoothTooltip", "展开代码")
        set_disable_global_tooltip(self._expand_btn)
        self._expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._expand_btn.clicked.connect(self._toggle_expanded)
        self._expand_btn.installEventFilter(self)

        for button in (self._copy_btn, self._save_btn, self._preview_btn, self._expand_btn):
            header_lay.addWidget(button, 0)

        outer.addWidget(header)

        self._editor = QPlainTextEdit(self)
        self._editor.setPlainText(self._code_text)
        self._editor.setReadOnly(True)
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._editor.setTabStopDistance(32)
        self._editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._editor.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if self._streaming
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._editor.setFrameShape(QFrame.Shape.NoFrame)
        self._editor.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        code_font = QFont("Consolas", 10)
        code_font.setStyleHint(QFont.StyleHint.Monospace)
        self._editor.setFont(code_font)
        try:
            from deepcat.ui.settings_dialog import SettingsDialog
            SettingsDialog._install_custom_text_context_menus(self, self._editor)
        except Exception:
            pass
        outer.addWidget(self._editor)

        self._update_header_buttons()
        self._apply_height()
        single_shot_scoped(650, self, self._enable_tooltips)

    def _lang_display(self) -> str:
        lang = self._lang.strip()
        if not lang:
            return "Code"
        if lang.lower() in {"js", "ts", "css", "html", "sql", "xml", "json", "yaml", "yml"}:
            return lang.upper()
        return lang.capitalize()

    def _make_icon_button(self, icon_name: str, tooltip: str, slot) -> QToolButton:
        button = QToolButton(self)
        button.setFixedSize(24, 24)
        icon = _cached_icon(icon_name)
        if not icon.isNull():
            button.setIcon(icon)
        button.setToolTip("")
        button.setAccessibleName(tooltip)
        button.setProperty("smoothTooltip", tooltip)
        set_disable_global_tooltip(button)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        button.installEventFilter(self)
        return button

    def _full_code_height(self) -> int:
        line_count = max(1, self._editor.document().blockCount())
        metrics = self._editor.fontMetrics()
        return int(line_count * metrics.lineSpacing() + 18)

    def _needs_expand_button(self) -> bool:
        return bool(self._full_code_height() > _CODE_VIEW_COLLAPSED_HEIGHT + 4)

    def _update_header_buttons(self) -> None:
        self._copy_btn.setVisible(True)
        self._save_btn.setVisible(True)
        self._preview_btn.setVisible(self._lang_display().upper() == "HTML")
        self._expand_btn.setVisible(self._needs_expand_button())
        standard_icon = (
            QStyle.StandardPixmap.SP_ArrowUp
            if self._expanded
            else QStyle.StandardPixmap.SP_ArrowDown
        )
        self._expand_btn.setIcon(self.style().standardIcon(standard_icon))
        expand_tip = "收起代码" if self._expanded else "展开代码"
        self._expand_btn.setAccessibleName(expand_tip)
        self._expand_btn.setProperty("smoothTooltip", expand_tip)

    def _enable_tooltips(self) -> None:
        self._tooltips_ready = True

    def _hide_tooltip(self) -> None:
        self._tooltip_timer.stop()
        self._hovered_tooltip_button = None
        if self._smooth_tooltip is not None:
            self._smooth_tooltip.hide()

    def _show_pending_tooltip(self) -> None:
        button = self._hovered_tooltip_button
        if button is None or not button.isVisible() or not button.underMouse():
            return
        tip_text = str(button.property("smoothTooltip") or "").strip()
        if not tip_text:
            return
        try:
            if self._smooth_tooltip is None:
                from deepcat.ui.post_capture_actions import SmoothToolTip
                self._smooth_tooltip = SmoothToolTip()
            pos = button.mapToGlobal(QPoint(int(button.width() / 2), button.height() + 6))
            self._smooth_tooltip.show_text(tip_text, pos, direction="below")
        except Exception:
            pass

    def eventFilter(self, watched, event) -> bool:
        tooltip_buttons = (
            getattr(self, "_copy_btn", None),
            getattr(self, "_save_btn", None),
            getattr(self, "_preview_btn", None),
            getattr(self, "_expand_btn", None),
        )
        if watched in tooltip_buttons:
            event_type = event.type()
            if event_type == QEvent.Type.ToolTip:
                return True
            if event_type in {QEvent.Type.Enter, QEvent.Type.HoverEnter}:
                if self._tooltips_ready:
                    self._hovered_tooltip_button = watched
                    self._tooltip_timer.start(420)
            elif event_type in {
                QEvent.Type.Leave,
                QEvent.Type.HoverLeave,
                QEvent.Type.Hide,
                QEvent.Type.MouseButtonPress,
            }:
                self._hide_tooltip()
        return super().eventFilter(watched, event)

    def _apply_height(self) -> None:
        editor_height = self._full_code_height() if self._expanded else _CODE_VIEW_COLLAPSED_HEIGHT
        self._editor.setFixedHeight(max(32, int(editor_height)))
        self._editor.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if self._expanded
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._editor.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if self._streaming
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.setFixedHeight(34 + self._editor.height())
        self._update_header_buttons()
        self.updateGeometry()
        self.height_changed.emit()
        if not self._expanded:
            QTimer.singleShot(0, self._scroll_code_to_bottom)

    def update_code(self, code_text: str, lang: str = "", *, streaming: bool = False) -> None:
        code_text = str(code_text or "")
        lang = str(lang or "").strip()
        previous_code_text = self._code_text
        content_changed = code_text != previous_code_text
        lang_changed = lang != self._lang
        streaming_changed = bool(streaming) != self._streaming
        if not (content_changed or lang_changed or streaming_changed):
            return

        old_needs_expand = self._needs_expand_button()
        old_block_count = self._editor.document().blockCount()
        self._code_text = code_text
        self._lang = lang
        self._streaming = bool(streaming)
        if lang_changed:
            self._title_label.setText(self._lang_display())
        if content_changed:
            started_at = time.perf_counter()
            try:
                if bool(streaming) and code_text.startswith(previous_code_text):
                    delta = code_text[len(previous_code_text):]
                    if delta:
                        cursor = self._editor.textCursor()
                        cursor.movePosition(QTextCursor.MoveOperation.End)
                        cursor.insertText(delta)
                        self._editor.setTextCursor(cursor)
                else:
                    self._editor.setPlainText(self._code_text)
            finally:
                _record_stream_perf(
                    self,
                    "code_update_ms",
                    elapsed_ms=(time.perf_counter() - started_at) * 1000.0,
                )
        new_block_count = self._editor.document().blockCount()
        height_changed = (
            bool(streaming_changed)
            or old_block_count != new_block_count
            or old_needs_expand != self._needs_expand_button()
            or bool(self._expanded)
        )
        if height_changed:
            self._apply_height()
        elif lang_changed:
            self._update_header_buttons()

    def _toggle_expanded(self) -> None:
        if not self._needs_expand_button() and not self._expanded:
            return
        self._expanded = not self._expanded
        self._apply_height()

    def _scroll_code_to_bottom(self) -> None:
        bar = self._editor.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def _copy_code(self) -> None:
        self.copy_requested.emit(self._code_text)

    def _download_code(self) -> None:
        self.download_requested.emit(self._code_text, self._lang_display())

    def _preview_code(self) -> None:
        self.preview_requested.emit(self._code_text)


class ChatImageWidget(QFrame):
    """聊天气泡中的真实图片控件，避免 Qt 富文本图片渲染不稳定。"""

    THUMBNAIL_MAX_WIDTH = 340
    THUMBNAIL_MAX_HEIGHT = 380
    IMAGE_RADIUS = 10

    def __init__(self, image_source: str, alt: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._data_url = str(image_source or "")
        self._alt = str(alt or "generated image").strip() or "generated image"
        self._remote_image = self._data_url.lower().startswith(("http://", "https://"))
        self._loading_image = self._remote_image
        self._image_error = ""
        self._image_loader: ChatImageLoader | None = None
        self._mime_type, self._image_bytes = (
            ("image/png", b"") if self._remote_image else self._decode_image_source(self._data_url)
        )
        self._pixmap = QPixmap()
        self._display_pixmap = QPixmap()
        self.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed))
        if self._image_bytes:
            _mime, thumbnail = decode_thumbnail(self._image_bytes)
            self._pixmap = QPixmap.fromImage(thumbnail)

        self.setObjectName("ChatImageWidget")
        self.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed))
        self.setStyleSheet("QFrame#ChatImageWidget { background: transparent; border: none; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(0)

        self._label = QLabel(self)
        self._label.setObjectName("ChatImageLabel")
        self._label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._label.setStyleSheet("QLabel#ChatImageLabel { background: transparent; border: none; }")
        self._label.setToolTip("点击查看原图")
        self._label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._label.customContextMenuRequested.connect(self._show_context_menu)
        self._label.setMouseTracking(True)
        layout.addWidget(self._label, 0, Qt.AlignmentFlag.AlignLeft)

        self._overlay = QWidget(self._label)
        self._overlay.setObjectName("ChatImageOverlay")
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._overlay.setMouseTracking(True)
        self._overlay.installEventFilter(self)
        self._overlay.setStyleSheet("QWidget#ChatImageOverlay { background: transparent; border: none; }")
        overlay_layout = QHBoxLayout(self._overlay)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay_layout.setSpacing(4)
        self._copy_btn = self._make_tool_button("icon_action_copy.svg", "复制图片", self._copy_image)
        self._download_btn = self._make_tool_button("", "下载图片", self._download_image, icon=self._make_download_icon())
        overlay_layout.addWidget(self._copy_btn)
        overlay_layout.addWidget(self._download_btn)
        self._overlay.hide()
        self._label.installEventFilter(self)

        self._refresh_pixmap()
        if self._remote_image:
            single_shot_scoped(0, self, self._load_remote_image)

    def _load_remote_image(self) -> None:
        if self._image_loader is not None:
            self._image_loader.cancel()
            self._image_loader.deleteLater()
        self._loading_image = True
        self._image_error = ""
        self._refresh_pixmap()
        self._image_loader = ChatImageLoader(self)
        self._image_loader.loaded.connect(self._remote_image_loaded)
        self._image_loader.failed.connect(self._remote_image_failed)
        self._image_loader.load(self._data_url)

    def _remote_image_loaded(self, data: bytes, mime: str, thumbnail: QImage) -> None:
        self._loading_image = False
        self._image_error = ""
        self._image_bytes = data
        self._mime_type = mime
        self._pixmap = QPixmap.fromImage(thumbnail)
        self._label.setToolTip("点击查看原图")
        self._label.setStyleSheet("QLabel#ChatImageLabel { background: transparent; border: none; padding: 0; }")
        self._refresh_pixmap()

    def _remote_image_failed(self, message: str) -> None:
        self._loading_image = False
        self._image_error = message
        self._label.setToolTip(message + "，点击重试")
        self._refresh_pixmap()

    @staticmethod
    def _decode_image_source(image_source: str) -> tuple[str, bytes]:
        source = str(image_source or "").strip()
        if source.lower().startswith("data:image/"):
            return ChatImageWidget._decode_data_url(source)
        return ChatImageWidget._decode_local_image(source)

    @staticmethod
    def _decode_data_url(data_url: str) -> tuple[str, bytes]:
        header, sep, payload = str(data_url or "").partition(",")
        if not sep or ";base64" not in header.lower():
            return "image/png", b""
        mime_type = header[5:].split(";", 1)[0].strip().lower() or "image/png"
        try:
            return mime_type, base64.b64decode(payload, validate=False)
        except Exception:
            return mime_type, b""

    @staticmethod
    def _decode_local_image(image_source: str) -> tuple[str, bytes]:
        path = ChatImageWidget._local_image_path(image_source)
        mime_type = ChatImageWidget._mime_type_from_suffix(path.suffix if path else "")
        if path is None:
            logger.info(
                "[ChatImage] decode_local_image.path_missing source=%s",
                _preview_log_text(image_source),
            )
            return mime_type, b""
        try:
            data = path.read_bytes()
            logger.info(
                "[ChatImage] decode_local_image.ok path=%s bytes=%s mime=%s",
                path,
                len(data),
                mime_type,
            )
            return mime_type, data
        except Exception as exc:
            logger.info(
                "[ChatImage] decode_local_image.read_failed path=%s error=%s",
                path,
                exc,
            )
            return mime_type, b""

    @staticmethod
    def _local_image_path(image_source: str) -> Path | None:
        source = str(image_source or "").strip()
        if not source:
            return None
        if source.lower().startswith("file://"):
            parsed = urlparse(source)
            raw_path = unquote(parsed.path or "")
            if parsed.netloc:
                raw_path = f"//{parsed.netloc}{raw_path}"
            if re.match(r"^/[A-Za-z]:[\\/]", raw_path):
                raw_path = raw_path[1:]
            source = raw_path
        try:
            path = Path(source)
        except Exception:
            logger.info("[ChatImage] local_image_path.invalid source=%s", _preview_log_text(image_source))
            return None
        if not path.exists() or not path.is_file():
            logger.info(
                "[ChatImage] local_image_path.not_found source=%s parsed=%s exists=%s is_file=%s",
                _preview_log_text(image_source),
                path,
                path.exists(),
                path.is_file() if path.exists() else False,
            )
            return None
        return path

    @staticmethod
    def _mime_type_from_suffix(suffix: str) -> str:
        normalized = str(suffix or "").lower()
        if normalized in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if normalized == ".webp":
            return "image/webp"
        if normalized == ".gif":
            return "image/gif"
        if normalized == ".bmp":
            return "image/bmp"
        if normalized == ".avif":
            return "image/avif"
        return "image/png"

    def _extension(self) -> str:
        if self._mime_type in {"image/jpeg", "image/jpg"}:
            return ".jpg"
        if self._mime_type == "image/webp":
            return ".webp"
        if self._mime_type == "image/gif":
            return ".gif"
        if self._mime_type == "image/bmp":
            return ".bmp"
        if self._mime_type == "image/avif":
            return ".avif"
        return ".png"

    def _make_tool_button(self, icon_name: str, tooltip: str, slot, *, icon: Optional[QIcon] = None) -> QToolButton:
        button = QToolButton(self._overlay)
        button.setObjectName("ChatImageToolButton")
        button.setFixedSize(24, 24)
        button.setIconSize(QSize(15, 15))
        if icon is not None:
            button.setIcon(icon)
        else:
            cached = _cached_icon(icon_name)
            if not cached.isNull():
                button.setIcon(cached)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMouseTracking(True)
        button.installEventFilter(self)
        button.setStyleSheet(
            "QToolButton#ChatImageToolButton {"
            " border:1px solid rgba(148,163,184,0.38);"
            " background:rgba(255,255,255,0.88);"
            " border-radius:7px;"
            " padding:4px;"
            "}"
            "QToolButton#ChatImageToolButton:hover {"
            " background:rgba(248,250,252,0.96);"
            " border-color:rgba(100,116,139,0.48);"
            "}"
            "QToolButton#ChatImageToolButton:pressed { background:rgba(226,232,240,0.96); }"
        )
        button.clicked.connect(slot)
        return button

    @staticmethod
    def _make_download_icon(color: str = "#475569") -> QIcon:
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(color))
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(9, 3, 9, 10)
        painter.drawLine(6, 7, 9, 10)
        painter.drawLine(12, 7, 9, 10)
        painter.drawLine(4, 14, 14, 14)
        painter.drawLine(4, 12, 4, 14)
        painter.drawLine(14, 12, 14, 14)
        painter.end()
        return QIcon(pixmap)

    def _calculated_height(self, image_height: int) -> int:
        return int(image_height) + 4

    def _position_overlay(self) -> None:
        if not hasattr(self, "_overlay"):
            return
        if not self._image_bytes or self._pixmap.isNull():
            self._overlay.hide()
            return
        overlay_w = 24 * 2 + 4
        overlay_h = 24
        self._overlay.setFixedSize(overlay_w, overlay_h)
        self._overlay.move(
            max(4, self._label.width() - overlay_w - 8),
            max(4, self._label.height() - overlay_h - 8),
        )
        self._sync_overlay_visibility()

    def _overlay_hover_targets(self) -> tuple[QWidget, ...]:
        return tuple(
            target
            for target in (
                getattr(self, "_label", None),
                getattr(self, "_overlay", None),
                getattr(self, "_copy_btn", None),
                getattr(self, "_download_btn", None),
            )
            if target is not None
        )

    def _has_visible_image(self) -> bool:
        return bool(self._image_bytes) and not self._pixmap.isNull()

    def _should_show_overlay(self) -> bool:
        if not self._has_visible_image():
            return False
        return any(target.underMouse() for target in self._overlay_hover_targets())

    def _sync_overlay_visibility(self) -> None:
        if not hasattr(self, "_overlay"):
            return
        if self._should_show_overlay():
            self._overlay.show()
            self._overlay.raise_()
            return
        self._overlay.hide()

    def _show_overlay(self) -> None:
        if not hasattr(self, "_overlay") or not self._has_visible_image():
            return
        self._position_overlay()
        self._overlay.show()
        self._overlay.raise_()

    def _schedule_overlay_sync(self) -> None:
        single_shot_scoped(0, self, self._sync_overlay_visibility)

    def _rounded_pixmap(self, pixmap: QPixmap) -> QPixmap:
        if pixmap.isNull():
            return pixmap
        rounded = QPixmap(pixmap.size())
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, pixmap.width(), pixmap.height()), self.IMAGE_RADIUS, self.IMAGE_RADIUS)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
        return rounded

    def _target_width(self) -> int:
        if self._pixmap.isNull():
            return 1
        view_w = 0
        p = self.parentWidget()
        while p is not None:
            if p.__class__.__name__ == "BubbleListView":
                try:
                    view_w = p.viewport().width() if p.viewport() else p.width()
                except Exception:
                    pass
                break
            p = p.parentWidget()
        if view_w > 0:
            parent_w = max(100, view_w - 80)
        else:
            parent_w = 640
        available = max(1, int(parent_w))
        natural = max(1, int(self._pixmap.width()))
        max_width = min(self.THUMBNAIL_MAX_WIDTH, available)
        if self._pixmap.height() > 0:
            max_width = min(max_width, int(self.THUMBNAIL_MAX_HEIGHT * (natural / max(1, self._pixmap.height()))))
        return max(1, min(natural, max_width))

    def _refresh_pixmap(self) -> None:
        if self._pixmap.isNull():
            logger.info(
                "[ChatImage] refresh_pixmap.null source=%s bytes=%s mime=%s",
                _preview_log_text(self._data_url),
                len(self._image_bytes or b""),
                self._mime_type,
            )
            if getattr(self, "_loading_image", False):
                self._label.setText("图片加载中…")
            elif getattr(self, "_image_error", ""):
                self._label.setText("图片加载失败，点击重试")
            elif self._image_bytes and self._mime_type == "image/avif":
                self._label.setText("图片格式暂不支持预览，可右键下载原图")
            else:
                self._label.setText("图片加载失败，可右键下载原图")
            self._label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            self._label.setStyleSheet(
                "QLabel#ChatImageLabel {"
                " background:#f8fafc;"
                " border:1px solid #e2e8f0;"
                " border-radius:8px;"
                " color:#64748b;"
                " padding:10px 12px;"
                " font-size:13px;"
                "}"
            )
            self._label.adjustSize()
            image_h = max(34, self._label.sizeHint().height())
            image_w = max(120, self._label.sizeHint().width())
            self._label.setFixedSize(image_w, image_h)
            self._overlay.hide()
            old_size = self.size()
            new_h = self._calculated_height(image_h)
            self._current_calculated_width = image_w
            self._current_calculated_height = new_h
            self.setFixedSize(image_w, new_h)
            self.updateGeometry()
            if old_size.width() != image_w or old_size.height() != new_h:
                self._notify_image_layout_changed(keep_bottom=False)
            return
        target_w = self._target_width()
        if target_w == self._pixmap.width():
            display = self._pixmap
        else:
            display = self._pixmap.scaledToWidth(target_w, Qt.TransformationMode.SmoothTransformation)
        logger.info(
            "[ChatImage] refresh_pixmap.ok source=%s natural=(%s,%s) display=(%s,%s)",
            _preview_log_text(self._data_url),
            self._pixmap.width(),
            self._pixmap.height(),
            display.width(),
            display.height(),
        )
        self._display_pixmap = display
        self._label.setPixmap(self._rounded_pixmap(display))
        self._label.setFixedSize(display.size())
        self._position_overlay()
        old_size = self.size()
        new_h = self._calculated_height(display.height())
        self._current_calculated_width = display.width()
        self._current_calculated_height = new_h
        self.setFixedSize(display.width(), new_h)
        self.updateGeometry()

        if old_size.width() != display.width() or old_size.height() != new_h:
            self._notify_image_layout_changed(keep_bottom=False)

    def _layout_targets(self) -> tuple[Optional[QWidget], Optional[QWidget]]:
        bubble = None
        view = None
        parent = self.parentWidget()
        while parent is not None:
            name = parent.__class__.__name__
            if name == "ChatBubble" and bubble is None:
                bubble = parent
            elif name == "BubbleListView":
                view = parent
                break
            parent = parent.parentWidget()
        return bubble, view

    def _notify_image_layout_changed(self, *, keep_bottom: bool) -> None:
        """图片首帧尺寸晚于气泡测高时，向上补一次稳定布局刷新。"""
        bubble, view = self._layout_targets()
        if bubble is not None:
            try:
                if hasattr(bubble, "_natural_width"):
                    bubble._natural_width = None
                invalidate_height = getattr(bubble, "_invalidate_height_cache", None)
                if callable(invalidate_height):
                    invalidate_height()
                box = getattr(bubble, "_bubble_box", None)
                if box is not None:
                    box_layout = box.layout()
                    if box_layout is not None:
                        box_layout.invalidate()
                    box.updateGeometry()
                bubble.updateGeometry()
                measure_w = int(getattr(bubble, "_measure_width", 0) or bubble.width() or bubble.sizeHint().width())
                if measure_w > 0 and hasattr(bubble, "heightForWidth"):
                    bubble.setMinimumHeight(max(1, int(bubble.heightForWidth(measure_w))))
            except RuntimeError:
                pass
            except Exception:
                logger.exception("[ChatImage] notify_layout_changed.bubble_failed")

        if view is None or not hasattr(view, "refresh_layout"):
            return

        effective_keep_bottom = bool(keep_bottom)
        if not effective_keep_bottom and hasattr(view, "is_at_bottom"):
            try:
                effective_keep_bottom = bool(view.is_at_bottom(48))
            except Exception:
                effective_keep_bottom = False

        def _refresh_view(target=view) -> None:
            try:
                target.refresh_layout(keep_bottom=effective_keep_bottom)
                if effective_keep_bottom and hasattr(target, "scroll_to_bottom"):
                    target.scroll_to_bottom()
            except RuntimeError:
                pass
            except Exception:
                logger.exception("[ChatImage] notify_layout_changed.view_failed")

        for delay_ms in (0, 16, 60):
            QTimer.singleShot(delay_ms, _refresh_view)

        try:
            panel = view.window()
        except Exception:
            panel = None
        try:
            suppress_until = float(getattr(view, "_suppress_image_panel_reposition_until", 0.0) or 0.0)
        except Exception:
            suppress_until = 0.0
        if suppress_until > time.monotonic():
            return
        if panel is not None and hasattr(panel, "_reposition"):
            def _reposition_panel(target=panel) -> None:
                try:
                    try:
                        target_view = self._layout_targets()[1]
                        suppress_until_inner = float(
                            getattr(target_view, "_suppress_image_panel_reposition_until", 0.0) or 0.0
                        ) if target_view is not None else 0.0
                    except Exception:
                        suppress_until_inner = 0.0
                    if suppress_until_inner > time.monotonic():
                        return
                    target._reposition()
                except RuntimeError:
                    pass
                except Exception:
                    logger.exception("[ChatImage] notify_layout_changed.reposition_failed")

            QTimer.singleShot(16, _reposition_panel)
            QTimer.singleShot(80, _reposition_panel)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and (self._image_bytes or self._remote_image):
            self._open_original_image()
            event.accept()
            return
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_pixmap()
        self._position_overlay()

    def eventFilter(self, watched, event) -> bool:
        if watched in self._overlay_hover_targets():
            if event.type() == QEvent.Type.Enter:
                self._show_overlay()
            elif event.type() == QEvent.Type.Leave:
                self._schedule_overlay_sync()
        return super().eventFilter(watched, event)

    def sizeHint(self) -> QSize:
        if hasattr(self, "_current_calculated_width") and hasattr(self, "_current_calculated_height"):
            return QSize(self._current_calculated_width, self._current_calculated_height)
        if self._pixmap.isNull():
            return QSize(120, self._calculated_height(34))
        target_w = self._target_width()
        target_h = max(1, int(self._pixmap.height() * (target_w / max(1, self._pixmap.width()))))
        return QSize(target_w, self._calculated_height(target_h))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _show_context_menu(self, pos: QPoint) -> None:
        if not self._image_bytes:
            return
        from deepcat.ui.post_capture_actions import OcrGenericMenuPopup

        popup = OcrGenericMenuPopup(
            [
                ("下载图片", self._download_image, True),
                ("复制图片", self._copy_image, True),
            ],
            parent=self,
        )
        popup.show_at_pos(self._label.mapToGlobal(pos))

    def _open_original_image(self) -> None:
        if self._remote_image and not self._image_bytes:
            if not self._loading_image:
                self._load_remote_image()
            return
        path = None if self._remote_image else self._local_image_path(self._data_url)
        if path is None and self._image_bytes:
            digest = hashlib.sha256(self._image_bytes).hexdigest()[:16]
            path = Path(tempfile.gettempdir()) / f"deepcat_generated_preview_{digest}{self._extension()}"
            try:
                if not path.exists():
                    path.write_bytes(self._image_bytes)
            except Exception:
                return
        if path is None:
            return
        from deepcat.ui.main_window import ImagePreviewDialog

        dialog = ImagePreviewDialog(str(path), parent=self.window())
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.exec()

    def _download_image(self) -> None:
        safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", self._alt).strip(" .") or "generated-image"
        default_name = f"{safe_name}{self._extension()}"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "下载图片",
            default_name,
            "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp);;所有文件 (*)",
        )
        if not file_path:
            return
        try:
            Path(file_path).write_bytes(self._image_bytes)
        except Exception:
            return

    def _copy_image(self) -> None:
        try:
            clipboard = QGuiApplication.clipboard()
            if clipboard is None:
                return
            image = QImage()
            if image.loadFromData(self._image_bytes):
                clipboard.setImage(image)
        except Exception:
            return


class ChatBubble(QFrame):
    """单条消息气泡：圆角 QFrame + 富文本 QLabel。"""

    copy_requested = pyqtSignal(int)
    delete_requested = pyqtSignal(int)
    regenerate_requested = pyqtSignal(int)
    copy_error_requested = pyqtSignal(int)
    switch_model_retry_requested = pyqtSignal(int)
    new_round_from_error_requested = pyqtSignal(int)
    add_to_note_requested = pyqtSignal(int)
    image_preview_requested = pyqtSignal(str)
    follow_up_requested = pyqtSignal(str)
    edit_from_requested = pyqtSignal(int)
    branch_from_requested = pyqtSignal(int)
    pin_context_requested = pyqtSignal(int)

    def __init__(self, role: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._role = "user" if role == "user" else "assistant"
        self._raw_text = ""
        self._measure_width = 0
        self._natural_width: Optional[int] = None
        # heightForWidth 结果缓存（key=外宽）。富文本 QLabel 的文档排版极贵，Qt 布局
        # 在一次 activate 中会对同一宽度反复询问 sizeHint/heightForWidth，多轮全量
        # 刷新叠加后成为长会话切换白屏的主因之一；内容变更时统一失效。
        self._height_cache: dict[int, int] = {}
        self._is_streaming = False
        self._stream_width_locked = False
        self._is_placeholder = False
        self._context_pinned = False
        self._last_render_key: Optional[tuple[str, bool, bool, bool]] = None
        self._long_answer_expanded = False
        self._expand_answer_btn: Optional[QToolButton] = None
        self._follow_up_queries: dict[str, str] = {}
        self._follow_up_labels: dict[str, str] = {}
        self._follow_up_counter = 0

        self._meta_start_time: Optional[str] = None
        self._meta_model_name: Optional[str] = None
        self._meta_elapsed: Optional[float] = None
        self._meta_reply_tokens: Optional[int] = None
        self._meta_total_tokens: Optional[int] = None

        self.setObjectName("ChatBubble")
        self.setStyleSheet("QFrame#ChatBubble { background: transparent; border: none; }")
        self.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred))

        self._bubble_box = QFrame(self)
        self._bubble_box.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._bubble_box.installEventFilter(self)
        self._bubble_box.customContextMenuRequested.connect(
            lambda pos: self._show_bubble_context_menu_at(self._bubble_box.mapToGlobal(pos))
        )
        self._padding_selection_label: Optional[SelectableAutoScrollLabel] = None
        self._padding_selection_press_global: Optional[QPoint] = None
        self._padding_selection_started = False
        if self._role == "user":
            self._bubble_box.setObjectName("ChatBubbleUser")
            self._bubble_box.setStyleSheet(
                "QFrame#ChatBubbleUser { background:#f3f8fe; border:1px solid #dbe3f0;"
                " border-radius:14px; }"
            )
        else:
            self._bubble_box.setObjectName("ChatBubbleAI")
            self._bubble_box.setStyleSheet(
                "QFrame#ChatBubbleAI { background:#ffffff; border:1px solid #e5e7eb;"
                " border-radius:14px; }"
            )

        box_lay = QVBoxLayout(self._bubble_box)
        box_lay.setContentsMargins(14, 10, 14, 10)
        box_lay.setSpacing(8)
        self._content_layout = box_lay

        self._code_blocks = []
        self._rendered_segment_widgets: list[tuple[str, QWidget]] = []
        self._rendered_segment_signature: list[tuple[str, str]] = []
        self._label = self._make_text_label()
        box_lay.addWidget(self._label)

        lay = QVBoxLayout(self)
        bottom_margin = 22 if self._role == "user" else 0
        lay.setContentsMargins(0, 0, 0, bottom_margin)
        lay.setSpacing(0)
        lay.addWidget(self._bubble_box)

        self._msg_index = -1

        self._action_toolbar = QFrame(self)
        self._action_toolbar.setObjectName("BubbleActionToolbar")
        self._action_toolbar.setStyleSheet(
            "QFrame#BubbleActionToolbar {"
            "  background-color: transparent;"
            "  border: none;"
            "}"
            "QToolButton {"
            "  border: none;"
            "  background: transparent;"
            "  border-radius: 4px;"
            "}"
            "QToolButton:hover {"
            "  background-color: rgba(0, 0, 0, 0.06);"
            "}"
        )
        self._action_toolbar.hide()
        self._hide_action_toolbar_queued = False
        self._action_toolbar.setMouseTracking(True)
        self._action_toolbar.installEventFilter(self)

        toolbar_lay = QHBoxLayout(self._action_toolbar)
        toolbar_lay.setContentsMargins(4, 2, 4, 2)
        toolbar_lay.setSpacing(4)

        get_icon = _cached_icon

        self._btn_regenerate = QToolButton(self._action_toolbar)
        self._btn_regenerate.setFixedSize(20, 20)
        self._btn_regenerate.setIcon(get_icon("icon_action_refresh.svg"))
        self._btn_regenerate.setToolTip("")
        set_disable_global_tooltip(self._btn_regenerate)
        self._btn_regenerate.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_regenerate.clicked.connect(self._on_regenerate_clicked)
        self._btn_regenerate.installEventFilter(self)
        toolbar_lay.addWidget(self._btn_regenerate)

        self._btn_add_to_note = QToolButton(self._action_toolbar)
        self._btn_add_to_note.setFixedSize(20, 20)
        self._btn_add_to_note.setIcon(get_icon("icon_action_notebook.svg"))
        self._btn_add_to_note.setToolTip("")
        set_disable_global_tooltip(self._btn_add_to_note)
        self._btn_add_to_note.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_add_to_note.clicked.connect(self._on_add_to_note_clicked)
        self._btn_add_to_note.installEventFilter(self)
        toolbar_lay.addWidget(self._btn_add_to_note)

        self._btn_copy = QToolButton(self._action_toolbar)
        self._btn_copy.setFixedSize(20, 20)
        self._btn_copy.setIcon(get_icon("icon_action_copy.svg"))
        self._btn_copy.setToolTip("")
        set_disable_global_tooltip(self._btn_copy)
        self._btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_copy.clicked.connect(self._on_copy_clicked)
        self._btn_copy.installEventFilter(self)
        toolbar_lay.addWidget(self._btn_copy)

        self._btn_delete = QToolButton(self._action_toolbar)
        self._btn_delete.setFixedSize(20, 20)
        self._btn_delete.setIcon(get_icon("icon_todo_delete.svg"))
        self._btn_delete.setToolTip("")
        set_disable_global_tooltip(self._btn_delete)
        self._btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_delete.clicked.connect(self._on_delete_clicked)
        self._btn_delete.installEventFilter(self)
        toolbar_lay.addWidget(self._btn_delete)

    def _position_action_toolbar(self) -> None:
        self._action_toolbar.adjustSize()
        tw = self._action_toolbar.width()
        th = self._action_toolbar.height()
        if self._role == "user":
            self._action_toolbar.setGeometry(self.width() - tw - 8, self._bubble_box.height() + 2, tw, th)
        else:
            self._action_toolbar.setGeometry(self.width() - tw - 8, self.height() - th - 4, tw, th)

    def _global_rect_contains_cursor(self, widget: QWidget, *, padding: int = 0) -> bool:
        if widget is None or not widget.isVisible():
            return False
        try:
            global_pos = QCursor.pos()
            top_left = widget.mapToGlobal(QPoint(0, 0))
            rect = widget.rect().translated(top_left)
            if int(padding) > 0:
                rect = rect.adjusted(-padding, -padding, padding, padding)
            return bool(rect.contains(global_pos))
        except Exception:
            return False

    def _cursor_in_action_hover_region(self) -> bool:
        return bool(
            self._global_rect_contains_cursor(self, padding=2)
            or self._global_rect_contains_cursor(self._action_toolbar, padding=8)
        )

    def _hide_action_toolbar_if_cursor_left(self) -> None:
        self._hide_action_toolbar_queued = False
        if self._cursor_in_action_hover_region():
            return
        self._action_toolbar.hide()
        if hasattr(self, "_smooth_tooltip"):
            self._smooth_tooltip.hide()

    def hide_transient_overlays(self) -> None:
        self._hide_action_toolbar_queued = False
        try:
            self._action_toolbar.hide()
        except Exception:
            pass
        if hasattr(self, "_metadata_label") and self._metadata_label:
            try:
                self._metadata_label.hide()
            except Exception:
                pass
        if hasattr(self, "_smooth_tooltip"):
            try:
                self._smooth_tooltip.hide()
            except Exception:
                pass

    def _schedule_action_toolbar_hide(self) -> None:
        if not self._action_toolbar.isVisible():
            return
        if bool(getattr(self, "_hide_action_toolbar_queued", False)):
            return
        self._hide_action_toolbar_queued = True
        QTimer.singleShot(90, self._hide_action_toolbar_if_cursor_left)

    def _make_text_label(self) -> SelectableAutoScrollLabel:
        label = SelectableAutoScrollLabel(self._bubble_box)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        label.setOpenExternalLinks(False)
        label.linkActivated.connect(self._handle_link_activated)
        label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        label.customContextMenuRequested.connect(
            lambda pos, target=label: self._show_bubble_context_menu_at(target.mapToGlobal(pos))
        )
        label.setStyleSheet("QLabel { background:transparent; border:none; }")
        label.setContentsMargins(0, 0, 0, 3)
        label.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        return label

    def _clear_rendered_content(self) -> None:
        self._clear_padding_selection_proxy()
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                # 先隐藏再删除，避免 setParent(None) 导致 widget 变成
                # 独立顶级窗口短暂弹出（deleteLater 在下一轮事件循环才执行）
                widget.hide()
                widget.deleteLater()
        self._rendered_segment_widgets = []
        self._rendered_segment_signature = []
        self._expand_answer_btn = None
        self._label = self._make_text_label()

    def _set_single_label_html(self, html: str) -> None:
        if (
            self._content_layout.count() == 1
            and isinstance(self._content_layout.itemAt(0).widget(), SelectableAutoScrollLabel)
        ):
            label = self._content_layout.itemAt(0).widget()
            if isinstance(label, SelectableAutoScrollLabel):
                if label.text() != str(html or ""):
                    label.setUpdatesEnabled(False)
                    label.setText(str(html or ""))
                    label.setUpdatesEnabled(True)
                self._label = label
                self._rendered_segment_widgets = [("markdown", label)]
                self._rendered_segment_signature = [("single", "")]
                return
        self._clear_rendered_content()
        self._label.setUpdatesEnabled(False)
        self._label.setText(str(html or ""))
        self._label.setUpdatesEnabled(True)
        self._content_layout.addWidget(self._label)
        self._rendered_segment_widgets = [("markdown", self._label)]
        self._rendered_segment_signature = [("single", "")]

    @staticmethod
    def _defer_streaming_media_markdown(text: str) -> str:
        return str(text or "")

    def _display_text_for_render(self, raw_text: str, *, streaming: bool, is_markdown: bool) -> str:
        text = str(raw_text or "")
        if bool(streaming) and bool(is_markdown):
            return self._defer_streaming_media_markdown(text)
        return text

    def _sync_expand_answer_button(self, raw_text: str, *, streaming: bool, is_markdown: bool) -> None:
        self._expand_answer_btn = None

    def _expand_long_answer(self) -> None:
        self._long_answer_expanded = True
        self._last_render_key = None
        self.set_content(self._raw_text, is_markdown=True, streaming=False)
        parent = self.parent()
        while parent is not None:
            if parent.__class__.__name__ == "BubbleListView" and hasattr(parent, "refresh_layout"):
                parent.refresh_layout(keep_bottom=False)
                break
            parent = parent.parent()

    def _reset_follow_up_links(self) -> None:
        self._follow_up_queries = {}
        self._follow_up_labels = {}
        self._follow_up_counter = 0

    @staticmethod
    def _looks_like_follow_up(label: str, query: str) -> bool:
        label_text = str(label or "").strip()
        query_text = str(query or "").strip()
        if not label_text or not query_text:
            return False
        if label_text == query_text:
            return False
        if len(label_text) > 180 or len(query_text) < 4:
            return False
        return any(label_text.startswith(prefix) for prefix in _FOLLOW_UP_LABEL_PREFIXES)

    def _register_follow_up_query(self, label: str, query: str) -> str:
        token = f"DEEPCATFOLLOWUPTOKEN_{self._follow_up_counter}_END"
        self._follow_up_counter += 1
        self._follow_up_labels[token] = str(label or "").strip()
        self._follow_up_queries[token] = str(query or "").strip()
        return token

    def _link_follow_up_line(self, line: str) -> str:
        match = _FOLLOW_UP_LINE_RE.match(str(line or ""))
        if not match:
            return line
        label = str(match.group("label") or "").strip()
        query = str(match.group("query") or "").strip()
        if not self._looks_like_follow_up(label, query):
            return line
        token = self._register_follow_up_query(label, query)
        return (
            f"{match.group('indent') or ''}"
            f"{match.group('marker') or ''}"
            f"{token}{match.group('sep') or '：'}{query}"
        )

    def _prepare_follow_up_markdown(self, text: str) -> str:
        if self._role != "assistant":
            return str(text or "")
        out: list[str] = []
        for raw_line in str(text or "").splitlines(keepends=True):
            body = raw_line.rstrip("\r\n")
            newline = raw_line[len(body):]
            out.append(self._link_follow_up_line(body) + newline)
        return "".join(out)

    def _inject_follow_up_links(self, html: str) -> str:
        rendered = str(html or "")
        for token, label in getattr(self, "_follow_up_labels", {}).items():
            href = _html.escape(f"{_FOLLOW_UP_LINK_PREFIX}{token}", quote=True)
            label_html = _html.escape(label, quote=False)
            rendered = rendered.replace(
                token,
                f'<a href="{href}" style="{_FOLLOW_UP_LINK_STYLE}">{label_html}</a>',
            )
        return rendered

    def _normalized_render_segments(self, segments: list[dict[str, object]]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []

        def add_markdown_and_images(markdown_text: str) -> None:
            last_end = 0
            for image in iter_markdown_images(markdown_text):
                before = markdown_text[last_end:image.start]
                if before.strip():
                    normalized.append({"type": "markdown", "text": before, "lang": ""})
                normalized.append(
                    {
                        "type": "image",
                        "text": image.source,
                        "lang": image.alt or "generated image",
                    }
                )
                last_end = image.end
            tail = markdown_text[last_end:]
            if tail.strip():
                normalized.append({"type": "markdown", "text": tail, "lang": ""})

        for segment in segments:
            segment_type = str(segment.get("type", "markdown"))
            if segment_type == "code":
                normalized.append(
                    {
                        "type": "code",
                        "text": str(segment.get("text", "") or ""),
                        "lang": str(segment.get("lang", "") or ""),
                    }
                )
                continue
            markdown_text = str(segment.get("text", "") or "")
            if markdown_text.strip():
                add_markdown_and_images(markdown_text)
        logger.info(
            "[ChatBubble] normalized_segments input=%s output=%s summary=%s",
            len(segments or []),
            len(normalized),
            [
                {
                    "type": str(item.get("type", "") or ""),
                    "text_len": len(str(item.get("text", "") or "")),
                    "preview": _preview_log_text(item.get("text", ""), 80),
                }
                for item in normalized
            ],
        )
        return normalized

    def _segment_signature(self, segments: list[dict[str, str]]) -> list[tuple[str, str]]:
        signature: list[tuple[str, str]] = []
        for segment in segments:
            segment_type = str(segment.get("type", ""))
            if segment_type == "code":
                # 流式开头常从 ``` / ```py 逐步变成 ```python。
                # 代码块组件本身能更新标题，结构签名只关注位置与类型，避免语言名变化触发重建。
                signature.append((segment_type, ""))
            else:
                signature.append((segment_type, str(segment.get("lang", ""))))
        return signature

    def _update_rendered_segments_in_place(
        self,
        segments: list[dict[str, str]],
        signature: list[tuple[str, str]],
        *,
        streaming: bool,
    ) -> bool:
        if self._rendered_segment_signature != signature:
            return False
        if len(self._rendered_segment_widgets) != len(segments):
            return False

        first_label: Optional[SelectableAutoScrollLabel] = None
        code_blocks: list[str] = []
        for segment, rendered in zip(segments, self._rendered_segment_widgets):
            segment_type, widget = rendered
            if segment_type != str(segment.get("type", "")):
                return False
            if segment_type == "code":
                if not isinstance(widget, CodeBlockWidget):
                    return False
                code_text = str(segment.get("text", "") or "")
                code_blocks.append(code_text)
                widget.update_code(
                    code_text,
                    str(segment.get("lang", "") or ""),
                    streaming=bool(streaming),
                )
                continue
            if segment_type == "image":
                if not isinstance(widget, ChatImageWidget):
                    return False
                continue

            if not isinstance(widget, SelectableAutoScrollLabel):
                return False
            render_text = self._prepare_follow_up_markdown(str(segment.get("text", "") or ""))
            started_at = time.perf_counter()
            html = MarkdownRenderer.to_html(
                render_text,
                streaming=bool(streaming),
                code_blocks_out=None,
                include_code_tools=False,
            )
            _record_stream_perf(
                self,
                "markdown_ms",
                elapsed_ms=(time.perf_counter() - started_at) * 1000.0,
            )
            html = self._inject_follow_up_links(html)
            if widget.text() != html:
                widget.setText(html)
                widget.updateGeometry()
            if first_label is None:
                first_label = widget

        self._code_blocks = code_blocks
        if first_label is not None:
            self._label = first_label
        return True

    def _render_markdown_with_code_widgets(self, segments: list[dict[str, object]], *, streaming: bool) -> None:
        normalized_segments = self._normalized_render_segments(segments)
        signature = self._segment_signature(normalized_segments)
        if self._update_rendered_segments_in_place(normalized_segments, signature, streaming=bool(streaming)):
            return

        self._bubble_box.setUpdatesEnabled(False)
        try:
            self._clear_rendered_content()
            self._code_blocks = []
            has_text_label = False
            prev_was_code = False

            for segment in normalized_segments:
                segment_type = str(segment.get("type", "markdown"))
                if segment_type == "code":
                    code_text = str(segment.get("text", "") or "")
                    lang = str(segment.get("lang", "") or "")
                    self._code_blocks.append(code_text)
                    # 连续代码块之间添加间距，防止多个代码块视觉上挤到一起
                    if prev_was_code:
                        spacer = QFrame(self._bubble_box)
                        spacer.setFixedHeight(6)
                        spacer.setStyleSheet("background:transparent; border:none;")
                        self._content_layout.addWidget(spacer)
                    code_widget = CodeBlockWidget(code_text, lang, streaming=bool(streaming), parent=self._bubble_box)
                    code_widget.copy_requested.connect(self._copy_code_text)
                    code_widget.download_requested.connect(self._download_code_text)
                    code_widget.preview_requested.connect(self._preview_html_content)
                    code_widget.height_changed.connect(self._handle_code_block_height_changed)
                    self._content_layout.addWidget(code_widget)
                    self._rendered_segment_widgets.append(("code", code_widget))
                    prev_was_code = True
                    continue
                if segment_type == "image":
                    image_widget = ChatImageWidget(
                        str(segment.get("text", "") or ""),
                        str(segment.get("lang", "") or "generated image"),
                        parent=self._bubble_box,
                    )
                    self._content_layout.addWidget(image_widget)
                    self._rendered_segment_widgets.append(("image", image_widget))
                    prev_was_code = False
                    continue

                markdown_text = self._prepare_follow_up_markdown(str(segment.get("text", "") or ""))
                started_at = time.perf_counter()
                html = MarkdownRenderer.to_html(
                    markdown_text,
                    streaming=bool(streaming),
                    code_blocks_out=None,
                    include_code_tools=False,
                )
                _record_stream_perf(
                    self,
                    "markdown_ms",
                    elapsed_ms=(time.perf_counter() - started_at) * 1000.0,
                )
                html = self._inject_follow_up_links(html)
                if not str(html or "").strip():
                    continue
                label = self._make_text_label()
                label.setText(html)
                self._content_layout.addWidget(label)
                self._rendered_segment_widgets.append(("markdown", label))
                if not has_text_label:
                    self._label = label
                    has_text_label = True
                prev_was_code = False

            if self._content_layout.count() == 0:
                self._content_layout.addWidget(self._label)
            self._rendered_segment_signature = signature
        finally:
            self._bubble_box.setUpdatesEnabled(True)
            self._bubble_box.update()

    def _copy_code_text(self, code_text: str) -> None:
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(str(code_text or ""))
        except Exception:
            pass

    def _handle_code_block_height_changed(self) -> None:
        self._natural_width = None
        self._bubble_box.updateGeometry()
        self.updateGeometry()
        parent = self.parentWidget()
        while parent is not None:
            if parent.__class__.__name__ == "BubbleListView" and hasattr(parent, "refresh_layout"):
                try:
                    parent.refresh_layout(keep_bottom=False)
                except Exception:
                    pass
                break
            parent = parent.parentWidget()

    def set_msg_index(self, index: int) -> None:
        self._msg_index = int(index)

    def set_context_pinned(self, pinned: bool) -> None:
        self._context_pinned = bool(pinned)

    def _on_regenerate_clicked(self) -> None:
        if self._msg_index >= 0:
            self.regenerate_requested.emit(self._msg_index)

    def _on_copy_clicked(self) -> None:
        if self._msg_index >= 0:
            self.copy_requested.emit(self._msg_index)

    def _on_copy_error_clicked(self) -> None:
        if self._msg_index >= 0:
            self.copy_error_requested.emit(self._msg_index)

    def _on_switch_model_retry_clicked(self) -> None:
        if self._msg_index >= 0:
            self.switch_model_retry_requested.emit(self._msg_index)

    def _on_new_round_from_error_clicked(self) -> None:
        if self._msg_index >= 0:
            self.new_round_from_error_requested.emit(self._msg_index)

    def _on_delete_clicked(self) -> None:
        if self._msg_index >= 0:
            self.delete_requested.emit(self._msg_index)

    def _on_add_to_note_clicked(self) -> None:
        if self._msg_index >= 0:
            self.add_to_note_requested.emit(self._msg_index)

    def _on_edit_from_clicked(self) -> None:
        if self._msg_index >= 0:
            self.edit_from_requested.emit(self._msg_index)

    def _on_branch_from_clicked(self) -> None:
        if self._msg_index >= 0:
            self.branch_from_requested.emit(self._msg_index)

    def _on_pin_context_clicked(self) -> None:
        if self._msg_index >= 0:
            self.pin_context_requested.emit(self._msg_index)

    def _is_failure_bubble(self) -> bool:
        if self._role != "assistant":
            return False
        text = str(self._raw_text or "").strip()
        if not text:
            return False
        return text.startswith(
            (
                "回答失败：",
                "问答失败：",
                "翻译失败：",
                "问答模型配置错误",
                "翻译模型配置错误",
                "读取问答设置失败：",
                "读取翻译设置失败：",
            )
        )

    def _show_bubble_context_menu_at(self, global_pos: QPoint) -> None:
        if self._msg_index < 0 or getattr(self, "_is_placeholder", False):
            return
        from deepcat.ui.post_capture_actions import OcrGenericMenuPopup

        has_text = bool(str(self._raw_text or "").strip())
        pin_label = "取消固定上下文" if bool(getattr(self, "_context_pinned", False)) else "固定到上下文"
        if self._role == "user":
            menu_items = [
                ("复制问题", self._on_copy_clicked, has_text),
                ("编辑问题从这里重发", self._on_edit_from_clicked, has_text),
                ("新建分支会话", self._on_branch_from_clicked, True),
                (pin_label, self._on_pin_context_clicked, has_text),
                ("删除问题", self._on_delete_clicked, True),
            ]
        elif self._is_failure_bubble():
            can_retry = not bool(getattr(self, "_is_streaming", False))
            menu_items = [
                ("重试", self._on_regenerate_clicked, can_retry),
                ("复制错误", self._on_copy_error_clicked, has_text),
                ("切换模型重试", self._on_switch_model_retry_clicked, can_retry),
                ("保留原问题新开一轮", self._on_new_round_from_error_clicked, can_retry),
                ("新建分支会话", self._on_branch_from_clicked, True),
                (pin_label, self._on_pin_context_clicked, has_text),
                ("删除回答", self._on_delete_clicked, True),
            ]
        else:
            menu_items = [
                ("复制回答", self._on_copy_clicked, has_text),
                ("重新生成", self._on_regenerate_clicked, not bool(getattr(self, "_is_streaming", False))),
                ("新建分支会话", self._on_branch_from_clicked, True),
                (pin_label, self._on_pin_context_clicked, has_text),
                ("加入笔记", self._on_add_to_note_clicked, has_text),
                ("删除回答", self._on_delete_clicked, True),
            ]
        popup = OcrGenericMenuPopup(menu_items, parent=self)
        popup.show_at_pos(global_pos)

    def set_metadata(
        self,
        start_time: Optional[str] = None,
        model_name: Optional[str] = None,
        elapsed: Optional[float] = None,
        reply_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
    ) -> None:
        if start_time is not None:
            self._meta_start_time = start_time
        if model_name is not None:
            self._meta_model_name = model_name
        if elapsed is not None:
            self._meta_elapsed = elapsed
        if reply_tokens is not None:
            self._meta_reply_tokens = reply_tokens
        if total_tokens is not None:
            self._meta_total_tokens = total_tokens

        # If reply_tokens is not provided, estimate it on the fly
        if self._meta_reply_tokens is None and self._raw_text:
            self._meta_reply_tokens = _estimate_tokens(self._raw_text)

        if hasattr(self, "_metadata_label") and self._metadata_label:
            txt = self._metadata_label_text()
            self._metadata_label.setText(txt)
            self._metadata_label.adjustSize()

    def _metadata_label_text(self) -> str:
        if getattr(self, "_is_placeholder", False):
            return ""
        parts = []
        if self._meta_start_time:
            parts.append(self._meta_start_time)
        if self._meta_model_name:
            parts.append(self._meta_model_name)
        if self._meta_elapsed is not None:
            val = float(self._meta_elapsed)
            if val < 1.0:
                parts.append(f"{val:.1f}秒")
            else:
                parts.append(f"{int(round(val))}秒")

        rep = self._meta_reply_tokens
        if rep is None and self._raw_text:
            rep = _estimate_tokens(self._raw_text)
        tot = self._meta_total_tokens

        if rep is not None:
            if tot:
                parts.append(f"Tokens: {rep}/{tot}")
            else:
                parts.append(f"Tokens: {rep}")

        return " | ".join(parts)

    def destroy(self, destroyWindow: bool = True, destroySubWindows: bool = True) -> None:
        if hasattr(self, "_metadata_label") and self._metadata_label:
            try:
                self._metadata_label.deleteLater()
            except Exception:
                pass
        super().destroy(destroyWindow, destroySubWindows)

    def enterEvent(self, event) -> None:
        if getattr(self, "_is_streaming", False) or getattr(self, "_is_placeholder", False):
            super().enterEvent(event)
            return

        if self._msg_index >= 0:
            self._hide_action_toolbar_queued = False
            self._position_action_toolbar()
            self._action_toolbar.show()
            self._action_toolbar.raise_()

        if self._role == "assistant" and self._metadata_label_text().strip() and self.parentWidget() is not None:
            if not hasattr(self, "_metadata_label") or self._metadata_label is None or self._metadata_label.parent() is None:
                self._metadata_label = QLabel(self.parentWidget())
                self._metadata_label.setStyleSheet(
                    "QLabel {"
                    "  color: #94a3b8;"
                    "  font-size: 10px;"
                    "  font-family: 'Microsoft YaHei', 'Segoe UI', system-ui;"
                    "  background: transparent;"
                    "  border: none;"
                    "}"
                )
            self._metadata_label.setText(self._metadata_label_text())
            self._metadata_label.adjustSize()
            pos = self.mapTo(self.parentWidget(), QPoint(12, -14))
            if pos.y() < 2:
                pos.setY(2)
            self._metadata_label.move(pos)
            self._metadata_label.show()
            self._metadata_label.raise_()

        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._schedule_action_toolbar_hide()
        if hasattr(self, "_metadata_label") and self._metadata_label:
            try:
                self._metadata_label.hide()
            except Exception:
                pass
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        action_toolbar = getattr(self, "_action_toolbar", None)
        if action_toolbar is not None and action_toolbar.isVisible():
            self._position_action_toolbar()

        for image_widget in self.findChildren(ChatImageWidget):
            try:
                image_widget._refresh_pixmap()
            except Exception:
                pass

    def _text_label_at_left_padding(self, pos: QPoint) -> Optional[SelectableAutoScrollLabel]:
        """返回纵向命中且位于其左侧内边距中的文本段。"""
        for kind, widget in list(getattr(self, "_rendered_segment_widgets", []) or []):
            if kind != "markdown" or not isinstance(widget, SelectableAutoScrollLabel):
                continue
            try:
                if not widget.isVisible():
                    continue
                top_left = widget.mapTo(self._bubble_box, QPoint(0, 0))
                label_rect = widget.rect().translated(top_left)
            except RuntimeError:
                continue
            if label_rect.top() <= pos.y() <= label_rect.bottom() and 0 <= pos.x() < label_rect.left():
                return widget
        return None

    @staticmethod
    def _send_padding_selection_mouse_event(
        label: SelectableAutoScrollLabel,
        event_type: QEvent.Type,
        global_pos: QPoint,
        *,
        button: Qt.MouseButton,
        buttons: Qt.MouseButton,
        modifiers: Qt.KeyboardModifier,
        clamp_to_left_edge: bool = False,
    ) -> None:
        local_pos = label.mapFromGlobal(global_pos)
        if clamp_to_left_edge:
            local_pos.setX(0)
            local_pos.setY(max(0, min(max(0, label.height() - 1), local_pos.y())))
        forwarded = QMouseEvent(
            event_type,
            QPointF(local_pos),
            QPointF(global_pos),
            button,
            buttons,
            modifiers,
        )
        QApplication.sendEvent(label, forwarded)

    def _clear_padding_selection_proxy(self) -> None:
        self._padding_selection_label = None
        self._padding_selection_press_global = None
        self._padding_selection_started = False
        try:
            if QWidget.mouseGrabber() is self._bubble_box:
                self._bubble_box.releaseMouse()
        except Exception:
            pass

    def eventFilter(self, watched, event) -> bool:
        from PyQt6.QtCore import QEvent, QPoint
        bubble_box = self.__dict__.get("_bubble_box")
        if bubble_box is None or sip.isdeleted(bubble_box):
            return False
        if watched is bubble_box:
            event_type = event.type()
            if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                label = self._text_label_at_left_padding(event.position().toPoint())
                if label is not None:
                    self._padding_selection_label = label
                    self._padding_selection_press_global = event.globalPosition().toPoint()
                    self._padding_selection_started = False
                    try:
                        self._bubble_box.grabMouse()
                    except Exception:
                        pass
                    return True
            elif event_type == QEvent.Type.MouseMove and self._padding_selection_label is not None:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    label = self._padding_selection_label
                    press_global = self._padding_selection_press_global
                    current_global = event.globalPosition().toPoint()
                    try:
                        if not self._padding_selection_started and press_global is not None and current_global != press_global:
                            self._send_padding_selection_mouse_event(
                                label,
                                QEvent.Type.MouseButtonPress,
                                press_global,
                                button=Qt.MouseButton.LeftButton,
                                buttons=Qt.MouseButton.LeftButton,
                                modifiers=event.modifiers(),
                                clamp_to_left_edge=True,
                            )
                            self._padding_selection_started = True
                        if self._padding_selection_started:
                            self._send_padding_selection_mouse_event(
                                label,
                                QEvent.Type.MouseMove,
                                current_global,
                                button=Qt.MouseButton.NoButton,
                                buttons=Qt.MouseButton.LeftButton,
                                modifiers=event.modifiers(),
                            )
                    except RuntimeError:
                        self._clear_padding_selection_proxy()
                    return True
            elif event_type == QEvent.Type.MouseButtonRelease and self._padding_selection_label is not None:
                label = self._padding_selection_label
                try:
                    if self._padding_selection_started and event.button() == Qt.MouseButton.LeftButton:
                        self._send_padding_selection_mouse_event(
                            label,
                            QEvent.Type.MouseButtonRelease,
                            event.globalPosition().toPoint(),
                            button=Qt.MouseButton.LeftButton,
                            buttons=Qt.MouseButton.NoButton,
                            modifiers=event.modifiers(),
                        )
                except RuntimeError:
                    pass
                self._clear_padding_selection_proxy()
                return True
            elif event_type in {QEvent.Type.UngrabMouse, QEvent.Type.Hide}:
                self._clear_padding_selection_proxy()
        copy_btn = getattr(self, "_btn_copy", None)
        delete_btn = getattr(self, "_btn_delete", None)
        regenerate_btn = getattr(self, "_btn_regenerate", None)
        add_to_note_btn = getattr(self, "_btn_add_to_note", None)
        action_toolbar = getattr(self, "_action_toolbar", None)
        action_targets = tuple(
            target
            for target in (
                action_toolbar,
                copy_btn,
                delete_btn,
                regenerate_btn,
                add_to_note_btn,
            )
            if target is not None
        )
        if watched in action_targets:
            if event.type() in {QEvent.Type.HoverEnter, QEvent.Type.Enter}:
                self._hide_action_toolbar_queued = False
                if self._msg_index >= 0 and action_toolbar is not None and not action_toolbar.isVisible():
                    self._position_action_toolbar()
                    action_toolbar.show()
                    action_toolbar.raise_()
            elif event.type() in {QEvent.Type.HoverLeave, QEvent.Type.Leave}:
                self._schedule_action_toolbar_hide()

        button_targets = tuple(
            target
            for target in (copy_btn, delete_btn, regenerate_btn, add_to_note_btn)
            if target is not None
        )
        if watched in button_targets:
            if event.type() in {QEvent.Type.HoverEnter, QEvent.Type.Enter}:
                tip_text = ""
                if watched is copy_btn:
                    tip_text = "复制"
                elif watched is delete_btn:
                    tip_text = "删除"
                elif watched is regenerate_btn:
                    tip_text = "重新生成"
                elif watched is add_to_note_btn:
                    tip_text = "笔记本"

                if tip_text:
                    if not hasattr(self, "_smooth_tooltip"):
                        from deepcat.ui.post_capture_actions import SmoothToolTip
                        self._smooth_tooltip = SmoothToolTip()

                    pos = watched.mapToGlobal(QPoint(int(watched.width() / 2), watched.height() + 6))
                    self._smooth_tooltip.show_text(
                        tip_text,
                        pos,
                        direction="below",
                    )
            elif event.type() in {QEvent.Type.HoverLeave, QEvent.Type.Leave}:
                try:
                    if watched.rect().contains(watched.mapFromGlobal(QCursor.pos())):
                        return False
                except Exception:
                    pass
                if hasattr(self, "_smooth_tooltip"):
                    self._smooth_tooltip.hide()
        return super().eventFilter(watched, event)

    def set_measure_width(self, width: int) -> None:
        width = int(max(116, width))
        if self._measure_width == width:
            return
        self._measure_width = width
        self.updateGeometry()

    def _invalidate_height_cache(self) -> None:
        if self._height_cache:
            self._height_cache.clear()

    def _height_for_outer_width(self, width: int) -> int:
        width = int(width)
        cached = self._height_cache.get(width)
        if cached is not None:
            return cached
        bottom_margin = 22 if self._role == "user" else 0
        left = 14
        right = 14
        top = 10
        bottom = 10

        content_w = max(1, int(width) - left - right)
        content_h = 0
        visible_count = 0
        spacing = max(0, int(self._content_layout.spacing()))

        for i in range(self._content_layout.count()):
            item = self._content_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if widget is None:
                continue
            if widget.isHidden():
                continue
            if visible_count:
                content_h += spacing
            if widget.hasHeightForWidth():
                widget_h = widget.heightForWidth(content_w)
            else:
                widget_h = widget.sizeHint().height()
            content_h += max(1, int(widget_h))
            visible_count += 1

        if visible_count <= 0:
            content_h = 1
        result = int(top + max(1, content_h) + bottom + bottom_margin)
        self._height_cache[width] = result
        return result

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._height_for_outer_width(int(width))

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        width = int(self._measure_width or self.width() or hint.width())
        max_w = int(self.maximumWidth())
        if max_w > 0 and max_w < 16777215:
            width = min(width, max_w)
        width = max(1, width)
        return QSize(width, self._height_for_outer_width(width))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def set_content(self, text: str, *, is_markdown: bool, streaming: bool = False) -> bool:
        raw_text = str(text or "")
        raw_changed = raw_text != self._raw_text
        if raw_changed:
            self._long_answer_expanded = False
        if _verbose_log_enabled():
            logger.info(
                "[ChatBubble] set_content role=%s markdown=%s streaming=%s len=%s has_image=%s preview=%s",
                self._role,
                bool(is_markdown),
                bool(streaming),
                len(raw_text),
                has_markdown_image(raw_text),
                _preview_log_text(raw_text),
            )
        render_key = (raw_text, bool(is_markdown), bool(streaming), bool(getattr(self, "_long_answer_expanded", False)))
        if self._last_render_key == render_key:
            return False
        self._last_render_key = render_key
        self._raw_text = raw_text
        self._is_streaming = bool(streaming)
        if bool(streaming):
            self._stream_width_locked = True
        self._is_placeholder = False
        self._code_blocks = []
        self._reset_follow_up_links()
        render_source = self._display_text_for_render(raw_text, streaming=bool(streaming), is_markdown=bool(is_markdown))
        if is_markdown:
            segments = MarkdownRenderer.split_code_segments(render_source)
            has_code_segment = any(str(segment.get("type")) == "code" for segment in segments)
            has_image = has_markdown_image(render_source)
            if has_code_segment or has_image:
                self._render_markdown_with_code_widgets(segments, streaming=bool(streaming))
            else:
                render_text = self._prepare_follow_up_markdown(render_source)
                started_at = time.perf_counter()
                html = MarkdownRenderer.to_html(
                    render_text,
                    streaming=bool(streaming),
                    code_blocks_out=None if streaming else self._code_blocks,
                    include_code_tools=not bool(streaming),
                )
                _record_stream_perf(
                    self,
                    "markdown_ms",
                    elapsed_ms=(time.perf_counter() - started_at) * 1000.0,
                )
                html = self._inject_follow_up_links(html)
                self._set_single_label_html(html)
        else:
            html = _render_plain_text_with_preview_links(render_source)
            self._set_single_label_html(f'<span style="{_PLAIN_SPAN}">{html}</span>')
        self._sync_expand_answer_button(raw_text, streaming=bool(streaming), is_markdown=bool(is_markdown))
        self._natural_width = None
        self._invalidate_height_cache()
        self._label.updateGeometry()
        self.updateGeometry()
        return True

    def _handle_link_activated(self, url: str) -> None:
        url_str = str(url or "").strip()
        if url_str.startswith("code-copy:"):
            try:
                idx = int(url_str.split(":")[1])
                if hasattr(self, "_code_blocks") and 0 <= idx < len(self._code_blocks):
                    code_text = self._code_blocks[idx]
                    from PyQt6.QtWidgets import QApplication
                    QApplication.clipboard().setText(code_text)
            except Exception:
                pass
        elif url_str.startswith("code-download:"):
            try:
                parts = url_str.split(":", 1)[1].split("-", 1)
                idx = int(parts[0])
                lang = parts[1] if len(parts) > 1 else "txt"
                if hasattr(self, "_code_blocks") and 0 <= idx < len(self._code_blocks):
                    code_text = self._code_blocks[idx]
                    self._download_code_text(code_text, lang)
            except Exception:
                pass
        elif url_str.startswith("code-preview:"):
            try:
                idx = int(url_str.split(":")[1])
                if hasattr(self, "_code_blocks") and 0 <= idx < len(self._code_blocks):
                    code_text = self._code_blocks[idx]
                    self._preview_html_content(code_text)
            except Exception:
                pass
        elif url_str.startswith("deepcat-image-preview:"):
            preview_id = url_str.split(":", 1)[1].strip()
            if preview_id:
                self.image_preview_requested.emit(preview_id)
        elif url_str.startswith(_FOLLOW_UP_LINK_PREFIX):
            token = url_str.split(":", 1)[1].strip()
            query = getattr(self, "_follow_up_queries", {}).get(token, "")
            if query:
                self.follow_up_requested.emit(query)
        else:
            from PyQt6.QtGui import QDesktopServices
            from PyQt6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(url_str))



    def _preview_html_content(self, code_text: str) -> None:
        try:
            import tempfile
            import webbrowser
            import os
            with tempfile.NamedTemporaryFile(suffix=".html", mode="w", encoding="utf-8", delete=False) as f:
                f.write(code_text)
                temp_path = f.name

            webbrowser.open(f"file:///{os.path.abspath(temp_path)}")
        except Exception:
            pass

    def _download_code_text(self, code_text: str, lang: str) -> None:
        try:
            from PyQt6.QtWidgets import QFileDialog
            ext_map = {
                "python": ".py", "py": ".py",
                "javascript": ".js", "js": ".js",
                "typescript": ".ts", "ts": ".ts",
                "html": ".html",
                "css": ".css",
                "cpp": ".cpp", "c++": ".cpp", "c": ".c",
                "java": ".java",
                "go": ".go", "golang": ".go",
                "rust": ".rs", "rs": ".rs",
                "sql": ".sql",
                "json": ".json",
                "xml": ".xml",
                "yaml": ".yaml", "yml": ".yaml",
                "shell": ".sh", "bash": ".sh", "sh": ".sh",
                "powershell": ".ps1", "ps1": ".ps1", "bat": ".bat", "cmd": ".bat",
                "markdown": ".md", "md": ".md",
            }
            ext = ext_map.get(lang.lower(), ".txt")
            filename, _ = QFileDialog.getSaveFileName(
                self,
                "保存代码文件",
                f"code_snippet{ext}",
                f"Files (*{ext});;All Files (*)"
            )
            if filename:
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(code_text)
        except Exception:
            pass

    def set_placeholder(self, text: str) -> None:
        self._raw_text = ""
        self._is_streaming = False
        self._stream_width_locked = False
        self._is_placeholder = True
        if hasattr(self, "_action_toolbar"):
            self._action_toolbar.hide()
        if hasattr(self, "_metadata_label") and self._metadata_label:
            try:
                self._metadata_label.hide()
            except Exception:
                pass
        self._last_render_key = None
        self._set_single_label_html(
            f"<span style=\"color:#94a3b8; font-family:'Microsoft YaHei','Segoe UI',system-ui;"
            f" font-size:14px;\">{_html.escape(text, quote=False)}</span>"
        )
        self._natural_width = None
        self._invalidate_height_cache()
        self._label.updateGeometry()
        self.updateGeometry()

    def raw_text(self) -> str:
        return self._raw_text

    def role(self) -> str:
        return self._role

    def is_streaming(self) -> bool:
        return bool(self._is_streaming)


class _ConversationNavBar(QWidget):
    """右侧会话导航条：轻量绘制刻度，hover 时显示问答摘要。"""

    def __init__(self, owner: "BubbleListView") -> None:
        super().__init__(owner.viewport())
        self._owner = owner
        self._items: list[_ConversationNavItem] = []
        self._hover_index = -1
        self._active_index = -1
        self._card = QFrame(owner.viewport())
        self._card.setObjectName("ConversationNavPreview")
        self._card.setStyleSheet(
            "QFrame#ConversationNavPreview {"
            "  background: #ffffff;"
            "  border: 1px solid #e5e7eb;"
            "  border-radius: 10px;"
            "}"
            "QLabel {"
            "  background: transparent;"
            "  color: #6b7280;"
            "  font-family: 'Microsoft YaHei', 'Segoe UI', system-ui;"
            "  font-size: 12px;"
            "  line-height: 150%;"
            "}"
        )
        self._card_title = QLabel(self._card)
        self._card_title.setWordWrap(True)
        self._card_title.setStyleSheet("color:#111827; font-size:13px; font-weight:800; background:transparent;")
        self._card_meta = QLabel(self._card)
        self._card_meta.setStyleSheet("color:#94a3b8; font-size:11px; background:transparent;")
        self._card_body = QLabel(self._card)
        self._card_body.setWordWrap(True)
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(6)
        card_layout.addWidget(self._card_title)
        card_layout.addWidget(self._card_meta)
        card_layout.addWidget(self._card_body)
        self._card.hide()

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.hide()

    def set_items(self, items: list[_ConversationNavItem]) -> None:
        self._items = list(items or [])
        self._hover_index = -1
        self._sync_visibility()
        self.update()

    def update_active_from_scroll(self) -> None:
        if not self._items:
            self._active_index = -1
            self.update()
            return
        scrollbar = self._owner.verticalScrollBar()
        if scrollbar is None:
            return
        scroll_y = int(scrollbar.value())
        active = 0
        for idx, item in enumerate(self._items):
            if int(item.target_y) <= scroll_y + 36:
                active = idx
            else:
                break
        if active != self._active_index:
            self._active_index = active
            self.update()

    def refresh_geometry(self) -> None:
        viewport = self._owner.viewport()
        if viewport is None:
            return
        width = 22
        right_pad = 4
        self.setGeometry(max(0, viewport.width() - width - right_pad), 8, width, max(1, viewport.height() - 16))
        self.raise_()
        self._sync_visibility()
        self.update()

    def _sync_visibility(self) -> None:
        try:
            scrollbar = self._owner.verticalScrollBar()
            can_scroll = int(scrollbar.maximum()) > 0 if scrollbar is not None else False
        except Exception:
            can_scroll = False
        visible = len(self._items) >= 1 and can_scroll and self._owner.isVisible()
        self.setVisible(bool(visible))
        if not visible:
            self._card.hide()

    def _marker_x(self, idx: int) -> tuple[int, int]:
        if self._hover_index < 0:
            return 8, 14  # 默认宽度 6px，围绕 22px 命中区中心线对称
        distance = abs(int(idx) - int(self._hover_index))
        if distance == 0:
            return 1, 21   # 宽度 20px
        if distance == 1:
            return 2, 20   # 宽度 18px
        if distance == 2:
            return 3, 19   # 宽度 16px
        if distance == 3:
            return 4, 18   # 宽度 14px
        return 8, 14       # 默认宽度 6px

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._items:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        normal_pen = QPen(QColor("#c9cdd3"), 1)
        neighbor_pen = QPen(QColor("#8f98a6"))
        neighbor_pen.setWidthF(1.4)
        hover_pen = QPen(QColor("#111827"), 2)
        for idx, item in enumerate(self._items):
            y = int(item.marker_y)
            if idx == self._hover_index:
                painter.setPen(hover_pen)
            elif self._hover_index >= 0 and abs(idx - self._hover_index) <= 3:
                painter.setPen(neighbor_pen)
            else:
                painter.setPen(normal_pen)
            x1, x2 = self._marker_x(idx)
            painter.drawLine(x1, y, x2, y)
        painter.end()

    def _index_at_y(self, y: int) -> int:
        if not self._items:
            return -1
        best_idx = -1
        best_dist = 9999
        for idx, item in enumerate(self._items):
            dist = abs(int(item.marker_y) - int(y))
            if dist < best_dist:
                best_idx = idx
                best_dist = dist
        return best_idx if best_dist <= 9 else -1

    def _show_card(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._items):
            old_rect = self._card.geometry().adjusted(-3, -3, 3, 3)
            self._card.hide()
            if self._owner.viewport():
                self._owner.viewport().update(old_rect)
            return
        item = self._items[idx]
        question = _html.escape(item.question or "问题", quote=False)
        answer = _html.escape(item.answer or "暂无回答摘要", quote=False)
        self._card_title.setText(f"{idx + 1}. {question}")
        self._card_body.setText(answer)

        # 组装并设置元信息标签内容（时间与位置百分比）
        meta_parts = []
        if item.time_str:
            meta_parts.append(item.time_str)
        total_h = self._owner.widget().height() if self._owner.widget() else 1
        percent = min(100, max(0, int(round(item.target_y / max(1, total_h) * 100))))
        meta_parts.append(f"位置 {percent}%")
        self._card_meta.setText("  ·  ".join(meta_parts))

        card_w = min(360, max(260, self._owner.viewport().width() - 82))
        self._card.setFixedWidth(card_w)
        self._card.adjustSize()
        card_h = min(150, max(78, self._card.sizeHint().height()))
        self._card.setFixedHeight(card_h)

        nav_pos = self.mapTo(self._owner.viewport(), QPoint(0, 0))
        y = nav_pos.y() + int(self._items[idx].marker_y) - card_h // 2
        y = max(8, min(y, max(8, self._owner.viewport().height() - card_h - 8)))
        x = max(8, nav_pos.x() - card_w - 8)

        # 为防止残影，记录旧区域并刷新父窗口
        old_rect = self._card.geometry().adjusted(-3, -3, 3, 3)
        self._card.move(x, y)
        self._card.show()
        self._card.raise_()

        if self._owner.viewport():
            self._owner.viewport().update(old_rect)
            self._owner.viewport().update(self._card.geometry().adjusted(-3, -3, 3, 3))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        idx = self._index_at_y(int(event.position().y()))
        if idx != self._hover_index:
            self._hover_index = idx
            self.update()
        self._show_card(idx)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_index = -1
        old_rect = self._card.geometry().adjusted(-3, -3, 3, 3)
        self._card.hide()
        if self._owner.viewport():
            self._owner.viewport().update(old_rect)
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._index_at_y(int(event.position().y()))
            if idx >= 0:
                self._owner.scroll_to_conversation_nav_item(idx)
                event.accept()
                return
        super().mousePressEvent(event)


class BubbleListView(QScrollArea):
    """可滚动的气泡列表，作为回答区的根控件（替换原 _translation_container）。"""

    copy_requested = pyqtSignal(int)
    delete_requested = pyqtSignal(int)
    regenerate_requested = pyqtSignal(int)
    copy_error_requested = pyqtSignal(int)
    switch_model_retry_requested = pyqtSignal(int)
    new_round_from_error_requested = pyqtSignal(int)
    add_to_note_requested = pyqtSignal(int)
    image_preview_requested = pyqtSignal(str)
    follow_up_requested = pyqtSignal(str)
    edit_from_requested = pyqtSignal(int)
    branch_from_requested = pyqtSignal(int)
    pin_context_requested = pyqtSignal(int)
    placeholder_card_clicked = pyqtSignal(str, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("BubbleListView")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        _vbar = self.verticalScrollBar()
        if _vbar is not None:
            _vbar.setCursor(Qt.CursorShape.ArrowCursor)

        self._container = QWidget()
        self._container.setObjectName("BubbleListContainer")
        self._container.installEventFilter(self)
        self._container.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        self._vbox = QVBoxLayout(self._container)
        self._vbox.setContentsMargins(10, 10, 10, 10)
        self._vbox.setSpacing(10)
        self._vbox.addStretch(1)
        self.setWidget(self._container)

        self._bubbles: list[ChatBubble] = []
        self._last_ai: Optional[ChatBubble] = None
        self._block_refresh = False
        self._is_batch_rendering = False
        self._pending_keep_bottom = False
        self._layout_flush_timer = QTimer(self)
        self._layout_flush_timer.setSingleShot(True)
        self._layout_flush_timer.timeout.connect(self._flush_pending_layout)
        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setSingleShot(True)
        self._stream_flush_timer.timeout.connect(self._flush_pending_stream_updates)
        self._pending_stream_updates: dict[tuple[str, int], dict[str, object]] = {}
        self._last_stream_flush_at = 0.0
        now = time.perf_counter()
        self._stream_perf = _StreamRenderPerf(window_started_at=now)
        self._last_stream_perf_snapshot: dict[str, float | int] = {}
        self._scroll_to_bottom_pending = False
        self._scroll_generation = 0
        self._locked_scroll_val = None
        self._stream_follow_bottom_until = 0.0
        self._viewport_resize_scroll_anchor: Optional[dict[str, float | int | bool]] = None
        self._restoring_viewport_resize_scroll = False
        # 尾部优先渐进渲染状态：切换长会话时先同步渲染最新若干条，其余历史消息
        # 分批异步插入顶部（视口按“距底部距离”补偿，画面保持静止）。
        self._progressive_generation = 0
        self._progressive_state: Optional[dict] = None
        # 渐进插入期间的视口钉住信息：QScrollArea 的 range 更新可能晚于布局激活
        # 一个事件循环，同步补偿不一定生效，因此在 rangeChanged 里按记录的
        # “距底部距离”二次钉住（dist 随用户滚动实时刷新，expire 到期自动失效）。
        self._progressive_scroll_pin: Optional[dict] = None
        self._conversation_nav = _ConversationNavBar(self)
        _vbar_init = self.verticalScrollBar()
        if _vbar_init is not None:
            _vbar_init.valueChanged.connect(self._handle_scroll_value_changed)
            _vbar_init.rangeChanged.connect(self._handle_scroll_range_changed)
        _vp_init = self.viewport()
        if _vp_init is not None:
            try:
                _vp_init.installEventFilter(self)
            except Exception:
                pass

        self.setStyleSheet(
            "QScrollArea#BubbleListView { background:#f8fafc; border:1px solid #e2e8f0; border-radius:0px; }"
            " QWidget#BubbleListContainer { background:#f8fafc; }"
            " QScrollBar:vertical { background: transparent; width: 6px; margin: 0px; }"
            " QScrollBar::handle:vertical { background: #f1f5f9; min-height: 24px; border-radius: 3px; margin: 0px; }"
            " QScrollBar::handle:vertical:hover { background: #94a3b8; }"
            " QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; width: 0px; }"
            " QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
        )

        self._placeholder = None
        self._update_placeholder_visibility()

    def eventFilter(self, watched, event) -> bool:
        if watched is getattr(self, "_container", None):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                container_pos = event.position().toPoint()
                for bubble in list(getattr(self, "_bubbles", []) or []):
                    try:
                        bubble_top_left = bubble.mapTo(self._container, QPoint(0, 0))
                        gap = bubble_top_left.x() - container_pos.x()
                        if not (0 < gap <= 12):
                            continue
                        bubble_local_y = bubble.mapFrom(self._container, container_pos).y()
                        box_local_y = bubble._bubble_box.mapFrom(bubble, QPoint(0, bubble_local_y)).y()
                        label = bubble._text_label_at_left_padding(QPoint(0, box_local_y))
                        if label is None:
                            continue
                        box_global = event.globalPosition().toPoint()
                        box_local = bubble._bubble_box.mapFromGlobal(box_global)
                        box_local.setX(0)
                        forwarded = QMouseEvent(
                            QEvent.Type.MouseButtonPress,
                            QPointF(box_local),
                            QPointF(box_global),
                            Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton,
                            event.modifiers(),
                        )
                        if bubble.eventFilter(bubble._bubble_box, forwarded):
                            return True
                    except RuntimeError:
                        continue
        try:
            if watched is self.viewport() and event.type() == QEvent.Type.Paint:
                self._record_stream_perf("paint_events")
        except Exception:
            pass
        return super().eventFilter(watched, event)

    def _record_stream_perf(self, metric: str, *, elapsed_ms: float = 0.0, count: int = 1) -> None:
        perf = getattr(self, "_stream_perf", None)
        if perf is None:
            return
        name = str(metric or "")
        amount = int(max(1, count))
        if name == "delta_events":
            perf.delta_events += amount
        elif name == "ui_flushes":
            perf.ui_flushes += amount
        elif name == "markdown_ms":
            perf.markdown_renders += amount
            perf.markdown_ms += max(0.0, float(elapsed_ms or 0.0))
        elif name == "code_update_ms":
            perf.code_updates += amount
            perf.code_update_ms += max(0.0, float(elapsed_ms or 0.0))
        elif name == "layout_flushes":
            perf.layout_flushes += amount
        elif name == "scroll_to_bottom_calls":
            perf.scroll_to_bottom_calls += amount
        elif name == "paint_events":
            perf.paint_events += amount
        else:
            return

        now = time.perf_counter()
        elapsed = max(0.001, now - float(perf.window_started_at or now))
        if elapsed < 1.0:
            return
        snapshot = {
            "delta_per_sec": round(perf.delta_events / elapsed, 2),
            "flushes": perf.ui_flushes,
            "markdown_renders": perf.markdown_renders,
            "markdown_ms": round(perf.markdown_ms, 2),
            "code_updates": perf.code_updates,
            "code_update_ms": round(perf.code_update_ms, 2),
            "layout_flushes": perf.layout_flushes,
            "scroll_to_bottom": perf.scroll_to_bottom_calls,
            "paint_events": perf.paint_events,
        }
        self._last_stream_perf_snapshot = snapshot
        logger.info("[ChatPerf] %s", snapshot)
        perf.reset(now)

    def stream_perf_snapshot(self) -> dict[str, float | int]:
        perf = getattr(self, "_stream_perf", None)
        if perf is None:
            return {}
        now = time.perf_counter()
        elapsed = max(0.001, now - float(perf.window_started_at or now))
        return {
            "delta_per_sec": round(perf.delta_events / elapsed, 2),
            "flushes": perf.ui_flushes,
            "markdown_renders": perf.markdown_renders,
            "markdown_ms": round(perf.markdown_ms, 2),
            "code_updates": perf.code_updates,
            "code_update_ms": round(perf.code_update_ms, 2),
            "layout_flushes": perf.layout_flushes,
            "scroll_to_bottom": perf.scroll_to_bottom_calls,
            "paint_events": perf.paint_events,
        }

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

    def _stream_flush_interval_ms(self, text: str) -> int:
        value = str(text or "")
        if self._stream_markdown_has_open_code_fence(value):
            return 130
        n = len(value)
        if n > 20000:
            return 340
        if n > 12000:
            return 280
        if n > 6000:
            return 220
        return 140

    def _should_defer_stream_updates(self) -> bool:
        if getattr(self, "_locked_scroll_val", None) is None:
            return False
        try:
            return not self.is_at_bottom(48)
        except Exception:
            return True

    def _schedule_stream_flush(self, text: str) -> None:
        interval = self._stream_flush_interval_ms(text)
        now = time.monotonic()
        last_flush = float(getattr(self, "_last_stream_flush_at", 0.0) or 0.0)
        remaining = max(0, int(interval - ((now - last_flush) * 1000.0))) if last_flush > 0 else interval
        if self._stream_flush_timer.isActive():
            return
        self._stream_flush_timer.start(max(1, remaining))

    def _queue_stream_update(
        self,
        key: tuple[str, int],
        text: str,
        *,
        is_markdown: bool,
        model_name: Optional[str],
        elapsed: Optional[float],
        reply_tokens: Optional[int],
        total_tokens: Optional[int],
        start_time: Optional[str],
    ) -> None:
        payload = {
            "text": str(text or ""),
            "is_markdown": bool(is_markdown),
            "model_name": model_name,
            "elapsed": elapsed,
            "reply_tokens": reply_tokens,
            "total_tokens": total_tokens,
            "start_time": start_time,
        }
        self._pending_stream_updates[key] = payload
        self._record_stream_perf("delta_events")
        if self._should_defer_stream_updates():
            return
        self._schedule_stream_flush(str(text or ""))

    def _flush_pending_stream_updates(self) -> None:
        pending = dict(getattr(self, "_pending_stream_updates", {}) or {})
        if not pending:
            return
        if self._should_defer_stream_updates():
            return
        self._pending_stream_updates.clear()
        self._last_stream_flush_at = time.monotonic()
        self._record_stream_perf("ui_flushes")
        for key, payload in pending.items():
            kind, index = key
            if kind == "last":
                self._apply_update_last_ai_now(
                    str(payload.get("text", "") or ""),
                    is_markdown=bool(payload.get("is_markdown", True)),
                    streaming=True,
                    model_name=payload.get("model_name") if payload.get("model_name") is not None else None,
                    elapsed=payload.get("elapsed") if payload.get("elapsed") is not None else None,
                    reply_tokens=payload.get("reply_tokens") if payload.get("reply_tokens") is not None else None,
                    total_tokens=payload.get("total_tokens") if payload.get("total_tokens") is not None else None,
                    start_time=payload.get("start_time") if payload.get("start_time") is not None else None,
                )
            elif kind == "index":
                self._apply_update_message_at_now(
                    int(index),
                    str(payload.get("text", "") or ""),
                    is_markdown=bool(payload.get("is_markdown", True)),
                    streaming=True,
                    model_name=payload.get("model_name") if payload.get("model_name") is not None else None,
                    elapsed=payload.get("elapsed") if payload.get("elapsed") is not None else None,
                    reply_tokens=payload.get("reply_tokens") if payload.get("reply_tokens") is not None else None,
                    total_tokens=payload.get("total_tokens") if payload.get("total_tokens") is not None else None,
                    start_time=payload.get("start_time") if payload.get("start_time") is not None else None,
                )

    def _flush_stream_update_for_index(self, index: int) -> None:
        key = ("index", int(index))
        pending = getattr(self, "_pending_stream_updates", {}) or {}
        if key not in pending:
            return
        payload = pending.pop(key)
        self._last_stream_flush_at = time.monotonic()
        self._record_stream_perf("ui_flushes")
        self._apply_update_message_at_now(
            int(index),
            str(payload.get("text", "") or ""),
            is_markdown=bool(payload.get("is_markdown", True)),
            streaming=True,
            model_name=payload.get("model_name") if payload.get("model_name") is not None else None,
            elapsed=payload.get("elapsed") if payload.get("elapsed") is not None else None,
            reply_tokens=payload.get("reply_tokens") if payload.get("reply_tokens") is not None else None,
            total_tokens=payload.get("total_tokens") if payload.get("total_tokens") is not None else None,
            start_time=payload.get("start_time") if payload.get("start_time") is not None else None,
        )

    def resizeEvent(self, event) -> None:
        keep_bottom = self.is_at_bottom(24)
        super().resizeEvent(event)
        self.refresh_layout(keep_bottom=keep_bottom)
        self._update_conversation_nav()

    def _history_switch_should_keep_bottom(self) -> bool:
        try:
            force_until = float(getattr(self, "_force_keep_bottom_until", 0.0) or 0.0)
        except Exception:
            force_until = 0.0
        return bool(force_until > time.monotonic())

    def _update_bubble_stretch_and_policy(self, bubble: ChatBubble, vw: int, max_w: int) -> None:
        bubble.setMaximumWidth(max_w)
        raw_text = bubble.raw_text()
        force_wide = (
            bubble.role() != "user"
            and not bool(getattr(bubble, "_is_placeholder", False))
            and (
                bool(getattr(bubble, "_is_streaming", False))
                or bool(getattr(bubble, "_stream_width_locked", False))
                or len(raw_text) > 600
                or "```" in raw_text
                or "~~~" in raw_text
                or has_markdown_image(raw_text)
            )
        )
        if force_wide:
            bubble.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred))
            bubble.set_measure_width(max_w)
            bubble.setMinimumHeight(max(1, bubble.heightForWidth(max_w)))
            lay = self._vbox
            if lay is not None:
                for i in range(lay.count()):
                    item = lay.itemAt(i)
                    if item is None:
                        continue
                    sub_lay = item.layout()
                    if isinstance(sub_lay, QHBoxLayout):
                        for j in range(sub_lay.count()):
                            w_item = sub_lay.itemAt(j)
                            if w_item is not None and w_item.widget() is bubble:
                                sub_lay.setStretch(j, 10)
            bubble.updateGeometry()
            return

        # 缓存机制：避免重复频繁测绘无折行状态的 QLabel
        if getattr(bubble, "_natural_width", None) is None:
            bubble._label.setWordWrap(False)
            natural_w = bubble._label.sizeHint().width() + 32
            bubble._label.setWordWrap(True)
            bubble._natural_width = natural_w
        else:
            natural_w = int(bubble._natural_width or 0)

        bubble.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred))
        stretch_val = 0

        measure_w = min(max_w, max(1, natural_w))
        bubble.set_measure_width(measure_w)
        bubble.setMinimumHeight(max(1, bubble.heightForWidth(measure_w)))

        # 动态更新其在 QHBoxLayout 中的 stretch factor
        lay = self._vbox
        if lay is not None:
            for i in range(lay.count()):
                item = lay.itemAt(i)
                if item is not None:
                    sub_lay = item.layout()
                    if isinstance(sub_lay, QHBoxLayout):
                        for j in range(sub_lay.count()):
                            w_item = sub_lay.itemAt(j)
                            if w_item is not None and w_item.widget() is bubble:
                                sub_lay.setStretch(j, stretch_val)

        bubble.updateGeometry()

    def _refresh_all_bubbles(self) -> None:
        max_w = self._max_bubble_width()
        vp = self.viewport()
        vw = vp.width() if vp is not None else self.width()
        for bubble in self._bubbles:
            self._update_bubble_stretch_and_policy(bubble, vw, max_w)

    def _invalidate_bubble_width_cache(self) -> None:
        for bubble in list(getattr(self, "_bubbles", []) or []):
            try:
                bubble._natural_width = None
            except Exception:
                pass

    # ---- 内部 ----
    def _active_progressive_pin(self) -> Optional[dict]:
        pin = getattr(self, "_progressive_scroll_pin", None)
        if pin is None:
            return None
        if time.monotonic() > float(pin.get("expire", 0.0) or 0.0):
            self._progressive_scroll_pin = None
            return None
        return pin

    def _handle_scroll_value_changed(self, *_args) -> None:
        bar = self.verticalScrollBar()
        if bar is not None:
            if bool(getattr(self, "_restoring_viewport_resize_scroll", False)):
                self._refresh_conversation_nav_active()
                return
            pin = self._active_progressive_pin()
            if pin is not None:
                # 用户在渐进补齐期间滚动时，实时刷新应保持的“距底部距离”
                pin["dist"] = max(0, int(bar.maximum()) - int(bar.value()))
            if not self.is_at_bottom(48):
                self._viewport_resize_scroll_anchor = None
                self._stream_follow_bottom_until = 0.0
                self._pending_keep_bottom = False
                if bool(getattr(self, "_scroll_to_bottom_pending", False)):
                    self._scroll_generation += 1
                    self._scroll_to_bottom_pending = False
                # 实时更新被锁定的滚动数值为用户手动滚动的最新位置
                self._locked_scroll_val = bar.value()
            else:
                # 滚回最底部时，解除锁定，恢复正常的跟随滚动
                self._locked_scroll_val = None
                flush_pending = getattr(self, "_flush_pending_stream_updates", None)
                if callable(flush_pending):
                    flush_pending()
        self._refresh_conversation_nav_active()

    def _handle_scroll_range_changed(self, min_val, max_val) -> None:
        self._update_conversation_nav()
        bar = self.verticalScrollBar()
        resize_anchor = getattr(self, "_viewport_resize_scroll_anchor", None)
        if isinstance(resize_anchor, dict):
            expires_at = float(resize_anchor.get("expires_at", 0.0) or 0.0)
            if expires_at > time.monotonic() and bar is not None:
                if bool(resize_anchor.get("at_bottom", False)):
                    target_value = int(max_val)
                else:
                    distance = int(resize_anchor.get("distance_from_bottom", 0) or 0)
                    target_value = max(int(min_val), int(max_val) - max(0, distance))
                self._restoring_viewport_resize_scroll = True
                try:
                    bar.setValue(target_value)
                finally:
                    self._restoring_viewport_resize_scroll = False
                return
            self._viewport_resize_scroll_anchor = None
        follow_until = float(getattr(self, "_stream_follow_bottom_until", 0.0) or 0.0)
        if follow_until > time.monotonic() and bar is not None:
            bar.setValue(int(max_val))
            return
        pin = self._active_progressive_pin()
        if pin is not None and bar is not None:
            # 渐进补齐把内容插到视口上方，range 扩大时按记录的“距底部距离”
            # 钉住画面；此 setValue 触发的 valueChanged 会把 dist 原样刷回，自洽
            bar.setValue(max(int(bar.minimum()), int(max_val) - int(pin.get("dist", 0) or 0)))
            return
        locked = getattr(self, "_locked_scroll_val", None)
        if locked is not None:
            if bar is not None:
                # 阻止任何由于 range 发生改变（如气泡重算）引发的被动 value 截断偏移，强制锁死在历史设定值
                bar.setValue(locked)

    def _schedule_layout_refresh(self, *, keep_bottom: bool = False, delay_ms: int = 0) -> None:
        if getattr(self, "_block_refresh", False):
            return
        if bool(keep_bottom):
            self._pending_keep_bottom = True
        elif not self.is_at_bottom(48):
            self._pending_keep_bottom = False
        if self._layout_flush_timer.isActive():
            return
        self._layout_flush_timer.start(max(0, int(delay_ms)))

    def _schedule_media_layout_stabilization(self) -> None:
        self._media_layout_retry_count = 0
        for delay_ms in (0, 16, 60, 120):
            QTimer.singleShot(delay_ms, self._stabilize_media_layout)

    def _retry_media_layout_stabilization(self) -> None:
        self._media_layout_retry_pending = False
        self._stabilize_media_layout()

    def _stabilize_media_layout(self) -> None:
        if getattr(self, "_block_refresh", False) or getattr(self, "_is_batch_rendering", False):
            retry_count = int(getattr(self, "_media_layout_retry_count", 0) or 0)
            if retry_count >= 12 or bool(getattr(self, "_media_layout_retry_pending", False)):
                return
            self._media_layout_retry_count = retry_count + 1
            self._media_layout_retry_pending = True
            retry_delay = min(400, 60 + retry_count * 40)
            QTimer.singleShot(retry_delay, self._retry_media_layout_stabilization)
            return
        self._media_layout_retry_count = 0
        self._media_layout_retry_pending = False
        for bubble in list(getattr(self, "_bubbles", []) or []):
            try:
                bubble._natural_width = None
                invalidate_height = getattr(bubble, "_invalidate_height_cache", None)
                if callable(invalidate_height):
                    invalidate_height()
                content_layout = getattr(bubble, "_content_layout", None)
                if content_layout is not None:
                    content_layout.invalidate()
                box = getattr(bubble, "_bubble_box", None)
                if box is not None:
                    box_layout = box.layout()
                    if box_layout is not None:
                        box_layout.invalidate()
                    box.updateGeometry()
                bubble.updateGeometry()
            except RuntimeError:
                continue
            except Exception:
                logger.exception("[ChatBubble] stabilize_media_layout.bubble_failed")
        try:
            self.refresh_layout(keep_bottom=True)
            self.scroll_to_bottom()
        except RuntimeError:
            pass
        except Exception:
            logger.exception("[ChatBubble] stabilize_media_layout.failed")
        try:
            panel = self.window()
        except Exception:
            panel = None
        if panel is not None and hasattr(panel, "_reposition"):
            try:
                getattr(panel, "_reposition")()
            except RuntimeError:
                pass
            except Exception:
                logger.exception("[ChatBubble] stabilize_media_layout.reposition_failed")

    def _flush_pending_layout(self) -> None:
        if getattr(self, "_block_refresh", False) or getattr(self, "_is_batch_rendering", False):
            return
        self._record_stream_perf("layout_flushes")
        keep_bottom = bool(self._pending_keep_bottom)
        self._pending_keep_bottom = False
        try:
            self._vbox.invalidate()
            self._vbox.activate()
            self._container.updateGeometry()
        except Exception:
            pass
        if keep_bottom:
            self.scroll_to_bottom()
        self._update_conversation_nav()

    def _apply_stream_layout_atomically(self, *, keep_bottom: bool) -> None:
        """在恢复绘制前一次性提交尺寸、布局和滚动位置，避免中间状态闪现。"""
        try:
            self._layout_flush_timer.stop()
            self._pending_keep_bottom = False
        except Exception:
            pass
        self._record_stream_perf("layout_flushes")
        try:
            self._vbox.invalidate()
            self._vbox.activate()
            self._container.updateGeometry()
        except Exception:
            pass
        if bool(keep_bottom):
            self._stream_follow_bottom_until = time.monotonic() + 0.8
            bar = self.verticalScrollBar()
            if bar is not None:
                bar.setValue(bar.maximum())

    def capture_viewport_scroll_anchor(self) -> dict[str, int | bool]:
        bar = self.verticalScrollBar()
        if bar is None:
            return {"at_bottom": True, "distance_from_bottom": 0}
        maximum = int(bar.maximum())
        value = int(bar.value())
        return {
            "at_bottom": value >= maximum - 48,
            "distance_from_bottom": max(0, maximum - value),
        }

    def refresh_for_viewport_resize(self, scroll_anchor: Optional[dict] = None) -> None:
        """视口宽度变化时一次性完成全量测量、布局和滚动恢复。"""
        anchor = dict(scroll_anchor or self.capture_viewport_scroll_anchor())
        anchor["expires_at"] = time.monotonic() + 0.8
        self._viewport_resize_scroll_anchor = anchor
        previous_updates = bool(self.updatesEnabled())
        if previous_updates:
            self.setUpdatesEnabled(False)
        try:
            try:
                self._layout_flush_timer.stop()
                self._pending_keep_bottom = False
            except Exception:
                pass
            self._record_stream_perf("layout_flushes")
            self._refresh_all_bubbles()
            try:
                self._vbox.invalidate()
                self._vbox.activate()
                self._container.updateGeometry()
            except Exception:
                pass

            bar = self.verticalScrollBar()
            if bar is not None:
                maximum = int(bar.maximum())
                if bool(anchor.get("at_bottom", False)):
                    target_value = maximum
                else:
                    distance = int(anchor.get("distance_from_bottom", 0) or 0)
                    target_value = max(int(bar.minimum()), maximum - max(0, distance))
                self._restoring_viewport_resize_scroll = True
                try:
                    bar.setValue(target_value)
                finally:
                    self._restoring_viewport_resize_scroll = False
        finally:
            if previous_updates:
                self.setUpdatesEnabled(True)
                viewport = self.viewport()
                if viewport is not None:
                    viewport.update()
        self._update_conversation_nav()

    def _apply_bubble_content_update_now(
        self,
        bubble: ChatBubble,
        text: str,
        *,
        is_markdown: bool,
        streaming: bool,
        model_name: Optional[str],
        elapsed: Optional[float],
        reply_tokens: Optional[int],
        total_tokens: Optional[int],
        start_time: Optional[str],
    ) -> bool:
        follow_bottom = self.is_at_bottom(48)
        atomic_render = bool(streaming or getattr(bubble, "_is_streaming", False))
        previous_updates = bool(self.updatesEnabled())
        if atomic_render and previous_updates:
            self.setUpdatesEnabled(False)
        try:
            changed = bubble.set_content(str(text or ""), is_markdown=bool(is_markdown), streaming=bool(streaming))
            bubble.set_metadata(
                start_time=start_time,
                model_name=model_name,
                elapsed=elapsed,
                reply_tokens=reply_tokens,
                total_tokens=total_tokens,
            )
            if not changed and bool(streaming):
                return True

            max_w = self._max_bubble_width()
            viewport = self.viewport()
            viewport_width = viewport.width() if viewport is not None else self.width()
            self._update_bubble_stretch_and_policy(bubble, viewport_width, max_w)
            if atomic_render:
                BubbleListView._apply_stream_layout_atomically(self, keep_bottom=follow_bottom)
            else:
                self._schedule_layout_refresh(keep_bottom=follow_bottom, delay_ms=0)
            if has_markdown_image(str(text or "")) and not bool(streaming):
                self._schedule_media_layout_stabilization()
            return True
        finally:
            if atomic_render and previous_updates:
                self.setUpdatesEnabled(True)
                viewport = self.viewport()
                if viewport is not None:
                    viewport.update()

    def apply_coalesced_stream_message_at(
        self,
        index: int,
        text: str,
        *,
        is_markdown: bool = True,
        model_name: Optional[str] = None,
        elapsed: Optional[float] = None,
        reply_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        start_time: Optional[str] = None,
    ) -> bool:
        """应用已由上层合并的流式快照，不再进入第二层定时队列。"""
        pending = getattr(self, "_pending_stream_updates", None)
        if isinstance(pending, dict):
            pending.pop(("index", int(index)), None)
            bubble = self._bubble_for_message_index(index)
            if bubble is getattr(self, "_last_ai", None):
                pending.pop(("last", -1), None)
            if not pending:
                self._stream_flush_timer.stop()
        return self._apply_update_message_at_now(
            int(index),
            str(text or ""),
            is_markdown=bool(is_markdown),
            streaming=True,
            model_name=model_name,
            elapsed=elapsed,
            reply_tokens=reply_tokens,
            total_tokens=total_tokens,
            start_time=start_time,
        )

    def apply_coalesced_stream_last_ai(
        self,
        text: str,
        *,
        is_markdown: bool = True,
        model_name: Optional[str] = None,
        elapsed: Optional[float] = None,
        reply_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        start_time: Optional[str] = None,
    ) -> None:
        """应用已由上层合并的单回答流式快照。"""
        pending = getattr(self, "_pending_stream_updates", None)
        if isinstance(pending, dict):
            pending.pop(("last", -1), None)
            if not pending:
                self._stream_flush_timer.stop()
        self._apply_update_last_ai_now(
            str(text or ""),
            is_markdown=bool(is_markdown),
            streaming=True,
            model_name=model_name,
            elapsed=elapsed,
            reply_tokens=reply_tokens,
            total_tokens=total_tokens,
            start_time=start_time,
        )

    @staticmethod
    def _conversation_nav_summary_text(text: str, limit: int = 88) -> str:
        value = str(text or "")
        value = re.sub(r"```.*?```", " 代码片段 ", value, flags=re.DOTALL)
        value = re.sub(r"!\[[^\]]*\]\([^)]+\)", " 图片 ", value)
        value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
        value = re.sub(r"📎\s*\[[^\]]+\]\([^)]+\)", " 附件 ", value)
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            return ""
        if len(value) > limit:
            return value[:limit].rstrip() + "..."
        return value

    def _conversation_nav_items(self) -> list[_ConversationNavItem]:
        items: list[_ConversationNavItem] = []
        pending_user: Optional[ChatBubble] = None
        for bubble in self._bubbles:
            if bubble.role() == "user":
                pending_user = bubble
                continue
            if bubble.role() != "assistant" or pending_user is None:
                continue
            question = self._conversation_nav_summary_text(pending_user.raw_text(), 52) or "问题"
            answer = self._conversation_nav_summary_text(bubble.raw_text(), 104) or "回答生成中..."
            time_str = bubble._meta_start_time or pending_user._meta_start_time or ""
            items.append(
                _ConversationNavItem(
                    question=question,
                    answer=answer,
                    target_y=max(0, int(pending_user.y()) - 8),
                    time_str=time_str,
                )
            )
            pending_user = None
        return items

    def _update_conversation_nav(self) -> None:
        nav = getattr(self, "_conversation_nav", None)
        if nav is None:
            return
        try:
            nav.refresh_geometry()
            items = self._conversation_nav_items()
            nav_height = max(1, int(nav.height()))
            item_count = len(items)
            if item_count == 1:
                items[0].marker_y = max(8, min(max(1, nav_height - 8), int(round(nav_height / 2))))
            elif item_count > 1:
                available_height = max(1, nav_height - 16)
                max_gap = 10
                gap = min(max_gap, max(1, int(available_height / max(1, item_count - 1))))
                cluster_height = int(gap * (item_count - 1))
                marker_top = max(8, int(round((nav_height - cluster_height) / 2)))
                marker_top = min(marker_top, max(8, nav_height - cluster_height - 8))
                for idx, item in enumerate(items):
                    item.marker_y = int(marker_top + idx * gap)
            nav.set_items(items)
            self._refresh_conversation_nav_active()
        except RuntimeError:
            pass
        except Exception:
            logger.exception("[ChatBubble] update_conversation_nav.failed")

    def _refresh_conversation_nav_active(self) -> None:
        nav = getattr(self, "_conversation_nav", None)
        if nav is None:
            return
        try:
            nav.update_active_from_scroll()
        except RuntimeError:
            pass
        except Exception:
            pass

    def scroll_to_conversation_nav_item(self, index: int) -> None:
        nav = getattr(self, "_conversation_nav", None)
        if nav is None:
            return
        items = getattr(nav, "_items", [])
        if index < 0 or index >= len(items):
            return
        bar = self.verticalScrollBar()
        if bar is None:
            return
        target_y = int(items[index].target_y)
        bar.setValue(max(bar.minimum(), min(bar.maximum(), target_y)))
        self._refresh_conversation_nav_active()

    def scroll_to_message_index(self, index: int, *, center: bool = True) -> bool:
        self._flush_progressive_now()
        try:
            target_index = int(index)
        except Exception:
            return False
        if target_index < 0:
            return False

        target_bubble = None
        for bubble in getattr(self, "_bubbles", []) or []:
            if int(getattr(bubble, "_msg_index", -1) or -1) == target_index:
                target_bubble = bubble
                break
        if target_bubble is None:
            bubbles = getattr(self, "_bubbles", []) or []
            if 0 <= target_index < len(bubbles):
                target_bubble = bubbles[target_index]
        if target_bubble is None:
            return False

        self._scroll_generation = int(getattr(self, "_scroll_generation", 0)) + 1
        self._scroll_to_bottom_pending = False
        try:
            self._force_keep_bottom_until = 0.0
        except Exception:
            pass
        try:
            self._layout_flush_timer.stop()
            self._pending_keep_bottom = False
            self._vbox.invalidate()
            self._vbox.activate()
            self._container.updateGeometry()
        except Exception:
            pass

        bar = self.verticalScrollBar()
        if bar is None:
            return False
        viewport = self.viewport()
        viewport_height = viewport.height() if viewport is not None else self.height()
        bubble_height = target_bubble.height()
        target_y = int(target_bubble.y())
        if center:
            target_y -= max(0, int((viewport_height - bubble_height) / 2))
        else:
            target_y -= 12
        bar.setValue(max(bar.minimum(), min(bar.maximum(), target_y)))
        self._refresh_conversation_nav_active()
        return True

    def _max_bubble_width(self) -> int:
        vp = self.viewport()
        vw = vp.width() if vp is not None else self.width()

        # 判断划词弹窗是否处于最大化状态
        p = self.window()
        is_max = False
        if p is not None and hasattr(p, "_is_maximized"):
            is_max = bool(getattr(p, "_is_maximized", False))

        if is_max:
            from PyQt6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
            if p is not None:
                screen = QGuiApplication.screenAt(p.geometry().center()) or screen
            if screen:
                screen_w = screen.availableGeometry().width()
                return int(screen_w * 0.60)

        # 默认窗口大小时保持原逻辑不变
        return max(140, int(vw * 0.82))

    def _add_bubble(self, role: str, *, insert_row: Optional[int] = None, list_pos: Optional[int] = None) -> ChatBubble:
        bubble = ChatBubble(role, self._container)
        bubble.image_preview_requested.connect(self.image_preview_requested.emit)
        bubble.follow_up_requested.connect(self.follow_up_requested.emit)
        max_w = self._max_bubble_width()

        vp = self.viewport()
        vw = vp.width() if vp is not None else self.width()

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        if role == "user":
            row.addStretch(1)
            row.addWidget(bubble, 0)
        else:
            row.addWidget(bubble, 0)
            row.addStretch(1)
        if insert_row is None:
            # 插入到末尾 stretch 之前
            self._vbox.insertLayout(self._vbox.count() - 1, row)
            self._bubbles.append(bubble)
            if role != "user":
                self._last_ai = bubble
        else:
            # 渐进补齐历史消息：按指定行号插入顶部区域，不改写 _last_ai
            self._vbox.insertLayout(max(0, int(insert_row)), row)
            pos = int(list_pos if list_pos is not None else insert_row)
            pos = max(0, min(pos, len(self._bubbles)))
            self._bubbles.insert(pos, bubble)

        self._update_bubble_stretch_and_policy(bubble, vw, max_w)
        self._update_placeholder_visibility()
        return bubble

    def _wire_message_bubble(self, bubble: ChatBubble) -> None:
        bubble.copy_requested.connect(self.copy_requested.emit)
        bubble.delete_requested.connect(self.delete_requested.emit)
        bubble.regenerate_requested.connect(self.regenerate_requested.emit)
        bubble.copy_error_requested.connect(self.copy_error_requested.emit)
        bubble.switch_model_retry_requested.connect(self.switch_model_retry_requested.emit)
        bubble.new_round_from_error_requested.connect(self.new_round_from_error_requested.emit)
        bubble.edit_from_requested.connect(self.edit_from_requested.emit)
        bubble.branch_from_requested.connect(self.branch_from_requested.emit)
        bubble.pin_context_requested.connect(self.pin_context_requested.emit)
        if hasattr(bubble, "add_to_note_requested"):
            bubble.add_to_note_requested.connect(self.add_to_note_requested.emit)

    def _content_for_message(self, msg: dict) -> str:
        display_content = str(msg.get("display_content", "") or "")
        return display_content if display_content.strip() else str(msg.get("content", "") or "")

    def _append_message_bubble(
        self,
        idx: int,
        msg: dict,
        accumulated_tokens: int,
        *,
        insert_row: Optional[int] = None,
        list_pos: Optional[int] = None,
    ) -> int:
        role = str(msg.get("role", "assistant") or "assistant")
        content = self._content_for_message(msg)
        msg_tokens = _estimate_tokens(content)
        accumulated_tokens += msg_tokens
        if role == "user":
            bubble = self._add_bubble("user", insert_row=insert_row, list_pos=list_pos)
            bubble.set_msg_index(idx)
            bubble.set_context_pinned(bool(msg.get("context_pinned", False)))
            bubble.set_content(content, is_markdown=False)
        else:
            bubble = self._add_bubble("assistant", insert_row=insert_row, list_pos=list_pos)
            bubble.set_msg_index(idx)
            bubble.set_context_pinned(bool(msg.get("context_pinned", False)))
            if content.strip():
                bubble.set_content(content, is_markdown=True)
            else:
                placeholder = str(msg.get("status_text", "") or "").strip() or "正在思考..."
                bubble.set_placeholder(placeholder)

            model_name = msg.get("model_name")
            elapsed = msg.get("elapsed") or msg.get("elapsed_secs")
            reply_tok = msg.get("reply_tokens")
            total_tok = msg.get("total_tokens")
            created_at = msg.get("created_at") or msg.get("start_time")

            start_time_str = ""
            if created_at:
                try:
                    dt = datetime.strptime(str(created_at).strip(), "%Y-%m-%d %H:%M:%S")
                    start_time_str = dt.strftime("%m/%d %H:%M")
                except Exception:
                    start_time_str = str(created_at)

            if not reply_tok:
                reply_tok = msg_tokens
            if not total_tok:
                total_tok = accumulated_tokens

            bubble.set_metadata(
                start_time=start_time_str,
                model_name=model_name,
                elapsed=elapsed,
                reply_tokens=reply_tok,
                total_tokens=total_tok,
            )
        self._wire_message_bubble(bubble)
        return accumulated_tokens

    def append_messages(self, history, *, start_index: int, keep_bottom: bool = True) -> bool:
        self._flush_progressive_now()
        if bool(keep_bottom):
            self._locked_scroll_val = None
        messages = list(history or [])
        start = int(max(0, start_index))
        if start > len(messages) or start != len(self._bubbles):
            return False
        if start >= len(messages):
            return True

        scrollbar = self.verticalScrollBar()
        previous_updates = bool(self.updatesEnabled())
        _vp = self.viewport()
        previous_viewport_updates = bool(_vp.updatesEnabled()) if _vp is not None else True
        previous_container_updates = bool(self._container.updatesEnabled())
        previous_scroll_signals = bool(scrollbar.signalsBlocked()) if scrollbar is not None else False
        self._layout_flush_timer.stop()
        self._pending_keep_bottom = False
        self._is_batch_rendering = True
        self.setUpdatesEnabled(False)
        if _vp is not None:
            _vp.setUpdatesEnabled(False)
        self._container.setUpdatesEnabled(False)
        if scrollbar is not None:
            scrollbar.blockSignals(True)
        try:
            accumulated_tokens = 0
            for prior in messages[:start]:
                if isinstance(prior, dict):
                    accumulated_tokens += _estimate_tokens(self._content_for_message(prior))
            for idx in range(start, len(messages)):
                msg = messages[idx]
                if not isinstance(msg, dict):
                    continue
                accumulated_tokens = self._append_message_bubble(idx, msg, accumulated_tokens)
            self._vbox.invalidate()
            self._vbox.activate()
            self._container.updateGeometry()
            if bool(keep_bottom) and scrollbar is not None:
                scrollbar.setValue(scrollbar.maximum())
        finally:
            if scrollbar is not None:
                scrollbar.blockSignals(previous_scroll_signals)
            self._container.setUpdatesEnabled(previous_container_updates)
            if _vp is not None:
                _vp.setUpdatesEnabled(previous_viewport_updates)
            self.setUpdatesEnabled(previous_updates)
            self._is_batch_rendering = False

        if bool(keep_bottom):
            self.scroll_to_bottom_now()
        _vp_end = self.viewport()
        if _vp_end is not None:
            _vp_end.update()
        self._update_conversation_nav()
        return True

    def _sync_last_ai(self) -> None:
        self._last_ai = None
        for bubble in reversed(getattr(self, "_bubbles", []) or []):
            if bubble.role() != "user":
                self._last_ai = bubble
                return

    def _row_layout_index_for_bubble(self, target_bubble: ChatBubble) -> int:
        for i in range(max(0, self._vbox.count() - 1)):
            item = self._vbox.itemAt(i)
            row = item.layout() if item is not None else None
            if row is None:
                continue
            for j in range(row.count()):
                child = row.itemAt(j)
                if child is not None and child.widget() is target_bubble:
                    return i
        return -1

    def _remove_bubble_widget(self, target_bubble: ChatBubble) -> bool:
        row_index = self._row_layout_index_for_bubble(target_bubble)
        if row_index < 0:
            return False
        item = self._vbox.takeAt(row_index)
        row = item.layout() if item is not None else None
        if row is not None:
            self._clear_layout(row)
        else:
            target_bubble.hide()
            target_bubble.deleteLater()
        try:
            self._bubbles.remove(target_bubble)
        except ValueError:
            pass
        return True

    def _bubble_for_message_index(self, index: int) -> Optional[ChatBubble]:
        self._flush_progressive_now()
        try:
            target_index = int(index)
        except Exception:
            return None
        for bubble in getattr(self, "_bubbles", []) or []:
            try:
                msg_index = int(getattr(bubble, "_msg_index", -1))
            except Exception:
                msg_index = -1
            if msg_index == target_index:
                return bubble
        return None

    def _apply_message_to_bubble(self, bubble: ChatBubble, index: int, msg: dict) -> None:
        bubble.set_msg_index(int(index))
        bubble.set_context_pinned(bool(msg.get("context_pinned", False)))
        role = str(msg.get("role", "assistant") or "assistant")
        content = self._content_for_message(msg)
        if role == "user":
            bubble.set_content(content, is_markdown=False)
            return
        if content.strip():
            bubble.set_content(content, is_markdown=True)
        else:
            placeholder = str(msg.get("status_text", "") or "").strip() or "正在思考..."
            bubble.set_placeholder(placeholder)
        bubble.set_metadata(
            start_time=msg.get("created_at") or msg.get("start_time"),
            model_name=msg.get("model_name"),
            elapsed=msg.get("elapsed") or msg.get("elapsed_secs"),
            reply_tokens=msg.get("reply_tokens"),
            total_tokens=msg.get("total_tokens"),
        )

    def _finish_local_message_change(self, *, keep_bottom: bool) -> None:
        self._sync_last_ai()
        self._refresh_all_bubbles()
        try:
            self._vbox.invalidate()
            self._vbox.activate()
            self._container.updateGeometry()
        except Exception:
            pass
        self.viewport().update()
        self._update_conversation_nav()
        if bool(keep_bottom):
            self.scroll_to_bottom_now()

    def remove_message_at(self, index: int, *, keep_bottom: bool = False) -> bool:
        bubble = self._bubble_for_message_index(index)
        if bubble is None:
            return False
        try:
            removed_index = int(getattr(bubble, "_msg_index", index))
        except Exception:
            removed_index = int(index)
        if not self._remove_bubble_widget(bubble):
            return False
        for item in getattr(self, "_bubbles", []) or []:
            try:
                msg_index = int(getattr(item, "_msg_index", -1))
            except Exception:
                msg_index = -1
            if msg_index > removed_index:
                item.set_msg_index(msg_index - 1)
        self._scroll_generation += 1
        self._scroll_to_bottom_pending = False
        self._finish_local_message_change(keep_bottom=bool(keep_bottom))
        return True

    def replace_message_at(self, index: int, msg: dict, *, keep_bottom: bool = False) -> bool:
        bubble = self._bubble_for_message_index(index)
        if bubble is None or not isinstance(msg, dict):
            return False
        if bubble.role() != str(msg.get("role", "assistant") or "assistant"):
            return False
        self._apply_message_to_bubble(bubble, int(index), msg)
        self._finish_local_message_change(keep_bottom=bool(keep_bottom))
        return True

    def update_message_at(
        self,
        index: int,
        text: str,
        *,
        is_markdown: bool = True,
        streaming: bool = False,
        model_name: Optional[str] = None,
        elapsed: Optional[float] = None,
        reply_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        start_time: Optional[str] = None,
    ) -> bool:
        if bool(streaming) and callable(getattr(self, "_queue_stream_update", None)):
            if self._bubble_for_message_index(index) is None:
                return False
            self._queue_stream_update(
                ("index", int(index)),
                str(text or ""),
                is_markdown=bool(is_markdown),
                model_name=model_name,
                elapsed=elapsed,
                reply_tokens=reply_tokens,
                total_tokens=total_tokens,
                start_time=start_time,
            )
            return True
        flush_pending = getattr(self, "_flush_pending_stream_updates", None)
        if callable(flush_pending):
            flush_pending()
        return BubbleListView._apply_update_message_at_now(
            self,
            index,
            text,
            is_markdown=is_markdown,
            streaming=streaming,
            model_name=model_name,
            elapsed=elapsed,
            reply_tokens=reply_tokens,
            total_tokens=total_tokens,
            start_time=start_time,
        )

    def _apply_update_message_at_now(
        self,
        index: int,
        text: str,
        *,
        is_markdown: bool = True,
        streaming: bool = False,
        model_name: Optional[str] = None,
        elapsed: Optional[float] = None,
        reply_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        start_time: Optional[str] = None,
    ) -> bool:
        bubble = self._bubble_for_message_index(index)
        if bubble is None:
            return False
        if not bool(streaming):
            pending = getattr(self, "_pending_stream_updates", None)
            if isinstance(pending, dict):
                pending.pop(("index", int(index)), None)
                if bubble is getattr(self, "_last_ai", None):
                    pending.pop(("last", -1), None)
        updated = BubbleListView._apply_bubble_content_update_now(
            self,
            bubble,
            str(text or ""),
            is_markdown=bool(is_markdown),
            streaming=bool(streaming),
            model_name=model_name,
            elapsed=elapsed,
            reply_tokens=reply_tokens,
            total_tokens=total_tokens,
            start_time=start_time,
        )
        self._update_conversation_nav()
        return bool(updated)

    def finalize_message_at(
        self,
        index: int,
        *,
        model_name: Optional[str] = None,
        elapsed: Optional[float] = None,
        reply_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        start_time: Optional[str] = None,
    ) -> bool:
        self._flush_stream_update_for_index(index)
        bubble = self._bubble_for_message_index(index)
        if bubble is None:
            return False
        bubble._is_streaming = False
        bubble.set_metadata(
            start_time=start_time,
            model_name=model_name,
            elapsed=elapsed,
            reply_tokens=reply_tokens,
            total_tokens=total_tokens,
        )
        self._update_conversation_nav()
        return True

    def _clear_layout(self, lay) -> None:
        while lay.count():
            it = lay.takeAt(0)
            if it is None:
                break
            w = it.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
            sub = it.layout()
            if sub is not None:
                self._clear_layout(sub)
        lay.deleteLater()

    # ---- 公共 API ----
    def clear(self) -> None:
        self._cancel_progressive()
        try:
            self._layout_flush_timer.stop()
        except Exception:
            pass
        try:
            self._stream_flush_timer.stop()
        except Exception:
            pass
        try:
            self._pending_stream_updates.clear()
        except Exception:
            pass
        self.hide_transient_overlays()
        self._pending_keep_bottom = False
        self._scroll_to_bottom_pending = False
        self._scroll_generation += 1
        while self._vbox.count() > 1:  # 保留末尾 stretch
            item = self._vbox.takeAt(0)
            if item is None:
                break
            lay = item.layout()
            if lay is not None:
                self._clear_layout(lay)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self._bubbles.clear()
        self._last_ai = None
        self._placeholder = None
        if getattr(self, "_conversation_nav", None) is not None:
            self._conversation_nav.set_items([])
        self._update_placeholder_visibility()

    def hide_transient_overlays(self) -> None:
        for bubble in list(getattr(self, "_bubbles", []) or []):
            try:
                bubble.hide_transient_overlays()
            except Exception:
                pass
        nav = getattr(self, "_conversation_nav", None)
        if nav is not None:
            try:
                nav._show_card(-1)
            except Exception:
                pass

    def render_messages(self, history, *, initial_scroll_to_bottom: bool = False) -> None:
        self._cancel_progressive()
        self._locked_scroll_val = None
        scrollbar = self.verticalScrollBar()
        previous_updates = bool(self.updatesEnabled())
        _vp_rm = self.viewport()
        previous_viewport_updates = bool(_vp_rm.updatesEnabled()) if _vp_rm is not None else True
        previous_container_updates = bool(self._container.updatesEnabled())
        previous_layout_enabled = bool(self._vbox.isEnabled())
        previous_scroll_signals = False
        if scrollbar is not None:
            previous_scroll_signals = bool(scrollbar.signalsBlocked())
        self._layout_flush_timer.stop()
        self._pending_keep_bottom = False
        self._is_batch_rendering = True
        self.setUpdatesEnabled(False)
        if _vp_rm is not None:
            _vp_rm.setUpdatesEnabled(False)
        self._container.setUpdatesEnabled(False)
        self._vbox.setEnabled(False)
        if scrollbar is not None:
            scrollbar.blockSignals(True)
        try:
            self.clear()
            accumulated_tokens = 0
            for idx, msg in enumerate(history or []):
                try:
                    role = msg.get("role", "assistant")
                    display_content = str(msg.get("display_content", "") or "")
                    content = self._content_for_message(msg)
                    if _verbose_log_enabled():
                        logger.info(
                            "[ChatBubble] render_messages.item idx=%s role=%s used=%s content_len=%s display_len=%s final_len=%s final_has_image=%s preview=%s",
                            idx,
                            role,
                            "display_content" if display_content.strip() else "content",
                            len(str(msg.get("content", "") or "")),
                            len(display_content),
                            len(content),
                            has_markdown_image(content),
                            _preview_log_text(content),
                        )
                except Exception:
                    logger.exception("[ChatBubble] render_messages.item_failed idx=%s", idx)
                    continue

                accumulated_tokens = self._append_message_bubble(idx, msg, accumulated_tokens)

            self._vbox.setEnabled(True)
            self._refresh_all_bubbles()
            self._vbox.invalidate()
            self._vbox.activate()
            self._container.updateGeometry()
            if scrollbar is not None:
                target_value = scrollbar.maximum() if bool(initial_scroll_to_bottom) else scrollbar.minimum()
                scrollbar.setValue(target_value)
        finally:
            if scrollbar is not None:
                scrollbar.blockSignals(previous_scroll_signals)
            self._vbox.setEnabled(previous_layout_enabled)
            self._container.setUpdatesEnabled(previous_container_updates)
            if _vp_rm is not None:
                _vp_rm.setUpdatesEnabled(previous_viewport_updates)
            self.setUpdatesEnabled(previous_updates)
            self._is_batch_rendering = False

        _vp_rm_end = self.viewport()
        if _vp_rm_end is not None:
            _vp_rm_end.update()
        self._update_conversation_nav()
        QTimer.singleShot(0, lambda: self._log_rendered_geometry("render_messages.after_layout"))

    # ---- 尾部优先渐进渲染 ----
    def _cancel_progressive(self) -> None:
        """作废尚未完成的渐进补齐任务（新一轮渲染 / 清空前调用）。"""
        self._progressive_generation += 1
        self._progressive_state = None
        self._progressive_scroll_pin = None

    def _flush_progressive_now(self) -> None:
        """同步补齐全部剩余历史气泡。

        供依赖“消息已全部渲染”的路径（追加消息、按索引定位或操作气泡）在
        渐进任务未完成的极小时间窗内调用，保证外部语义与全量渲染一致。
        """
        if self._progressive_state is None:
            return
        self._prepend_pending_batch(int(self._progressive_generation), flush_all=True)

    def render_messages_tail_first(
        self,
        history,
        *,
        tail_count: int = 12,
        batch_size: int = 8,
        batch_interval_ms: int = 40,
        first_batch_delay_ms: int = 90,
    ) -> None:
        """切换长会话专用：先同步渲染最后 tail_count 条并直接定位到底部，
        其余更早的消息分批异步插入顶部。感知加载时长只取决于尾部渲染，
        且补齐过程中视口画面保持静止（无滚动、无跳动）。"""
        messages = [msg for msg in (history or []) if isinstance(msg, dict)]
        total = len(messages)
        if total <= int(tail_count) + 4:
            self.render_messages(messages, initial_scroll_to_bottom=True)
            return

        # 每条消息 token 的前缀和：供 assistant 气泡 total_tokens 兜底，与全量渲染一致
        token_prefix = [0] * (total + 1)
        for i, msg in enumerate(messages):
            token_prefix[i + 1] = token_prefix[i] + _estimate_tokens(self._content_for_message(msg))
        tail_start = total - int(tail_count)

        self._locked_scroll_val = None
        scrollbar = self.verticalScrollBar()
        previous_updates = bool(self.updatesEnabled())
        _vp_tf = self.viewport()
        previous_viewport_updates = bool(_vp_tf.updatesEnabled()) if _vp_tf is not None else True
        previous_container_updates = bool(self._container.updatesEnabled())
        previous_layout_enabled = bool(self._vbox.isEnabled())
        previous_scroll_signals = bool(scrollbar.signalsBlocked()) if scrollbar is not None else False
        self._layout_flush_timer.stop()
        self._pending_keep_bottom = False
        self._is_batch_rendering = True
        self.setUpdatesEnabled(False)
        if _vp_tf is not None:
            _vp_tf.setUpdatesEnabled(False)
        self._container.setUpdatesEnabled(False)
        self._vbox.setEnabled(False)
        if scrollbar is not None:
            scrollbar.blockSignals(True)
        try:
            self.clear()
            for idx in range(tail_start, total):
                self._append_message_bubble(idx, messages[idx], token_prefix[idx])
            self._vbox.setEnabled(True)
            self._refresh_all_bubbles()
            self._vbox.invalidate()
            self._vbox.activate()
            self._container.updateGeometry()
            if scrollbar is not None:
                scrollbar.setValue(scrollbar.maximum())
        finally:
            if scrollbar is not None:
                scrollbar.blockSignals(previous_scroll_signals)
            self._vbox.setEnabled(previous_layout_enabled)
            self._container.setUpdatesEnabled(previous_container_updates)
            if _vp_tf is not None:
                _vp_tf.setUpdatesEnabled(previous_viewport_updates)
            self.setUpdatesEnabled(previous_updates)
            self._is_batch_rendering = False

        _vp_tf_end = self.viewport()
        if _vp_tf_end is not None:
            _vp_tf_end.update()
        self._update_conversation_nav()

        # clear() 会作废旧的渐进任务并推进 generation，故在此之后再登记新任务
        generation = int(self._progressive_generation)
        self._progressive_state = {
            "messages": messages,
            "token_prefix": token_prefix,
            "remaining": tail_start,
            "batch_size": max(1, int(batch_size)),
            "interval_ms": max(0, int(batch_interval_ms)),
        }
        QTimer.singleShot(
            max(0, int(first_batch_delay_ms)),
            lambda: self._prepend_pending_batch(generation),
        )
        QTimer.singleShot(0, lambda: self._log_rendered_geometry("render_messages_tail_first.after_layout"))

    def _prepend_pending_batch(self, generation: int, *, flush_all: bool = False) -> None:
        state = self._progressive_state
        if state is None or generation != int(self._progressive_generation):
            return
        remaining = int(state.get("remaining", 0) or 0)
        if remaining <= 0:
            self._progressive_state = None
            return
        messages = state["messages"]
        token_prefix = state["token_prefix"]
        batch = remaining if flush_all else min(remaining, int(state["batch_size"]))
        start = remaining - batch  # 本批渲染 [start, remaining)

        scrollbar = self.verticalScrollBar()
        previous_updates = bool(self.updatesEnabled())
        _vp_pb = self.viewport()
        previous_viewport_updates = bool(_vp_pb.updatesEnabled()) if _vp_pb is not None else True
        previous_container_updates = bool(self._container.updatesEnabled())
        previous_scroll_signals = bool(scrollbar.signalsBlocked()) if scrollbar is not None else False
        self._is_batch_rendering = True
        self.setUpdatesEnabled(False)
        if _vp_pb is not None:
            _vp_pb.setUpdatesEnabled(False)
        self._container.setUpdatesEnabled(False)
        if scrollbar is not None:
            scrollbar.blockSignals(True)
        dist_to_bottom = 0
        if scrollbar is not None:
            dist_to_bottom = max(0, int(scrollbar.maximum()) - int(scrollbar.value()))
        try:
            max_w = self._max_bubble_width()
            vp = self.viewport()
            vw = vp.width() if vp is not None else self.width()
            for offset, idx in enumerate(range(start, remaining)):
                self._append_message_bubble(
                    idx,
                    messages[idx],
                    token_prefix[idx],
                    insert_row=offset,
                    list_pos=offset,
                )
                # 全量渲染由 _refresh_all_bubbles 统一按内容重算测量宽高，
                # 这里只需处理本批新插入的气泡
                self._update_bubble_stretch_and_policy(self._bubbles[offset], vw, max_w)
            self._vbox.invalidate()
            self._vbox.activate()
            self._container.updateGeometry()
            if scrollbar is not None:
                scrollbar.setValue(max(int(scrollbar.minimum()), int(scrollbar.maximum()) - dist_to_bottom))
        finally:
            if scrollbar is not None:
                scrollbar.blockSignals(previous_scroll_signals)
            self._container.setUpdatesEnabled(previous_container_updates)
            if _vp_pb is not None:
                _vp_pb.setUpdatesEnabled(previous_viewport_updates)
            self.setUpdatesEnabled(previous_updates)
            self._is_batch_rendering = False

        state["remaining"] = start
        # QScrollArea 的 range 更新可能晚一个事件循环，登记钉住信息，
        # 由 rangeChanged 处理器完成二次补偿
        self._progressive_scroll_pin = {
            "dist": dist_to_bottom,
            "expire": time.monotonic() + 0.6,
        }
        if self._locked_scroll_val is not None and scrollbar is not None:
            # 顶部插入使绝对滚动值整体平移，同步修正锁定值，防止 rangeChanged 回拉
            self._locked_scroll_val = int(scrollbar.value())
        if start <= 0:
            self._progressive_state = None
            _vp_pb_end = self.viewport()
            if _vp_pb_end is not None:
                _vp_pb_end.update()
            self._update_conversation_nav()
            return
        if not flush_all:
            interval = int(state.get("interval_ms", 40) or 0)
            QTimer.singleShot(interval, lambda: self._prepend_pending_batch(generation))

    def show_single(
        self,
        text: str,
        is_markdown: bool,
        *,
        model_name: Optional[str] = None,
        elapsed: Optional[float] = None,
        reply_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        start_time: Optional[str] = None,
    ) -> None:
        self.clear()
        bubble = self._add_bubble("assistant")
        bubble.set_msg_index(0)
        bubble.set_content(str(text or ""), is_markdown=bool(is_markdown))
        bubble.set_metadata(
            start_time=start_time,
            model_name=model_name,
            elapsed=elapsed,
            reply_tokens=reply_tokens,
            total_tokens=total_tokens,
        )
        bubble.copy_requested.connect(self.copy_requested.emit)
        bubble.delete_requested.connect(self.delete_requested.emit)
        bubble.regenerate_requested.connect(self.regenerate_requested.emit)
        bubble.copy_error_requested.connect(self.copy_error_requested.emit)
        bubble.switch_model_retry_requested.connect(self.switch_model_retry_requested.emit)
        bubble.new_round_from_error_requested.connect(self.new_round_from_error_requested.emit)
        bubble.edit_from_requested.connect(self.edit_from_requested.emit)
        bubble.branch_from_requested.connect(self.branch_from_requested.emit)
        bubble.pin_context_requested.connect(self.pin_context_requested.emit)
        bubble.add_to_note_requested.connect(self.add_to_note_requested.emit)
        self.refresh_layout(keep_bottom=False)
        self._update_conversation_nav()

    def update_last_ai(
        self,
        text: str,
        *,
        is_markdown: bool = True,
        streaming: bool = False,
        model_name: Optional[str] = None,
        elapsed: Optional[float] = None,
        reply_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        start_time: Optional[str] = None,
    ) -> None:
        if (
            bool(streaming)
            and self._last_ai is not None
            and callable(getattr(self, "_queue_stream_update", None))
        ):
            self._queue_stream_update(
                ("last", -1),
                str(text or ""),
                is_markdown=bool(is_markdown),
                model_name=model_name,
                elapsed=elapsed,
                reply_tokens=reply_tokens,
                total_tokens=total_tokens,
                start_time=start_time,
            )
            return
        flush_pending = getattr(self, "_flush_pending_stream_updates", None)
        if callable(flush_pending):
            flush_pending()
        BubbleListView._apply_update_last_ai_now(
            self,
            text,
            is_markdown=is_markdown,
            streaming=streaming,
            model_name=model_name,
            elapsed=elapsed,
            reply_tokens=reply_tokens,
            total_tokens=total_tokens,
            start_time=start_time,
        )

    def _apply_update_last_ai_now(
        self,
        text: str,
        *,
        is_markdown: bool = True,
        streaming: bool = False,
        model_name: Optional[str] = None,
        elapsed: Optional[float] = None,
        reply_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        start_time: Optional[str] = None,
    ) -> None:
        if self._last_ai is None:
            self.show_single(
                text,
                is_markdown,
                model_name=model_name,
                elapsed=elapsed,
                reply_tokens=reply_tokens,
                total_tokens=total_tokens,
                start_time=start_time,
            )
            return
        if not bool(streaming):
            pending = getattr(self, "_pending_stream_updates", None)
            if isinstance(pending, dict):
                pending.pop(("last", -1), None)
        BubbleListView._apply_bubble_content_update_now(
            self,
            self._last_ai,
            str(text or ""),
            is_markdown=bool(is_markdown),
            streaming=bool(streaming),
            model_name=model_name,
            elapsed=elapsed,
            reply_tokens=reply_tokens,
            total_tokens=total_tokens,
            start_time=start_time,
        )
        self._update_conversation_nav()

    def is_at_bottom(self, tolerance: int = 4) -> bool:
        bar = self.verticalScrollBar()
        if bar is None:
            return True
        return int(bar.value()) >= int(bar.maximum()) - int(max(0, tolerance))

    def refresh_layout(self, *, keep_bottom: bool = False) -> None:
        if getattr(self, "_block_refresh", False):
            return
        self._record_stream_perf("layout_flushes")
        keep_bottom = bool(keep_bottom or self._history_switch_should_keep_bottom())

        # 记录重算前的滚动条数值，用于非跟随状态下锁死视口
        bar = self.verticalScrollBar()
        scroll_val = bar.value() if bar is not None else None

        # 如果当前不需要跟随底部，且没有锁定的滚动数值，说明是第一次进入被动锁定状态，先锁死当前值
        if not keep_bottom and scroll_val is not None and getattr(self, "_locked_scroll_val", None) is None:
            self._locked_scroll_val = scroll_val

        self._refresh_all_bubbles()
        try:
            self._vbox.invalidate()
            self._vbox.activate()
            self._container.updateGeometry()
        except Exception:
            pass
        self.viewport().update()
        self._update_conversation_nav()
        if keep_bottom:
            self.scroll_to_bottom()
        elif scroll_val is not None and bar is not None:
            # 若不跟随底部，强制将滚动条值设回重算前的值，锁死视口，避免由于容器高度瞬时变化导致的 range 抖动截断与闪现
            bar.setValue(scroll_val)

    def _log_rendered_geometry(self, event_name: str) -> None:
        if not _verbose_log_enabled():
            return
        try:
            bar = self.verticalScrollBar()
            logger.info(
                "[ChatBubble] geometry.%s viewport=(%s,%s) container=(%s,%s) scroll=(%s,%s) bubbles=%s",
                event_name,
                self.viewport().width(),
                self.viewport().height(),
                self._container.width(),
                self._container.height(),
                bar.value() if bar is not None else None,
                bar.maximum() if bar is not None else None,
                len(self._bubbles),
            )
            for idx, bubble in enumerate(self._bubbles):
                images = bubble.findChildren(ChatImageWidget)
                logger.info(
                    "[ChatBubble] geometry.bubble idx=%s role=%s visible=%s geom=(%s,%s,%s,%s) size_hint=(%s,%s) images=%s",
                    idx,
                    bubble.role(),
                    bubble.isVisible(),
                    bubble.x(),
                    bubble.y(),
                    bubble.width(),
                    bubble.height(),
                    bubble.sizeHint().width(),
                    bubble.sizeHint().height(),
                    len(images),
                )
                for image_idx, image_widget in enumerate(images):
                    logger.info(
                        "[ChatImage] geometry idx=%s.%s visible=%s geom=(%s,%s,%s,%s) size_hint=(%s,%s)",
                        idx,
                        image_idx,
                        image_widget.isVisible(),
                        image_widget.x(),
                        image_widget.y(),
                        image_widget.width(),
                        image_widget.height(),
                        image_widget.sizeHint().width(),
                        image_widget.sizeHint().height(),
                    )
        except Exception:
            logger.exception("[ChatBubble] geometry.%s.failed", event_name)

    def scroll_to_bottom(self) -> None:
        if bool(getattr(self, "_scroll_to_bottom_pending", False)):
            return
        self._record_stream_perf("scroll_to_bottom_calls")
        self._scroll_to_bottom_pending = True
        generation = int(getattr(self, "_scroll_generation", 0))

        def _do(remaining: int = 2) -> None:
            if generation != int(getattr(self, "_scroll_generation", 0)):
                return
            bar = self.verticalScrollBar()
            if bar is not None:
                bar.setValue(bar.maximum())
            if remaining > 0:
                single_shot_scoped(16, self, lambda: _do(remaining - 1))
            else:
                self._scroll_to_bottom_pending = False
                self._refresh_conversation_nav_active()

        single_shot_scoped(0, self, _do)

    def scroll_to_bottom_now(self) -> None:
        self._record_stream_perf("scroll_to_bottom_calls")
        self._scroll_generation += 1
        self._scroll_to_bottom_pending = False
        try:
            self._layout_flush_timer.stop()
            self._pending_keep_bottom = False
            self._vbox.invalidate()
            self._vbox.activate()
            self._container.updateGeometry()
        except Exception:
            pass
        bar = self.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())
        self._refresh_conversation_nav_active()

    def content_height(self) -> int:
        return int(self._container.sizeHint().height())

    def plain_text(self) -> str:
        if self._last_ai is not None and self._last_ai.raw_text().strip():
            return self._last_ai.raw_text()
        for b in reversed(self._bubbles):
            if b.raw_text().strip():
                return b.raw_text()
        return ""

    def is_empty(self) -> bool:
        return not self._bubbles

    def _update_placeholder_visibility(self) -> None:
        if self.is_empty():
            if not hasattr(self, "_placeholder") or self._placeholder is None or sip.isdeleted(self._placeholder):
                self._placeholder = AIChatPlaceholderWidget(self._container)
                self._placeholder.card_clicked.connect(self.placeholder_card_clicked.emit)
                self._vbox.insertWidget(0, self._placeholder, 1000)
            self._placeholder.show()
        else:
            if hasattr(self, "_placeholder") and self._placeholder is not None and not sip.isdeleted(self._placeholder):
                self._placeholder.hide()

    def refresh_placeholder(self) -> None:
        if hasattr(self, "_placeholder") and self._placeholder is not None and not sip.isdeleted(self._placeholder):
            self._vbox.removeWidget(self._placeholder)
            self._placeholder.deleteLater()
            self._placeholder = None
        self._update_placeholder_visibility()


class AIChatLogoWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(80, 80)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        glow_grad = QRadialGradient(QPointF(rect.center()), rect.width() / 2.0)
        glow_grad.setColorAt(0.0, QColor(99, 102, 241, 40)) # Indigo with opacity
        glow_grad.setColorAt(0.8, QColor(139, 92, 246, 10))
        glow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(glow_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(rect)

        circle_rect = rect.adjusted(12, 12, -12, -12)
        grad = QLinearGradient(QPointF(circle_rect.topLeft()), QPointF(circle_rect.bottomRight()))
        grad.setColorAt(0.0, QColor("#6366f1")) # Indigo
        grad.setColorAt(0.5, QColor("#8b5cf6")) # Purple
        grad.setColorAt(1.0, QColor("#ec4899")) # Pink

        painter.setBrush(QBrush(grad))
        painter.drawEllipse(circle_rect)

        painter.setBrush(QBrush(Qt.GlobalColor.white))
        center = circle_rect.center()
        cx, cy = center.x(), center.y()
        r = 12
        path = QPainterPath()
        path.moveTo(cx, cy - r)
        path.quadTo(cx, cy, cx + r, cy)
        path.quadTo(cx, cy, cx, cy + r)
        path.quadTo(cx, cy, cx - r, cy)
        path.quadTo(cx, cy, cx, cy - r)
        painter.drawPath(path)

        r2 = 5
        cx2, cy2 = cx + 10, cy - 10
        path2 = QPainterPath()
        path2.moveTo(cx2, cy2 - r2)
        path2.quadTo(cx2, cy2, cx2 + r2, cy2)
        path2.quadTo(cx2, cy2, cx2, cy2 + r2)
        path2.quadTo(cx2, cy2, cx2 - r2, cy2)
        path2.quadTo(cx2, cy2, cx2, cy2 - r2)
        painter.drawPath(path2)


class CardIconWidget(QWidget):
    def __init__(self, icon_type: str, parent=None):
        super().__init__(parent)
        self.icon_type = icon_type
        self.setFixedSize(30, 30)

    def paintEvent(self, event):
        from PyQt6.QtCore import QRectF, QPointF
        from PyQt6.QtGui import QColor, QBrush, QPen, QPixmap

        builtin_types = {"musk", "buffett", "decision", "creative", "code_expert", "marketing", "healing", "interview", "english", "weekly",
                         "translate", "search", "summarize", "write", "code"}
        is_custom = self.icon_type not in builtin_types

        if is_custom:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = self.rect()
            bg_color = QColor("#f1f5f9")
            stroke_color = QColor("#64748b")

            painter.setBrush(QBrush(bg_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 8.0, 8.0)

            cx, cy = rect.center().x(), rect.center().y()
            from deepcat.utils.paths import get_app_dir
            full_path = get_app_dir() / self.icon_type
            if full_path.exists():
                pixmap = QPixmap(str(full_path))
                if not pixmap.isNull():
                    target_rect = QRectF(cx - 8, cy - 8, 16, 16)
                    painter.drawPixmap(target_rect, pixmap, QRectF(pixmap.rect()))
            else:
                painter.setPen(QPen(stroke_color, 1.2))
                painter.setBrush(QBrush(Qt.GlobalColor.transparent))
                painter.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))
            painter.end()
        else:
            from deepcat.ui.settings_dialog.sub_dialogs import get_card_icon_pixmap
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pixmap = get_card_icon_pixmap(self.icon_type, 30)
            painter.drawPixmap(0, 0, pixmap)
            painter.end()


class SuggestionCard(QFrame):
    clicked = pyqtSignal(str, str)
    card_deleted = pyqtSignal()

    def __init__(self, card_id: str, icon_type: str, title: str, subtitle: str, prompt_text: str, default_title: str, default_prompt: str, parent=None):
        super().__init__(parent)
        self.setObjectName("SuggestionCard")
        self.card_id = card_id
        self.icon_type = icon_type
        self.prompt_text = prompt_text
        self.default_title = default_title
        self.default_prompt = default_prompt
        self._drag_start_pos: Optional[QPoint] = None
        self._pressed_for_click = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.icon_widget = CardIconWidget(icon_type, self)
        layout.addWidget(self.icon_widget, 0, Qt.AlignmentFlag.AlignVCenter)

        display_title = title
        if len(title) > 6:
            display_title = title[:6] + "..."
        self.title_label = QLabel(display_title, self)
        self.title_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #334155; background: transparent; border: none;")
        layout.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.setToolTip(title)

        self.setStyleSheet(
            "QFrame#SuggestionCard {"
            "  background: #ffffff;"
            "  border: none;"
            "  border-radius: 10px;"
            "}"
            "QFrame#SuggestionCard:hover {"
            "  background: #f1f5f9;"
            "}"
        )

    def contextMenuEvent(self, event):
        self._show_context_menu(event.globalPos())
        event.accept()

    def _show_context_menu(self, pos):
        from deepcat.ui.post_capture_actions.model_menus import OcrGenericMenuPopup

        items = [
            ("编辑卡片", self._on_edit_clicked, True),
            ("关闭卡片", self._on_delete_clicked, True)
        ]

        popup = OcrGenericMenuPopup(
            items=items,
            parent=self,
            active_indicator="background"
        )
        popup.show_at_pos(pos)

    def _on_edit_clicked(self):
        from PyQt6.QtWidgets import QDialog
        from deepcat.ui.settings_dialog.sub_dialogs import AddChatCardDialog
        from deepcat.settings_store import load_settings, update_ui_settings

        dlg = AddChatCardDialog(
            parent=self.window(),
            title="编辑快捷卡片",
            initial_title=self.title_label.text(),
            initial_icon_type=self.icon_type,
            initial_prompt=self.prompt_text
        )

        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_title = dlg.card_title()
            new_prompt = dlg.card_prompt()
            new_icon_type = dlg.card_icon_type()

            # 更新本地 UI
            display_title = new_title
            if len(new_title) > 6:
                display_title = new_title[:6] + "..."
            self.title_label.setText(display_title)
            self.setToolTip(new_title)
            self.prompt_text = new_prompt
            self.icon_type = new_icon_type

            # 局部更新 CardIconWidget 图标属性并触发瞬间重绘，避免整卡重构闪烁
            if hasattr(self, "icon_widget"):
                self.icon_widget.icon_type = new_icon_type
                self.icon_widget.update()

            # 保存到配置
            try:
                settings = load_settings()
                saved_cards = settings.ui.get("chat_placeholder_cards") or []
                if not isinstance(saved_cards, list):
                    saved_cards = []

                updated_cards = []
                found = False
                for sc in saved_cards:
                    if isinstance(sc, dict) and sc.get("id") == self.card_id:
                        sc = dict(sc)
                        sc["title"] = new_title
                        sc["prompt"] = new_prompt
                        sc["icon_type"] = new_icon_type
                        found = True
                    updated_cards.append(sc)
                if not found:
                    updated_cards.append({
                        "id": self.card_id,
                        "title": new_title,
                        "prompt": new_prompt,
                        "icon_type": new_icon_type
                    })

                # 同步存入 SQLite (prompt.db)
                import json
                from deepcat.prompt_store import PromptStore
                try:
                    store = PromptStore()
                    store.save_prompt("chat_placeholder_cards", json.dumps(updated_cards, ensure_ascii=False))
                except Exception as e:
                    logger.error(f"[SuggestionCard] failed to save to prompt.db: {e}")

                update_ui_settings(chat_placeholder_cards=updated_cards)
            except Exception as e:
                logger.error(f"[SuggestionCard] failed to save card settings: {e}")

    def _on_delete_clicked(self):
        from deepcat.settings_store import load_settings, update_ui_settings
        from deepcat.prompt_store import PromptStore
        import json

        try:
            settings = load_settings()
            saved_cards = settings.ui.get("chat_placeholder_cards")

            # 如果配置中的卡片列表为空，说明当前显示的是系统初始状态默认的 4 个卡片。
            # 为了防止直接删除后其余 3 个卡片也跟着不显示，我们会以这 4 个默认卡片数据初始化列表，然后再执行删除。
            if not isinstance(saved_cards, list) or len(saved_cards) == 0:
                saved_cards = [
                    {
                        "id": "translate",
                        "icon_type": "translate",
                        "title": "多语翻译",
                        "prompt": "请帮我将以下文本翻译成地道的英文，并给出难点解析：\n"
                    },
                    {
                        "id": "search",
                        "icon_type": "search",
                        "title": "AI搜索",
                        "prompt": "请帮我搜索以下内容，并对搜索结果进行系统性的整理和深度总结：\n"
                    },
                    {
                        "id": "summarize",
                        "icon_type": "summarize",
                        "title": "长文提炼",
                        "prompt": "请帮我提炼以下长文的核心内容，用条理清晰的列表输出关键要点：\n"
                    },
                    {
                        "id": "write",
                        "icon_type": "write",
                        "title": "文本润色",
                        "prompt": "请帮我润色以下文本，纠正语法错误并提升其学术流畅度，同时列出主要修改点：\n"
                    }
                ]

            # 过滤掉需要删除的卡片
            updated_cards = [sc for sc in saved_cards if isinstance(sc, dict) and sc.get("id") != self.card_id]

            # 同步保存到 SQLite 数据库中
            try:
                store = PromptStore()
                store.save_prompt("chat_placeholder_cards", json.dumps(updated_cards, ensure_ascii=False))
            except Exception as e:
                logger.error(f"[SuggestionCard] failed to save to prompt.db during delete: {e}")

            # 保存到应用配置
            update_ui_settings(chat_placeholder_cards=updated_cards)

            # 发射删除信号以触发重新加载
            self.card_deleted.emit()

        except Exception as e:
            logger.error(f"[SuggestionCard] failed to delete card: {e}")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
            self._pressed_for_click = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._drag_start_pos is not None:
            distance = (event.position().toPoint() - self._drag_start_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._pressed_for_click = False
                self._drag_start_pos = None
                self._start_drag()
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._pressed_for_click:
                self.clicked.emit(self.title_label.text(), self.prompt_text)
            self._pressed_for_click = False
            self._drag_start_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _start_drag(self) -> None:
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_CHAT_CARD_DRAG_MIME, str(self.card_id or "").encode("utf-8"))

        pixmap = self.grab()
        painter = QPainter(pixmap)
        try:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.fillRect(pixmap.rect(), QColor(0, 0, 0, 160))
        finally:
            if painter.isActive():
                painter.end()

        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)


class AIChatPlaceholderWidget(QWidget):
    card_clicked = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AIChatPlaceholderWidget")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAcceptDrops(True)
        self._cards_to_show: list[dict[str, str]] = []
        self._card_widgets: list[SuggestionCard] = []

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(0)

        main_layout.addStretch(1)

        center_widget = QWidget(self)
        center_widget.setMinimumWidth(680)
        center_widget.setMaximumWidth(960)
        center_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(16)
        center_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.title_label = QLabel("今天想聊点什么？", center_widget)
        self.title_label.setStyleSheet("font-size: 20px; font-weight: 600; color: #0f172a; background: transparent;")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        center_layout.addWidget(self.title_label)

        self.subtitle_label = QLabel("选择下方快捷卡片开始，或直接在底部输入框输入", center_widget)
        self.subtitle_label.setStyleSheet("font-size: 13px; color: #64748b; background: transparent;")
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        center_layout.addWidget(self.subtitle_label)

        center_layout.addSpacing(8)

        self.grid_widget = QWidget(center_widget)
        self.grid_widget.setAcceptDrops(True)
        self.grid_widget.installEventFilter(self)
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(12)
        for c in range(4):
            self.grid_layout.setColumnStretch(c, 1)

        # 使用 QScrollArea 容器包裹 grid_widget 以便超出4行时垂直滚动
        from PyQt6.QtWidgets import QScrollArea, QFrame
        self.scroll_area = QScrollArea(center_widget)
        self.scroll_area.setObjectName("CardsScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setWidget(self.grid_widget)
        self.scroll_area.setMaximumHeight(260)

        self.scroll_area.setStyleSheet("""
            QScrollArea#CardsScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea#CardsScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QScrollBar:vertical {
                border: none;
                background: #f1f5f9;
                width: 6px;
                margin: 0px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #cbd5e1;
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #94a3b8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
                height: 0px;
            }
        """)

        self.reload_cards()

        center_layout.addWidget(self.scroll_area)
        main_layout.addWidget(center_widget, 0, Qt.AlignmentFlag.AlignHCenter)
        main_layout.addStretch(1)
        self.setStyleSheet("QWidget#AIChatPlaceholderWidget { background: #f8fafc; }")

    def eventFilter(self, obj, event):
        if obj is getattr(self, "grid_widget", None) and event.type() in {
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.Drop,
        }:
            if event.type() == QEvent.Type.Drop:
                self._handle_card_drop(event, event.position().toPoint())
            else:
                self._accept_card_drag(event)
            return event.isAccepted()
        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event) -> None:
        self._accept_card_drag(event)

    def dragMoveEvent(self, event) -> None:
        self._accept_card_drag(event)

    def dropEvent(self, event) -> None:
        pos_in_grid = self.grid_widget.mapFrom(self, event.position().toPoint())
        self._handle_card_drop(event, pos_in_grid)

    def _accept_card_drag(self, event) -> None:
        if event.mimeData().hasFormat(_CHAT_CARD_DRAG_MIME):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def _handle_card_drop(self, event, pos_in_grid: QPoint) -> None:
        if not event.mimeData().hasFormat(_CHAT_CARD_DRAG_MIME):
            event.ignore()
            return
        try:
            card_id = bytes(event.mimeData().data(_CHAT_CARD_DRAG_MIME)).decode("utf-8")
        except Exception:
            card_id = ""
        if not card_id:
            event.ignore()
            return
        if self._reorder_card_from_grid_position(card_id, pos_in_grid):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def _reorder_card_from_grid_position(self, card_id: str, pos_in_grid: QPoint) -> bool:
        target_index = -1
        min_distance = 999999
        for index, card in enumerate(getattr(self, "_card_widgets", [])):
            if card is None or not card.isVisible():
                continue
            distance = (card.geometry().center() - pos_in_grid).manhattanLength()
            if distance < min_distance:
                min_distance = distance
                target_index = index
        if target_index < 0:
            return False
        return self._move_card_to_index(card_id, target_index)

    def _move_card_to_index(self, card_id: str, target_index: int) -> bool:
        cards = self._load_persistable_cards()
        source_index = next((i for i, card in enumerate(cards) if str(card.get("id", "")) == str(card_id)), -1)
        if source_index < 0:
            return False
        target_index = max(0, min(int(target_index), len(cards) - 1))
        if source_index == target_index:
            return False
        moved_card = cards.pop(source_index)
        cards.insert(target_index, moved_card)
        if not self._save_chat_placeholder_cards(cards):
            return False
        self.reload_cards()
        return True

    def _load_persistable_cards(self) -> list[dict[str, str]]:
        from deepcat.settings_store import load_settings

        try:
            saved_cards = load_settings().ui.get("chat_placeholder_cards")
        except Exception:
            saved_cards = None
        if isinstance(saved_cards, list) and saved_cards:
            return [dict(card) for card in saved_cards if isinstance(card, dict)]
        return [
            {
                "id": str(card.get("id", "")),
                "icon_type": str(card.get("icon_type", "")),
                "title": str(card.get("title", "")),
                "prompt": str(card.get("prompt", "")),
            }
            for card in getattr(self, "_cards_to_show", [])
            if isinstance(card, dict) and str(card.get("id", ""))
        ]

    def _save_chat_placeholder_cards(self, cards: list[dict[str, str]]) -> bool:
        import json
        from deepcat.settings_store import update_ui_settings

        normalized_cards = [dict(card) for card in cards if isinstance(card, dict) and str(card.get("id", ""))]
        try:
            from deepcat.prompt_store import PromptStore
            try:
                PromptStore().save_prompt("chat_placeholder_cards", json.dumps(normalized_cards, ensure_ascii=False))
            except Exception as exc:
                logger.error("[AIChatPlaceholderWidget] failed to save card order to prompt.db: %s", exc)
            update_ui_settings(chat_placeholder_cards=normalized_cards)
            return True
        except Exception as exc:
            logger.error("[AIChatPlaceholderWidget] failed to save card order: %s", exc)
            return False

    def reload_cards(self):
        # 1. 清理已有卡片小部件并销毁以释放内存
        self._card_widgets = []
        while self.grid_layout.count() > 0:
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        # 2. 默认卡片数据
        default_cards_data = [
            {
                "id": "translate",
                "icon_type": "translate",
                "title": "多语翻译",
                "subtitle": "将文本翻译为地道的目标语言",
                "prompt": "请帮我将以下文本翻译成地道的英文，并给出难点解析：\n"
            },
            {
                "id": "search",
                "icon_type": "search",
                "title": "AI搜索",
                "subtitle": "结合网络检索进行深度总结与分析",
                "prompt": "请帮我搜索以下内容，并对搜索结果进行系统性的整理和深度总结：\n"
            },
            {
                "id": "summarize",
                "icon_type": "summarize",
                "title": "长文提炼",
                "subtitle": "快速提取文章或段落的核心要点",
                "prompt": "请帮我提炼以下长文的核心内容，用条理清晰的列表输出关键要点：\n"
            },
            {
                "id": "write",
                "icon_type": "write",
                "title": "文本润色",
                "subtitle": "修改语法错误，提升文章表达的学术感与流畅度",
                "prompt": "请帮我润色以下文本，纠正语法错误并提升其学术流畅度，同时列出主要修改点：\n"
            }
        ]

        from deepcat.settings_store import load_settings
        try:
            settings = load_settings()
            saved_cards = settings.ui.get("chat_placeholder_cards")
        except Exception:
            saved_cards = None

        # 决定展示的卡片列表，支持全部自定义的快捷卡片能够配置化地遍历呈现
        cards_to_show = []
        if isinstance(saved_cards, list) and len(saved_cards) > 0:
            for sc in saved_cards:
                if isinstance(sc, dict):
                    cid = sc.get("id", "")

                    # 匹配该 id 对应的默认图标类型，避免硬编码 'write' 导致老卡片图标全退化成魔法棒
                    fallback_icon_type = "write"
                    for dc in default_cards_data:
                        if dc["id"] == cid or (cid == "search" and dc["id"] == "code"):
                            fallback_icon_type = dc["icon_type"]
                            break

                    icon_type = sc.get("icon_type") or fallback_icon_type
                    title = sc.get("title", "自定义卡片")
                    prompt = sc.get("prompt", "")

                    subtitle = ""
                    default_title = title
                    default_prompt = prompt
                    for dc in default_cards_data:
                        if dc["id"] == cid or (cid == "search" and dc["id"] == "code"):
                            subtitle = dc.get("subtitle", "")
                            default_title = dc["title"]
                            default_prompt = dc["prompt"]
                            break

                    cards_to_show.append({
                        "id": cid,
                        "icon_type": icon_type,
                        "title": title,
                        "subtitle": subtitle,
                        "prompt": prompt,
                        "default_title": default_title,
                        "default_prompt": default_prompt
                    })
        else:
            for dc in default_cards_data:
                cards_to_show.append({
                    "id": dc["id"],
                    "icon_type": dc["icon_type"],
                    "title": dc["title"],
                    "subtitle": dc.get("subtitle", ""),
                    "prompt": dc["prompt"],
                    "default_title": dc["title"],
                    "default_prompt": dc["prompt"]
                })

        self._cards_to_show = [dict(card) for card in cards_to_show]

        for i, card_info in enumerate(cards_to_show):
            card_id = card_info["id"]
            icon_type = card_info["icon_type"]
            current_title = card_info["title"]
            subtitle = card_info["subtitle"]
            current_prompt = card_info["prompt"]
            default_title = card_info["default_title"]
            default_prompt = card_info["default_prompt"]

            row = i // 4
            col = i % 4
            card = SuggestionCard(
                card_id=card_id,
                icon_type=icon_type,
                title=current_title,
                subtitle=subtitle,
                prompt_text=current_prompt,
                default_title=default_title,
                default_prompt=default_prompt,
                parent=self.grid_widget
            )
            card.clicked.connect(self.card_clicked.emit)
            card.card_deleted.connect(self.reload_cards)
            self.grid_layout.addWidget(card, row, col)
            self._card_widgets.append(card)

        if hasattr(self, "scroll_area") and self.scroll_area is not None:
            if len(cards_to_show) > 16:
                self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            else:
                self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
