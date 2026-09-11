from __future__ import annotations

from typing import Callable, Optional
from PyQt6.QtCore import QByteArray, QEvent, QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from deepcat.ui.post_capture_actions import ModernPopupComboBox, _OcrModelMenuPopup, OcrGenericMenuPopup

from deepcat.ui.settings_dialog._shared import (
    CODEX_CURRENT_MODEL_CHECK_COLOR,
    TRANSLATOR_MODEL_CODEX_BOUND_ROLE,
    TRANSLATOR_MODEL_HIGHLIGHT_COLOR_ROLE,
    TRANSLATOR_MODEL_SEARCH_ROLE,
)


class PasteAwareLineEdit(QLineEdit):
    pasted = pyqtSignal(str)

    def paste(self) -> None:
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        super().paste()
        if text:
            self.pasted.emit(text)


class PasteAwareComboBox(ModernPopupComboBox):
    pasted = pyqtSignal(str)
    _EMPTY_MODEL_ID_COLOR = "rgba(17, 24, 39, 128)"
    _MODEL_ID_TEXT_COLOR = "#111827"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self._line_edit = PasteAwareLineEdit(self)
        self.setLineEdit(self._line_edit)
        self._line_edit.setStyleSheet(f"color: {self._MODEL_ID_TEXT_COLOR};")
        self._line_edit.pasted.connect(self.pasted.emit)

    def setEditable(self, editable: bool) -> None:
        super().setEditable(True)
        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.setReadOnly(not bool(editable))

    def text(self) -> str:
        return self.currentText()

    def _has_visible_popup_items(self) -> bool:
        try:
            for idx in range(self.count()):
                if str(self.itemText(idx) or "").strip():
                    return True
        except Exception:
            pass
        return False

    def showPopup(self) -> None:
        if bool(self.property("disablePopupWhenEmpty")) and not self._has_visible_popup_items():
            return
        super().showPopup()

    def _refresh_empty_text_state(self) -> None:
        try:
            is_empty = not bool(str(self.currentText() or "").strip())
            self.setProperty("emptyModelId", is_empty)
            if self.lineEdit() is not None:
                color = self._EMPTY_MODEL_ID_COLOR if is_empty else self._MODEL_ID_TEXT_COLOR
                self.lineEdit().setStyleSheet(f"color: {color};")
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()
        except Exception:
            pass

    def setText(self, val: str) -> None:
        text = str(val or "").strip()
        self.setCurrentText(text)
        self._refresh_empty_text_state()

    def setDisplayText(self, val: str) -> None:
        self.setText(val)

    def setCurrentText(self, text: str) -> None:
        super().setCurrentText(str(text or ""))
        self._refresh_empty_text_state()

    def setPlaceholderText(self, text: str) -> None:
        try:
            QComboBox.setPlaceholderText(self, str(text or ""))
        except Exception:
            pass
        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText(text)
            return
        try:
            super().setPlaceholderText(text)
        except Exception:
            pass

    @property
    def editingFinished(self):
        line_edit = self.lineEdit()
        return line_edit.editingFinished if line_edit is not None else self.currentIndexChanged


class TranslatorModelComboBox(QComboBox):
    deleteRequested = pyqtSignal(str)
    batchDeleteRequested = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("TranslatorModelCombo")
        self._arrow_pressed = False
        self.setStyleSheet(
            "QComboBox#TranslatorModelCombo { padding-right: 28px; }"
            "QComboBox#TranslatorModelCombo::drop-down { border: none; width: 28px; }"
            "QComboBox#TranslatorModelCombo::down-arrow { image: none; width: 0px; height: 0px; }"
        )

    def setEditable(self, editable: bool) -> None:
        super().setEditable(editable)
        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.setTextMargins(0, 0, 22, 0)

    def _arrow_rect(self) -> QRect:
        return QRect(max(0, self.width() - 28), 0, 28, max(1, self.height()))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        rect = self._arrow_rect()
        cx = int(rect.center().x())
        cy = int(rect.center().y()) + 2
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(76, 107, 136), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(cx - 6, cy - 3, cx, cy + 3)
        painter.drawLine(cx, cy + 3, cx + 6, cy - 3)
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._arrow_rect().contains(event.position().toPoint()):
            self._arrow_pressed = True
            event.accept()
            return
        self._arrow_pressed = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and bool(getattr(self, "_arrow_pressed", False)):
            self._arrow_pressed = False
            if self._arrow_rect().contains(event.position().toPoint()):
                self.showPopup()
            event.accept()
            return
        self._arrow_pressed = False
        super().mouseReleaseEvent(event)

    def showPopup(self) -> None:
        model = self.model()
        if not model:
            return

        grouped_models = []
        highlighted_models = set()
        highlighted_model_colors: dict[str, str] = {}
        current_group = None
        current_group_models = []

        for i in range(model.rowCount()):
            item = model.item(i)
            if not item:
                continue
            text = item.text()
            if not item.isEnabled():
                if current_group is not None:
                    grouped_models.append((current_group, current_group_models))
                current_group = text.strip("[]")
                current_group_models = []
            else:
                current_group_models.append(text)
                if bool(item.data(TRANSLATOR_MODEL_CODEX_BOUND_ROLE)):
                    highlighted_models.add(text)
                    color = str(item.data(TRANSLATOR_MODEL_HIGHLIGHT_COLOR_ROLE) or "").strip()
                    if color:
                        highlighted_model_colors[text] = color

        if current_group is not None:
            grouped_models.append((current_group, current_group_models))

        current_val = self.currentText()
        search_texts = {}
        for idx in range(model.rowCount()):
            item = model.item(idx)
            if item is not None and item.isEnabled():
                text = str(item.text() or "")
                search_texts[text] = str(item.data(TRANSLATOR_MODEL_SEARCH_ROLE) or text)

        def on_selected(model_name: str) -> None:
            target_idx = -1
            for idx in range(self.model().rowCount()):
                item = self.model().item(idx)
                if item and item.text() == model_name:
                    target_idx = idx
                    break
            if target_idx >= 0:
                self.setCurrentIndex(target_idx)
                self.activated.emit(target_idx)

        def on_delete(model_name: str) -> None:
            self.deleteRequested.emit(model_name)

        def on_batch_delete(model_names: list[str]) -> None:
            self.batchDeleteRequested.emit(list(model_names))

        purpose = "qa" if str(self.property("purpose") or "").strip().lower() == "qa" else "translate"

        def codex_action_label(model_name: str) -> str:
            return "还原Codex" if str(model_name or "").strip() in highlighted_models else "填入Codex"

        popup = _OcrModelMenuPopup(
            grouped_models=grouped_models,
            current_model=current_val,
            on_selected=on_selected,
            parent=self,
            on_delete=on_delete,
            on_batch_delete=on_batch_delete,
            search_texts=search_texts,
            purpose=purpose,
            match_parent_width=True,
            active_check_color=highlighted_model_colors.get(current_val, str(self.property("activeCheckColor") or "#111827")),
            highlighted_models=highlighted_models,
            highlighted_text_color=CODEX_CURRENT_MODEL_CHECK_COLOR,
            highlighted_model_colors=highlighted_model_colors,
            on_codex_action=None,
            codex_action_label_for_model=codex_action_label,
        )
        global_pos = self.mapToGlobal(QPoint(0, self.height() + 2))
        popup.show_at(self, global_pos)


class ApiKeyVisibilityButton(QToolButton):
    _SVG_EYE = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>'
        '<circle cx="12" cy="12" r="3.2"/>'
        '</svg>'
    )
    _SVG_EYE_OFF = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>'
        '<circle cx="12" cy="12" r="3.2" fill="{color}"/>'
        '</svg>'
    )

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("显示 API 密钥")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("QToolButton { background: transparent; border: none; padding: 0px; }")

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w = self.width()
            h = self.height()
            if w <= 4 or h <= 4:
                return

            icon_size = max(14.0, min(18.0, float(min(w, h)) - 6.0))
            x = (float(w) - icon_size) / 2.0
            y = (float(h) - icon_size) / 2.0
            target_rect = QRectF(x, y, icon_size, icon_size)

            if self.isDown():
                color = "#0f172a"
            elif self.underMouse():
                color = "#334155"
            else:
                color = "#64748b"

            template = self._SVG_EYE if self.isChecked() else self._SVG_EYE_OFF
            svg_data = template.format(color=color).encode("utf-8")
            renderer = QSvgRenderer(QByteArray(svg_data))
            if renderer.isValid():
                renderer.render(painter, target_rect)
        finally:
            if painter.isActive():
                painter.end()


class ProxyTestButton(QToolButton):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("测试代理连通性")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("QToolButton { background: transparent; border: none; padding: 0px; }")

        self._state = "idle"  # "idle", "testing", "success", "failure"
        self._error_msg = ""
        self._latency = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)
        self._angle = 0

    def setState(self, state: str, error_msg: str = "", latency: float = 0.0) -> None:
        self._state = state
        self._error_msg = error_msg
        self._latency = latency
        if state == "testing":
            self.setToolTip("正在测试代理连通性...")
            if not self._timer.isActive():
                self._timer.start(50)
        else:
            self._timer.stop()
            if state == "success":
                self.setToolTip(f"代理连通成功 (延迟: {latency:.1f}ms)")
            elif state == "failure":
                tip = f"代理连通失败: {error_msg}"
                if len(tip) > 200:
                    tip = tip[:200] + "..."
                self.setToolTip(tip)
            else:
                self.setToolTip("测试代理连通性")
        self.update()

    def _rotate(self) -> None:
        self._angle = (self._angle + 15) % 360
        self.update()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = self.rect().adjusted(2, 2, -2, -2)
            if r.width() <= 4 or r.height() <= 4:
                return

            c = r.center()

            if self._state == "idle":
                # 鼠标 hover 变深，提升交互动感
                if self.underMouse():
                    pen_color = QColor(71, 85, 105)  # Slate 600
                else:
                    pen_color = QColor(148, 163, 184)  # Slate 400
                pen = QPen(pen_color, 1.6)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)

                # 外圆
                painter.drawEllipse(r)

                # 水平中线
                painter.drawLine(r.left(), c.y(), r.right(), c.y())
                # 垂直中线
                painter.drawLine(c.x(), r.top(), c.x(), r.bottom())

                # 竖向弧线，模拟经线
                w = r.width()
                h = r.height()
                from PyQt6.QtCore import QRectF
                painter.drawEllipse(QRectF(c.x() - w / 4.0, r.top(), w / 2.0, h))

            elif self._state == "testing":
                pen = QPen(QColor(59, 130, 246), 2.0)  # Blue 500
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawArc(r, -self._angle * 16, 120 * 16)

            elif self._state == "success":
                pen = QPen(QColor(34, 197, 94), 2.2)  # Green 500
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)

                w = r.width()
                h = r.height()
                from PyQt6.QtCore import QPointF
                from PyQt6.QtGui import QPainterPath
                p1 = QPointF(r.left() + 0.28 * w, r.top() + 0.52 * h)
                p2 = QPointF(r.left() + 0.44 * w, r.top() + 0.68 * h)
                p3 = QPointF(r.left() + 0.72 * w, r.top() + 0.32 * h)

                path = QPainterPath()
                path.moveTo(p1)
                path.lineTo(p2)
                path.lineTo(p3)
                painter.drawPath(path)

            elif self._state == "failure":
                pen = QPen(QColor(239, 68, 68), 2.2)  # Red 500
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)

                w = r.width()
                h = r.height()
                from PyQt6.QtCore import QPointF
                p1 = QPointF(r.left() + 0.3 * w, r.top() + 0.3 * h)
                p2 = QPointF(r.right() - 0.3 * w, r.bottom() - 0.3 * h)
                painter.drawLine(p1, p2)

                p3 = QPointF(r.right() - 0.3 * w, r.top() + 0.3 * h)
                p4 = QPointF(r.left() + 0.3 * w, r.bottom() - 0.3 * h)
                painter.drawLine(p3, p4)

        finally:
            if painter.isActive():
                painter.end()


class _FetchedModelIdPopup(QWidget):
    def __init__(
        self,
        models: list[str],
        current_model_id: str,
        on_selected: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        try:
            flags |= Qt.WindowType.NoDropShadowWindowHint
        except AttributeError:
            pass
        super().__init__(parent, flags)
        self.setObjectName("FetchedModelIdPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self._models = list(models)
        self._current_model_id = str(current_model_id or "").strip()
        self._on_selected = on_selected
        self._row_widgets: list[QPushButton | QLabel] = []
        if parent is not None:
            parent.installEventFilter(self)

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(8, 8, 8, 8)
        self._root_layout.setSpacing(7)

        self._title = QLabel(f"已获取 {len(self._models)} 个模型 ID")
        self._title.setObjectName("FetchedModelIdPopupTitle")
        self._root_layout.addWidget(self._title)

        self._search = QLineEdit()
        self._search.setObjectName("FetchedModelIdPopupSearch")
        self._search.setPlaceholderText("搜索模型 ID")
        self._search.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self._search.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._search.setInputMethodHints(Qt.InputMethodHint.ImhNone)
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._refresh_rows)
        self._root_layout.addWidget(self._search)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("FetchedModelIdPopupScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._container = QWidget()
        self._container.setObjectName("FetchedModelIdPopupList")
        self._list_layout = QVBoxLayout(self._container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(3)
        self._scroll.setWidget(self._container)
        self._root_layout.addWidget(self._scroll)

        self.setStyleSheet("""
            QWidget#FetchedModelIdPopup {
                background: #ffffff;
                border: 1px solid #dfe4ec;
                border-radius: 8px;
            }
            QLabel#FetchedModelIdPopupTitle {
                color: #111827;
                font-size: 12px;
                font-weight: 800;
                padding: 1px 2px;
            }
            QLineEdit#FetchedModelIdPopupSearch {
                min-height: 28px;
                max-height: 30px;
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 2px 9px;
                color: #111827;
                font-size: 12px;
            }
            QLineEdit#FetchedModelIdPopupSearch:focus {
                background: #ffffff;
                border: 1px solid #cbd5e1;
            }
            QScrollArea#FetchedModelIdPopupScroll {
                border: none;
                background: transparent;
            }
            QWidget#FetchedModelIdPopupList {
                background: transparent;
            }
            QPushButton#FetchedModelIdPopupItem {
                min-height: 30px;
                max-height: 30px;
                background: transparent;
                border: none;
                border-radius: 6px;
                color: #374151;
                font-size: 12px;
                font-weight: 500;
                padding: 0px 10px;
                text-align: left;
            }
            QPushButton#FetchedModelIdPopupItem:hover {
                background: #f3f4f6;
                color: #111827;
            }
            QPushButton#FetchedModelIdPopupItem[activeModel="true"] {
                background: #e8f0ff;
                color: #1d4ed8;
                font-weight: 800;
            }
            QLabel#FetchedModelIdPopupEmpty {
                min-height: 68px;
                color: #94a3b8;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 10px;
                background: transparent;
            }
            QScrollBar:vertical {
                border: none;
                background: #f1f5f9;
                width: 8px;
                margin: 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #cbd5e1;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #94a3b8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        self._refresh_rows()
        self._resize_for_rows()

    def eventFilter(self, obj, event) -> bool:
        parent = self.parentWidget()
        if obj is parent and self.isVisible():
            if event.type() in {QEvent.Type.KeyPress, QEvent.Type.InputMethod}:
                self._focus_search()
                QApplication.sendEvent(self._search, event)
                return True
        return super().eventFilter(obj, event)

    def _clear_rows(self) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._row_widgets = []

    def _filtered_models(self) -> list[str]:
        query = str(self._search.text() or "").strip().lower()
        if not query:
            return list(self._models)
        return [model_id for model_id in self._models if query in model_id.lower()]

    def _refresh_rows(self) -> None:
        self._clear_rows()
        filtered = self._filtered_models()
        if not filtered:
            empty = QLabel("没有匹配的模型 ID\n换个关键词试试")
            empty.setObjectName("FetchedModelIdPopupEmpty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            empty.setFixedHeight(68)
            self._list_layout.addWidget(empty)
            self._row_widgets.append(empty)
            return

        for model_id in filtered:
            btn = QPushButton(model_id)
            btn.setObjectName("FetchedModelIdPopupItem")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("activeModel", model_id == self._current_model_id)
            btn.clicked.connect(lambda _checked=False, value=model_id: self._select_model(value))
            btn.setToolTip("左键填入模型 ID，右键复制模型 ID")
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, value=model_id, button=btn: self._show_model_id_context_menu(
                    value,
                    button.mapToGlobal(pos),
                )
            )
            self._list_layout.addWidget(btn)
            self._row_widgets.append(btn)
        self._list_layout.addStretch(1)

    def _show_model_id_context_menu(self, model_id: str, global_pos: QPoint) -> None:
        popup = OcrGenericMenuPopup(
            [("复制模型 ID", lambda value=str(model_id or ""): self._copy_model_id(value), True)],
            parent=self,
        )
        popup.show_at_pos(global_pos)

    def _copy_model_id(self, model_id: str) -> None:
        text = str(model_id or "").strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        original = self._title.text()
        self._title.setText("已复制模型 ID")
        # 定时器以 self._title 为 parent：标题控件销毁时定时器一并销毁，
        # 避免延迟回调访问已删除的 QLabel（PyQt6 会把回调内未处理异常
        # 升级为 qFatal 直接杀死进程）。
        restore_timer = QTimer(self._title)
        restore_timer.setSingleShot(True)
        restore_timer.timeout.connect(
            lambda expected="已复制模型 ID", restore=original: self._title.setText(restore)
            if self._title.text() == expected
            else None
        )
        restore_timer.timeout.connect(restore_timer.deleteLater)
        restore_timer.start(1200)

    def _resize_for_rows(self) -> None:
        parent_w = int(self.parentWidget().width()) if self.parentWidget() is not None else 280
        width = max(320, min(520, parent_w + 120))
        visible_rows = max(1, min(8, len(self._models)))
        height = min(430, 96 + visible_rows * 33)
        self.setFixedSize(width, height)

    def _select_model(self, model_id: str) -> None:
        self.close()
        self._on_selected(str(model_id or "").strip())

    def show_at_pos(self, global_pos: QPoint) -> None:
        margin = 6
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        bounds = screen.availableGeometry()
        x = int(global_pos.x())
        y = int(global_pos.y())
        if y + self.height() > bounds.bottom() + 1 - margin:
            y = y - self.height()
        if x + self.width() > bounds.right() + 1 - margin:
            x = bounds.right() + 1 - margin - self.width()
        x = max(bounds.left() + margin, x)
        y = max(bounds.top() + margin, min(y, bounds.bottom() + 1 - margin - self.height()))
        self.move(QPoint(x, y))
        self.show()
        self._focus_search()
        QTimer.singleShot(0, self._focus_search)
        QTimer.singleShot(80, self._focus_search)

    def _focus_search(self) -> None:
        try:
            parent = self.parentWidget()
            if parent is not None:
                parent.clearFocus()
            self.raise_()
            self.activateWindow()
            QApplication.setActiveWindow(self)
            self._search.setFocus(Qt.FocusReason.PopupFocusReason)
            input_method = QGuiApplication.inputMethod()
            input_method.reset()
            input_method.update(
                Qt.InputMethodQuery.ImEnabled
                | Qt.InputMethodQuery.ImHints
                | Qt.InputMethodQuery.ImCursorRectangle
            )
        except Exception:
            self._search.setFocus(Qt.FocusReason.PopupFocusReason)
