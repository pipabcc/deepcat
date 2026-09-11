from __future__ import annotations

import os
import shutil
import time
import html
from pathlib import Path
from typing import Any, Optional
from PyQt6.QtCore import Qt, QThread, QRect, QEvent, QPoint, pyqtSignal, QUrl, QMimeData
from PyQt6.QtGui import QGuiApplication, QColor, QImage, QPixmap, QDesktopServices, QTextCharFormat, QTextCursor, QBrush, QPalette, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemDelegate,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QStyle,
)
from deepcat.settings_store import get_image_output_dir
from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionButton, QStyleOptionViewItem

from deepcat.ui.main_window.compact import _CompactListWindow, _GroupManageDialog, StyledInputDialog, StyledDialog, StyledMessageBox
from deepcat.ui.main_window.helpers import _note_auto_link_parts

class _ReturnDownDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        from PyQt6.QtWidgets import QStyleOptionViewItem
        from PyQt6.QtGui import QPalette

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        fg = index.data(Qt.ItemDataRole.ForegroundRole)
        if fg is not None:
            opt.palette.setBrush(QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText, fg)
            opt.palette.setBrush(QPalette.ColorGroup.Inactive, QPalette.ColorRole.HighlightedText, fg)
        else:
            default_text = opt.palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
            opt.palette.setColor(QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText, default_text)
            opt.palette.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.HighlightedText, default_text)

        super().paint(painter, opt, index)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.commitData.emit(obj)
            self.closeEditor.emit(obj, QAbstractItemDelegate.EndEditHint.NoHint)
            table = self.parent()
            if isinstance(table, QTableWidget):
                current = table.currentIndex()
                next_row = current.row() + 1
                if next_row < table.rowCount():
                    table.setCurrentIndex(table.model().index(next_row, current.column()))
            return True
        return super().eventFilter(obj, event)


class _NoFocusTableDelegate(QStyledItemDelegate):
    _CHECK_SIZE = 14

    def _is_centered_check_index(self, index) -> bool:
        return index.column() == 0 and bool(index.flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def _centered_check_rect(self, option) -> QRect:
        size = self._CHECK_SIZE
        return QRect(
            option.rect.center().x() - size // 2,
            option.rect.center().y() - size // 2,
            size,
            size,
        )

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        if not self._is_centered_check_index(index):
            super().paint(painter, opt, index)
            return

        display_opt = QStyleOptionViewItem(opt)
        display_opt.text = ""
        display_opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        style = display_opt.widget.style() if display_opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, display_opt, painter, display_opt.widget)

        check_opt = QStyleOptionButton()
        check_opt.rect = self._centered_check_rect(opt)
        check_opt.state = QStyle.StateFlag.State_Enabled
        if bool(opt.state & QStyle.StateFlag.State_MouseOver):
            check_opt.state |= QStyle.StateFlag.State_MouseOver
        if index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked:
            check_opt.state |= QStyle.StateFlag.State_On
        else:
            check_opt.state |= QStyle.StateFlag.State_Off
        if not bool(index.flags() & Qt.ItemFlag.ItemIsEnabled):
            check_opt.state &= ~QStyle.StateFlag.State_Enabled
        style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck, check_opt, painter, display_opt.widget)

    def editorEvent(self, event, model, option, index):
        if not self._is_centered_check_index(index):
            return super().editorEvent(event, model, option, index)
        if event.type() == QEvent.Type.MouseButtonRelease:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            if self._centered_check_rect(option).contains(pos):
                current = index.data(Qt.ItemDataRole.CheckStateRole)
                next_state = Qt.CheckState.Unchecked if current == Qt.CheckState.Checked else Qt.CheckState.Checked
                return bool(model.setData(index, next_state, Qt.ItemDataRole.CheckStateRole))
            return False
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Select):
            current = index.data(Qt.ItemDataRole.CheckStateRole)
            next_state = Qt.CheckState.Unchecked if current == Qt.CheckState.Checked else Qt.CheckState.Checked
            return bool(model.setData(index, next_state, Qt.ItemDataRole.CheckStateRole))
        return False


class _NotesTable(QTableWidget):
    def __init__(self, rows: int, cols: int, main_win=None):
        super().__init__(rows, cols)
        self._main_win = main_win

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._main_win:
                self._main_win._table_clear_cells_content(self)
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        if not self._main_win:
            return

        items = [
            ("📋 复制选中单元格 (Ctrl+C)", lambda: self._main_win._table_copy_cells(self), True),
            ("📥 粘贴文本到此处 (Ctrl+V)", lambda: self._main_win._table_paste_cells(self), True),
            ("🧹 清除选中单元格内容", lambda: self._main_win._table_clear_cells_content(self), True),
            ("-", None, False),
            ("➕ 在上方插入一行", lambda: self._main_win._table_insert_row_above(self), True),
            ("➕ 在下方插入一行", lambda: self._main_win._table_insert_row_below(self), True),
            ("➖ 删除选中行", lambda: self._main_win._table_delete_row(self), True),
            ("-", None, False),
            ("➕ 在左侧插入一列", lambda: self._main_win._table_insert_col_left(self), True),
            ("➕ 在右侧插入一列", lambda: self._main_win._table_insert_col_right(self), True),
            ("➖ 删除选中列", lambda: self._main_win._table_delete_col(self), True),
            ("-", None, False),
            ("📏 调整表格大小 (修改总行数/列数)...", lambda: self._main_win._table_resize(self), True),
            ("🗑️ 清空整张表格数据", lambda: self._main_win._table_clear_all(self), True),
            ("📌 置顶表格", lambda: self._main_win._pin_tab_content("table", self._main_win._active_table_tab), True),
        ]

        from deepcat.ui.post_capture_actions import OcrGenericMenuPopup
        popup = OcrGenericMenuPopup(items, parent=self)
        popup.show_at_pos(event.globalPos())


class ImagePreviewDialog(QDialog):
    """内置的 Lightbox 风格的高清图片预览对话框"""
    def __init__(self, image_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("ImagePreviewDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._bg_widget = QWidget(self)
        self._bg_widget.setObjectName("LightboxBackground")
        self._bg_widget.setStyleSheet("QWidget#LightboxBackground { background-color: rgba(0, 0, 0, 0.65); }")

        bg_layout = QVBoxLayout(self._bg_widget)
        bg_layout.setContentsMargins(24, 24, 24, 24)
        bg_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._img_label = QLabel(self)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            screen = QGuiApplication.primaryScreen()
            screen_geom = screen.availableGeometry()
            max_w = int(screen_geom.width() * 0.8)
            max_h = int(screen_geom.height() * 0.8)

            if pixmap.width() > max_w or pixmap.height() > max_h:
                display_pixmap = pixmap.scaled(
                    max_w, max_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            else:
                display_pixmap = pixmap
            self._img_label.setPixmap(display_pixmap)
            self._img_label.setFixedSize(display_pixmap.size())
            self._img_label.setStyleSheet("""
                QLabel {
                    border: 2px solid rgba(255, 255, 255, 0.95);
                    border-radius: 8px;
                    background-color: #ffffff;
                }
            """)

        bg_layout.addWidget(self._img_label)
        layout.addWidget(self._bg_widget)

        parent_widget = parent.window() if parent else None
        if parent_widget:
            self.setGeometry(parent_widget.geometry())
        else:
            screen = QGuiApplication.primaryScreen()
            self.setGeometry(screen.availableGeometry())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.accept()
        super().mousePressEvent(event)


class _ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


class _NotesEditor(QTextEdit):
    _RICH_HTML_MARKERS = (
        "<a ",
        "<blockquote",
        "<h1",
        "<h2",
        "<h3",
        "<h4",
        "<h5",
        "<h6",
        "<hr",
        "<img",
        "<li",
        "<ol",
        "<span",
        "<table",
        "<td",
        "<th",
        "<tr",
        "<ul",
    )

    def __init__(self, main_win=None):
        super().__init__()
        self._main_win = main_win
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._apply_force_yahei()

        # 创建字符数统计水印标签
        self._watermark_label = QLabel(self)
        self._watermark_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._watermark_label.setStyleSheet("""
            QLabel {
                color: rgba(107, 114, 128, 0.45);
                background-color: transparent;
                font-family: "Microsoft YaHei", sans-serif;
                font-size: 12px;
                font-weight: 500;
            }
        """)
        self.textChanged.connect(self._update_watermark)
        self._update_watermark()
        self._watermark_label.hide()
        self._init_search_panel()
        self._register_todo_resources()

    def _register_todo_resources(self) -> None:
        from PyQt6.QtGui import QPixmap, QPainter, QPen, QBrush, QColor, QTextDocument
        from PyQt6.QtCore import QRectF, QUrl
        from PyQt6.QtSvg import QSvgRenderer
        from pathlib import Path

        assets_dir = Path(__file__).resolve().parent.parent / "assets"

        # 1. 未勾选
        pix_unchecked = QPixmap(28, 28)
        pix_unchecked.fill(Qt.GlobalColor.transparent)
        painter_u = QPainter(pix_unchecked)
        painter_u.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter_u.setPen(QPen(QColor("#cbd5e1"), 2))
        painter_u.setBrush(QBrush(Qt.GlobalColor.white))
        painter_u.drawRoundedRect(2, 2, 24, 24, 6, 6)
        painter_u.end()
        self.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl("todo_unchecked.png"), pix_unchecked)

        # 2. 已勾选
        pix_checked = QPixmap(28, 28)
        pix_checked.fill(Qt.GlobalColor.transparent)
        painter_c = QPainter(pix_checked)
        painter_c.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter_c.setPen(QPen(QColor("#1e293b"), 2))
        painter_c.setBrush(QBrush(QColor("#1e293b")))
        painter_c.drawRoundedRect(2, 2, 24, 24, 6, 6)

        # 绘制饱满且对比度明显的标准白色对号
        from PyQt6.QtCore import QPointF
        pen_check = QPen(QColor("#ffffff"))
        pen_check.setWidthF(3.0)
        pen_check.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen_check.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter_c.setPen(pen_check)

        painter_c.drawLine(QPointF(7.5, 13.5), QPointF(12.0, 18.0))
        painter_c.drawLine(QPointF(12.0, 18.0), QPointF(20.5, 8.0))

        painter_c.end()
        self.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl("todo_checked.png"), pix_checked)

    def _insert_note_todo(self) -> None:
        from PyQt6.QtGui import QTextImageFormat, QTextCharFormat
        img_fmt = QTextImageFormat()
        img_fmt.setName("todo_unchecked.png")
        img_fmt.setWidth(16)
        img_fmt.setHeight(16)
        img_fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignMiddle)

        cursor = self.textCursor()
        cursor.insertImage(img_fmt)
        cursor.insertText(" ")
        self.setTextCursor(cursor)
        self.setFocus()

    def _is_click_on_todo_image(self, pos):
        from PyQt6.QtGui import QTextCursor
        cursor = self.cursorForPosition(pos)
        curr_pos = cursor.position()
        doc = self.document()

        # 检查右侧字符 (光标在图片左侧)
        if curr_pos < doc.characterCount() - 1:
            test_cursor = QTextCursor(cursor)
            test_cursor.setPosition(curr_pos)
            test_cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
            char_fmt = test_cursor.charFormat()
            if char_fmt.isImageFormat():
                img_name = char_fmt.toImageFormat().name()
                if img_name in ("todo_unchecked.png", "todo_checked.png"):
                    rect = self.cursorRect(cursor)
                    # 图片宽度为 16px，检查点击的 x 坐标是否真的落在图片上
                    if rect.left() <= pos.x() <= rect.left() + 16:
                        test_cursor.clearSelection()
                        test_cursor.setPosition(curr_pos)
                        return test_cursor, img_name

        # 检查左侧字符 (光标在图片右侧)
        if curr_pos > 0:
            test_cursor = QTextCursor(cursor)
            test_cursor.setPosition(curr_pos - 1)
            test_cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
            char_fmt = test_cursor.charFormat()
            if char_fmt.isImageFormat():
                img_name = char_fmt.toImageFormat().name()
                if img_name in ("todo_unchecked.png", "todo_checked.png"):
                    left_cursor = QTextCursor(cursor)
                    left_cursor.setPosition(curr_pos - 1)
                    rect = self.cursorRect(left_cursor)
                    # 检查点击的 x 坐标是否真的落在图片上
                    if rect.left() <= pos.x() <= rect.left() + 16:
                        test_cursor.clearSelection()
                        test_cursor.setPosition(curr_pos - 1)
                        return test_cursor, img_name

        return None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            res = self._is_click_on_todo_image(event.pos())
            if res:
                cursor, img_name = res
                from PyQt6.QtGui import QTextCursor, QTextImageFormat, QTextCharFormat

                # 采用当前活动光标，保证选区动作和状态与文档完全一致
                active_cursor = self.textCursor()
                active_cursor.setPosition(cursor.position())
                active_cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)

                new_fmt = QTextImageFormat()
                new_fmt.setName("todo_checked.png" if img_name == "todo_unchecked.png" else "todo_unchecked.png")
                new_fmt.setWidth(16)
                new_fmt.setHeight(16)
                new_fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignMiddle)

                # 开启编辑块，作为一个原子操作，便于单次 Undo/Redo 撤销还原
                active_cursor.beginEditBlock()
                # 显式删除选中的旧待办图片字符，防止在旁边多生出新勾选框
                active_cursor.removeSelectedText()
                # 在删除位置插入新勾选框图片
                active_cursor.insertImage(new_fmt)
                active_cursor.endEditBlock()

                # 同步活跃光标到新图片右侧
                self.setTextCursor(active_cursor)
                self.textChanged.emit()
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        res = self._is_click_on_todo_image(event.pos())
        if res:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        super().mouseMoveEvent(event)

    def _update_watermark_position(self) -> None:
        if hasattr(self, "_watermark_label") and self._watermark_label:
            margin_x = 15
            margin_y = 10
            scrollbar_w = 0
            if self.verticalScrollBar() and self.verticalScrollBar().isVisible():
                scrollbar_w = self.verticalScrollBar().width()
            scrollbar_h = 0
            if self.horizontalScrollBar() and self.horizontalScrollBar().isVisible():
                scrollbar_h = self.horizontalScrollBar().height()

            self._watermark_label.adjustSize()
            label_w = self._watermark_label.width()
            label_h = self._watermark_label.height()

            x = self.width() - scrollbar_w - label_w - margin_x
            y = self.height() - scrollbar_h - label_h - margin_y
            self._watermark_label.move(x, y)

    def _update_watermark(self) -> None:
        text = self.toPlainText() or ""
        char_count = len(text)
        self._watermark_label.setText(f"字符数: {char_count}")
        self._update_watermark_position()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_watermark_position()
        if hasattr(self, "_search_panel") and self._search_panel.isVisible():
            self._update_search_panel_position()

    def _apply_force_yahei(self) -> None:
        font = self.font()
        font.setFamily("Microsoft YaHei")
        self.setFont(font)
        self.document().setDefaultFont(font)
        self.document().setDefaultStyleSheet("body { font-family: 'Microsoft YaHei', sans-serif; }")
        fmt = self.currentCharFormat()
        fmt.setFontFamily("Microsoft YaHei")
        self.setCurrentCharFormat(fmt)

    def keyPressEvent(self, event) -> None:
        # 当搜索面板显示时，按 Esc 键仅关闭搜索面板，避免直接关闭其它窗口
        if event.key() == Qt.Key.Key_Escape and hasattr(self, "_search_panel") and self._search_panel.isVisible():
            self._hide_search_panel()
            event.accept()
            return
        super().keyPressEvent(event)

    def _create_arrow_icon(self, direction: str) -> QIcon:
        from PyQt6.QtGui import QPixmap, QPainter, QPen, QIcon
        from PyQt6.QtCore import QPointF

        # 动态手绘 V 型折角箭头，避开 Unicode 符号在全局 QSS 或字体下的不显示隐患
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        pen = QPen(QColor("#475569"))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        if direction == "up":
            p1 = QPointF(3, 10)
            p2 = QPointF(8, 5)
            p3 = QPointF(13, 10)
        else:
            p1 = QPointF(3, 6)
            p2 = QPointF(8, 11)
            p3 = QPointF(13, 6)

        painter.drawLine(p1, p2)
        painter.drawLine(p2, p3)
        painter.end()

        return QIcon(pixmap)

    def _create_close_icon(self) -> QIcon:
        from PyQt6.QtGui import QPixmap, QPainter, QPen, QIcon
        from PyQt6.QtCore import QPointF

        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        pen = QPen(QColor("#64748b"))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        painter.drawLine(QPointF(4, 4), QPointF(12, 12))
        painter.drawLine(QPointF(12, 4), QPointF(4, 12))
        painter.end()

        return QIcon(pixmap)

    def _create_toggle_icon(self, expanded: bool) -> QIcon:
        from PyQt6.QtGui import QPixmap, QPainter, QPen, QIcon
        from PyQt6.QtCore import QPointF

        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        pen = QPen(QColor("#64748b"))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        if expanded:
            p1 = QPointF(5, 6)
            p2 = QPointF(8, 9)
            p3 = QPointF(11, 6)
        else:
            p1 = QPointF(6, 5)
            p2 = QPointF(9, 8)
            p3 = QPointF(6, 11)

        painter.drawLine(p1, p2)
        painter.drawLine(p2, p3)
        painter.end()

        return QIcon(pixmap)

    def _init_search_panel(self) -> None:
        from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton, QToolButton, QGraphicsDropShadowEffect
        from PyQt6.QtGui import QShortcut, QKeySequence

        self._search_cursors = []
        self._current_search_index = -1

        self._search_panel = QWidget(self)
        self._search_panel.setObjectName("SearchPanel")
        self._search_panel.setVisible(False)
        self._search_panel.setStyleSheet("""
            QWidget#SearchPanel {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
            }
        """)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(8)
        shadow.setColor(QColor(0, 0, 0, 35))
        shadow.setOffset(0, 2)
        self._search_panel.setGraphicsEffect(shadow)

        # 改用垂直主布局包裹两行
        main_layout = QVBoxLayout(self._search_panel)
        main_layout.setContentsMargins(6, 4, 6, 4)
        main_layout.setSpacing(4)

        # ------------------ 第一行：查找条 ------------------
        search_row = QWidget()
        search_row.setStyleSheet("border: none; background: transparent;")
        search_layout = QHBoxLayout(search_row)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(6)

        # 折叠展开替换面板小按钮
        self._replace_toggle_btn = QPushButton()
        self._replace_toggle_btn.setToolTip("展开/收起替换")
        self._replace_toggle_btn.setIcon(self._create_toggle_icon(False))
        self._replace_toggle_btn.setIconSize(QSize(10, 10))
        self._replace_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._replace_toggle_btn.setFixedSize(14, 14)
        self._replace_toggle_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #f1f5f9;
            }
        """)
        search_layout.addWidget(self._replace_toggle_btn)

        search_icon = QLabel("🔍")
        search_icon.setStyleSheet("font-size: 11px; color: #94a3b8;")
        search_layout.addWidget(search_icon)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("查找...")
        self._search_input.setStyleSheet("""
            QLineEdit {
                border: none;
                background: transparent;
                font-size: 12px;
                color: #1e293b;
                padding: 1px 0px;
            }
        """)
        search_layout.addWidget(self._search_input, 1)

        self._search_status = QLabel("")
        self._search_status.setStyleSheet("""
            QLabel {
                color: #94a3b8;
                font-size: 11px;
                font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
            }
        """)
        search_layout.addWidget(self._search_status)

        self._separator_label = QLabel("|")
        self._separator_label.setStyleSheet("color: #e2e8f0; font-size: 11px;")
        search_layout.addWidget(self._separator_label)

        self._prev_btn = QPushButton()
        self._prev_btn.setToolTip("上一个 (Shift+Enter)")
        self._prev_btn.setIcon(self._create_arrow_icon("up"))
        self._prev_btn.setIconSize(QSize(11, 11))
        self._prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev_btn.setFixedSize(20, 20)
        self._prev_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #f1f5f9;
            }
        """)
        search_layout.addWidget(self._prev_btn)

        self._next_btn = QPushButton()
        self._next_btn.setToolTip("下一个 (Enter)")
        self._next_btn.setIcon(self._create_arrow_icon("down"))
        self._next_btn.setIconSize(QSize(11, 11))
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.setFixedSize(20, 20)
        self._next_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #f1f5f9;
            }
        """)
        search_layout.addWidget(self._next_btn)

        self._close_btn = QToolButton()
        self._close_btn.setIcon(self._create_close_icon())
        self._close_btn.setIconSize(QSize(10, 10))
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setFixedSize(20, 20)
        self._close_btn.setStyleSheet("""
            QToolButton {
                background: transparent;
                border: none;
                border-radius: 4px;
            }
            QToolButton:hover {
                background-color: #f1f5f9;
            }
        """)
        search_layout.addWidget(self._close_btn)
        main_layout.addWidget(search_row)

        # ------------------ 第二行：替换条 ------------------
        self._replace_row_widget = QWidget()
        self._replace_row_widget.setVisible(False)
        self._replace_row_widget.setStyleSheet("border: none; background: transparent;")
        replace_layout = QHBoxLayout(self._replace_row_widget)
        replace_layout.setContentsMargins(0, 0, 0, 0)
        replace_layout.setSpacing(6)

        spacer = QWidget()
        spacer.setFixedWidth(14)
        replace_layout.addWidget(spacer)

        replace_icon = QLabel("✍️")
        replace_icon.setStyleSheet("font-size: 11px; color: #94a3b8;")
        replace_layout.addWidget(replace_icon)

        self._replace_input = QLineEdit()
        self._replace_input.setPlaceholderText("替换为...")
        self._replace_input.setStyleSheet("""
            QLineEdit {
                border: none;
                background: transparent;
                font-size: 12px;
                color: #1e293b;
                padding: 1px 0px;
            }
        """)
        replace_layout.addWidget(self._replace_input, 1)

        self._replace_btn = _ClickableLabel("替换")
        self._replace_btn.setObjectName("ReplaceTextBtn")
        self._replace_btn.setFixedHeight(16)
        self._replace_btn.setStyleSheet("""
            QLabel#ReplaceTextBtn {
                background-color: #f1f5f9;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 0px 5px;
                font-size: 11px;
                color: #475569;
                font-weight: bold;
            }
            QLabel#ReplaceTextBtn:hover {
                background-color: #e2e8f0;
            }
        """)
        replace_layout.addWidget(self._replace_btn)

        self._replace_all_btn = _ClickableLabel("全部替换")
        self._replace_all_btn.setObjectName("ReplaceAllTextBtn")
        self._replace_all_btn.setFixedHeight(16)
        self._replace_all_btn.setStyleSheet("""
            QLabel#ReplaceAllTextBtn {
                background-color: #f1f5f9;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 0px 5px;
                font-size: 11px;
                color: #475569;
                font-weight: bold;
            }
            QLabel#ReplaceAllTextBtn:hover {
                background-color: #e2e8f0;
            }
        """)
        replace_layout.addWidget(self._replace_all_btn)
        main_layout.addWidget(self._replace_row_widget)

        # ------------------ 动作连接 ------------------
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.returnPressed.connect(self._find_next)
        self._prev_btn.clicked.connect(self._find_prev)
        self._next_btn.clicked.connect(self._find_next)
        self._close_btn.clicked.connect(self._hide_search_panel)

        self._replace_toggle_btn.clicked.connect(self._toggle_replace)
        self._replace_btn.clicked.connect(self._replace_current)
        self._replace_all_btn.clicked.connect(self._replace_all)

        # 注册 Ctrl+F 查找热键
        self._shortcut_find = QShortcut(QKeySequence("Ctrl+F"), self)
        self._shortcut_find.activated.connect(self._show_search_panel)

        # 注册 Ctrl+H 替换热键
        self._shortcut_replace = QShortcut(QKeySequence("Ctrl+H"), self)
        self._shortcut_replace.activated.connect(self._show_replace_panel)

        self._shortcut_prev = QShortcut(QKeySequence("Shift+Return"), self._search_input)
        self._shortcut_prev.activated.connect(self._find_prev)

    def _toggle_replace(self) -> None:
        visible = not self._replace_row_widget.isVisible()
        self._replace_row_widget.setVisible(visible)
        self._replace_toggle_btn.setIcon(self._create_toggle_icon(visible))
        self._update_search_panel_position()

    def _update_search_panel_position(self) -> None:
        if hasattr(self, "_search_panel") and self._search_panel:
            self._search_panel.setFixedWidth(260)
            self._search_panel.adjustSize()
            scrollbar_w = 0
            if self.verticalScrollBar() and self.verticalScrollBar().isVisible():
                scrollbar_w = self.verticalScrollBar().width()
            x = self.width() - self._search_panel.width() - scrollbar_w - 15
            y = 10
            self._search_panel.move(x, y)

    def _show_search_panel(self) -> None:
        if hasattr(self, "_search_panel"):
            self._search_panel.setVisible(True)
            self._update_search_panel_position()
            self._search_input.setFocus()
            self._search_input.selectAll()
            if self._search_input.text():
                self._on_search_text_changed(self._search_input.text())

    def _show_replace_panel(self) -> None:
        if hasattr(self, "_search_panel"):
            self._search_panel.setVisible(True)
            self._replace_row_widget.setVisible(True)
            self._replace_toggle_btn.setIcon(self._create_toggle_icon(True))
            self._update_search_panel_position()
            self._replace_input.setFocus()
            self._replace_input.selectAll()
            if self._search_input.text():
                self._on_search_text_changed(self._search_input.text())

    def _hide_search_panel(self) -> None:
        if hasattr(self, "_search_panel"):
            self._search_panel.setVisible(False)
            self.setExtraSelections([])
            self.setFocus()

    def _on_search_text_changed(self, text: str) -> None:
        self._search_cursors = []
        self._current_search_index = -1

        if not text:
            self._search_status.setText("")
            self.setExtraSelections([])
            return

        doc = self.document()
        cursor = QTextCursor(doc)

        while True:
            cursor = doc.find(text, cursor)
            if cursor.isNull():
                break
            self._search_cursors.append(QTextCursor(cursor))

        total = len(self._search_cursors)
        if total > 0:
            self._current_search_index = 0
            self._highlight_current_index()
        else:
            self._search_status.setText("0/0")
            self.setExtraSelections([])

    def _find_next(self) -> None:
        if not self._search_cursors:
            return
        self._current_search_index = (self._current_search_index + 1) % len(self._search_cursors)
        self._highlight_current_index()

    def _find_prev(self) -> None:
        if not self._search_cursors:
            return
        self._current_search_index = (self._current_search_index - 1) % len(self._search_cursors)
        self._highlight_current_index()

    def _highlight_current_index(self) -> None:
        if 0 <= self._current_search_index < len(self._search_cursors):
            cur = self._search_cursors[self._current_search_index]
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._highlight_all_matches()
            self._search_status.setText(f"{self._current_search_index + 1}/{len(self._search_cursors)}")

    def _highlight_all_matches(self) -> None:
        selections = []
        query = self._search_input.text()
        if not query or not self._search_cursors:
            self.setExtraSelections([])
            return

        current_cursor = self._search_cursors[self._current_search_index]

        for cur in self._search_cursors:
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cur
            if cur == current_cursor:
                selection.format.setBackground(QColor("#fde047"))
            else:
                selection.format.setBackground(QColor("#fef08a"))
            selections.append(selection)

        self.setExtraSelections(selections)

    def _replace_current(self) -> None:
        search_text = self._search_input.text()
        replace_text = self._replace_input.text()
        if not search_text or not self._search_cursors or self._current_search_index < 0:
            return

        cur = self._search_cursors[self._current_search_index]
        self.setTextCursor(cur)
        self.insertPlainText(replace_text)
        self._on_search_text_changed(search_text)

    def _replace_all(self) -> None:
        search_text = self._search_input.text()
        replace_text = self._replace_input.text()
        if not search_text:
            return

        self._search_cursors = []
        self._current_search_index = -1

        self.document().beginEditBlock()
        try:
            cursor = QTextCursor(self.document())
            while True:
                cursor = self.document().find(search_text, cursor)
                if cursor.isNull():
                    break
                cursor.insertText(replace_text)
        finally:
            self.document().endEditBlock()

        self._on_search_text_changed(search_text)

    @staticmethod
    def _plain_text_has_markdown(text: str) -> bool:
        import re

        for line in str(text or "").splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            if (
                line_str.startswith("#")
                or line_str.startswith("-")
                or line_str.startswith("*")
                or line_str.startswith(">")
                or "**" in line_str
                or "__" in line_str
            ):
                return True
            if re.match(r"^\d+\.\s", line_str):
                return True
        return False

    @classmethod
    def _html_has_rich_markup(cls, html_text: str) -> bool:
        lower_html = str(html_text or "").lower()
        return any(marker in lower_html for marker in cls._RICH_HTML_MARKERS)

    def setHtml(self, html: str) -> None:
        if not html:
            super().setHtml(html)
            self._apply_force_yahei()
            return

        from deepcat.ui.markdown_renderer import MarkdownRenderer
        from PyQt6.QtGui import QTextDocument

        temp_doc = QTextDocument()
        temp_doc.setHtml(html)
        plain_text = temp_doc.toPlainText()

        if self._plain_text_has_markdown(plain_text) and not self._html_has_rich_markup(html):
            rendered = MarkdownRenderer.to_html(plain_text)
            super().setHtml(rendered)
        else:
            super().setHtml(html)
        self._apply_force_yahei()

    def clear(self) -> None:
        super().clear()
        self._apply_force_yahei()

    def _char_format_at(self, pos: QPoint) -> QTextCharFormat:
        cursor = self.cursorForPosition(pos)
        char_format = cursor.charFormat()
        if char_format.isImageFormat():
            return char_format

        test_cursor = QTextCursor(cursor)
        if test_cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, 1):
            test_cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
            left_format = test_cursor.charFormat()
            if left_format.isImageFormat():
                return left_format
        return char_format

    def _has_image_at(self, pos: QPoint) -> bool:
        return self._char_format_at(pos).isImageFormat()

    def _open_anchor(self, anchor: str) -> bool:
        href = str(anchor or "").strip()
        if not href or href in ("todo_checked.png", "todo_unchecked.png"):
            return False
        try:
            if href.startswith("file://"):
                QDesktopServices.openUrl(QUrl.fromLocalFile(QUrl(href).toLocalFile()))
            elif os.path.exists(href):
                QDesktopServices.openUrl(QUrl.fromLocalFile(href))
            else:
                QDesktopServices.openUrl(QUrl.fromUserInput(href))
            return True
        except Exception:
            return False

    def _plain_note_char_format(self, base_format: QTextCharFormat) -> QTextCharFormat:
        fmt = QTextCharFormat(base_format)
        fmt.setAnchor(False)
        fmt.setAnchorHref("")
        fmt.setAnchorNames([])
        return fmt

    def _insert_plain_text_with_auto_links(self, text: str) -> bool:
        parts = _note_auto_link_parts(text)
        if not parts:
            return False

        cursor = self.textCursor()
        plain_format = self._plain_note_char_format(cursor.charFormat())
        link_base_format = QTextCharFormat(plain_format)
        link_base_format.setAnchor(True)
        link_base_format.setFontUnderline(True)
        link_base_format.setForeground(QBrush(QColor("#2563eb")))

        cursor.beginEditBlock()
        try:
            for kind, part_text, href in parts:
                if not part_text:
                    continue
                if kind == "link":
                    link_format = QTextCharFormat(link_base_format)
                    link_format.setAnchorHref(href)
                    cursor.insertText(part_text, link_format)
                else:
                    cursor.insertText(part_text, plain_format)
        finally:
            cursor.endEditBlock()

        self.setTextCursor(cursor)
        self.setCurrentCharFormat(plain_format)
        return True

    def mouseMoveEvent(self, event) -> None:
        try:
            pos = event.pos()
            if self.anchorAt(pos) or self._has_image_at(pos):
                self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.viewport().setCursor(Qt.CursorShape.IBeamCursor)

            # 仅在鼠标进入右下角水印区域时显示，移出时隐藏
            if hasattr(self, "_watermark_label") and self._watermark_label:
                rect = self._watermark_label.geometry()
                # 判定区域向左、向上扩展 20 像素，向右、向下扩展 10 像素以增加易触达性
                detect_rect = rect.adjusted(-20, -20, 10, 10)
                if detect_rect.contains(pos):
                    self._watermark_label.show()
                else:
                    self._watermark_label.hide()
        except Exception:
            pass
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        # 鼠标离开编辑器时强制隐藏水印
        if hasattr(self, "_watermark_label") and self._watermark_label:
            self._watermark_label.hide()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            try:
                anchor = self.anchorAt(event.pos())
                if anchor and anchor not in ("todo_checked.png", "todo_unchecked.png") and self._open_anchor(anchor):
                    event.accept()
                    return
            except Exception:
                pass
        super().mouseReleaseEvent(event)

    def insertFromMimeData(self, source) -> None:
        if source is None:
            super().insertFromMimeData(source)
            return

        # 1. 拦截图片数据粘贴 (如剪贴板复制的截图或网页图片)
        if source.hasImage():
            image = source.imageData()
            if image:
                try:
                    from PyQt6.QtGui import QImage
                    from deepcat.settings_store import get_image_output_dir
                    import time

                    qimg = image
                    if not isinstance(qimg, QImage):
                        if hasattr(image, "value"):
                            qimg = image.value()

                    if qimg and not qimg.isNull():
                        attach_dir = get_image_output_dir() / "notes_attachments"
                        attach_dir.mkdir(parents=True, exist_ok=True)

                        name = f"img_{time.strftime('%Y%m%d_%H%M%S', time.localtime())}_{time.time_ns() % 1000000}.png"
                        dst = attach_dir / name
                        qimg.save(str(dst), "PNG")

                        rel_path = str(dst).replace("\\", "/")
                        cursor = self.textCursor()
                        cursor.insertHtml(f'<img src="{rel_path}" style="max-width:300px;" /><br>')
                        self.setTextCursor(cursor)
                        return
                except Exception:
                    pass

        # 2. 拦截图片文件粘贴 (在资源管理器中复制图片文件直接粘贴)
        if source.hasUrls():
            from pathlib import Path
            urls = source.urls()
            inserted_any = False
            for url in urls:
                path = url.toLocalFile()
                if path:
                    ext = Path(path).suffix.lower()
                    if ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
                        try:
                            from deepcat.settings_store import get_image_output_dir
                            import shutil
                            import time

                            attach_dir = get_image_output_dir() / "notes_attachments"
                            attach_dir.mkdir(parents=True, exist_ok=True)

                            name = f"img_{time.strftime('%Y%m%d_%H%M%S', time.localtime())}_{time.time_ns() % 1000000}{ext}"
                            dst = attach_dir / name
                            shutil.copy2(path, dst)

                            if ext == ".png":
                                try:
                                    from PIL import Image
                                    with Image.open(dst) as im:
                                        im.save(dst)
                                except Exception:
                                    pass

                            rel_path = str(dst).replace("\\", "/")
                            cursor = self.textCursor()
                            cursor.insertHtml(f'<img src="{rel_path}" style="max-width:300px;" /><br>')
                            self.setTextCursor(cursor)
                            inserted_any = True
                        except Exception:
                            pass
            if inserted_any:
                return

        # 3. 拦截文本/HTML 粘贴并进行 markdown 检测与渲染
        pasted_text = ""
        has_html_flag = source.hasHtml()

        if has_html_flag:
            html_content = source.html()
            from PyQt6.QtGui import QTextDocument
            temp_doc = QTextDocument()
            temp_doc.setHtml(html_content)
            pasted_text = temp_doc.toPlainText()
        elif source.hasText():
            pasted_text = source.text()
        elif source.hasUrls():
            pasted_text = "\n".join(str(url.toString() or "") for url in source.urls()).strip()

        if pasted_text:
            if self._plain_text_has_markdown(pasted_text):
                from deepcat.ui.markdown_renderer import MarkdownRenderer
                rendered_html = MarkdownRenderer.to_html(pasted_text)
                self.textCursor().insertHtml(rendered_html)
                return

        if not has_html_flag and pasted_text:
            if self._insert_plain_text_with_auto_links(pasted_text):
                return

        super().insertFromMimeData(source)

    def contextMenuEvent(self, event):
        pos = event.pos()
        cursor = self.cursorForPosition(pos)
        char_format = cursor.charFormat()
        table = cursor.currentTable()
        main_win = self._main_win

        if char_format.isImageFormat():
            image_format = char_format.toImageFormat()
            src = image_format.name()

            def do_copy_image(local_path=src):
                import os
                from PyQt6.QtCore import QUrl, QMimeData
                from PyQt6.QtGui import QImage
                if src.startswith("file://"):
                    local_path = QUrl(src).toLocalFile()
                if os.path.exists(local_path):
                    clipboard = QApplication.clipboard()
                    mime_data = QMimeData()
                    mime_data.setImageData(QImage(local_path))
                    clipboard.setMimeData(mime_data)

            def do_save_image(local_path=src):
                import os
                import shutil
                from PyQt6.QtCore import QUrl
                if src.startswith("file://"):
                    local_path = QUrl(src).toLocalFile()
                if os.path.exists(local_path):
                    file_path, _ = QFileDialog.getSaveFileName(
                        self, "另存图片", "",
                        "Images (*.png *.jpg *.jpeg *.bmp *.gif)"
                    )
                    if file_path:
                        try:
                            shutil.copy(local_path, file_path)
                        except Exception as e:
                            QMessageBox.warning(self, "错误", f"保存图片失败: {e}")

            def do_delete_image():
                img_cursor = self.cursorForPosition(pos)
                from PyQt6.QtGui import QTextCursor
                img_cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
                img_cursor.removeSelectedText()

            items = [
                ("📸 已选中笔记本内的图片", None, False),
                ("-", None, False),
                ("🖼️ 复制图片 (Copy Image)", do_copy_image, True),
                ("💾 另存图片为 (Save Image As)...", do_save_image, True),
                ("🗑️ 删除此图片 (Delete Image)", do_delete_image, True),
            ]

        elif table is not None:
            cell = table.cellAt(cursor)

            def do_delete_table(t=table):
                from PyQt6.QtGui import QTextCursor
                table_cursor = self.textCursor()
                start_pos = t.firstPosition() - 1
                end_pos = t.lastPosition() + 1
                table_cursor.setPosition(start_pos)
                table_cursor.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
                table_cursor.removeSelectedText()

            items = [
                ("📊 已选中笔记本内的表格", None, False),
                ("-", None, False),
                ("➕ 在上方插入行", lambda: table.insertRows(cell.row(), 1), True),
                ("➕ 在下方插入行", lambda: table.insertRows(cell.row() + 1, 1), True),
                ("➖ 删除当前行", lambda: table.removeRows(cell.row(), 1), True),
                ("-", None, False),
                ("➕ 在左侧插入列", lambda: table.insertColumns(cell.column(), 1), True),
                ("➕ 在右侧插入列", lambda: table.insertColumns(cell.column() + 1, 1), True),
                ("➖ 删除当前列", lambda: table.removeColumns(cell.column(), 1), True),
                ("-", None, False),
                ("🔗 合并选中的单元格", lambda: table.mergeCells(self.textCursor()), True),
                ("🔓 拆分当前单元格", lambda: table.splitCell(cell.row(), cell.column(), 1, 1), True),
                ("-", None, False),
                ("🗑️ 删除整个表格 (Delete Table)", do_delete_table, True),
            ]

        else:
            def do_insert_img():
                file_path, _ = QFileDialog.getOpenFileName(
                    self, "选择要插入的图片", "",
                    "Images (*.png *.jpg *.jpeg *.bmp *.gif)"
                )
                if file_path:
                    img_cursor = self.textCursor()
                    img_cursor.insertImage(file_path)

            def do_insert_tab():
                from PyQt6.QtWidgets import QInputDialog
                rows, ok1 = QInputDialog.getInt(self, "插入表格", "请输入表格行数 (1-50):", 3, 1, 50)
                if ok1:
                    cols, ok2 = QInputDialog.getInt(self, "插入表格", "请输入表格列数 (1-20):", 3, 1, 20)
                    if ok2:
                        self.textCursor().insertTable(rows, cols)

            items = [
                ("↶ 撤销 (Undo)", self.undo, self.document().isUndoAvailable()),
                ("↷ 重做 (Redo)", self.redo, self.document().isRedoAvailable()),
                ("-", None, False),
                ("✂️ 剪切 (Cut)", self.cut, self.textCursor().hasSelection()),
                ("📋 复制 (Copy)", self.copy, self.textCursor().hasSelection()),
                ("📥 粘贴 (Paste)", self.paste, True),
                ("-", None, False),
                ("☑️ 插入待办...", self._insert_note_todo, True),
                ("🖼️ 插入本地图片...", do_insert_img, True),
                ("📊 插入新表格 (M×N)...", do_insert_tab, True),
            ]

            if main_win:
                items.extend([
                    ("-", None, False),
                    (
                        f"🧭 {main_win._note_outline_menu_label()}",
                        main_win._toggle_note_outline_visibility,
                        True,
                    ),
                    ("📌 置顶记事本", lambda: main_win._pin_tab_content("note", main_win._active_note_tab), True)
                ])

        from deepcat.ui.post_capture_actions import OcrGenericMenuPopup
        popup = OcrGenericMenuPopup(items, parent=self)
        popup.show_at_pos(event.globalPos())


from deepcat.ui.scroll_result import ScrollResultPrepareWorker


# --- Automatically Appended Mixin Imports and Class ---

import json
import hashlib
import os
import re
import shutil
import sys
import threading
import time
import ctypes
import html
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote, urlparse
from PyQt6.QtCore import (
    Qt,
    QThread,
    QTimer,
    QRect,
    QSize,
    QByteArray,
    QEvent,
    QEventLoop,
    QPoint,
    QDate,
    QDateTime,
    QTime,
    pyqtSignal,
    QUrl,
    QObject,
    QFileInfo,
)
from PyQt6.QtGui import (
    QAction,
    QGuiApplication,
    QIcon,
    QColor,
    QCursor,
    QPainter,
    QPen,
    QImage,
    QKeySequence,
    QPixmap,
    QDesktopServices,
    QTextCharFormat,
    QTextBlockFormat,
    QTextCursor,
    QTextListFormat,
    QTextDocument,
    QFont,
    QBrush,
    QPalette,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QAbstractItemView,
    QButtonGroup,
    QCalendarWidget,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileIconProvider,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QMenu,
    QStyle,
    QFrame,
    QCheckBox,
    QLineEdit,
)
from deepcat import __version__
from deepcat.config import Config
from deepcat.input.hotkey_format import pynput_to_qt, qt_to_pynput
from deepcat.input.hotkey_listener import GlobalStartHotkey
from deepcat.settings_store import (
    AppSettings,
    DEFAULT_CAT_REMINDER,
    DEFAULT_EXPLAIN_PROMPT,
    DEFAULT_EXPLAIN_PROMPT_BUTTON_NAME,
    DEFAULT_AI_SEARCH_PROMPT,
    DEFAULT_AI_SEARCH_PROMPT_BUTTON_NAME,
    DEFAULT_REPLY_PROMPT,
    DEFAULT_REPLY_PROMPT_BUTTON_NAME,
    DEFAULT_SUMMARY_PROMPT,
    DEFAULT_SUMMARY_PROMPT_BUTTON_NAME,
    COPY_ON_CAPTURE_MODE_COPY_CLOSE,
    COPY_ON_CAPTURE_MODE_COPY_KEEP,
    COPY_ON_CAPTURE_MODE_OFF,
    DEFAULT_COPY_ON_CAPTURE_MODE,
    decode_qbytearray,
    encode_qbytearray,
    coerce_auto_backup_interval_minutes,
    get_image_output_dir,
    get_pdf_output_dir,
    infer_translator_model_type,
    load_settings,
    normalize_cat_reminder_settings,
    normalize_later_read_settings,
    normalize_feature_visibility,
    normalize_copy_on_capture_mode,
    normalize_todo_items,
    normalize_annotation_style,
    normalize_log_settings,
    normalize_output_dir,
    normalize_translator_settings,
    normalize_updater_settings,
    update_settings,
    update_settings_fields,
    update_ui_settings,
    validate_settings_output_dirs_async,
    validate_output_dir,
)
from deepcat.ui.app_icon import create_app_icon
from deepcat.ui.cat_reminder import CatReminderSession
from deepcat.ui.countdown_overlay import CountdownOverlay
from deepcat.ui.floating_bar import FloatingBar
from deepcat.ui.network_probe import NetworkProbeMonitor
from deepcat.ui.notifications import CaptureNotificationState
from deepcat.ui.popup_behavior import POPUP_EXACT_WIDTH_PROPERTY
from deepcat.ui.settings_dialog import (
    ProviderSwitchHintDelegate,
    SettingsDialog,
    TranslatorConnectionTestWorker,
    ProxyConnectionTestWorker,
    UpdateCheckWorker,
    UpdateDownloadWorker,
)
from deepcat.ui.selection_border_overlay import SelectionBorderOverlay, SelectionShadeOverlay
from deepcat.ui.capture_worker import CaptureWorker
from deepcat.ui.region_overlay import RegionOverlay, SelectedRegion, logical_rect_to_physical_tuple
from deepcat.ui.post_capture_actions import PostCaptureActions, ModernPopupComboBox
from deepcat.ui.tab_list_popup import GroupedNoteListPopup, RoundedListPopup
from deepcat.ui.task_feedback import TaskFeedback
from deepcat.table_notes_store import TableNotesStore, default_ima_config
from deepcat.translation_history_store import TranslationHistoryStore
from deepcat.todo_store import TodoStore
from deepcat.later_read_store import LaterReadStore
from deepcat.drive_cleaner import now_iso
from deepcat.utils.autostart import is_autostart_enabled, set_autostart
from deepcat.utils.crash_reporter import write_crash_breadcrumb
from deepcat.utils.logger import get_log_dir, get_log_file_path, get_logger
from deepcat.utils.paths import get_app_dir

from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND, MAIN_WINDOW_BACKGROUND_COLORREF, MODULE_BACKGROUND, _RESOURCE_SHORTCUT_MAX_ITEMS
from deepcat.ui.main_window.helpers import (
    _date_key,
    _normalize_resource_shortcut_kind,
    _normalize_resource_shortcut_target,
    _normalize_resource_shortcuts,
    _resource_shortcut_default_title,
)
from deepcat.ui.main_window.misc_widgets import (
    RoundedTextEditContainer,
    _DoubleClickLabel,
    _DraggableTile,
    _ModernComboStyle,
    _MouseClickOnlyComboBox,
    _SidebarHoverFilter,
    _SidebarNavButton,
    _SwitchCheckBox,
)
from deepcat.ui.main_window.todo import _TodoCalendarWidget, _TodoEditDialog, _TodoListItemWidget, _TodoReminderPopup, _style_todo_combo_popup_view
from deepcat.ui.main_window.later_read import _LaterReadEditDialog, _LaterReadItemWidget, _LaterReadPinnedFoldToggleWidget
from deepcat.ui.main_window.notifications import NotificationPopup, _CatRestReminderPopup
from deepcat.ui.main_window.tab_password import _TabPasswordSetDialog, _TabPasswordVerifyDialog
from deepcat.ui.main_window.ima import _ImaSettingsDialog, _ImaSyncWorker
from deepcat.ui.main_window.notes import ImagePreviewDialog, ScrollResultPrepareWorker, _NotesEditor, _NotesTable, _ReturnDownDelegate
from deepcat.ui.main_window.drive_cleaner import _DriveCleanerWindow
from deepcat.ui.main_window.resource_shortcuts import _DeleteShortcutPopup, _ResourceShortcutDialog, _TodoResourceWindow
from deepcat.ui.main_window.stashed_captures import _StashedCapturesDialog
from deepcat.ui.main_window.compact import _CompactListWindow, _GroupManageDialog



class _NoteOutlineWindowEventFilter(QObject):
    """在主窗口状态稳定后同步记事本目录栏的可见性。"""

    def __init__(self, owner: QWidget) -> None:
        super().__init__(owner)
        self._owner = owner

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        owner = self._owner
        if owner is None:
            return False
        if event.type() == QEvent.Type.WindowStateChange:
            callback = getattr(owner, "_handle_note_window_state_change", None)
            if callback is None:
                callback = getattr(owner, "_sync_note_outline_visibility", None)
            if callback is not None:
                QTimer.singleShot(0, callback)
        elif event.type() == QEvent.Type.Show and hasattr(owner, "_sync_note_outline_visibility"):
            QTimer.singleShot(0, owner._sync_note_outline_visibility)
        return False


class NotesMixin:
    _NOTE_OUTLINE_PAGE_INDEX = 5
    _NOTE_OUTLINE_REFRESH_DELAY_MS = 160
    _NOTE_OUTLINE_POSITION_ROLE = int(Qt.ItemDataRole.UserRole)

    @staticmethod
    def _font_weight_value(weight: Any) -> int:
        try:
            value = weight.value if hasattr(weight, "value") else weight
            return int(value)
        except (TypeError, ValueError):
            return 400

    @classmethod
    def _note_block_heading_level(cls, block: Any) -> int:
        """返回文本块的标题层级，并兼容旧版仅靠字号与字重保存的标题。"""
        if block is None or not block.isValid() or not str(block.text() or "").strip():
            return 0

        try:
            semantic_level = int(block.blockFormat().headingLevel())
        except (AttributeError, TypeError, ValueError):
            semantic_level = 0
        if 1 <= semantic_level <= 6:
            return semantic_level

        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        char_format = cursor.charFormat()
        point_size = float(char_format.fontPointSize())
        weight = cls._font_weight_value(char_format.fontWeight())
        if 19.5 <= point_size <= 20.5 and weight >= 750:
            return 1
        if 15.5 <= point_size <= 16.5 and weight >= 700:
            return 2
        return 0

    @classmethod
    def _extract_note_outline(cls, document: QTextDocument) -> list[tuple[int, str, int]]:
        """从富文本文档提取“层级、标题、块位置”目录数据。"""
        headings: list[tuple[int, str, int]] = []
        block = document.begin()
        while block.isValid():
            level = cls._note_block_heading_level(block)
            text = " ".join(str(block.text() or "").split())
            if level and text:
                headings.append((level, text, int(block.position())))
            block = block.next()
        return headings

    def _schedule_note_outline_refresh(self) -> None:
        self._note_outline_dirty = True
        timer = getattr(self, "_note_outline_refresh_timer", None)
        if timer is None:
            return
        panel = getattr(self, "_note_outline_panel", None)
        if panel is None or not panel.isVisible() or not self._should_show_note_outline():
            timer.stop()
            return
        timer.start(self._NOTE_OUTLINE_REFRESH_DELAY_MS)

    def _refresh_note_outline(self) -> None:
        tree = getattr(self, "_note_outline_tree", None)
        editor = getattr(self, "_notes_editor", None)
        if tree is None or editor is None:
            return
        if not self._should_show_note_outline():
            self._note_outline_dirty = True
            return

        headings = self._extract_note_outline(editor.document())
        self._note_outline_heading_positions = [position for _level, _text, position in headings]
        self._note_outline_items_by_position: dict[int, QTreeWidgetItem] = {}
        tree.clear()

        if not headings:
            empty_item = QTreeWidgetItem(["当前记事本暂无标题"])
            empty_item.setFlags(empty_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            empty_item.setForeground(0, QBrush(QColor("#94a3b8")))
            tree.addTopLevelItem(empty_item)
            self._note_outline_dirty = False
            return

        parent_stack: list[tuple[int, QTreeWidgetItem]] = []
        for level, text, position in headings:
            while parent_stack and parent_stack[-1][0] >= level:
                parent_stack.pop()
            parent = parent_stack[-1][1] if parent_stack else tree.invisibleRootItem()
            item = QTreeWidgetItem(parent, [text])
            item.setData(0, self._NOTE_OUTLINE_POSITION_ROLE, position)
            item.setToolTip(0, text)
            self._note_outline_items_by_position[position] = item
            font = item.font(0)
            font.setBold(level <= 2)
            item.setFont(0, font)
            parent_stack.append((level, item))

        tree.expandAll()
        self._note_outline_dirty = False
        self._sync_note_outline_current_item()

    def _jump_to_note_outline_item(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        position = item.data(0, self._NOTE_OUTLINE_POSITION_ROLE)
        editor = getattr(self, "_notes_editor", None)
        if editor is None or position is None:
            return
        block = editor.document().findBlock(int(position))
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()
        editor.setFocus()

    def _sync_note_outline_current_item(self) -> None:
        tree = getattr(self, "_note_outline_tree", None)
        editor = getattr(self, "_notes_editor", None)
        positions = list(getattr(self, "_note_outline_heading_positions", []) or [])
        if tree is None or editor is None or not positions:
            return

        cursor_position = int(editor.textCursor().position())
        active_position: Optional[int] = None
        for position in positions:
            if position > cursor_position:
                break
            active_position = position
        if active_position is None:
            tree.setCurrentItem(None)
            return

        item = getattr(self, "_note_outline_items_by_position", {}).get(active_position)
        if item is not None and tree.currentItem() is not item:
            tree.setCurrentItem(item)
            tree.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)

    def _should_show_note_outline(self) -> bool:
        try:
            on_notes_page = self._stack is not None and self._stack.currentIndex() == self._NOTE_OUTLINE_PAGE_INDEX
            in_note_mode = self._table_notes_stack.currentIndex() == 1
            if not on_notes_page or not in_note_mode:
                return False

            manual_visibility = getattr(self, "_note_outline_manual_visibility", None)
            if manual_visibility is not None:
                return bool(manual_visibility)

            return bool(self.windowState() & Qt.WindowState.WindowMaximized) or bool(self.isMaximized())
        except (AttributeError, RuntimeError):
            return False

    def _note_outline_menu_label(self) -> str:
        return "隐藏目录" if NotesMixin._should_show_note_outline(self) else "显示目录"

    def _toggle_note_outline_visibility(self) -> None:
        self._note_outline_manual_visibility = not NotesMixin._should_show_note_outline(self)
        NotesMixin._sync_note_outline_visibility(self)

    def _ensure_active_note_tab_visible(self) -> None:
        try:
            on_notes_page = self._stack is not None and self._stack.currentIndex() == self._NOTE_OUTLINE_PAGE_INDEX
            in_note_mode = self._table_notes_stack.currentIndex() == 1
            if on_notes_page and in_note_mode:
                self._scroll_active_tab_into_view("note")
        except (AttributeError, RuntimeError):
            return

    def _ensure_active_table_notes_tab_visible(self) -> None:
        try:
            if self._stack is None or self._stack.currentIndex() != self._NOTE_OUTLINE_PAGE_INDEX:
                return
            tab_type = "note" if self._table_notes_stack.currentIndex() == 1 else "table"
            self._scroll_active_tab_into_view(tab_type)
        except (AttributeError, RuntimeError):
            return

    def _schedule_active_table_notes_tab_visibility(self) -> None:
        QTimer.singleShot(0, self._ensure_active_table_notes_tab_visible)
        QTimer.singleShot(160, self._ensure_active_table_notes_tab_visible)

    def _handle_note_window_state_change(self) -> None:
        """窗口最大化或还原后恢复自动目录规则，并校正活动标签位置。"""
        self._note_outline_manual_visibility = None
        NotesMixin._sync_note_outline_visibility(self)
        NotesMixin._ensure_active_note_tab_visible(self)
        timer = getattr(self, "_note_window_layout_timer", None)
        if timer is not None:
            timer.start(160)

    def _sync_note_outline_visibility(self, *_args: Any) -> None:
        panel = getattr(self, "_note_outline_panel", None)
        if panel is None:
            return
        should_show = self._should_show_note_outline()
        if panel.isVisible() != should_show:
            panel.setVisible(should_show)
        if not should_show:
            timer = getattr(self, "_note_outline_refresh_timer", None)
            if timer is not None:
                timer.stop()
        if should_show and bool(getattr(self, "_note_outline_dirty", True)):
            self._refresh_note_outline()

    @staticmethod
    def _is_default_note_tab_name(name: str) -> bool:
        """判断笔记本名称是否是自动生成的默认名称。"""
        import re
        stripped = str(name or "").strip()
        return bool(re.fullmatch(r"[记笔]事本\d+", stripped))

    def _should_sync_note_tab_to_ima(self, tab: dict[str, Any]) -> bool:
        """判断笔记本标签页是否应该同步到 IMA。
        仅当用户手动重命名过（非默认自动生成名称）时才同步。
        """
        if not isinstance(tab, dict):
            return False
        if bool(tab.get("_user_renamed", False)):
            return True
        name = str(tab.get("name", "") or "")
        return not self._is_default_note_tab_name(name) and bool(name.strip())

    @staticmethod
    def _extract_note_title_from_html(html_text: str, fallback: str = "") -> str:
        """从笔记 HTML 内容中提取第一行非空文字作为标题。"""
        try:
            from PyQt6.QtGui import QTextDocument
            doc = QTextDocument()
            doc.setHtml(str(html_text or ""))
            plain = doc.toPlainText()
        except Exception:
            import re
            import html as html_lib
            text = re.sub(r"<br\s*/?>", "\n", str(html_text or ""), flags=re.IGNORECASE)
            text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", "", text)
            plain = html_lib.unescape(text)
        for line in str(plain or "").splitlines():
            line_str = line.strip()
            if line_str:
                return line_str[:50]
        return str(fallback or "").strip() or "未命名笔记"

    def _update_note_title_from_content(self) -> None:
        index = getattr(self, "_active_note_tab", -1)
        if index < 0 or index >= len(self._note_tabs):
            return
        tab = self._note_tabs[index]
        if tab.get("name") == "随手记":
            return
        if tab.get("_user_renamed"):
            return
        editor = getattr(self, "_notes_editor", None)
        if not isinstance(editor, QTextEdit):
            return
        text = str(editor.toPlainText() or "")
        first_line = ""
        for line in text.splitlines():
            line_str = line.strip()
            if line_str:
                first_line = line_str
                break
        first_line = first_line[:30].strip()
        if not first_line:
            return
        old_name = tab.get("name", "")
        if old_name != first_line:
            tab["name"] = first_line
            self._refresh_tab_bars()

    def _open_table_notes_from_tray(self) -> None:
        if self._is_tray_menu_click_throttled():
            return
        if not self._feature_enabled("table_notes"):
            return
        self._switch_page(5)
        self._show_from_tray()
        try:
            self.activateWindow()
        except Exception:
            pass

    def _show_table_notes_status(self, text: str, *, tone: str = "info", auto_hide_ms: int = 3600) -> None:
        self._show_inline_status(getattr(self, "_table_notes_status_label", None), text, tone=tone, auto_hide_ms=auto_hide_ms)

    def _make_table_notes_switcher(self) -> QWidget:
        self._table_notes_switch_btn = QPushButton("切换记事本")
        self._table_notes_switch_btn.setObjectName("BtnPrimary")
        self._table_notes_switch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._table_notes_switch_btn.setMinimumSize(132, 42)
        self._table_notes_switch_btn.setMaximumSize(132, 42)
        self._table_notes_switch_btn.setIcon(self._asset_icon("icon_switch_white.svg"))
        self._table_notes_switch_btn.setIconSize(QSize(20, 20))
        self._table_notes_switch_btn.clicked.connect(self._on_table_notes_switch_clicked)
        return self._table_notes_switch_btn

    def _on_table_notes_switch_clicked(self) -> None:
        current = self._table_notes_stack.currentIndex()
        self._switch_table_notes_mode("note" if current == 0 else "table")

    def _build_table_notes_page(self) -> QWidget:
        page = self._make_page("表格记事", action_widget=self._make_table_notes_switcher())
        content_layout: QVBoxLayout = page._content_layout  # type: ignore[attr-defined]
        self._table_notes_status_label = self._make_inline_status_label()
        content_layout.addWidget(self._table_notes_status_label)

        assets_dir = Path(__file__).resolve().parent.parent / "assets"

        # 初始化表格默认文字与背景颜色，呼应图标底下一横
        self._last_table_text_color = QColor("#dc2626")
        self._last_table_bg_color = QColor("#fef08a")

        def _icon(name: str) -> QIcon:
            p = assets_dir / name
            return QIcon(str(p)) if p.exists() else QIcon()

        def make_color_btn(icon_name: str, quick_slot: Callable[[], None], pick_slot: Callable[[], None], tooltip: str) -> QWidget:
            w = QWidget()
            w.setCursor(Qt.CursorShape.PointingHandCursor)
            layout = QHBoxLayout(w)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            main_btn = QToolButton()
            main_btn.setIcon(_icon(icon_name))
            main_btn.setIconSize(QSize(13, 13)) # 图标大小微调为 13x13，彻底消除突兀感
            main_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            main_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            main_btn.setToolTip(tooltip)
            main_btn.setFixedSize(22, 26)
            main_btn.setStyleSheet("QToolButton { border: none; background: transparent; border-radius: 5px 0 0 5px; } QToolButton:hover { background: rgba(15, 23, 42, 0.045); }")
            main_btn.clicked.connect(lambda *_: quick_slot())
            main_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            arrow_btn = QToolButton()
            arrow_btn.setText("▼")
            arrow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            arrow_btn.setToolTip(f"选择{tooltip}")
            arrow_btn.setFixedSize(8, 26)
            arrow_btn.setStyleSheet("QToolButton { border: none; background: transparent; border-radius: 0 5px 5px 0; font-size: 5px; color: #6b7280; padding: 0; } QToolButton:hover { background: rgba(15, 23, 42, 0.045); }")
            arrow_btn.clicked.connect(lambda *_: pick_slot())
            arrow_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            layout.addWidget(main_btn)
            layout.addWidget(arrow_btn)
            w.main_btn = main_btn
            w.icon_name = icon_name
            return w
        self._table_notes_stack = QStackedWidget()
        self._table_notes_save_timer = QTimer(self)
        self._table_notes_save_timer.setSingleShot(True)
        self._table_notes_save_timer.timeout.connect(self._save_table_notes_settings)
        self._ima_auto_sync_pending_note_index: Optional[int] = None
        self._ima_auto_sync_timer = QTimer(self)
        self._ima_auto_sync_timer.setSingleShot(True)
        self._ima_auto_sync_timer.timeout.connect(self._run_pending_ima_auto_sync)

        table_page = QWidget()
        table_layout = QVBoxLayout(table_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)
        self._table_tab_bar = self._make_tab_bar("table")
        table_layout.addWidget(self._table_tab_bar)
        main_win = self

        # 2. 构建专门的表格编辑工具栏 TableToolbar (与 NoteToolbar 风格和高度完全一致)
        table_toolbar = QWidget()
        table_toolbar.setObjectName("TableToolbar")
        table_toolbar_layout = QHBoxLayout(table_toolbar)
        table_toolbar_layout.setContentsMargins(6, 6, 6, 6)
        table_toolbar_layout.setSpacing(0)

        assets_dir = Path(__file__).resolve().parent.parent / "assets"
        def _table_icon(name: str) -> QIcon:
            p = assets_dir / name
            return QIcon(str(p)) if p.exists() else QIcon()

        def add_table_tool(slot: Callable[[], None], icon_name: str, tooltip: str) -> QToolButton:
            btn = QToolButton()
            btn.setIcon(_table_icon(icon_name))
            btn.setIconSize(QSize(13, 13))  # 纤细高清 13x13 尺寸，完美匹配 NoteToolbar
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if tooltip:
                btn.setToolTip(tooltip)
            btn.clicked.connect(lambda *_: slot())
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            table_toolbar_layout.addWidget(btn)
            return btn

        # 添加表格编辑动作按钮
        add_table_tool(lambda: self._table_copy_cells(self._notes_table), "icon_action_copy.svg", "复制选中单元格 (Ctrl+C)")
        add_table_tool(lambda: self._table_paste_cells(self._notes_table), "icon_action_paste.svg", "粘贴文本到此处 (Ctrl+V)")
        add_table_tool(lambda: self._table_clear_cells_content(self._notes_table), "icon_action_eraser.svg", "清除选中单元格内容")

        # 1px 实色灰色垂直分割线 (完美复刻 NoteToolbar 视觉)
        sep1 = QFrame()
        sep1.setFixedWidth(1)
        sep1.setFixedHeight(12)
        sep1.setStyleSheet("background-color: #cbd5e1; margin: 0 4px;")
        table_toolbar_layout.addWidget(sep1)

        add_table_tool(lambda: self._table_insert_row_above(self._notes_table), "icon_table_row_above.svg", "在上方插入一行")
        add_table_tool(lambda: self._table_insert_row_below(self._notes_table), "icon_table_row_below.svg", "在下方插入一行")
        add_table_tool(lambda: self._table_insert_col_left(self._notes_table), "icon_table_col_left.svg", "在左侧插入一列")
        add_table_tool(lambda: self._table_insert_col_right(self._notes_table), "icon_table_col_right.svg", "在右侧插入一列")

        sep2 = QFrame()
        sep2.setFixedWidth(1)
        sep2.setFixedHeight(12)
        sep2.setStyleSheet("background-color: #cbd5e1; margin: 0 4px;")
        table_toolbar_layout.addWidget(sep2)

        add_table_tool(lambda: self._table_delete_row(self._notes_table), "icon_table_row_delete.svg", "删除选中行")
        add_table_tool(lambda: self._table_delete_col(self._notes_table), "icon_table_col_delete.svg", "删除选中列")
        add_table_tool(lambda: self._table_resize(self._notes_table), "icon_table_resize.svg", "调整表格大小 (修改总行数/列数)...")
        add_table_tool(lambda: self._table_clear_all(self._notes_table), "icon_table_clear_all.svg", "清空整张表格数据")

        # 1px 实色灰色垂直分割线 (完美复刻 NoteToolbar 视觉)
        sep3 = QFrame()
        sep3.setFixedWidth(1)
        sep3.setFixedHeight(12)
        sep3.setStyleSheet("background-color: #cbd5e1; margin: 0 4px;")
        table_toolbar_layout.addWidget(sep3)

        # 加粗、倾斜、下划线、删除线
        add_table_tool(lambda: self._table_toggle_bold(self._notes_table), "icon_edit_bold.svg", "加粗")
        add_table_tool(lambda: self._table_toggle_italic(self._notes_table), "icon_edit_italic.svg", "斜体")
        add_table_tool(lambda: self._table_toggle_underline(self._notes_table), "icon_edit_underline.svg", "下划线")
        add_table_tool(lambda: self._table_toggle_strike(self._notes_table), "icon_edit_strike.svg", "删除线")

        # 1px 实色灰色垂直分割线 (完美复刻 NoteToolbar 视觉)
        sep4 = QFrame()
        sep4.setFixedWidth(1)
        sep4.setFixedHeight(12)
        sep4.setStyleSheet("background-color: #cbd5e1; margin: 0 4px;")
        table_toolbar_layout.addWidget(sep4)

        # 文字颜色与背景颜色复合按钮
        self._table_text_color_widget = make_color_btn("icon_text_color.svg", lambda: self._apply_last_table_text_color(self._notes_table), lambda: self._choose_table_text_color(self._notes_table), "文字颜色")
        self._table_bg_color_widget = make_color_btn("icon_bg_color.svg", lambda: self._apply_last_table_bg_color(self._notes_table), lambda: self._choose_table_bg_color(self._notes_table), "背景颜色")
        table_toolbar_layout.addWidget(self._table_text_color_widget)
        table_toolbar_layout.addWidget(self._table_bg_color_widget)

        table_toolbar_layout.addStretch(1)
        table_layout.addWidget(table_toolbar)

        self._notes_table = _NotesTable(self._DEFAULT_TABLE_ROWS, self._DEFAULT_TABLE_COLUMNS, self)
        self._notes_table.setObjectName("NotesTable")
        self._notes_table.setAlternatingRowColors(True)
        self._notes_table.setHorizontalHeaderLabels([f"列 {i}" for i in range(1, self._DEFAULT_TABLE_COLUMNS + 1)])
        self._notes_table.verticalHeader().setVisible(True)
        self._notes_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._notes_table.verticalHeader().setDefaultSectionSize(28)
        for i in range(self._DEFAULT_TABLE_COLUMNS):
            self._notes_table.setColumnWidth(i, self._DEFAULT_TABLE_COLUMN_WIDTH)
        self._notes_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._notes_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked |
            QAbstractItemView.EditTrigger.EditKeyPressed |
            QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self._notes_table.itemChanged.connect(lambda *_: self._on_notes_table_changed())
        self._notes_table.horizontalHeader().sectionResized.connect(self._on_notes_table_section_resized)

        self._notes_table.setItemDelegate(_ReturnDownDelegate(self._notes_table))
        self._notes_sum_table = QTableWidget(1, self._DEFAULT_TABLE_COLUMNS)
        self._notes_sum_table.setObjectName("NotesSumTable")
        self._notes_sum_table.setHorizontalHeaderLabels([f"列 {i}" for i in range(1, self._DEFAULT_TABLE_COLUMNS + 1)])
        self._notes_sum_table.horizontalHeader().setVisible(False)
        self._notes_sum_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i in range(self._DEFAULT_TABLE_COLUMNS):
            self._notes_sum_table.setColumnWidth(i, self._DEFAULT_TABLE_COLUMN_WIDTH)
        self._notes_sum_table.verticalHeader().setVisible(False)
        self._notes_sum_table.setFixedHeight(34)
        self._notes_sum_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._notes_sum_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table_layout.addWidget(self._notes_table, 1)
        table_layout.addWidget(self._notes_sum_table)

        note_page = QWidget()
        note_layout = QVBoxLayout(note_page)
        note_layout.setContentsMargins(0, 0, 0, 0)
        note_layout.setSpacing(0)
        self._note_tab_bar = self._make_tab_bar("note")
        note_layout.addWidget(self._note_tab_bar)
        toolbar = QWidget()
        toolbar.setObjectName("NoteToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(6, 6, 6, 6)
        toolbar_layout.setSpacing(0)

        def add_tool(text: str, slot: Callable[[], None], checkable: bool = False, icon: QIcon | None = None, tooltip: str = "") -> QToolButton:
            btn = QToolButton()
            if icon is not None:
                btn.setIcon(icon)
                btn.setIconSize(QSize(13, 13)) # 图标大小微调为 13x13，极致纤细精致
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            else:
                btn.setText(text)
            btn.setCheckable(bool(checkable))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if tooltip:
                btn.setToolTip(tooltip)
            btn.clicked.connect(lambda *_: slot())
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            toolbar_layout.addWidget(btn)
            return btn

        add_tool("", lambda: self._notes_editor.undo(), icon=_icon("icon_action_undo.svg"), tooltip="撤销")
        add_tool("", lambda: self._notes_editor.redo(), icon=_icon("icon_action_redo.svg"), tooltip="重做")
        self._note_bold_btn = add_tool("", self._toggle_note_bold, True, icon=_icon("icon_edit_bold.svg"), tooltip="加粗")
        self._note_italic_btn = add_tool("", self._toggle_note_italic, True, icon=_icon("icon_edit_italic.svg"), tooltip="斜体")
        self._note_underline_btn = add_tool("", self._toggle_note_underline, True, icon=_icon("icon_edit_underline.svg"), tooltip="下划线")
        self._note_strike_btn = add_tool("", self._toggle_note_strike, True, icon=_icon("icon_edit_strike.svg"), tooltip="删除线")
        add_tool("", self._clear_note_format, icon=_icon("icon_action_eraser.svg"), tooltip="清除格式")
        # 插入下拉菜单
        insert_btn = QToolButton()
        insert_btn.setText("插入")
        insert_btn.setIcon(_icon("icon_insert.svg"))
        insert_btn.setIconSize(QSize(14, 14))
        insert_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        insert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        insert_btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")

        def show_insert_popup():
            items = [
                ("待办", lambda: self._notes_editor._insert_note_todo(), True),
                ("表格", lambda: self._insert_note_table(), True),
                ("链接", lambda: self._insert_note_link(), True),
                ("图片", lambda: self._insert_note_image(), True),
                ("分割线", lambda: self._insert_note_hr(), True),
                ("附件", lambda: self._insert_note_attachment(), True),
                ("代码块", lambda: self._insert_note_code_block(), True),
                ("引用", lambda: self._insert_note_blockquote(), True),
            ]
            from deepcat.ui.post_capture_actions import OcrGenericMenuPopup
            popup = OcrGenericMenuPopup(items, parent=insert_btn)
            global_pos = insert_btn.mapToGlobal(QPoint(0, insert_btn.height() + 2))
            popup.show_at_pos(global_pos)

        insert_btn.clicked.connect(show_insert_popup)
        toolbar_layout.addWidget(insert_btn)
        self._note_text_color_widget = make_color_btn("icon_text_color.svg", self._apply_last_note_text_color, self._choose_note_text_color, "文字颜色")
        self._note_bg_color_widget = make_color_btn("icon_bg_color.svg", self._apply_last_note_bg_color, self._choose_note_bg_color, "背景颜色")
        toolbar_layout.addWidget(self._note_text_color_widget)
        toolbar_layout.addWidget(self._note_bg_color_widget)
        self._note_block_combo = ModernPopupComboBox()
        self._note_block_combo.setProperty("matchPopupWidthToParent", True)
        self._note_block_combo.setProperty("activeIndicator", "background")
        self._note_block_combo.addItems(["正文", "标题 1", "标题 2", "引用", "5号", "4号", "3号", "2号"])
        self._note_block_combo.setCurrentText("5号")  # 默认选中 5 号字
        self._note_block_combo.currentTextChanged.connect(self._apply_note_block_style)
        toolbar_layout.addWidget(self._note_block_combo)
        add_tool("", lambda: self._notes_editor.setAlignment(Qt.AlignmentFlag.AlignLeft), icon=_icon("icon_align_left.svg"), tooltip="左对齐")
        add_tool("", lambda: self._notes_editor.setAlignment(Qt.AlignmentFlag.AlignCenter), icon=_icon("icon_align_center.svg"), tooltip="居中对齐")
        add_tool("", lambda: self._notes_editor.setAlignment(Qt.AlignmentFlag.AlignRight), icon=_icon("icon_align_right.svg"), tooltip="右对齐")
        add_tool("", lambda: self._insert_note_list(False), icon=_icon("icon_list_bullet.svg"), tooltip="无序列表")
        add_tool("", lambda: self._insert_note_list(True), icon=_icon("icon_list_number.svg"), tooltip="有序列表")

        toolbar_layout.addStretch(1)

        self._notes_editor = _NotesEditor(self)
        self._notes_editor.setObjectName("NotesEditor")
        self._notes_editor.document().setDocumentMargin(10)
        self._notes_editor.setAcceptRichText(True)
        # 首次初始化编辑器，默认应用 5 号字及微软雅黑字体
        first_fmt = QTextCharFormat()
        first_fmt.setFontPointSize(10.5)
        first_fmt.setFontFamily("Microsoft YaHei")
        self._notes_editor.setCurrentCharFormat(first_fmt)

        self._last_text_color = QColor("#dc2626")  # 默认文字颜色设为红色以呼应按钮底下一横
        self._last_bg_color = QColor("#FFF3A3")    # 默认背景颜色设为黄色以呼应按钮背景

        self._notes_editor.textChanged.connect(self._on_notes_editor_text_changed)
        self._notes_editor.currentCharFormatChanged.connect(self._on_notes_current_format_changed)
        self._notes_editor_container = RoundedTextEditContainer(self._notes_editor)

        self._note_outline_refresh_timer = QTimer(self)
        self._note_outline_refresh_timer.setSingleShot(True)
        self._note_outline_refresh_timer.timeout.connect(self._refresh_note_outline)
        self._note_outline_heading_positions: list[int] = []
        self._note_outline_items_by_position: dict[int, QTreeWidgetItem] = {}
        self._note_outline_manual_visibility: Optional[bool] = None
        self._note_outline_dirty = True
        self._note_window_layout_timer = QTimer(self)
        self._note_window_layout_timer.setSingleShot(True)
        self._note_window_layout_timer.timeout.connect(self._ensure_active_note_tab_visible)
        self._notes_editor.textChanged.connect(self._schedule_note_outline_refresh)
        self._notes_editor.cursorPositionChanged.connect(self._sync_note_outline_current_item)

        self._note_outline_panel = QFrame()
        self._note_outline_panel.setObjectName("NoteOutlinePanel")
        self._note_outline_panel.setFixedWidth(236)
        self._note_outline_panel.setVisible(False)
        self._note_outline_panel.setStyleSheet("""
            QFrame#NoteOutlinePanel {
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
            QLabel#NoteOutlineTitle {
                color: #0f172a;
                font-size: 14px;
                font-weight: 700;
                padding: 0px;
            }
            QTreeWidget#NoteOutlineTree {
                background: transparent;
                border: none;
                color: #334155;
                font-size: 13px;
                outline: none;
            }
            QTreeWidget#NoteOutlineTree::item {
                min-height: 26px;
                padding: 1px 3px;
                border-radius: 5px;
            }
            QTreeWidget#NoteOutlineTree::item:hover {
                background: #f1f5f9;
            }
            QTreeWidget#NoteOutlineTree::item:selected {
                background: #e2e8f0;
                color: #0f172a;
            }
        """)
        outline_layout = QVBoxLayout(self._note_outline_panel)
        outline_layout.setContentsMargins(10, 10, 8, 10)
        outline_layout.setSpacing(6)
        outline_title = QLabel("目录")
        outline_title.setObjectName("NoteOutlineTitle")
        outline_layout.addWidget(outline_title)
        self._note_outline_tree = QTreeWidget()
        self._note_outline_tree.setObjectName("NoteOutlineTree")
        self._note_outline_tree.setHeaderHidden(True)
        self._note_outline_tree.setRootIsDecorated(True)
        self._note_outline_tree.setIndentation(15)
        self._note_outline_tree.setAnimated(False)
        self._note_outline_tree.setUniformRowHeights(True)
        self._note_outline_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._note_outline_tree.itemClicked.connect(self._jump_to_note_outline_item)
        outline_layout.addWidget(self._note_outline_tree, 1)

        note_body = QWidget()
        note_body_layout = QHBoxLayout(note_body)
        note_body_layout.setContentsMargins(0, 0, 0, 0)
        note_body_layout.setSpacing(8)
        note_body_layout.addWidget(self._note_outline_panel)
        note_body_layout.addWidget(self._notes_editor_container, 1)

        self._note_outline_window_filter = _NoteOutlineWindowEventFilter(self)
        self.installEventFilter(self._note_outline_window_filter)
        if self._stack is not None:
            self._stack.currentChanged.connect(self._sync_note_outline_visibility)

        class _NoteClickFilter(QObject):
            def __init__(self, editor: QTextEdit, parent: QObject | None = None):
                super().__init__(parent)
                self._editor = editor

            def eventFilter(self, obj: QObject, event: QEvent) -> bool:
                try:
                    if not self._editor:
                        return False
                    editor = self._editor
                    viewport = editor.viewport()
                except (RuntimeError, AttributeError):
                    return False

                if obj is editor or obj is viewport:
                    if event.type() == QEvent.Type.MouseMove:
                        try:
                            pos = event.pos()
                            if obj is editor:
                                pos = viewport.mapFromParent(pos)
                            cursor = editor.cursorForPosition(pos)
                            char_format = cursor.charFormat()

                            is_img = char_format.isImageFormat()
                            if not is_img:
                                from PyQt6.QtGui import QTextCursor
                                test_cursor = QTextCursor(cursor)
                                if test_cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, 1):
                                    test_cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
                                    if test_cursor.charFormat().isImageFormat():
                                        is_img = True

                            if is_img or editor.anchorAt(pos):
                                viewport.setCursor(Qt.CursorShape.PointingHandCursor)
                            else:
                                viewport.setCursor(Qt.CursorShape.IBeamCursor)
                        except Exception:
                            pass

                    elif event.type() == QEvent.Type.MouseButtonRelease:
                        if event.button() == Qt.MouseButton.LeftButton:
                            try:
                                pos = event.pos()
                                if obj is editor:
                                    pos = viewport.mapFromParent(pos)
                                cursor = editor.cursorForPosition(pos)
                                char_format = cursor.charFormat()

                                # 同样进行左侧探测，极大提升点击原图的灵敏度
                                if not char_format.isImageFormat():
                                    from PyQt6.QtGui import QTextCursor
                                    test_cursor = QTextCursor(cursor)
                                    if test_cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, 1):
                                        test_cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
                                        if test_cursor.charFormat().isImageFormat():
                                            char_format = test_cursor.charFormat()

                                if char_format.isImageFormat():
                                    image_format = char_format.toImageFormat()
                                    src = image_format.name()
                                    if src:
                                        if src in ("todo_checked.png", "todo_unchecked.png"):
                                            return True
                                        local_path = src
                                        if src.startswith("file://"):
                                            from PyQt6.QtCore import QUrl
                                            local_path = QUrl(src).toLocalFile()

                                        import os
                                        if os.path.exists(local_path):
                                            dialog = ImagePreviewDialog(local_path, parent=editor)
                                            dialog.exec()
                                        else:
                                            from PyQt6.QtCore import QUrl
                                            QDesktopServices.openUrl(QUrl.fromUserInput(src))
                                    return True
                                anchor = editor.anchorAt(pos)
                                if anchor:
                                    if hasattr(editor, "_open_anchor") and editor._open_anchor(anchor):
                                        return True
                                    QDesktopServices.openUrl(QUrl.fromUserInput(anchor))
                                    return True
                            except Exception:
                                pass
                return super().eventFilter(obj, event)

        self._notes_editor_click_filter = _NoteClickFilter(self._notes_editor, self)
        self._notes_editor.installEventFilter(self._notes_editor_click_filter)
        self._notes_editor.viewport().installEventFilter(self._notes_editor_click_filter)
        note_layout.addWidget(toolbar)
        note_layout.addWidget(note_body, 1)

        self._table_notes_stack.addWidget(table_page)
        self._table_notes_stack.addWidget(note_page)
        self._table_notes_empty_state = self._make_unified_empty_state(
            "还没有表格内容",
            "在单元格里输入内容后，会自动保存到当前表格。",
            "开始填写",
            self._focus_table_notes_from_empty,
            compact=True,
        )
        content_layout.addWidget(self._table_notes_empty_state)
        content_layout.addWidget(self._table_notes_stack, 1)
        self._table_tabs: list[dict[str, Any]] = [{"name": "表格1", "data": None, "column_widths": self._default_table_column_widths(), "group_name": ""}]
        self._note_tabs: list[dict[str, Any]] = [{"name": "记事本1", "html": None, "group_name": "", "ima_config": default_ima_config()}]
        self._active_table_tab = 0
        self._active_note_tab = 0
        self._current_table_group = "全部"
        self._current_note_group = "全部"
        self._load_table_notes_settings()
        self._switch_table_notes_mode("table")
        self._refresh_tab_bars()
        return page

    def _table_notes_settings(self) -> dict[str, Any]:
        """兼容接口：从 SQLite 加载数据并返回与旧格式相同的 dict。"""
        try:
            table_tabs, active_table = self._table_notes_store.load_table_tabs()
            note_tabs, active_note = self._table_notes_store.load_note_tabs()
            return {
                "table_tabs": table_tabs,
                "note_tabs": note_tabs,
                "active_table_tab": active_table,
                "active_note_tab": active_note,
            }
        except Exception:
            return {"table": [], "note_html": ""}

    def _load_table_notes_settings(self) -> None:
        self._loading_table_notes = True
        self._table_tab_unlocked = True
        self._note_tab_unlocked = True
        try:
            self._table_tabs, self._active_table_tab = self._table_notes_store.load_table_tabs()
            self._note_tabs, self._active_note_tab = self._table_notes_store.load_note_tabs()
            self._load_current_table_tab_data()
            self._load_current_note_tab_data()
        finally:
            self._loading_table_notes = False
        self._recalculate_table_sums()
        self._refresh_tab_bars()
        self._refresh_table_notes_empty_state()
        self._reset_table_notes_save_state()

    def _reset_table_notes_save_state(self) -> None:
        """将当前内存状态设为保存基线；空表的默认页签仍需在首次变更时写入。"""
        store = getattr(self, "_table_notes_store", None)
        table_checker = getattr(store, "has_table_tabs", None)
        note_checker = getattr(store, "has_note_tabs", None)
        table_persisted = bool(table_checker()) if callable(table_checker) else True
        note_persisted = bool(note_checker()) if callable(note_checker) else True

        self._dirty_table_tab_indices: set[int] = set()
        self._dirty_note_tab_indices: set[int] = set()
        self._persisted_table_tab_ids = [id(tab) for tab in self._table_tabs] if table_persisted else []
        self._persisted_note_tab_ids = [id(tab) for tab in self._note_tabs] if note_persisted else []
        if not table_persisted:
            self._dirty_table_tab_indices.update(range(len(self._table_tabs)))
        if not note_persisted:
            self._dirty_note_tab_indices.update(range(len(self._note_tabs)))
        self._table_tabs_structure_dirty = False
        self._note_tabs_structure_dirty = False
        self._persisted_active_table_tab: Optional[int] = self._active_table_tab if table_persisted else None
        self._persisted_active_note_tab: Optional[int] = self._active_note_tab if note_persisted else None
        self._active_table_index_dirty = not table_persisted
        self._active_note_index_dirty = not note_persisted

    def _ensure_table_notes_save_state(self) -> None:
        if hasattr(self, "_dirty_table_tab_indices") and hasattr(self, "_dirty_note_tab_indices"):
            return
        self._dirty_table_tab_indices = set(range(len(getattr(self, "_table_tabs", []))))
        self._dirty_note_tab_indices = set(range(len(getattr(self, "_note_tabs", []))))
        self._persisted_table_tab_ids = []
        self._persisted_note_tab_ids = []
        self._table_tabs_structure_dirty = False
        self._note_tabs_structure_dirty = False
        self._persisted_active_table_tab = None
        self._persisted_active_note_tab = None
        self._active_table_index_dirty = True
        self._active_note_index_dirty = True

    def _mark_table_notes_tab_dirty(self, tab_type: str, index: int) -> None:
        NotesMixin._ensure_table_notes_save_state(self)
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        if 0 <= index < len(tabs):
            dirty = self._dirty_table_tab_indices if tab_type == "table" else self._dirty_note_tab_indices
            dirty.add(index)

    def _detect_table_notes_structure_changes(self, tab_type: str) -> None:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        current_ids = [id(tab) for tab in tabs]
        if tab_type == "table":
            persisted_ids = self._persisted_table_tab_ids
            dirty = self._dirty_table_tab_indices
            structure_attr = "_table_tabs_structure_dirty"
        else:
            persisted_ids = self._persisted_note_tab_ids
            dirty = self._dirty_note_tab_indices
            structure_attr = "_note_tabs_structure_dirty"

        dirty.intersection_update(range(len(current_ids)))
        structure_changed = (
            len(current_ids) < len(persisted_ids)
            or current_ids[:len(persisted_ids)] != persisted_ids
        )
        setattr(self, structure_attr, structure_changed)
        if not structure_changed:
            dirty.update(range(len(persisted_ids), len(current_ids)))

    def _record_table_notes_structure_saved(self, tab_type: str) -> None:
        if tab_type == "table":
            self._persisted_table_tab_ids = [id(tab) for tab in self._table_tabs]
            self._dirty_table_tab_indices.clear()
            self._table_tabs_structure_dirty = False
        else:
            self._persisted_note_tab_ids = [id(tab) for tab in self._note_tabs]
            self._dirty_note_tab_indices.clear()
            self._note_tabs_structure_dirty = False

    def _save_table_notes_collection(self, tab_type: str, errors: list[tuple[str, Exception]]) -> None:
        is_table = tab_type == "table"
        tabs = self._table_tabs if is_table else self._note_tabs
        dirty = self._dirty_table_tab_indices if is_table else self._dirty_note_tab_indices
        structure_attr = "_table_tabs_structure_dirty" if is_table else "_note_tabs_structure_dirty"
        persisted_ids_attr = "_persisted_table_tab_ids" if is_table else "_persisted_note_tab_ids"
        active_attr = "_active_table_tab" if is_table else "_active_note_tab"
        persisted_active_attr = "_persisted_active_table_tab" if is_table else "_persisted_active_note_tab"
        active_dirty_attr = "_active_table_index_dirty" if is_table else "_active_note_index_dirty"
        item_label = "表格" if is_table else "笔记"

        self._detect_table_notes_structure_changes(tab_type)
        if bool(getattr(self, structure_attr)):
            replace = self._table_notes_store.replace_table_tabs if is_table else self._table_notes_store.replace_note_tabs
            try:
                replace(tabs)
            except Exception as exc:
                errors.append((f"{item_label}页签结构", exc))
                get_logger().exception("保存%s页签结构失败", item_label)
                return
            self._record_table_notes_structure_saved(tab_type)

        save_tab = self._table_notes_store.save_table_tab if is_table else self._table_notes_store.save_note_tab
        persisted_ids: list[int] = getattr(self, persisted_ids_attr)
        for index in sorted(dirty):
            if index < 0 or index >= len(tabs):
                continue
            try:
                save_tab(index, tabs[index])
            except Exception as exc:
                errors.append((f"{item_label}页签 {index + 1}", exc))
                get_logger().exception("保存%s页签 %d 失败", item_label, index + 1)
                continue
            dirty.discard(index)
            if index == len(persisted_ids):
                persisted_ids.append(id(tabs[index]))

        active_index = int(getattr(self, active_attr))
        persisted_active = getattr(self, persisted_active_attr)
        active_dirty = persisted_active is None or active_index != persisted_active
        setattr(self, active_dirty_attr, active_dirty)
        if not active_dirty or active_index < 0 or active_index >= len(persisted_ids):
            return

        set_active = (
            self._table_notes_store.set_active_table_tab
            if is_table
            else self._table_notes_store.set_active_note_tab
        )
        try:
            set_active(active_index)
        except Exception as exc:
            errors.append((f"{item_label}活动页签", exc))
            get_logger().exception("保存%s活动页签失败", item_label)
            return
        setattr(self, persisted_active_attr, active_index)
        setattr(self, active_dirty_attr, False)

    def _save_table_notes_settings(self) -> None:
        if bool(getattr(self, "_loading_table_notes", False)):
            return
        self._ensure_table_notes_save_state()
        self._save_current_table_tab_data()
        self._save_current_note_tab_data()
        errors: list[tuple[str, Exception]] = []
        self._save_table_notes_collection("table", errors)
        self._save_table_notes_collection("note", errors)
        if errors:
            target, exc = errors[0]
            detail = str(exc).strip() or exc.__class__.__name__
            show_status = getattr(self, "_show_table_notes_status", None)
            if callable(show_status):
                show_status(
                    f"{target}保存失败，未保存的更改仍会保留：{detail}",
                    tone="error",
                    auto_hide_ms=5200,
                )

    def _load_table_notes_view_safely(self, loader: Callable[[], None]) -> None:
        was_loading = bool(getattr(self, "_loading_table_notes", False))
        self._loading_table_notes = True
        try:
            loader()
        finally:
            self._loading_table_notes = was_loading

    def _table_notes_current_table_empty(self) -> bool:
        table = getattr(self, "_notes_table", None)
        if not isinstance(table, QTableWidget):
            return False
        for row in range(table.rowCount()):
            for col in range(table.columnCount()):
                item = table.item(row, col)
                if item is not None and str(item.text() or "").strip():
                    return False
        return True

    def _table_notes_current_note_empty(self) -> bool:
        editor = getattr(self, "_notes_editor", None)
        if not isinstance(editor, QTextEdit):
            return False
        if str(editor.toPlainText() or "").strip():
            return False
        html_content = str(editor.toHtml() or "").lower()
        return not any(token in html_content for token in ("<img", "<table", "href=", "data-attachment"))

    def _refresh_table_notes_empty_state(self) -> None:
        empty_state = getattr(self, "_table_notes_empty_state", None)
        stack = getattr(self, "_table_notes_stack", None)
        if not isinstance(empty_state, QWidget) or not isinstance(stack, QStackedWidget):
            return

        # 用户要求不再显示"还没有笔记内容"/"还没有表格内容"空状态提示，
        # 因此无论是否为空都隐藏该组件。
        empty_state.setVisible(False)

    def _focus_table_notes_from_empty(self) -> None:
        stack = getattr(self, "_table_notes_stack", None)
        if isinstance(stack, QStackedWidget) and stack.currentIndex() == 1:
            if hasattr(self, "_notes_editor"):
                self._notes_editor.setFocus()
            return

        table = getattr(self, "_notes_table", None)
        if not isinstance(table, QTableWidget):
            return
        if table.rowCount() <= 0 or table.columnCount() <= 0:
            self._reset_notes_table_to_default()
        item = table.item(0, 0)
        if item is None:
            item = QTableWidgetItem("")
            table.setItem(0, 0, item)
        table.setCurrentCell(0, 0)
        table.setFocus()
        table.editItem(item)

    def _on_notes_editor_text_changed(self) -> None:
        if getattr(self, "_loading_table_notes", False):
            return
        self._refresh_table_notes_empty_state()
        self._schedule_table_notes_save()
        self._update_note_title_from_content()
        self._schedule_ima_auto_sync_for_active_note()
        pinned = self._active_pinned_notes.get(self._active_note_tab)
        if pinned:
            html = self._notes_editor.toHtml()
            if pinned.toHtml() != html:
                pinned.blockSignals(True)
                pinned.setHtml(html)
                pinned.blockSignals(False)

    def _schedule_table_notes_save(self) -> None:
        if bool(getattr(self, "_loading_table_notes", False)):
            return
        try:
            self._table_notes_save_timer.start(350)
        except Exception:
            self._save_table_notes_settings()

    def _schedule_ima_auto_sync_for_active_note(self) -> None:
        if bool(getattr(self, "_loading_table_notes", False)):
            return
        index = int(getattr(self, "_active_note_tab", -1))
        if index < 0 or index >= len(getattr(self, "_note_tabs", [])):
            return
        tab = getattr(self, "_note_tabs", [])[index]
        if not self._should_sync_note_tab_to_ima(tab):
            return
        config = dict(tab.get("ima_config") or {})
        if not bool(config.get("enabled", False)) or not bool(config.get("auto_sync_enabled", False)):
            return
        if not str(config.get("client_id", "") or "").strip() or not str(config.get("api_key", "") or "").strip():
            return
        self._ima_auto_sync_pending_note_index = index
        try:
            self._ima_auto_sync_timer.start(self._IMA_AUTO_SYNC_DELAY_MS)
        except Exception:
            self._run_pending_ima_auto_sync()

    def _inherited_ima_config_for_new_note(self, exclude_index: int = -1) -> dict[str, Any]:
        inherited = default_ima_config()
        tabs = list(getattr(self, "_note_tabs", []) or [])
        active_index = int(getattr(self, "_active_note_tab", -1))
        candidate_indices: list[int] = []
        if 0 <= active_index < len(tabs):
            candidate_indices.append(active_index)
        candidate_indices.extend(index for index in range(len(tabs)) if index not in candidate_indices)

        for index in candidate_indices:
            if index == exclude_index:
                continue
            config = dict(tabs[index].get("ima_config") or {})
            if not config:
                continue
            if not bool(config.get("enabled", False)) and not str(config.get("client_id", "") or "").strip():
                continue
            inherited.update(config)
            inherited["remote_note_id"] = ""
            inherited["remote_note_title"] = ""
            inherited["knowledge_media_id"] = ""
            inherited["last_sync_at"] = ""
            return inherited
        return inherited

    def _sync_external_note_to_ima(self, index: int, *, inherit_config: bool = False) -> None:
        if index < 0 or index >= len(getattr(self, "_note_tabs", [])):
            return
        tab = getattr(self, "_note_tabs", [])[index]
        if not self._should_sync_note_tab_to_ima(tab):
            return
        if inherit_config:
            config = dict(self._note_tabs[index].get("ima_config") or {})
            if not self._ima_auto_sync_config_ready(config):
                inherited = self._inherited_ima_config_for_new_note(exclude_index=index)
                if self._ima_auto_sync_config_ready(inherited):
                    self._note_tabs[index]["ima_config"] = inherited
                    NotesMixin._mark_table_notes_tab_dirty(self, "note", index)
                    self._save_table_notes_settings()

        config = dict(self._note_tabs[index].get("ima_config") or {})
        if not self._ima_auto_sync_config_ready(config):
            return

        worker = getattr(self, "_ima_sync_worker", None)
        if worker is not None:
            try:
                if worker.isRunning():
                    self._ima_auto_sync_pending_note_index = index
                    self._ima_auto_sync_timer.start(self._IMA_AUTO_SYNC_DELAY_MS)
                    return
            except Exception:
                return
        self._run_ima_tab_action(index, "append_note", silent=True)

    def _reset_notes_table_to_default(self) -> None:
        self._notes_table.blockSignals(True)
        try:
            self._notes_table.clearContents()
            self._notes_table.setRowCount(self._DEFAULT_TABLE_ROWS)
            self._notes_table.setColumnCount(self._DEFAULT_TABLE_COLUMNS)
            self._notes_table.setHorizontalHeaderLabels([f"列 {i}" for i in range(1, self._DEFAULT_TABLE_COLUMNS + 1)])
        finally:
            self._notes_table.blockSignals(False)
        if getattr(self, "_notes_sum_table", None) is not None:
            self._notes_sum_table.blockSignals(True)
            try:
                self._notes_sum_table.clearContents()
                self._notes_sum_table.setColumnCount(self._DEFAULT_TABLE_COLUMNS)
                self._notes_sum_table.setHorizontalHeaderLabels([f"列 {i}" for i in range(1, self._DEFAULT_TABLE_COLUMNS + 1)])
            finally:
                self._notes_sum_table.blockSignals(False)
        self._apply_table_column_widths(self._notes_table, self._default_table_column_widths(), sync_sum=True)

    def _on_notes_table_section_resized(self, index: int, old_size: int, new_size: int) -> None:
        if bool(getattr(self, "_syncing_column_widths", False)):
            return
        self._syncing_column_widths = True
        try:
            if getattr(self, "_notes_sum_table", None) is not None:
                self._notes_sum_table.setColumnWidth(index, new_size)
            pinned = self._active_pinned_tables.get(self._active_table_tab)
            if pinned is not None:
                pinned.setColumnWidth(index, new_size)
        finally:
            self._syncing_column_widths = False
        if not bool(getattr(self, "_loading_table_notes", False)):
            self._remember_table_column_widths(self._active_table_tab, self._notes_table)
            self._schedule_table_notes_save()

    def _on_notes_table_changed(self) -> None:
        if bool(getattr(self, "_loading_table_notes", False)):
            return
        pinned = self._active_pinned_tables.get(self._active_table_tab)
        if pinned:
            self._sync_table_widget(self._notes_table, pinned)
        self._recalculate_table_sums()
        self._refresh_table_notes_empty_state()
        self._schedule_table_notes_save()

    def _switch_table_notes_mode(self, mode: str, *, verify_password: bool = True) -> None:
        is_note = str(mode) == "note"

        if not hasattr(self, "_table_tab_unlocked"):
            self._table_tab_unlocked = False
        if not hasattr(self, "_note_tab_unlocked"):
            self._note_tab_unlocked = False

        if is_note:
            self._table_tab_unlocked = False  # 离焦表格，锁住
            n_active_tab = self._note_tabs[self._active_note_tab] if hasattr(self, "_note_tabs") and self._active_note_tab < len(self._note_tabs) else None
            n_pwd = n_active_tab.get("password", "") if n_active_tab else ""
            if n_pwd and not self._note_tab_unlocked:
                if not verify_password:
                    self._load_table_notes_view_safely(self._load_current_note_tab_data)
                    self._note_tab_unlocked = True
                else:
                    dialog = _TabPasswordVerifyDialog(n_pwd, self)
                    dialog.setWindowTitle("输入记事本密码以解锁")
                    if dialog.exec() == _TabPasswordVerifyDialog.DialogCode.Accepted:
                        self._load_table_notes_view_safely(self._load_current_note_tab_data)
                        self._note_tab_unlocked = True
                    else:
                        fallback_idx = -1
                        for idx, tab in enumerate(self._note_tabs):
                            if not tab.get("password", ""):
                                fallback_idx = idx
                                break
                        if fallback_idx != -1:
                            self._active_note_tab = fallback_idx
                            self._load_table_notes_view_safely(self._load_current_note_tab_data)
                            self._note_tab_unlocked = True
                            self._save_table_notes_settings()
                        else:
                            self._load_table_notes_view_safely(self._notes_editor.clear)
        else:
            self._note_tab_unlocked = False  # 离焦记事本，锁住
            t_active_tab = self._table_tabs[self._active_table_tab] if hasattr(self, "_table_tabs") and self._active_table_tab < len(self._table_tabs) else None
            t_pwd = t_active_tab.get("password", "") if t_active_tab else ""
            table_view_reloaded = False
            if t_pwd and not self._table_tab_unlocked:
                if not verify_password:
                    self._load_table_notes_view_safely(self._load_current_table_tab_data)
                    self._table_tab_unlocked = True
                    table_view_reloaded = True
                else:
                    dialog = _TabPasswordVerifyDialog(t_pwd, self)
                    dialog.setWindowTitle("输入表格密码以解锁")
                    if dialog.exec() == _TabPasswordVerifyDialog.DialogCode.Accepted:
                        self._load_table_notes_view_safely(self._load_current_table_tab_data)
                        self._table_tab_unlocked = True
                        table_view_reloaded = True
                    else:
                        fallback_idx = -1
                        for idx, tab in enumerate(self._table_tabs):
                            if not tab.get("password", ""):
                                fallback_idx = idx
                                break
                        if fallback_idx != -1:
                            self._active_table_tab = fallback_idx
                            self._load_table_notes_view_safely(self._load_current_table_tab_data)
                            self._table_tab_unlocked = True
                            table_view_reloaded = True
                            self._save_table_notes_settings()
                        else:
                            def clear_locked_table() -> None:
                                old_blocked = self._notes_table.blockSignals(True)
                                try:
                                    self._notes_table.clearContents()
                                    self._notes_table.setRowCount(0)
                                finally:
                                    self._notes_table.blockSignals(old_blocked)

                            self._load_table_notes_view_safely(clear_locked_table)
                            table_view_reloaded = True
            if table_view_reloaded:
                pinned = self._active_pinned_tables.get(self._active_table_tab)
                if pinned:
                    self._sync_table_widget(self._notes_table, pinned)
                self._recalculate_table_sums()

        self._table_notes_stack.setCurrentIndex(1 if is_note else 0)
        if hasattr(self, "_table_notes_switch_btn") and self._table_notes_switch_btn is not None:
            self._table_notes_switch_btn.setText("切换表格" if is_note else "切换记事本")
        self._refresh_tab_bars()
        self._refresh_table_notes_empty_state()
        QTimer.singleShot(0, self._sync_note_outline_visibility)

    def _make_tab_bar(self, tab_type: str) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TabBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        group_btn = QToolButton()
        group_btn.setObjectName("TabGroupButton")
        group_btn.setText("分组: 全部 ▾")
        group_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        group_btn.setFixedHeight(28)
        group_btn.setToolTip("切换标签页分组")
        group_btn.setStyleSheet("""
            QToolButton#TabGroupButton {
                border: 1px solid #dfe4ec;
                border-radius: 4px;
                background-color: #f8fafc;
                color: #475569;
                font-size: 12px;
                font-weight: bold;
                padding: 0 6px;
            }
            QToolButton#TabGroupButton:hover {
                background-color: #f1f5f9;
                border-color: #cbd5e1;
            }
        """)
        group_btn.clicked.connect(lambda *_: self._show_group_menu(tab_type, group_btn))
        layout.addWidget(group_btn)

        left_btn = QToolButton()
        left_btn.setText("◀")
        left_btn.setObjectName("TabArrowButton")
        left_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        left_btn.setFixedSize(22, 28)
        left_btn.clicked.connect(lambda *_: self._scroll_tab_bar(bar, -60))
        layout.addWidget(left_btn)

        scroll = QScrollArea()
        scroll.setObjectName("TabScrollArea")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(36)

        scroll_widget = QWidget()
        scroll_widget.setObjectName("TabScrollWidget")
        scroll_layout = QHBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(4)

        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll, 1)

        def custom_scroll_wheel_event(event) -> None:
            angle = event.angleDelta()
            val = angle.y() if angle.y() != 0 else angle.x()
            self._scroll_tab_bar(bar, -val)
            event.accept()
        scroll.wheelEvent = custom_scroll_wheel_event

        right_btn = QToolButton()
        right_btn.setText("▶")
        right_btn.setObjectName("TabArrowButton")
        right_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        right_btn.setFixedSize(22, 28)
        right_btn.clicked.connect(lambda *_: self._scroll_tab_bar(bar, 60))
        layout.addWidget(right_btn)

        list_btn = QToolButton()
        list_btn.setIcon(self._asset_icon("icon_list_bullet.svg"))
        list_btn.setIconSize(QSize(16, 16))
        list_btn.setObjectName("TabListButton")
        list_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        list_btn.setFixedSize(28, 28)
        list_btn.setToolTip("显示全部表格" if tab_type == "table" else "显示全部笔记本")
        list_btn.clicked.connect(lambda *_: self._show_tab_list_menu(tab_type, list_btn))
        layout.addWidget(list_btn)

        add_btn = QToolButton()
        add_btn.setText("+")
        add_btn.setObjectName("TabAddButton")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setFixedSize(28, 28)
        add_btn.clicked.connect(lambda *_: self._on_add_tab(tab_type))
        layout.addWidget(add_btn)

        bar._scroll = scroll
        bar._scroll_layout = scroll_layout
        bar._scroll_widget = scroll_widget
        bar._left_btn = left_btn
        bar._right_btn = right_btn
        bar._list_btn = list_btn
        bar._add_btn = add_btn
        bar._tab_type = tab_type
        bar._group_btn = group_btn
        return bar

    def _scroll_tab_bar(self, bar: QWidget, delta: int) -> None:
        scroll: QScrollArea = getattr(bar, "_scroll")
        hbar = scroll.horizontalScrollBar()
        hbar.setValue(hbar.value() + delta)
        self._update_tab_arrows(bar)

    def _update_tab_arrows(self, bar: QWidget) -> None:
        scroll: QScrollArea = getattr(bar, "_scroll")
        hbar = scroll.horizontalScrollBar()
        left_btn: QToolButton = getattr(bar, "_left_btn")
        right_btn: QToolButton = getattr(bar, "_right_btn")
        left_btn.setVisible(hbar.value() > hbar.minimum())
        right_btn.setVisible(hbar.value() < hbar.maximum())

    def _tab_list_display_name(self, tab: dict[str, Any], index: int, tab_type: str) -> str:
        default_name = f"表格{index + 1}" if tab_type == "table" else f"笔记本{index + 1}"
        name = str(tab.get("name", "") or default_name).strip()
        return name or default_name

    def _show_tab_list_menu(self, tab_type: str, anchor: QToolButton) -> None:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        if not tabs:
            return

        old_popup = getattr(self, "_tab_list_menu", None)
        if old_popup is not None:
            try:
                old_popup.close()
            except RuntimeError:
                pass

        raw_active_index = self._active_table_tab if tab_type == "table" else self._active_note_tab

        items = []
        for i, tab in enumerate(tabs):
            name = self._tab_list_display_name(tab, i, tab_type)
            items.append({
                "text": name,
                "name": name,
                "index": i,
                "group_name": str(tab.get("group_name", "") or ""),
            })

        popup = GroupedNoteListPopup(
            items,
            lambda item: self._select_tab_from_list_menu(tab_type, item),
            active_index=raw_active_index,
            initially_expanded=True,
            empty_text="暂无表格" if tab_type == "table" else "暂无笔记本",
            max_height=450,
            on_context_menu=lambda global_pos, item=None: self._show_tab_list_context_menu(tab_type, global_pos, item),
            parent=self,
        )

        self._tab_list_menu = popup
        popup.destroyed.connect(
            lambda *_: setattr(self, "_tab_list_menu", None)
            if getattr(self, "_tab_list_menu", None) is popup else None
        )
        popup.show_for_anchor(anchor, align_right=True, bounds_widget=self)

    def _select_tab_from_list_menu(self, tab_type: str, item: dict[str, Any]) -> None:
        try:
            index = int(item.get("index", -1))
        except Exception:
            index = -1
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        if index < 0 or index >= len(tabs):
            return

        target_group = str(tabs[index].get("group_name", "") or "")
        current_group = self._current_table_group if tab_type == "table" else self._current_note_group
        if current_group != "全部" and current_group != target_group:
            self._set_current_group_label(tab_type, target_group)
        self._on_tab_clicked(tab_type, index)

    def _set_current_group_label(self, tab_type: str, group_name: str) -> None:
        display = "全部" if group_name == "全部" else (group_name if group_name else "未分组")
        if tab_type == "table":
            self._current_table_group = group_name
            bar = getattr(self, "_table_tab_bar", None)
        else:
            self._current_note_group = group_name
            bar = getattr(self, "_note_tab_bar", None)
        btn = getattr(bar, "_group_btn", None)
        if btn:
            btn.setText(f"分组: {display} ▾")

    def _show_tab_list_context_menu(self, tab_type: str, global_pos: QPoint, item: Optional[dict[str, Any]] = None) -> None:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs

        def close_tab_list_popup() -> None:
            old_popup = getattr(self, "_tab_list_menu", None)
            if old_popup is not None:
                try:
                    old_popup.close()
                except RuntimeError:
                    pass

        menu_items = []
        try:
            item_index = int((item or {}).get("index", -1))
        except Exception:
            item_index = -1
        if 0 <= item_index < len(tabs):
            delete_label = "删除笔记" if tab_type == "note" else "删除表格"

            def delete_single(index: int = item_index) -> None:
                close_tab_list_popup()
                self._on_close_tab(tab_type, index)

            menu_items.append((delete_label, delete_single, True))

        def open_batch_delete() -> None:
            close_tab_list_popup()
            self._show_tab_batch_delete_dialog(tab_type)

        if menu_items:
            menu_items.append(("-", None, False))
        menu_items.append(("批量管理", open_batch_delete, True))

        from deepcat.ui.post_capture_actions import OcrGenericMenuPopup
        popup = OcrGenericMenuPopup(menu_items, parent=self)
        popup.show_at_pos(global_pos)

    def _show_tab_batch_delete_dialog(self, tab_type: str) -> None:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        if not tabs:
            return

        item_label = "表格" if tab_type == "table" else "笔记"
        from deepcat.ui.main_window.compact import StyledDialog
        dialog = StyledDialog(self)
        dialog.setWindowTitle(f"批量管理{item_label}")
        dialog.setMinimumSize(360, 420)

        root = QVBoxLayout(dialog)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        summary = QLabel(f"勾选要删除或移动分组的{item_label}。")
        summary.setStyleSheet("color: #475569;")
        root.addWidget(summary)

        select_all = QCheckBox("全选")
        root.addWidget(select_all)

        scroll = QScrollArea(dialog)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll, 1)

        content = QWidget()
        list_layout = QVBoxLayout(content)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(6)
        scroll.setWidget(content)

        grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for index, tab in enumerate(tabs):
            group_name = str(tab.get("group_name", "") or "").strip() or "未分组"
            grouped.setdefault(group_name, []).append((index, tab))

        group_names = sorted(grouped)
        if "未分组" in group_names:
            group_names.remove("未分组")
            group_names.insert(0, "未分组")

        checkboxes: list[QCheckBox] = []
        group_checkboxes: dict[str, QCheckBox] = {}
        group_item_checkboxes: dict[str, list[QCheckBox]] = {}
        for group_name in group_names:
            header = QCheckBox(f"{group_name}（{len(grouped[group_name])}）")
            header.setTristate(True)
            header.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 700; padding-top: 4px;")
            group_checkboxes[group_name] = header
            group_item_checkboxes[group_name] = []
            list_layout.addWidget(header)

            for index, tab in grouped[group_name]:
                name = self._tab_list_display_name(tab, index, tab_type)
                checkbox = QCheckBox(name)
                checkbox.setProperty("tab_index", index)
                checkbox.setToolTip(name)
                checkbox.setStyleSheet("padding-left: 8px;")
                checkboxes.append(checkbox)
                group_item_checkboxes[group_name].append(checkbox)
                list_layout.addWidget(checkbox)

        list_layout.addStretch(1)

        selected_count = QLabel("已选 0 项")
        selected_count.setStyleSheet("color: #64748b; font-size: 12px;")
        root.addWidget(selected_count)

        # 分组选择与操作按钮
        op_layout = QHBoxLayout()
        op_layout.setSpacing(8)

        group_label = QLabel("选择分组：")
        group_label.setStyleSheet("color: #475569;")
        op_layout.addWidget(group_label)

        group_combo = ModernPopupComboBox(dialog)
        group_combo.setProperty("matchPopupWidthToParent", True)
        group_combo.setProperty("activeIndicator", "background")
        group_combo.setEnabled(False)
        all_group_names = sorted({str(t.get("group_name", "") or "").strip() or "未分组" for t in tabs})
        if "未分组" in all_group_names:
            all_group_names.remove("未分组")
            all_group_names.insert(0, "未分组")
        for name in all_group_names:
            group_combo.addItem(name, "" if name == "未分组" else name)
        group_combo.addItem("＋ 新建分组...", "__new__")
        group_combo.setMinimumWidth(140)
        op_layout.addWidget(group_combo, 1)

        move_btn = QPushButton("移动至分组", dialog)
        move_btn.setEnabled(False)
        op_layout.addWidget(move_btn)

        op_layout.addStretch(1)

        delete_btn = QPushButton("删除选中", dialog)
        delete_btn.setEnabled(False)
        op_layout.addWidget(delete_btn)

        cancel_btn = QPushButton("取消", dialog)
        op_layout.addWidget(cancel_btn)

        root.addLayout(op_layout)

        def update_group_checkbox_state(group_name: str) -> None:
            header = group_checkboxes.get(group_name)
            boxes = group_item_checkboxes.get(group_name, [])
            if header is None or not boxes:
                return
            checked_count = sum(1 for checkbox in boxes if checkbox.isChecked())
            old_blocked = header.blockSignals(True)
            try:
                if checked_count == 0:
                    header.setCheckState(Qt.CheckState.Unchecked)
                elif checked_count == len(boxes):
                    header.setCheckState(Qt.CheckState.Checked)
                else:
                    header.setCheckState(Qt.CheckState.PartiallyChecked)
            finally:
                header.blockSignals(old_blocked)

        def update_selection_state() -> None:
            count = sum(1 for checkbox in checkboxes if checkbox.isChecked())
            selected_count.setText(f"已选 {count} 项")
            delete_btn.setEnabled(count > 0)
            move_btn.setEnabled(count > 0)
            group_combo.setEnabled(count > 0)
            old_blocked = select_all.blockSignals(True)
            try:
                select_all.setChecked(bool(checkboxes) and count == len(checkboxes))
            finally:
                select_all.blockSignals(old_blocked)
            for group_name in group_names:
                update_group_checkbox_state(group_name)

        def toggle_all(checked: bool) -> None:
            for checkbox in checkboxes:
                checkbox.setChecked(bool(checked))
            update_selection_state()

        def toggle_group(group_name: str, checked: bool) -> None:
            for checkbox in group_item_checkboxes.get(group_name, []):
                checkbox.setChecked(bool(checked))
            update_selection_state()

        def get_selected_indices() -> list[int]:
            return sorted(
                {
                    int(checkbox.property("tab_index"))
                    for checkbox in checkboxes
                    if checkbox.isChecked()
                }
            )

        def ensure_target_group() -> Optional[str]:
            data = group_combo.currentData()
            if data == "__new__":
                name, ok = StyledInputDialog.get_text(dialog, "新建分组", "请输入新分组名称:")
                if not ok or not name.strip():
                    return None
                new_name = name.strip()
                existing_index = group_combo.findData(new_name)
                if existing_index < 0:
                    group_combo.insertItem(group_combo.count() - 1, new_name, new_name)
                    existing_index = group_combo.findData(new_name)
                group_combo.setCurrentIndex(existing_index)
                return new_name
            return data

        def do_delete() -> None:
            indices = get_selected_indices()
            if not indices:
                return
            preview_names = [self._tab_list_display_name(tabs[i], i, tab_type) for i in indices[:6] if 0 <= i < len(tabs)]
            preview = "\n".join(f"- {name}" for name in preview_names)
            if len(indices) > len(preview_names):
                preview += f"\n- 另有 {len(indices) - len(preview_names)} 项"
            reply = StyledMessageBox.question(
                self,
                f"确认批量删除{item_label}",
                f"确定删除选中的 {len(indices)} 个{item_label}吗？此操作不可恢复。\n\n{preview}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                deleted = self._delete_tabs_by_indices(tab_type, indices)
            except Exception as exc:
                self._show_table_notes_status(f"批量删除失败：{exc}", tone="error", auto_hide_ms=5200)
                return
            if deleted:
                self._show_table_notes_status(f"已删除 {len(indices)} 个{item_label}。", tone="success")
            dialog.accept()

        def do_move() -> None:
            indices = get_selected_indices()
            if not indices:
                return
            target_group = ensure_target_group()
            if target_group is None:
                return
            try:
                for idx in indices:
                    tabs[idx]["group_name"] = target_group
                    NotesMixin._mark_table_notes_tab_dirty(self, tab_type, idx)
                self._save_table_notes_settings()
                self._refresh_tab_bars()
                current_group = self._current_table_group if tab_type == "table" else self._current_note_group
                if current_group != "全部":
                    visible_indices = [i for i, t in enumerate(tabs) if t.get("group_name", "") == current_group]
                    if not visible_indices:
                        self._set_current_group(tab_type, "全部")
                    else:
                        active_index = self._active_table_tab if tab_type == "table" else self._active_note_tab
                        if active_index not in visible_indices:
                            self._on_tab_clicked(tab_type, visible_indices[0])
                display_group = target_group or "未分组"
                self._show_table_notes_status(f"已移动 {len(indices)} 个{item_label} 到「{display_group}」。", tone="success")
            except Exception as exc:
                self._show_table_notes_status(f"批量移动失败：{exc}", tone="error", auto_hide_ms=5200)
                return
            dialog.accept()

        select_all.toggled.connect(toggle_all)
        for group_name, header in group_checkboxes.items():
            header.clicked.connect(lambda checked=False, name=group_name: toggle_group(name, checked))
        for checkbox in checkboxes:
            checkbox.toggled.connect(lambda *_: update_selection_state())
        delete_btn.clicked.connect(do_delete)
        move_btn.clicked.connect(do_move)
        cancel_btn.clicked.connect(dialog.reject)

        dialog.exec()

    def _ensure_active_tab_visible_in_current_group(self, tab_type: str) -> None:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        current_group = self._current_table_group if tab_type == "table" else self._current_note_group
        if current_group == "全部":
            return

        visible_indices = [
            index
            for index, tab in enumerate(tabs)
            if str(tab.get("group_name", "") or "") == current_group
        ]
        if not visible_indices:
            self._set_current_group_label(tab_type, "全部")
            return

        if tab_type == "table":
            if self._active_table_tab not in visible_indices:
                self._active_table_tab = visible_indices[0]
        elif self._active_note_tab not in visible_indices:
            self._active_note_tab = visible_indices[0]

    def _refresh_tab_bars(self) -> None:
        self._refresh_tab_bar(self._table_tab_bar, self._table_tabs, self._active_table_tab, "table")
        self._refresh_tab_bar(self._note_tab_bar, self._note_tabs, self._active_note_tab, "note")

    def _refresh_tab_bar(self, bar: QWidget, tabs: list[dict[str, Any]], active_index: int, tab_type: str) -> None:
        scroll_layout: QHBoxLayout = getattr(bar, "_scroll_layout")
        while scroll_layout.count():
            item = scroll_layout.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.deleteLater()

        current_group = self._current_table_group if tab_type == "table" else self._current_note_group
        active_btn = None
        for i, tab in enumerate(tabs):
            if current_group != "全部" and tab.get("group_name", "") != current_group:
                continue

            original_name = str(tab.get("name", f"标签{i+1}"))
            btn = QPushButton()
            btn.setObjectName("TabButtonActive" if i == active_index else "TabButton")
            btn.setCheckable(True)
            btn.setChecked(i == active_index)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setMaximumWidth(150)
            btn.setToolTip(original_name)

            fm = btn.fontMetrics()
            elided_name = fm.elidedText(original_name, Qt.TextElideMode.ElideRight, 120)
            btn.setText(elided_name)

            btn.clicked.connect(lambda _=False, idx=i: self._on_tab_clicked(tab_type, idx))
            btn._tab_index = i
            btn._tab_type = tab_type
            btn.mouseDoubleClickEvent = lambda event, b=btn: self._on_tab_double_click(event, b)
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(lambda pos, b=btn: self._on_tab_context_menu(pos, b))
            scroll_layout.addWidget(btn)
            if i == active_index:
                active_btn = btn
        scroll_layout.addStretch(1)
        if active_btn is not None:
            QTimer.singleShot(0, lambda b=bar, act=active_btn: self._scroll_to_active_tab(b, act))
        else:
            QTimer.singleShot(0, lambda b=bar: self._update_tab_arrows(b))

    def _scroll_active_tab_into_view(self, tab_type: str) -> None:
        bar = self._table_tab_bar if tab_type == "table" else self._note_tab_bar
        scroll_layout: QHBoxLayout = getattr(bar, "_scroll_layout", None)
        if scroll_layout is None:
            return
        active_index = self._active_table_tab if tab_type == "table" else self._active_note_tab
        for i in range(scroll_layout.count()):
            item = scroll_layout.itemAt(i)
            btn = item.widget() if item else None
            if btn is not None and getattr(btn, "_tab_index", -1) == active_index:
                QTimer.singleShot(0, lambda b=bar, act=btn: self._scroll_to_active_tab(b, act))
                break

    def _on_tab_clicked(self, tab_type: str, index: int) -> bool:
        if not hasattr(self, "_table_tab_unlocked"):
            self._table_tab_unlocked = False
        if not hasattr(self, "_note_tab_unlocked"):
            self._note_tab_unlocked = False

        if tab_type == "table":
            if index < 0 or index >= len(self._table_tabs):
                return False
            if self._active_table_tab == index:
                return True
            target_tab = self._table_tabs[index]
            hashed_pwd = target_tab.get("password", "")
            if hashed_pwd:
                dialog = _TabPasswordVerifyDialog(hashed_pwd, self)
                if not dialog.exec():
                    self._refresh_tab_bars()
                    return False
            self._save_current_table_tab_data()
            self._active_table_tab = index
            self._table_tab_unlocked = True
            self._load_table_notes_view_safely(self._load_current_table_tab_data)
            pinned = self._active_pinned_tables.get(index)
            if pinned:
                self._sync_table_widget(self._notes_table, pinned)
            self._recalculate_table_sums()
            self._save_table_notes_settings()
        else:
            if index < 0 or index >= len(self._note_tabs):
                return False
            if self._active_note_tab == index:
                return True
            target_tab = self._note_tabs[index]
            hashed_pwd = target_tab.get("password", "")
            if hashed_pwd:
                dialog = _TabPasswordVerifyDialog(hashed_pwd, self)
                if not dialog.exec():
                    self._refresh_tab_bars()
                    return False
            self._save_current_note_tab_data()
            self._active_note_tab = index
            self._note_tab_unlocked = True
            self._load_table_notes_view_safely(self._load_current_note_tab_data)
            self._save_table_notes_settings()
        self._refresh_tab_bars()
        self._scroll_active_tab_into_view(tab_type)
        return True

    def _on_tab_context_menu(self, pos, btn: QPushButton) -> None:
        tab_type = getattr(btn, "_tab_type", "")
        index = getattr(btn, "_tab_index", 0)
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        tab = tabs[index]

        items = [
            ("置顶", lambda: self._pin_tab_content(tab_type, index), True),
            ("重命名", lambda: self._rename_tab(tab_type, index), True),
        ]

        has_pwd = bool(tab.get("password", ""))
        if has_pwd:
            items.append(("关闭密码", lambda: self._remove_tab_password(tab_type, index), True))
        else:
            items.append(("设置密码", lambda: self._set_tab_password(tab_type, index), True))

        if tab_type == "note":
            ima_config = dict(tab.get("ima_config") or {})
            ima_enabled = bool(ima_config.get("enabled", False))
            download_ready = ima_enabled and bool(str(ima_config.get("remote_note_id", "") or "").strip())
            kb_ready = ima_enabled and bool(ima_config.get("knowledge_base_enabled", False)) and bool(str(ima_config.get("knowledge_base_id", "") or "").strip())
            items.append(("设置IMA", lambda: self._set_tab_ima(tab_type, index), True))
            items.append(("下载IMA笔记", lambda: self._download_tab_from_ima_note(index), download_ready))
            items.append(("追加到IMA笔记", lambda: self._append_tab_to_ima_note(index), ima_enabled))
            items.append(("添加到IMA知识库", lambda: self._add_tab_to_ima_knowledge_base(index), kb_ready))

        close_label = "删除笔记" if tab_type == "note" else "关闭"
        items.append((close_label, lambda: self._on_close_tab(tab_type, index), True))
        items.append(("批量管理", lambda: self._show_tab_batch_delete_dialog(tab_type), True))

        # 移动到分组 (扁平化展现)
        items.append(("-", None, False))
        items.append(("移动到：未分组", lambda: self._move_tab_to_group(tab_type, index, ""), True))

        existing_groups = sorted(list({t.get("group_name", "").strip() for t in tabs if t.get("group_name", "").strip()}))
        for g in existing_groups:
            items.append((f"移动到：{g}", lambda group_name=g: self._move_tab_to_group(tab_type, index, group_name), True))

        items.append(("移动至新分组...", lambda: self._move_tab_to_new_group(tab_type, index), True))

        from deepcat.ui.post_capture_actions import OcrGenericMenuPopup
        popup = OcrGenericMenuPopup(items, parent=self)
        popup.show_at_pos(btn.mapToGlobal(pos))

    def _set_tab_password(self, tab_type: str, index: int) -> None:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        tab = tabs[index]
        dialog = _TabPasswordSetDialog(self)
        if dialog.exec():
            pwd = dialog.password()
            if pwd:
                import hashlib
                hashed = hashlib.sha256(pwd.encode("utf-8")).hexdigest()
                tab["password"] = hashed
                NotesMixin._mark_table_notes_tab_dirty(self, tab_type, index)
                self._save_table_notes_settings()
                QMessageBox.information(self, "成功", "已成功为此标签页启用密码锁保护。")
                self._refresh_tab_bars()

    def _remove_tab_password(self, tab_type: str, index: int) -> None:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        tab = tabs[index]
        hashed_current = tab.get("password", "")
        if hashed_current:
            dialog = _TabPasswordVerifyDialog(hashed_current, self)
            if not dialog.exec():
                return
        tab["password"] = ""
        NotesMixin._mark_table_notes_tab_dirty(self, tab_type, index)
        self._save_table_notes_settings()
        QMessageBox.information(self, "成功", "已成功关闭此标签页的密码锁保护。")
        self._refresh_tab_bars()

    def _set_tab_ima(self, tab_type: str, index: int) -> None:
        if tab_type != "note":
            return
        if index < 0 or index >= len(self._note_tabs):
            return
        tab = self._note_tabs[index]
        dialog = _ImaSettingsDialog(dict(tab.get("ima_config") or {}), str(tab.get("name", "记事本")), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_shared_ima_config_to_note_tabs(index, dialog.config())
            self._save_table_notes_settings()
            self._refresh_tab_bars()
            QMessageBox.information(self, "成功", "IMA 设置已保存。")

    def _apply_shared_ima_config_to_note_tabs(self, source_index: int, config: dict[str, Any]) -> None:
        if source_index < 0 or source_index >= len(getattr(self, "_note_tabs", [])):
            return
        shared_config = dict(config or {})
        self._note_tabs[source_index]["ima_config"] = shared_config
        NotesMixin._mark_table_notes_tab_dirty(self, "note", source_index)

        for index, tab in enumerate(self._note_tabs):
            if index == source_index:
                continue
            tab_config = dict(shared_config)
            tab_config["remote_note_id"] = ""
            tab_config["remote_note_title"] = ""
            tab_config["knowledge_media_id"] = ""
            tab_config["last_sync_at"] = ""
            tab["ima_config"] = tab_config
            NotesMixin._mark_table_notes_tab_dirty(self, "note", index)

    def _current_note_tab_snapshot(self, index: int) -> tuple[str, str]:
        if index < 0 or index >= len(self._note_tabs):
            return "", ""
        if index == self._active_note_tab and getattr(self, "_notes_editor", None) is not None:
            self._save_current_note_tab_data()
        tab = self._note_tabs[index]
        return str(tab.get("name", "记事本") or "记事本"), str(tab.get("html", "") or "")

    def _sync_tab_to_ima_note(self, index: int) -> None:
        self._append_tab_to_ima_note(index)

    def _append_tab_to_ima_note(self, index: int) -> None:
        self._run_ima_tab_action(index, "append_note")

    def _download_tab_from_ima_note(self, index: int) -> None:
        self._clear_pending_ima_auto_sync()
        self._run_ima_tab_action(index, "download_note")

    def _add_tab_to_ima_knowledge_base(self, index: int) -> None:
        self._run_ima_tab_action(index, "add_note_to_kb")

    def _show_ima_notification(self, message: str) -> None:
        self._send_tray_notification("IMA", message, 2600)

    def _run_ima_tab_action(self, index: int, action: str, extra: Optional[dict[str, Any]] = None, *, silent: bool = False) -> None:
        if index < 0 or index >= len(self._note_tabs):
            return
        tab = self._note_tabs[index]
        if action in {"append_note", "sync_note", "add_note_to_kb"}:
            if not self._should_sync_note_tab_to_ima(tab):
                if not silent:
                    self._show_ima_notification("当前是默认名称的笔记本，请重命名后再上传到 IMA。")
                return
        config = dict(tab.get("ima_config") or {})
        if not bool(config.get("enabled", False)):
            if not silent:
                self._show_ima_notification("请先在“设置IMA”中启用并保存配置。")
            return
        if action == "download_note":
            remote_note_id = str(config.get("remote_note_id", "") or "").strip()
            if not remote_note_id:
                if not silent:
                    self._show_ima_notification("请先在“设置IMA”中填写绑定笔记 ID。")
                return
            self._clear_pending_ima_auto_sync()
        if action == "add_note_to_kb":
            if not bool(config.get("knowledge_base_enabled", False)) or not str(config.get("knowledge_base_id", "") or "").strip():
                if not silent:
                    self._show_ima_notification("请先在“设置IMA”中启用知识库并填写知识库 ID。")
                return
        tab_name, note_html = self._current_note_tab_snapshot(index)
        # 用内容第一行作为 IMA 上传标题
        ima_title = self._extract_note_title_from_html(note_html, fallback=tab_name)
        worker = _ImaSyncWorker(action, config, ima_title, note_html, extra=extra)
        self._ima_sync_worker = worker

        def on_finished(success: bool, message: str, result: dict) -> None:
            try:
                result_config = dict(result or {})
                downloaded_content = result_config.pop("_downloaded_note_content", None)
                if 0 <= index < len(self._note_tabs):
                    if success and action == "download_note" and downloaded_content is not None:
                        self._apply_downloaded_ima_note_content(index, str(downloaded_content))
                    self._note_tabs[index]["ima_config"] = result_config
                    NotesMixin._mark_table_notes_tab_dirty(self, "note", index)
                    self._save_table_notes_settings()
                    self._refresh_tab_bars()
                if success:
                    if not silent:
                        QMessageBox.information(self, "IMA", message)
                else:
                    NotificationPopup.show_notification("IMA", message, duration_ms=4200)
            finally:
                self._ima_sync_worker = None

        worker.finished.connect(on_finished)
        worker.start()

    def _apply_downloaded_ima_note_content(self, index: int, content: str) -> None:
        if index < 0 or index >= len(self._note_tabs):
            return
        self._clear_pending_ima_auto_sync()
        latest_content = self._latest_ima_appended_note_content(content)
        html_content = self._ima_note_content_to_html(latest_content)
        self._note_tabs[index]["html"] = html_content
        NotesMixin._mark_table_notes_tab_dirty(self, "note", index)

        if index == getattr(self, "_active_note_tab", -1) and getattr(self, "_notes_editor", None) is not None:
            was_loading = bool(getattr(self, "_loading_table_notes", False))
            old_blocked = self._notes_editor.blockSignals(True)
            self._loading_table_notes = True
            try:
                self._notes_editor.setHtml(html_content)
            finally:
                self._notes_editor.blockSignals(old_blocked)
                self._loading_table_notes = was_loading

        pinned = getattr(self, "_active_pinned_notes", {}).get(index)
        if pinned is not None:
            try:
                old_blocked = pinned.blockSignals(True)
                pinned.setHtml(html_content)
                pinned.blockSignals(old_blocked)
            except Exception:
                pass

    @staticmethod
    def _last_html_heading_after_separator(text: str) -> int:
        latest_start = -1
        import re
        pattern = re.compile(
            r"<hr\b[^>]*>\s*(?P<header><h[1-6]\b[^>]*>)",
            flags=re.IGNORECASE | re.DOTALL,
        )
        for match in pattern.finditer(text):
            latest_start = match.start("header")
        return latest_start

    @staticmethod
    def _last_markdown_heading_after_separator(text: str) -> int:
        latest_start = -1
        import re
        lines = str(text or "").splitlines(keepends=True)
        offsets: list[int] = []
        cursor = 0
        for line in lines:
            offsets.append(cursor)
            cursor += len(line)

        for index, line in enumerate(lines):
            if not re.fullmatch(r"[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*(?:\r?\n)?", line):
                continue
            next_index = index + 1
            while next_index < len(lines) and not lines[next_index].strip():
                next_index += 1
            if next_index < len(lines) and re.match(r"[ \t]{0,3}#{1,6}[ \t]+\S", lines[next_index]):
                latest_start = offsets[next_index]
        return latest_start

    @staticmethod
    def _latest_ima_appended_note_content(content: str) -> str:
        text = str(content or "")
        if not text.strip():
            return text

        html_header_start = NotesMixin._last_html_heading_after_separator(text)
        markdown_header_start = NotesMixin._last_markdown_heading_after_separator(text)
        latest_start = max(html_header_start, markdown_header_start)
        if latest_start >= 0:
            return text[latest_start:].lstrip()
        return text

    @staticmethod
    def _ima_note_content_to_html(content: str) -> str:
        text = str(content or "")
        import re
        from PyQt6.QtGui import QTextDocument
        doc = QTextDocument()
        if re.search(r"<(?:html|body|p|div|h[1-6]|ul|ol|li|br|table|span)\b", text, flags=re.IGNORECASE):
            doc.setHtml(text)
        elif hasattr(doc, "setMarkdown"):
            doc.setMarkdown(text)
        else:
            doc.setPlainText(text)
        return doc.toHtml()


    def _pin_tab_content(self, tab_type: str, index: int) -> None:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        tab = tabs[index]
        hashed_pwd = tab.get("password", "")
        if hashed_pwd:
            dialog = _TabPasswordVerifyDialog(hashed_pwd, self)
            if not dialog.exec():
                return
        win = _PinnedTabWindow(None)
        win.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        ui = dict(getattr(self._app_settings, "ui", {}) or {})
        size = ui.get("pinned_window_size", [640, 480])
        if isinstance(size, (list, tuple)) and len(size) >= 2:
            win.resize(int(size[0]), int(size[1]))
        else:
            win.resize(640, 480)
        layout = QVBoxLayout(win)
        layout.setContentsMargins(2, 0, 2, 4)
        if tab_type == "table":
            tab = self._table_tabs[index]
            win.setWindowTitle(str(tab.get("name", "表格")))
            rows = tab.get("data", [])
            row_count = self._DEFAULT_TABLE_ROWS
            col_count = self._DEFAULT_TABLE_COLUMNS
            if isinstance(rows, list) and rows:
                row_count = len(rows)
                col_count = max(
                    self._DEFAULT_TABLE_COLUMNS,
                    max((len(row) for row in rows if isinstance(row, list)), default=self._DEFAULT_TABLE_COLUMNS),
                )
            table = _NotesTable(row_count, col_count, self)
            table._table_tab_index = index
            table.setItemDelegate(_ReturnDownDelegate(table))
            table.setHorizontalHeaderLabels([f"列 {i}" for i in range(1, col_count + 1)])
            table.verticalHeader().setVisible(True)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            table.verticalHeader().setDefaultSectionSize(28)
            self._apply_table_column_widths(table, tab.get("column_widths", []))
            table.horizontalHeader().sectionResized.connect(
                lambda idx, old_sz, new_sz, tab_idx=index, pinned_table=table: self._on_pinned_table_section_resized(tab_idx, pinned_table, idx, new_sz)
            )
            table.setStyleSheet("""
                QTableWidget {
                    background: white;
                    border: 1px solid #dfe4ec;
                    border-top-left-radius: 0px;
                    border-top-right-radius: 0px;
                    border-bottom-left-radius: 8px;
                    border-bottom-right-radius: 8px;
                    font-size: 13px;
                    gridline-color: #e5e7eb;
                    selection-background-color: #f1f5f9;
                }
                QTableWidget::item:selected {
                    background-color: #f1f5f9;
                }
                QHeaderView::section {
                    background: #f7f9fc;
                    border: none;
                    border-bottom: 1px solid #dfe4ec;
                    padding: 4px 8px;
                    font-size: 12px;
                    color: #6b7280;
                }
                QTableWidget QScrollBar:vertical {
                    width: 8px;
                    background: transparent;
                    margin: 0px 0px 0px 0px;
                    border: none;
                }
                QTableWidget QScrollBar::handle:vertical {
                    background: rgba(148, 163, 184, 0.25);
                    border-radius: 1px;
                    min-height: 26px;
                    margin: 0px 5px 0px 0px;
                }
                QTableWidget QScrollBar::handle:vertical:hover {
                    background: rgba(148, 163, 184, 0.55);
                    border-radius: 3px;
                    margin: 0px 1px 0px 0px;
                }
                QTableWidget QScrollBar::add-line:vertical,
                QTableWidget QScrollBar::sub-line:vertical {
                    height: 0px;
                }
                QTableWidget QScrollBar::add-page:vertical,
                QTableWidget QScrollBar::sub-page:vertical {
                    background: transparent;
                }
            """)
            self._active_pinned_tables[index] = table
            win.destroyed.connect(lambda _=None, idx=index: self._active_pinned_tables.pop(idx, None))
            if self._active_table_tab == index:
                self._sync_table_widget(self._notes_table, table)
            else:
                if isinstance(rows, list):
                    for r in range(min(row_count, len(rows))):
                        row = rows[r]
                        if not isinstance(row, list):
                            continue
                        for c in range(min(col_count, len(row))):
                            self._set_table_item_from_serialized_data(table, r, c, row[c])
            table.itemChanged.connect(lambda: self._on_pinned_table_changed(table, index))
            layout.addWidget(table)
        else:
            tab = self._note_tabs[index]
            win.setWindowTitle(str(tab.get("name", "记事本")))
            editor = _NotesEditor(self)
            editor.setFrameStyle(QFrame.Shape.NoFrame)
            editor.setStyleSheet("""
                QTextEdit {
                    background: white;
                    border: none;
                    border-top-left-radius: 0px;
                    border-top-right-radius: 0px;
                    border-bottom-left-radius: 8px;
                    border-bottom-right-radius: 8px;
                    font-size: 13px;
                    font-family: "Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI", sans-serif;
                    color: #111827;
                }
                QTextEdit QScrollBar:vertical {
                    width: 8px;
                    background: transparent;
                    margin: 0px 0px 0px 0px;
                    border: none;
                }
                QTextEdit QScrollBar::handle:vertical {
                    background: rgba(148, 163, 184, 0.25);
                    border-radius: 1px;
                    min-height: 26px;
                    margin: 0px 5px 0px 0px;
                }
                QTextEdit QScrollBar::handle:vertical:hover {
                    background: rgba(148, 163, 184, 0.55);
                    border-radius: 3px;
                    margin: 0px 1px 0px 0px;
                }
                QTextEdit QScrollBar::add-line:vertical,
                QTextEdit QScrollBar::sub-line:vertical {
                    height: 0px;
                }
                QTextEdit QScrollBar::add-page:vertical,
                QTextEdit QScrollBar::sub-page:vertical {
                    background: transparent;
                }
            """)
            editor.document().setDocumentMargin(10)
            self._active_pinned_notes[index] = editor
            win.destroyed.connect(lambda _=None, idx=index: self._active_pinned_notes.pop(idx, None))
            if self._active_note_tab == index:
                editor.setHtml(self._notes_editor.toHtml())
            else:
                editor.setHtml(str(tab.get("html", "") or ""))
            editor.textChanged.connect(lambda: self._on_pinned_note_changed(editor, index))
            layout.addWidget(editor)

        def _on_pinned_close(event):
            self._save_pinned_window_size(win.width(), win.height())
            QWidget.closeEvent(win, event)
        win.closeEvent = _on_pinned_close

        self._pinned_tab_windows.append(win)
        win.destroyed.connect(lambda _=None, w=win: self._pinned_tab_windows.remove(w) if w in self._pinned_tab_windows else None)
        win.show()
        win.raise_()
        win.activateWindow()

    def _on_pinned_note_changed(self, editor: QTextEdit, index: int) -> None:
        if getattr(self, "_loading_table_notes", False):
            return
        if index < 0 or index >= len(self._note_tabs):
            return
        html = editor.toHtml()
        if self._note_tabs[index].get("html") != html:
            self._note_tabs[index]["html"] = html
            NotesMixin._mark_table_notes_tab_dirty(self, "note", index)
        if self._active_note_tab == index:
            self._loading_table_notes = True
            try:
                self._notes_editor.blockSignals(True)
                if self._notes_editor.toHtml() != html:
                    self._notes_editor.setHtml(html)
                self._notes_editor.blockSignals(False)
            finally:
                self._loading_table_notes = False
            self._schedule_ima_auto_sync_for_active_note()
        else:
            if index >= 0 and index < len(self._note_tabs):
                tab = self._note_tabs[index]
                if self._should_sync_note_tab_to_ima(tab):
                    config = dict(tab.get("ima_config") or {})
                    if bool(config.get("enabled", False)) and bool(config.get("auto_sync_enabled", False)):
                        if str(config.get("client_id", "") or "").strip() and str(config.get("api_key", "") or "").strip():
                            self._ima_auto_sync_pending_note_index = index
                            try:
                                self._ima_auto_sync_timer.start(self._IMA_AUTO_SYNC_DELAY_MS)
                            except Exception:
                                self._run_pending_ima_auto_sync()
        self._schedule_table_notes_save()

    def _on_tab_double_click(self, event, btn: QPushButton) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            QPushButton.mouseDoubleClickEvent(btn, event)
            return
        tab_type = getattr(btn, "_tab_type", "")
        index = getattr(btn, "_tab_index", 0)
        self._rename_tab(tab_type, index)
        QPushButton.mouseDoubleClickEvent(btn, event)

    def _show_group_menu(self, tab_type: str, anchor: QToolButton) -> None:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        groups = sorted(list({t.get("group_name", "").strip() for t in tabs if t.get("group_name", "").strip()}))
        current_group = self._current_table_group if tab_type == "table" else self._current_note_group

        items = []
        items.append({"text": "全部", "group_name": "全部"})
        items.append({"text": "未分组", "group_name": ""})
        for g in groups:
            items.append({"text": g, "group_name": g})

        active_index = -1
        for idx, item in enumerate(items):
            if item.get("group_name") == current_group:
                active_index = idx
                break

        items.append({"text": "＋ 新建分组...", "action": "new"})
        if groups:
            items.append({"text": "⚙️ 管理分组...", "action": "manage"})

        def on_selected(item: dict[str, Any]) -> None:
            action = item.get("action")
            if action == "new":
                self._create_new_group(tab_type)
            elif action == "manage":
                self._manage_groups(tab_type)
            else:
                self._set_current_group(tab_type, item.get("group_name", "全部"))

        popup = RoundedListPopup(
            items,
            on_selected,
            active_index=active_index,
            empty_text="暂无分组",
            parent=self
        )
        self._tab_list_menu = popup
        popup.show_for_anchor(anchor, align_right=False, bounds_widget=self)

    def _move_tab_to_group(self, tab_type: str, index: int, group_name: str) -> None:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        if index < 0 or index >= len(tabs):
            return
        tabs[index]["group_name"] = group_name
        NotesMixin._mark_table_notes_tab_dirty(self, tab_type, index)
        self._save_table_notes_settings()

        current_group = self._current_table_group if tab_type == "table" else self._current_note_group
        if current_group != "全部" and current_group != group_name:
            filtered_indices = [idx for idx, t in enumerate(tabs) if current_group == "全部" or t.get("group_name", "") == current_group]
            if filtered_indices:
                active_index = self._active_table_tab if tab_type == "table" else self._active_note_tab
                if active_index not in filtered_indices:
                    self._on_tab_clicked(tab_type, filtered_indices[0])
            else:
                self._set_current_group(tab_type, "全部")
                return

        self._refresh_tab_bars()
        self._scroll_active_tab_into_view(tab_type)

    def _move_tab_to_new_group(self, tab_type: str, index: int) -> None:
        from deepcat.ui.main_window.compact import StyledInputDialog
        name, ok = StyledInputDialog.get_text(self, "移动至新分组", "请输入新分组名称:")
        if ok and name.strip():
            self._move_tab_to_group(tab_type, index, name.strip())

    def _save_current_table_tab_data(self) -> None:
        if not hasattr(self, "_table_tabs") or not self._table_tabs:
            return
        if self._active_table_tab < 0 or self._active_table_tab >= len(self._table_tabs):
            return
        rows = self._serialize_table_widget(self._notes_table)
        tab = self._table_tabs[self._active_table_tab]
        column_widths = self._table_column_widths(self._notes_table)
        if tab.get("data") != rows or tab.get("column_widths") != column_widths:
            tab["data"] = rows
            tab["column_widths"] = column_widths
            NotesMixin._mark_table_notes_tab_dirty(self, "table", self._active_table_tab)

    def _load_current_table_tab_data(self) -> None:
        if not hasattr(self, "_table_tabs") or not self._table_tabs:
            return
        if self._active_table_tab < 0 or self._active_table_tab >= len(self._table_tabs):
            return
        tab = self._table_tabs[self._active_table_tab]
        rows = tab.get("data", [])
        old_blocked = self._notes_table.blockSignals(True)
        try:
            self._notes_table.clearContents()
            if isinstance(rows, list) and len(rows) > 0:
                row_count = len(rows)
                col_count = max(
                    self._DEFAULT_TABLE_COLUMNS,
                    max((len(row) for row in rows if isinstance(row, list)), default=self._DEFAULT_TABLE_COLUMNS),
                )
                self._notes_table.setRowCount(row_count)
                self._notes_table.setColumnCount(col_count)
                self._notes_table.setHorizontalHeaderLabels([f"列 {i}" for i in range(1, col_count + 1)])
                for r in range(row_count):
                    row = rows[r]
                    if not isinstance(row, list):
                        continue
                    for c in range(min(col_count, len(row))):
                        self._set_table_item_from_serialized_data(self._notes_table, r, c, row[c])
            else:
                self._notes_table.setRowCount(self._DEFAULT_TABLE_ROWS)
                self._notes_table.setColumnCount(self._DEFAULT_TABLE_COLUMNS)
                self._notes_table.setHorizontalHeaderLabels([f"列 {i}" for i in range(1, self._DEFAULT_TABLE_COLUMNS + 1)])
        finally:
            self._notes_table.blockSignals(old_blocked)
        self._apply_table_column_widths(self._notes_table, tab.get("column_widths", []), sync_sum=True)
        self._refresh_table_notes_empty_state()

    def _save_current_note_tab_data(self) -> None:
        if not hasattr(self, "_note_tabs") or not self._note_tabs:
            return
        if self._active_note_tab < 0 or self._active_note_tab >= len(self._note_tabs):
            return
        html_content = self._notes_editor.toHtml()
        if self._note_tabs[self._active_note_tab].get("html") != html_content:
            self._note_tabs[self._active_note_tab]["html"] = html_content
            NotesMixin._mark_table_notes_tab_dirty(self, "note", self._active_note_tab)

    def _load_current_note_tab_data(self) -> None:
        if not hasattr(self, "_note_tabs") or not self._note_tabs:
            return
        tab = self._note_tabs[self._active_note_tab]
        html_content = str(tab.get("html", "") or "")
        self._notes_editor.setHtml(html_content)
        if not html_content.strip():
            fmt = QTextCharFormat()
            fmt.setFontPointSize(10.5)
            fmt.setFontFamily("Microsoft YaHei")
            self._notes_editor.setCurrentCharFormat(fmt)
        self._refresh_table_notes_empty_state()
        self._schedule_note_outline_refresh()

    def _merge_note_format(self, fmt: QTextCharFormat) -> None:
        cursor = self._notes_editor.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        cursor.mergeCharFormat(fmt)
        self._notes_editor.mergeCurrentCharFormat(fmt)
        self._notes_editor.setFocus()

    def _on_notes_current_format_changed(self, fmt: QTextCharFormat) -> None:
        try:
            if hasattr(self, "_note_bold_btn") and self._note_bold_btn is not None:
                weight = fmt.fontWeight()
                try:
                    if hasattr(weight, "value"):
                        weight_val = weight.value
                    else:
                        weight_val = int(weight)
                except Exception:
                    weight_val = 400
                is_bold = weight_val >= 700
                self._note_bold_btn.setChecked(is_bold)
            if hasattr(self, "_note_italic_btn") and self._note_italic_btn is not None:
                self._note_italic_btn.setChecked(fmt.fontItalic())
            if hasattr(self, "_note_underline_btn") and self._note_underline_btn is not None:
                self._note_underline_btn.setChecked(fmt.fontUnderline())
            if hasattr(self, "_note_strike_btn") and self._note_strike_btn is not None:
                self._note_strike_btn.setChecked(fmt.fontStrikeOut())
        except Exception:
            pass

    def _toggle_note_bold(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontWeight(700 if self._note_bold_btn.isChecked() else 400)
        self._merge_note_format(fmt)

    def _toggle_note_italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(bool(self._note_italic_btn.isChecked()))
        self._merge_note_format(fmt)

    def _toggle_note_underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(bool(self._note_underline_btn.isChecked()))
        self._merge_note_format(fmt)

    def _toggle_note_strike(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(bool(self._note_strike_btn.isChecked()))
        self._merge_note_format(fmt)

    def _choose_note_text_color(self) -> None:
        initial = getattr(self, "_last_text_color", QColor("#111827"))
        color = self._exec_color_dialog(initial, "选择文字颜色")
        if color.isValid():
            self._last_text_color = color
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            self._merge_note_format(fmt)
            if hasattr(self, "_note_text_color_widget"):
                self._note_text_color_widget.main_btn.setIcon(self._colored_svg_icon("icon_text_color.svg", color))

    def _choose_note_bg_color(self) -> None:
        initial = getattr(self, "_last_bg_color", QColor("#FFF3A3"))
        color = self._exec_color_dialog(initial, "选择底色")
        if color.isValid():
            self._last_bg_color = color
            fmt = QTextCharFormat()
            fmt.setBackground(color)
            self._merge_note_format(fmt)
            if hasattr(self, "_note_bg_color_widget"):
                self._note_bg_color_widget.main_btn.setIcon(self._colored_svg_icon("icon_bg_color.svg", color))

    def _apply_last_note_text_color(self) -> None:
        color = getattr(self, "_last_text_color", QColor("#dc2626"))
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        self._merge_note_format(fmt)

    def _apply_last_note_bg_color(self) -> None:
        color = getattr(self, "_last_bg_color", QColor("#FFF3A3"))
        fmt = QTextCharFormat()
        fmt.setBackground(color)
        self._merge_note_format(fmt)

    def _apply_note_block_style(self, text: str) -> None:
        fmt = QTextCharFormat()
        t = str(text or "")
        heading_level = 0
        if t == "标题 1":
            fmt.setFontPointSize(20)
            fmt.setFontWeight(800)
            heading_level = 1
        elif t == "标题 2":
            fmt.setFontPointSize(16)
            fmt.setFontWeight(750)
            heading_level = 2
        elif t == "引用":
            fmt.setFontItalic(True)
            fmt.setForeground(QColor("#5b6472"))
        elif t == "5号":
            fmt.setFontPointSize(10.5)
        elif t == "4号":
            fmt.setFontPointSize(14)
        elif t == "3号":
            fmt.setFontPointSize(16)
        elif t == "2号":
            fmt.setFontPointSize(22)
        else:
            fmt.setFontPointSize(12)
            fmt.setFontWeight(400)
            fmt.setFontItalic(False)

        editor = self._notes_editor
        original_cursor = editor.textCursor()
        format_cursor = QTextCursor(original_cursor)
        if not format_cursor.hasSelection():
            format_cursor.select(QTextCursor.SelectionType.BlockUnderCursor)

        block_format = QTextBlockFormat()
        block_format.setHeadingLevel(heading_level)
        format_cursor.beginEditBlock()
        try:
            format_cursor.mergeBlockFormat(block_format)
            format_cursor.mergeCharFormat(fmt)
        finally:
            format_cursor.endEditBlock()

        editor.setTextCursor(original_cursor)
        editor.setCurrentCharFormat(fmt)
        editor.setFocus()
        self._schedule_note_outline_refresh()

    def _insert_note_list(self, numbered: bool) -> None:
        cursor = self._notes_editor.textCursor()
        style = QTextListFormat.Style.ListDecimal if bool(numbered) else QTextListFormat.Style.ListDisc
        fmt = QTextListFormat()
        fmt.setStyle(style)
        # 使用标准的列表层级 1，避免设置为 0 带来的非预期底层渲染问题
        fmt.setIndent(1)

        cursor.beginEditBlock()
        text_list = cursor.createList(fmt)
        if text_list:
            # 遍历列表中的所有 block，确保它们的 blockFormat 缩进、左边距和首行缩进都为 0，防止自动缩进
            for i in range(text_list.count()):
                block = text_list.item(i)
                block_fmt = block.blockFormat()
                block_fmt.setIndent(0)
                block_fmt.setLeftMargin(0)
                block_fmt.setTextIndent(0)

                block_cursor = QTextCursor(block)
                block_cursor.setBlockFormat(block_fmt)
        cursor.endEditBlock()
        self._notes_editor.setTextCursor(cursor)

    def _clear_note_format(self) -> None:
        cursor = self._notes_editor.textCursor()
        # 若没有选择，则自动选取当前光标所在的整个段落块以进行格式净化
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)

        cursor.beginEditBlock()
        # 1. 净化字符格式并退回 5 号默认字号大小
        fmt = QTextCharFormat()
        fmt.setFontWeight(400)
        fmt.setFontItalic(False)
        fmt.setFontUnderline(False)
        fmt.setFontStrikeOut(False)
        fmt.setForeground(QColor("#000000"))
        fmt.setFontPointSize(10.5)
        cursor.setCharFormat(fmt)

        # 2. 遍历净化段落缩进与脱离任何有序/无序列表状态
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        runner = QTextCursor(cursor.document())
        runner.setPosition(start)

        while runner.position() <= end:
            block = runner.block()
            if not block.isValid():
                break

            # 清零段落的所有缩进、左边距和首行缩进
            block_fmt = block.blockFormat()
            block_fmt.setIndent(0)
            block_fmt.setLeftMargin(0)
            block_fmt.setTextIndent(0)
            runner.setBlockFormat(block_fmt)

            # 从列表中解绑并剥离
            text_list = block.textList()
            if text_list:
                text_list.remove(block)
                # 列表移出后再次重置 blockFormat 以防止列表格式残留导致多余缩进
                block_fmt = block.blockFormat()
                block_fmt.setIndent(0)
                block_fmt.setLeftMargin(0)
                block_fmt.setTextIndent(0)
                runner.setBlockFormat(block_fmt)

            # 下移一块段落，越界则退出
            if not runner.movePosition(QTextCursor.MoveOperation.NextBlock):
                break
            if runner.position() > end:
                break

        cursor.endEditBlock()
        self._notes_editor.setTextCursor(cursor)

    def _insert_note_table(self) -> None:
        cursor = self._notes_editor.textCursor()
        html = '<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;width:100%;"><tr><td></td><td></td><td></td></tr><tr><td></td><td></td><td></td></tr></table><br>'
        cursor.insertHtml(html)
        self._notes_editor.setTextCursor(cursor)

    def _insert_note_link(self) -> None:
        from deepcat.ui.main_window.compact import StyledInputDialog
        url, ok = StyledInputDialog.get_text(self, "插入链接", "链接地址:", text="https://")
        if not ok or not str(url).strip():
            return
        cursor = self._notes_editor.textCursor()
        text = cursor.selectedText() or str(url).strip()
        html = f'<a href="{str(url).strip()}">{text}</a>'
        cursor.insertHtml(html)
        self._notes_editor.setTextCursor(cursor)

    def _insert_note_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.gif *.bmp *.webp)")
        if not path:
            return
        try:
            attach_dir = get_image_output_dir() / "notes_attachments"
            attach_dir.mkdir(parents=True, exist_ok=True)
            import shutil, time
            ext = Path(path).suffix.lower() or ".png"
            name = f"img_{time.strftime('%Y%m%d_%H%M%S', time.localtime())}_{time.time_ns() % 1000000}{ext}"
            dst = attach_dir / name
            shutil.copy2(path, dst)
            if ext == ".png":
                try:
                    from PIL import Image
                    with Image.open(dst) as im:
                        im.save(dst)
                except Exception:
                    pass
            rel_path = str(dst).replace("\\", "/")
            cursor = self._notes_editor.textCursor()
            cursor.insertHtml(f'<img src="{rel_path}" style="max-width:300px;" /><br>')
            self._notes_editor.setTextCursor(cursor)
        except Exception:
            pass

    def _insert_note_code_block(self) -> None:
        cursor = self._notes_editor.textCursor()
        selected = cursor.selectedText()
        if selected:
            html = f'<pre style="background:#f4f4f5;padding:8px;border-radius:4px;font-family:monospace;">{selected}</pre><br>'
            cursor.insertHtml(html)
        else:
            cursor.insertHtml('<pre style="background:#f4f4f5;padding:8px;border-radius:4px;font-family:monospace;">code</pre><br>')
        self._notes_editor.setTextCursor(cursor)

    def _insert_note_hr(self) -> None:
        cursor = self._notes_editor.textCursor()
        cursor.insertHtml('<hr style="border:none;border-top:1px solid #e5e7eb;"><br>')
        self._notes_editor.setTextCursor(cursor)

    def _insert_note_blockquote(self) -> None:
        cursor = self._notes_editor.textCursor()
        selected = cursor.selectedText()
        if selected:
            html = f'<blockquote style="border-left:3px solid #d1d5db;padding-left:8px;color:#5b6472;margin:4px 0;">{selected}</blockquote><br>'
            cursor.insertHtml(html)
        else:
            cursor.insertHtml('<blockquote style="border-left:3px solid #d1d5db;padding-left:8px;color:#5b6472;margin:4px 0;">引用</blockquote><br>')
        self._notes_editor.setTextCursor(cursor)

    def _insert_note_attachment(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择附件", "", "所有文件 (*)")
        if not path:
            return
        try:
            attach_dir = get_image_output_dir() / "notes_attachments"
            attach_dir.mkdir(parents=True, exist_ok=True)
            import shutil, time
            src = Path(path)
            name = f"file_{time.strftime('%Y%m%d_%H%M%S', time.localtime())}_{time.time_ns() % 1000000}{src.suffix}"
            dst = attach_dir / name
            shutil.copy2(path, dst)
            rel_path = str(dst).replace("\\", "/")
            cursor = self._notes_editor.textCursor()
            cursor.insertHtml(f'<a href="{rel_path}">📎 {src.name}</a><br>')
            self._notes_editor.setTextCursor(cursor)
        except Exception:
            pass

    def _todo_group_label(self, item: dict[str, Any], now: QDateTime) -> str:
        if bool(item.get("completed", False)):
            return "已完成"
        due_at = self._todo_due_at(item)
        if due_at.isValid() and int(due_at.toSecsSinceEpoch()) <= int(now.toSecsSinceEpoch()):
            return "已到点"
        if bool(item.get("important", False)):
            return "重要"
        return "按时间"

    def _add_todo_group_header(self, title: str, count: int) -> None:
        header = QListWidgetItem()
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        header.setSizeHint(QSize(0, 36))
        self._todo_list.addItem(header)
        label = QLabel(f"{title}  {count}")
        label.setObjectName("TodoGroupHeader")
        label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        label.setMinimumHeight(28)
        label.setStyleSheet(
            "QLabel#TodoGroupHeader { color:#64748b; background:#f8fafc;"
            " border:1px solid #f1f5f9; border-radius:8px; padding:4px 10px;"
            " font-size:12px; font-weight:900; }"
        )
        self._todo_list.setItemWidget(header, label)

    def _search_table_notes(self, keyword: str) -> None:
        layout = self._search_card_notes.items_layout
        self._clear_layout(layout)

        matched_results = []
        if hasattr(self, "_table_tabs") and self._table_tabs:
            for idx, tab in enumerate(self._table_tabs):
                tab_name = tab.get("name", "")
                data = tab.get("data", [])
                found = False
                snippet = ""

                if keyword.lower() in tab_name.lower():
                    found = True
                    snippet = f"标签页名称: {tab_name}"
                else:
                    for row in data:
                        if isinstance(row, list):
                            for cell in row:
                                cell_str = str(cell or "")
                                if keyword.lower() in cell_str.lower():
                                    found = True
                                    snippet = f"单元格内容: ...{cell_str[:30]}..."
                                    break
                        if found:
                            break
                if found:
                    matched_results.append({
                        "type": "table",
                        "index": idx,
                        "name": tab_name,
                        "snippet": snippet
                    })

        if hasattr(self, "_note_tabs") and self._note_tabs:
            for idx, tab in enumerate(self._note_tabs):
                tab_name = tab.get("name", "")
                html = tab.get("html", "")
                found = False
                snippet = ""

                import re
                text_content = re.sub('<[^<]+?>', '', html)

                if keyword.lower() in tab_name.lower():
                    found = True
                    snippet = f"记事本名称: {tab_name}"
                elif keyword.lower() in text_content.lower():
                    found = True
                    pos = text_content.lower().find(keyword.lower())
                    start = max(0, pos - 15)
                    end = min(len(text_content), pos + len(keyword) + 15)
                    snippet = f"内容片段: ...{text_content[start:end]}..."

                if found:
                    matched_results.append({
                        "type": "note",
                        "index": idx,
                        "name": tab_name,
                        "snippet": snippet
                    })

        if not matched_results:
            self._search_card_notes.view_all_btn.setVisible(False)
            self._add_search_empty_state(
                layout,
                "没有匹配的表格记事",
                "换个关键词，或清空搜索。",
            )
            return

        self._search_card_notes.view_all_btn.setVisible(True)
        for res in matched_results[:8]:
            item_widget = self._create_notes_search_item_widget(res)
            layout.addWidget(item_widget)

    def _create_notes_search_item_widget(self, res: dict) -> QWidget:
        w = QWidget()
        w.setObjectName("SearchItem")
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setStyleSheet("""
            QWidget#SearchItem {
                border-radius: 6px;
                background-color: #f8fafc;
            }
            QWidget#SearchItem:hover {
                background-color: rgba(59, 130, 246, 0.05);
            }
        """)
        layout = QHBoxLayout(w)
        layout.setContentsMargins(12, 8, 12, 8)

        txt_lbl = QLabel()
        txt_lbl.setText(f"<b>[{'表格' if res['type'] == 'table' else '笔记'}] {res['name']}</b>  <span style='color: #64748b;'>{res['snippet']}</span>")
        txt_lbl.setTextFormat(Qt.TextFormat.RichText)
        txt_lbl.setStyleSheet("font-size: 13px; color: #334155;")
        txt_lbl.setMinimumWidth(10)
        layout.addWidget(txt_lbl, 1)

        jump_btn = QToolButton()
        jump_btn.setIcon(self._asset_icon("icon_action_paste.svg"))
        jump_btn.setToolTip("跳转并打开该标签页")
        jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        jump_btn.setStyleSheet("QToolButton { border: none; background: transparent; } QToolButton:hover { background: rgba(15, 23, 42, 0.05); border-radius: 4px; }")
        jump_btn.clicked.connect(lambda: self._jump_to_table_note(res["type"], res["index"]))
        layout.addWidget(jump_btn)
        return w

    def _jump_to_table_note(self, res_type: str, index: int) -> None:
        self._switch_page(5)
        if res_type == "table":
            if hasattr(self, "_table_notes_stack"):
                self._table_notes_stack.setCurrentIndex(0)
            self._on_tab_clicked("table", index)
        else:
            if hasattr(self, "_table_notes_stack"):
                self._table_notes_stack.setCurrentIndex(1)
            self._on_tab_clicked("note", index)

    def _on_close_tab(self, tab_type: str, index: int) -> None:
        tabs = self._table_tabs if tab_type == "table" else self._note_tabs
        if index < 0 or index >= len(tabs):
            return
        tab = tabs[index]
        item_label = "表格" if tab_type == "table" else "笔记"
        tab_name = str(tab.get("name", f"未命名{item_label}") or f"未命名{item_label}")
        single_tab_before_close = len(tabs) <= 1
        hashed_pwd = tab.get("password", "")
        if hashed_pwd:
            dialog = _TabPasswordVerifyDialog(hashed_pwd, self)
            if not dialog.exec():
                return
        try:
            if tab_type == "table":
                tab_name = str(self._table_tabs[index].get("name", "未命名表格"))
                if len(self._table_tabs) <= 1:
                    msg = f"当前仅剩一个表格标签页「{tab_name}」，关闭它将彻底清空该表格内的所有内容，且不可恢复。\n\n您确定要清空整张表格吗？"
                else:
                    msg = f"您确定要关闭并删除表格标签页「{tab_name}」吗？\n\n此操作将移除该表格页面的所有数据，且不可恢复。"
            else:
                tab_name = str(self._note_tabs[index].get("name", "未命名笔记"))
                if len(self._note_tabs) <= 1:
                    msg = f"当前仅剩一个笔记标签页「{tab_name}」，关闭它将彻底清空该笔记内的所有内容，且不可恢复。\n\n您确定要清空此笔记吗？"
                else:
                    msg = f"您确定要关闭并删除笔记标签页「{tab_name}」吗？\n\n此操作将移除该笔记页面的所有数据，且不可恢复。"
        except Exception:
            msg = "您确定要关闭此标签页吗？关闭后相关数据将彻底丢失且不可恢复。"

        from deepcat.ui.main_window.compact import StyledMessageBox
        reply = StyledMessageBox.question(
            self, "确认关闭", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted = self._delete_tabs_by_indices(tab_type, [index])
        if deleted:
            if tab_type == "table":
                message = f"已清空表格「{tab_name}」。" if single_tab_before_close else f"已删除表格「{tab_name}」。"
            else:
                message = f"已清空笔记「{tab_name}」。" if single_tab_before_close else f"已删除笔记「{tab_name}」。"
            self._show_table_notes_status(message, tone="success")

    def _create_or_get_float_note_index(self) -> int:
        for idx, tab in enumerate(self._note_tabs):
            if tab.get("name") == "随手记":
                return idx

        self._save_current_note_tab_data()
        new_index = len(self._note_tabs)
        ima_config = self._inherited_ima_config_for_new_note()
        ima_config["enabled"] = True
        ima_config["auto_sync_enabled"] = True
        self._note_tabs.append({
            "name": "随手记",
            "html": "",
            "group_name": "",
            "ima_config": ima_config,
        })
        self._active_note_tab = new_index
        self._note_tab_unlocked = True
        self._load_table_notes_view_safely(self._load_current_note_tab_data)
        self._refresh_tab_bars()
        self._save_table_notes_settings()
        self._scroll_active_tab_into_view("note")
        return new_index

    def _trigger_note_float(self) -> None:
        if not self._feature_enabled("table_notes"):
            return
        try:
            index = self._create_or_get_float_note_index()
            self._pin_tab_content("note", index)
        except Exception:
            get_logger().exception("触发随手记浮窗失败")


class _PinnedTabWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PinnedTabWindow")
        from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
        self.setStyleSheet(f"QWidget#PinnedTabWindow {{ background-color: {MAIN_WINDOW_BACKGROUND}; }}")

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
