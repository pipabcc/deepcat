from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractButton,
    QPushButton,
    QHBoxLayout,
    QFrame,
    QWidget,
    QGraphicsDropShadowEffect,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QDialog,
    QStyle,
    QCheckBox,
    QListWidget,
    QListWidgetItem,
    QInputDialog,
    QStyledItemDelegate,
)
from deepcat.translation_history_store import TranslationHistoryStore
from PyQt6.QtWidgets import QFrame, QPushButton, QStyle
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame
from PyQt6.QtGui import QPainter, QPen, QColor, QFont
from PyQt6.QtCore import QPointF, QRectF, QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton

from deepcat.ui.post_capture_actions._shared import _AI_CHAT_BATCH_SELECTED_ROLE, _AI_CHAT_GROUP_ROLE, _AI_CHAT_RECORD_ROLE
from deepcat.ui.post_capture_actions.model_menus import OcrGenericMenuPopup
from deepcat.ui.timer_scope import single_shot_scoped


class AIChatNewButton(QPushButton):
    """历史侧栏的新对话按钮，使用 QPainter 自绘精致且现代化的极简细线加号图标，保证高 DPI 极度清晰。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setText("")
        self.setFixedSize(36, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("新对话")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def paintEvent(self, event) -> None:
        from PyQt6.QtGui import QPainter, QPen, QColor
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. 精致扁平化背景绘制
        is_hovered = self.underMouse()
        is_pressed = self.isDown()

        if is_pressed:
            bg_color = QColor("#e2e8f0")
        elif is_hovered:
            bg_color = QColor("#eef2f7")
        else:
            bg_color = QColor("transparent")

        if bg_color != QColor("transparent"):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg_color)
            painter.drawRoundedRect(self.rect(), 8, 8)

        # 2. 精致现代化极简加号绘制 (1.5px 极细圆角线)
        color = QColor("#1e293b") if is_hovered or is_pressed else QColor("#64748b")
        pen = QPen(color)
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        r = 6.0  # 加号线长度为 12px

        # 水平线
        painter.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
        # 垂直线
        painter.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))


class AIChatHistoryFilterButton(QAbstractButton):
    """历史筛选按钮：完全自绘，绕开 QPushButton 原生焦点/默认按钮绘制。"""

    _HORIZONTAL_VISUAL_INSET = 1.0

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setText(str(text or ""))
        font = self.font()
        font.setPixelSize(12)
        self.setFont(font)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        focus_attr = getattr(Qt.WidgetAttribute, "WA_MacShowFocusRect", None)
        if focus_attr is not None:
            self.setAttribute(focus_attr, False)
        self.pressed.connect(lambda: self.update())
        self.released.connect(lambda: self.update())
        self.toggled.connect(lambda _checked=False: self.update())

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self.font())
        return QSize(max(44, int(metrics.horizontalAdvance(self.text())) + 14), 26)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self.isChecked():
                background_color = QColor("#1f3a5f")
                border_color = QColor("#1f3a5f")
                text_color = QColor("#ffffff")
            elif self.underMouse() and self.isEnabled():
                background_color = QColor("#ffffff")
                border_color = QColor("#b8c4d6")
                text_color = QColor("#334155")
            else:
                background_color = QColor("#ffffff")
                border_color = QColor("#dbe3ee")
                text_color = QColor("#64748b")

            if self.isDown() and self.isEnabled() and not self.isChecked():
                background_color = QColor("#f8fafc")
            if not self.isEnabled():
                text_color = QColor("#94a3b8")

            inset_x = float(self._HORIZONTAL_VISUAL_INSET)
            rect = QRectF(self.rect()).adjusted(inset_x + 0.5, 0.5, -inset_x - 0.5, -0.5)
            painter.setPen(QPen(border_color, 1))
            painter.setBrush(background_color)
            painter.drawRoundedRect(rect, 7, 7)
            painter.setPen(text_color)
            painter.drawText(QRectF(self.rect()).adjusted(inset_x, 0.0, -inset_x, 0.0), int(Qt.AlignmentFlag.AlignCenter), self.text())
        finally:
            if painter.isActive():
                painter.end()
        event.accept()


def _ai_history_search_terms(query: str) -> list[str]:
    compact = " ".join(str(query or "").split())
    if not compact:
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for term in [compact, *compact.split()]:
        key = term.casefold()
        if key and key not in seen:
            seen.add(key)
            terms.append(term)
    return terms


def _first_search_match(text: str, terms: list[str]) -> tuple[int, int]:
    haystack = str(text or "")
    lowered = haystack.casefold()
    best_start = -1
    best_len = 0
    for term in terms:
        needle = str(term or "").casefold()
        if not needle:
            continue
        start = lowered.find(needle)
        if start < 0:
            continue
        if best_start < 0 or start < best_start or (start == best_start and len(needle) > best_len):
            best_start = start
            best_len = len(str(term or ""))
    return best_start, best_len


class AIChatHistoryItemDelegate(QStyledItemDelegate):
    """用委托绘制历史记录，避免每条记录创建 QWidget 子控件。"""

    def __init__(self, sidebar: "AIChatHistorySidebar", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._sidebar = sidebar

    def sizeHint(self, option, index) -> QSize:
        group_title = index.data(_AI_CHAT_GROUP_ROLE)
        if group_title:
            return QSize(0, 30)
        return QSize(0, 64)

    def paint(self, painter: QPainter, option, index) -> None:
        group_title = index.data(_AI_CHAT_GROUP_ROLE)
        if group_title:
            painter.save()
            group_font = QFont(option.font)
            group_font.setBold(True)
            if group_font.pointSize() > 0:
                group_font.setPointSize(max(8, group_font.pointSize() - 1))
            painter.setFont(group_font)
            painter.setPen(QColor("#64748b"))
            painter.drawText(
                option.rect.adjusted(10, 6, -8, -2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                str(group_title),
            )
            painter.restore()
            return

        record = index.data(_AI_CHAT_RECORD_ROLE)
        if not isinstance(record, dict):
            return

        row = index.row()
        hovered = row == getattr(self._sidebar, "_hovered_row", -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        batch_mode = bool(getattr(self._sidebar, "_batch_mode", False))
        batch_selected = bool(index.data(_AI_CHAT_BATCH_SELECTED_ROLE))

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        row_rect = option.rect.adjusted(0, 2, 0, -2)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#e7eef8"))
            painter.drawRoundedRect(row_rect, 8, 8)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#eef4fb"))
            painter.drawRoundedRect(row_rect, 8, 8)

        left = row_rect.left() + 8
        right = row_rect.right() - 8
        center_y = row_rect.center().y()

        if batch_mode:
            checkbox_rect = self._sidebar._checkbox_rect_for_row_rect(row_rect)
            self._draw_checkbox(painter, checkbox_rect, batch_selected)
            left = checkbox_rect.right() + 8
        elif hovered:
            menu_rect = self._sidebar._menu_rect_for_row_rect(row_rect)
            self._draw_menu_dots(painter, menu_rect)
            right = menu_rect.left() - 4

        search_terms = _ai_history_search_terms(getattr(self._sidebar, "_search_text", ""))
        title_text = self._sidebar._display_title(record)
        if bool(record.get("is_pinned")):
            title_text = f"置顶 · {title_text}"

        title_font = QFont(option.font)
        title_font.setBold(True)
        if title_font.pointSize() > 0:
            title_font.setPointSize(max(9, title_font.pointSize()))
        painter.setFont(title_font)
        title_metrics = QFontMetrics(title_font)
        title_rect = QRect(left, row_rect.top() + 8, max(24, right - left + 1), 20)
        self._draw_highlighted_single_line(
            painter,
            title_rect,
            title_text,
            title_metrics,
            text_color=QColor("#1f2937"),
            terms=search_terms,
        )

        meta_text = self._sidebar._record_meta_text(record, limit=80)

        meta_font = QFont(option.font)
        if meta_font.pointSize() > 0:
            meta_font.setPointSize(max(8, meta_font.pointSize() - 2))
        painter.setFont(meta_font)
        meta_metrics = QFontMetrics(meta_font)
        meta_rect = QRect(left, center_y + 4, max(24, right - left + 1), 18)
        self._draw_highlighted_single_line(
            painter,
            meta_rect,
            meta_text.strip(),
            meta_metrics,
            text_color=QColor("#64748b"),
            terms=search_terms,
        )

        painter.restore()

    @staticmethod
    def _draw_highlighted_single_line(
        painter: QPainter,
        rect: QRect,
        text: str,
        metrics: QFontMetrics,
        *,
        text_color: QColor,
        terms: list[str],
    ) -> None:
        visible_text = metrics.elidedText(str(text or ""), Qt.TextElideMode.ElideRight, max(0, rect.width()))
        if not visible_text:
            return

        ranges: list[tuple[int, int]] = []
        lowered = visible_text.casefold()
        for term in terms:
            needle = str(term or "").casefold()
            if not needle:
                continue
            start = 0
            while True:
                pos = lowered.find(needle, start)
                if pos < 0:
                    break
                end = pos + len(str(term or ""))
                if end <= len(visible_text):
                    ranges.append((pos, end))
                start = max(pos + 1, end)
        if ranges:
            merged: list[tuple[int, int]] = []
            for start, end in sorted(ranges):
                if not merged or start > merged[-1][1]:
                    merged.append((start, end))
                else:
                    prev_start, prev_end = merged[-1]
                    merged[-1] = (prev_start, max(prev_end, end))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#fff2a8"))
            for start, end in merged:
                prefix_w = metrics.horizontalAdvance(visible_text[:start])
                match_w = metrics.horizontalAdvance(visible_text[start:end])
                highlight_rect = QRect(
                    rect.left() + prefix_w - 1,
                    rect.top() + 2,
                    max(2, match_w + 2),
                    max(10, rect.height() - 4),
                )
                painter.drawRoundedRect(highlight_rect, 3, 3)

        painter.setPen(text_color)
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
            visible_text,
        )

    @staticmethod
    def _draw_checkbox(painter: QPainter, rect: QRect, checked: bool) -> None:
        painter.setPen(QPen(QColor("#1f3a5f" if checked else "#cbd5e1"), 1))
        painter.setBrush(QColor("#1f3a5f" if checked else "#ffffff"))
        painter.drawRoundedRect(rect, 4, 4)
        if not checked:
            return
        painter.setPen(QPen(QColor("#ffffff"), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        x = rect.left()
        y = rect.top()
        painter.drawLine(QPoint(x + 4, y + 8), QPoint(x + 7, y + 11))
        painter.drawLine(QPoint(x + 7, y + 11), QPoint(x + 12, y + 5))

    @staticmethod
    def _draw_menu_dots(painter: QPainter, rect: QRect) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#64748b"))
        center_y = rect.center().y()
        start_x = rect.center().x() - 5
        for offset in (0, 5, 10):
            painter.drawEllipse(QRect(start_x + offset, center_y - 1, 2, 2))


class AIChatHistorySidebar(QWidget):
    """AI 对话窗口内嵌的轻量历史侧栏。"""

    record_selected = pyqtSignal(int)
    record_search_selected = pyqtSignal(int, str)
    new_chat_requested = pyqtSignal()
    records_deleted = pyqtSignal(list)

    def __init__(self, history_store: Optional[TranslationHistoryStore], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._store = history_store
        self._page_limit = 60
        self._loaded_count = 0
        self._has_more = True
        self._loading_more = False
        self._search_text = ""
        self._task_filter: Optional[str] = None
        self._starred_only = False
        self._last_group_title = ""
        self._selected_record_id: Optional[int] = None
        self._batch_mode = False
        self._selected_batch_record_ids: set[int] = set()
        self._hovered_row = -1
        self.setObjectName("AIChatHistorySidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(280)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self.refresh)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 10, 10)
        root.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        self._search_input = QLineEdit(self)
        self._search_input.setObjectName("AIChatHistorySearchInput")
        self._search_input.setPlaceholderText("搜索历史记录 / 模型")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(lambda: self._search_timer.start())
        self._search_input.setFixedHeight(36)
        try:
            from deepcat.ui.settings_dialog import SettingsDialog
            SettingsDialog._install_custom_text_context_menus(self, self._search_input)
        except Exception:
            pass
        top_row.addWidget(self._search_input, 1, Qt.AlignmentFlag.AlignVCenter)

        self._new_chat_btn = AIChatNewButton(self)
        self._new_chat_btn.setObjectName("AIChatHistoryNewButton")
        self._new_chat_btn.clicked.connect(self.new_chat_requested)
        top_row.addWidget(self._new_chat_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(top_row)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(4)
        self._task_filter_buttons: dict[str, QPushButton] = {}
        for key, label in (
            ("all", "全部"),
            ("qa", "问答"),
            ("translate", "翻译"),
            ("starred", "收藏"),
        ):
            btn = AIChatHistoryFilterButton(label, self)
            btn.setObjectName("AIChatHistoryFilterButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda _checked=False, value=key: self._on_task_filter_changed(value))
            self._task_filter_buttons[key] = btn
            filter_row.addWidget(btn)
        self._task_filter_buttons["all"].setChecked(True)
        root.addLayout(filter_row)

        self._list = QListWidget(self)
        self._list.setObjectName("AIChatHistoryList")
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setMouseTracking(True)
        self._list.setUniformItemSizes(False)
        self._list.setItemDelegate(AIChatHistoryItemDelegate(self, self._list))
        self._list.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)
        self.installEventFilter(self)
        self._list.installEventFilter(self)
        try:
            self._list.viewport().setMouseTracking(True)
            self._list.viewport().installEventFilter(self)
        except Exception:
            pass
        root.addWidget(self._list, 1)

        self._empty_label = QLabel("暂无历史记录", self)
        self._empty_label.setObjectName("AIChatHistoryEmptyLabel")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.hide()
        root.addWidget(self._empty_label, 1)

        self._batch_bar = QWidget(self)
        self._batch_bar.setObjectName("AIChatHistoryBatchBar")
        batch_layout = QHBoxLayout(self._batch_bar)
        batch_layout.setContentsMargins(8, 7, 8, 7)
        batch_layout.setSpacing(6)

        self._batch_select_all = QCheckBox("全选", self._batch_bar)
        self._batch_select_all.setObjectName("AIChatHistoryBatchSelectAll")
        self._batch_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._batch_select_all.toggled.connect(self._on_select_all_toggled)
        batch_layout.addWidget(self._batch_select_all, 0, Qt.AlignmentFlag.AlignVCenter)

        self._batch_count_label = QLabel("已选 0 项", self._batch_bar)
        self._batch_count_label.setObjectName("AIChatHistoryBatchCount")
        batch_layout.addWidget(self._batch_count_label, 1, Qt.AlignmentFlag.AlignVCenter)

        self._batch_delete_btn = QPushButton("删除", self._batch_bar)
        self._batch_delete_btn.setObjectName("AIChatHistoryBatchDeleteButton")
        self._batch_delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._batch_delete_btn.clicked.connect(self._on_batch_delete_clicked)
        batch_layout.addWidget(self._batch_delete_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._batch_cancel_btn = QPushButton("取消", self._batch_bar)
        self._batch_cancel_btn.setObjectName("AIChatHistoryBatchCancelButton")
        self._batch_cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._batch_cancel_btn.clicked.connect(lambda: self._set_batch_mode(False))
        batch_layout.addWidget(self._batch_cancel_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._batch_bar.hide()
        root.addWidget(self._batch_bar)

        checkbox_check_icon_url = str((Path(__file__).resolve().parent.parent / "assets" / "icon_checkbox_check.svg").as_posix())
        style_sheet = (
            "QWidget#AIChatHistorySidebar {"
            "  background:#f6f8fb;"
            "  border-left:none;"
            "  border-right:1px solid #f1f5f9;"
            "  border-bottom-left-radius:8px;"
            "}"
            "QLineEdit#AIChatHistorySearchInput {"
            "  background:#ffffff;"
            "  border:1px solid #dbe3ee;"
            "  border-radius:8px;"
            "  padding:7px 10px;"
            "  color:#111827;"
            "  font-size:13px;"
            "  margin-left:1px;"
            "  margin-right:1px;"
            "}"
            "QLineEdit#AIChatHistorySearchInput:hover,"
            "QLineEdit#AIChatHistorySearchInput:focus {"
            "  border-color:#b8c4d6;"
            "}"
            "QPushButton#AIChatHistoryFilterButton {"
            "  background:#ffffff;"
            "  border:1px solid #dbe3ee;"
            "  border-radius:7px;"
            "  color:#64748b;"
            "  font-size:12px;"
            "  padding:3px 7px;"
            "  outline:none;"
            "}"
            "QPushButton#AIChatHistoryFilterButton:focus { outline:none; }"
            "QPushButton#AIChatHistoryFilterButton:hover {"
            "  border-color:#b8c4d6;"
            "  color:#334155;"
            "}"
            "QPushButton#AIChatHistoryFilterButton:checked {"
            "  background:#1f3a5f;"
            "  border-color:#1f3a5f;"
            "  color:#ffffff;"
            "}"
            "QListWidget#AIChatHistoryList {"
            "  background:transparent;"
            "  border:none;"
            "  outline:none;"
            "}"
            "QListWidget#AIChatHistoryList::item {"
            "  border-radius:8px;"
            "  margin:2px 0px;"
            "}"
            "QListWidget#AIChatHistoryList::item:selected {"
            "  background:#e7eef8;"
            "}"
            "QListWidget#AIChatHistoryList::item:hover:!selected {"
            "  background:#eef4fb;"
            "}"
            "QListWidget#AIChatHistoryList QScrollBar:vertical {"
            "  background:transparent;"
            "  width:6px;"
            "  margin:0px;"
            "}"
            "QListWidget#AIChatHistoryList QScrollBar::handle:vertical {"
            "  background:rgba(100,116,139,0.28);"
            "  border-radius:3px;"
            "  min-height:24px;"
            "}"
            "QListWidget#AIChatHistoryList QScrollBar::handle:vertical:hover {"
            "  background:rgba(100,116,139,0.46);"
            "}"
            "QListWidget#AIChatHistoryList QScrollBar::add-line:vertical,"
            "QListWidget#AIChatHistoryList QScrollBar::sub-line:vertical {"
            "  height:0px;"
            "}"
            "QListWidget#AIChatHistoryList QScrollBar::add-page:vertical,"
            "QListWidget#AIChatHistoryList QScrollBar::sub-page:vertical {"
            "  background:transparent;"
            "}"
            "QLabel#AIChatHistoryEmptyLabel {"
            "  color:#94a3b8;"
            "  font-size:13px;"
            "  background:transparent;"
            "  border:none;"
            "}"
            "QWidget#AIChatHistoryItem {"
            "  background:transparent;"
            "  border:none;"
            "  border-radius:8px;"
            "}"
            "QWidget#AIChatHistoryItem[hovered=\"true\"] {"
            "  background:#eef4fb;"
            "}"
            "QLabel#AIChatHistoryItemTitle {"
            "  color:#1f2937;"
            "  font-size:13px;"
            "  font-weight:600;"
            "  background:transparent;"
            "  border:none;"
            "}"
            "QLabel#AIChatHistoryItemMeta {"
            "  color:#64748b;"
            "  font-size:11px;"
            "  background:transparent;"
            "  border:none;"
            "}"
            "QCheckBox#AIChatHistoryItemCheck {"
            "  background:transparent;"
            "  border:none;"
            "  spacing:0px;"
            "}"
            "QCheckBox#AIChatHistoryItemCheck::indicator,"
            "QCheckBox#AIChatHistoryBatchSelectAll::indicator {"
            "  width:15px;"
            "  height:15px;"
            "  border:1px solid #cbd5e1;"
            "  border-radius:4px;"
            "  background:#ffffff;"
            "}"
            "QCheckBox#AIChatHistoryItemCheck::indicator:checked,"
            "QCheckBox#AIChatHistoryBatchSelectAll::indicator:checked {"
            "  background:#1f3a5f;"
            "  border-color:#1f3a5f;"
            "  image:url('__CHECKBOX_ICON__');"
            "}"
            "QWidget#AIChatHistoryBatchBar {"
            "  background:#ffffff;"
            "  border:1px solid #e2e8f0;"
            "  border-radius:8px;"
            "}"
            "QCheckBox#AIChatHistoryBatchSelectAll {"
            "  color:#475569;"
            "  font-size:12px;"
            "  background:transparent;"
            "  border:none;"
            "}"
            "QLabel#AIChatHistoryBatchCount {"
            "  color:#64748b;"
            "  font-size:12px;"
            "  background:transparent;"
            "  border:none;"
            "}"
            "QPushButton#AIChatHistoryBatchDeleteButton,"
            "QPushButton#AIChatHistoryBatchCancelButton {"
            "  border:none;"
            "  border-radius:6px;"
            "  padding:5px 8px;"
            "  font-size:12px;"
            "}"
            "QPushButton#AIChatHistoryBatchDeleteButton {"
            "  background:#1f3a5f;"
            "  color:#ffffff;"
            "}"
            "QPushButton#AIChatHistoryBatchDeleteButton:disabled {"
            "  background:#cbd5e1;"
            "  color:#f8fafc;"
            "}"
            "QPushButton#AIChatHistoryBatchDeleteButton:hover:!disabled {"
            "  background:#172c49;"
            "}"
            "QPushButton#AIChatHistoryBatchCancelButton {"
            "  background:#eef2f7;"
            "  color:#475569;"
            "}"
            "QPushButton#AIChatHistoryBatchCancelButton:hover {"
            "  background:#e2e8f0;"
            "  color:#0f172a;"
            "}"
        ).replace("__CHECKBOX_ICON__", checkbox_check_icon_url)
        self.setStyleSheet(style_sheet)

    def _active_query_filters(self) -> dict:
        return {
            "task_type_filter": self._task_filter,
            "search_query": self._search_text or None,
            "starred_only": bool(self._starred_only),
            "model_name_filter": None,
        }

    def _on_task_filter_changed(self, key: str) -> None:
        key = str(key or "all")
        for name, btn in self._task_filter_buttons.items():
            btn.blockSignals(True)
            try:
                btn.setChecked(name == key)
            finally:
                btn.blockSignals(False)
        self._task_filter = None if key in ("all", "starred") else key
        self._starred_only = key == "starred"
        self.refresh()

    def refresh(self) -> None:
        self._loaded_count = 0
        self._has_more = True
        self._list.clear()
        self._search_text = self._search_input.text().strip()
        self._last_group_title = ""
        self._load_next_page()

        has_records = self._list.count() > 0
        self._list.setVisible(has_records)
        self._empty_label.setVisible(not has_records)
        self._update_batch_controls()

    def _load_next_page(self) -> None:
        if self._store is None or self._loading_more or not self._has_more:
            return
        self._loading_more = True
        try:
            get_summaries = getattr(self._store, "get_record_summaries", None)
            query_records = get_summaries if callable(get_summaries) else self._store.get_records
            filters = self._active_query_filters()
            records = query_records(
                limit=self._page_limit,
                offset=self._loaded_count,
                task_type_filter=filters["task_type_filter"],
                search_query=filters["search_query"],
                starred_only=filters["starred_only"],
                model_name_filter=filters["model_name_filter"],
                pinned_first=True,
            )
        except Exception:
            records = []
        finally:
            self._loading_more = False

        if len(records) < self._page_limit:
            self._has_more = False
        if not records:
            return

        self._loaded_count += len(records)
        self._list.setUpdatesEnabled(False)
        try:
            for record in records:
                group_title = self._record_group_title(record)
                if group_title and group_title != self._last_group_title:
                    self._last_group_title = group_title
                    self._add_group_item(group_title)
                item = QListWidgetItem()
                record_id = int(record.get("id", 0) or 0)
                item.setData(Qt.ItemDataRole.UserRole, record_id)
                item.setData(_AI_CHAT_RECORD_ROLE, record)
                item.setData(_AI_CHAT_BATCH_SELECTED_ROLE, record_id in self._selected_batch_record_ids)
                item.setSizeHint(QSize(0, 64))
                self._list.addItem(item)
        finally:
            self._list.setUpdatesEnabled(True)

        self._list.setVisible(True)
        self._empty_label.hide()
        self._sync_list_scrollbar_visibility(self.underMouse())
        self._update_batch_controls()

    def _add_group_item(self, title: str) -> None:
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setData(Qt.ItemDataRole.UserRole, 0)
        item.setData(_AI_CHAT_GROUP_ROLE, str(title or "").strip())
        item.setSizeHint(QSize(0, 30))
        self._list.addItem(item)

    @staticmethod
    def _record_group_title(record: dict) -> str:
        if bool(record.get("is_pinned")):
            return "置顶"
        created_at = str(record.get("created_at", "") or "").strip()
        try:
            record_date = datetime.strptime(created_at[:10], "%Y-%m-%d").date()
        except Exception:
            return "更早"
        today = datetime.now().date()
        if record_date == today:
            return "今天"
        week_start = today - timedelta(days=today.weekday())
        if record_date >= week_start:
            return "本周"
        return "更早"

    def _on_scroll_value_changed(self, value: int) -> None:
        bar = self._list.verticalScrollBar()
        if bar is not None and value >= bar.maximum() - 24:
            self._load_next_page()

    def _sync_list_scrollbar_visibility(self, visible: bool) -> None:
        try:
            if bool(visible) and self._list.verticalScrollBar().maximum() > 0:
                self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
            else:
                self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        except Exception:
            pass

    def select_record(self, record_id: Optional[int]) -> None:
        if record_id is None:
            self._selected_record_id = None
            self._list.clearSelection()
            return
        target_id = int(record_id)
        self._selected_record_id = target_id
        guard = 0
        while self._has_more and guard < 20:
            if any(
                self._list.item(i) and int(self._list.item(i).data(Qt.ItemDataRole.UserRole) or 0) == target_id
                for i in range(self._list.count())
            ):
                break
            self._load_next_page()
            guard += 1
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and int(item.data(Qt.ItemDataRole.UserRole) or 0) == target_id:
                self._list.setCurrentItem(item)
                return

    def _activate_record(self, record_id: int) -> None:
        if record_id <= 0:
            return
        search_query = " ".join(str(getattr(self, "_search_text", "") or "").split())
        if self._batch_mode:
            item = self._item_for_record(record_id)
            if item is not None:
                self._set_record_selected(record_id, not bool(item.data(_AI_CHAT_BATCH_SELECTED_ROLE)))
            return
        if not search_query and int(getattr(self, "_selected_record_id", 0) or 0) == int(record_id):
            item = self._item_for_record(record_id)
            if item is not None:
                self._list.setCurrentItem(item)
            return
        self._selected_record_id = record_id
        item = self._item_for_record(record_id)
        if item is not None:
            self._list.setCurrentItem(item)
        if search_query:
            self.record_search_selected.emit(record_id, search_query)
            return
        self.record_selected.emit(record_id)

    def _show_record_menu(self, record: dict, anchor_or_pos: QWidget | QPoint) -> None:
        if self._store is None:
            return
        if not isinstance(record, dict) or int(record.get("id", 0) or 0) <= 0:
            return
        pinned = bool(record.get("is_pinned"))
        if isinstance(anchor_or_pos, QPoint):
            global_pos = anchor_or_pos
        else:
            global_pos = anchor_or_pos.mapToGlobal(
                QPoint(max(0, anchor_or_pos.width() - 4), anchor_or_pos.height())
            )
        starred = bool(record.get("is_starred"))
        menu_items = [
            ("重命名", lambda rec=record: self._rename_record(rec), True),
            ("取消置顶" if pinned else "置顶", lambda rec=record: self._toggle_pin_record(rec), True),
            ("取消收藏" if starred else "收藏", lambda rec=record: self._toggle_star_record(rec), True),
            ("删除", lambda rec=record: self._delete_record(rec), True),
            ("删除上下5条", lambda rec=record: self._delete_surrounding_records(rec), not pinned),
            ("-", None, True),
            ("批量删除", lambda: self._set_batch_mode(True), True),
        ]
        popup = OcrGenericMenuPopup(menu_items, parent=self)
        popup.show_at_pos(global_pos)

    def _rename_record(self, record: dict) -> None:
        if self._store is None:
            return
        record_id = int(record.get("id", 0) or 0)
        if record_id <= 0:
            return
        current_title = self._display_title(record)
        new_title, ok = QInputDialog.getText(self, "重命名记录", "新的标题：", text=current_title)
        new_title = " ".join(str(new_title or "").split()).strip()
        if not ok or not new_title or new_title == current_title:
            return
        try:
            renamed = self._store.rename_record(record_id, new_title)
        except Exception:
            renamed = False
        if renamed:
            self._refresh_preserving_selection()

    def _toggle_pin_record(self, record: dict) -> None:
        if self._store is None:
            return
        record_id = int(record.get("id", 0) or 0)
        if record_id <= 0:
            return
        try:
            self._store.set_pinned(record_id, not bool(record.get("is_pinned")))
        except Exception:
            return
        self._refresh_preserving_selection()

    def _toggle_star_record(self, record: dict) -> None:
        if self._store is None:
            return
        record_id = int(record.get("id", 0) or 0)
        if record_id <= 0:
            return
        try:
            toggle_starred = getattr(self._store, "toggle_starred", None)
            if callable(toggle_starred):
                toggle_starred(record_id)
            else:
                return
        except Exception:
            return
        self._refresh_preserving_selection()

    def _confirm_delete(self, title: str, text: str, info_text: str = "删除后将无法恢复，请谨慎操作。") -> bool:
        """弹出精心设计的确认删除弹窗，解决‘是’和‘否’按钮背景色不明显的问题。"""
        class ConfirmDeleteDialog(QDialog):
            def __init__(self, title_str: str, text_str: str, info_str: str, parent_widget: Optional[QWidget]) -> None:
                super().__init__(parent_widget)
                self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
                self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

                self.result_approved = False

                # 主外层布局，提供 margins 保证阴影不会被裁剪
                main_layout = QVBoxLayout(self)
                main_layout.setContentsMargins(16, 16, 16, 16)
                main_layout.setSpacing(0)

                # 容器 Frame
                self.container = QFrame(self)
                self.container.setObjectName("ConfirmDeleteContainer")
                self.container.setStyleSheet("""
                    QFrame#ConfirmDeleteContainer {
                        background-color: #f6f8fb;
                        border: none;
                        border-radius: 12px;
                    }
                """)

                # 精致弥散阴影
                shadow = QGraphicsDropShadowEffect(self)
                shadow.setBlurRadius(16)
                shadow.setColor(QColor(0, 0, 0, 45))
                shadow.setOffset(0, 4)
                self.container.setGraphicsEffect(shadow)

                main_layout.addWidget(self.container)

                # 容器内布局
                container_layout = QVBoxLayout(self.container)
                container_layout.setContentsMargins(20, 20, 20, 20)
                container_layout.setSpacing(14)

                # 内容行（图标 + 文字）
                content_layout = QHBoxLayout()
                content_layout.setSpacing(12)
                content_layout.setContentsMargins(0, 0, 0, 0)

                # 问号图标
                self.icon_label = QLabel(self.container)
                self.icon_label.setFixedSize(36, 36)
                icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxQuestion)
                pixmap = icon.pixmap(30, 30)
                self.icon_label.setPixmap(pixmap)
                self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                content_layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)

                # 文字布局
                text_layout = QVBoxLayout()
                text_layout.setSpacing(6)
                text_layout.setContentsMargins(0, 0, 0, 0)

                self.title_label = QLabel(text_str, self.container)
                self.title_label.setWordWrap(True)
                self.title_label.setStyleSheet("""
                    color: #0f172a;
                    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                    font-size: 14px;
                    font-weight: bold;
                    background: transparent;
                """)
                text_layout.addWidget(self.title_label)

                if info_str:
                    self.info_label = QLabel(info_str, self.container)
                    self.info_label.setWordWrap(True)
                    self.info_label.setStyleSheet("""
                        color: #64748b;
                        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                        font-size: 12px;
                        background: transparent;
                    """)
                    text_layout.addWidget(self.info_label)

                content_layout.addLayout(text_layout, 1)
                container_layout.addLayout(content_layout)

                # 按钮行
                btn_layout = QHBoxLayout()
                btn_layout.setSpacing(8)
                btn_layout.setContentsMargins(0, 4, 0, 0)
                btn_layout.addStretch(1)

                self.yes_btn = QPushButton("是(Y)", self.container)
                self.yes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                self.yes_btn.clicked.connect(self._on_yes)

                self.no_btn = QPushButton("否(N)", self.container)
                self.no_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                self.no_btn.clicked.connect(self._on_no)

                btn_layout.addWidget(self.yes_btn)
                btn_layout.addWidget(self.no_btn)
                container_layout.addLayout(btn_layout)

                # 按钮样式表
                self.setStyleSheet("""
                    QPushButton {
                        background: #f1f5f9;
                        color: #334155;
                        border: 1px solid #cbd5e1;
                        border-radius: 6px;
                        padding: 6px 14px;
                        font-weight: bold;
                        font-size: 12px;
                        min-width: 64px;
                    }
                    QPushButton:hover {
                        background: #e2e8f0;
                        color: #0f172a;
                    }
                    QPushButton[text="是(Y)"] {
                        background: #ef4444;
                        color: white;
                        border: none;
                    }
                    QPushButton[text="是(Y)"]:hover {
                        background: #dc2626;
                    }
                    QPushButton[text="是(Y)"]:pressed {
                        background: #b91c1c;
                    }
                    QPushButton[text="否(N)"] {
                        background: #ffffff;
                        color: #334155;
                        border: 1px solid #cbd5e1;
                    }
                    QPushButton[text="否(N)"]:hover {
                        background: #f1f5f9;
                        color: #0f172a;
                        border-color: #94a3b8;
                    }
                    QPushButton[text="否(N)"]:pressed {
                        background: #e2e8f0;
                    }
                """)

                self.no_btn.setFocus()

            def _on_yes(self) -> None:
                self.result_approved = True
                self.accept()

            def _on_no(self) -> None:
                self.result_approved = False
                self.reject()

            def keyPressEvent(self, event) -> None:
                if event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
                    if self.yes_btn.hasFocus():
                        self._on_yes()
                    else:
                        self._on_no()
                elif event.key() == Qt.Key.Key_Y:
                    self._on_yes()
                elif event.key() in (Qt.Key.Key_N, Qt.Key.Key_Escape):
                    self._on_no()
                else:
                    super().keyPressEvent(event)

        dialog = ConfirmDeleteDialog(title, text, info_text, self)
        dialog.exec()
        return dialog.result_approved


    def _delete_record(self, record: dict) -> None:
        if self._store is None:
            return
        record_id = int(record.get("id", 0) or 0)
        if record_id <= 0:
            return
        title = self._display_title(record)
        if not self._confirm_delete(
            "确认删除",
            f"确定要删除“{self._compact_text(title, 28)}”吗？"
        ):
            return
        try:
            deleted = self._store.delete_record(record_id)
        except Exception:
            deleted = False
        if not deleted:
            return
        if self._selected_record_id == record_id:
            self._selected_record_id = None
        self.records_deleted.emit([record_id])
        self.refresh()
        if self._selected_record_id is not None:
            self.select_record(self._selected_record_id)

    def _delete_surrounding_records(self, record: dict) -> None:
        if self._store is None:
            return
        record_id = int(record.get("id", 0) or 0)
        if record_id <= 0 or bool(record.get("is_pinned")):
            return
        record_ids = self._surrounding_record_ids(record_id, radius=5)
        if not record_ids:
            return
        if not self._confirm_delete(
            "确认批量删除",
            f"确定要删除此记录及上下最多 5 条非置顶历史记录（共 {len(record_ids)} 条）吗？\n置顶记录不会删除。"
        ):
            return
        try:
            deleted_count = self._store.delete_records(record_ids)
        except Exception:
            deleted_count = 0
        if deleted_count <= 0:
            return
        deleted_ids = set(record_ids)
        self._selected_batch_record_ids.difference_update(deleted_ids)
        if self._selected_record_id in deleted_ids:
            self._selected_record_id = None
        self.records_deleted.emit(record_ids)
        self.refresh()
        if self._selected_record_id is not None:
            self.select_record(self._selected_record_id)

    def _refresh_preserving_selection(self) -> None:
        selected_id = self._selected_record_id
        self.refresh()
        if selected_id is not None:
            self.select_record(selected_id)

    def _set_batch_mode(self, enabled: bool) -> None:
        self._batch_mode = bool(enabled)
        if not self._batch_mode:
            self._selected_batch_record_ids.clear()
        self._batch_bar.setVisible(self._batch_mode)
        for item in self._iter_loaded_items():
            record_id = int(item.data(Qt.ItemDataRole.UserRole) or 0)
            item.setData(_AI_CHAT_BATCH_SELECTED_ROLE, record_id in self._selected_batch_record_ids)
        self._list.viewport().update()
        self._update_batch_controls()

    def _set_record_selected(self, record_id: int, selected: bool) -> None:
        record_id = int(record_id)
        if record_id <= 0:
            return
        if selected:
            self._selected_batch_record_ids.add(record_id)
        else:
            self._selected_batch_record_ids.discard(record_id)
        item = self._item_for_record(record_id)
        if item is not None:
            item.setData(_AI_CHAT_BATCH_SELECTED_ROLE, bool(selected))
            self._list.viewport().update(self._list.visualItemRect(item))
        self._update_batch_controls()

    def _on_select_all_toggled(self, checked: bool) -> None:
        visible_ids = self._visible_record_ids()
        if checked:
            self._selected_batch_record_ids.update(visible_ids)
        else:
            self._selected_batch_record_ids.difference_update(visible_ids)
        for item in self._iter_loaded_items():
            record_id = int(item.data(Qt.ItemDataRole.UserRole) or 0)
            item.setData(_AI_CHAT_BATCH_SELECTED_ROLE, record_id in self._selected_batch_record_ids)
        self._list.viewport().update()
        self._update_batch_controls()

    def _on_batch_delete_clicked(self) -> None:
        if self._store is None:
            return
        record_ids = sorted(self._selected_batch_record_ids)
        if not record_ids:
            return
        if not self._confirm_delete(
            "确认批量删除",
            f"确定要删除选中的 {len(record_ids)} 条历史记录吗？"
        ):
            return
        try:
            deleted_count = self._store.delete_records(record_ids)
        except Exception:
            deleted_count = 0
        if deleted_count <= 0:
            return
        deleted_ids = set(record_ids)
        if self._selected_record_id in deleted_ids:
            self._selected_record_id = None
        self.records_deleted.emit(record_ids)
        self._set_batch_mode(False)
        self.refresh()
        if self._selected_record_id is not None:
            self.select_record(self._selected_record_id)

    def _update_batch_controls(self) -> None:
        selected_count = len(self._selected_batch_record_ids)
        self._batch_count_label.setText(f"已选 {selected_count} 项")
        self._batch_delete_btn.setEnabled(selected_count > 0)
        visible_ids = self._visible_record_ids()
        self._batch_select_all.setEnabled(bool(visible_ids))
        self._batch_select_all.blockSignals(True)
        try:
            all_visible_selected = (
                bool(visible_ids)
                and all(record_id in self._selected_batch_record_ids for record_id in visible_ids)
            )
            self._batch_select_all.setChecked(all_visible_selected)
        finally:
            self._batch_select_all.blockSignals(False)

    def _iter_loaded_items(self) -> list[QListWidgetItem]:
        items: list[QListWidgetItem] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is not None:
                items.append(item)
        return items

    def _item_for_record(self, record_id: int) -> Optional[QListWidgetItem]:
        for item in self._iter_loaded_items():
            if int(item.data(Qt.ItemDataRole.UserRole) or 0) == int(record_id):
                return item
        return None

    def _row_for_record(self, record_id: int) -> int:
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None and int(item.data(Qt.ItemDataRole.UserRole) or 0) == int(record_id):
                return row
        return -1

    def _surrounding_record_ids(self, record_id: int, radius: int = 5) -> list[int]:
        record_ids = self._loaded_unpinned_record_ids_in_order()
        try:
            record_index = record_ids.index(int(record_id))
        except ValueError:
            return []
        safe_radius = max(0, int(radius))
        while record_index + safe_radius >= len(record_ids) and self._has_more:
            before_count = len(record_ids)
            self._load_next_page()
            record_ids = self._loaded_unpinned_record_ids_in_order()
            if len(record_ids) <= before_count:
                break
            try:
                record_index = record_ids.index(int(record_id))
            except ValueError:
                return []
        start_index = max(0, record_index - safe_radius)
        end_index = min(len(record_ids), record_index + safe_radius + 1)
        return record_ids[start_index:end_index]

    def _loaded_unpinned_record_ids_in_order(self) -> list[int]:
        ids: list[int] = []
        for item in self._iter_loaded_items():
            record = self._record_from_item(item)
            record_id = int(record.get("id", 0) or 0)
            if record_id > 0 and not bool(record.get("is_pinned")):
                ids.append(record_id)
        return ids

    def _loaded_record_ids_in_order(self) -> list[int]:
        ids: list[int] = []
        for item in self._iter_loaded_items():
            record_id = int(item.data(Qt.ItemDataRole.UserRole) or 0)
            if record_id > 0:
                ids.append(record_id)
        return ids

    def _visible_record_ids(self) -> set[int]:
        return {
            int(item.data(Qt.ItemDataRole.UserRole) or 0)
            for item in self._iter_loaded_items()
            if int(item.data(Qt.ItemDataRole.UserRole) or 0) > 0
        }

    def _record_from_item(self, item: Optional[QListWidgetItem]) -> dict:
        if item is None:
            return {}
        record = item.data(_AI_CHAT_RECORD_ROLE)
        return record if isinstance(record, dict) else {}

    def _item_at_pos(self, pos: QPoint) -> Optional[QListWidgetItem]:
        index = self._list.indexAt(pos)
        if not index.isValid():
            return None
        return self._list.item(index.row())

    def _event_pos(self, event) -> QPoint:
        try:
            return event.position().toPoint()
        except Exception:
            try:
                return event.pos()
            except Exception:
                return QPoint()

    def _event_global_pos(self, event) -> QPoint:
        try:
            return event.globalPosition().toPoint()
        except Exception:
            try:
                return event.globalPos()
            except Exception:
                return self._list.viewport().mapToGlobal(self._event_pos(event))

    def _row_rect_for_item(self, item: QListWidgetItem) -> QRect:
        return self._list.visualItemRect(item).adjusted(0, 2, 0, -2)

    @staticmethod
    def _checkbox_rect_for_row_rect(row_rect: QRect) -> QRect:
        return QRect(row_rect.left() + 8, row_rect.center().y() - 8, 16, 16)

    @staticmethod
    def _menu_rect_for_row_rect(row_rect: QRect) -> QRect:
        return QRect(row_rect.right() - 32, row_rect.top() + 8, 24, 18)

    def _update_hovered_row(self, pos: QPoint) -> None:
        index = self._list.indexAt(pos)
        row = index.row() if index.isValid() else -1
        if row != self._hovered_row:
            old_row = self._hovered_row
            self._hovered_row = row
            for changed_row in (old_row, row):
                if 0 <= changed_row < self._list.count():
                    item = self._list.item(changed_row)
                    if item is not None:
                        self._list.viewport().update(self._list.visualItemRect(item))
        self._update_viewport_cursor(pos)

    def _update_viewport_cursor(self, pos: QPoint) -> None:
        item = self._item_at_pos(pos)
        if item is None:
            self._list.viewport().unsetCursor()
            return
        row_rect = self._row_rect_for_item(item)
        over_action = False
        if self._batch_mode:
            over_action = self._checkbox_rect_for_row_rect(row_rect).contains(pos)
        else:
            over_action = self._menu_rect_for_row_rect(row_rect).contains(pos)
        if over_action:
            self._list.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self._list.viewport().unsetCursor()

    def _handle_viewport_click(self, pos: QPoint) -> bool:
        item = self._item_at_pos(pos)
        if item is None:
            return False
        record_id = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        if record_id <= 0:
            return False
        row_rect = self._row_rect_for_item(item)
        if self._batch_mode and self._checkbox_rect_for_row_rect(row_rect).contains(pos):
            self._set_record_selected(record_id, not bool(item.data(_AI_CHAT_BATCH_SELECTED_ROLE)))
            return True
        if not self._batch_mode and self._menu_rect_for_row_rect(row_rect).contains(pos):
            menu_rect = self._menu_rect_for_row_rect(row_rect)
            global_pos = self._list.viewport().mapToGlobal(QPoint(menu_rect.right(), menu_rect.bottom()))
            self._list.setCurrentItem(item)
            self._selected_record_id = record_id
            self._show_record_menu(self._record_from_item(item), global_pos)
            return True
        self._activate_record(record_id)
        return True

    def _handle_viewport_context_menu(self, pos: QPoint, global_pos: QPoint) -> bool:
        if self._batch_mode:
            return False
        item = self._item_at_pos(pos)
        if item is None:
            return False
        record = self._record_from_item(item)
        if not record:
            return False
        self._list.setCurrentItem(item)
        self._selected_record_id = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        self._show_record_menu(record, global_pos)
        return True

    def eventFilter(self, obj, event) -> bool:
        list_widget = self.__dict__.get("_list")
        if list_widget is None or sip.isdeleted(list_widget):
            return False
        viewport = list_widget.viewport()
        if obj in {self, self._list, viewport}:
            if event.type() == QEvent.Type.Enter:
                self._sync_list_scrollbar_visibility(True)
            elif event.type() == QEvent.Type.Leave:
                if obj is viewport and self._hovered_row != -1:
                    old_row = self._hovered_row
                    self._hovered_row = -1
                    item = self._list.item(old_row) if 0 <= old_row < self._list.count() else None
                    if item is not None:
                        self._list.viewport().update(self._list.visualItemRect(item))
                    self._list.viewport().unsetCursor()
                single_shot_scoped(80, self, lambda: self._sync_list_scrollbar_visibility(self.underMouse()))
            elif obj is viewport and event.type() == QEvent.Type.MouseMove:
                self._sync_list_scrollbar_visibility(True)
                self._update_hovered_row(self._event_pos(event))
            elif obj is viewport and event.type() == QEvent.Type.ContextMenu:
                if self._handle_viewport_context_menu(self._event_pos(event), self._event_global_pos(event)):
                    return True
            elif obj is viewport and event.type() == QEvent.Type.MouseButtonRelease:
                try:
                    is_left_button = event.button() == Qt.MouseButton.LeftButton
                except Exception:
                    is_left_button = False
                if is_left_button and self._handle_viewport_click(self._event_pos(event)):
                    return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _display_title(record: dict) -> str:
        title = " ".join(str(record.get("title", "") or "").split())
        source_text = " ".join(str(record.get("source_text", "") or "").split())
        display_title = title or source_text or "新对话"
        if str(record.get("task_type", "") or "").strip() == "translate":
            if display_title.startswith("[翻译]"):
                return display_title
            return f"[翻译]{display_title}"
        return display_title

    @staticmethod
    def _display_model_name(record: dict) -> str:
        model_name = " ".join(str(record.get("model_name", "") or "").split())
        if " | " in model_name:
            model_name = model_name.split(" | ", 1)[0].strip()
        return model_name

    def _search_snippet(self, record: dict, limit: int = 80) -> str:
        limit = max(12, int(limit))
        terms = _ai_history_search_terms(getattr(self, "_search_text", ""))
        candidates = (
            str(record.get("result_text", "") or ""),
            str(record.get("source_text", "") or ""),
            str(record.get("prompt_text", "") or ""),
        )

        if terms:
            for raw_text in candidates:
                text = " ".join(str(raw_text or "").split())
                if not text:
                    continue
                start, length = _first_search_match(text, terms)
                if start < 0:
                    continue
                half = max(4, int((limit - max(1, length)) / 2))
                snippet_start = max(0, start - half)
                snippet_end = min(len(text), snippet_start + limit)
                snippet_start = max(0, min(snippet_start, snippet_end - limit))
                prefix = "..." if snippet_start > 0 else ""
                suffix = "..." if snippet_end < len(text) else ""
                return f"{prefix}{text[snippet_start:snippet_end]}{suffix}"

        for raw_text in candidates:
            snippet = self._compact_text(str(raw_text or ""), limit)
            if snippet:
                return snippet
        return ""

    def _record_meta_text(self, record: dict, limit: int = 80) -> str:
        created_at = str(record.get("created_at", "") or "").strip()
        if len(created_at) >= 16:
            created_at = created_at[5:16]
        has_search = bool(_ai_history_search_terms(getattr(self, "_search_text", "")))
        model_name = self._display_model_name(record) if has_search else ""
        preview = self._search_snippet(record, limit=limit)
        return "  ".join(part for part in (created_at, f"模型: {model_name}" if model_name else "", preview) if part)

    @staticmethod
    def _compact_text(text: str, limit: int) -> str:
        value = " ".join(str(text or "").split())
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 1)] + "..."
