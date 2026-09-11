from __future__ import annotations

from typing import Any, Optional
from PyQt6.QtCore import Qt, QRect, QEvent, QPoint, pyqtSignal, QObject, QMimeData
from PyQt6.QtGui import QColor, QPainter, QPen, QStandardItemModel, QStandardItem, QDrag
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QLabel,
    QPushButton,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QStyle,
    QProxyStyle,
    QStyleOption,
    QFrame,
    QCheckBox,
)
from deepcat.ui.post_capture_actions import ModernPopupComboBox


class RoundedTextEditContainer(QFrame):
    def __init__(self, editor: QTextEdit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RoundedTextEditContainer")
        self._editor = editor

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._editor)

        self._editor.setFrameStyle(QFrame.Shape.NoFrame)
        self._editor.setStyleSheet("background: transparent; border: none;")
        self._editor.installEventFilter(self)

        self._hovered = False
        self._focused = False
        self.update_style()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._editor:
            if event.type() == QEvent.Type.FocusIn:
                self._focused = True
                self.update_style()
            elif event.type() == QEvent.Type.FocusOut:
                self._focused = False
                self.update_style()
            elif event.type() == QEvent.Type.Enter:
                self._hovered = True
                self.update_style()
            elif event.type() == QEvent.Type.Leave:
                self._hovered = False
                self.update_style()
        return super().eventFilter(obj, event)

    def update_style(self) -> None:
        self.setProperty("focused", self._focused)
        self.setProperty("hovered", self._hovered)
        self.style().polish(self)
        self.update()


class _ModernComboStyle(QProxyStyle):
    def __init__(self, parent=None) -> None:
        # 代理必须独占基样式，不能接管并释放 QApplication 的共享样式。
        super().__init__()
        self.setParent(parent)

    def drawPrimitive(self, element, option: QStyleOption, painter: QPainter, widget=None) -> None:
        if element == QStyle.PrimitiveElement.PE_IndicatorArrowDown:
            r = option.rect
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(QColor(0, 0, 0, 150))
            pen.setWidthF(1.6)
            painter.setPen(pen)
            cx = float(r.center().x())
            cy = float(r.center().y())
            s = float(min(r.width(), r.height())) * 0.34
            painter.drawLine(int(cx - s), int(cy - s * 0.35), int(cx), int(cy + s * 0.55))
            painter.drawLine(int(cx), int(cy + s * 0.55), int(cx + s), int(cy - s * 0.35))
            painter.restore()
            return
        return super().drawPrimitive(element, option, painter, widget)


class _MouseClickOnlyComboBox(ModernPopupComboBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def keyPressEvent(self, event) -> None:
        event.ignore()

    def wheelEvent(self, event) -> None:
        event.ignore()


class _SwitchCheckBox(QCheckBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(42, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText("")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        checked = bool(self.isChecked())
        track = QRect(1, 2, 40, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2f3d56") if checked else QColor("#cbd5e1"))
        painter.drawRoundedRect(track, 10, 10)
        knob_x = 21 if checked else 3
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRect(knob_x, 4, 16, 16))
        painter.end()


class _MultiSelectComboBox(QComboBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.setModel(QStandardItemModel(self))
        self.view().setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view().pressed.connect(self._on_item_pressed)
        self.setStyleSheet("font-size: 14px;")
        self._placeholder = "请选择"

    def addItem(self, text: str, userData: Any = None) -> None:
        item = QStandardItem(text)
        item.setData(userData, Qt.ItemDataRole.UserRole)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        self.model().appendRow(item)

    def _on_item_pressed(self, index) -> None:
        item = self.model().itemFromIndex(index)
        if item is None:
            return
        new_state = Qt.CheckState.Checked if item.checkState() == Qt.CheckState.Unchecked else Qt.CheckState.Unchecked
        item.setCheckState(new_state)
        self._update_text()

    def _update_text(self) -> None:
        texts = []
        for i in range(self.model().rowCount()):
            item = self.model().item(i)
            if item.checkState() == Qt.CheckState.Checked:
                texts.append(item.text())
        self.lineEdit().setText(", ".join(texts) if texts else self._placeholder)

    def setCurrentIndices(self, indices: list[int]) -> None:
        for i in range(self.model().rowCount()):
            item = self.model().item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            checked = data in indices if isinstance(data, int) else i in indices
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._update_text()

    def currentIndices(self) -> list[int]:
        indices = []
        for i in range(self.model().rowCount()):
            item = self.model().item(i)
            if item.checkState() == Qt.CheckState.Checked:
                data = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(data, int):
                    indices.append(data)
        return indices


class _DraggableTile(QToolButton):
    def __init__(self, shortcut_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._shortcut_id = shortcut_id
        self._drag_start_pos: Optional[QPoint] = None
        self._countdown_text: str = ""

    def set_countdown_text(self, text: str) -> None:
        if getattr(self, "_countdown_text", "") != text:
            self._countdown_text = text
            self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        text = getattr(self, "_countdown_text", "")
        if not text:
            return

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            # Setup font
            font = painter.font()
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)

            fm = painter.fontMetrics()
            text_width = fm.horizontalAdvance(text)
            text_height = fm.height()

            # Calculate badge size
            padding_x = 5
            padding_y = 2
            badge_width = max(18, text_width + 2 * padding_x)
            badge_height = max(18, text_height + 2 * padding_y)

            # Position at top right
            rect = self.rect()
            badge_x = rect.right() - badge_width - 6
            badge_y = rect.top() + 6

            badge_rect = QRect(badge_x, badge_y, badge_width, badge_height)

            # Decide color based on remaining time
            is_warning = text == "休息中" or text.startswith("00:")
            if is_warning:
                bg_color = QColor("#ef4444")
            else:
                bg_color = QColor("#2563eb")

            painter.setBrush(bg_color)
            painter.setPen(Qt.PenStyle.NoPen)

            # Draw rounded rect
            painter.drawRoundedRect(badge_rect, 9, 9)

            # Draw text
            painter.setPen(QColor("#ffffff"))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, text)
        finally:
            if painter.isActive():
                painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._drag_start_pos is not None:
            if (event.position().toPoint() - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance():
                if self._shortcut_id:
                    self._start_drag()
                    return
        super().mouseMoveEvent(event)

    def _start_drag(self) -> None:
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-deepcat-shortcut-id", self._shortcut_id.encode("utf-8"))

        # 抓取半透明预览图
        pixmap = self.grab()
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.fillRect(pixmap.rect(), QColor(0, 0, 0, 160))
        painter.end()

        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)


class _SidebarNavButton(QPushButton):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.isChecked():
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(QColor("#1e293b"))
            pen.setWidthF(2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            h = max(1, int(self.height()))
            bar_h = 14
            y1 = int((h - bar_h) / 2)
            y2 = y1 + bar_h
            x = 6
            painter.drawLine(x, y1, x, y2)
        finally:
            if painter.isActive():
                painter.end()


class _DoubleClickLabel(QLabel):
    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class _SidebarHoverFilter(QObject):
    def __init__(self, tooltip, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._tooltip = tooltip

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Enter:
            tip = obj.property("tipText")
            if tip:
                global_pos = obj.mapToGlobal(QPoint(obj.width() // 2, -4))
                self._tooltip.show_text(str(tip), global_pos, direction="above")
        elif event.type() == QEvent.Type.Leave or event.type() == QEvent.Type.MouseButtonPress:
            self._tooltip.hide()
        return super().eventFilter(obj, event)
