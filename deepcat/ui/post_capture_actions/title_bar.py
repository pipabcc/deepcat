from __future__ import annotations

from pathlib import Path
from typing import Optional
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QPushButton, QHBoxLayout, QFrame, QWidget, QLabel
from deepcat.ui.app_icon import create_app_icon
from PyQt6.QtWidgets import QFrame, QPushButton
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame
from PyQt6.QtGui import QPainter, QPixmap, QIcon, QPen, QColor, QFont
from PyQt6.QtCore import QPointF, QRectF, QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtSvg import QSvgRenderer


def _load_title_owl_pixmap(size: int) -> QPixmap:
    """渲染标题栏图标；优先加载扁平猫头图标 deepcat_logo.svg，缺失或失败时回退。"""
    svg_path = Path(__file__).resolve().parent.parent / "assets" / "deepcat_logo.svg"
    try:
        if svg_path.exists():
            renderer = QSvgRenderer(str(svg_path))
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                renderer.render(painter)
            finally:
                painter.end()
            return pm
    except Exception:
        pass

    ico_path = Path(__file__).resolve().parent.parent / "assets" / "app.ico"
    try:
        if ico_path.exists():
            icon = QIcon(str(ico_path))
            if not icon.isNull():
                return icon.pixmap(size, size)
    except Exception:
        pass
    # 回退：使用通用应用图标
    try:
        return create_app_icon().pixmap(size, size)
    except Exception:
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        return pm


class _OcrTitleBarButton(QPushButton):
    """标题栏按钮：在半透明无边框窗口中主动刷新父区域，避免 hover 残影。"""

    def _refresh_title_bar_region(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            self.update()
            return
        rect = self.geometry().adjusted(-2, -2, 2, 2)
        parent.update(rect)
        self.update()

        def refresh_later(widget=parent, update_rect=QRect(rect)) -> None:
            try:
                widget.update(update_rect)
            except RuntimeError:
                pass

        QTimer.singleShot(0, refresh_later)

    def enterEvent(self, event) -> None:
        self._refresh_title_bar_region()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._refresh_title_bar_region()
        super().leaveEvent(event)


class OcrTitleBar(QFrame):
    """支持窗口拖拽的高保真悬浮实体标题栏。"""

    @staticmethod
    def _make_minimize_icon(size: int = 16) -> QIcon:
        def draw(p, s, pw):
            m = pw * 3
            mid_y = s / 2
            p.drawLine(QPointF(m, mid_y), QPointF(s - m, mid_y))

        pix_normal = QPixmap(size, size)
        pix_normal.fill(Qt.GlobalColor.transparent)
        painter1 = QPainter(pix_normal)
        try:
            painter1.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter1.setPen(QPen(QColor("#475569"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            draw(painter1, size, 1.2)
        finally:
            painter1.end()
            del painter1

        pix_active = QPixmap(size, size)
        pix_active.fill(Qt.GlobalColor.transparent)
        painter2 = QPainter(pix_active)
        try:
            painter2.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter2.setPen(QPen(QColor("#0f172a"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            draw(painter2, size, 1.2)
        finally:
            painter2.end()
            del painter2

        icon = QIcon()
        icon.addPixmap(pix_normal, QIcon.Mode.Normal)
        icon.addPixmap(pix_active, QIcon.Mode.Active)
        return icon

    @staticmethod
    def _make_maximize_icon(size: int = 16) -> QIcon:
        def draw(p, s, pw):
            m = pw * 2
            p.drawRoundedRect(QRectF(m, m, s - m * 2, s - m * 2), 1.5, 1.5)

        pix_normal = QPixmap(size, size)
        pix_normal.fill(Qt.GlobalColor.transparent)
        painter1 = QPainter(pix_normal)
        try:
            painter1.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter1.setPen(QPen(QColor("#475569"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            draw(painter1, size, 1.2)
        finally:
            painter1.end()
            del painter1

        pix_active = QPixmap(size, size)
        pix_active.fill(Qt.GlobalColor.transparent)
        painter2 = QPainter(pix_active)
        try:
            painter2.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter2.setPen(QPen(QColor("#0f172a"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            draw(painter2, size, 1.2)
        finally:
            painter2.end()
            del painter2

        icon = QIcon()
        icon.addPixmap(pix_normal, QIcon.Mode.Normal)
        icon.addPixmap(pix_active, QIcon.Mode.Active)
        return icon

    @staticmethod
    def _make_restore_icon(size: int = 16) -> QIcon:
        def draw_with_color(p, s, pw, color):
            m = pw * 2
            offset = pw * 2.5
            p.setOpacity(0.35)
            p.setPen(QPen(color, 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawRoundedRect(QRectF(m + offset, m, s - m * 2 - offset, s - m * 2 - offset), 1.5, 1.5)
            p.setOpacity(1.0)
            p.drawRoundedRect(QRectF(m, m + offset, s - m * 2 - offset, s - m * 2 - offset), 1.5, 1.5)

        pix_normal = QPixmap(size, size)
        pix_normal.fill(Qt.GlobalColor.transparent)
        painter1 = QPainter(pix_normal)
        try:
            painter1.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            draw_with_color(painter1, size, 1.2, QColor("#475569"))
        finally:
            painter1.end()
            del painter1

        pix_active = QPixmap(size, size)
        pix_active.fill(Qt.GlobalColor.transparent)
        painter2 = QPainter(pix_active)
        try:
            painter2.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            draw_with_color(painter2, size, 1.2, QColor("#0f172a"))
        finally:
            painter2.end()
            del painter2

        icon = QIcon()
        icon.addPixmap(pix_normal, QIcon.Mode.Normal)
        icon.addPixmap(pix_active, QIcon.Mode.Active)
        return icon

    @staticmethod
    def _make_close_icon(size: int = 16) -> QIcon:
        def draw(p, s, pw):
            m = pw * 3
            p.drawLine(QPointF(m, m), QPointF(s - m, s - m))
            p.drawLine(QPointF(s - m, m), QPointF(m, s - m))

        pix_normal = QPixmap(size, size)
        pix_normal.fill(Qt.GlobalColor.transparent)
        painter1 = QPainter(pix_normal)
        try:
            painter1.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter1.setPen(QPen(QColor("#475569"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            draw(painter1, size, 1.2)
        finally:
            painter1.end()
            del painter1

        pix_active = QPixmap(size, size)
        pix_active.fill(Qt.GlobalColor.transparent)
        painter2 = QPainter(pix_active)
        try:
            painter2.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter2.setPen(QPen(QColor("#ffffff"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            draw(painter2, size, 1.2)
        finally:
            painter2.end()
            del painter2

        icon = QIcon()
        icon.addPixmap(pix_normal, QIcon.Mode.Normal)
        icon.addPixmap(pix_active, QIcon.Mode.Active)
        return icon

    def __init__(self, panel: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._panel = panel
        self.setObjectName("OcrTitleBar")
        self.setFixedHeight(30)
        self._dragging = False
        self._drag_offset = QPoint()
        self._icon_size = 16

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)

        # 软件猫头图标（替换原 macOS 风格三色圆点）
        self.dot_owl = QLabel()
        self.dot_owl.setFixedSize(22, 22)
        self.dot_owl.setPixmap(_load_title_owl_pixmap(22))
        self.dot_owl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dot_owl.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(self.dot_owl)
        layout.addSpacing(-4)  # 拉近文字与图标的距离

        self.title_label = QLabel("AI对话")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.title_label.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.title_label.setFixedHeight(30)
        self.title_label.setStyleSheet("color: #1e293b; background: #f6f8fb; border: none; padding-right: 4px;")
        layout.addWidget(self.title_label)

        layout.addStretch(1)

        self.control_layout = QHBoxLayout()
        self.control_layout.setContentsMargins(0, 0, 0, 0)
        self.control_layout.setSpacing(0)

        self.btn_minimize = _OcrTitleBarButton()
        self.btn_minimize.setIcon(self._make_minimize_icon(self._icon_size))
        self.btn_minimize.setIconSize(QSize(self._icon_size, self._icon_size))
        self.btn_minimize.setToolTip("最小化")
        self.btn_minimize.setFixedSize(46, 30)
        self.btn_minimize.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_minimize.setObjectName("TitleBarBtn")
        self.btn_minimize.clicked.connect(self._panel._minimize_panel)
        self.control_layout.addWidget(self.btn_minimize)

        self.btn_maximize = _OcrTitleBarButton()
        self.btn_maximize.setIcon(self._make_maximize_icon(self._icon_size))
        self.btn_maximize.setIconSize(QSize(self._icon_size, self._icon_size))
        self.btn_maximize.setToolTip("最大化")
        self.btn_maximize.setFixedSize(46, 30)
        self.btn_maximize.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_maximize.setObjectName("TitleBarBtn")
        self.btn_maximize.clicked.connect(self._panel.toggle_maximize)
        self.control_layout.addWidget(self.btn_maximize)

        self.btn_close = _OcrTitleBarButton()
        self.btn_close.setIcon(self._make_close_icon(self._icon_size))
        self.btn_close.setIconSize(QSize(self._icon_size, self._icon_size))
        self.btn_close.setToolTip("关闭窗口")
        self.btn_close.setFixedSize(46, 30)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setObjectName("TitleBarBtnClose")
        self.btn_close.clicked.connect(self._panel.close)
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
            event.accept()
        else:
            super().mouseReleaseEvent(event)
