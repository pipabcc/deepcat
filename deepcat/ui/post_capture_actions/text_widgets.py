from __future__ import annotations

import time
from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QPushButton, QHBoxLayout, QFrame, QWidget, QLabel, QTextEdit, QVBoxLayout, QStyle
from PyQt6.QtWidgets import QFrame, QPushButton, QStyle, QStyleOptionButton
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame
from PyQt6.QtGui import QPainter, QPixmap, QPen, QColor
from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton

from deepcat.ui.post_capture_actions._shared import _AI_THINKING_CARD_TEMP_DISABLED
from deepcat.ui.post_capture_actions.helpers import _trace_ai_panel
from deepcat.ui.post_capture_actions.model_menus import OcrGenericMenuPopup


class ExpandIconButton(QPushButton):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(22, 22)
        self.setObjectName("ExpandIconButton")
        self._is_expanded = False
        self.setMouseTracking(True)

    def set_expanded(self, expanded: bool) -> None:
        if self._is_expanded != expanded:
            self._is_expanded = expanded
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        is_hovered = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        is_pressed = bool(opt.state & QStyle.StateFlag.State_Sunken)

        if is_hovered:
            bg_color = QColor(19, 104, 232, 36) if not is_pressed else QColor(19, 104, 232, 60)
            painter.setBrush(bg_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(self.rect(), 4, 4)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        arrow_color = QColor("#475569") if not is_hovered else QColor("#1368e8")
        pen = QPen(arrow_color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        cx = self.width() // 2
        cy = self.height() // 2

        if self._is_expanded:
            # 向上折回 ∧
            path = QPainterPath()
            path.moveTo(cx - 4, cy + 2)
            path.lineTo(cx, cy - 2)
            path.lineTo(cx + 4, cy + 2)
            painter.drawPath(path)
        else:
            # 向下展开 ∨
            path = QPainterPath()
            path.moveTo(cx - 4, cy - 2)
            path.lineTo(cx, cy + 2)
            path.lineTo(cx + 4, cy - 2)
            painter.drawPath(path)

        painter.end()


def is_input_method_composing(editor: QTextEdit | None) -> bool:
    check = getattr(editor, "is_composing", None)
    return bool(check()) if callable(check) else False


class PremiumTextEdit(QTextEdit):
    files_pasted = pyqtSignal(list)
    image_pasted = pyqtSignal(str)
    at_triggered = pyqtSignal()
    exit_requested = pyqtSignal()
    composition_changed = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        import time
        self._init_time = time.time()
        self._ime_preedit_active = False
        self._handling_input_method_event = False

    def is_composing(self) -> bool:
        return bool(
            getattr(self, "_ime_preedit_active", False)
            or getattr(self, "_handling_input_method_event", False)
        )

    def inputMethodEvent(self, event) -> None:
        was_composing = self.is_composing()
        self._ime_preedit_active = bool(event.preeditString())
        self._handling_input_method_event = True
        if not was_composing:
            self.composition_changed.emit(True)
        try:
            # 上屏会同步触发 textChanged；直到 Qt 处理完本次输入法事件才恢复界面刷新。
            super().inputMethodEvent(event)
        finally:
            self._handling_input_method_event = False
            if not self._ime_preedit_active:
                self.composition_changed.emit(False)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if self._ime_preedit_active:
            self._ime_preedit_active = False
            self.composition_changed.emit(False)

    def event(self, event) -> bool:
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and self.is_composing()
            and event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape}
        ):
            event.accept()
            return True
        return super().event(event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            import os
            urls = event.mimeData().urls()
            paths = []
            for url in urls:
                p = url.toLocalFile()
                if p and os.path.exists(p):
                    paths.append(p)
            if paths:
                self.files_pasted.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def insertFromMimeData(self, source) -> None:
        if source is None:
            super().insertFromMimeData(source)
            return

        # 1. 拦截文件粘贴 (从资源管理器中复制)
        if source.hasUrls():
            import os
            urls = source.urls()
            paths = []
            for url in urls:
                p = url.toLocalFile()
                if p and os.path.exists(p):
                    paths.append(p)
            if paths:
                self.files_pasted.emit(paths)
                return

        # 2. 拦截图片像素粘贴 (如截图)
        if source.hasImage():
            image = source.imageData()
            if image:
                try:
                    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
                    from PyQt6.QtGui import QImage
                    ba = QByteArray()
                    buffer = QBuffer(ba)
                    buffer.open(QIODevice.OpenModeFlag.WriteOnly)

                    qimg = image
                    if not isinstance(qimg, QImage):
                        if hasattr(image, "value"):
                            qimg = image.value()

                    if qimg and not qimg.isNull():
                        qimg.save(buffer, "PNG")
                        b64_data = ba.toBase64().data().decode("utf-8")
                        self.image_pasted.emit(b64_data)
                        return
                except Exception:
                    pass

        super().insertFromMimeData(source)

    def keyPressEvent(self, event) -> None:
        if self.is_composing():
            super().keyPressEvent(event)
            return
        import time
        if int(event.key()) == int(Qt.Key.Key_Escape):
            self.exit_requested.emit()
            event.accept()
            return

        # 1. 过滤因快捷键唤起（如双击 Ctrl+C）在毫秒级焦点竞争中漏进来的单个 "c" 字符
        if (time.time() - getattr(self, "_init_time", 0.0)) < 0.200:
            if event.text().lower() == "c" and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                return

        # 2. 首位输入 "@" 时触发菜单并完全吃掉此按键，防止文件名后多出 @ 字符
        if event.text() == "@":
            cursor = self.textCursor()
            if cursor.position() == 0:
                self.at_triggered.emit()
                return

        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
        try:
            has_selected = self.textCursor().hasSelection()
            can_undo = self.document().isUndoAvailable()
            can_redo = self.document().isRedoAvailable()
            from PyQt6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            can_paste = bool(clipboard.text())

            menu_items = [
                ("撤销", self.undo, can_undo),
                ("重做", self.redo, can_redo),
                ("-", None, True),
                ("剪切", self.cut, has_selected),
                ("复制", self.copy, has_selected),
                ("粘贴", self.paste, can_paste),
                ("-", None, True),
                ("全选", self.selectAll, True),
                ("-", None, True),
                ("退出", self.exit_requested.emit, True),
            ]

            popup = OcrGenericMenuPopup(menu_items, parent=self)
            try:
                gp = event.globalPosition().toPoint()
            except Exception:
                gp = event.globalPos()
            popup.show_at_pos(gp)
        except Exception:
            super().contextMenuEvent(event)


class RoundedTextEditContainer(QFrame):
    def __init__(self, editor: QTextEdit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RoundedTextEditContainer")
        self._editor = editor

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self._editor)

        self._editor.setFrameStyle(QFrame.Shape.NoFrame)
        self._editor.setStyleSheet("background: transparent; border: none;")
        self._editor.installEventFilter(self)
        self._editor.setMouseTracking(True)
        if self._editor.viewport() is not None:
            self._editor.viewport().installEventFilter(self)
            self._editor.viewport().setMouseTracking(True)

        self._hovered = False
        self._focused = False
        self._resizing = False
        self._drag_start_y = 0.0
        self._start_height = 0
        self.setMouseTracking(True)

        # 输入区保持滚动和拖动调高，不再提供内容过多时自动出现的展开按钮。
        self._is_expanded = False
        self._pre_expand_height = 0
        self._pre_expand_sibling_height = 0
        self._pre_expand_window_height = 0
        self._expand_btn = None

        self.update_style()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._editor or (self._editor.viewport() is not None and obj is self._editor.viewport()):
            from PyQt6.QtCore import QEvent, Qt, QPoint

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
                if not getattr(self, "_resizing", False):
                    self._editor.setCursor(Qt.CursorShape.IBeamCursor)
                    if self._editor.viewport() is not None:
                        self._editor.viewport().setCursor(Qt.CursorShape.IBeamCursor)
                self.update_style()
            elif event.type() == QEvent.Type.MouseMove:
                if getattr(self, "_resizing", False):
                    # 安全阀：如果鼠标移动时检测到左键并没有按住，则强制安全退出拉伸状态，杜绝任何粘连
                    if not (event.buttons() & Qt.MouseButton.LeftButton):
                        self._resizing = False
                        self._editor.setCursor(Qt.CursorShape.IBeamCursor)
                        if self._editor.viewport() is not None:
                            self._editor.viewport().setCursor(Qt.CursorShape.IBeamCursor)
                    else:
                        # 处于拉伸状态下，拖动计算容器新高度
                        delta_y = event.globalPosition().y() - self._drag_start_y
                        p = self.window()
                        if p is not None:
                            # 获取屏幕最大高度限制
                            from PyQt6.QtGui import QGuiApplication
                            screen = QGuiApplication.screenAt(p.pos()) or QGuiApplication.primaryScreen()
                            max_h = screen.availableGeometry().height() - 16 if screen else 800

                            editor_container = getattr(p, "_editor_container", None)
                            translation_container = getattr(p, "_translation_container", None)

                            # 判定是否可以消长：两个大框都存在，且回答框可见，且拖拽的是问题框
                            if (editor_container is not None and
                                    translation_container is not None and
                                    translation_container.isVisible() and
                                    self is editor_container):

                                target_editor_h = max(50, self._start_height + int(delta_y))
                                start_sibling_h = getattr(self, "_start_sibling_height", 0)
                                target_trans_h = start_sibling_h - (target_editor_h - self._start_height)

                                if target_trans_h >= 50:
                                    # 在消长安全区内，保持窗口总高度绝对不变，直接重新排布两者高度
                                    self.setFixedHeight(target_editor_h)
                                    translation_container.setFixedHeight(target_trans_h)
                                else:
                                    # 回答框已压缩到极限 50px，若继续往下拖，则回答框锁死 50px，开始拉伸窗口总高度
                                    translation_container.setFixedHeight(50)
                                    actual_delta = target_editor_h - self.height()
                                    target_window_h = p.height() + actual_delta
                                    if target_window_h <= max_h:
                                        self.setFixedHeight(target_editor_h)
                                        p.resize(p.width(), target_window_h)
                            else:
                                # 其他拉伸情况（回答框拉伸，或问题框隐藏）：直接等比调整窗口高度，并拦截限高
                                target_h = max(50, self._start_height + int(delta_y))
                                actual_delta = target_h - self.height()
                                if actual_delta != 0:
                                    target_window_h = p.height() + actual_delta
                                    if target_window_h <= max_h:
                                        self.setFixedHeight(target_h)
                                        p.resize(p.width(), target_window_h)

                # 注意：此处为独立的 if 判断。如果由于上面左键未按住而被重置了拉伸状态，
                # 能够无缝进入普通悬浮敏感区逻辑，动态决定是否保持/重置光标指针。
                if not getattr(self, "_resizing", False):
                    # 检测鼠标位置是否在输入框容器的最底部 10 像素敏感区内
                    pos_in_self = self.mapFromGlobal(event.globalPosition().toPoint())
                    margin = 10
                    if self.height() - margin <= pos_in_self.y() <= self.height() + 4:
                        self._editor.setCursor(Qt.CursorShape.SizeVerCursor)
                        if self._editor.viewport() is not None:
                            self._editor.viewport().setCursor(Qt.CursorShape.SizeVerCursor)
                    else:
                        self._editor.setCursor(Qt.CursorShape.IBeamCursor)
                        if self._editor.viewport() is not None:
                            self._editor.viewport().setCursor(Qt.CursorShape.IBeamCursor)
            elif event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    pos_in_self = self.mapFromGlobal(event.globalPosition().toPoint())
                    margin = 10
                    if self.height() - margin <= pos_in_self.y() <= self.height() + 4:
                        self._resizing = True
                        self._drag_start_y = event.globalPosition().y()
                        self._start_height = self.height()

                        if getattr(self, "_is_expanded", False):
                            self._is_expanded = False
                            self._expand_btn.set_expanded(False)

                        p = self.window()
                        if p is not None:
                            if hasattr(p, "_is_expanded"):
                                p._is_expanded = False
                            if hasattr(p, "_pre_expand_snapshot"):
                                p._pre_expand_snapshot = None

                        # 额外记录同胞框的初始高度，供消长算法使用
                        self._start_sibling_height = 0
                        p = self.window()
                        if p is not None:
                            editor_container = getattr(p, "_editor_container", None)
                            translation_container = getattr(p, "_translation_container", None)
                            if editor_container is not None and translation_container is not None:
                                sibling = translation_container if self is editor_container else editor_container
                                if sibling.isVisible():
                                    self._start_sibling_height = sibling.height()

                        # 拦截此按下事件，避免输入框进入光标选字状态影响拖拽
                        event.accept()
                        return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if getattr(self, "_resizing", False):
                    self._resizing = False
                    self._editor.setCursor(Qt.CursorShape.IBeamCursor)
                    if self._editor.viewport() is not None:
                        self._editor.viewport().setCursor(Qt.CursorShape.IBeamCursor)
                    event.accept()
                    return True
        return super().eventFilter(obj, event)

    def update_style(self) -> None:
        self.setProperty("focused", self._focused)
        self.setProperty("hovered", self._hovered)
        self.style().polish(self)
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

    def _check_expand_button_visibility(self) -> None:
        return

    def _handle_expand_clicked(self) -> None:
        p = self.window()
        if p is not None and hasattr(p, "toggle_editor_expand"):
            p.toggle_editor_expand()


class _ThinkingCard(QFrame):
    """
    精致优雅的 AI 流式思考卡片组件 (与上下控件实现不透明无缝融合)
    """
    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ThinkingCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._elapsed = 0.0
        self._is_thinking = False
        self._is_expanded = False
        self._thinking_text = ""
        self._hovered = False
        self._model_name = ""
        self._status_title = "思考中"
        self._done_status_title = "已思考"
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._update_time)
        self._start_time = 0.0
        self._pending_thinking_chunks: list[str] = []
        self._thinking_flush_timer = QTimer(self)
        self._thinking_flush_timer.setSingleShot(True)
        self._thinking_flush_timer.timeout.connect(self._flush_pending_thinking_text)

        # UI 构建
        root_layout = QHBoxLayout(self)
        # 紧凑态卡片固定 32px，高 DPI/加粗字体的真实行高会超过 12px；
        # 收紧内部纵向留白，保证状态行完整显示，同时不改变卡片整体高度。
        root_layout.setContentsMargins(8, 6, 1, 6)
        # 间距由 10px 缩窄为 6px，使图标区极其紧凑精致
        root_layout.setSpacing(6)

        # 1. 左侧图标 (发光 AI 标志，尺寸缩减为 16x16 以减少宽度占用)
        self._icon_label = QLabel()
        self._icon_label.setFixedSize(16, 16)

        try:
            from deepcat.ui.main_window import _assets_dir
            icon_path = str((_assets_dir() / "icon_nav_ai.svg").resolve()).replace("\\", "/")
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                self._icon_label.setPixmap(pixmap.scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            else:
                self._icon_label.setText("💡")
                self._icon_label.setStyleSheet("font-size: 11px; background: transparent; border: none;")
        except Exception:
            self._icon_label.setText("💡")
            self._icon_label.setStyleSheet("font-size: 11px; background: transparent; border: none;")

        root_layout.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignTop)

        # 2. 中间文本区域 (垂直布局)
        text_widget = QWidget()
        text_widget.setStyleSheet("background: transparent; border: none;")
        text_layout = QVBoxLayout(text_widget)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)

        # 第一行：状态标题 + 耗时
        self._status_label = QLabel("思考中 (用时 0.0 秒)")
        self._status_label.setStyleSheet("font-size: 12px; font-weight: 700; color: #475569; background: transparent; border: none;")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._status_label.setFixedHeight(18)
        text_layout.addWidget(self._status_label)

        # 第二行：流式思考内容 (使用只读 PremiumTextEdit，始终使用 ScrollBarAsNeeded 支持所有高度下的滚动)
        self._content_edit = PremiumTextEdit()
        self._content_edit.setReadOnly(True)
        self._content_edit.setFrameStyle(QFrame.Shape.NoFrame)
        self._content_edit.setStyleSheet("QTextEdit { background: transparent; border: none; font-size: 11px; color: #94a3b8; padding: 0px; }")
        self._content_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # 极细美化垂直滚动条
        self._content_edit.verticalScrollBar().setStyleSheet("""
            QScrollBar:vertical { background: transparent; width: 4px; margin: 0px; }
            QScrollBar::handle:vertical { background: #cbd5e1; border-radius: 2px; }
            QScrollBar::handle:vertical:hover { background: #94a3b8; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)
        # 禁用交互选字，让它的点击事件能够穿透到卡片本身以便触发折叠/展开
        self._content_edit.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        text_layout.addWidget(self._content_edit)

        root_layout.addWidget(text_widget, 1, Qt.AlignmentFlag.AlignVCenter)

        # 3. 右侧展开折叠按钮 (脱离布局，改用绝对悬浮定位在右上角，避免挤占滚动条宽度)
        self._arrow_btn = QPushButton(self)
        self._arrow_btn.setFixedSize(16, 16)
        self._arrow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._arrow_btn.setText("∨")
        self._arrow_btn.setStyleSheet("""
            QPushButton {
                border: none;
                background: transparent;
                color: #64748b;
                font-size: 10px;
                font-weight: 700;
                border-radius: 4px;
            }
            QPushButton:hover {
                background: rgba(30, 41, 59, 0.06);
                color: #1e293b;
            }
        """)

        # 与输入框和回答框完全一致的边框粗细与颜色，并且背景色调整为纯白色 #ffffff，实现无缝垂直嵌入
        self.setStyleSheet("""
            QWidget#ThinkingCard {
                background-color: #ffffff;
                border-left: 1px solid #e2e8f0;
                border-right: 1px solid #e2e8f0;
                border-top: 1px solid #e2e8f0;
                border-bottom: none;
                border-radius: 0px;
                margin: 0px;
                margin-top: -1px;
            }
            QWidget#ThinkingCard[hovered="true"] {
                border-left: 1px solid #cbd5e1;
                border-right: 1px solid #cbd5e1;
                border-top: 1px solid #cbd5e1;
                margin: 0px;
                margin-top: -1px;
            }
        """)

        self.installEventFilter(self)
        self._arrow_btn.clicked.connect(self.toggle_expand)
        self.hide()

        # 确保悬浮按钮置顶于界面最上层，防止被其他控件遮挡
        self._arrow_btn.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        btn_w, btn_h = 16, 16
        self._arrow_btn.setFixedSize(btn_w, btn_h)
        # 绝对定位在右上角，保留右侧 6px 以避开 4px 极细滚动条，呈现精致感
        self._arrow_btn.setGeometry(self.width() - btn_w - 6, 6, btn_w, btn_h)
        self._notify_panel_seam_refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._notify_panel_seam_refresh()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._notify_panel_seam_refresh()

    def _notify_panel_seam_refresh(self) -> None:
        panel = self.window()
        refresh_surface = getattr(panel, "_refresh_interaction_surface", None)
        if callable(refresh_surface):
            try:
                refresh_surface(defer=True)
                return
            except Exception:
                pass
        refresh = getattr(panel, "_update_interaction_seam_covers", None)
        if callable(refresh):
            try:
                refresh(defer=True)
            except Exception:
                pass

    def eventFilter(self, watched, event) -> bool:
        if watched is self and event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = self._arrow_btn.mapFromGlobal(event.globalPosition().toPoint())
                if not self._arrow_btn.rect().contains(pos):
                    # 如果点击的是垂直滚动条区域，不应该触发卡片收起/展开，以防用户滚动文本时卡片塌陷
                    scroll_bar = self._content_edit.verticalScrollBar()
                    if scroll_bar.isVisible():
                        scroll_pos = scroll_bar.mapFromGlobal(event.globalPosition().toPoint())
                        if scroll_bar.rect().contains(scroll_pos):
                            return super().eventFilter(watched, event)

                    self.toggle_expand()
                    return True
        return super().eventFilter(watched, event)

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update_style()
        super().leaveEvent(event)

    def update_style(self) -> None:
        self.setProperty("hovered", self._hovered)
        self.style().polish(self)
        self.update()

    def is_thinking(self) -> bool:
        return bool(self._is_thinking)

    def _model_suffix(self) -> str:
        """返回状态栏末尾的模型别名后缀（与 set_model_and_elapsed 的格式一致）。
        无模型别名时返回空串。"""
        name = str(getattr(self, "_model_name", "") or "").strip()
        return f"  {name}" if name else ""

    def start_thinking(
        self,
        reposition: bool = True,
        model_name: str = "",
        status_title: str = "",
        done_status_title: str = "",
    ) -> None:
        if _AI_THINKING_CARD_TEMP_DISABLED:
            self._thinking_text = ""
            self._pending_thinking_chunks.clear()
            self._thinking_flush_timer.stop()
            self._timer.stop()
            self._is_thinking = False
            self._is_expanded = False
            self.hide()
            return

        p = self.window()
        if getattr(self, "_is_thinking", False) and self.isVisible():
            reposition = False

        _trace_ai_panel(
            p,
            "thinking.start.begin",
            card_geometry=self.geometry(),
            card_max_h=self.maximumHeight(),
            card_visible=self.isVisible(),
        )
        self._thinking_text = ""
        self._pending_thinking_chunks.clear()
        self._thinking_flush_timer.stop()
        self._elapsed = 0.0
        self._is_thinking = True
        self._is_expanded = False  # 初始折叠
        self._model_name = str(model_name or "").strip()
        self._status_title = "思考中"
        self._done_status_title = "已思考"
        self._status_label.setText(f"{self._status_title} (用时 0.0 秒){self._model_suffix()}")
        self._content_edit.setPlainText("")
        self._arrow_btn.setText("∨")

        # 初始思考中无内容时，保持 32px 高度并隐藏文本编辑区和展开折叠按钮，避免闪烁和高度空变
        self.setMaximumHeight(32)
        self._content_edit.hide()
        self._arrow_btn.hide()
        self._content_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content_edit.verticalScrollBar().setValue(0)

        self._start_time = time.time()
        self._timer.start()
        _trace_ai_panel(
            p,
            "thinking.start.before_show",
            card_geometry=self.geometry(),
            card_max_h=self.maximumHeight(),
            card_visible=self.isVisible(),
        )
        self.show()
        _trace_ai_panel(
            p,
            "thinking.start.after_show",
            card_geometry=self.geometry(),
            card_max_h=self.maximumHeight(),
            card_visible=self.isVisible(),
        )
        if reposition and p is not None and hasattr(p, "_reposition"):
            _trace_ai_panel(
                p,
                "thinking.start.before_reposition",
                card_geometry=self.geometry(),
                card_max_h=self.maximumHeight(),
                card_visible=self.isVisible(),
            )
            p._reposition()
            _trace_ai_panel(
                p,
                "thinking.start.after_reposition",
                card_geometry=self.geometry(),
                card_max_h=self.maximumHeight(),
                card_visible=self.isVisible(),
            )

    def add_thinking_delta(self, chunk: str) -> None:
        if _AI_THINKING_CARD_TEMP_DISABLED:
            return
        if not self._is_thinking:
            return

        chunk_text = str(chunk or "")
        if not chunk_text:
            return
        had_content = bool(self._thinking_text.strip())
        self._thinking_text += chunk_text
        has_content = bool(self._thinking_text.strip())

        # 首次有实质思考内容，从 32px 展开为 68px
        if not had_content and has_content:
            self._content_edit.show()
            self._arrow_btn.show()
            self.setMaximumHeight(150 if self._is_expanded else 68)
            p = self.window()
            if p is not None and hasattr(p, "_reposition"):
                _trace_ai_panel(
                    p,
                    "thinking.delta.first_content.before_reposition",
                    card_geometry=self.geometry(),
                    card_max_h=self.maximumHeight(),
                    chunk_len=len(str(chunk or "")),
                )
                p._reposition()
                _trace_ai_panel(
                    p,
                    "thinking.delta.first_content.after_reposition",
                    card_geometry=self.geometry(),
                    card_max_h=self.maximumHeight(),
                    chunk_len=len(str(chunk or "")),
                )

        self._pending_thinking_chunks.append(chunk_text)
        if not self._thinking_flush_timer.isActive():
            self._thinking_flush_timer.start(220)

    def _flush_pending_thinking_text(self) -> None:
        if _AI_THINKING_CARD_TEMP_DISABLED:
            self._pending_thinking_chunks.clear()
            return
        if not self._pending_thinking_chunks:
            return
        pending_text = "".join(self._pending_thinking_chunks)
        self._pending_thinking_chunks.clear()
        try:
            from PyQt6.QtGui import QTextCursor
            cursor = self._content_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(pending_text)
            self._content_edit.setTextCursor(cursor)
        except Exception:
            self._content_edit.setPlainText(self._thinking_text)

        # 多的内容动态自动移到最新内容的最底部，保证流式接收时光标始终处于最新内容区
        bar = self._content_edit.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def stop_thinking(self) -> None:
        if _AI_THINKING_CARD_TEMP_DISABLED:
            self._thinking_text = ""
            self._pending_thinking_chunks.clear()
            self._thinking_flush_timer.stop()
            self._timer.stop()
            self._is_thinking = False
            self._is_expanded = False
            self.hide()
            return
        if not self._is_thinking:
            return
        p = self.window()
        self._flush_pending_thinking_text()
        _trace_ai_panel(
            p,
            "thinking.stop.begin",
            card_geometry=self.geometry(),
            card_max_h=self.maximumHeight(),
            has_thinking=bool(self._thinking_text.strip()),
        )
        self._is_thinking = False
        self._timer.stop()


        elapsed = time.time() - self._start_time
        done_text = f"{self._done_status_title} (用时 {elapsed:.1f} 秒){self._model_suffix()}"
        if self._status_label.text() != done_text:
            self._status_label.setText(done_text)

        # 思考完成后，根据是否有实际思考文本自适应调整界面紧凑度与让位逻辑
        has_thinking = bool(self._thinking_text.strip())
        if has_thinking:
            self._content_edit.show()
            self._arrow_btn.show()
            if self._is_expanded:
                self.setMaximumHeight(300)
            else:
                self.setMaximumHeight(68)
            # 无论折叠还是展开，思考完成后确保滚动条停留在内容的最底部（即最大值），不要回到最开头
            bar = self._content_edit.verticalScrollBar()
            if bar is not None:
                bar.setValue(bar.maximum())
        else:
            # 无思考文本：彻底隐藏中间空白输入框与展开折叠按钮，死锁紧凑高度32px，将空间完美让位给问题输入框
            self._content_edit.hide()
            self._arrow_btn.hide()
            self._is_expanded = False
            self.setMaximumHeight(32)

        if p is not None and hasattr(p, "_reposition"):
            _trace_ai_panel(
                p,
                "thinking.stop.before_reposition",
                card_geometry=self.geometry(),
                card_max_h=self.maximumHeight(),
                has_thinking=bool(self._thinking_text.strip()),
            )
            p._reposition()
            _trace_ai_panel(
                p,
                "thinking.stop.after_reposition",
                card_geometry=self.geometry(),
                card_max_h=self.maximumHeight(),
                has_thinking=bool(self._thinking_text.strip()),
            )

    def toggle_expand(self) -> None:
        # 如果没有实际思考内容，直接禁止展开/折叠
        if not bool(self._thinking_text.strip()):
            return

        self._is_expanded = not self._is_expanded

        if self._is_expanded:
            # 展开状态下依据是否在思考中，自适应设置最大高度上限：思考中 150px，思考完成后 300px
            max_h = 150 if self._is_thinking else 300
            self.setMaximumHeight(max_h)

            # 若是在思考中点击展开，顺便把滚动条拉到底部以显示最新流式结果
            bar = self._content_edit.verticalScrollBar()
            if bar is not None and self._is_thinking:
                bar.setValue(bar.maximum())
        else:
            # 折叠状态下最大高度限制在 68px，多的内容依然支持自由滚动，但在收拢的瞬间滚动位置复位到 0 以保持整洁
            self.setMaximumHeight(68)
            self._content_edit.verticalScrollBar().setValue(0)

        self._arrow_btn.setText("∧" if self._is_expanded else "∨")

        p = self.window()
        if p is not None and hasattr(p, "_reposition"):
            p._reposition()

    def _update_time(self) -> None:
        if _AI_THINKING_CARD_TEMP_DISABLED:
            return
        if not self._is_thinking:
            return
        elapsed = time.time() - self._start_time
        status_text = f"{self._status_title} (用时 {elapsed:.1f} 秒){self._model_suffix()}"
        if self._status_label.text() != status_text:
            self._status_label.setText(status_text)

    def set_model_and_elapsed(self, model: str, elapsed: float, tokens_str: str = "") -> None:
        if _AI_THINKING_CARD_TEMP_DISABLED:
            return
        current_status = self._status_label.text()
        if "  " in current_status:
            current_status = current_status.split("  ")[0]
        status_text = f"{current_status}  {model} | {elapsed:.2f}秒{tokens_str}"
        if self._status_label.text() != status_text:
            self._status_label.setText(status_text)
