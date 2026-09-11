from __future__ import annotations

import os
import time
import ctypes
from pathlib import Path
from typing import Any, Callable, Optional
from PyQt6.QtCore import Qt, QTimer, QRect, QSize, QEvent, QPoint, QDate, QDateTime, QTime, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QIcon, QColor, QCursor, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCalendarWidget,
    QComboBox,
    QDialog,
    QDateTimeEdit,
    QFileDialog,
    QFormLayout,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QMenu,
    QFrame,
    QCheckBox,
    QLineEdit,
)
from deepcat.ui.post_capture_actions import ModernPopupComboBox

from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND, MAIN_WINDOW_BACKGROUND_COLORREF, MODULE_BACKGROUND
from deepcat.ui.main_window.helpers import _date_key
from deepcat.ui.main_window.misc_widgets import _MultiSelectComboBox


def _todo_combo_popup_view_style() -> str:
    return """
        QAbstractItemView#TodoFilterPopup {
            background: #ffffff;
            color: #111827;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            outline: none;
            padding: 4px;
            selection-background-color: #f1f5f9;
            selection-color: #0f172a;
            font-size: 13px;
            font-weight: 700;
        }
        QAbstractItemView#TodoFilterPopup::item {
            min-height: 26px;
            padding: 4px 8px;
            border-radius: 6px;
        }
        QAbstractItemView#TodoFilterPopup::item:hover,
        QAbstractItemView#TodoFilterPopup::item:selected {
            background: #f1f5f9;
            color: #0f172a;
        }
    """


def _style_todo_combo_popup_view(combo: QComboBox) -> None:
    try:
        view = combo.view()
        view.setObjectName("TodoFilterPopup")
        view.setStyleSheet(_todo_combo_popup_view_style())
    except Exception:
        pass


class _TodoComboPopup(QWidget):
    optionSelected = pyqtSignal(int)

    def __init__(self, options: list[tuple[str, Any]], current_index: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("TodoComboPopup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        card = QFrame(self)
        card.setObjectName("TodoComboPopupCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(4, 4, 4, 4)
        card_layout.setSpacing(0)

        self._list = QListWidget(card)
        self._list.setObjectName("TodoFilterPopup")
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._list.viewport().setAutoFillBackground(False)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for index, (label, value) in enumerate(options):
            item = QListWidgetItem(str(label))
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setData(Qt.ItemDataRole.UserRole + 1, value)
            item.setSizeHint(QSize(90, 34))
            self._list.addItem(item)
            if index == int(current_index):
                self._list.setCurrentItem(item)
        self._list.itemClicked.connect(self._on_item_clicked)
        card_layout.addWidget(self._list)
        layout.addWidget(card)
        self.setStyleSheet(
            f"""
            QWidget#TodoComboPopup {{
                background: transparent;
                border: none;
            }}
            QFrame#TodoComboPopupCard {{
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
            }}
            {_todo_combo_popup_view_style()}
            QAbstractItemView#TodoFilterPopup {{
                background: transparent;
                border: none;
                padding: 0;
            }}
            """
        )

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self.optionSelected.emit(int(item.data(Qt.ItemDataRole.UserRole) or 0))
        self.close()

    def show_below(self, anchor: QWidget, *, width: int) -> None:
        row_height = 34
        visible_rows = max(1, min(self._list.count(), 8))
        self.setFixedSize(max(72, int(width)), visible_rows * row_height + 8)
        pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        try:
            screen = QGuiApplication.screenAt(pos)
            geo = screen.availableGeometry() if screen is not None else QGuiApplication.primaryScreen().availableGeometry()
            x = min(max(pos.x(), geo.left() + 4), geo.right() - self.width() - 4)
            y = pos.y()
            if y + self.height() > geo.bottom() - 4:
                y = anchor.mapToGlobal(QPoint(0, -self.height() - 4)).y()
            self.move(int(x), int(max(geo.top() + 4, y)))
        except Exception:
            self.move(pos)
        self.show()
        self.raise_()


class _TodoPopupComboBox(QComboBox):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._todo_popup: Optional[_TodoComboPopup] = None

    def showPopup(self) -> None:
        options = [(self.itemText(i), self.itemData(i)) for i in range(self.count())]
        if not options:
            return
        try:
            old_popup = getattr(self, "_todo_popup", None)
            if old_popup is not None:
                old_popup.close()
        except Exception:
            pass
        popup = _TodoComboPopup(options, self.currentIndex(), self)
        self._todo_popup = popup

        def select_index(index: int) -> None:
            if 0 <= int(index) < self.count():
                self.setCurrentIndex(int(index))

        popup.optionSelected.connect(select_index)
        popup.destroyed.connect(lambda *_: setattr(self, "_todo_popup", None))
        popup.show_below(self, width=max(self.width(), self.minimumSizeHint().width()))

    def hidePopup(self) -> None:
        popup = getattr(self, "_todo_popup", None)
        if popup is not None:
            popup.close()


class _TodoMonthPopup(QWidget):
    monthSelected = pyqtSignal(int)

    _MONTH_NAMES = [
        "一月", "二月", "三月", "四月", "五月", "六月",
        "七月", "八月", "九月", "十月", "十一月", "十二月",
    ]

    def __init__(self, current_month: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("TodoMonthPopup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        card = QFrame(self)
        card.setObjectName("TodoMonthPopupCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(4, 4, 4, 4)
        card_layout.setSpacing(0)

        self._list = QListWidget(card)
        self._list.setObjectName("TodoFilterPopup")
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._list.viewport().setAutoFillBackground(False)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for index, label in enumerate(self._MONTH_NAMES, start=1):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setSizeHint(QSize(70, 34))
            self._list.addItem(item)
            if index == int(current_month):
                self._list.setCurrentItem(item)
        self._list.itemClicked.connect(self._on_item_clicked)
        card_layout.addWidget(self._list)
        layout.addWidget(card)
        self.setFixedSize(78, 416)
        self.setStyleSheet(
            f"""
            QWidget#TodoMonthPopup {{
                background: transparent;
                border: none;
            }}
            QFrame#TodoMonthPopupCard {{
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
            }}
            {_todo_combo_popup_view_style()}
            QAbstractItemView#TodoFilterPopup {{
                background: transparent;
                border: none;
                padding: 0;
            }}
            """
        )

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        month = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        if 1 <= month <= 12:
            self.monthSelected.emit(month)
        self.close()

    def show_below(self, anchor: QWidget) -> None:
        pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        try:
            screen = QGuiApplication.screenAt(pos)
            geo = screen.availableGeometry() if screen is not None else QGuiApplication.primaryScreen().availableGeometry()
            x = min(max(pos.x(), geo.left() + 4), geo.right() - self.width() - 4)
            y = pos.y()
            if y + self.height() > geo.bottom() - 4:
                y = anchor.mapToGlobal(QPoint(0, -self.height() - 4)).y()
            self.move(int(x), int(max(geo.top() + 4, y)))
        except Exception:
            self.move(pos)
        self.show()
        self.raise_()


class _TodoCalendarWidget(QCalendarWidget):
    _POPUP_STYLE = """
        QMenu {
            background: #ffffff;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            padding: 4px;
            color: #111827;
            font-family: 'Microsoft YaHei', 'Segoe UI', system-ui;
            font-size: 13px;
            font-weight: 700;
        }
        QMenu::item {
            min-height: 26px;
            padding: 4px 8px;
            border-radius: 6px;
            background: transparent;
        }
        QMenu::item:hover,
        QMenu::item:selected {
            background: #f1f5f9;
            color: #0f172a;
        }
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._todo_dates: set[str] = set()
        self._tooltips: dict[str, str] = {}
        self._date_status: dict[str, dict[str, int]] = {}
        self._date_rects: dict[str, QRect] = {}
        self._hover_key: str = ""
        self._month_popup: Optional[_TodoMonthPopup] = None
        from deepcat.ui.post_capture_actions import SmoothToolTip
        self._tooltip = SmoothToolTip()
        self._today_button = QToolButton(self)
        self._today_button.setObjectName("TodoCalendarTodayButton")
        self._today_button.setText("今天")
        self._today_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._today_button.setFixedSize(46, 24)
        self._today_button.clicked.connect(self._go_today)
        self._today_button.setStyleSheet(
            "QToolButton#TodoCalendarTodayButton {"
            " background:#f1f5f9; color:#334155; border:1px solid #e2e8f0;"
            " border-radius:12px; font-size:12px; font-weight:800; padding:0px 8px;"
            "}"
            "QToolButton#TodoCalendarTodayButton:hover { background:#e2e8f0; color:#0f172a; }"
        )
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        QTimer.singleShot(0, self._install_hover_filter)
        QTimer.singleShot(0, self._style_calendar_popups)

    def set_todo_summary(
        self,
        todo_dates: set[str],
        tooltips: dict[str, str],
        date_status: Optional[dict[str, dict[str, int]]] = None,
    ) -> None:
        self._todo_dates = set(todo_dates)
        self._tooltips = dict(tooltips)
        self._date_status = {
            str(key): {
                "active": int((value or {}).get("active", 0) or 0),
                "completed": int((value or {}).get("completed", 0) or 0),
                "overdue": int((value or {}).get("overdue", 0) or 0),
            }
            for key, value in dict(date_status or {}).items()
        }
        self._install_hover_filter()
        self.updateCells()
        try:
            view = self.findChild(QAbstractItemView)
            if view is not None and view.viewport() is not None:
                view.viewport().update()
        except Exception:
            pass

    def _go_today(self) -> None:
        today = QDate.currentDate()
        self.setSelectedDate(today)
        try:
            self.showSelectedDate()
        except Exception:
            pass

    def _shift_months(self, months: int) -> None:
        try:
            current = QDate(int(self.yearShown()), int(self.monthShown()), 1)
            if not current.isValid():
                return
            target_month = current.addMonths(int(months))
            selected = self.selectedDate()
            selected_day = int(selected.day()) if selected.isValid() else 1
            target_day = max(1, min(selected_day, int(target_month.daysInMonth())))
            target_date = QDate(target_month.year(), target_month.month(), target_day)
            self.setSelectedDate(target_date)
            self.setCurrentPage(target_month.year(), target_month.month())
            self._style_calendar_popups()
            self.hide_tooltip()
            self.updateCells()
        except Exception:
            pass

    def _position_today_button(self) -> None:
        try:
            nav = self.findChild(QWidget, "qt_calendar_navigationbar")
            next_btn = self.findChild(QToolButton, "qt_calendar_nextmonth")
            if nav is None:
                self._today_button.move(max(8, self.width() - 84), 7)
            elif next_btn is not None:
                x = max(nav.x() + 8, nav.x() + next_btn.x() - self._today_button.width() - 8)
                y = nav.y() + max(2, int((nav.height() - self._today_button.height()) / 2))
                self._today_button.move(x, y)
            else:
                self._today_button.move(max(nav.x() + 8, nav.right() - self._today_button.width() - 52), nav.y() + 4)
            self._today_button.raise_()
            self._today_button.show()
        except Exception:
            pass

    def hide_tooltip(self) -> None:
        try:
            self._tooltip.hide()
        except Exception:
            pass
        self._hover_key = ""

    def _install_hover_filter(self) -> None:
        try:
            view = self.findChild(QAbstractItemView)
            if view is not None and view.viewport() is not None:
                view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                view.setFrameShape(QFrame.Shape.NoFrame)
                focus_attr = getattr(Qt.WidgetAttribute, "WA_MacShowFocusRect", None)
                if focus_attr is not None:
                    view.setAttribute(focus_attr, False)
                view.viewport().setMouseTracking(True)
                view.viewport().setFocusPolicy(Qt.FocusPolicy.NoFocus)
                if not bool(view.viewport().property("todoHoverFilterInstalled")):
                    view.viewport().installEventFilter(self)
                    view.viewport().setProperty("todoHoverFilterInstalled", True)
        except Exception:
            pass
        try:
            nav = self.findChild(QWidget, "qt_calendar_navigationbar")
            if nav is not None:
                nav.setToolTip("双击空白回到今日")
        except Exception:
            pass
        try:
            month_btn = self.findChild(QToolButton, "qt_calendar_monthbutton")
            if month_btn is not None:
                month_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                if not bool(month_btn.property("todoMonthPopupFilterInstalled")):
                    month_btn.installEventFilter(self)
                    month_btn.setProperty("todoMonthPopupFilterInstalled", True)
        except Exception:
            pass
        self._position_today_button()

    def _style_calendar_popups(self) -> None:
        try:
            month_btn = self.findChild(QToolButton, "qt_calendar_monthbutton")
            if month_btn is not None and month_btn.menu() is not None:
                month_btn.menu().setStyleSheet(self._POPUP_STYLE)
        except Exception:
            pass
        try:
            for menu in self.findChildren(QMenu):
                menu.setStyleSheet(self._POPUP_STYLE)
        except Exception:
            pass

    def _show_month_popup(self) -> None:
        try:
            old_popup = getattr(self, "_month_popup", None)
            if old_popup is not None:
                old_popup.close()
        except Exception:
            pass
        month_btn = self.findChild(QToolButton, "qt_calendar_monthbutton")
        if month_btn is None:
            return
        popup = _TodoMonthPopup(int(self.monthShown()), self)
        self._month_popup = popup

        def on_month_selected(month: int) -> None:
            try:
                year = int(self.yearShown())
                selected = self.selectedDate()
                selected_day = int(selected.day()) if selected.isValid() else 1
                target_base = QDate(year, int(month), 1)
                if not target_base.isValid():
                    return
                target_day = max(1, min(selected_day, int(target_base.daysInMonth())))
                self.setSelectedDate(QDate(year, int(month), target_day))
                self.setCurrentPage(year, int(month))
                self.hide_tooltip()
                self._repaint_cells_without_qt_artifacts()
                QTimer.singleShot(0, self._install_hover_filter)
            except Exception:
                pass

        popup.monthSelected.connect(on_month_selected)
        popup.destroyed.connect(lambda *_: setattr(self, "_month_popup", None))
        popup.show_below(month_btn)

    def eventFilter(self, obj, event) -> bool:
        month_btn = self.findChild(QToolButton, "qt_calendar_monthbutton")
        if obj is month_btn:
            if event.type() == QEvent.Type.MouseButtonPress:
                try:
                    if event.button() == Qt.MouseButton.LeftButton:
                        self._show_month_popup()
                        event.accept()
                        return True
                except Exception:
                    self._show_month_popup()
                    return True
            if event.type() in {QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick}:
                event.accept()
                return True
        if event.type() == QEvent.Type.MouseMove:
            key = ""
            global_pos = QCursor.pos()
            try:
                pos = event.position().toPoint()
            except Exception:
                try:
                    pos = event.pos()
                except Exception:
                    pos = QPoint()
            try:
                if isinstance(obj, QWidget):
                    global_pos = obj.mapToGlobal(pos)
                    view = self.findChild(QAbstractItemView)
                    viewport = view.viewport() if view is not None else None
                    if viewport is not None and obj is not viewport:
                        pos = viewport.mapFromGlobal(global_pos)
            except Exception:
                pass
            for date_key, rect in self._date_rects.items():
                if rect.contains(pos):
                    key = date_key
                    break
            if key != self._hover_key:
                self._hover_key = key
                text = self._tooltips.get(key, "")
                if text:
                    try:
                        global_pos = event.globalPosition().toPoint()
                    except Exception:
                        try:
                            global_pos = event.globalPos()
                        except Exception:
                            pass
                    self._tooltip.show_text(
                        text,
                        global_pos,
                        direction="above",
                        alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        max_width=280,
                        max_height=180,
                    )
                else:
                    self._tooltip.hide()
        elif event.type() == QEvent.Type.Leave:
            self._hover_key = ""
            self._tooltip.hide()
        return super().eventFilter(obj, event)

    def mouseDoubleClickEvent(self, event) -> None:
        try:
            nav = self.findChild(QWidget, "qt_calendar_navigationbar")
            if nav is not None and nav.geometry().contains(event.pos()):
                self._go_today()
                return
        except Exception:
            pass
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        self._style_calendar_popups()
        super().mousePressEvent(event)
        self._repaint_cells_without_qt_artifacts()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._repaint_cells_without_qt_artifacts()

    def wheelEvent(self, event) -> None:
        try:
            delta = int(event.angleDelta().y())
        except Exception:
            delta = 0
        if delta:
            self._shift_months(-1 if delta > 0 else 1)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        key = int(event.key())
        if key == int(Qt.Key.Key_Left):
            self._shift_months(-1)
            event.accept()
            return
        if key == int(Qt.Key.Key_Right):
            self._shift_months(1)
            event.accept()
            return
        if key in (int(Qt.Key.Key_Home), int(Qt.Key.Key_T)):
            self._go_today()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_today_button()

    def _repaint_cells_without_qt_artifacts(self) -> None:
        try:
            self.updateCells()
            view = self.findChild(QAbstractItemView)
            if view is not None and view.viewport() is not None:
                view.viewport().update()
        except Exception:
            pass

    def paintCell(self, painter: QPainter, rect: QRect, date: QDate) -> None:
        painter.save()
        try:
            key = _date_key(date)
            self._date_rects[key] = QRect(rect)
            current_month = date.month() == self.monthShown() and date.year() == self.yearShown()
            is_today = date == QDate.currentDate()
            is_selected = date == self.selectedDate()
            status = self._date_status.get(key, {})
            active_count = int(status.get("active", 0) or 0)
            completed_count = int(status.get("completed", 0) or 0)
            overdue_count = int(status.get("overdue", 0) or 0)
            total_count = active_count + completed_count + overdue_count
            has_todo = total_count > 0 or key in self._todo_dates

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.fillRect(rect, QColor("#f8fafc"))

            cell_side = max(22, min(rect.width(), rect.height()) - 10)
            cell_height = min(rect.height() - 4, cell_side + 8)
            cell_box = QRect(
                rect.center().x() - cell_side // 2,
                rect.center().y() - cell_height // 2,
                cell_side,
                cell_height,
            )

            if has_todo and current_month:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(30, 41, 59, 10))
                painter.drawRoundedRect(cell_box, 9, 9)

            if is_selected:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(37, 99, 235, 30))
                painter.drawRoundedRect(cell_box.adjusted(-2, -2, 2, 2), 9, 9)
                painter.setPen(QPen(QColor("#2563eb"), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(cell_box, 8, 8)

            day_rect = QRect(rect)
            circle = min(24, max(20, min(rect.width(), rect.height()) - 12))
            cx = rect.center().x()
            cy = rect.center().y()
            circle_rect = QRect(cx - circle // 2, cy - circle // 2, circle, circle)
            if is_today:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#64748b"))
                painter.drawEllipse(circle_rect)

            painter.setPen(QColor("white") if is_today else QColor("#1f2937" if current_month else "#b8c1ce"))
            font = painter.font()
            font.setBold(is_today or is_selected or has_todo)
            painter.setFont(font)
            if is_today:
                painter.drawText(circle_rect, Qt.AlignmentFlag.AlignCenter, str(date.day()))
            else:
                painter.drawText(day_rect, Qt.AlignmentFlag.AlignCenter, str(date.day()))

            if total_count > 1:
                count_text = str(min(total_count, 99))
                badge_w = 16 if total_count < 10 else 22
                badge_rect = QRect(cell_box.right() - badge_w + 2, cell_box.top() - 2, badge_w, 16)
                badge_color = QColor("#ef4444") if (active_count or overdue_count) else QColor("#16a34a")
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(badge_color)
                painter.drawRoundedRect(badge_rect, 8, 8)
                count_font = painter.font()
                count_font.setBold(True)
                count_font.setPointSize(max(7, count_font.pointSize() - 2))
                painter.setFont(count_font)
                painter.setPen(QColor("white"))
                painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, count_text)

            dot_specs: list[QColor] = []
            if overdue_count:
                dot_specs.append(QColor("#2f3d56"))
            if active_count:
                dot_specs.append(QColor("#ef4444"))
            if completed_count:
                dot_specs.append(QColor("#16a34a"))
            if not dot_specs and has_todo:
                dot_specs.append(QColor("#64748b"))
            if dot_specs:
                dot_size = 5
                gap = 4
                total_w = len(dot_specs) * dot_size + max(0, len(dot_specs) - 1) * gap
                x = rect.center().x() - total_w // 2
                y = cell_box.bottom() - dot_size - 5
                painter.setPen(Qt.PenStyle.NoPen)
                for color in dot_specs:
                    painter.setBrush(color)
                    painter.drawEllipse(x, y, dot_size, dot_size)
                    x += dot_size + gap
        finally:
            painter.restore()


class _TodoListItemWidget(QWidget):
    editRequested = pyqtSignal(str)
    deleteRequested = pyqtSignal(str)
    completeRequested = pyqtSignal(str, bool)
    snoozeRequested = pyqtSignal(str, str, int)
    selectionChanged = pyqtSignal(str, bool)

    def __init__(
        self,
        item: dict[str, Any],
        *,
        time_text: str,
        status_text: str,
        edit_icon: QIcon,
        delete_icon: QIcon,
        batch_mode: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._item_id = str(item.get("id", ""))
        self._occurrence_key = str(item.get("_occurrence_date", "") or "")
        self._edit_icon = edit_icon
        self._delete_icon = delete_icon
        self._struck_off = bool(item.get("struck_off", False))
        self._batch_mode = bool(batch_mode)
        self._completed = bool(item.get("completed", False))
        is_important = bool(item.get("important", False))
        self.setObjectName("TodoListItemWidget")
        self.setProperty("important", bool(is_important and not self._completed and not self._struck_off))
        self.setProperty("actionsActive", False)
        self.setMouseTracking(True)

        root = QHBoxLayout(self)
        root.setContentsMargins(2, 4, 8, 4)
        root.setSpacing(8)

        if self._batch_mode:
            self._checkbox = QCheckBox()
            self._checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
            self._checkbox.stateChanged.connect(lambda state: self.selectionChanged.emit(self._item_id, bool(state)))
            root.addWidget(self._checkbox)
        else:
            self._checkbox = QCheckBox()
            self._checkbox.setObjectName("TodoCompleteCheck")
            self._checkbox.setToolTip("标记完成")
            self._checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
            self._checkbox.setChecked(self._completed)
            self._checkbox.stateChanged.connect(lambda state: self.completeRequested.emit(self._item_id, bool(state)))
            root.addWidget(self._checkbox)

        self._text_box = QWidget()
        self._text_box.setMinimumWidth(0)
        self._text_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        text_layout = QVBoxLayout(self._text_box)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        title = str(item.get("title", "未命名待办"))
        title_row = QWidget()
        title_row_layout = QHBoxLayout(title_row)
        title_row_layout.setContentsMargins(0, 0, 0, 0)
        title_row_layout.setSpacing(6)
        self._title = QLabel(f"{time_text}  {title}")
        self._title.setObjectName("TodoItemTitle")
        self._title.setWordWrap(False)
        self._title.setMinimumWidth(0)
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if is_important and not self._struck_off:
            self._title.setStyleSheet("color: #b91c1c; font-weight: 900;")
        title_row_layout.addWidget(self._title, 1)
        if is_important:
            self._important_badge = QLabel("重要")
            self._important_badge.setObjectName("TodoImportantBadge")
            self._important_badge.setFixedSize(40, 20)
            self._important_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title_row_layout.addWidget(self._important_badge)
        else:
            self._important_badge = None
        repeat_label_text = self._repeat_label_text(item)
        if repeat_label_text:
            self._repeat_badge = QLabel(repeat_label_text)
            self._repeat_badge.setObjectName("TodoRepeatBadge")
            self._repeat_badge.setFixedSize(40, 20)
            self._repeat_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title_row_layout.addWidget(self._repeat_badge)
        else:
            self._repeat_badge = None
        text_layout.addWidget(title_row)
        content = str(item.get("content", "")).strip()
        if content:
            self._body = QLabel(content)
            self._body.setObjectName("TodoItemBody")
            self._body.setWordWrap(False)
            self._body.setMinimumWidth(0)
            self._body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            text_layout.addWidget(self._body)
        else:
            self._body = None
        status_text = str(status_text or "").strip()
        if status_text:
            self._status = QLabel(status_text)
            self._status.setObjectName("TodoItemStatus")
            self._status.setWordWrap(False)
            self._status.setMinimumWidth(0)
            self._status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            if status_text.startswith("已过期"):
                self._status.setProperty("tone", "overdue")
                self._status.setStyleSheet("color:#475569; font-weight:800;")
            elif status_text.startswith("已提醒"):
                self._status.setProperty("tone", "reminded")
                self._status.setStyleSheet("color:#64748b; font-weight:800;")
            elif status_text.startswith("已完成"):
                self._status.setProperty("tone", "completed")
                self._status.setStyleSheet("color:#16a34a; font-weight:800;")
            text_layout.addWidget(self._status)
        else:
            self._status = None
        root.addWidget(self._text_box, 1)

        self._action_box = QWidget(self)
        self._edit_btn = QToolButton()
        self._edit_btn.setObjectName("TodoItemIconButton")
        self._edit_btn.setIcon(edit_icon)
        self._edit_btn.setToolTip("编辑待办")
        self._edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_btn.setFixedSize(26, 26)
        self._edit_btn.clicked.connect(lambda *_: self.editRequested.emit(self._item_id))
        self._snooze_btn = QToolButton()
        self._snooze_btn.setObjectName("TodoItemIconButton")
        self._snooze_btn.setText("顺延")
        self._snooze_btn.setToolTip("选择顺延时间")
        self._snooze_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._snooze_btn.setFixedSize(42, 26)
        self._snooze_btn.clicked.connect(self._show_snooze_menu)
        self._delete_btn = QToolButton()
        self._delete_btn.setObjectName("TodoItemIconButton")
        self._delete_btn.setIcon(delete_icon)
        self._delete_btn.setToolTip("删除待办")
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.setFixedSize(26, 26)
        self._delete_btn.clicked.connect(lambda *_: self.deleteRequested.emit(self._item_id))
        action_layout = QHBoxLayout(self._action_box)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(4)
        action_layout.addWidget(self._edit_btn)
        action_layout.addWidget(self._snooze_btn)
        action_layout.addWidget(self._delete_btn)
        self._action_box.setFixedSize(self._action_box.sizeHint())
        self.setMinimumHeight(max(56, int(self.sizeHint().height())))
        self._actions_visible = True
        self._set_actions_visible(False)
        self._apply_struck_off_style()

    def item_id(self) -> str:
        return self._item_id

    def hide_tooltip(self) -> None:
        try:
            self._tooltip.hide()
        except Exception:
            pass

    def setChecked(self, checked: bool) -> None:
        if self._checkbox is not None:
            self._checkbox.setChecked(bool(checked))

    def isChecked(self) -> bool:
        if self._checkbox is not None:
            return bool(self._checkbox.isChecked())
        return False

    def is_completed(self) -> bool:
        return bool(self._completed)

    @staticmethod
    def _repeat_label_text(item: dict[str, Any]) -> str:
        repeat = str(item.get("repeat", "none") or "none").strip().lower()
        if repeat == "daily":
            return "每天"
        if repeat == "weekly":
            return "每周"
        if repeat == "monthly":
            return "每月"
        return ""

    def _show_snooze_menu(self) -> None:
        from deepcat.ui.post_capture_actions import OcrGenericMenuPopup

        items = [
            ("10 分钟", lambda: self.snoozeRequested.emit(self._item_id, self._occurrence_key, 10), True),
            ("30 分钟", lambda: self.snoozeRequested.emit(self._item_id, self._occurrence_key, 30), True),
            ("1 小时", lambda: self.snoozeRequested.emit(self._item_id, self._occurrence_key, 60), True),
        ]
        popup = OcrGenericMenuPopup(items, parent=self._snooze_btn, match_parent_width=False, active_indicator="background")
        popup.show_at_pos(self._snooze_btn.mapToGlobal(QPoint(0, self._snooze_btn.height() + 4)))

    def _apply_struck_off_style(self) -> None:
        title_font = self._title.font()
        title_font.setStrikeOut(bool(self._struck_off or self._completed))
        self._title.setFont(title_font)
        if self._struck_off or self._completed:
            self._title.setStyleSheet("color: #94a3b8;")
            if self._title.parentWidget() is not None:
                for child in self._title.parentWidget().findChildren(QLabel, "TodoItemBody"):
                    child.setStyleSheet("color: #94a3b8;")
        else:
            self._title.setStyleSheet("")
            if getattr(self, "_important_badge", None) is not None:
                self._title.setStyleSheet("color: #b91c1c; font-weight: 900;")
            if self._title.parentWidget() is not None:
                for child in self._title.parentWidget().findChildren(QLabel, "TodoItemBody"):
                    child.setStyleSheet("")

    def _set_actions_visible(self, visible: bool) -> None:
        active = bool(visible) and not self._batch_mode
        if bool(getattr(self, "_actions_visible", False)) == active:
            return
        self._actions_visible = active
        self.setProperty("actionsActive", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self._position_action_box()
        enabled = not self._batch_mode
        self._edit_btn.setEnabled(enabled)
        self._snooze_btn.setEnabled(enabled)
        self._delete_btn.setEnabled(enabled)
        self._action_box.setVisible(active)
        if active:
            self._action_box.raise_()

    def _position_action_box(self) -> None:
        try:
            self._action_box.move(
                max(0, self.width() - self._action_box.width() - 8),
                max(0, int((self.height() - self._action_box.height()) / 2)),
            )
        except Exception:
            pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_action_box()

    def enterEvent(self, event) -> None:
        self._set_actions_visible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._set_actions_visible(False)
        self.hide_tooltip()
        super().leaveEvent(event)


class _TodoEditDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        selected_date: QDate,
        item: Optional[dict[str, Any]] = None,
        existing_items: Optional[list[dict[str, Any]]] = None,
        on_validation_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._item = dict(item or {})
        self._existing_items = list(existing_items or [])
        self._on_validation_error = on_validation_error
        self._styled_datetime_calendars: set[int] = set()
        self.setWindowTitle("编辑待办" if self._item else "新建待办")
        self.setModal(True)
        self.setMinimumWidth(430)
        base_font = self.font()
        base_font.setPointSize(10)
        self.setFont(base_font)
        base_date = selected_date if selected_date.isValid() else QDate.currentDate()
        start_dt = QDateTime(base_date, QTime(9, 0))
        if base_date == QDate.currentDate():
            now = QDateTime.currentDateTime().addSecs(30 * 60)
            start_dt = QDateTime(now.date(), QTime(now.time().hour(), 0)).addSecs(60 * 60)
        if self._item:
            start_dt = self._from_iso(self._item.get("start"), start_dt)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        form_card = QFrame()
        form_card.setObjectName("TodoDialogCard")
        form = QFormLayout(form_card)
        form.setContentsMargins(14, 12, 14, 12)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self._title = QLineEdit()
        self._title.setPlaceholderText("标题")
        self._title.setText(str(self._item.get("title", "")))
        form.addRow("标题", self._title)
        self._validation_label = QLabel("")
        self._validation_label.setObjectName("TodoValidationLabel")
        self._validation_label.setVisible(False)
        form.addRow("", self._validation_label)

        self._content = QTextEdit()
        self._content.setPlaceholderText("内容")
        self._content.setFixedHeight(64)
        self._content.setPlainText(str(self._item.get("content", "")))
        form.addRow("内容", self._content)

        self._all_day = QCheckBox("全天（提醒以 09:00 为基准）")
        self._all_day.setChecked(bool(self._item.get("all_day", False)))
        form.addRow("", self._all_day)

        self._start = QDateTimeEdit(start_dt)
        self._start.setCalendarPopup(True)
        self._start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._start.installEventFilter(self)
        form.addRow("开始", self._start)

        end_dt = self._from_iso(self._item.get("end"), start_dt.addSecs(60 * 60))
        self._end = QDateTimeEdit(end_dt)
        self._end.setCalendarPopup(True)
        self._end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._end.installEventFilter(self)
        form.addRow("结束", self._end)
        quick_row = QWidget()
        quick_layout = QHBoxLayout(quick_row)
        quick_layout.setContentsMargins(0, 0, 0, 0)
        quick_layout.setSpacing(6)
        for text, slot in [
            ("15 分钟后", lambda *_: self._set_quick_start(QDateTime.currentDateTime().addSecs(15 * 60))),
            ("30 分钟后", lambda *_: self._set_quick_start(QDateTime.currentDateTime().addSecs(30 * 60))),
            ("今晚", self._set_quick_tonight),
            ("明早", self._set_quick_tomorrow_morning),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("TodoQuickTimeButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.clicked.connect(slot)
            quick_layout.addWidget(btn)
        quick_layout.addStretch(1)
        form.addRow("快速时间", quick_row)
        root.addWidget(form_card)

        remind_card = QFrame()
        remind_card.setObjectName("TodoDialogCard")
        remind_form = QFormLayout(remind_card)
        remind_form.setContentsMargins(14, 12, 14, 12)
        remind_form.setHorizontalSpacing(12)
        remind_form.setVerticalSpacing(10)

        self._repeat = ModernPopupComboBox()
        self._repeat.addItem("不重复", "none")
        self._repeat.addItem("每天", "daily")
        self._repeat.addItem("每周", "weekly")
        self._repeat.addItem("每月", "monthly")
        self._repeat.setProperty("matchPopupWidthToParent", True)
        remind_form.addRow("重复", self._repeat)

        self._repeat_weekday = _MultiSelectComboBox()
        for text, day in [("星期一", 1), ("星期二", 2), ("星期三", 3), ("星期四", 4), ("星期五", 5), ("星期六", 6), ("星期日", 7)]:
            self._repeat_weekday.addItem(text, day)
        remind_form.addRow("每周", self._repeat_weekday)

        self._repeat_month_day = _MultiSelectComboBox()
        for day in range(1, 32):
            self._repeat_month_day.addItem(f"{day} 日", day)
        remind_form.addRow("每月", self._repeat_month_day)

        self._reminder = ModernPopupComboBox()
        for text, minutes in [
            ("准时", 0),
            ("提前 5 分钟", 5),
            ("提前 10 分钟", 10),
            ("提前 30 分钟", 30),
            ("提前 1 小时", 60),
            ("提前 1 天", 24 * 60),
        ]:
            self._reminder.addItem(text, minutes)
        self._reminder.setProperty("matchPopupWidthToParent", True)
        self._reminder.setCurrentIndex(2)
        remind_form.addRow("提醒", self._reminder)

        self._important = QCheckBox("标为重要（列表突出显示，全屏通知）")
        self._important.setChecked(bool(self._item.get("important", False)))
        remind_form.addRow("", self._important)
        root.addWidget(remind_card)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("TodoDialogSecondary")
        ok = QPushButton("完成")
        ok.setObjectName("TodoDialogPrimary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._accept_if_valid)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        root.addLayout(buttons)

        self._all_day.stateChanged.connect(self._sync_all_day_format)
        self._repeat.currentIndexChanged.connect(self._sync_repeat_options)
        self._title.textChanged.connect(lambda *_: self._clear_field_errors())
        self._start.dateTimeChanged.connect(lambda *_: self._clear_field_errors())
        self._end.dateTimeChanged.connect(lambda *_: self._clear_field_errors())
        self._reminder.currentIndexChanged.connect(lambda *_: self._clear_field_errors())
        self._apply_initial_values(start_dt)
        self._sync_all_day_format()
        self._sync_repeat_options()
        arrow_url = str((Path(__file__).resolve().parent.parent / "assets" / "icon_combo_arrow.svg").as_posix())
        checkbox_check_icon_url = str((Path(__file__).resolve().parent.parent / "assets" / "icon_checkbox_check.svg").as_posix())
        self.setStyleSheet(
            """
            QDialog {
                background: __DIALOG_BACKGROUND__;
                font-size: 14px;
            }
            QFrame#TodoDialogCard {
                background: __SURFACE_BACKGROUND__;
                border: 1px solid #f1f5f9;
                border-radius: 12px;
            }
            QLabel {
                color: #1f2937;
                font-size: 14px;
            }
            QCheckBox {
                color: #1f2937;
                font-size: 14px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border-radius: 4px;
                border: 1px solid #cbd5e1;
                background: __SURFACE_BACKGROUND__;
            }
            QCheckBox::indicator:hover {
                border-color: #94a3b8;
            }
            QCheckBox::indicator:checked {
                border-color: #1e293b;
                background: #1e293b;
                image: url('__CHECKBOX_ICON__');
            }
            QLineEdit, QTextEdit, QDateTimeEdit, QComboBox {
                background: __SURFACE_BACKGROUND__;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 6px 8px;
                color: #1f2937;
                font-size: 14px;
            }
            QLineEdit:hover, QTextEdit:hover, QDateTimeEdit:hover, QComboBox:hover {
                border-color: #cbd5e1;
            }
            QLineEdit:focus, QTextEdit:focus, QDateTimeEdit:focus, QComboBox:focus {
                border-color: #cbd5e1;
            }
            QComboBox::drop-down, QDateTimeEdit::drop-down {
                width: 24px;
                border: none;
                background: transparent;
            }
            QComboBox::down-arrow, QDateTimeEdit::down-arrow {
                image: url('__ARROW_ICON__');
                width: 12px;
                height: 12px;
            }
            QComboBox QAbstractItemView {
                background: __SURFACE_BACKGROUND__;
                color: #1f2937;
                font-size: 14px;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                selection-background-color: rgba(59, 130, 246, 0.06);
                selection-color: #111827;
            }
            QPushButton {
                background: #1e293b;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: 800;
                font-size: 14px;
            }
            QPushButton:hover { background: #334155; }
            QPushButton:pressed { background: #0f172a; }
            QPushButton#TodoDialogSecondary {
                background: #f1f5f9;
                color: #334155;
                border: none;
                border-radius: 8px;
            }
            QPushButton#TodoDialogSecondary:hover {
                background: #e2e8f0;
                color: #0f172a;
            }
            QPushButton#TodoQuickTimeButton {
                background: #f1f5f9;
                color: #334155;
                border: 1px solid #e2e8f0;
                border-radius: 7px;
                padding: 4px 10px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton#TodoQuickTimeButton:hover {
                background: #e2e8f0;
                color: #0f172a;
            }
            QLabel#TodoValidationLabel {
                color: #b91c1c;
                background: transparent;
                font-size: 12px;
                font-weight: 700;
            }
            """
        .replace("__ARROW_ICON__", arrow_url)
        .replace("__CHECKBOX_ICON__", checkbox_check_icon_url)
        .replace("__DIALOG_BACKGROUND__", MAIN_WINDOW_BACKGROUND)
        .replace("__SURFACE_BACKGROUND__", MODULE_BACKGROUND))
        from deepcat.ui.settings_dialog import SettingsDialog
        SettingsDialog._install_custom_text_context_menus(self, self)

    def _apply_caption_color(self) -> None:
        self._apply_caption_color_to_window(self)

    def _apply_caption_color_to_window(self, window: QWidget) -> None:
        try:
            if os.name != "nt":
                return
            hwnd = int(window.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
        except Exception:
            pass

    def _apply_dialog_caption_color(self, dialog: QDialog) -> None:
        self._apply_caption_color_to_window(dialog)
        QTimer.singleShot(0, lambda d=dialog: self._apply_caption_color_to_window(d))

    def _drive_get_existing_directory(self, title: str, start_dir: str) -> str:
        dialog = QFileDialog(self, title, start_dir)
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self._apply_dialog_caption_color(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""
        files = dialog.selectedFiles()
        return str(files[0]) if files else ""

    def _drive_get_save_file_name(self, title: str, start_path: str, name_filter: str) -> tuple[str, str]:
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            title,
            start_path,
            name_filter,
        )
        return str(path or ""), str(selected_filter or "")

    def _drive_get_int(self, title: str, label: str, value: int, minimum: int, maximum: int, step: int = 1) -> tuple[int, bool]:
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setInputMode(QInputDialog.InputMode.IntInput)
        dialog.setIntRange(int(minimum), int(maximum))
        dialog.setIntStep(int(step))
        dialog.setIntValue(int(value))
        self._apply_dialog_caption_color(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return int(value), False
        return int(dialog.intValue()), True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_caption_color()

    def eventFilter(self, obj, event) -> bool:
        if obj in (getattr(self, "_start", None), getattr(self, "_end", None)):
            if event.type() in {QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.FocusIn}:
                self._ensure_datetime_calendar_styled(obj)
        return super().eventFilter(obj, event)

    def _ensure_datetime_calendar_styled(self, editor: object) -> None:
        if not isinstance(editor, QDateTimeEdit):
            return
        try:
            calendar = editor.calendarWidget()
            ident = id(calendar)
            if ident in self._styled_datetime_calendars:
                return
            self._style_calendar_popup(calendar)
            self._styled_datetime_calendars.add(ident)
        except Exception:
            pass


    @staticmethod
    def _from_iso(value: Any, fallback: QDateTime) -> QDateTime:
        dt = QDateTime.fromString(str(value or ""), Qt.DateFormat.ISODate)
        return dt if dt.isValid() else fallback

    def _style_calendar_popup(self, calendar: Optional[QCalendarWidget]) -> None:
        if calendar is None:
            return
        calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        calendar.setStyleSheet(
            """
            QCalendarWidget {
                background: #ffffff;
                color: #1f2937;
                font-size: 14px;
            }
            QCalendarWidget QToolButton {
                background: transparent;
                color: #1f2937;
                border: none;
                font-weight: 800;
                padding: 6px 8px;
                min-height: 24px;
                font-size: 14px;
            }
            QCalendarWidget QToolButton::menu-indicator {
                image: none;
                width: 0px;
            }
            QCalendarWidget QAbstractItemView {
                background: #ffffff;
                color: #1f2937;
                selection-background-color: #1e293b;
                selection-color: white;
                outline: none;
                font-size: 14px;
            }
            QCalendarWidget QSpinBox {
                min-height: 20px;
                max-height: 24px;
                padding: 1px 4px;
                border-radius: 6px;
                background: white;
                border: 1px solid #e2e8f0;
                margin: 4px 2px;
                font-size: 14px;
            }
            """
        )

    def _set_quick_start(self, start_dt: QDateTime) -> None:
        if not start_dt.isValid():
            return
        duration = max(15 * 60, int(self._start.dateTime().secsTo(self._end.dateTime())))
        if duration <= 0:
            duration = 60 * 60
        self._all_day.setChecked(False)
        self._start.setDateTime(start_dt)
        self._end.setDateTime(start_dt.addSecs(duration))
        self._clear_field_errors()

    def _set_quick_tonight(self) -> None:
        now = QDateTime.currentDateTime()
        target = QDateTime(now.date(), QTime(20, 0))
        if target <= now:
            target = target.addDays(1)
        self._set_quick_start(target)

    def _set_quick_tomorrow_morning(self) -> None:
        tomorrow = QDate.currentDate().addDays(1)
        self._set_quick_start(QDateTime(tomorrow, QTime(9, 0)))

    def _field_error_style(self) -> str:
        return (
            f"background: {MODULE_BACKGROUND}; border: 1px solid #ef4444; border-radius: 8px;"
            " padding: 6px 8px; color: #1f2937; font-size: 14px;"
        )

    def _clear_field_errors(self) -> None:
        for widget in (self._title, self._start, self._end, self._reminder):
            try:
                widget.setStyleSheet("")
            except Exception:
                pass
        self._validation_label.setVisible(False)
        self._validation_label.setText("")

    def _show_validation_error(self, message: str, *widgets: QWidget) -> None:
        text = str(message or "请检查待办信息。")
        self._validation_label.setText(text)
        self._validation_label.setVisible(True)
        if callable(getattr(self, "_on_validation_error", None)):
            try:
                self._on_validation_error(text)
            except Exception:
                pass
        for widget in widgets:
            try:
                widget.setStyleSheet(self._field_error_style())
            except Exception:
                pass
        if widgets:
            try:
                widgets[0].setFocus()
            except Exception:
                pass

    def _reminder_signature(self, payload: dict[str, Any]) -> tuple:
        start_dt = self._from_iso(payload.get("start"), QDateTime())
        reminder_minutes = int(payload.get("reminder_minutes", 0) or 0)
        trigger = start_dt.addSecs(-reminder_minutes * 60) if start_dt.isValid() else QDateTime()
        repeat = str(payload.get("repeat", "none") or "none")
        weekdays = tuple(sorted(int(x) for x in list(payload.get("repeat_weekday", []) or []))) if repeat == "weekly" else ()
        month_days = tuple(sorted(int(x) for x in list(payload.get("repeat_month_day", []) or []))) if repeat == "monthly" else ()
        return (
            repeat,
            weekdays,
            month_days,
            bool(payload.get("all_day", False)),
            trigger.toString(Qt.DateFormat.ISODate) if trigger.isValid() else "",
        )

    def _has_reminder_conflict(self, payload: dict[str, Any]) -> bool:
        signature = self._reminder_signature(payload)
        item_id = str(payload.get("id", "") or "")
        if not signature[-1]:
            return False
        for item in self._existing_items:
            if str(item.get("id", "") or "") == item_id:
                continue
            if bool(item.get("completed", False)) or bool(item.get("struck_off", False)):
                continue
            if self._reminder_signature(item) == signature:
                return True
        return False

    def _validate_payload(self, payload: dict[str, Any]) -> bool:
        self._clear_field_errors()
        if not str(self._title.text()).strip():
            self._show_validation_error("请填写待办标题。", self._title)
            return False
        start_dt = self._start.dateTime()
        end_dt = self._end.dateTime()
        if not start_dt.isValid() or not end_dt.isValid() or end_dt < start_dt:
            self._show_validation_error("结束时间不能早于开始时间。", self._start, self._end)
            return False
        if self._has_reminder_conflict(payload):
            self._show_validation_error("这个提醒时间与已有待办冲突，请调整开始时间或提醒时间。", self._start, self._reminder)
            return False
        return True

    def _accept_if_valid(self) -> None:
        payload = self.payload()
        if not self._validate_payload(payload):
            return
        self.accept()

    def _set_combo_by_data(self, combo: QComboBox, value: Any) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    def _apply_initial_values(self, start_dt: QDateTime) -> None:
        repeat = str(self._item.get("repeat", "none") or "none")
        self._set_combo_by_data(self._repeat, repeat)
        weekday_val = self._item.get("repeat_weekday", start_dt.date().dayOfWeek())
        if isinstance(weekday_val, list):
            self._repeat_weekday.setCurrentIndices([int(x) for x in weekday_val])
        else:
            self._repeat_weekday.setCurrentIndices([int(weekday_val or start_dt.date().dayOfWeek())])
        month_val = self._item.get("repeat_month_day", start_dt.date().day())
        if isinstance(month_val, list):
            self._repeat_month_day.setCurrentIndices([int(x) for x in month_val])
        else:
            self._repeat_month_day.setCurrentIndices([int(month_val or start_dt.date().day())])
        self._set_combo_by_data(self._reminder, int(self._item.get("reminder_minutes", 10) or 0))

    def _sync_repeat_options(self) -> None:
        repeat = str(self._repeat.currentData() or "none")
        self._repeat_weekday.setVisible(repeat == "weekly")
        self._repeat_month_day.setVisible(repeat == "monthly")
        try:
            self._repeat_weekday.parentWidget().layout().labelForField(self._repeat_weekday).setVisible(repeat == "weekly")  # type: ignore[union-attr]
            self._repeat_month_day.parentWidget().layout().labelForField(self._repeat_month_day).setVisible(repeat == "monthly")  # type: ignore[union-attr]
        except Exception:
            pass

    def _sync_all_day_format(self) -> None:
        fmt = "yyyy-MM-dd" if self._all_day.isChecked() else "yyyy-MM-dd HH:mm"
        self._start.setDisplayFormat(fmt)
        self._end.setDisplayFormat(fmt)

    def payload(self) -> dict[str, Any]:
        start_dt = self._start.dateTime()
        end_dt = self._end.dateTime()
        if not end_dt.isValid() or end_dt < start_dt:
            end_dt = start_dt.addSecs(24 * 60 * 60 if self._all_day.isChecked() else 60 * 60)
        return {
            "id": str(self._item.get("id") or f"todo-{int(time.time() * 1000)}"),
            "title": str(self._title.text()).strip() or "未命名待办",
            "content": str(self._content.toPlainText()).strip(),
            "start": start_dt.toString(Qt.DateFormat.ISODate),
            "end": end_dt.toString(Qt.DateFormat.ISODate),
            "all_day": bool(self._all_day.isChecked()),
            "important": bool(self._important.isChecked()),
            "reminder_minutes": int(self._reminder.currentData() or 0),
            "snooze_minutes": int(self._item.get("snooze_minutes", 10) or 10),
            "repeat": str(self._repeat.currentData() or "none"),
            "repeat_weekday": self._repeat_weekday.currentIndices() or [start_dt.date().dayOfWeek()],
            "repeat_month_day": self._repeat_month_day.currentIndices() or [start_dt.date().day()],
            "completed": bool(self._item.get("completed", False)),
            "struck_off": bool(self._item.get("struck_off", False)),
            "reminded_at": str(self._item.get("reminded_at", "")),
            "reminded_occurrences": list(self._item.get("reminded_occurrences", [])) if isinstance(self._item.get("reminded_occurrences"), list) else [],
            "next_remind_at": str(self._item.get("next_remind_at", "")),
            "next_remind_occurrence": str(self._item.get("next_remind_occurrence", "")),
        }


class _TodoReminderPopup(QWidget):
    snoozeRequested = pyqtSignal(str, str, int)
    dismissRequested = pyqtSignal(str, str)

    def __init__(self, item: dict[str, Any], *, parent=None) -> None:
        super().__init__(parent)
        self._item_id = str(item.get("id", ""))
        self._occurrence_key = str(item.get("_occurrence_date", ""))
        self.setObjectName("TodoReminderPopup")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(0)
        card = QFrame()
        card.setObjectName("TodoReminderCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(15, 23, 42, 58))
        card.setGraphicsEffect(shadow)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(9)
        root.addWidget(card)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        headline = QLabel("待办提醒")
        headline.setObjectName("TodoPopupHeadline")
        title_row.addWidget(headline)
        title_row.addStretch(1)
        card_layout.addLayout(title_row)

        title = QLabel(str(item.get("title", "未命名待办")))
        title.setObjectName("TodoPopupTitle")
        title.setWordWrap(True)
        card_layout.addWidget(title)

        content = str(item.get("content", "")).strip()
        if content:
            body = QLabel(content)
            body.setObjectName("TodoPopupBody")
            body.setWordWrap(True)
            card_layout.addWidget(body)

        when = QLabel(str(item.get("_display_time", "")))
        when.setObjectName("TodoPopupTime")
        card_layout.addWidget(when)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self._snooze = _TodoPopupComboBox()
        self._snooze.setObjectName("TodoFilterCombo")
        for text, minutes in [("5 分钟后", 5), ("10 分钟后", 10), ("15 分钟后", 15), ("30 分钟后", 30), ("1 小时后", 60)]:
            self._snooze.addItem(text, minutes)
        snooze_minutes = int(item.get("snooze_minutes", 10) or 10)
        for i in range(self._snooze.count()):
            if int(self._snooze.itemData(i) or 0) == snooze_minutes:
                self._snooze.setCurrentIndex(i)
                break
        later = QPushButton("再次提醒")
        later.setObjectName("TodoPopupSecondary")
        done = QPushButton("知道了")
        done.setObjectName("TodoPopupPrimary")
        later.clicked.connect(self._emit_snooze)
        done.clicked.connect(self._emit_done)
        actions.addWidget(self._snooze, 1)
        actions.addWidget(later)
        actions.addWidget(done)
        card_layout.addLayout(actions)

        arrow_url = str((Path(__file__).resolve().parent.parent / "assets" / "icon_combo_arrow.svg").as_posix())
        self.setStyleSheet(
            """
            QWidget#TodoReminderPopup {
                background: transparent;
            }
            QFrame#TodoReminderCard {
                background: #ffffff;
                border: 1px solid #f1f5f9;
                border-radius: 14px;
            }
            QLabel#TodoPopupHeadline {
                color: #1e293b;
                font-size: 13px;
                font-weight: 900;
            }
            QLabel#TodoPopupTitle {
                color: #111827;
                font-size: 17px;
                font-weight: 900;
            }
            QLabel#TodoPopupBody {
                color: #4b5563;
                font-size: 13px;
                line-height: 1.4;
            }
            QLabel#TodoPopupTime {
                color: #64748b;
                font-size: 12px;
                font-weight: 700;
            }
            QComboBox {
                min-height: 28px;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 3px 28px 3px 8px;
                color: #1f2937;
                background: white;
            }
            QComboBox:hover {
                border-color: #cbd5e1;
            }
            QComboBox:focus {
                border: 1px solid #cbd5e1;
            }
            QComboBox::drop-down {
                width: 24px;
                border: none;
                background: transparent;
            }
            QComboBox::down-arrow {
                image: url('__ARROW_ICON__');
                width: 12px;
                height: 12px;
            }
            QPushButton {
                min-height: 28px;
                border-radius: 8px;
                padding: 4px 14px;
                font-weight: 800;
            }
            QPushButton#TodoPopupPrimary {
                color: white;
                background: #1e293b;
                border: none;
            }
            QPushButton#TodoPopupPrimary:hover {
                background: #334155;
            }
            QPushButton#TodoPopupPrimary:pressed {
                background: #0f172a;
            }
            QPushButton#TodoPopupSecondary {
                color: #334155;
                background: #f1f5f9;
                border: none;
            }
            QPushButton#TodoPopupSecondary:hover {
                background: #e2e8f0;
                color: #0f172a;
            }
            """
        .replace("__ARROW_ICON__", arrow_url))
        _style_todo_combo_popup_view(self._snooze)

    def _emit_snooze(self) -> None:
        self.snoozeRequested.emit(self._item_id, self._occurrence_key, int(self._snooze.currentData() or 10))
        self.close()

    def _emit_done(self) -> None:
        self.dismissRequested.emit(self._item_id, self._occurrence_key)
        self.close()


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



class TodoMixin:
    def _install_todo_shortcuts(self) -> None:
        pass

    def _show_todo_status(self, text: str, *, tone: str = "info", auto_hide_ms: int = 2600) -> None:
        self._show_inline_status(getattr(self, "_todo_status_label", None), text, tone=tone, auto_hide_ms=auto_hide_ms)

    def _open_todo_resource_window(self) -> None:
        window = getattr(self, "_todo_resource_window", None)
        try:
            if window is not None:
                window.show()
                window.raise_()
                window.activateWindow()
                return
        except Exception:
            pass
        window = _TodoResourceWindow(self)
        self._todo_resource_window = window
        window.show()
        window.raise_()
        window.activateWindow()

    def _make_todo_stat_chip(self, title: str) -> tuple[QFrame, QLabel]:
        chip = QFrame()
        chip.setObjectName("TodoStatChip")
        chip.setProperty("active", False)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        chip.setFixedHeight(70)
        layout = QVBoxLayout(chip)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(0)
        value_label = QLabel("0")
        value_label.setObjectName("TodoStatValue")
        title_label = QLabel(str(title))
        title_label.setObjectName("TodoStatTitle")
        layout.addWidget(value_label)
        layout.addWidget(title_label)
        return chip, value_label

    def _style_todo_filter_combo(self, combo: QComboBox) -> None:
        combo.setStyleSheet(
            """
            QComboBox#TodoFilterCombo {
                min-height: 30px;
                max-height: 32px;
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-radius: 8px;
                padding: 2px 28px 2px 10px;
                color: #111827;
                font-size: 13px;
                font-weight: 700;
            }
            QComboBox#TodoFilterCombo:hover {
                border-color: #cbd5e1;
            }
            QComboBox#TodoFilterCombo:focus {
                border-color: #94a3b8;
                background: #ffffff;
            }
            QComboBox#TodoFilterCombo::drop-down {
                border: none;
                width: 26px;
            }
            QComboBox#TodoFilterCombo QAbstractItemView {
                background: #ffffff;
                color: #111827;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                outline: none;
                padding: 4px;
                selection-background-color: #f1f5f9;
                selection-color: #0f172a;
            }
            """
        )
        _style_todo_combo_popup_view(combo)

    def _connect_todo_resource_controls(self) -> None:
        if hasattr(self, "_cat_reminder_enabled") and not bool(getattr(self._cat_reminder_enabled, "_deepcat_connected", False)):
            self._cat_reminder_enabled.stateChanged.connect(self._apply_cat_reminder_settings)
            self._cat_reminder_voice.stateChanged.connect(self._apply_cat_reminder_settings)
            self._cat_reminder_voice.stateChanged.connect(lambda *_: self._update_cat_voice_status_label())
            self._cat_reminder_exit.stateChanged.connect(self._apply_cat_reminder_settings)
            self._cat_reminder_pre_notify.stateChanged.connect(self._apply_cat_reminder_settings)
            self._cat_reminder_pre_notify.stateChanged.connect(lambda *_: self._update_cat_pre_notify_controls())
            self._cat_reminder_interval.valueChanged.connect(self._apply_cat_reminder_settings)
            self._cat_reminder_duration.valueChanged.connect(self._apply_cat_reminder_settings)
            self._cat_reminder_pre_notify_seconds.valueChanged.connect(self._apply_cat_reminder_settings)
            self._cat_reminder_message.editingFinished.connect(self._apply_cat_reminder_settings)
            self._cat_reminder_message_reset.clicked.connect(self._reset_cat_reminder_message)
            self._cat_reminder_preview.clicked.connect(self._preview_cat_reminder)
            self._cat_reminder_enabled._deepcat_connected = True
        if hasattr(self, "_todo_calendar") and not bool(getattr(self._todo_calendar, "_deepcat_connected", False)):
            self._todo_calendar.selectionChanged.connect(self._refresh_todo_after_calendar_selection)
            self._todo_calendar.currentPageChanged.connect(lambda *_: self._refresh_todo_calendar_markers())
            self._todo_search.textChanged.connect(lambda *_: self._refresh_todo_list())
            self._todo_date_scope.currentIndexChanged.connect(lambda *_: self._refresh_todo_list())
            self._todo_status_filter.currentIndexChanged.connect(lambda *_: self._set_todo_list_filter(str(self._todo_status_filter.currentData() or "")))
            if hasattr(self, "_todo_filter_clear_btn"):
                self._todo_filter_clear_btn.clicked.connect(lambda *_: self._set_todo_list_filter(""))
            self._todo_add_btn.clicked.connect(lambda *_: self._open_todo_dialog())
            self._todo_batch_select_all.stateChanged.connect(self._batch_select_all)
            self._todo_batch_delete.clicked.connect(self._batch_delete)
            self._todo_batch_strike.clicked.connect(self._batch_strike)
            self._todo_batch_done.clicked.connect(lambda *_: self._toggle_todo_batch_mode(False))
            if hasattr(self, "_todo_today_btn"):
                self._todo_today_btn.clicked.connect(lambda *_: self._select_todo_date_offset(0))
            if hasattr(self, "_todo_tomorrow_btn"):
                self._todo_tomorrow_btn.clicked.connect(lambda *_: self._select_todo_date_offset(1))
            if hasattr(self, "_todo_batch_btn"):
                self._todo_batch_btn.clicked.connect(lambda *_: self._toggle_todo_batch_mode(True))
            self._todo_calendar._deepcat_connected = True

    def _update_todo_tile_countdown(self) -> None:
        tile = getattr(self, "_todo_tile_widget", None)
        if tile is None:
            return
        try:
            tile.objectName()
        except RuntimeError:
            self._todo_tile_widget = None
            return

        if not self._feature_enabled("todo"):
            tile.set_countdown_text("")
            return

        reminder = self._current_cat_reminder_settings()
        if not bool(reminder.get("enabled", False)):
            tile.set_countdown_text("")
            return

        if self._cat_reminder_session is not None:
            tile.set_countdown_text("休息中")
            return

        remaining_ms = self._cat_reminder_timer.remainingTime()
        if remaining_ms <= 0:
            tile.set_countdown_text("")
            return

        total_seconds = max(0, remaining_ms // 1000)
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        tile.set_countdown_text(f"{minutes:02d}:{seconds:02d}")

    def _current_todo_items(self) -> list[dict[str, Any]]:
        """从 SQLite 读取所有待办事项。"""
        try:
            items = normalize_todo_items(self._todo_store.load_items())
            self._todo_load_error_reported = False
            return items
        except Exception as exc:
            get_logger().exception("读取待办数据失败")
            if not bool(getattr(self, "_todo_load_error_reported", False)):
                self._todo_load_error_reported = True
                self._show_todo_status(f"待办数据读取失败：{exc}", tone="error", auto_hide_ms=5200)
            return []

    def _save_todo_items(self, items: list[dict[str, Any]], *, refresh: bool = True) -> bool:
        normalized = normalize_todo_items(items)
        self._todo_items = normalized
        try:
            self._todo_store.save_items(normalized)
        except Exception as exc:
            get_logger().exception("保存待办数据失败")
            self._show_todo_status(f"待办数据保存失败：{exc}", tone="error", auto_hide_ms=5200)
            return False
        if refresh:
            self._refresh_todo_list()
            self._refresh_todo_calendar_markers()
        self._refresh_tray_tooltip()
        self._schedule_todo_checks()
        return True

    def _todo_dialog_parent(self) -> Optional[QWidget]:
        resource_win = getattr(self, "_todo_resource_window", None)
        active = QApplication.activeWindow()
        if isinstance(resource_win, QWidget) and resource_win.isVisible():
            if active is None or active is self or self._is_widget_inside(active, resource_win):
                return resource_win
        if isinstance(active, QWidget) and active.isVisible():
            return active
        if self.isVisible():
            return self
        return None

    def _handle_todo_page_keypress(self, obj, event) -> bool:
        page = getattr(self, "_todo_page", None)
        if page is None or not isinstance(page, QWidget) or not page.isVisible():
            return False
        focus = QApplication.focusWidget()
        widget = obj if isinstance(obj, QWidget) else focus
        if not self._is_widget_inside(widget, page) and not self._is_widget_inside(focus, page):
            return False
        key = int(event.key())
        modifiers = event.modifiers()
        has_ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        has_shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        has_alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        has_meta = bool(modifiers & Qt.KeyboardModifier.MetaModifier)
        plain = not (has_ctrl or has_shift or has_alt or has_meta)
        ctrl_only = has_ctrl and not (has_shift or has_alt or has_meta)
        if ctrl_only and key in (int(Qt.Key.Key_Return), int(Qt.Key.Key_Enter)):
            self._open_todo_dialog()
            event.accept()
            return True
        search = getattr(self, "_todo_search", None)
        focus_is_input = isinstance(focus, (QLineEdit, QTextEdit, QComboBox, QAbstractSpinBox, QKeySequenceEdit))
        if key == int(Qt.Key.Key_Escape):
            if (
                isinstance(search, QLineEdit)
                and str(search.text() or "").strip()
                and (focus is search or not focus_is_input)
            ):
                search.clear()
                self._show_todo_status("搜索已清空。", tone="info", auto_hide_ms=1600)
                event.accept()
                return True
            if bool(getattr(self, "_todo_batch_mode", False)):
                self._toggle_todo_batch_mode(False)
                event.accept()
                return True
        if focus_is_input:
            return False
        if not plain:
            return False
        if key == int(Qt.Key.Key_N):
            self._open_todo_dialog()
            event.accept()
            return True
        if key in (int(Qt.Key.Key_Return), int(Qt.Key.Key_Enter)):
            item_id = self._selected_todo_item_id()
            if item_id:
                self._open_todo_dialog(item_id)
            else:
                self._show_todo_status("请先选中要编辑的待办。", tone="warning", auto_hide_ms=2200)
            event.accept()
            return True
        if key == int(Qt.Key.Key_Delete):
            self._delete_selected_todo()
            event.accept()
            return True
        if key == int(Qt.Key.Key_Space):
            self._toggle_selected_todo_completed()
            event.accept()
            return True
        if key in (int(Qt.Key.Key_Left), int(Qt.Key.Key_Right)):
            todo_list = getattr(self, "_todo_list", None)
            if isinstance(todo_list, QWidget) and self._is_widget_inside(focus, todo_list):
                return False
            self._change_todo_calendar_month(-1 if key == int(Qt.Key.Key_Left) else 1)
            event.accept()
            return True
        return False

    def _selected_todo_item_id(self) -> str:
        if not hasattr(self, "_todo_list"):
            return ""
        row = self._todo_list.currentItem()
        if row is None:
            return ""
        return str(row.data(Qt.ItemDataRole.UserRole) or "")

    def _selected_todo_item_widget(self) -> Optional[_TodoListItemWidget]:
        if not hasattr(self, "_todo_list"):
            return None
        row = self._todo_list.currentItem()
        if row is None:
            return None
        widget = self._todo_list.itemWidget(row)
        return widget if isinstance(widget, _TodoListItemWidget) else None

    def _delete_selected_todo(self) -> None:
        item_id = self._selected_todo_item_id()
        if not item_id:
            self._show_todo_status("请先选中要删除的待办。", tone="warning", auto_hide_ms=2200)
            return
        self._delete_todo(item_id)

    def _toggle_selected_todo_completed(self) -> None:
        item_id = self._selected_todo_item_id()
        if not item_id:
            self._show_todo_status("请先选中要完成的待办。", tone="warning", auto_hide_ms=2200)
            return
        widget = self._selected_todo_item_widget()
        if widget is not None:
            self._toggle_todo_completed(item_id, not widget.is_completed())
            return
        target = next((x for x in self._current_todo_items() if str(x.get("id", "")) == item_id), None)
        self._toggle_todo_completed(item_id, not bool((target or {}).get("completed", False)))

    def _selected_todo_date(self) -> QDate:
        try:
            if hasattr(self, "_todo_calendar"):
                d = self._todo_calendar.selectedDate()
                if d.isValid():
                    return d
        except Exception:
            pass
        return QDate.currentDate()

    def _select_todo_date_offset(self, days: int) -> None:
        if not hasattr(self, "_todo_calendar"):
            return
        target = QDate.currentDate().addDays(int(days))
        self._todo_calendar.setSelectedDate(target)
        try:
            self._todo_calendar.showSelectedDate()
        except Exception:
            pass
        self._refresh_todo_after_calendar_selection()

    def _activate_todo_overview_card(self, days: int, filter_name: str = "") -> None:
        if hasattr(self, "_todo_date_scope") and isinstance(self._todo_date_scope, QComboBox):
            for i in range(self._todo_date_scope.count()):
                if str(self._todo_date_scope.itemData(i) or "") == "selected":
                    self._with_blocked_signals(self._todo_date_scope, lambda idx=i: self._todo_date_scope.setCurrentIndex(idx))
                    break
        self._select_todo_date_offset(int(days))
        self._set_todo_list_filter(str(filter_name or ""))

    def _change_todo_calendar_month(self, months: int) -> None:
        calendar = getattr(self, "_todo_calendar", None)
        if not isinstance(calendar, QCalendarWidget):
            return
        try:
            if isinstance(calendar, _TodoCalendarWidget):
                calendar._shift_months(int(months))
            else:
                current = QDate(int(calendar.yearShown()), int(calendar.monthShown()), 1)
                target = current.addMonths(int(months))
                calendar.setCurrentPage(target.year(), target.month())
            self._refresh_todo_calendar_markers()
        except Exception:
            pass

    def _refresh_todo_stat_card_state(self) -> None:
        cards = getattr(self, "_todo_stat_cards", None)
        if not isinstance(cards, dict):
            return
        filter_key = str(getattr(self, "_todo_list_filter", "") or "")
        if filter_key:
            active_key = filter_key
        else:
            selected_date = self._selected_todo_date()
            today = QDate.currentDate()
            if selected_date == today:
                active_key = "all"
            elif selected_date == today.addDays(1):
                active_key = "tomorrow"
            else:
                active_key = ""
        for key, card in cards.items():
            try:
                card.setProperty("active", key == active_key)
                card.style().unpolish(card)
                card.style().polish(card)
                card.update()
            except Exception:
                pass

    def _set_todo_list_filter(self, filter_name: str) -> None:
        filter_name = str(filter_name or "")
        if filter_name == "all":
            filter_name = ""
        self._todo_list_filter = filter_name
        combo = getattr(self, "_todo_status_filter", None)
        if isinstance(combo, QComboBox):
            for i in range(combo.count()):
                if str(combo.itemData(i) or "") == filter_name:
                    self._with_blocked_signals(combo, lambda idx=i: combo.setCurrentIndex(idx))
                    break
        clear_btn = getattr(self, "_todo_filter_clear_btn", None)
        if isinstance(clear_btn, QToolButton):
            clear_btn.setVisible(bool(filter_name))
        self._refresh_todo_stat_card_state()
        self._refresh_todo_list()

    def _set_todo_overdue_banner(self, overdue_items: list[dict[str, Any]]) -> None:
        banner = getattr(self, "_todo_overdue_banner", None)
        if not isinstance(banner, QLabel):
            return
        count = len(overdue_items)
        if count <= 1:
            banner.setVisible(False)
            self._todo_overdue_banner_item_id = ""
            return
        first = sorted(overdue_items, key=self._todo_sort_key)[0]
        self._todo_overdue_banner_item_id = str(first.get("id", "") or "")
        banner.setText(f"有 {count} 项已到点，点击查看")
        banner.setVisible(True)

    def _locate_todo_overdue_banner_item(self) -> None:
        target_id = str(getattr(self, "_todo_overdue_banner_item_id", "") or "")
        if not target_id:
            return
        self._set_todo_list_filter("overdue")
        try:
            for row_index in range(self._todo_list.count()):
                row = self._todo_list.item(row_index)
                if str(row.data(Qt.ItemDataRole.UserRole) or "") == target_id:
                    self._todo_list.setCurrentRow(row_index)
                    self._todo_list.scrollToItem(row, QAbstractItemView.ScrollHint.PositionAtCenter)
                    break
        except Exception:
            pass

    def _todo_item_matches_filter(self, item: dict[str, Any], filter_name: str, now: QDateTime) -> bool:
        filter_name = str(filter_name or "")
        if filter_name in ("", "all"):
            return True
        is_active = not bool(item.get("completed", False)) and not bool(item.get("struck_off", False))
        if filter_name == "active":
            return is_active
        if filter_name == "important":
            return is_active and bool(item.get("important", False))
        if filter_name == "overdue":
            due_at = self._todo_due_at(item)
            return is_active and due_at.isValid() and int(due_at.toSecsSinceEpoch()) <= int(now.toSecsSinceEpoch())
        if filter_name == "completed":
            return bool(item.get("completed", False))
        return True

    def _todo_current_date_scope(self) -> str:
        combo = getattr(self, "_todo_date_scope", None)
        if isinstance(combo, QComboBox):
            value = str(combo.currentData() or "").strip()
            if value:
                return value
        return "selected"

    def _todo_items_for_view_scope(self, date: QDate) -> tuple[list[dict[str, Any]], bool, str]:
        scope = self._todo_current_date_scope()
        if scope == "today":
            return self._todo_items_for_date(QDate.currentDate()), False, "今天"
        if scope == "next7":
            items: list[dict[str, Any]] = []
            today = QDate.currentDate()
            for offset in range(7):
                items.extend(self._todo_items_for_date(today.addDays(offset)))
            items.sort(key=self._todo_sort_key)
            return items, True, "未来 7 天"
        if scope == "all":
            items = self._current_todo_items()
            items.sort(key=self._todo_sort_key)
            return items, True, "全部待办"
        return self._todo_items_for_date(date), False, ""

    def _todo_item_matches_search(self, item: dict[str, Any], query: str, now: QDateTime) -> bool:
        query = str(query or "").strip().lower()
        if not query:
            return True
        start = self._todo_datetime_from_iso(item.get("_occurrence_start", item.get("start")), QDateTime())
        date_bits: list[str] = []
        if start.isValid():
            date_bits.extend([
                start.toString("yyyy-MM-dd"),
                start.toString("M月d日"),
                start.toString("HH:mm"),
            ])
        status_text = self._todo_list_status_text(item, now)
        group_text = self._todo_group_label(item, now)
        if bool(item.get("completed", False)):
            status_text = f"{status_text} 已完成".strip()
        if bool(item.get("important", False)):
            status_text = f"{status_text} 重要".strip()
        haystack = " ".join(
            [
                str(item.get("title", "")),
                str(item.get("content", "")),
                self._todo_display_time(item, include_date=True),
                str(item.get("_occurrence_date", "")),
                group_text,
                status_text,
                *date_bits,
            ]
        ).lower()
        return query in haystack

    def _todo_filter_empty_text(self, date: QDate, filter_name: str, query: str = "", scope_title: str = "") -> tuple[str, str]:
        if str(query or "").strip():
            return "没有找到标题/内容/日期匹配项", "换个关键词，或清空搜索。"
        filter_name = str(filter_name or "")
        if filter_name == "active":
            return "没有未完成待办", "切换筛选或新建一条待办。"
        if filter_name == "important":
            return "没有重要待办", "可以把需要优先处理的待办标为重要。"
        if filter_name == "overdue":
            return "没有已到点待办", "当前没有需要立即处理的提醒。"
        if filter_name == "completed":
            return "没有已完成待办", "完成一项待办后会显示在这里。"
        if scope_title == "未来 7 天":
            return "未来 7 天没有安排", "可以切换日期范围，或新建一条待办。"
        if scope_title == "全部待办":
            return "还没有待办", "按 N 或点击加号新建一条待办。"
        if date == QDate.currentDate():
            return "今天没有更多安排，可以安心休息一下", "可以新建待办，或切换日期查看其他安排。"
        return "这天没有安排", "切回今天，或为这天添加一条待办。"

    def _locate_todo_overview_item(self, kind: str = "next") -> None:
        if not hasattr(self, "_todo_list"):
            return
        target_id = str(
            getattr(
                self,
                "_todo_next_reminder_item_id" if kind == "reminder" else "_todo_overview_item_id",
                "",
            )
            or ""
        )
        if not target_id:
            return
        target_date = getattr(
            self,
            "_todo_next_reminder_item_date" if kind == "reminder" else "_todo_overview_item_date",
            QDate(),
        )
        try:
            if isinstance(target_date, QDate) and target_date.isValid() and hasattr(self, "_todo_calendar"):
                self._todo_calendar.setSelectedDate(target_date)
                self._todo_calendar.showSelectedDate()
                self._refresh_todo_after_calendar_selection()
        except Exception:
            pass
        self._set_todo_list_filter("")
        try:
            for row_index in range(self._todo_list.count()):
                row = self._todo_list.item(row_index)
                if str(row.data(Qt.ItemDataRole.UserRole) or "") == target_id:
                    self._todo_list.setCurrentRow(row_index)
                    self._todo_list.scrollToItem(row, QAbstractItemView.ScrollHint.PositionAtCenter)
                    break
        except Exception:
            pass

    def _update_todo_overview(self) -> None:
        if not hasattr(self, "_todo_today_count_label"):
            return
        try:
            now = QDateTime.currentDateTime()
            today = QDate.currentDate()
            self._todo_overview_item_id = ""
            self._todo_overview_item_date = today
            self._todo_next_reminder_item_id = ""
            self._todo_next_reminder_item_date = today
            today_items = self._todo_items_for_date(QDate.currentDate())
            tomorrow_items = self._todo_items_for_date(QDate.currentDate().addDays(1))
            completed_items = [x for x in today_items if bool(x.get("completed", False))]
            active_items = [
                x for x in today_items
                if not bool(x.get("completed", False)) and not bool(x.get("struck_off", False))
            ]
            important_items = [x for x in active_items if bool(x.get("important", False))]
            overdue_items = []
            upcoming_items = []
            now_ts = int(now.toSecsSinceEpoch())
            for item in active_items:
                due_at = self._todo_due_at(item)
                start_at = self._todo_datetime_from_iso(item.get("_occurrence_start", item.get("start")), QDateTime())
                if due_at.isValid() and int(due_at.toSecsSinceEpoch()) <= now_ts:
                    overdue_items.append(item)
                if start_at.isValid() and int(start_at.toSecsSinceEpoch()) >= now_ts:
                    upcoming_items.append(item)

            self._todo_today_count_label.setText(str(len(today_items)))
            if hasattr(self, "_todo_tomorrow_count_label"):
                self._todo_tomorrow_count_label.setText(str(len(tomorrow_items)))
            self._todo_active_count_label.setText(str(len(active_items)))
            self._todo_important_count_label.setText(str(len(important_items)))
            self._todo_overdue_count_label.setText(str(len(overdue_items)))
            if hasattr(self, "_todo_completed_count_label"):
                self._todo_completed_count_label.setText(str(len(completed_items)))
            self._refresh_todo_stat_card_state()

            just_reminded = getattr(self, "_todo_just_reminded", None)
            just_until = int(getattr(self, "_todo_just_reminded_until", 0) or 0)
            if isinstance(just_reminded, dict) and just_until > now_ts:
                title = str(just_reminded.get("title", "未命名待办")).strip() or "未命名待办"
                when = str(just_reminded.get("time", "")).strip()
                prefix = f"{when}  " if when else ""
                self._todo_next_label.setText(f"刚刚提醒：{prefix}{title}")
                self._todo_overview_item_id = str(just_reminded.get("id", "") or "")
            elif overdue_items:
                overdue_items.sort(key=self._todo_sort_key)
                next_item = overdue_items[0]
                title = str(next_item.get("title", "未命名待办")).strip() or "未命名待办"
                self._todo_next_label.setText(f"已到点：{self._todo_display_time(next_item)}  {title}")
                self._todo_overview_item_id = str(next_item.get("id", "") or "")
            elif upcoming_items:
                upcoming_items.sort(key=self._todo_sort_key)
                next_item = upcoming_items[0]
                title = str(next_item.get("title", "未命名待办")).strip() or "未命名待办"
                self._todo_next_label.setText(f"下一项：{self._todo_display_time(next_item)}  {title}")
                self._todo_overview_item_id = str(next_item.get("id", "") or "")
            else:
                self._todo_next_label.setText("下一项：暂无")
            reminder_items = []
            for item in active_items:
                trigger_at = self._todo_trigger_at(item)
                if trigger_at.isValid() and int(trigger_at.toSecsSinceEpoch()) >= now_ts:
                    reminder_items.append(item)
            reminder_items.sort(key=lambda x: int(self._todo_trigger_at(x).toSecsSinceEpoch()))
            if reminder_items and hasattr(self, "_todo_next_reminder_label"):
                reminder_item = reminder_items[0]
                title = str(reminder_item.get("title", "未命名待办")).strip() or "未命名待办"
                self._todo_next_reminder_label.setText(f"下一次提醒：{self._todo_trigger_at(reminder_item).toString('HH:mm')}  {title}")
                self._todo_next_reminder_item_id = str(reminder_item.get("id", "") or "")
            elif hasattr(self, "_todo_next_reminder_label"):
                self._todo_next_reminder_label.setText("下一次提醒：暂无")
        except Exception:
            get_logger().exception("刷新待办概览失败")

    def _mark_todo_just_reminded(self, item: dict[str, Any]) -> None:
        try:
            title = str(item.get("title", "未命名待办")).strip() or "未命名待办"
            self._todo_just_reminded = {
                "id": str(item.get("id", "") or ""),
                "title": title,
                "time": self._todo_display_time(item),
            }
            self._todo_just_reminded_until = int(QDateTime.currentDateTime().addSecs(12).toSecsSinceEpoch())
            self._update_todo_overview()

            def clear_if_expired() -> None:
                try:
                    now_ts = int(QDateTime.currentDateTime().toSecsSinceEpoch())
                    if int(getattr(self, "_todo_just_reminded_until", 0) or 0) <= now_ts:
                        self._todo_just_reminded = None
                        self._todo_just_reminded_until = 0
                        self._update_todo_overview()
                except Exception:
                    pass

            QTimer.singleShot(12000, clear_if_expired)
        except Exception:
            pass

    def _open_todo_dialog(self, item_id: str = "") -> None:
        if not self._feature_enabled("todo"):
            return
        item_id = str(item_id or "") if isinstance(item_id, str) else ""
        items = list(self._current_todo_items())
        editing_index = -1
        editing_item: Optional[dict[str, Any]] = None
        if item_id:
            for i, item in enumerate(items):
                if str(item.get("id", "")) == item_id:
                    editing_index = i
                    editing_item = dict(item)
                    break
        dialog_parent = self._todo_dialog_parent()
        dialog = _TodoEditDialog(
            dialog_parent,
            selected_date=self._selected_todo_date(),
            item=editing_item,
            existing_items=items,
            on_validation_error=lambda message: self._show_todo_status(message, tone="warning", auto_hide_ms=3600),
        )
        def _focus_dialog() -> None:
            try:
                if not dialog.isVisible():
                    return
                dialog.raise_()
                dialog.activateWindow()
                if os.name == "nt":
                    try:
                        hwnd = int(dialog.winId())
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                    except Exception:
                        pass
            except Exception:
                pass

        QTimer.singleShot(0, _focus_dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        if editing_index >= 0:
            items[editing_index] = payload
        else:
            items.append(payload)
        if self._save_todo_items(items):
            self._show_todo_status("待办已更新。" if editing_index >= 0 else "待办已新建。", tone="success", auto_hide_ms=1800)
            self._check_due_todos()

    def _open_todo_dialog_from_tray(self) -> None:
        if self._is_tray_menu_click_throttled():
            return
        if not self._feature_enabled("todo"):
            return
        self._open_todo_dialog()

    def _delete_todo(self, item_id: str) -> None:
        item_id = str(item_id or "")
        if not item_id:
            return
        items = self._current_todo_items()
        target = next((x for x in items if str(x.get("id", "")) == item_id), None)
        if target is None:
            return
        from deepcat.ui.main_window.compact import StyledMessageBox
        box = StyledMessageBox(self._todo_dialog_parent())
        box.setWindowTitle("删除待办")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"确定删除“{str(target.get('title', '未命名待办'))}”吗？")
        no_btn = box.addButton("否", QMessageBox.ButtonRole.NoRole)
        yes_btn = box.addButton("是", QMessageBox.ButtonRole.YesRole)
        box.setDefaultButton(no_btn)
        box.exec()
        if box.clickedButton() != yes_btn:
            return
        if self._save_todo_items([x for x in items if str(x.get("id", "")) != item_id]):
            self._show_todo_status("待办已删除。", tone="success", auto_hide_ms=1800)

    def _strike_todo(self, item_id: str) -> None:
        item_id = str(item_id or "")
        if not item_id:
            return
        items = self._current_todo_items()
        changed = False
        for item in items:
            if str(item.get("id", "")) == item_id:
                item["struck_off"] = not bool(item.get("struck_off", False))
                changed = True
                break
        if changed:
            if self._save_todo_items(items):
                self._show_todo_status("待办状态已更新。", tone="success", auto_hide_ms=1800)

    def _toggle_todo_completed(self, item_id: str, completed: bool) -> None:
        item_id = str(item_id or "")
        if not item_id:
            return
        items = self._current_todo_items()
        changed = False
        for item in items:
            if str(item.get("id", "")) == item_id:
                item["completed"] = bool(completed)
                if bool(completed):
                    item["next_remind_at"] = ""
                    item["next_remind_occurrence"] = ""
                    item["reminded_at"] = QDateTime.currentDateTime().toString(Qt.DateFormat.ISODate)
                else:
                    item["reminded_at"] = ""
                    item["reminded_occurrences"] = []
                changed = True
                break
        if changed:
            if self._save_todo_items(items):
                self._show_todo_status("待办已完成。" if completed else "已取消完成状态。", tone="success", auto_hide_ms=1800)

    def _toggle_todo_batch_mode(self, enabled: bool) -> None:
        previous = getattr(self, "_todo_batch_mode", None)
        self._todo_batch_mode = bool(enabled)
        is_batch = bool(enabled)
        self._todo_date_label.setVisible(not is_batch)
        self._todo_add_btn.setVisible(not is_batch)
        self._todo_batch_select_all.setVisible(is_batch)
        self._todo_batch_strike.setVisible(is_batch)
        self._todo_batch_delete.setVisible(is_batch)
        self._todo_batch_done.setVisible(is_batch)
        self._todo_batch_select_all.setChecked(False)
        self._refresh_todo_list()
        if previous is not None and bool(previous) != is_batch:
            self._show_todo_status("已进入批量整理。" if is_batch else "已退出批量整理。", tone="info", auto_hide_ms=1800)

    def _todo_datetime_from_iso(self, value: Any, fallback: Optional[QDateTime] = None) -> QDateTime:
        text = str(value or "").strip()
        dt = QDateTime.fromString(text, Qt.DateFormat.ISODate) if text else QDateTime()
        if dt.isValid():
            return dt
        return fallback if fallback is not None else QDateTime.currentDateTime()

    def _todo_repeat(self, item: dict[str, Any]) -> str:
        repeat = str(item.get("repeat", "none") or "none").lower()
        return repeat if repeat in {"daily", "weekly", "monthly"} else "none"

    def _todo_occurs_on(self, item: dict[str, Any], date: QDate) -> bool:
        if not date.isValid():
            return False
        start = self._todo_datetime_from_iso(item.get("start"), QDateTime())
        end = self._todo_datetime_from_iso(item.get("end"), start)
        if not start.isValid():
            return False
        if end.toSecsSinceEpoch() < start.toSecsSinceEpoch():
            end = start
        repeat = self._todo_repeat(item)
        if repeat == "none":
            return start.date() <= date <= end.date()
        if date < start.date():
            return False
        if repeat == "daily":
            return True
        if repeat == "weekly":
            weekdays = item.get("repeat_weekday", [start.date().dayOfWeek()])
            if not isinstance(weekdays, list):
                weekdays = [weekdays]
            return int(date.dayOfWeek()) in [int(x) for x in weekdays]
        if repeat == "monthly":
            configured_days = item.get("repeat_month_day", [start.date().day()])
            if not isinstance(configured_days, list):
                configured_days = [configured_days]
            return int(date.day()) in [min(int(x), int(date.daysInMonth())) for x in configured_days]
        return False

    def _todo_occurrence_for_date(self, item: dict[str, Any], date: QDate) -> Optional[dict[str, Any]]:
        if not self._todo_occurs_on(item, date):
            return None
        start = self._todo_datetime_from_iso(item.get("start"), QDateTime())
        end = self._todo_datetime_from_iso(item.get("end"), start.addSecs(60 * 60))
        if bool(item.get("all_day", False)):
            occurrence_start = QDateTime(date, QTime(9, 0))
        else:
            occurrence_start = QDateTime(date, start.time())
        duration = max(0, int(start.secsTo(end))) if end.isValid() and start.isValid() else 0
        occurrence = dict(item)
        occurrence["_occurrence_date"] = _date_key(date)
        occurrence["_occurrence_start"] = occurrence_start.toString(Qt.DateFormat.ISODate)
        occurrence["_occurrence_end"] = occurrence_start.addSecs(duration).toString(Qt.DateFormat.ISODate)
        return occurrence

    def _todo_items_for_date(self, date: QDate) -> list[dict[str, Any]]:
        if not date.isValid():
            date = QDate.currentDate()
        matched: list[dict[str, Any]] = []
        for item in self._current_todo_items():
            occurrence = self._todo_occurrence_for_date(item, date)
            if occurrence is not None:
                matched.append(occurrence)
        matched.sort(key=self._todo_sort_key)
        return matched

    def _todo_sort_key(self, item: dict[str, Any]) -> int:
        return int(self._todo_datetime_from_iso(item.get("_occurrence_start", item.get("start")), QDateTime()).toSecsSinceEpoch())

    def _todo_display_time(self, item: dict[str, Any], *, include_date: bool = False) -> str:
        start = self._todo_datetime_from_iso(item.get("_occurrence_start", item.get("start")), QDateTime.currentDateTime())
        if bool(item.get("all_day", False)):
            return f"{start.toString('M月d日')} 全天" if include_date else "全天"
        return start.toString("M月d日 HH:mm" if include_date else "HH:mm")

    def _todo_human_elapsed(self, seconds: int) -> str:
        seconds = max(0, int(seconds))
        minutes = seconds // 60
        if minutes < 1:
            return "不到 1 分钟"
        if minutes < 60:
            return f"{minutes} 分钟"
        hours = minutes // 60
        if hours < 24:
            rest = minutes % 60
            return f"{hours} 小时" if rest == 0 else f"{hours} 小时 {rest} 分钟"
        days = hours // 24
        rest_hours = hours % 24
        return f"{days} 天" if rest_hours == 0 else f"{days} 天 {rest_hours} 小时"

    def _todo_is_reminded(self, item: dict[str, Any]) -> bool:
        if str(item.get("reminded_at", "") or "").strip():
            return True
        occurrence_key = str(item.get("_occurrence_date", "") or "")
        occurrences = item.get("reminded_occurrences") if isinstance(item.get("reminded_occurrences"), list) else []
        return bool(occurrence_key and occurrence_key in occurrences)

    def _todo_list_status_text(self, item: dict[str, Any], now: QDateTime) -> str:
        if bool(item.get("completed", False)):
            return "已完成"
        next_dt = self._todo_datetime_from_iso(item.get("next_remind_at"), QDateTime())
        next_occurrence = str(item.get("next_remind_occurrence", "") or "")
        occurrence_key = str(item.get("_occurrence_date", "") or "")
        if next_dt.isValid() and (not next_occurrence or next_occurrence == occurrence_key):
            return f"已顺延至 {next_dt.toString('HH:mm')}"
        if self._todo_is_reminded(item):
            return "已提醒"
        due_at = self._todo_due_at(item)
        if due_at.isValid() and int(due_at.toSecsSinceEpoch()) <= int(now.toSecsSinceEpoch()):
            return f"已过期 {self._todo_human_elapsed(int(due_at.secsTo(now)))}"
        return ""

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

    def _add_todo_empty_state(
        self,
        date: QDate,
        title_text: str = "",
        subtitle_text: str = "",
        action_text: str = "",
        action_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        row = QListWidgetItem()
        row.setFlags(Qt.ItemFlag.NoItemFlags)
        row.setSizeHint(QSize(0, 128 if action_text else 96))
        self._todo_list.addItem(row)
        box = QWidget()
        box.setObjectName("TodoEmptyState")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(5)
        if not title_text or not subtitle_text:
            title_text, subtitle_text = self._todo_filter_empty_text(date, "")
        title = QLabel(title_text)
        title.setObjectName("TodoEmptyTitle")
        subtitle = QLabel(subtitle_text)
        subtitle.setObjectName("TodoEmptySubtitle")
        title.setStyleSheet("color:#334155; font-size:14px; font-weight:900; background:transparent;")
        subtitle.setStyleSheet("color:#94a3b8; font-size:12px; font-weight:700; background:transparent;")
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        if action_text:
            action = QPushButton(action_text)
            action.setObjectName("TodoEmptyAction")
            action.setCursor(Qt.CursorShape.PointingHandCursor)
            action.setFixedHeight(28)
            action.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            if callable(action_callback):
                action.clicked.connect(action_callback)
            action_row = QHBoxLayout()
            action_row.setContentsMargins(0, 2, 0, 0)
            action_row.addWidget(action)
            action_row.addStretch(1)
            layout.addLayout(action_row)
        layout.addStretch(1)
        box.setStyleSheet(
            "QWidget#TodoEmptyState { background:#f8fafc; border:1px solid #f1f5f9; border-radius:10px; }"
            "QPushButton#TodoEmptyAction { background:#ffffff; color:#334155; border:1px solid #dbe4ef;"
            " border-radius:8px; padding:0px 10px; font-size:12px; font-weight:800; }"
            "QPushButton#TodoEmptyAction:hover { background:#f1f5f9; color:#0f172a; border-color:#cbd5e1; }"
        )
        self._todo_list.setItemWidget(row, box)

    def _dispose_todo_item_widget(self, widget: Optional[QWidget]) -> None:
        if widget is None:
            return
        try:
            for child in widget.findChildren(QWidget):
                try:
                    child.blockSignals(True)
                    child.hide()
                    child.setParent(None)
                    child.deleteLater()
                except Exception:
                    pass
            widget.blockSignals(True)
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        except Exception:
            pass

    def _clear_todo_list_without_artifacts(self) -> None:
        if not hasattr(self, "_todo_list"):
            return
        try:
            for row_index in range(self._todo_list.count()):
                row = self._todo_list.item(row_index)
                widget = self._todo_list.itemWidget(row)
                if widget is None:
                    continue
                try:
                    self._todo_list.removeItemWidget(row)
                except Exception:
                    pass
                self._dispose_todo_item_widget(widget)
        except Exception:
            pass
        self._todo_list.clear()

    def _refresh_todo_calendar_markers(self) -> None:
        if not hasattr(self, "_todo_calendar"):
            return
        try:
            if isinstance(self._todo_calendar, _TodoCalendarWidget):
                self._todo_calendar.hide_tooltip()
            first = QDate(int(self._todo_calendar.yearShown()), int(self._todo_calendar.monthShown()), 1)
            if not first.isValid():
                return
            start = first.addDays(-(int(first.dayOfWeek()) - 1))
            visible_dates = [start.addDays(offset) for offset in range(42)]
            for extra_date in (self._todo_calendar.selectedDate(), QDate.currentDate()):
                if extra_date.isValid() and all(_date_key(extra_date) != _date_key(date) for date in visible_dates):
                    visible_dates.append(extra_date)
            todo_dates: set[str] = set()
            tooltips: dict[str, str] = {}
            date_status: dict[str, dict[str, int]] = {}
            now_ts = int(QDateTime.currentDateTime().toSecsSinceEpoch())
            source_items = self._current_todo_items()

            def compact_summary_text(value: object, limit: int = 26) -> str:
                text = str(value or "").strip() or "未命名待办"
                return text if len(text) <= limit else f"{text[:limit - 1]}..."

            for date in visible_dates:
                items: list[dict[str, Any]] = []
                for raw_item in source_items:
                    occurrence = self._todo_occurrence_for_date(raw_item, date)
                    if occurrence is not None:
                        items.append(occurrence)
                if not items:
                    continue
                items.sort(key=self._todo_sort_key)
                key = _date_key(date)
                todo_dates.add(key)
                completed_items = [x for x in items if bool(x.get("completed", False))]
                active_items = [x for x in items if not bool(x.get("completed", False))]
                overdue_items = [
                    x for x in active_items
                    if self._todo_due_at(x).isValid() and int(self._todo_due_at(x).toSecsSinceEpoch()) <= now_ts
                ]
                active_count = max(0, len(active_items) - len(overdue_items))
                completed_count = len(completed_items)
                overdue_count = len(overdue_items)
                date_status[key] = {
                    "active": active_count,
                    "completed": completed_count,
                    "overdue": overdue_count,
                }
                summary_bits = []
                if active_count:
                    summary_bits.append(f"未完成 {active_count}")
                if completed_count:
                    summary_bits.append(f"已完成 {completed_count}")
                if overdue_count:
                    summary_bits.append(f"已到点 {overdue_count}")
                lines = [date.toString("M月d日") + (f"  {' / '.join(summary_bits)}" if summary_bits else "")]
                for item in items[:6]:
                    if bool(item.get("completed", False)):
                        status_prefix = "已完成"
                    elif item in overdue_items:
                        status_prefix = "已到点"
                    else:
                        status_prefix = "未完成"
                    lines.append(f"{status_prefix}  {self._todo_display_time(item)} {compact_summary_text(item.get('title', '未命名待办'))}")
                if len(items) > 6:
                    lines.append(f"... 还有 {len(items) - 6} 项")
                tooltips[key] = "\n".join(lines)
            if isinstance(self._todo_calendar, _TodoCalendarWidget):
                self._todo_calendar.set_todo_summary(todo_dates, tooltips, date_status)
        except Exception:
            get_logger().exception("刷新待办日历标记失败")

    def _refresh_todo_after_calendar_selection(self) -> None:
        self._refresh_todo_list()
        try:
            QTimer.singleShot(0, self._refresh_todo_calendar_markers)
        except Exception:
            self._refresh_todo_calendar_markers()

    def _refresh_todo_list(self) -> None:
        paused_widgets: list[QWidget] = []
        try:
            if not all(hasattr(self, attr) for attr in ("_todo_date_label", "_todo_list")):
                return
            for widget in (
                getattr(self, "_todo_side", None),
                getattr(self, "_todo_list", None),
                self._todo_list.viewport() if hasattr(self, "_todo_list") else None,
            ):
                if isinstance(widget, QWidget):
                    widget.setUpdatesEnabled(False)
                    paused_widgets.append(widget)
            date = self._selected_todo_date()
            weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            weekday = weekdays[max(0, min(6, int(date.dayOfWeek()) - 1))]
            self._clear_todo_list_without_artifacts()
            self._update_todo_overview()
            self._refresh_todo_stat_card_state()
            all_items, include_date, scope_title = self._todo_items_for_view_scope(date)
            self._todo_date_label.setText(scope_title or f"{date.month()}月{date.day()}日 {weekday}")
            now = QDateTime.currentDateTime()
            now_ts = int(now.toSecsSinceEpoch())
            overdue_all_items = [
                item for item in all_items
                if not bool(item.get("completed", False))
                and not bool(item.get("struck_off", False))
                and self._todo_due_at(item).isValid()
                and int(self._todo_due_at(item).toSecsSinceEpoch()) <= now_ts
            ]
            self._set_todo_overdue_banner(overdue_all_items)
            filter_name = str(getattr(self, "_todo_list_filter", "") or "")
            query = str(getattr(getattr(self, "_todo_search", None), "text", lambda: "")() or "")
            items = [
                item for item in all_items
                if self._todo_item_matches_filter(item, filter_name, now)
                and self._todo_item_matches_search(item, query, now)
            ]
            batch_mode = bool(getattr(self, "_todo_batch_mode", False))
            if not items:
                empty_title, empty_subtitle = self._todo_filter_empty_text(date, filter_name, query, scope_title)
                if str(query or "").strip():
                    self._add_todo_empty_state(
                        date,
                        empty_title,
                        empty_subtitle,
                        "清空搜索",
                        lambda *_: getattr(self, "_todo_search", None).clear()
                        if isinstance(getattr(self, "_todo_search", None), QLineEdit)
                        else None,
                    )
                else:
                    self._add_todo_empty_state(date, empty_title, empty_subtitle)
                self._refresh_todo_calendar_markers()
                return
            grouped: list[tuple[str, list[dict[str, Any]]]] = []
            group_order = ["重要", "按时间", "已完成", "已到点"]
            for group_name in group_order:
                group_items = [item for item in items if self._todo_group_label(item, now) == group_name]
                if group_items:
                    grouped.append((group_name, group_items))
            show_groups = len(items) > 1
            for group_name, group_items in grouped:
                if show_groups:
                    self._add_todo_group_header(group_name, len(group_items))
                for item in group_items:
                    self._add_todo_list_item(item, batch_mode=batch_mode, now=now, include_date=include_date)
            self._refresh_todo_calendar_markers()
        except Exception:
            get_logger().exception("刷新待办列表失败")
        finally:
            for widget in reversed(paused_widgets):
                try:
                    widget.setUpdatesEnabled(True)
                    widget.update()
                except Exception:
                    pass

    def _add_todo_list_item(self, item: dict[str, Any], *, batch_mode: bool, now: QDateTime, include_date: bool = False) -> None:
        try:
            row = QListWidgetItem()
            row.setData(Qt.ItemDataRole.UserRole, str(item.get("id", "")))
            status_text = self._todo_list_status_text(item, now)
            widget = _TodoListItemWidget(
                item,
                time_text=self._todo_display_time(item, include_date=bool(include_date)),
                status_text=status_text,
                edit_icon=self._asset_icon("icon_todo_edit.svg", self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)),
                delete_icon=self._asset_icon("icon_todo_delete.svg", self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)),
                batch_mode=batch_mode,
            )
            row.setSizeHint(QSize(0, max(62, int(widget.sizeHint().height()) + 12)))
            self._todo_list.addItem(row)
            widget.editRequested.connect(self._open_todo_dialog)
            widget.completeRequested.connect(self._toggle_todo_completed)
            widget.snoozeRequested.connect(self._snooze_todo_from_list)
            widget.deleteRequested.connect(self._delete_todo)
            self._todo_list.setItemWidget(row, widget)
        except Exception:
            get_logger().exception("添加待办列表项失败")

    def _schedule_todo_checks(self) -> None:
        if not self._feature_enabled("todo"):
            return
        try:
            if not self._todo_timer.isActive():
                self._todo_timer.start()
        except Exception:
            pass
        try:
            QTimer.singleShot(1000, self._check_due_todos)
        except Exception:
            pass

    def _todo_due_at(self, item: dict[str, Any]) -> QDateTime:
        start = self._todo_datetime_from_iso(item.get("_occurrence_start", item.get("start")), QDateTime())
        return start.addSecs(-int(item.get("reminder_minutes", 10) or 0) * 60)

    def _todo_trigger_at(self, item: dict[str, Any]) -> QDateTime:
        next_dt = self._todo_datetime_from_iso(item.get("next_remind_at"), QDateTime())
        next_occurrence = str(item.get("next_remind_occurrence", "") or "")
        occurrence = str(item.get("_occurrence_date", "") or "")
        if next_dt.isValid() and (not next_occurrence or next_occurrence == occurrence):
            return next_dt
        return self._todo_due_at(item)

    def _check_due_todos(self) -> None:
        if not self._feature_enabled("todo"):
            return
        try:
            self._todo_items = self._current_todo_items()
            self._refresh_tray_tooltip()
            if self._todo_popup is not None and self._todo_popup.isVisible():
                return
            if self._cat_reminder_session is not None:
                return
            now = QDateTime.currentDateTime()
            now_ts = int(now.toSecsSinceEpoch())
            due: list[dict[str, Any]] = []
            for item in self._todo_items:
                if bool(item.get("completed", False)) or bool(item.get("struck_off", False)):
                    continue
                repeat = self._todo_repeat(item)
                if repeat == "none":
                    occurrences = [self._todo_occurrence_for_date(item, self._todo_datetime_from_iso(item.get("start"), now).date())]
                else:
                    occurrences = [self._todo_occurrence_for_date(item, now.date().addDays(offset)) for offset in range(0, 8)]
                for occurrence in [x for x in occurrences if x is not None]:
                    occurrence_key = str(occurrence.get("_occurrence_date", ""))
                    reminded_occurrences = occurrence.get("reminded_occurrences") if isinstance(occurrence.get("reminded_occurrences"), list) else []
                    if repeat == "none" and str(occurrence.get("reminded_at", "")).strip():
                        continue
                    if repeat != "none" and occurrence_key in reminded_occurrences:
                        continue
                    trigger = self._todo_trigger_at(occurrence)
                    if trigger.isValid() and now_ts >= int(trigger.toSecsSinceEpoch()):
                        due.append(occurrence)
            if not due:
                return
            due.sort(key=self._todo_sort_key)
            item = due[0]
            if bool(item.get("important", False)):
                self._show_important_todo_reminder(item)
            else:
                self._show_todo_popup(item)
        except Exception:
            get_logger().exception("检查待办提醒失败")

    def _show_important_todo_reminder(self, item: dict[str, Any]) -> None:
        if self._cat_reminder_session is not None:
            return
        reminder = self._current_cat_reminder_settings()
        title = str(item.get("title", "未命名待办")).strip()
        content = str(item.get("content", "")).strip()
        when = self._todo_display_time(item, include_date=True)
        message = f"{title}\n{content}\n{when}".strip()
        item_id = str(item.get("id", ""))
        occurrence_key = str(item.get("_occurrence_date", ""))
        try:
            session = CatReminderSession(
                duration_seconds=int(reminder.get("duration_seconds", 20)),
                title="待办提醒",
                message=message,
                voice_enabled=bool(reminder.get("voice_enabled", False)),
                icon_path=self._assets_dir() / "deepcat_logo.svg",
                exit_enabled=bool(reminder.get("exit_enabled", True)),
                countdown_enabled=False,
                snooze_enabled=True,
                snooze_minutes=int(item.get("snooze_minutes", 10) or 10),
            )
            self._cat_reminder_session = session

            def done() -> None:
                if self._cat_reminder_session is session:
                    self._cat_reminder_session = None
                self._schedule_cat_reminder()

            session.snoozeRequested.connect(lambda minutes: self._snooze_todo(item_id, occurrence_key, minutes))
            session.dismissRequested.connect(lambda: self._mark_todo_reminded(item_id, occurrence_key))
            session.finished.connect(done)
            session.start()
            self._mark_todo_just_reminded(item)
        except Exception:
            self._cat_reminder_session = None
            get_logger().exception("显示重要待办提醒失败")

    def _show_todo_popup(self, item: dict[str, Any]) -> None:
        try:
            payload = dict(item)
            payload["_display_time"] = self._todo_display_time(item, include_date=True)
            popup = _TodoReminderPopup(payload)
            self._todo_popup = popup
            popup.snoozeRequested.connect(self._snooze_todo)
            popup.dismissRequested.connect(self._mark_todo_reminded)
            popup.destroyed.connect(lambda *_: setattr(self, "_todo_popup", None))
            popup.resize(360, popup.sizeHint().height())
            self._position_todo_popup(popup)
            popup.show()
            popup.raise_()
            popup.activateWindow()
            self._mark_todo_just_reminded(item)
        except Exception:
            self._todo_popup = None
            get_logger().exception("显示待办弹窗失败")

    def _position_todo_popup(self, popup: QWidget) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        popup.adjustSize()
        width = min(380, max(320, popup.width()))
        height = popup.height()
        popup.resize(width, height)
        popup.move(geo.right() - popup.width() - 18, geo.bottom() - popup.height() - 18)

    def _update_todo_item(self, item_id: str, update: Callable[[dict[str, Any]], None]) -> bool:
        items = self._current_todo_items()
        changed = False
        for item in items:
            if str(item.get("id", "")) == str(item_id):
                update(item)
                changed = True
                break
        if changed:
            return self._save_todo_items(items)
        return False

    def _mark_todo_reminded(self, item_id: str, occurrence_key: str = "") -> None:
        def apply(item: dict[str, Any]) -> None:
            if self._todo_repeat(item) == "none":
                item["reminded_at"] = QDateTime.currentDateTime().toString(Qt.DateFormat.ISODate)
            else:
                occurrences = item.get("reminded_occurrences") if isinstance(item.get("reminded_occurrences"), list) else []
                key = str(occurrence_key or QDate.currentDate().toString(Qt.DateFormat.ISODate))
                if key not in occurrences:
                    occurrences.append(key)
                item["reminded_occurrences"] = occurrences
            item["next_remind_at"] = ""
            item["next_remind_occurrence"] = ""

        self._update_todo_item(item_id, apply)

    def _snooze_todo(self, item_id: str, occurrence_key: str, minutes: int) -> bool:
        minutes = int(max(1, min(24 * 60, int(minutes))))

        def snooze_base_time(item: dict[str, Any]) -> QDateTime:
            occurrence = dict(item)
            key = str(occurrence_key or "")
            date = QDate.fromString(key, Qt.DateFormat.ISODate) if key else QDate()
            if date.isValid():
                occurrence = self._todo_occurrence_for_date(item, date) or occurrence
            base = self._todo_trigger_at(occurrence)
            return base if base.isValid() else QDateTime.currentDateTime()

        def apply(item: dict[str, Any]) -> None:
            next_remind_at = snooze_base_time(item).addSecs(minutes * 60)
            item["snooze_minutes"] = minutes
            item["next_remind_at"] = next_remind_at.toString(Qt.DateFormat.ISODate)
            item["next_remind_occurrence"] = str(occurrence_key or "")
            item["reminded_at"] = ""

        return self._update_todo_item(item_id, apply)

    def _snooze_todo_from_list(self, item_id: str, occurrence_key: str, minutes: int) -> None:
        if self._snooze_todo(item_id, occurrence_key, minutes):
            minutes = int(minutes)
            if minutes >= 24 * 60:
                text = "已顺延至明天。"
            elif minutes >= 60:
                hours = minutes // 60
                rest = minutes % 60
                text = f"已顺延 {hours} 小时。" if rest == 0 else f"已顺延 {hours} 小时 {rest} 分钟。"
            else:
                text = f"已顺延 {minutes} 分钟。"
            self._show_todo_status(text, tone="success", auto_hide_ms=1800)
