from __future__ import annotations

from PyQt6.QtCore import QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget


class CountdownOverlay(QWidget):
    def __init__(self, seconds: int = 3) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        screen = QGuiApplication.primaryScreen()
        geo = screen.geometry() if screen else QRect(0, 0, 800, 600)
        self.setGeometry(geo)

        self._remaining = max(0, int(seconds))
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.update()
        if self._remaining <= 0:
            self.close()
            return
        self._timer.start()

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._timer.stop()
            self.close()
            return
        self.update()

    def remaining(self) -> int:
        return int(self._remaining)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        text = str(max(1, int(self._remaining)))
        font = QFont()
        font.setBold(True)
        font.setPointSize(72)
        painter.setFont(font)

        rect = self.rect()
        metrics = painter.fontMetrics()
        tw = metrics.horizontalAdvance(text)
        th = metrics.height()
        x = int(rect.center().x() - tw / 2)
        y = int(rect.center().y() + th / 4)

        path = QPainterPath()
        path.addText(x, y, font, text)

        pen = QPen(QColor(0, 0, 0, 240))
        pen.setWidth(6)
        painter.setPen(pen)
        painter.drawPath(path)

        painter.fillPath(path, QColor(255, 255, 255, 255))
