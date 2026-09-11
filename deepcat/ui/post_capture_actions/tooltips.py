from __future__ import annotations

from typing import Optional
from PyQt6.QtCore import QObject, QEvent, QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont, QFontMetrics, QGuiApplication, QPainter, QPen, QCursor
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QToolTip
from deepcat.ui.popup_behavior import is_global_tooltip_disabled
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPen, QColor, QFont
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtWidgets import QWidget, QLabel


class SmoothToolTip(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self._text_color = QColor(30, 30, 30)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._text = ""
        self._alignment = Qt.AlignmentFlag.AlignCenter
        self._word_wrap = False
        self._font = QFont("Microsoft YaHei UI", 9)
        self.setFont(self._font)
        # 内置自动隐藏定时器，防止残留
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(3000)
        self._hide_timer.timeout.connect(self.hide)

    def show_text(self, text: str, pos: QPoint, *, direction: str = "below", alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignCenter, padding: int = 24, max_width: int = 0, max_height: int = 0, text_color: Optional[QColor] = None, auto_hide: bool = True) -> None:
        new_text = str(text or "").strip()
        self._text_color = text_color if text_color is not None else QColor(30, 30, 30)
        if not new_text:
            self.hide()
            return
        self._alignment = alignment
        metrics = QFontMetrics(self._font)

        # 使用Qt内置的boundingRect高效计算换行后的尺寸
        self._word_wrap = bool(max_width > 0)
        text_flags = int(Qt.TextFlag.TextWordWrap) | int(alignment)
        if max_width > 0:
            # 限制最大宽度，让Qt自动换行
            calc_width = max(42, max_width - padding)
            calc_rect = QRect(0, 0, calc_width, 10000)
            bounding = metrics.boundingRect(calc_rect, text_flags, new_text)
            new_w = min(max_width, bounding.width() + padding)
            new_h = bounding.height() + 10
            if max_height > 0:
                new_h = min(new_h, max_height)
        else:
            # 无宽度限制时使用原始逻辑
            lines = [line for line in new_text.split("\n") if line]
            if not lines:
                lines = [""]
            max_w = max(int(metrics.horizontalAdvance(line)) for line in lines)
            line_h = int(metrics.height())
            line_count = len(lines)
            new_w = max(42, max_w + padding + 12)
            new_h = max(26, line_count * line_h + (line_count - 1) * 4 + 10)

        # 确保最小尺寸合理
        new_w = max(42, new_w)
        new_h = max(26, new_h)

        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)
        new_x = int(pos.x() - new_w / 2)
        if direction == "above":
            new_y = int(pos.y() - new_h)
        else:
            new_y = int(pos.y())
        margin = 6
        new_x = max(int(geo.left() + margin), min(new_x, int(geo.right() - new_w - margin + 1)))
        new_y = max(int(geo.top() + margin), min(new_y, int(geo.bottom() - new_h - margin + 1)))

        already_visible = self.isVisible()
        same_text = self._text == new_text
        same_pos = self.x() == new_x and self.y() == new_y
        same_size = self.width() == new_w and self.height() == new_h
        self._text = new_text
        if already_visible and same_text and same_pos and same_size:
            return
        if already_visible and same_text:
            self.move(new_x, new_y)
            return
        if not same_size:
            self.resize(new_w, new_h)
        if not same_pos:
            self.move(new_x, new_y)
        if not already_visible:
            self.show()
            self.raise_()
        if auto_hide:
            self._hide_timer.start()
        self.update()

    def hide(self) -> None:
        """隐藏时同时停止自动隐藏定时器。"""
        self._hide_timer.stop()
        super().hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRect(1, 1, max(1, self.width() - 2), max(1, self.height() - 2))
            painter.setPen(QPen(QColor(0, 0, 0, 46), 1))
            painter.setBrush(QBrush(QColor(255, 255, 255, 246)))
            painter.drawRoundedRect(rect, 7, 7)
            painter.setFont(self._font)
            painter.setPen(self._text_color)
            text_rect = rect.adjusted(10, 5, -10, -5)
            text_flags = int(self._alignment)
            if bool(getattr(self, "_word_wrap", False)):
                text_flags |= int(Qt.TextFlag.TextWordWrap)
            painter.drawText(text_rect, text_flags, self._text)
        finally:
            if painter.isActive():
                painter.end()


class SmoothToolTipController(QObject):
    """用同一套 SmoothToolTip 接管 QWidget 原生 tooltip。"""

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._tooltip = SmoothToolTip()
        self._current_widget: QWidget | None = None

    def eventFilter(self, obj, event) -> bool:
        event_type = event.type()
        if event_type == QEvent.Type.ToolTip and isinstance(obj, QWidget):
            if is_global_tooltip_disabled(obj):
                if self._current_widget is obj:
                    self._current_widget = None
                self._tooltip.hide()
                try:
                    QToolTip.hideText()
                except Exception:
                    pass
                event.ignore()
                return True
            text = str(obj.toolTip() or "").strip()
            if not text:
                self._tooltip.hide()
                event.ignore()
                return True
            try:
                QToolTip.hideText()
            except Exception:
                pass
            pos, direction = self._tooltip_anchor(obj, event)
            self._current_widget = obj
            self._tooltip.show_text(
                text,
                pos,
                direction=direction,
                alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                padding=24,
                max_width=360,
                auto_hide=False,
            )
            event.accept()
            return True

        if event_type in {
            QEvent.Type.Leave,
            QEvent.Type.Hide,
            QEvent.Type.Close,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
            QEvent.Type.FocusOut,
        }:
            if event_type == QEvent.Type.Leave and isinstance(obj, QWidget):
                try:
                    if obj.rect().contains(obj.mapFromGlobal(QCursor.pos())):
                        return False
                except Exception:
                    pass
            if self._current_widget is obj or event_type in {QEvent.Type.MouseButtonPress, QEvent.Type.Wheel}:
                self._current_widget = None
                self._tooltip.hide()
        return False

    def _tooltip_anchor(self, widget: QWidget, event) -> tuple[QPoint, str]:
        try:
            global_pos = event.globalPos()
        except Exception:
            try:
                global_pos = event.globalPosition().toPoint()
            except Exception:
                global_pos = widget.mapToGlobal(QPoint(widget.width() // 2, widget.height()))

        try:
            screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)
            below = widget.mapToGlobal(QPoint(widget.width() // 2, widget.height() + 8))
            if below.y() + 40 <= geo.bottom():
                return below, "below"
            above_anchor = widget.mapToGlobal(QPoint(widget.width() // 2, -8))
            return above_anchor, "above"
        except Exception:
            return QPoint(global_pos.x(), global_pos.y() + 18), "below"


def install_smooth_tooltips(app: QApplication | None = None) -> SmoothToolTipController | None:
    app = app or QApplication.instance()
    if app is None:
        return None
    controller = getattr(app, "_deepcat_smooth_tooltip_controller", None)
    if isinstance(controller, SmoothToolTipController):
        return controller
    controller = SmoothToolTipController(app)
    app.installEventFilter(controller)
    setattr(app, "_deepcat_smooth_tooltip_controller", controller)
    return controller


class RedDotLabel(QLabel):
    hovered = pyqtSignal()
    unhovered = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFixedSize(18, 40) # 加宽感应区域，灵敏度 100%

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor("#ef4444")))
            painter.drawEllipse(6, 6, 8, 8) # 在绝对定位 (6, 6) 精确自绘亮红点
        finally:
            if painter.isActive():
                painter.end()

    def enterEvent(self, event: QEvent) -> None:
        self.hovered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.unhovered.emit()
        super().leaveEvent(event)
