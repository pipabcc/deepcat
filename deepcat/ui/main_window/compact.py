from __future__ import annotations

from typing import Any
from PyQt6.QtCore import Qt, QTimer, QRect, QSize, QEvent, QPoint, pyqtSignal, QUrl
from PyQt6.QtGui import QAction, QGuiApplication, QColor, QPainter, QPen, QDesktopServices, QFontMetrics, QFont, QBrush
from PyQt6.QtWidgets import (
    QDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QMenu,
    QFrame,
    QLineEdit,
)
from deepcat.table_notes_store import default_ima_config

from deepcat.ui.main_window.tab_password import _TabPasswordVerifyDialog


class _CompactToolTip(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
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
        self._alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        self._word_wrap = False
        self._font = QFont("Microsoft YaHei UI", 11)
        self.setFont(self._font)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(3000)
        self._hide_timer.timeout.connect(self.hide)

    def show_text(self, text: str, pos: QPoint, *, direction: str = "above", alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, padding: int = 28, max_width: int = 0, max_height: int = 0) -> None:
        new_text = str(text or "").strip()
        if not new_text:
            self.hide()
            return
        self._alignment = alignment
        metrics = QFontMetrics(self._font)

        self._word_wrap = bool(max_width > 0)
        text_flags = int(Qt.TextFlag.TextWordWrap) | int(alignment)
        if max_width > 0:
            calc_width = max(42, max_width - 32)
            calc_rect = QRect(0, 0, calc_width, 10000)
            bounding = metrics.boundingRect(calc_rect, text_flags, new_text)
            new_w = min(max_width, bounding.width() + 32)
            new_h = bounding.height() + 28
            if max_height > 0:
                new_h = min(new_h, max_height)
        else:
            lines = [line for line in new_text.split("\n") if line]
            if not lines:
                lines = [""]
            max_w = max(int(metrics.horizontalAdvance(line)) for line in lines)
            line_h = int(metrics.height())
            line_count = len(lines)
            new_w = max(42, max_w + padding + 12)
            new_h = max(34, line_count * line_h + (line_count - 1) * 4 + 28)

        new_w = max(42, new_w)
        new_h = max(34, new_h)

        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)
        new_x = int(pos.x() - new_w / 2)
        margin = 6
        if direction == "above":
            new_y = int(pos.y() - new_h - 10)
            if new_y < int(geo.top() + margin):
                new_y = int(pos.y() + 10)
        else:
            new_y = int(pos.y() + 10)

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
        self._hide_timer.start()
        self.update()

    def hide(self) -> None:
        self._hide_timer.stop()
        super().hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRect(1, 1, max(1, self.width() - 2), max(1, self.height() - 2))
            painter.setPen(QPen(QColor(0, 0, 0, 36), 1))
            painter.setBrush(QBrush(QColor(255, 255, 255, 255)))
            painter.drawRoundedRect(rect, 8, 8)

            painter.setFont(self._font)
            painter.setPen(QColor(17, 24, 39))
            text_rect = rect.adjusted(12, 6, -12, -6)
            text_flags = int(self._alignment)
            if bool(getattr(self, "_word_wrap", False)):
                text_flags |= int(Qt.TextFlag.TextWordWrap)
            painter.drawText(text_rect, text_flags, self._text)
        finally:
            if painter.isActive():
                painter.end()


class _CompactLaterReadItemWidget(QWidget):
    openRequested = pyqtSignal(str)
    deleteRequested = pyqtSignal(QWidget)

    def __init__(self, item: dict, compact_win) -> None:
        super().__init__(compact_win)
        self.setObjectName("CompactLaterReadItemWidget")
        self._parent_window = compact_win
        self._item_id = str(item.get("id", ""))
        title = str(item.get("title", "") or item.get("url", "") or "未命名链接")
        self._full_title = title + "\n" + str(item.get("url", ""))
        self._title_text = title

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 10, 4)
        layout.setSpacing(6)

        self._dot = QLabel("•")
        self._dot.setFixedWidth(10)
        self._dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        from deepcat.ui.main_window.window import MainWindow
        dot_color = "#f59e0b" if MainWindow._later_read_item_is_pinned(item) else ("#9fb1c8" if bool(item.get("read", False)) else "#173a67")
        self._dot.setStyleSheet(f"color: {dot_color}; font-size: 16px; font-weight: bold;")
        self._dot.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dot.installEventFilter(self)
        layout.addWidget(self._dot)

        self._title = QLabel("")
        self._title.setObjectName("LaterReadTitle")
        self._title.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title.setStyleSheet("color: #111827; font-size: 12px;")
        layout.addWidget(self._title, 1)

        self._delete_btn = QPushButton(self)
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.setFixedSize(20, 20)
        self._delete_btn.hide()

        self.setStyleSheet("""
            QWidget#CompactLaterReadItemWidget {
                background: transparent;
            }
            QWidget#CompactLaterReadItemWidget:hover {
                background-color: rgba(30, 41, 59, 0.04);
            }
            QWidget#CompactLaterReadItemWidget:hover QLabel#LaterReadTitle {
                color: #2563eb;
            }
            QPushButton {
                border: none;
                background: transparent;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: rgba(30, 41, 59, 0.08);
            }
        """)

        self._delete_btn.clicked.connect(lambda: self.deleteRequested.emit(self))

    def eventFilter(self, watched, event) -> bool:
        if watched == self._dot:
            if event.type() == QEvent.Type.Enter:
                try:
                    main_win = self._parent_window._main_window
                    max_w = main_win.width()
                    max_h = main_win.height()

                    dot_rect = self._dot.rect()
                    global_pos = self._dot.mapToGlobal(QPoint(dot_rect.width() // 2, -4))

                    self._parent_window._tooltip.show_text(
                        self._full_title,
                        global_pos,
                        direction="above",
                        max_width=max_w,
                        max_height=max_h
                    )
                except Exception:
                    pass
            elif event.type() == QEvent.Type.Leave:
                try:
                    self._parent_window._tooltip.hide()
                except Exception:
                    pass
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        btn_w, btn_h = 20, 20
        x = self.width() - btn_w - 6
        y = (self.height() - btn_h) // 2
        self._delete_btn.setGeometry(x, y, btn_w, btn_h)

        fm = QFontMetrics(self._title.font())
        available_w = max(50, self.width() - 32)
        elided_text = fm.elidedText(self._title_text, Qt.TextElideMode.ElideRight, available_w)
        self._title.setText(elided_text)

    def enterEvent(self, event) -> None:
        self._delete_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._delete_btn.hide()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._delete_btn.isVisible() and self._delete_btn.rect().contains(self._delete_btn.mapFrom(self, event.position().toPoint())):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.openRequested.emit(self._item_id)
            event.accept()


class _CompactClipboardItemWidget(QWidget):
    copyRequested = pyqtSignal(int)
    deleteRequested = pyqtSignal(QWidget)

    def __init__(self, record_id: int, content: str, compact_win) -> None:
        super().__init__(compact_win)
        self.setObjectName("CompactClipboardItemWidget")
        self._parent_window = compact_win
        self._record_id = record_id
        self._full_title = content
        cleaned = " ".join(content.splitlines()).strip()
        self._title_text = cleaned

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 10, 4)
        layout.setSpacing(6)

        self._dot = QLabel("•")
        self._dot.setFixedWidth(10)
        self._dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dot.setStyleSheet("color: #173a67; font-size: 16px; font-weight: bold;")
        self._dot.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dot.installEventFilter(self)
        layout.addWidget(self._dot)

        self._title = QLabel("")
        self._title.setObjectName("ClipboardTitle")
        self._title.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title.setStyleSheet("color: #111827; font-size: 12px;")
        layout.addWidget(self._title, 1)

        self._delete_btn = QPushButton(self)
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.setFixedSize(20, 20)
        self._delete_btn.hide()

        self.setStyleSheet("""
            QWidget#CompactClipboardItemWidget {
                background: transparent;
            }
            QWidget#CompactClipboardItemWidget:hover {
                background-color: rgba(30, 41, 59, 0.04);
            }
            QWidget#CompactClipboardItemWidget:hover QLabel#ClipboardTitle {
                color: #2563eb;
            }
            QPushButton {
                border: none;
                background: transparent;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: rgba(30, 41, 59, 0.08);
            }
        """)

        self._delete_btn.clicked.connect(lambda: self.deleteRequested.emit(self))

    def eventFilter(self, watched, event) -> bool:
        if watched == self._dot:
            if event.type() == QEvent.Type.Enter:
                try:
                    main_win = self._parent_window._main_window
                    max_w = main_win.width()
                    max_h = main_win.height()

                    dot_rect = self._dot.rect()
                    global_pos = self._dot.mapToGlobal(QPoint(dot_rect.width() // 2, -4))

                    self._parent_window._tooltip.show_text(
                        self._full_title,
                        global_pos,
                        direction="above",
                        max_width=max_w,
                        max_height=max_h
                    )
                except Exception:
                    pass
            elif event.type() == QEvent.Type.Leave:
                try:
                    self._parent_window._tooltip.hide()
                except Exception:
                    pass
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        btn_w, btn_h = 20, 20
        x = self.width() - btn_w - 6
        y = (self.height() - btn_h) // 2
        self._delete_btn.setGeometry(x, y, btn_w, btn_h)

        fm = QFontMetrics(self._title.font())
        available_w = max(50, self.width() - 32)
        elided_text = fm.elidedText(self._title_text, Qt.TextElideMode.ElideRight, available_w)
        self._title.setText(elided_text)

    def enterEvent(self, event) -> None:
        self._delete_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._delete_btn.hide()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._delete_btn.isVisible() and self._delete_btn.rect().contains(self._delete_btn.mapFrom(self, event.position().toPoint())):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.copyRequested.emit(self._record_id)
            event.accept()


class _CompactListWindow(QDialog):
    def __init__(self, main_window, page_type: str) -> None:
        super().__init__(None)
        self._main_window = main_window
        self._page_type = page_type
        self._drag_position = QPoint()
        self._drag_start_pos = QPoint()
        self._is_collapsed = False
        self._db = None
        self._items = []
        self._records = []
        from deepcat.clipboard_history.query_worker import ClipboardQueryController
        self._query_controller = ClipboardQueryController(self)
        self._query_controller.completed.connect(self._on_clipboard_query_completed)
        self._query_controller.failed.connect(self._on_clipboard_query_failed)

        self._tooltip = _CompactToolTip()

        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(300, 500)

        self.container = QWidget(self)
        self.container.setObjectName("CompactContainer")
        self.container.setFixedSize(280, 480)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(shadow)

        from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
        self.container.setStyleSheet(f"""
            QWidget#CompactContainer {{
                background-color: {MAIN_WINDOW_BACKGROUND};
                border: 1px solid #e2e8f0;
                border-radius: 12px;
            }}
        """)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(6, 12, 6, 12)
        layout.setSpacing(8)

        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(4, 2, 4, 2)

        title_lbl = QLabel("稍后阅读" if page_type == "later_read" else "复制记录")
        title_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #1e293b;")

        self.min_btn = QPushButton("—")
        self.min_btn.setObjectName("CompactMinBtn")
        self.min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.min_btn.setFixedSize(20, 20)
        self.min_btn.setStyleSheet("""
            QPushButton#CompactMinBtn {
                border: none !important;
                background: transparent !important;
                color: #334155 !important;
                font-size: 10px !important;
                font-weight: bold !important;
                border-radius: 4px !important;
            }
            QPushButton#CompactMinBtn:hover {
                color: #2563eb !important;
                background-color: rgba(37, 99, 235, 0.08) !important;
            }
        """)
        self.min_btn.clicked.connect(self._on_min_clicked)

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("CompactCloseBtn")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setStyleSheet("""
            QPushButton#CompactCloseBtn {
                border: none !important;
                background: transparent !important;
                color: #334155 !important;
                font-size: 12px !important;
                font-weight: bold !important;
                border-radius: 4px !important;
            }
            QPushButton#CompactCloseBtn:hover {
                color: #ef4444 !important;
                background-color: rgba(239, 68, 68, 0.08) !important;
            }
        """)
        self.close_btn.clicked.connect(self.close)

        title_layout.addWidget(title_lbl)
        title_layout.addStretch(1)
        title_layout.addWidget(self.min_btn)
        title_layout.addWidget(self.close_btn)
        layout.addLayout(title_layout)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索标题或网址..." if page_type == "later_read" else "搜索记录内容...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedHeight(30)
        self.search_input.setStyleSheet("""
            QLineEdit {
                padding: 0 8px;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                background: white;
                color: #1f2937;
                font-size: 12px;
            }
            QLineEdit:hover {
                border-color: #cbd5e1;
            }
            QLineEdit:focus {
                border-color: #94a3b8;
            }
        """)
        self.search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_input)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("CompactListWidget")
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setStyleSheet("""
            QListWidget#CompactListWidget {
                background: transparent;
                border: 1px solid #f1f5f9;
                border-radius: 8px;
            }
            QListWidget#CompactListWidget::item {
                background: transparent;
                border-bottom: 1px solid #f1f5f9;
                padding: 0px;
            }
            QListWidget#CompactListWidget::item:selected {
                background: transparent;
            }
        """)
        layout.addWidget(self.list_widget, 1)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.addWidget(self.container)

        self._position_window()

        if self._page_type == "clipboard":
            if hasattr(self._main_window, "_clipboard_history_page") and self._main_window._clipboard_history_page:
                self._db = getattr(self._main_window._clipboard_history_page, "_database", None)
            if self._db is None:
                try:
                    from deepcat.clipboard_history.clipboard_database import ClipboardDatabase
                    self._db = ClipboardDatabase()
                except Exception as e:
                    pass

        # 滚动无限加载相关状态
        self._offset = 0
        self._loading_more = False
        self._has_more = True
        self.list_widget.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

        self.load_data()

    def _position_window(self) -> None:
        try:
            parent_geo = self._main_window.geometry()
            x = parent_geo.right() - self.width() + 10
            y = parent_geo.top() + 50
            if self._page_type == "clipboard":
                y += 60
            self.move(x, y)
        except Exception:
            pass

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._drag_start_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if hasattr(self, "_is_collapsed") and self._is_collapsed:
                curr_pos = event.globalPosition().toPoint()
                start_pos = getattr(self, "_drag_start_pos", curr_pos)
                dist = (curr_pos - start_pos).manhattanLength()

                # 排除点击在关闭按钮或最小化按钮上
                local_close = self.close_btn.mapFrom(self, event.position().toPoint())
                local_min = self.min_btn.mapFrom(self, event.position().toPoint())
                is_click_close = self.close_btn.rect().contains(local_close)
                is_click_min = self.min_btn.rect().contains(local_min)

                if dist < 5 and not is_click_close and not is_click_min:
                    self._toggle_collapse(False)
                    event.accept()
                    return
        super().mouseReleaseEvent(event)

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

            menu.exec(event.globalPos())
            event.accept()
        else:
            super().contextMenuEvent(event)

    def _on_min_clicked(self) -> None:
        self._toggle_collapse(not self._is_collapsed)

    def _toggle_collapse(self, collapse: bool) -> None:
        self._is_collapsed = collapse
        if collapse:
            self.search_input.hide()
            self.list_widget.hide()
            self.container.setFixedSize(280, 44)
            self.setFixedSize(300, 64)
            self.min_btn.setText("＋")
            self.min_btn.setToolTip("还原")
        else:
            self.container.setFixedSize(280, 480)
            self.setFixedSize(300, 500)
            self.search_input.show()
            self.list_widget.show()
            self.min_btn.setText("—")
            self.min_btn.setToolTip("最小化")

    def closeEvent(self, event) -> None:
        self._query_controller.cancel()
        try:
            self._tooltip.close()
        except Exception:
            pass
        super().closeEvent(event)

    def load_data(self) -> None:
        self.list_widget.clear()
        self._offset = 0
        self._loading_more = False
        self._has_more = True

        keyword = self.search_input.text().strip()

        if self._page_type == "later_read":
            self._items = self._main_window._current_later_read_items()
            filtered = []
            for item in self._items:
                title = str(item.get("title", "") or "").lower()
                url = str(item.get("url", "") or "").lower()
                if keyword:
                    keywords = [k for k in keyword.lower().split() if k]
                    if keywords and all(k in title or k in url for k in keywords):
                        filtered.append(item)
                else:
                    filtered.append(item)

            filtered = self._main_window._sort_later_read_items_for_display(filtered, "time")
            for item in filtered:
                widget = _CompactLaterReadItemWidget(item, self)
                widget._delete_btn.setIcon(self._main_window._asset_icon("icon_todo_delete.svg"))
                widget.openRequested.connect(self._open_later_read_url)
                widget.deleteRequested.connect(self._delete_later_read)

                list_item = QListWidgetItem(self.list_widget)
                list_item.setSizeHint(QSize(0, 32))
                self.list_widget.addItem(list_item)
                self.list_widget.setItemWidget(list_item, widget)

        elif self._page_type == "clipboard" and self._db is not None:
            self._query_controller.submit(self._db, keyword=keyword, include_pinned=True)

    def _on_clipboard_query_completed(self, query, payload) -> None:
        records = payload["records"]
        if query.offset == 0:
            self._records = []
            self.list_widget.clear()
        known_ids = {record[0] for record in self._records}
        for record in records:
            if record[0] in known_ids:
                continue
            known_ids.add(record[0])
            self._records.append(record)
            widget = _CompactClipboardItemWidget(record[0], record[1], self)
            widget._delete_btn.setIcon(self._main_window._asset_icon("icon_todo_delete.svg"))
            widget.copyRequested.connect(self._copy_clipboard_record)
            widget.deleteRequested.connect(self._delete_clipboard_record)
            item = QListWidgetItem(self.list_widget)
            item.setSizeHint(QSize(0, 32))
            self.list_widget.setItemWidget(item, widget)
        self._offset = query.offset + len(records)
        self._has_more = payload["has_more"]
        self._loading_more = False

    def _on_clipboard_query_failed(self, message: str) -> None:
        self._loading_more = False
        from deepcat.utils.logger import get_logger
        get_logger().warning("紧凑复制记录查询失败: %s", message)

    def _on_scroll_changed(self, value: int) -> None:
        if self._page_type != "clipboard":
            return
        scroll_bar = self.list_widget.verticalScrollBar()
        if value >= scroll_bar.maximum() - 15 and not self._loading_more and self._has_more:
            self._load_more_data()

    def _load_more_data(self) -> None:
        if (self._page_type != "clipboard" or self._db is None or self._loading_more
                or not self._has_more or self._query_controller.busy):
            return
        self._loading_more = True
        self._query_controller.submit(
            self._db, keyword=self.search_input.text().strip(), offset=self._offset, include_pinned=True,
        )

    def _on_search_changed(self) -> None:
        self.load_data()

    def _open_later_read_url(self, item_id: str) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        for item in self._items:
            if str(item.get("id", "")) == item_id:
                url = str(item.get("url", "")).strip()
                if url:
                    QDesktopServices.openUrl(QUrl(url))
                    item["read"] = True
                    self._main_window._save_later_read_items(self._items, refresh=True)
                    self.load_data()
                break

    def _delete_later_read(self, item_widget) -> None:
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            w = self.list_widget.itemWidget(item)
            if w == item_widget:
                self.list_widget.takeItem(i)
                item_widget.deleteLater()
                break

        item_id = item_widget._item_id
        self._items = [x for x in self._items if str(x.get("id", "")) != item_id]

        def do_save():
            try:
                self._main_window._save_later_read_items(self._items, refresh=True)
            except Exception:
                pass
        QTimer.singleShot(50, do_save)

    def _copy_clipboard_record(self, record_id: int) -> None:
        if self._db is None:
            return
        try:
            r = self._db.get_record_by_id(record_id)
            if r:
                content = str(r[1] or "")
                content_type = str(r[2] or "text")
                file_path = str(r[4] or "")
                from PyQt6.QtGui import QGuiApplication
                clipboard = QGuiApplication.clipboard()
                if clipboard:
                    copy_text = file_path if content_type in ("file_path", "folder_path", "image") and file_path else content
                    try:
                        page = getattr(self._main_window, "_clipboard_history_page", None)
                        monitor = getattr(page, "_monitor", None)
                        if monitor is not None:
                            monitor.ignore_next_content(copy_text)
                    except Exception:
                        pass
                    clipboard.setText(copy_text)
                    self._main_window._send_tray_notification("已复制到剪贴板", "记录内容已成功复制到剪贴板！", 1500)
        except Exception:
            pass

    def _delete_clipboard_record(self, item_widget) -> None:
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            w = self.list_widget.itemWidget(item)
            if w == item_widget:
                self.list_widget.takeItem(i)
                item_widget.deleteLater()
                break

        record_id = item_widget._record_id
        def do_delete():
            try:
                if self._db is not None:
                    self._db.delete_record(record_id)
                    if hasattr(self._main_window, "_clipboard_history_page") and self._main_window._clipboard_history_page:
                        self._main_window._clipboard_history_page._refresh_list()
            except Exception:
                pass
        QTimer.singleShot(50, do_delete)


class StyledInputDialog(QInputDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
        self.setStyleSheet(f"""
            QInputDialog {{
                background-color: {MAIN_WINDOW_BACKGROUND};
            }}
            QLabel {{
                color: #0f172a;
                font-size: 13px;
            }}
            QLineEdit {{
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 4px 8px;
                color: #0f172a;
                font-size: 13px;
            }}
            QLineEdit:hover, QLineEdit:focus {{
                border-color: #94a3b8;
            }}
            QComboBox {{
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 4px 8px;
                color: #0f172a;
                font-size: 13px;
                min-width: 150px;
            }}
            QComboBox:hover, QComboBox:focus {{
                border-color: #94a3b8;
            }}
            QComboBox QAbstractItemView {{
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                selection-background-color: #3b82f6;
                selection-color: white;
                color: #0f172a;
            }}
            QPushButton {{
                background-color: #1e293b;
                color: white;
                border: none;
                border-radius: 6px;
                min-width: 75px;
                min-height: 28px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #334155;
            }}
            QPushButton:pressed {{
                background-color: #0f172a;
            }}
        """)

    def _apply_caption_color(self) -> None:
        try:
            import os
            import ctypes
            if os.name != "nt":
                return
            from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND_COLORREF
            dwmapi = ctypes.windll.dwmapi
            hwnd = int(self.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_caption_color()

    @classmethod
    def get_text(cls, parent, title, label, text="") -> tuple[str, bool]:
        dialog = cls(parent)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setTextValue(text)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        ok = dialog.exec()
        return dialog.textValue(), bool(ok)

    @classmethod
    def get_item(cls, parent, title, label, items, current=0, editable=False) -> tuple[str, bool]:
        dialog = cls(parent)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setComboBoxItems(items)
        dialog.setComboBoxEditable(editable)
        if items and 0 <= current < len(items):
            dialog.setTextValue(items[current])
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        ok = dialog.exec()
        return dialog.textValue(), bool(ok)


class StyledDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {MAIN_WINDOW_BACKGROUND};
            }}
            QLabel {{
                color: #0f172a;
                font-size: 13px;
            }}
            QLineEdit {{
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 4px 8px;
                color: #0f172a;
                font-size: 13px;
            }}
            QLineEdit:hover, QLineEdit:focus {{
                border-color: #94a3b8;
            }}
            QScrollArea, QScrollArea > QWidget {{
                background: transparent;
            }}
            QDialogButtonBox QPushButton {{
                background-color: #1e293b;
                color: white;
                border: none;
                border-radius: 6px;
                min-width: 75px;
                min-height: 28px;
                font-size: 13px;
                font-weight: bold;
            }}
            QDialogButtonBox QPushButton:hover {{
                background-color: #334155;
            }}
            QDialogButtonBox QPushButton:pressed {{
                background-color: #0f172a;
            }}
            QDialogButtonBox QPushButton:disabled {{
                background-color: #cbd5e1;
                color: #ffffff;
            }}
        """)

    def _apply_caption_color(self) -> None:
        try:
            import os
            import ctypes
            if os.name != "nt":
                return
            from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND_COLORREF
            dwmapi = ctypes.windll.dwmapi
            hwnd = int(self.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_caption_color()


class StyledMessageBox(QMessageBox):
    def __init__(self, parent=None):
        from PyQt6.QtWidgets import QWidget
        actual_parent = parent if isinstance(parent, QWidget) else None
        super().__init__(actual_parent)
        from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
        self.setStyleSheet(f"""
            QMessageBox {{
                background-color: {MAIN_WINDOW_BACKGROUND};
            }}
            QLabel {{
                color: #0f172a;
                font-size: 13px;
            }}
            QPushButton {{
                background-color: #1e293b;
                color: white;
                border: none;
                border-radius: 6px;
                min-width: 75px;
                min-height: 28px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #334155;
            }}
            QPushButton:pressed {{
                background-color: #0f172a;
            }}
        """)

    def _apply_caption_color(self) -> None:
        try:
            import os
            import ctypes
            if os.name != "nt":
                return
            from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND_COLORREF
            dwmapi = ctypes.windll.dwmapi
            hwnd = int(self.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_caption_color()

    @classmethod
    def question(cls, parent, title, text, buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, default_button=QMessageBox.StandardButton.No):
        import types
        is_builtin = isinstance(QMessageBox.question, types.BuiltinFunctionType) or str(QMessageBox.question).startswith("<built-in method")
        if not is_builtin:
            return QMessageBox.question(parent, title, text, buttons, default_button)
        box = cls(parent)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Question)
        box.setStandardButtons(buttons)
        box.setDefaultButton(default_button)
        return box.exec()

    @classmethod
    def warning(cls, parent, title, text, buttons=QMessageBox.StandardButton.Ok, default_button=QMessageBox.StandardButton.NoButton):
        import types
        is_builtin = isinstance(QMessageBox.warning, types.BuiltinFunctionType) or str(QMessageBox.warning).startswith("<built-in method")
        if not is_builtin:
            return QMessageBox.warning(parent, title, text, buttons, default_button)
        box = cls(parent)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setStandardButtons(buttons)
        box.setDefaultButton(default_button)
        return box.exec()

    @classmethod
    def information(cls, parent, title, text, buttons=QMessageBox.StandardButton.Ok, default_button=QMessageBox.StandardButton.NoButton):
        import types
        is_builtin = isinstance(QMessageBox.information, types.BuiltinFunctionType) or str(QMessageBox.information).startswith("<built-in method")
        if not is_builtin:
            return QMessageBox.information(parent, title, text, buttons, default_button)
        box = cls(parent)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Information)
        box.setStandardButtons(buttons)
        box.setDefaultButton(default_button)
        return box.exec()

    @classmethod
    def critical(cls, parent, title, text, buttons=QMessageBox.StandardButton.Ok, default_button=QMessageBox.StandardButton.NoButton):
        import types
        is_builtin = isinstance(QMessageBox.critical, types.BuiltinFunctionType) or str(QMessageBox.critical).startswith("<built-in method")
        if not is_builtin:
            return QMessageBox.critical(parent, title, text, buttons, default_button)
        box = cls(parent)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setStandardButtons(buttons)
        box.setDefaultButton(default_button)
        return box.exec()


class _GroupManageDialog(QDialog):
    """分组管理对话框"""
    def __init__(self, tab_type: str, tabs: list[dict[str, Any]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("管理分组")
        self.setMinimumSize(320, 240)
        self.tab_type = tab_type
        self.tabs = tabs
        self.parent_win = parent
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        label = QLabel("当前已有的分组列表：")
        label.setStyleSheet("font-weight: bold; color: #374151;")
        layout.addWidget(label)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                border: 1px solid #dfe4ec;
                border-radius: 6px;
                padding: 4px;
                background-color: #ffffff;
            }
            QListWidget::item {
                height: 36px;
                border-bottom: 1px solid #f3f4f6;
                color: #374151;
            }
            QListWidget::item:hover {
                background-color: #f9fafb;
                color: #111827;
            }
            QListWidget::item:selected {
                background-color: #f3f4f6;
                color: #111827;
                font-weight: bold;
            }
            QListWidget::item:last {
                border-bottom: none;
            }
        """)
        layout.addWidget(self.list_widget, 1)

        groups = sorted(list({t.get("group_name", "").strip() for t in self.tabs if t.get("group_name", "").strip()}))
        for g in groups:
            item = QListWidgetItem(g)
            self.list_widget.addItem(item)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch(1)

        rename_btn = QPushButton("重命名")
        rename_btn.setObjectName("BtnSecondary")
        rename_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rename_btn.clicked.connect(self._on_rename)
        btn_layout.addWidget(rename_btn)

        delete_btn = QPushButton("删除分组")
        delete_btn.setObjectName("BtnDanger")
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.clicked.connect(self._on_delete)
        btn_layout.addWidget(delete_btn)

        btn_layout.addStretch(1)
        layout.addLayout(btn_layout)

        from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
        self.setStyleSheet(f"QDialog {{ background-color: {MAIN_WINDOW_BACKGROUND}; }}\n" + """
            QPushButton#BtnPrimary {
                background-color: #dc2626;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton#BtnPrimary:hover {
                background-color: #b91c1c;
            }
            QPushButton#BtnSecondary {
                background-color: #f3f4f6;
                color: #374151;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton#BtnSecondary:hover {
                background-color: #e5e7eb;
            }
            QPushButton#BtnDanger {
                background-color: #fee2e2;
                color: #dc2626;
                border: 1px solid #fca5a5;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton#BtnDanger:hover {
                background-color: #fecaca;
            }
        """)

    def _on_rename(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        old_name = item.text()
        new_name, ok = StyledInputDialog.get_text(self, "重命名分组", f"将分组「{old_name}」重命名为:", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            new_name = new_name.strip()
            changed_indices: list[int] = []
            for index, t in enumerate(self.tabs):
                if t.get("group_name", "") == old_name:
                    t["group_name"] = new_name
                    changed_indices.append(index)
            if self.parent_win:
                for index in changed_indices:
                    self.parent_win._mark_table_notes_tab_dirty(self.tab_type, index)
                if self.tab_type == "table" and getattr(self.parent_win, "_current_table_group", "") == old_name:
                    self.parent_win._current_table_group = new_name
                    btn = getattr(self.parent_win._table_tab_bar, "_group_btn", None)
                    if btn:
                        btn.setText(f"分组: {new_name} ▾")
                elif self.tab_type == "note" and getattr(self.parent_win, "_current_note_group", "") == old_name:
                    self.parent_win._current_note_group = new_name
                    btn = getattr(self.parent_win._note_tab_bar, "_group_btn", None)
                    if btn:
                        btn.setText(f"分组: {new_name} ▾")

            item.setText(new_name)
            if self.parent_win:
                self.parent_win._save_table_notes_settings()
                self.parent_win._refresh_tab_bars()

    def _on_delete(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        group_name = item.text()

        msg_box = StyledMessageBox(self)
        msg_box.setWindowTitle("删除分组确认")
        msg_box.setText(f"您确定要删除分组「{group_name}」吗？")
        msg_box.setInformativeText("请选择删除策略：\n\n【保留数据】: 仅删除分组，保留原属于该分组的所有标签页，并将它们归为“未分组”。\n【连同数据一起删除】: 彻底删除该分组以及其下的所有标签页。")

        keep_data_btn = msg_box.addButton("保留数据", QMessageBox.ButtonRole.YesRole)
        delete_all_btn = msg_box.addButton("连同数据一起删除", QMessageBox.ButtonRole.NoRole)
        cancel_btn = msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)

        msg_box.exec()
        clicked = msg_box.clickedButton()

        if clicked == cancel_btn:
            return

        old_active_index = -1
        old_active_tab_id: int | None = None
        if self.parent_win:
            old_active_index = (
                self.parent_win._active_table_tab
                if self.tab_type == "table"
                else self.parent_win._active_note_tab
            )
            if 0 <= old_active_index < len(self.tabs):
                old_active_tab_id = id(self.tabs[old_active_index])

        if clicked == keep_data_btn:
            changed_indices: list[int] = []
            for index, t in enumerate(self.tabs):
                if t.get("group_name", "") == group_name:
                    t["group_name"] = ""
                    changed_indices.append(index)
            if self.parent_win:
                for index in changed_indices:
                    self.parent_win._mark_table_notes_tab_dirty(self.tab_type, index)
            StyledMessageBox.information(self, "成功", f"已删除分组「{group_name}」，其中包含的标签页已移至“未分组”。")
        elif clicked == delete_all_btn:
            remaining = [t for t in self.tabs if t.get("group_name", "") != group_name]
            protected_passwords: list[str] = []
            seen_passwords: set[str] = set()
            for tab in self.tabs:
                if tab.get("group_name", "") != group_name:
                    continue
                password = str(tab.get("password", "") or "")
                if password and password not in seen_passwords:
                    protected_passwords.append(password)
                    seen_passwords.add(password)
            for password in protected_passwords:
                dialog = _TabPasswordVerifyDialog(password, self.parent_win or self)
                if not dialog.exec():
                    return
            old_active_removed = (
                old_active_tab_id is not None
                and all(id(t) != old_active_tab_id for t in remaining)
            )
            if old_active_removed and remaining and self.parent_win:
                replacement_index = min(max(old_active_index, 0), len(remaining) - 1)
                replacement_password = str(remaining[replacement_index].get("password", "") or "")
                if replacement_password:
                    dialog = _TabPasswordVerifyDialog(replacement_password, self.parent_win)
                    if not dialog.exec():
                        return
            if not remaining:
                if self.tab_type == "table":
                    self.tabs[:] = [{"name": "表格1", "data": [], "column_widths": self.parent_win._default_table_column_widths(), "group_name": ""}]
                else:
                    self.tabs[:] = [{"name": "记事本1", "html": "", "group_name": "", "ima_config": default_ima_config()}]
            else:
                self.tabs[:] = remaining
            StyledMessageBox.information(self, "成功", f"已成功删除分组「{group_name}」及其中所有的标签页。")

        if self.parent_win:
            if self.tab_type == "table" and getattr(self.parent_win, "_current_table_group", "") == group_name:
                self.parent_win._current_table_group = "全部"
                btn = getattr(self.parent_win._table_tab_bar, "_group_btn", None)
                if btn:
                    btn.setText("分组: 全部 ▾")
            elif self.tab_type == "note" and getattr(self.parent_win, "_current_note_group", "") == group_name:
                self.parent_win._current_note_group = "全部"
                btn = getattr(self.parent_win._note_tab_bar, "_group_btn", None)
                if btn:
                    btn.setText("分组: 全部 ▾")

        row = self.list_widget.row(item)
        self.list_widget.takeItem(row)

        if self.parent_win:
            if self.tab_type == "table":
                self.parent_win._active_table_tab = self._active_index_after_tabs_changed(
                    old_active_tab_id,
                    old_active_index,
                )
                self.parent_win._load_table_notes_view_safely(self.parent_win._load_current_table_tab_data)
            else:
                self.parent_win._active_note_tab = self._active_index_after_tabs_changed(
                    old_active_tab_id,
                    old_active_index,
                )
                self.parent_win._load_table_notes_view_safely(self.parent_win._load_current_note_tab_data)
            self.parent_win._save_table_notes_settings()
            self.parent_win._refresh_tab_bars()

    def _active_index_after_tabs_changed(self, old_tab_id: int | None, old_index: int) -> int:
        if not self.tabs:
            return 0
        if old_tab_id is not None:
            for index, tab in enumerate(self.tabs):
                if id(tab) == old_tab_id:
                    return index
        return min(max(old_index, 0), len(self.tabs) - 1)

    def _apply_caption_color(self) -> None:
        try:
            import os
            import ctypes
            if os.name != "nt":
                return
            from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND_COLORREF
            dwmapi = ctypes.windll.dwmapi
            hwnd = int(self.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_caption_color()
