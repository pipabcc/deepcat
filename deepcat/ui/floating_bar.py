from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from deepcat.ui.style_tokens import Colors, Radius, Spacing


class FloatingBar(QWidget):
    stop_clicked = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._label = QLabel("正在截取...")
        self._label.setStyleSheet(f"color: {Colors.TEXT_ON_DARK};")

        btn = QPushButton("停止")
        btn.clicked.connect(self.stop_clicked.emit)
        btn.setFixedWidth(56)
        btn.setStyleSheet(
            f"QPushButton {{ background:{Colors.DANGER}; color:{Colors.TEXT_ON_DARK}; border:none; padding:4px 6px; border-radius:{Radius.MD}px; }}"
            f"QPushButton:hover {{ background:{Colors.DANGER_HOVER}; }}"
        )
        self._btn_stop = btn

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Spacing.FLOATING_MARGIN_X, Spacing.FLOATING_MARGIN_Y, Spacing.FLOATING_MARGIN_X, Spacing.FLOATING_MARGIN_Y)
        layout.setSpacing(Spacing.FLOATING_GAP)
        layout.addWidget(self._label)
        layout.addWidget(btn)

        self.setStyleSheet(f"background: {Colors.FLOATING_SURFACE}; border-radius:{Radius.LG}px;")
        self.setFixedWidth(300)
        self._move_to_top_right()

    def _move_to_top_right(self) -> None:
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else self.geometry()
        x = geo.x() + geo.width() - self.width() - 16
        y = geo.y() + 16
        self.move(int(x), int(y))

    def set_frame_index(self, n: int) -> None:
        self._label.setText(f"正在截取... 第 {int(n)} 帧")

    def set_metrics(self, frame_index: int, captured_height: int, elapsed_seconds: int) -> None:
        self._label.setText(
            f"第 {int(frame_index)} 帧 | 已捕获 {int(captured_height)}px | 用时 {int(elapsed_seconds)}s"
        )

    def set_status(self, text: str) -> None:
        self._label.setText(str(text))

    def set_stop_enabled(self, enabled: bool) -> None:
        self._btn_stop.setEnabled(bool(enabled))

    def set_stop_text(self, text: str) -> None:
        label = str(text or "停止")
        self._btn_stop.setText(label)
        self._btn_stop.setToolTip("停止/取消当前截图任务")
