"""翻译/问答历史记录浏览面板。

嵌入到 OcrTextPanel 的 QStackedWidget 中，提供两栏式的
历史记录浏览功能，包括搜索、筛选、收藏、导出等。
"""
from __future__ import annotations

import time
import weakref
from datetime import datetime, timedelta
from typing import Any, Optional

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QCursor, QFont, QFontMetrics, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from deepcat.ui.popup_behavior import set_disable_global_tooltip
from deepcat.ui.timer_scope import single_shot_scoped

# ------------------------------------------------------------------
# 常量
# ------------------------------------------------------------------

_TASK_TYPE_COLORS: dict[str, str] = {
    "translate": "#58A9FF",
    "qa": "#7C5CFC",
    "reply_prompt": "#FF8C42",
    "ai_search": "#4CAF50",
    "explain": "#F06292",
    "summary": "#26A69A",
}

_TASK_TYPE_TAGS: dict[str, str] = {
    "translate": "翻",
    "qa": "问",
    "reply_prompt": "回",
    "ai_search": "搜",
    "explain": "解",
    "summary": "总",
}

_TASK_TYPE_LABELS: dict[str, str] = {
    "translate": "翻译",
    "qa": "问答",
    "reply_prompt": "回复",
    "ai_search": "搜索",
    "explain": "解释",
    "summary": "总结",
}

_RESULT_LABELS: dict[str, str] = {
    "translate": "翻译结果",
    "qa": "问答结果",
    "reply_prompt": "回复结果",
    "ai_search": "搜索结果",
    "explain": "解释结果",
    "summary": "总结结果",
}


class DetailHeaderWidget(QWidget):
    """详情标题组件。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("DetailHeaderWidget")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.title_label = QLabel()
        self.title_label.setFont(QFont("Microsoft YaHei UI", 11, QFont.Weight.Bold))
        self.title_label.setStyleSheet("color: #111827; background: transparent; border: none;")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label, 1)


class HistoryListItemWidget(QWidget):
    """左侧历史列表项自定义组件，悬停时显示右侧功能按钮。"""

    @property
    def _panel(self) -> Optional[TranslationHistoryPanel]:
        return self._panel_ref()

    def __init__(self, record: dict, panel: TranslationHistoryPanel, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._record = record
        self._panel_ref = weakref.ref(panel)
        self.setObjectName("HistoryListItemWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 4, 4)
        layout.setSpacing(4)

        self.checkbox = QCheckBox(self)
        self.checkbox.setChecked(False)
        self.checkbox.setVisible(bool(panel._batch_mode))
        layout.addWidget(self.checkbox)

        tag = _TASK_TYPE_TAGS.get(record.get("task_type", "translate"), "翻")
        source_full = str(record.get("source_text", "")).replace("\n", " ").strip() or "(空)"
        if len(source_full) > 1000:
            source_full = source_full[:1000] + "..."
        starred = "⭐ " if record.get("is_starred") else ""
        self._full_text = f"[{tag}] {starred}{source_full}"

        created = str(record.get("created_at", ""))
        if len(created) > 16:
            created = created[11:16]

        self._source_label = QLabel()
        self._source_label.setStyleSheet("background: transparent; border: none; color: #334155; font-size: 12px;")
        self._source_label.setWordWrap(False)
        self._source_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._source_label, 1)

        self._time_label = QLabel(created)
        self._time_label.setStyleSheet("background: transparent; border: none; color: #94a3b8; font-size: 11px;")
        self._time_label.setWordWrap(False)
        self._time_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._time_label.setMinimumWidth(38)
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._time_label)

        # 悬浮按钮的不透明容器
        self._btn_container = QWidget(self)
        self._btn_container.setObjectName("HoverBtnContainer")
        self._btn_container.setFixedSize(98, 24)

        btn_layout = QHBoxLayout(self._btn_container)
        btn_layout.setContentsMargins(3, 2, 3, 2)
        btn_layout.setSpacing(4)

        self.btn_export = QPushButton("📤", self._btn_container)
        self.btn_export.setToolTip("导出")
        self.btn_export.setFixedSize(20, 20)
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.setObjectName("HoverIconBtn")
        self.btn_export.clicked.connect(self._on_export)

        self.btn_delete = QPushButton("🗑", self._btn_container)
        self.btn_delete.setToolTip("删除")
        self.btn_delete.setFixedSize(20, 20)
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.setObjectName("HoverIconBtn")
        self.btn_delete.clicked.connect(self._on_delete)

        self.btn_copy = QPushButton("📋", self._btn_container)
        self.btn_copy.setToolTip("复制")
        self.btn_copy.setFixedSize(20, 20)
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setObjectName("HoverIconBtn")
        self.btn_copy.clicked.connect(self._on_copy)

        self.btn_star = QPushButton("★" if record.get("is_starred") else "☆", self._btn_container)
        self.btn_star.setToolTip("收藏")
        self.btn_star.setFixedSize(20, 20)
        self.btn_star.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_star.setObjectName("HoverIconBtn")
        self.btn_star.clicked.connect(self._on_toggle_star)

        btn_layout.addWidget(self.btn_export)
        btn_layout.addWidget(self.btn_delete)
        btn_layout.addWidget(self.btn_copy)
        btn_layout.addWidget(self.btn_star)

        self.hover_buttons = [self.btn_export, self.btn_delete, self.btn_copy, self.btn_star]
        self._btn_container.hide()
        self._update_button_positions()
        self._update_elided_text()

        # Smooth tooltip
        # 共用 TranslationHistoryPanel 级的全局单例，避免重复创建数以百计的顶级 OS 系统窗口
        self._tooltip = panel._shared_tooltip
        self._tooltip_timer: Optional[QTimer] = None
        for btn in self.hover_buttons:
            set_disable_global_tooltip(btn)
            btn.installEventFilter(self)

    def _update_button_positions(self) -> None:
        w = self.width()
        h = self.height()
        container_w = 98
        container_h = 24

        time_x = self._time_label.x()
        time_w = self._time_label.width()
        if time_x > 0 and time_w > 0:
            time_right = time_x + time_w
        else:
            time_right = w - 4

        x = time_right - container_w - 4
        y = (h - container_h) // 2
        self._btn_container.move(x, y)

    def _update_elided_text(self) -> None:
        w = self.width()
        if w <= 0:
            return

        # 动态计算保留宽度
        checkbox_w = 22 if self.checkbox.isVisible() else 0
        if not self._time_label.isVisible():
            reserved = 6 + 4 + 4 + 98 + 10 + checkbox_w  # 122
        else:
            time_w = self._time_label.sizeHint().width()
            if time_w <= 0:
                time_w = 38
            reserved = 6 + 4 + 4 + time_w + 10 + checkbox_w  # 62 左右

        max_w = w - reserved
        if max_w < 40:
            max_w = 40

        fm = self._source_label.fontMetrics()
        elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideRight, max_w)
        self._source_label.setText(elided)

    def resizeEvent(self, event) -> None:
        self._update_button_positions()
        self._update_elided_text()
        super().resizeEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            # 彻底屏蔽 Qt 原生系统 ToolTip
            event.accept()
            return True
        return super().event(event)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            event.accept()
            return True
        if event.type() == QEvent.Type.Enter and isinstance(obj, QPushButton) and obj.toolTip():
            self._tooltip.hide()
            pos = obj.mapToGlobal(QPoint(int(obj.width() / 2), int(obj.height() + 4)))
            self._tooltip.show_text(obj.toolTip(), pos, padding=21)
            if self._tooltip_timer is not None:
                self._tooltip_timer.stop()
            self._tooltip_timer = QTimer(self)
            self._tooltip_timer.setSingleShot(True)
            self._tooltip_timer.setInterval(2500)
            self._tooltip_timer.timeout.connect(self._tooltip.hide)
            self._tooltip_timer.start()
        elif event.type() == QEvent.Type.Leave and isinstance(obj, QPushButton):
            self._tooltip.hide()
            if self._tooltip_timer is not None:
                self._tooltip_timer.stop()
                self._tooltip_timer = None
        return super().eventFilter(obj, event)

    def enterEvent(self, event) -> None:
        self._update_button_positions()
        self._time_label.hide()
        self._btn_container.show()
        self._update_elided_text()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._tooltip.hide()
        if self._tooltip_timer is not None:
            self._tooltip_timer.stop()
            self._tooltip_timer = None
        self._time_label.show()
        QTimer.singleShot(50, self._maybe_hide_buttons)
        super().leaveEvent(event)

    def _maybe_hide_buttons(self) -> None:
        if not self.underMouse():
            self._time_label.show()
            self._btn_container.hide()
            self._update_elided_text()

    def mousePressEvent(self, event) -> None:
        if self.checkbox.underMouse():
            super().mousePressEvent(event)
            return
        if not any(btn.underMouse() for btn in self.hover_buttons):
            for i in range(self._panel._list.count()):
                item = self._panel._list.item(i)
                if item and item.data(Qt.ItemDataRole.UserRole) == self._record.get("id"):
                    self._panel._list.setCurrentItem(item)
                    break
        super().mousePressEvent(event)

    def _on_export(self) -> None:
        self._panel._export_record(self._record)

    def _on_delete(self) -> None:
        self._panel._delete_record(self._record)

    def _on_copy(self) -> None:
        self._panel._copy_record(self._record)

    def _on_toggle_star(self) -> None:
        self._panel._toggle_star_record(self._record)


class HistoryTitleBar(QWidget):
    """支持窗口拖拽的实体标题栏。"""

    @staticmethod
    def _make_title_icon(size: int, pen_width: float, color: QColor, draw_fn) -> QIcon:
        """用 QPainter 手绘细线条风格图标。"""
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(color, pen_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        draw_fn(painter, size, pen_width)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _make_minimize_icon(size: int = 16) -> QIcon:
        """最小化图标：中间单条细水平线。"""
        def draw(painter, s, pw):
            m = pw * 3
            mid_y = s / 2
            painter.drawLine(QPointF(m, mid_y), QPointF(s - m, mid_y))

        pix_normal = QPixmap(size, size)
        pix_normal.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix_normal)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#475569"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        draw(painter, size, 1.2)
        painter.end()

        pix_active = QPixmap(size, size)
        pix_active.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix_active)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#0f172a"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        draw(painter, size, 1.2)
        painter.end()

        icon = QIcon()
        icon.addPixmap(pix_normal, QIcon.Mode.Normal)
        icon.addPixmap(pix_active, QIcon.Mode.Active)
        return icon

    @staticmethod
    def _make_maximize_icon(size: int = 16) -> QIcon:
        """最大化图标：细线空心方框。"""
        def draw(painter, s, pw):
            m = pw * 2
            painter.drawRoundedRect(QRectF(m, m, s - m * 2, s - m * 2), 1.5, 1.5)

        pix_normal = QPixmap(size, size)
        pix_normal.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix_normal)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#475569"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        draw(painter, size, 1.2)
        painter.end()

        pix_active = QPixmap(size, size)
        pix_active.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix_active)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#0f172a"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        draw(painter, size, 1.2)
        painter.end()

        icon = QIcon()
        icon.addPixmap(pix_normal, QIcon.Mode.Normal)
        icon.addPixmap(pix_active, QIcon.Mode.Active)
        return icon

    @staticmethod
    def _make_restore_icon(size: int = 16) -> QIcon:
        """还原图标：两个重叠的细线空心方框。"""
        def draw_with_color(painter, s, pw, color):
            m = pw * 2
            offset = pw * 2.5
            # 后方框（右下）
            painter.setOpacity(0.35)
            painter.setPen(QPen(color, 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawRoundedRect(QRectF(m + offset, m, s - m * 2 - offset, s - m * 2 - offset), 1.5, 1.5)
            # 前方框（左上）
            painter.setOpacity(1.0)
            painter.drawRoundedRect(QRectF(m, m + offset, s - m * 2 - offset, s - m * 2 - offset), 1.5, 1.5)

        pix_normal = QPixmap(size, size)
        pix_normal.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix_normal)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        draw_with_color(painter, size, 1.2, QColor("#475569"))
        painter.end()

        pix_active = QPixmap(size, size)
        pix_active.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix_active)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        draw_with_color(painter, size, 1.2, QColor("#0f172a"))
        painter.end()

        icon = QIcon()
        icon.addPixmap(pix_normal, QIcon.Mode.Normal)
        icon.addPixmap(pix_active, QIcon.Mode.Active)
        return icon

    @staticmethod
    def _make_close_icon(size: int = 16) -> QIcon:
        """关闭图标：细线 X。"""
        def draw(painter, s, pw):
            m = pw * 3
            painter.drawLine(QPointF(m, m), QPointF(s - m, s - m))
            painter.drawLine(QPointF(s - m, m), QPointF(m, s - m))

        pix_normal = QPixmap(size, size)
        pix_normal.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix_normal)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#475569"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        draw(painter, size, 1.2)
        painter.end()

        pix_active = QPixmap(size, size)
        pix_active.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix_active)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#ffffff"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        draw(painter, size, 1.2)
        painter.end()

        icon = QIcon()
        icon.addPixmap(pix_normal, QIcon.Mode.Normal)
        icon.addPixmap(pix_active, QIcon.Mode.Active)
        return icon

    @staticmethod
    def _make_back_icon(size: int = 16) -> QIcon:
        """返回图标：细线左箭头。"""
        def draw(painter, s, pw):
            m = pw * 3
            mid_y = s / 2
            # 水平线
            painter.drawLine(QPointF(m, mid_y), QPointF(s - m, mid_y))
            # 箭头尖
            painter.drawLine(QPointF(m, mid_y), QPointF(m + pw * 3, mid_y - pw * 3))
            painter.drawLine(QPointF(m, mid_y), QPointF(m + pw * 3, mid_y + pw * 3))
        return HistoryTitleBar._make_title_icon(size, 1.2, QColor("#475569"), draw)

    def __init__(self, panel: TranslationHistoryPanel, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._panel = panel
        self.setObjectName("HistoryTitleBar")
        self.setFixedHeight(30)
        self._dragging = False
        self._drag_offset = QPoint()
        self._icon_size = 16

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)

        # 返回按钮：细线左箭头 + "返回" 文本
        self.btn_return = QPushButton("返回")
        self.btn_return.setIcon(self._make_back_icon(self._icon_size))
        self.btn_return.setIconSize(QSize(self._icon_size, self._icon_size))
        self.btn_return.setToolTip("返回原划词窗口")
        self.btn_return.setFixedHeight(24)
        self.btn_return.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_return.setObjectName("TitleBarBtnBack")
        layout.addWidget(self.btn_return)

        self.title_label = QLabel("历史记录")
        self.title_label.setFont(QFont("Microsoft YaHei UI", 11, QFont.Weight.Bold))
        self.title_label.setStyleSheet("color: #1e293b; background: transparent; border: none; padding-right: 4px;")
        layout.addWidget(self.title_label)

        layout.addStretch(1)

        # 嵌套的控制按钮布局：相邻无缝拼合
        self.control_layout = QHBoxLayout()
        self.control_layout.setContentsMargins(0, 0, 0, 0)
        self.control_layout.setSpacing(0)

        # 最小化按钮：细线杠
        self.btn_minimize = QPushButton()
        self.btn_minimize.setIcon(self._make_minimize_icon(self._icon_size))
        self.btn_minimize.setIconSize(QSize(self._icon_size, self._icon_size))
        self.btn_minimize.setToolTip("最小化")
        self.btn_minimize.setFixedSize(46, 30)
        self.btn_minimize.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_minimize.setObjectName("TitleBarBtn")
        self.control_layout.addWidget(self.btn_minimize)

        # 全屏按钮：细线最大化图标（空心方框）
        self.btn_fullscreen = QPushButton()
        self.btn_fullscreen.setIcon(self._make_maximize_icon(self._icon_size))
        self.btn_fullscreen.setIconSize(QSize(self._icon_size, self._icon_size))
        self.btn_fullscreen.setToolTip("全屏查看")
        self.btn_fullscreen.setFixedSize(46, 30)
        self.btn_fullscreen.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fullscreen.setObjectName("TitleBarBtn")
        self.control_layout.addWidget(self.btn_fullscreen)

        # 关闭按钮：细线 X
        self.btn_close = QPushButton()
        self.btn_close.setIcon(self._make_close_icon(self._icon_size))
        self.btn_close.setIconSize(QSize(self._icon_size, self._icon_size))
        self.btn_close.setToolTip("关闭窗口")
        self.btn_close.setFixedSize(46, 30)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setObjectName("TitleBarBtnClose")
        self.control_layout.addWidget(self.btn_close)

        layout.addLayout(self.control_layout)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            win = self.window()
            try:
                g_pos = event.globalPosition().toPoint()
            except Exception:
                g_pos = event.globalPos()
            self._drag_offset = g_pos - win.frameGeometry().topLeft()
            self._drag_start_pos = g_pos
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            win = self.window()
            try:
                g_pos = event.globalPosition().toPoint()
            except Exception:
                g_pos = event.globalPos()
            target_pos = g_pos - self._drag_offset
            if hasattr(win, "_clamp_window_pos"):
                win.move(win._clamp_window_pos(target_pos))
            else:
                win.move(target_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            win = self.window()
            if hasattr(win, "_is_collapsed") and win._is_collapsed:
                try:
                    g_pos = event.globalPosition().toPoint()
                except Exception:
                    g_pos = event.globalPos()
                start_pos = getattr(self, "_drag_start_pos", g_pos)
                dist = (g_pos - start_pos).manhattanLength()

                try:
                    p = event.position().toPoint()
                except AttributeError:
                    p = event.pos()

                # 排除点击在关闭按钮或最小化按钮上
                local_close = self.btn_close.mapFrom(self, p)
                local_min = self.btn_minimize.mapFrom(self, p)
                is_click_close = self.btn_close.rect().contains(local_close)
                is_click_min = self.btn_minimize.rect().contains(local_min)

                if dist < 5 and not is_click_close and not is_click_min:
                    if hasattr(win, "_toggle_collapse"):
                        win._toggle_collapse(False)
                    event.accept()
                    return
                elif dist >= 5:
                    if hasattr(win, "_snap_collapsed_to_edge"):
                        win._snap_collapsed_to_edge()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        win = self.window()
        if hasattr(win, "_is_collapsed") and win._is_collapsed:
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu {
                    background-color: #ffffff;
                    border: 1px solid #e2e8f0;
                    border-radius: 8px;
                    padding: 4px;
                }
                QMenu::item {
                    padding: 6px 20px;
                    border-radius: 4px;
                    color: #334155;
                    font-size: 12px;
                }
                QMenu::item:selected {
                    background-color: rgba(37, 99, 235, 0.08);
                    color: #2563eb;
                }
            """)

            restore_action = QAction("还原", self)
            restore_action.triggered.connect(lambda: win._toggle_collapse(False))

            close_action = QAction("关闭", self)
            close_action.triggered.connect(win.close)

            menu.addAction(restore_action)
            menu.addAction(close_action)

            try:
                g_pos = event.globalPosition().toPoint()
            except Exception:
                g_pos = event.globalPos()
            menu.exec(g_pos)
            event.accept()
        else:
            super().contextMenuEvent(event)

    def paintEvent(self, event) -> None:
        from PyQt6.QtWidgets import QStyle, QStyleOption
        from PyQt6.QtGui import QPainter
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, self)


class TranslationHistoryPanel(QWidget):
    """翻译/问答历史记录浏览面板。"""

    close_requested = pyqtSignal()
    fullscreen_requested = pyqtSignal(bool)

    def paintEvent(self, event) -> None:
        from PyQt6.QtWidgets import QStyle, QStyleOption
        from PyQt6.QtGui import QPainter
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, self)

    def __init__(self, history_store: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("TranslationHistoryPanel")
        self._store = history_store
        self._current_filter: Optional[str] = None  # None=全部
        self._starred_only = False
        self._search_text = ""
        self._current_record: Optional[dict] = None
        self._is_fullscreen = False
        self._text_selection_update_block_until = 0.0
        self._text_selection_event_sources: list[object] = []
        self._batch_mode = False

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._do_search)

        # 分页与懒加载管理
        self._loaded_count = 0
        self._page_limit = 50
        self._all_records: list[dict] = []
        self._total_count = 0
        self._loading_more = False

        # 实例化全局唯一的 SmoothToolTip，供所有 ListItemWidget 共享，极大节省窗口句柄和 CPU 开销
        from deepcat.ui.post_capture_actions import SmoothToolTip
        self._shared_tooltip = SmoothToolTip()

        self._build_ui()
        self._apply_styles()

    # ------------------------------------------------------------------
    # 构建 UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        # ---- 顶栏 ----
        self._title_bar = HistoryTitleBar(self)
        root.addWidget(self._title_bar)

        # ---- 底部实体状态栏 ----
        self._status_bar = QWidget()
        self._status_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._status_bar.setObjectName("HistoryStatusBar")
        self._status_bar.setFixedHeight(28)
        status_layout = QHBoxLayout(self._status_bar)
        status_layout.setContentsMargins(12, 0, 12, 0)

        self._count_label = QLabel("共 0 条记录")
        self._count_label.setStyleSheet(
            "color: #64748b; font-size: 11px; background: transparent; border: none; font-weight: bold;"
        )
        status_layout.addWidget(self._count_label)

        # 批量操作按钮
        self._btn_batch = QPushButton("批量操作")
        self._btn_batch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_batch.setObjectName("StatusBatchBtn")
        self._btn_batch.clicked.connect(lambda: self._toggle_batch_mode())
        status_layout.addWidget(self._btn_batch)

        # 全选按钮
        self._btn_select_all = QPushButton("全选")
        self._btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_select_all.setObjectName("StatusSelectAllBtn")
        self._btn_select_all.clicked.connect(self._on_select_all_clicked)
        self._btn_select_all.setVisible(False)
        status_layout.addWidget(self._btn_select_all)

        # 删除所选按钮
        self._btn_delete_selected = QPushButton("删除所选")
        self._btn_delete_selected.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_delete_selected.setObjectName("StatusDeleteSelectedBtn")
        self._btn_delete_selected.clicked.connect(self._on_delete_selected_clicked)
        self._btn_delete_selected.setVisible(False)
        status_layout.addWidget(self._btn_delete_selected)

        status_layout.addStretch(1)

        self._info_label = QLabel()
        self._info_label.setStyleSheet(
            "color: #64748b; font-size: 11px; background: transparent; border: none; font-weight: bold;"
        )
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        status_layout.addWidget(self._info_label)

        # ---- 主体: 左右分栏 ----
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(0)

        # ==== 左侧面板 ====
        left_panel = QWidget()
        left_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        left_panel.setObjectName("HistoryLeftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 4, 2, 4)
        left_layout.setSpacing(6)

        # 搜索框
        self._search_input = QLineEdit()
        self._search_input.setObjectName("HistorySearchInput")
        self._search_input.setPlaceholderText("搜索历史...")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_search_changed)
        # 搜索图标放在搜索框内右侧
        icon_pixmap = QPixmap(16, 16)
        icon_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(icon_pixmap)
        painter.setFont(QFont("Microsoft YaHei UI", 10))
        painter.setPen(QColor("#94a3b8"))
        painter.drawText(icon_pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "🔍")
        painter.end()
        self._search_input.addAction(QIcon(icon_pixmap), QLineEdit.ActionPosition.TrailingPosition)
        left_layout.addWidget(self._search_input)

        # 筛选按钮组
        filter_row = QHBoxLayout()
        filter_row.setSpacing(2)
        filter_row.setContentsMargins(0, 0, 0, 0)
        self._filter_buttons: dict[str, QPushButton] = {}
        for key, label in [
            ("all", "全部"),
            ("translate", "翻译"),
            ("qa", "问答"),
            ("starred", "⭐"),
        ]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda checked, k=key: self._on_filter_changed(k))
            self._filter_buttons[key] = btn
            filter_row.addWidget(btn)
        self._filter_buttons["all"].setChecked(True)
        left_layout.addLayout(filter_row)

        # 列表
        self._list = QListWidget()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.currentItemChanged.connect(self._on_item_selected)
        left_layout.addWidget(self._list, 1)

        # 空状态标签
        self._empty_label = QLabel("暂无历史记录\n翻译或问答后将自动保存")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            "color: #94a3b8; font-size: 13px; background: transparent; border: none;"
        )
        self._empty_label.hide()
        left_layout.addWidget(self._empty_label)

        self._splitter.addWidget(left_panel)

        # ==== 右侧面板 ====
        right_panel = QWidget()
        right_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        right_panel.setObjectName("HistoryRightPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(2, 4, 8, 4)
        right_layout.setSpacing(6)

        # 气泡聊天视图
        from deepcat.ui.chat_bubbles import BubbleListView
        self._bubble_view = BubbleListView(right_panel)
        self._bubble_view.setObjectName("HistoryBubbleView")
        right_layout.addWidget(self._bubble_view, 1)
        self._install_text_selection_filters()


        # 右侧空状态
        self._detail_empty = QLabel("← 选择一条记录查看详情")
        self._detail_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail_empty.setStyleSheet(
            "color: #94a3b8; font-size: 13px; background: transparent; border: none;"
        )

        self._splitter.addWidget(right_panel)
        self._splitter.setSizes([220, 480])
        root.addWidget(self._splitter, 1)
        root.addWidget(self._status_bar)

        # ---- 顶栏控制按钮绑定 ----
        self._btn_fullscreen = self._title_bar.btn_fullscreen
        self._btn_fullscreen.clicked.connect(self._on_fullscreen)

        self._btn_return = self._title_bar.btn_return
        self._btn_return.clicked.connect(self._on_close)

        self._btn_close_window = self._title_bar.btn_close
        self._btn_close_window.clicked.connect(self.window().close)

        self._btn_minimize = self._title_bar.btn_minimize
        self._btn_minimize.clicked.connect(self._on_min_clicked)

        # 快捷键
        esc_shortcut = QShortcut(QKeySequence("Esc"), self)
        esc_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc_shortcut.activated.connect(self._on_close)

        # 绑定滚动条监听事件实现触底懒加载
        self._list.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)

    def _install_text_selection_filters(self) -> None:
        sources: list[object] = [self._search_input, self._bubble_view]
        try:
            sources.append(self._bubble_view.viewport())
        except Exception:
            pass
        self._text_selection_event_sources = sources
        for obj in sources:
            try:
                obj.installEventFilter(self)
            except Exception:
                pass


    def eventFilter(self, obj, event) -> bool:
        if obj in getattr(self, "_text_selection_event_sources", []):
            t = event.type()
            if t in {QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride}:
                try:
                    if int(event.key()) == int(Qt.Key.Key_A) and bool(
                        event.modifiers() & Qt.KeyboardModifier.ControlModifier
                    ):
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
                        self._mark_text_selection_interaction(
                            duration=2.0 if self._any_text_widget_has_selection() else 0.5
                        )
                except Exception:
                    pass
        return super().eventFilter(obj, event)

    def _mark_text_selection_interaction(self, duration: float = 1.0) -> None:
        until = time.monotonic() + max(0.1, float(duration))
        self._text_selection_update_block_until = max(
            float(getattr(self, "_text_selection_update_block_until", 0.0) or 0.0),
            float(until),
        )
        try:
            from deepcat.ui.selection_translate import suppress_selection_reuse_for_text_input

            suppress_selection_reuse_for_text_input(float(duration))
        except Exception:
            pass

    def _text_widget_has_selection(self, widget: object) -> bool:
        try:
            if isinstance(widget, QLineEdit):
                return bool(widget.hasSelectedText())
        except Exception:
            pass
        try:
            cursor = widget.textCursor()
            return bool(cursor and cursor.hasSelection())
        except Exception:
            return False

    def _any_text_widget_has_selection(self) -> bool:
        return self._text_widget_has_selection(self._search_input)


    def _text_selection_update_blocked(self) -> bool:
        try:
            if self._any_text_widget_has_selection():
                return True
        except Exception:
            pass
        try:
            return time.monotonic() < float(getattr(self, "_text_selection_update_block_until", 0.0) or 0.0)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 样式
    # ------------------------------------------------------------------

    def _apply_styles(self) -> None:
        from pathlib import Path
        checkbox_check_icon_url = str((Path(__file__).resolve().parent / "assets" / "icon_checkbox_check.svg").as_posix())
        self.setStyleSheet("""
            QWidget#TranslationHistoryPanel {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
            QWidget#HoverBtnContainer {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
            }
            QPushButton#HoverIconBtn {
                background: transparent;
                border: none;
                border-radius: 4px;
                padding: 0px;
            }
            QPushButton#HoverIconBtn:hover {
                background: #f1f5f9;
            }
            QWidget#HistoryTitleBar {
                background: #ffffff;
                border-bottom: 1px solid #e2e8f0;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
            QWidget#HistoryStatusBar {
                background: #f8fafc;
                border-top: 1px solid #e2e8f0;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }
            QWidget#HistoryLeftPanel {
                background: #f8fafc;
                border: none;
            }
            QWidget#HistoryRightPanel {
                background: #ffffff;
                border: none;
            }
            QLineEdit {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 6px 8px;
                font-size: 13px;
                color: #111827;
            }
            QLineEdit:hover {
                border-color: #cbd5e1;
            }
            QLineEdit:focus {
                border-color: #cbd5e1;
                border-width: 1px;
            }
            QListWidget {
                background: transparent;
                border: none;
                outline: none;
                font-size: 12px;
            }
            QListWidget::item {
                border-radius: 6px;
                padding: 6px 8px;
                margin: 1px 2px;
                color: #334155;
            }
            QListWidget::item:selected {
                background: #eaf5ff;
                color: #1e293b;
                font-weight: bold;
            }
            QListWidget::item:hover:!selected {
                background: #f1f5f9;
            }
            QScrollArea#HistoryBubbleView {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }

            QPushButton {
                background: #f1f5f9;
                color: #334155;
                border: none;
                border-radius: 6px;
                padding: 4px 10px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background: #e2e8f0;
                color: #0f172a;
            }
            QPushButton:pressed {
                background: #cbd5e1;
            }
            QPushButton:checked {
                background: #1e293b;
                color: #ffffff;
            }
            QPushButton#TitleBarBtn {
                background: transparent;
                border: none;
                border-radius: 0px;
                color: #64748b;
                font-size: 13px;
                padding: 0px;
                min-width: 46px;
                max-width: 46px;
                height: 30px;
            }
            QPushButton#TitleBarBtn:hover {
                background: #f1f2f5;
                color: #2563eb;
            }
            QPushButton#TitleBarBtnBack {
                background: transparent;
                border: none;
                border-radius: 6px;
                color: #475569;
                font-size: 12px;
                font-weight: bold;
                padding: 2px 8px 2px 6px;
            }
            QPushButton#TitleBarBtnBack:hover {
                background: #f1f5f9;
                color: #0f172a;
            }
            QPushButton#TitleBarBtnBack:pressed {
                background: #e2e8f0;
            }
            QPushButton#TitleBarBtnClose {
                background: transparent;
                border: none;
                border-radius: 0px;
                color: #64748b;
                font-size: 12px;
                padding: 0px;
                min-width: 46px;
                max-width: 46px;
                height: 30px;
            }
            QPushButton#TitleBarBtnClose:hover {
                background: #c42b1c;
                color: #ffffff;
                border-top-right-radius: 7px;
            }
            QPushButton#HoverIconBtn {
                background: transparent;
                border: none;
                border-radius: 4px;
                color: #64748b;
                font-size: 12px;
                padding: 0px;
            }
            QPushButton#HoverIconBtn:hover {
                background: #e2e8f0;
                color: #0f172a;
            }
            QCheckBox {
                spacing: 4px;
                color: #111827;
                background: transparent;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border-radius: 4px;
                border: 1px solid #cbd5e1;
                background: white;
            }
            QCheckBox::indicator:hover {
                border-color: #94a3b8;
            }
            QCheckBox::indicator:checked {
                border-color: #1e293b;
                background: #1e293b;
                image: url('__CHECKBOX_ICON__');
            }
            QPushButton#StatusBatchBtn, QPushButton#StatusSelectAllBtn {
                background: #f1f5f9;
                color: #334155;
                font-weight: bold;
                font-size: 11px;
                border-radius: 4px;
                padding: 2px 8px;
            }
            QPushButton#StatusBatchBtn:hover, QPushButton#StatusSelectAllBtn:hover {
                background: #e2e8f0;
                color: #0f172a;
            }
            QPushButton#StatusDeleteSelectedBtn {
                background: #1e293b;
                color: #ffffff;
                font-weight: bold;
                font-size: 11px;
                border-radius: 4px;
                padding: 2px 8px;
            }
            QPushButton#StatusDeleteSelectedBtn:hover {
                background: #334155;
            }
            QPushButton#StatusDeleteSelectedBtn:pressed {
                background: #0f172a;
            }
            QSplitter::handle {
                background: transparent;
                width: 0px;
            }
            QScrollBar:vertical {
                background: transparent; width: 6px; margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #cbd5e1; min-height: 20px; border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #94a3b8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px; width: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QScrollBar:horizontal { height: 0px; }
        """.replace("__CHECKBOX_ICON__", checkbox_check_icon_url))

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------

    def _update_count_label_text(self) -> None:
        db_size = "0 B"
        if self._store:
            try:
                db_size = self._store.get_db_file_size_str()
            except Exception:
                pass
        self._count_label.setText(f"共 {self._total_count} 条记录 (文件大小: {db_size})")

    def refresh(self) -> None:
        """刷新列表数据。"""
        self._load_records()


    def _on_scroll_value_changed(self, value: int) -> None:
        """监听滚动条，当接近触底时自动增量加载下一页。"""
        scrollbar = self._list.verticalScrollBar()
        max_val = scrollbar.maximum()
        # 还有不到 20px 的余量触底且未加载完全部数据时，懒加载下一页
        if max_val > 0 and value >= max_val - 20:
            if self._loaded_count < self._total_count:
                self._load_next_page()

    def _load_records(self) -> None:
        """重置条件并触发首屏加载。"""
        self._loaded_count = 0
        self._all_records = []
        self._loading_more = False
        self._load_next_page()

    def _load_next_page(self) -> None:
        """分页增量加载核心逻辑。"""
        if self._loading_more:
            return
        self._loading_more = True

        try:
            task_filter = None
            if self._current_filter and self._current_filter not in ("all", "starred"):
                task_filter = self._current_filter
            starred = self._starred_only
            search = self._search_text.strip() or None

            new_records = self._store.get_records(
                limit=self._page_limit,
                offset=self._loaded_count,
                task_type_filter=task_filter,
                search_query=search,
                starred_only=starred,
            )
            count = self._store.get_count(
                task_type_filter=task_filter,
                starred_only=starred,
            )
            self._total_count = count
        except Exception:
            new_records = []
            self._total_count = len(self._all_records)

        if not new_records and self._loaded_count == 0:
            self._list.blockSignals(True)
            self._list.clear()
            self._empty_label.show()
            self._list.hide()
            self._update_count_label_text()
            self._clear_detail()
            self._list.blockSignals(False)
            self._loading_more = False
            return


        self._empty_label.hide()
        self._list.show()
        self._update_count_label_text()


        self._list.blockSignals(True)
        if self._loaded_count == 0:
            self._list.clear()

        # 分页时保持按时间分组的连续性
        last_group = ""
        if self._all_records:
            last_rec = self._all_records[-1]
            last_group = self._format_time_group(last_rec.get("created_at", ""))

        for rec in new_records:
            self._all_records.append(rec)
            group = self._format_time_group(rec.get("created_at", ""))
            if group != last_group:
                last_group = group
                sep_item = QListWidgetItem(f"  {group}")
                sep_item.setFlags(Qt.ItemFlag.NoItemFlags)
                sep_item.setData(Qt.ItemDataRole.UserRole, None)
                font = QFont("Microsoft YaHei UI", 10)
                font.setBold(True)
                sep_item.setFont(font)
                sep_item.setForeground(QColor(140, 140, 140))
                sep_item.setSizeHint(QSize(0, 28))
                self._list.addItem(sep_item)

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, rec.get("id"))
            item.setSizeHint(QSize(0, 42))
            self._list.addItem(item)

            widget = HistoryListItemWidget(rec, self)
            self._list.setItemWidget(item, widget)

        self._loaded_count = len(self._all_records)
        self._list.blockSignals(False)

        # 仅在首屏加载完后，且有有效项时自动选中第一条
        if self._loaded_count == len(new_records):
            for i in range(self._list.count()):
                item = self._list.item(i)
                if item and item.data(Qt.ItemDataRole.UserRole) is not None:
                    self._list.setCurrentItem(item)
                    break

        self._loading_more = False
        # 刷新悬停状态
        QTimer.singleShot(50, self._refresh_hover_state)

    def _refresh_hover_state(self) -> None:
        """检查鼠标是否仍在某个列表项上方，手动触发悬停按钮显示。"""
        viewport = self._list.viewport()
        mouse_pos = viewport.mapFromGlobal(QCursor.pos())
        for i in range(self._list.count()):
            item = self._list.item(i)
            widget = self._list.itemWidget(item)
            if widget is not None and isinstance(widget, HistoryListItemWidget):
                item_rect = self._list.visualItemRect(item)
                if item_rect.contains(mouse_pos):
                    widget._time_label.hide()
                    widget._btn_container.show()
                    for btn in widget.hover_buttons:
                        btn.show()
                    widget._update_elided_text()
                    break

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------

    def _on_item_selected(self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem] = None) -> None:
        """列表项选中时加载右侧详情。"""
        if current is None:
            self._clear_detail()
            return
        record_id = current.data(Qt.ItemDataRole.UserRole)
        if record_id is None:
            return
        try:
            record = self._store.get_record(int(record_id))
        except Exception:
            record = None
        if record is None:
            self._clear_detail()
            return
        self._current_record = record
        self._show_detail(record)

    def _on_search_changed(self, text: str) -> None:
        """搜索框文字变化，300ms防抖。"""
        self._search_text = str(text or "")
        self._search_timer.start()

    def _do_search(self) -> None:
        """实际执行搜索。"""
        self._load_records()

    def _on_filter_changed(self, filter_key: str) -> None:
        """筛选标签切换。"""
        # 更新按钮状态
        for k, btn in self._filter_buttons.items():
            btn.setChecked(k == filter_key)

        if filter_key == "starred":
            self._current_filter = None
            self._starred_only = True
        elif filter_key == "all":
            self._current_filter = None
            self._starred_only = False
        else:
            self._current_filter = filter_key
            self._starred_only = False

        self._load_records()

    def _on_fullscreen(self) -> None:
        """切换全屏。"""
        self._is_fullscreen = not self._is_fullscreen
        icon_size = self._title_bar._icon_size
        if self._is_fullscreen:
            self._btn_fullscreen.setIcon(HistoryTitleBar._make_restore_icon(icon_size))
            self._btn_fullscreen.setToolTip("退出全屏")
        else:
            self._btn_fullscreen.setIcon(HistoryTitleBar._make_maximize_icon(icon_size))
            self._btn_fullscreen.setToolTip("全屏查看")
        self.fullscreen_requested.emit(self._is_fullscreen)
        # 全屏切换后刷新列表项悬停状态
        single_shot_scoped(200, self, self._refresh_hover_state)

    def _on_close(self) -> None:
        """关闭历史面板。"""
        if self._is_fullscreen:
            self._on_fullscreen()
        self.close_requested.emit()

    def _on_min_clicked(self) -> None:
        """点击最小化按钮，触发胶囊折叠。"""
        win = self.window()
        if hasattr(win, "_toggle_collapse"):
            win._toggle_collapse(not getattr(win, "_is_collapsed", False))
        else:
            win.showMinimized()

    # ------------------------------------------------------------------
    # 详情显示
    # ------------------------------------------------------------------

    def _show_detail(self, record: dict) -> None:
        """显示记录详情（改成同划词弹窗对话一致的气泡样式）。"""
        prompt_text = str(record.get("prompt_text", "")).strip()
        chat_history = []
        is_chat = False

        # 尝试从 prompt_text 反序列化多轮对话
        if prompt_text.startswith("[") and prompt_text.endswith("]"):
            try:
                import json
                chat_history = json.loads(prompt_text)
                if isinstance(chat_history, list):
                    is_chat = True
            except Exception:
                pass

        if is_chat and chat_history:
            for msg in reversed(chat_history):
                if msg.get("role") == "assistant":
                    if "model_name" not in msg or not msg["model_name"]:
                        msg["model_name"] = record.get("model_name", "")
                    if "elapsed" not in msg or not msg["elapsed"]:
                        msg["elapsed"] = record.get("elapsed_secs", 0)
                    if "created_at" not in msg or not msg["created_at"]:
                        msg["created_at"] = record.get("created_at", "")
                    break

        if not is_chat:
            # 否则为单次翻译/问答记录，构造一个临时双轮对话气泡
            source_text = str(record.get("source_text", ""))
            result_text = str(record.get("result_text", ""))
            chat_history = [
                {"role": "user", "content": source_text},
                {
                    "role": "assistant",
                    "content": result_text,
                    "model_name": record.get("model_name", ""),
                    "elapsed": record.get("elapsed_secs", 0),
                    "created_at": record.get("created_at", ""),
                }
            ]

        # 多轮对话使用尾部优先渐进渲染以避免白屏卡顿；短对话直接全量渲染
        if len(chat_history) > 16 and hasattr(self._bubble_view, "render_messages_tail_first"):
            self._bubble_view.render_messages_tail_first(chat_history)
        else:
            self._bubble_view.render_messages(chat_history)

        model = str(record.get("model_name", ""))
        if " | " in model:
            model = model.split(" | ")[0]
        elapsed = float(record.get("elapsed_secs", 0))
        self._info_label.setText(f"{model} | {elapsed:.2f}秒")

    def _clear_detail(self) -> None:
        """清空右侧详情。"""
        self._bubble_view.clear()
        self._info_label.setText("")
        self._current_record = None


    def _export_record(self, record: dict) -> None:
        """导出指定记录。"""
        try:
            text = self._store.export_records([record["id"]])
            if not text:
                return
            path, _ = QFileDialog.getSaveFileName(
                self, "导出历史记录", "translation_history.txt", "文本文件 (*.txt)"
            )
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
        except Exception:
            pass

    def _delete_record(self, record: dict) -> None:
        """删除指定记录。"""
        try:
            self._store.delete_record(record["id"])
        except Exception:
            pass
        if self._current_record and self._current_record.get("id") == record.get("id"):
            self._current_record = None
        self._load_records()

    def _copy_record(self, record: dict) -> None:
        """复制指定记录的结果到剪贴板。"""
        text = str(record.get("result_text", "")).strip()
        if text:
            try:
                QApplication.clipboard().setText(text)
            except Exception:
                pass

    def _toggle_star_record(self, record: dict) -> None:
        """切换指定记录的收藏状态。"""
        try:
            new_state = self._store.toggle_starred(record["id"])
        except Exception:
            return
        if self._current_record and self._current_record.get("id") == record.get("id"):
            self._current_record["is_starred"] = int(new_state)
        self._load_records()

    def _toggle_batch_mode(self, force_state: Optional[bool] = None) -> None:
        """一键切换或指定批量操作模式。"""
        if force_state is not None:
            self._batch_mode = force_state
        else:
            self._batch_mode = not self._batch_mode

        # 更新状态栏按钮展示
        if self._batch_mode:
            self._btn_batch.setText("退出批量")
            self._btn_select_all.setVisible(True)
            self._btn_delete_selected.setVisible(True)
        else:
            self._btn_batch.setText("批量操作")
            self._btn_select_all.setVisible(False)
            self._btn_delete_selected.setVisible(False)

        # 遍历列表项同步多选框可见性，并在退出模式时重置勾选
        for i in range(self._list.count()):
            item = self._list.item(i)
            widget = self._list.itemWidget(item)
            if isinstance(widget, HistoryListItemWidget):
                widget.checkbox.setVisible(self._batch_mode)
                if not self._batch_mode:
                    widget.checkbox.setChecked(False)
                # 重新裁减文字
                widget._update_elided_text()

    def _on_select_all_clicked(self) -> None:
        """智能双向全选/取消全选切换。"""
        widgets: list[HistoryListItemWidget] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            widget = self._list.itemWidget(item)
            if isinstance(widget, HistoryListItemWidget):
                widgets.append(widget)

        if not widgets:
            return

        all_checked = all(w.checkbox.isChecked() for w in widgets)
        target_state = not all_checked
        for w in widgets:
            w.checkbox.setChecked(target_state)

    def _on_delete_selected_clicked(self) -> None:
        """批量物理删除勾选的历史记录。"""
        selected_records: list[dict] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            widget = self._list.itemWidget(item)
            if isinstance(widget, HistoryListItemWidget) and widget.checkbox.isChecked():
                selected_records.append(widget._record)

        if not selected_records:
            return

        count = len(selected_records)

        # 弹出二次 Styled QMessageBox 确认弹窗
        from deepcat.ui.main_window.compact import StyledMessageBox
        from PyQt6.QtWidgets import QMessageBox
        msg_box = StyledMessageBox(self)
        msg_box.setWindowTitle("确认删除")
        msg_box.setText(f"确定要删除选中的 {count} 条历史记录吗？")
        msg_box.setInformativeText("删除后将无法恢复，请谨慎操作。")

        yes_btn = msg_box.addButton("确认删除", QMessageBox.ButtonRole.YesRole)
        cancel_btn = msg_box.addButton("取消", QMessageBox.ButtonRole.NoRole)
        msg_box.setDefaultButton(cancel_btn)

        msg_box.setStyleSheet("""
            QMessageBox {
                background: #f4faff;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
            }
            QMessageBox QLabel {
                color: #1e293b;
                background: transparent;
                font-family: "Microsoft YaHei UI";
                font-size: 13px;
            }
            QMessageBox QLabel#qt_msgbox_label {
                font-weight: bold;
                font-size: 14px;
                color: #0f172a;
            }
            QPushButton {
                background: #f1f5f9;
                color: #334155;
                border: none;
                border-radius: 6px;
                padding: 5px 12px;
                font-weight: bold;
                font-size: 12px;
                min-width: 64px;
            }
            QPushButton:hover {
                background: #e2e8f0;
                color: #0f172a;
            }
            QPushButton[text="确认删除"] {
                background: #1e293b;
                color: white;
            }
            QPushButton[text="确认删除"]:hover {
                background: #334155;
            }
            QPushButton[text="确认删除"]:pressed {
                background: #0f172a;
            }
        """)

        msg_box.exec()
        if msg_box.clickedButton() == yes_btn:
            for rec in selected_records:
                try:
                    self._store.delete_record(rec["id"])
                except Exception:
                    pass
                if self._current_record and self._current_record.get("id") == rec.get("id"):
                    self._current_record = None

            # 强制清退批量状态并重载
            self._toggle_batch_mode(force_state=False)
            self._load_records()

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _format_time_group(created_at: str) -> str:
        """将 created_at 字符串归类为 '今天'/'昨天'/'更早'。"""
        try:
            dt = datetime.strptime(str(created_at)[:10], "%Y-%m-%d")
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            if dt >= today:
                return "今天"
            if dt >= today - timedelta(days=1):
                return "昨天"
        except Exception:
            pass
        return "更早"

    @staticmethod
    def _task_type_label(task_type: str) -> str:
        return _TASK_TYPE_LABELS.get(task_type, "翻译")

    @staticmethod
    def _task_type_tag(task_type: str) -> str:
        return _TASK_TYPE_TAGS.get(task_type, "翻")

    @staticmethod
    def _result_label(task_type: str) -> str:
        return _RESULT_LABELS.get(task_type, "结果")


from PyQt6.QtWidgets import QDialog
class TranslationHistoryDialog(QDialog):
    """独立的、无边框、支持拖拽的翻译历史记录对话框。"""

    def __init__(self, history_store: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(736, 536)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self.panel = TranslationHistoryPanel(history_store, self)
        layout.addWidget(self.panel)

        # 加装高雅的 QGraphicsDropShadowEffect 悬浮立体阴影效果
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 48))
        self.panel.setGraphicsEffect(shadow)

        self.panel.close_requested.connect(self.close)
        self._center_on_screen()

        # 折叠胶囊态相关初始化
        self._is_collapsed = False
        self._drag_start_pos = QPoint()
        self._dragging = False
        self._drag_offset = QPoint()
        self._last_expanded_pos = None

    def _toggle_collapse(self, collapse: bool) -> None:
        self._is_collapsed = collapse
        if collapse:
            self._last_expanded_pos = self.pos()
            # 1. 隐藏 Panel 的底部内容
            self.panel._splitter.hide()
            self.panel._status_bar.hide()

            # 2. 隐藏标题栏的部分按钮与标签，避免挤压右侧最小化/关闭按钮的空间
            self.panel._title_bar.btn_return.hide()
            self.panel._title_bar.btn_fullscreen.hide()
            if hasattr(self.panel._title_bar, "title_label"):
                self.panel._title_bar.title_label.show()
                self.panel._title_bar.title_label.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Medium))
                self.panel._title_bar.title_label.setStyleSheet("color: #475569; background: transparent; border: none; padding-left: 8px;")

            # 3. 改变标题栏的按钮状态：最小化改为横竖等长英文字符加号，关闭改为纯字符叉号，以防手绘图标悬停白底时变白隐形
            self.panel._title_bar.btn_minimize.setIcon(QIcon())
            self.panel._title_bar.btn_minimize.setText("+")
            self.panel._title_bar.btn_minimize.setToolTip("还原")
            self.panel._title_bar.btn_minimize.setFixedSize(28, 28)

            self.panel._title_bar.btn_close.setIcon(QIcon())
            self.panel._title_bar.btn_close.setText("✕")
            self.panel._title_bar.btn_close.setToolTip("关闭窗口")
            self.panel._title_bar.btn_close.setFixedSize(28, 28)

            # 4. 调整样式，使折叠状态成为完美的、四角对称的 12px 圆角胶囊，清空内外边距并指定最小宽度，彻底杜绝文字剪裁/遮挡
            self.panel.setStyleSheet("""
                QWidget#TranslationHistoryPanel {
                    background: #ffffff;
                    border: 1px solid #cbd5e1;
                    border-radius: 12px;
                }
                QWidget#HistoryLeftPanel, QWidget#HistoryRightPanel {
                    border: none;
                }
                QWidget#HistoryTitleBar {
                    background: #ffffff;
                    border: none;
                    border-radius: 12px;
                }
                QPushButton#TitleBarBtn, QPushButton#TitleBarBtnClose {
                    background: transparent !important;
                    border: none !important;
                    color: #64748b !important;
                    font-weight: bold !important;
                    font-size: 13px !important;
                    padding: 0px !important;
                    margin: 0px !important;
                    min-width: 28px !important;
                    max-width: 28px !important;
                    min-height: 28px !important;
                    max-height: 28px !important;
                }
                QPushButton#TitleBarBtn:hover {
                    color: #2563eb !important;
                    background: transparent !important;
                }
                QPushButton#TitleBarBtnClose:hover {
                    color: #ef4444 !important;
                    background: transparent !important;
                }
            """)

            # 5. 动态测算胶囊宽度，同步调整标题栏布局 margins，达到与划词胶囊 100% 对齐
            try:
                title_w = int(self.panel._title_bar.title_label.fontMetrics().horizontalAdvance("历史记录"))
            except Exception:
                title_w = 56
            width = max(168, int(title_w + 20 + 12 + 16 + 28 + 28 + 12))
            self.panel._title_bar.layout().setContentsMargins(20, 0, 12, 0)
            self.panel.setFixedSize(width, 44)
            self.setFixedSize(width + 16, 60)
        else:
            # 1. 恢复 Panel 的底部内容
            self.panel._splitter.show()
            self.panel._status_bar.show()

            # 2. 恢复标题栏的按钮与标签
            self.panel._title_bar.btn_return.show()
            self.panel._title_bar.btn_fullscreen.show()
            if hasattr(self.panel._title_bar, "title_label"):
                self.panel._title_bar.title_label.show()
                self.panel._title_bar.title_label.setFont(QFont("Microsoft YaHei UI", 11, QFont.Weight.Bold))
                self.panel._title_bar.title_label.setStyleSheet("color: #1e293b; background: transparent; border: none; padding-right: 4px;")

            # 3. 恢复控制按钮的原有手绘图标和文本并重置尺寸为 46x30，恢复标题栏边距
            self.panel._title_bar.layout().setContentsMargins(12, 0, 0, 0)
            self.panel._title_bar.btn_minimize.setFixedSize(46, 30)
            self.panel._title_bar.btn_minimize.setText("")
            self.panel._title_bar.btn_minimize.setIcon(HistoryTitleBar._make_minimize_icon(self.panel._title_bar._icon_size))
            self.panel._title_bar.btn_minimize.setToolTip("最小化")

            self.panel._title_bar.btn_fullscreen.setFixedSize(46, 30)

            self.panel._title_bar.btn_close.setFixedSize(46, 30)
            self.panel._title_bar.btn_close.setText("")
            self.panel._title_bar.btn_close.setIcon(HistoryTitleBar._make_close_icon(self.panel._title_bar._icon_size))
            self.panel._title_bar.btn_close.setToolTip("关闭窗口")

            # 4. 恢复原有样式
            self.panel._apply_styles()

            # 5. 解除大小限制并调整尺寸
            self.panel.setMinimumSize(0, 0)
            self.panel.setMaximumSize(16777215, 16777215)
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)

            # 还原到最后一次停留的位置
            last_expanded_pos = getattr(self, "_last_expanded_pos", None)
            if last_expanded_pos is not None:
                self.move(last_expanded_pos)
            self.resize(736, 536)
            QTimer.singleShot(100, self.panel._refresh_hover_state)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            try:
                g_pos = event.globalPosition().toPoint()
            except Exception:
                g_pos = event.globalPos()
            self._drag_offset = g_pos - self.frameGeometry().topLeft()
            self._drag_start_pos = g_pos
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if getattr(self, "_dragging", False) and (event.buttons() & Qt.MouseButton.LeftButton):
            try:
                g_pos = event.globalPosition().toPoint()
            except Exception:
                g_pos = event.globalPos()
            self.move(g_pos - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            if hasattr(self, "_is_collapsed") and self._is_collapsed:
                try:
                    g_pos = event.globalPosition().toPoint()
                except Exception:
                    g_pos = event.globalPos()
                start_pos = getattr(self, "_drag_start_pos", g_pos)
                dist = (g_pos - start_pos).manhattanLength()

                try:
                    p = event.position().toPoint()
                except AttributeError:
                    p = event.pos()

                # 排除点击在关闭按钮或最小化按钮上
                local_close = self.panel._title_bar.btn_close.mapFrom(self, p)
                local_min = self.panel._title_bar.btn_minimize.mapFrom(self, p)
                is_click_close = self.panel._title_bar.btn_close.rect().contains(local_close)
                is_click_min = self.panel._title_bar.btn_minimize.rect().contains(local_min)

                if dist < 5 and not is_click_close and not is_click_min:
                    self._toggle_collapse(False)
                    event.accept()
                    return
                elif dist >= 5:
                    self._snap_collapsed_to_edge()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _snap_collapsed_to_edge(self) -> None:
        if not getattr(self, "_is_collapsed", False):
            return
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtCore import QRect
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)
        x = int(self.x())
        y = int(self.y())
        w = int(self.width())
        h = int(self.height())
        threshold = 28
        visible = 18
        distances = {
            "left": abs(x - int(geo.left())),
            "right": abs(int(geo.right() + 1) - int(x + w)),
            "top": abs(y - int(geo.top())),
            "bottom": abs(int(geo.bottom() + 1) - int(y + h)),
        }
        edge, distance = min(distances.items(), key=lambda item: item[1])
        if int(distance) > threshold:
            return
        if edge == "left":
            target = QPoint(int(geo.left() - w + visible), max(int(geo.top()), min(int(y), int(geo.bottom() - h + 1))))
        elif edge == "right":
            target = QPoint(int(geo.right() + 1 - visible), max(int(geo.top()), min(int(y), int(geo.bottom() - h + 1))))
        elif edge == "top":
            target = QPoint(max(int(geo.left()), min(int(x), int(geo.right() - w + 1))), int(geo.top() - h + visible))
        else:
            target = QPoint(max(int(geo.left()), min(int(x), int(geo.right() - w + 1))), int(geo.bottom() + 1 - visible))
        self.move(target)

    def contextMenuEvent(self, event) -> None:
        if hasattr(self, "_is_collapsed") and self._is_collapsed:
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu {
                    background-color: #ffffff;
                    border: 1px solid #e2e8f0;
                    border-radius: 8px;
                    padding: 4px;
                }
                QMenu::item {
                    padding: 6px 20px;
                    border-radius: 4px;
                    color: #334155;
                    font-size: 12px;
                }
                QMenu::item:selected {
                    background-color: rgba(37, 99, 235, 0.08);
                    color: #2563eb;
                }
            """)

            restore_action = QAction("还原", self)
            restore_action.triggered.connect(lambda: self._toggle_collapse(False))

            close_action = QAction("关闭", self)
            close_action.triggered.connect(self.close)

            menu.addAction(restore_action)
            menu.addAction(close_action)

            try:
                g_pos = event.globalPosition().toPoint()
            except Exception:
                g_pos = event.globalPos()
            menu.exec(g_pos)
            event.accept()
        else:
            super().contextMenuEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        single_shot_scoped(50, self, self.panel.refresh)
        # 强制重绘，消除无边框透明阴影窗口刚打开时的按钮绘制延迟/隐藏 Bug
        single_shot_scoped(100, self, lambda: self.panel._title_bar.update())
        single_shot_scoped(150, self, lambda: self.update())

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)
