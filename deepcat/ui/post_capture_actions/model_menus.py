from __future__ import annotations

import time
from typing import Callable, Optional
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer
from PyQt6.QtGui import QColor, QBrush, QCursor, QFontMetrics, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QPushButton,
    QHBoxLayout,
    QFrame,
    QWidget,
    QGraphicsDropShadowEffect,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QToolButton,
    QScrollArea,
    QSizePolicy,
    QCheckBox,
)
from deepcat.ui.popup_behavior import find_parent_with_attr, mark_anchor_popup_closed, should_skip_anchor_popup
from PyQt6.QtWidgets import QFrame, QPushButton
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame
from PyQt6.QtGui import QPainter, QPixmap, QIcon, QPen, QColor
from PyQt6.QtCore import QPointF, QRectF, QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton

from deepcat.ui.post_capture_actions._shared import (
    _LOCAL_GEMINI_TEST_FAILURE_COOLDOWN_SECONDS,
    _LOCAL_GEMINI_TEST_SUCCESS_COOLDOWN_SECONDS,
    _ROUNDED_POPUP_MENU_MAX_HEIGHT,
    logger,
)
from deepcat.ui.post_capture_actions.helpers import wrap_error_message


_local_gemini_test_next_at = 0.0


class ModelDeleteButton(QToolButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QToolButton { background: transparent; border: none; }")

    def paintEvent(self, event) -> None:
        from PyQt6.QtCore import QRectF, QPointF
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bg_color = QColor("#f1f5f9") if self.underMouse() else QColor("#ffffff")
        painter.setPen(QPen(QColor("#e2e8f0"), 1))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 5.0, 5.0)
        color = QColor("#ef4444") if self.underMouse() else QColor("#94a3b8")
        pen = QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        r = self.rect().adjusted(3, 3, -3, -3)
        c = r.center()
        radius = 7.0
        painter.drawEllipse(QRectF(c.x() - radius, c.y() - radius, radius * 2.0, radius * 2.0))
        size = 2.4
        painter.drawLine(QPointF(c.x() - size, c.y() - size), QPointF(c.x() + size, c.y() + size))
        painter.drawLine(QPointF(c.x() + size, c.y() - size), QPointF(c.x() - size, c.y() + size))
        painter.end()


class ModelClearButton(QToolButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QToolButton { background: transparent; border: none; }")

    def paintEvent(self, event) -> None:
        from PyQt6.QtCore import QRectF, QPointF
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bg_color = QColor("#f1f5f9") if self.underMouse() else QColor("#ffffff")
        painter.setPen(QPen(QColor("#e2e8f0"), 1))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 5.0, 5.0)

        color = QColor("#f97316") if self.underMouse() else QColor("#94a3b8")
        pen = QPen(color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        painter.drawLine(QPointF(6.0, 6.5), QPointF(16.0, 6.5))
        painter.drawLine(QPointF(9.5, 4.5), QPointF(12.5, 4.5))
        painter.drawLine(QPointF(9.5, 4.5), QPointF(9.5, 6.5))
        painter.drawLine(QPointF(12.5, 4.5), QPointF(12.5, 6.5))

        painter.drawLine(QPointF(7.5, 7.5), QPointF(9.0, 16.5))
        painter.drawLine(QPointF(14.5, 7.5), QPointF(13.0, 16.5))
        painter.drawLine(QPointF(9.0, 16.5), QPointF(13.0, 16.5))

        painter.drawLine(QPointF(10.0, 9.5), QPointF(10.5, 14.5))
        painter.drawLine(QPointF(12.0, 9.5), QPointF(11.5, 14.5))

        painter.end()


class ModelTestButton(QToolButton):
    def __init__(self, model_name: str, popup: _OcrModelMenuPopup, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model_name = str(model_name)
        self._popup = popup
        self._state = "idle" # "idle", "testing", "success", "failed"
        self._worker: Optional[QThread] = None
        self._angle = 0

        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QToolButton { background: transparent; border: none; }")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)

        self.clicked.connect(self._on_clicked)

    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "testing":
            if not self._timer.isActive():
                self._timer.start(16)
        else:
            self._timer.stop()
            self._angle = 0
        if state != "failed":
            self.setToolTip("")
        self.update()

    def _rotate(self) -> None:
        self._angle = (self._angle + 6) % 360
        self.update()

    def stop_timer(self) -> None:
        self._timer.stop()

    def set_worker(self, worker: Optional[QThread]) -> None:
        self._worker = worker

    def worker(self) -> Optional[QThread]:
        return self._worker

    def has_active_worker(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _on_clicked(self) -> None:
        if self._state == "testing":
            return
        self._popup.enqueue_model_test(self._model_name)

    def paintEvent(self, event) -> None:
        from PyQt6.QtCore import QRectF
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bg_color = QColor("#f1f5f9") if self.underMouse() else QColor("#ffffff")
        painter.setPen(QPen(QColor("#e2e8f0"), 1))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 5.0, 5.0)
        r = self.rect().adjusted(3, 3, -3, -3)
        c = r.center()
        radius = 7.0

        if self._state == "testing":
            pen = QPen(QColor("#f97316"), 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.translate(c.x(), c.y())
            painter.rotate(self._angle)
            painter.drawArc(QRectF(-radius, -radius, radius * 2.0, radius * 2.0), 0, 270 * 16)
        elif self._state == "success":
            pen = QPen(QColor("#22c55e"), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawEllipse(QRectF(c.x() - radius, c.y() - radius, radius * 2.0, radius * 2.0))

            path = QPainterPath()
            path.moveTo(c.x() - 3.0, c.y())
            path.lineTo(c.x() - 0.8, c.y() + 2.5)
            path.lineTo(c.x() + 3.5, c.y() - 2.5)
            painter.drawPath(path)
        elif self._state == "failed":
            pen = QPen(QColor("#ef4444"), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawEllipse(QRectF(c.x() - radius, c.y() - radius, radius * 2.0, radius * 2.0))
            painter.drawLine(QPointF(c.x(), c.y() - 3.2), QPointF(c.x(), c.y() + 0.8))
            painter.drawPoint(QPointF(c.x(), c.y() + 3.2))
        else:
            color = QColor("#1e293b") if self.underMouse() else QColor("#94a3b8")
            pen = QPen(color, 1.8)
            painter.setPen(pen)
            painter.drawEllipse(QRectF(c.x() - radius, c.y() - radius, radius * 2.0, radius * 2.0))

            poly = QPolygonF([
                QPointF(c.x() - 1.5, c.y() - 3.0),
                QPointF(c.x() - 1.5, c.y() + 3.0),
                QPointF(c.x() + 3.0, c.y())
            ])
            painter.setBrush(QBrush(color))
            painter.drawPolygon(poly)

        painter.end()


class GroupBatchTestButton(QToolButton):
    def __init__(self, group_name: str, popup: _OcrModelMenuPopup, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group_name = str(group_name)
        self._popup = popup
        self._state = "idle" # "idle", "testing", "success", "failed"
        self._angle = 0

        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QToolButton { background: transparent; border: none; }")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)

        self.clicked.connect(self._on_clicked)

    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "testing":
            if not self._timer.isActive():
                self._timer.start(16)
        else:
            self._timer.stop()
            self._angle = 0
        self.update()

    def _rotate(self) -> None:
        self._angle = (self._angle + 6) % 360
        self.update()

    def stop_timer(self) -> None:
        self._timer.stop()

    def _on_clicked(self) -> None:
        if self._state == "testing":
            return
        self._popup.start_group_batch_test(self._group_name)

    def paintEvent(self, event) -> None:
        if self._state != "idle":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        r = self.rect().adjusted(3, 3, -3, -3)
        c = r.center()

        color = QColor("#1e293b") if self.underMouse() else QColor("#94a3b8")
        from PyQt6.QtGui import QPolygonF
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        poly1 = QPolygonF([
            QPointF(c.x() - 5.5, c.y() - 3.5),
            QPointF(c.x() - 5.5, c.y() + 3.5),
            QPointF(c.x() - 0.5, c.y())
        ])
        poly2 = QPolygonF([
            QPointF(c.x() - 0.5, c.y() - 3.5),
            QPointF(c.x() - 0.5, c.y() + 3.5),
            QPointF(c.x() + 4.5, c.y())
        ])
        painter.drawPolygon(poly1)
        painter.drawPolygon(poly2)
        painter.end()


class _OcrModelMenuPopup(QWidget):
    MAX_WIDTH = 360
    MIN_WIDTH = 180
    MAX_HEIGHT = _ROUNDED_POPUP_MENU_MAX_HEIGHT
    ITEM_HEIGHT = 32
    PANEL_PADDING = 12
    CHECK_ICON_SIZE = 14
    SCROLLBAR_IDLE_WIDTH = 4
    SCROLLBAR_HOVER_WIDTH = 6

    def __init__(
        self,
        grouped_models: list[tuple[str, list[str]]],
        current_model: str,
        on_selected: Callable[[str], None],
        parent: QWidget | None = None,
        on_delete: Optional[Callable[[str], None]] = None,
        on_batch_delete: Optional[Callable[[list[str]], None]] = None,
        search_texts: Optional[dict[str, str]] = None,
        purpose: str = "translate",
        match_parent_width: bool = False,
        active_check_color: str = "#111827",
        highlighted_models: Optional[set[str]] = None,
        highlighted_text_color: str = "#2563eb",
        highlighted_model_colors: Optional[dict[str, str]] = None,
        on_group_selected: Optional[Callable[[str], None]] = None,
        on_codex_action: Optional[Callable[..., None]] = None,
        codex_action_label_for_model: Optional[Callable[..., str]] = None,
        codex_action_color_for_model: Optional[Callable[..., str]] = None,
        show_group_move_action: bool = False,
    ) -> None:
        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        try:
            flags |= Qt.WindowType.NoDropShadowWindowHint
        except AttributeError:
            pass
        super().__init__(parent, flags)
        self.setObjectName("OcrModelMenuPopupRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self._active_button: QPushButton | QToolButton | None = None
        self._scroll: QScrollArea | None = None
        self._scrollbar = None
        self._on_selected = on_selected
        self._on_group_selected = on_group_selected
        self._on_delete = on_delete
        self._on_batch_delete = on_batch_delete
        self._on_codex_action = on_codex_action
        self._codex_action_label_for_model = codex_action_label_for_model
        self._codex_action_color_for_model = codex_action_color_for_model
        self._show_group_move_action = bool(show_group_move_action)
        self._search_texts = {str(k): str(v or "") for k, v in dict(search_texts or {}).items()}
        self._checked_icon = self._make_check_icon(str(active_check_color or "#111827"))
        self._hover_checked_icon = self._make_check_icon("#94a3b8")
        self._highlighted_models = {
            str(name or "").strip()
            for name in set(highlighted_models or set())
            if str(name or "").strip()
        }
        self._highlighted_text_color = str(highlighted_text_color or "#2563eb")
        self._highlighted_model_colors: dict[str, str] = {}
        for raw_name, raw_color in dict(highlighted_model_colors or {}).items():
            name = str(raw_name or "").strip()
            color = str(raw_color or "").strip()
            if name and color:
                self._highlighted_model_colors[name] = color
        for name in self._highlighted_models:
            self._highlighted_model_colors.setdefault(name, self._highlighted_text_color)
        self._highlighted_models.update(self._highlighted_model_colors.keys())
        self._highlighted_icon_cache: dict[str, QIcon] = {}
        self._highlighted_checked_icon = self._make_check_icon(self._highlighted_text_color)
        self._highlighted_icon_cache[self._highlighted_text_color] = self._highlighted_checked_icon
        empty_pixmap = QPixmap(self.CHECK_ICON_SIZE, self.CHECK_ICON_SIZE)
        empty_pixmap.fill(Qt.GlobalColor.transparent)
        self._empty_icon = QIcon(empty_pixmap)
        self._anchor = None
        self._model_test_buttons = {}
        self._group_test_buttons = {}
        self._test_queue = []
        self._group_batch_tests = {}
        self._max_active_workers = 4
        self._grouped_models = grouped_models
        self._purpose = "qa" if str(purpose or "").strip().lower() == "qa" else "translate"
        self._gemini_next_test_at = 0.0
        self._test_queue_retry_scheduled = False
        self._match_parent_width = match_parent_width
        self._delete_selection_mode = False
        self._selected_delete_models: set[str] = set()
        self._delete_selection_bar: QWidget | None = None
        self._delete_selection_count_label: QLabel | None = None
        self._delete_selection_confirm_btn: QPushButton | None = None
        self._normal_popup_height = 0
        self._empty_state_label: QLabel | None = None

        self._list_items: list[dict] = []
        self._build(grouped_models, current_model)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)

    def _delete_action(self, model_name: str, on_delete: Callable[[str], None]) -> None:
        self.close()
        on_delete(model_name)

    def _codex_action(self, model_name: str, provider: str = "") -> None:
        if self._on_codex_action is None:
            logger.warning(
                "[模型分组下拉] Codex按钮点击后未找到回调: model=%s provider=%s",
                model_name,
                provider,
            )
            return
        action = self._on_codex_action
        logger.info(
            "[模型分组下拉] Codex按钮已触发: model=%s provider=%s",
            model_name,
            provider,
        )
        self.close()

        def _invoke() -> None:
            try:
                try:
                    action(model_name, provider)
                    return
                except TypeError:
                    try:
                        action(model_name)
                        return
                    except TypeError:
                        action()
            except Exception:
                logger.exception(
                    "[模型分组下拉] Codex按钮回调执行失败: model=%s provider=%s",
                    model_name,
                    provider,
                )

        QTimer.singleShot(0, _invoke)

    def _batch_delete_action(self, model_names: list[str]) -> None:
        if self._on_batch_delete is None:
            return
        names = self._deletable_model_names(model_names)
        if not names:
            return
        self.close()
        self._on_batch_delete(names)

    def _enter_delete_selection_mode(self, initial_names: list[str] | None = None) -> None:
        if self._on_batch_delete is None:
            return
        self._delete_selection_mode = True
        self._selected_delete_models = set(self._deletable_model_names(initial_names or []))
        self._hide_other_model_buttons()
        self._hide_other_group_buttons()
        for item in getattr(self, "_list_items", []):
            if item.get("kind") != "model":
                continue
            checkbox = item.get("checkbox")
            if checkbox is None:
                continue
            model_name = str(item.get("text") or "")
            button = item.get("button")
            if button is not None:
                button.setIcon(self._empty_icon)
            checkbox.blockSignals(True)
            try:
                checkbox.setChecked(model_name in self._selected_delete_models)
                checkbox.setVisible(bool(item.get("deletable")))
            finally:
                checkbox.blockSignals(False)
        if self._delete_selection_bar is not None:
            self._delete_selection_bar.show()
        self._sync_delete_selection_controls()

    def _exit_delete_selection_mode(self) -> None:
        self._delete_selection_mode = False
        self._selected_delete_models.clear()
        for item in getattr(self, "_list_items", []):
            button = item.get("button")
            if button is not None:
                button.setIcon(self._checked_icon_for_button(button) if bool(button.property("model_active")) else self._empty_icon)
            checkbox = item.get("checkbox")
            if checkbox is not None:
                checkbox.blockSignals(True)
                try:
                    checkbox.setChecked(False)
                    checkbox.hide()
                finally:
                    checkbox.blockSignals(False)
        if self._delete_selection_bar is not None:
            self._delete_selection_bar.hide()
        self._sync_delete_selection_controls()

    def _toggle_delete_selection(self, model_name: str, checked: bool | None = None) -> None:
        if not self._delete_selection_mode:
            return
        names = self._deletable_model_names([model_name])
        if not names:
            return
        model_name = names[0]
        if checked is None:
            checked = model_name not in self._selected_delete_models
        if checked:
            self._selected_delete_models.add(model_name)
        else:
            self._selected_delete_models.discard(model_name)
        for item in getattr(self, "_list_items", []):
            if str(item.get("text") or "") != model_name:
                continue
            checkbox = item.get("checkbox")
            if checkbox is not None and checkbox.isChecked() != checked:
                checkbox.blockSignals(True)
                try:
                    checkbox.setChecked(bool(checked))
                finally:
                    checkbox.blockSignals(False)
        self._sync_delete_selection_controls()

    def _confirm_selected_delete_models(self) -> None:
        self._batch_delete_action(sorted(self._selected_delete_models))

    def _sync_delete_selection_controls(self) -> None:
        selected_count = len(self._selected_delete_models)
        if self._delete_selection_count_label is not None:
            self._delete_selection_count_label.setText(f"已选 {selected_count} 个")
        if self._delete_selection_confirm_btn is not None:
            self._delete_selection_confirm_btn.setEnabled(selected_count > 0)
        self._set_popup_content_height(self._normal_popup_height or self.height())

    def _set_popup_content_height(self, base_height: int) -> None:
        self._normal_popup_height = max(1, int(base_height))
        reserved_height = 40 if self._on_batch_delete is not None else 0
        self.setFixedHeight(self._normal_popup_height + reserved_height)
        self._adjust_position()

    def _delete_group_action(self, prov_name: str, parent_dialog: QWidget) -> None:
        self.close()
        try:
            parent_dialog.delete_provider_group(prov_name)
        except Exception:
            pass

    def _clear_group_action(self, prov_name: str, parent_dialog: QWidget) -> None:
        self.close()
        try:
            parent_dialog.clear_provider_models(prov_name)
        except Exception:
            pass

    def _move_group_action(self, prov_name: str, parent_dialog: QWidget) -> None:
        self.close()
        try:
            parent_dialog._move_current_model_to_provider(prov_name)
        except Exception:
            pass

    def _deletable_model_names(self, model_names: object) -> list[str]:
        excluded_names = {"Google翻译", "DeepLX", "自定义模型"}
        names: list[str] = []
        seen: set[str] = set()
        if isinstance(model_names, str):
            raw_names = [model_names]
        else:
            try:
                raw_names = list(model_names or [])
            except TypeError:
                raw_names = []
        for raw_name in raw_names:
            name = str(raw_name or "").strip()
            if not name or name in seen or name in excluded_names:
                continue
            names.append(name)
            seen.add(name)
        return names

    def _model_names_for_provider(self, provider: str, *, visible_only: bool = False) -> list[str]:
        provider = str(provider or "").strip()
        return self._deletable_model_names([
            str(item.get("text") or "")
            for item in getattr(self, "_list_items", [])
            if item.get("kind") == "model"
            and str(item.get("provider") or "").strip() == provider
            and (not visible_only or bool(item.get("widget") is not None and item["widget"].isVisible()))
        ])

    def _visible_model_names(self) -> list[str]:
        return self._deletable_model_names([
            str(item.get("text") or "")
            for item in getattr(self, "_list_items", [])
            if item.get("kind") == "model"
            and item.get("widget") is not None
            and item["widget"].isVisible()
        ])

    def _item_for_container(self, container: QWidget) -> dict | None:
        for item in getattr(self, "_list_items", []):
            if item.get("widget") is container:
                return item
        return None

    def _checked_icon_for_button(self, button: QWidget) -> QIcon:
        color = str(button.property("highlight_color") or "").strip()
        if color:
            return self._highlighted_check_icon_for_color(color)
        return self._checked_icon

    def _highlight_color_for_model(self, model_name: str) -> str:
        model_name = str(model_name or "").strip()
        if not model_name:
            return ""
        return str(self._highlighted_model_colors.get(model_name, "") or "").strip()

    def _highlighted_check_icon_for_color(self, color: str) -> QIcon:
        color = str(color or self._highlighted_text_color or "#2563eb").strip()
        icon = self._highlighted_icon_cache.get(color)
        if icon is None:
            icon = self._make_check_icon(color)
            self._highlighted_icon_cache[color] = icon
        return icon

    def _codex_action_label(self, model_name: str, provider: str = "") -> str:
        model_name = str(model_name or "").strip()
        if callable(self._codex_action_label_for_model):
            try:
                label = str(self._codex_action_label_for_model(model_name, provider) or "").strip()
                if label:
                    return label
            except TypeError:
                try:
                    label = str(self._codex_action_label_for_model(model_name) or "").strip()
                    if label:
                        return label
                except Exception:
                    pass
            except Exception:
                pass
        return "还原Codex" if model_name in self._highlighted_models else "填入Codex"

    def _codex_action_color(self, model_name: str, provider: str = "") -> str:
        color = ""
        if callable(self._codex_action_color_for_model):
            try:
                color = str(self._codex_action_color_for_model(model_name, provider) or "").strip()
            except TypeError:
                try:
                    color = str(self._codex_action_color_for_model(model_name) or "").strip()
                except Exception:
                    color = ""
            except Exception:
                color = ""
        return color or "#2563eb"

    def _show_model_context_menu(self, item: dict, global_pos: QPoint) -> bool:
        if self._on_delete is None and self._on_batch_delete is None:
            return False

        kind = str(item.get("kind") or "")
        provider = str(item.get("provider") or "")
        current_model = str(item.get("text") or "").strip() if kind == "model" else ""
        current_names = self._deletable_model_names([current_model])
        group_names = self._model_names_for_provider(provider)
        visible_names = self._visible_model_names()

        menu_items: list[tuple] = []
        if current_names and self._on_delete is not None:
            menu_items.append((
                "删除当前模型",
                lambda name=current_names[0]: self._delete_action(name, self._on_delete),
                True,
            ))

        if group_names and self._on_batch_delete is not None:
            menu_items.append((
                f"删除本分组模型（{len(group_names)}）",
                lambda names=list(group_names): self._batch_delete_action(names),
                True,
            ))

        if visible_names and self._on_batch_delete is not None:
            menu_items.append((
                "勾选删除模式",
                lambda: QTimer.singleShot(0, self._enter_delete_selection_mode),
                True,
            ))

        if not menu_items:
            menu_items.append(("没有可删除的模型", None, False))

        popup = OcrGenericMenuPopup(menu_items, parent=self)
        popup.show_at_pos(global_pos)
        return True

    def _on_search_text_changed(self, search_text: str) -> None:
        search_tokens = [token for token in search_text.strip().lower().split() if token]
        provider_has_visible_model = {}
        for item in self._list_items:
            if item["kind"] == "model":
                haystack = str(item.get("search_text") or item.get("text") or "").lower()
                visible = not search_tokens or all(token in haystack for token in search_tokens)
                if not visible:
                    self._hide_model_delete_button(item.get("widget"))
                item["widget"].setVisible(visible)
                prov = item["provider"]
                if visible:
                    provider_has_visible_model[prov] = True

        visible_count = 0
        for item in self._list_items:
            if item["kind"] == "header":
                prov = item["provider"]
                visible = bool(provider_has_visible_model.get(prov, False))
                if not visible:
                    self._hide_model_delete_button(item.get("widget"))
                item["widget"].setVisible(visible)
                if visible:
                    visible_count += 1
            else:
                if item["widget"].isVisible():
                    visible_count += 1

        total_models = sum(len(names) for _, names in self._grouped_models)
        search_h = 0 if total_models <= 5 else (28 + 6)
        empty_label = getattr(self, "_empty_state_label", None)
        show_empty = visible_count <= 0
        if isinstance(empty_label, QLabel):
            empty_label.setText("没有匹配的模型\n换个关键词试试" if search_tokens else "暂无可用模型配置")
            empty_label.setVisible(show_empty)
        empty_height = 64 if show_empty else 0
        content_height = (
            empty_height
            if show_empty
            else visible_count * self.ITEM_HEIGHT + max(0, visible_count - 1) * 2
        )
        popup_height = min(self.MAX_HEIGHT, content_height + self.PANEL_PADDING + search_h)

        min_height = self.ITEM_HEIGHT + self.PANEL_PADDING + search_h
        self._set_popup_content_height(max(min_height, popup_height) + 16)

    def _build(self, grouped_models: list[tuple[str, list[str]]], current_model: str) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(0)

        panel = QWidget(self)
        panel.setObjectName("OcrModelMenuPopupPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        panel.setStyleSheet("""
            QWidget#OcrModelMenuPopupPanel {
                background: #ffffff;
                border: 1px solid #dfe4ec;
                border-radius: 8px;
            }
        """)
        if self._on_batch_delete is None:
            shadow = QGraphicsDropShadowEffect(panel)
            shadow.setBlurRadius(12)
            shadow.setColor(QColor(15, 23, 42, 38))
            shadow.setOffset(0, 3)
            panel.setGraphicsEffect(shadow)
        root_layout.addWidget(panel)

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(6, 6, 6, 6)
        panel_layout.setSpacing(6)

        self.search_edit = QLineEdit(panel)
        self.search_edit.setPlaceholderText("搜索模型...")
        self.search_edit.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.search_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.search_edit.setInputMethodHints(Qt.InputMethodHint.ImhNone)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setStyleSheet("""
            QLineEdit {
                padding: 4px 8px;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                font-size: 12px;
                background: #f8fafc;
            }
            QLineEdit:focus {
                border-color: #1e293b;
                background: #ffffff;
            }
        """)
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        panel_layout.addWidget(self.search_edit)

        total_models = sum(len(names) for _, names in grouped_models)
        if total_models <= 5:
            self.search_edit.hide()
            panel_layout.setSpacing(0)
        else:
            self.search_edit.show()
            panel_layout.setSpacing(6)

        scroll = QScrollArea(panel)
        scroll.setObjectName("OcrModelMenuPopupScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        panel_layout.addWidget(scroll)
        self._scroll = scroll
        self._scrollbar = scroll.verticalScrollBar()
        self._scrollbar.setFixedWidth(self.SCROLLBAR_IDLE_WIDTH)
        self._scrollbar.installEventFilter(self)

        selection_bar = QWidget(panel)
        selection_bar.setObjectName("OcrModelDeleteSelectionBar")
        selection_bar.setFixedHeight(34)
        selection_bar.hide()
        selection_layout = QHBoxLayout(selection_bar)
        selection_layout.setContentsMargins(6, 2, 6, 2)
        selection_layout.setSpacing(6)

        selection_count = QLabel("已选 0 个", selection_bar)
        selection_count.setObjectName("OcrModelDeleteSelectionCount")
        selection_layout.addWidget(selection_count, 1)

        cancel_btn = QPushButton("取消", selection_bar)
        cancel_btn.setObjectName("OcrModelDeleteSelectionCancel")
        cancel_btn.setFixedHeight(26)
        cancel_btn.clicked.connect(self._exit_delete_selection_mode)
        selection_layout.addWidget(cancel_btn)

        confirm_btn = QPushButton("删除", selection_bar)
        confirm_btn.setObjectName("OcrModelDeleteSelectionConfirm")
        confirm_btn.setFixedHeight(26)
        confirm_btn.setEnabled(False)
        confirm_btn.clicked.connect(self._confirm_selected_delete_models)
        selection_layout.addWidget(confirm_btn)

        panel_layout.addWidget(selection_bar)
        self._delete_selection_bar = selection_bar
        self._delete_selection_count_label = selection_count
        self._delete_selection_confirm_btn = confirm_btn

        content = QWidget()
        content.setObjectName("OcrModelMenuPopupContent")
        list_layout = QVBoxLayout(content)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(2)
        scroll.setWidget(content)

        rows: list[tuple[str, str]] = []
        for provider, names in grouped_models:
            rows.append(("header", provider))
            rows.extend(("model", name) for name in names)

        metrics = QFontMetrics(self.font())
        widest = max(
            (
                metrics.horizontalAdvance(f"[{text}]")
                if kind == "header"
                else metrics.horizontalAdvance(str(text)) + self.CHECK_ICON_SIZE + 16 + (24 if (self._on_delete is not None and str(text) not in {"Google翻译", "DeepLX", "自定义模型"}) else 0) + (76 if self._on_codex_action is not None else 0)
                for kind, text in rows
            ),
            default=0,
        )
        popup_width = min(self.MAX_WIDTH, max(self.MIN_WIDTH, widest + 74))

        if getattr(self, "_match_parent_width", False) and self.parentWidget() and hasattr(self.parentWidget(), "width"):
            parent_w = self.parentWidget().width()
            if parent_w > 0:
                popup_width = parent_w

        total_models = sum(len(names) for _, names in grouped_models)
        search_h = 0 if total_models <= 5 else (28 + 6)
        max_h = self.MAX_HEIGHT
        if getattr(self, "_match_parent_width", False) and self.parentWidget():
            try:
                from PyQt6.QtGui import QGuiApplication
                from PyQt6.QtCore import QPoint
                parent = self.parentWidget()
                top_level = parent.window() if parent is not None else None
                global_y = parent.mapToGlobal(QPoint(0, parent.height() + 2)).y()
                margin = 6
                screen = QGuiApplication.screenAt(parent.mapToGlobal(QPoint(0, 0))) or QGuiApplication.primaryScreen()
                bounds = screen.availableGeometry()
                if top_level is not None:
                    bounds = bounds.intersected(top_level.frameGeometry())
                max_usable_h = bounds.bottom() + 1 - margin - global_y
                if max_usable_h > 120:
                    max_h = min(self.MAX_HEIGHT, max_usable_h - 16)
            except Exception:
                pass

        content_height = len(rows) * self.ITEM_HEIGHT + max(0, len(rows) - 1) * list_layout.spacing()
        popup_height = min(max_h, content_height + self.PANEL_PADDING + search_h)
        needs_scroll = content_height + self.PANEL_PADDING + search_h > max_h
        text_width = popup_width - (104 if needs_scroll else 84)
        header_text_width = popup_width - (50 if needs_scroll else 36)

        base_popup_height = max(self.ITEM_HEIGHT + self.PANEL_PADDING + search_h, popup_height) + 16
        reserved_selection_height = 40 if self._on_batch_delete is not None else 0
        self.setFixedSize(popup_width + 16, base_popup_height + reserved_selection_height)
        self._normal_popup_height = base_popup_height
        content.setFixedWidth(popup_width - self.PANEL_PADDING - (12 if needs_scroll else 0))

        self.setStyleSheet("""
            QWidget#OcrModelMenuPopupRoot {
                background: transparent;
            }
            QWidget#OcrModelMenuItemContainer {
                background: transparent;
                border-radius: 6px;
            }
            QWidget#OcrModelMenuItemContainer:hover {
                background: #f3f4f6;
            }
            QWidget#OcrModelMenuGroupHeaderContainer {
                background: #eef2f7;
                border-radius: 6px;
            }
            QScrollArea#OcrModelMenuPopupScroll,
            QScrollArea#OcrModelMenuPopupScroll > QWidget,
            QWidget#OcrModelMenuPopupContent {
                background: transparent;
                border: none;
            }
            QPushButton#OcrModelMenuGroupHeader,
            QToolButton#OcrModelMenuGroupHeader,
            QLabel#OcrModelMenuGroupHeader,
            QPushButton#OcrModelMenuItem,
            QPushButton#OcrModelMenuItemActive,
            QToolButton#OcrModelMenuItem,
            QToolButton#OcrModelMenuItemActive {
                background: transparent !important;
                border: none !important;
                border-radius: 6px !important;
                color: #374151 !important;
                font-size: 13px !important;
                font-weight: 500 !important;
                padding: 0 8px !important;
                text-align: left !important;
            }
            QPushButton#OcrModelMenuGroupHeader,
            QPushButton#OcrModelMenuGroupHeader:disabled,
            QToolButton#OcrModelMenuGroupHeader,
            QToolButton#OcrModelMenuGroupHeader:disabled,
            QLabel#OcrModelMenuGroupHeader {
                background: transparent !important;
                color: #4b5563 !important;
                font-weight: 800 !important;
                padding: 0 8px !important;
                padding-left: 30px !important;
                text-align: left !important;
            }
            QPushButton#OcrModelMenuItem,
            QPushButton#OcrModelMenuItemActive,
            QToolButton#OcrModelMenuItem,
            QToolButton#OcrModelMenuItemActive {
                padding-left: 30px !important;
                text-align: left !important;
            }
            QPushButton#OcrModelMenuItem:hover,
            QToolButton#OcrModelMenuItem:hover {
                background: transparent !important;
                color: #111827 !important;
                font-weight: 800 !important;
            }
            QPushButton#OcrModelMenuItemActive,
            QToolButton#OcrModelMenuItemActive {
                background: transparent !important;
                color: #111827 !important;
                font-weight: 800 !important;
                text-align: left !important;
            }
            QPushButton#OcrModelMenuItemActive:hover,
            QToolButton#OcrModelMenuItemActive:hover {
                background: transparent !important;
                color: #111827 !important;
                font-weight: 800 !important;
            }
            QLabel#OcrModelMenuEmpty {
                min-height: 64px;
                color: #94a3b8;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 10px;
                background: transparent;
            }
            QScrollBar:vertical {
                width: 4px;
                background: transparent;
                margin: 2px 0px 2px 0px;
                border: none;
            }
            QScrollBar:vertical:hover {
                width: 6px;
            }
            QScrollBar::handle:vertical {
                background: rgba(148, 163, 184, 0.45);
                border-radius: 2px;
                min-height: 28px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(100, 116, 139, 0.65);
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
                background: transparent;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QCheckBox#OcrModelDeleteSelectionCheck {
                background: transparent;
                padding-left: 6px;
                padding-right: 2px;
            }
            QLabel#OcrModelDeleteSelectionCount {
                color: #475569;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#OcrModelDeleteSelectionCancel,
            QPushButton#OcrModelDeleteSelectionConfirm {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 0 10px;
                font-size: 12px;
                font-weight: 700;
                background: #ffffff;
                color: #334155;
            }
            QPushButton#OcrModelDeleteSelectionConfirm {
                border-color: #ef4444;
                background: #ef4444;
                color: #ffffff;
            }
            QPushButton#OcrModelDeleteSelectionConfirm:disabled {
                border-color: #fecaca;
                background: #fecaca;
                color: #ffffff;
            }
        """)

        current_provider = ""
        parent_dialog = (
            find_parent_with_attr(self, "delete_provider_group")
            or find_parent_with_attr(self, "_move_current_model_to_provider")
            or find_parent_with_attr(self, "clear_provider_models")
        )

        for kind, text in rows:
            if kind == "header":
                current_provider = text

                container = QWidget(content)
                container.setFixedHeight(self.ITEM_HEIGHT)
                container.setObjectName("OcrModelMenuGroupHeaderContainer")

                container_layout = QHBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.setSpacing(2)

                is_deletable = text != "默认分组" and parent_dialog is not None and hasattr(parent_dialog, "delete_provider_group")
                is_default_group = text == "默认分组" and parent_dialog is not None and hasattr(parent_dialog, "clear_provider_models")
                is_movable = (
                    self._show_group_move_action
                    and parent_dialog is not None
                    and hasattr(parent_dialog, "_move_current_model_to_provider")
                )
                has_action_btn = is_movable or is_deletable or is_default_group
                group_has_models = any(
                    str(group_name) == str(text) and bool(model_names)
                    for group_name, model_names in self._grouped_models
                )

                if self._on_group_selected is not None:
                    header_label = QPushButton(container)
                    header_label.setFlat(True)
                    header_label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                    header_label.setCursor(Qt.CursorShape.PointingHandCursor)
                    header_label.clicked.connect(lambda _=False, group_name=str(text): self._activate_group_row(group_name))
                else:
                    header_label = QLabel(container)
                    header_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                header_label.setFixedHeight(self.ITEM_HEIGHT)
                header_label.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed))
                header_label.setObjectName("OcrModelMenuGroupHeader")
                group_text_width = header_text_width - (28 if has_action_btn else 0) - 46
                header_label.setText(metrics.elidedText(f"[{text}]", Qt.TextElideMode.ElideRight, max(0, group_text_width)))
                header_label.setProperty("context_menu_container", container)
                header_label.installEventFilter(self)
                container_layout.addWidget(header_label, 1)

                if group_has_models:
                    batch_test_btn = GroupBatchTestButton(str(text), self, container)
                    batch_test_btn.hide()
                    batch_test_btn.setProperty("group_test_button", True)
                    batch_test_btn.setProperty("model_container", container)
                    batch_test_btn.installEventFilter(self)
                    batch_test_btn.move(6, int((self.ITEM_HEIGHT - 22) / 2))
                    batch_test_btn.raise_()
                    container.setProperty("group_test_btn", batch_test_btn)
                    self._group_test_buttons[str(text)] = batch_test_btn

                if is_movable:
                    move_btn = QPushButton("移动当前模型到此分组", container)
                    move_btn.hide()
                    move_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    move_btn.setFixedHeight(22)
                    move_btn.setStyleSheet("""
                        QPushButton {
                            background: #e2e8f0;
                            color: #475569;
                            border: none;
                            border-radius: 4px;
                            font-size: 12px;
                            font-weight: 700;
                            padding: 2px 8px;
                        }
                        QPushButton:hover {
                            background: #cbd5e1;
                            color: #1e293b;
                        }
                    """)
                    move_btn.clicked.connect(lambda _=False, prov_name=str(text): self._move_group_action(prov_name, parent_dialog))
                    container_layout.addWidget(move_btn)
                    move_btn.setProperty("group_move_button", True)
                    move_btn.setProperty("model_container", container)
                    move_btn.installEventFilter(self)
                    container.setProperty("move_btn", move_btn)

                if is_deletable:
                    del_btn = ModelDeleteButton(container)
                    del_btn.hide()
                    del_btn.clicked.connect(lambda _=False, prov_name=str(text): self._delete_group_action(prov_name, parent_dialog))
                    container_layout.addWidget(del_btn)
                    del_btn.setProperty("model_delete_button", True)
                    del_btn.setProperty("model_container", container)
                    del_btn.installEventFilter(self)
                    container.setProperty("del_btn", del_btn)

                if is_default_group:
                    clear_btn = ModelClearButton(container)
                    clear_btn.hide()
                    clear_btn.setToolTip("清空默认分组中的所有模型")
                    clear_btn.clicked.connect(lambda _=False: self._clear_group_action("默认分组", parent_dialog))
                    container_layout.addWidget(clear_btn)
                    clear_btn.setProperty("model_delete_button", True)
                    clear_btn.setProperty("model_container", container)
                    clear_btn.installEventFilter(self)
                    container.setProperty("del_btn", clear_btn)

                container.installEventFilter(self)
                container.setProperty("is_group_container", True)

                list_layout.addWidget(container)
                self._list_items.append({
                    "kind": "header",
                    "text": text,
                    "provider": text,
                    "widget": container
                })
            else:
                is_active = str(text) == str(current_model)
                highlight_color = self._highlight_color_for_model(str(text))
                is_highlighted = bool(highlight_color)
                is_deletable = self._on_delete is not None and str(text) not in {"Google翻译", "DeepLX", "自定义模型"}

                container = QWidget(content)
                container.setFixedHeight(self.ITEM_HEIGHT)
                container.setObjectName("OcrModelMenuItemContainer")

                container_layout = QHBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.setSpacing(0)

                selection_check = QCheckBox(container)
                selection_check.setObjectName("OcrModelDeleteSelectionCheck")
                selection_check.setFixedSize(28, self.ITEM_HEIGHT)
                selection_check.hide()
                selection_check.setCursor(Qt.CursorShape.PointingHandCursor)
                selection_check.stateChanged.connect(
                    lambda state, model_name=str(text): self._toggle_delete_selection(
                        model_name,
                        state == Qt.CheckState.Checked.value,
                    )
                )
                container_layout.addWidget(selection_check)

                button = QPushButton(container)
                button.setFlat(True)
                button.setFixedHeight(self.ITEM_HEIGHT)
                button.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed))
                button.setObjectName("OcrModelMenuItemActive" if is_active else "OcrModelMenuItem")
                checked_icon = self._highlighted_check_icon_for_color(highlight_color) if is_active and is_highlighted else self._checked_icon
                button.setIcon(checked_icon if is_active else self._empty_icon)
                button.setIconSize(QSize(self.CHECK_ICON_SIZE, self.CHECK_ICON_SIZE))

                codex_btn_width = 72 if self._on_codex_action is not None else 0
                codex_overhead = codex_btn_width + 4 if codex_btn_width else 0
                if is_deletable:
                    base_overhead = 78 + codex_overhead + (10 if needs_scroll else 0)
                else:
                    base_overhead = 52 + codex_overhead + (10 if needs_scroll else 0)
                item_text_width = max(30, popup_width - base_overhead)
                button.setText(metrics.elidedText(str(text), Qt.TextElideMode.ElideRight, item_text_width))

                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setProperty("model_active", is_active)
                button.setProperty("codex_highlighted", bool(is_highlighted))
                button.setProperty("highlight_color", highlight_color)
                if is_highlighted:
                    button.setStyleSheet(
                        "QPushButton {"
                        f" color: {highlight_color} !important;"
                        "}"
                        "QPushButton:hover {"
                        f" color: {highlight_color} !important;"
                        "}"
                    )
                button.setProperty("context_menu_container", container)
                button.installEventFilter(self)
                button.clicked.connect(lambda _=False, model_name=str(text): self._activate_model_row(model_name))
                if is_active:
                    self._active_button = button

                container_layout.addWidget(button, 1)

                container_w = popup_width - 12 - (12 if needs_scroll else 0)
                del_x = container_w - 22 - 4
                del_y = int((self.ITEM_HEIGHT - 22) / 2)

                test_btn = ModelTestButton(str(text), self, container)
                test_btn.hide()
                test_btn.setProperty("model_test_button", True)
                test_btn.setProperty("model_container", container)
                test_btn.installEventFilter(self)
                test_btn.move(6, int((self.ITEM_HEIGHT - 22) / 2))
                test_btn.raise_()
                container.setProperty("test_btn", test_btn)
                self._model_test_buttons[str(text)] = test_btn

                if is_deletable:
                    del_btn = ModelDeleteButton(container)
                    del_btn.hide()
                    del_btn.clicked.connect(lambda _=False, model_name=str(text): self._delete_action(model_name, self._on_delete))
                    del_btn.setProperty("model_delete_button", True)
                    del_btn.setProperty("model_container", container)
                    del_btn.installEventFilter(self)
                    del_btn.move(del_x, del_y)
                    del_btn.raise_()
                    container.setProperty("del_btn", del_btn)

                if self._on_codex_action is not None:
                    item_provider = str(current_provider or "")
                    codex_btn = QPushButton(self._codex_action_label(str(text), item_provider), container)
                    action_color = self._codex_action_color(str(text), item_provider)
                    is_warm_action = action_color.lower() == "#d97757"
                    action_bg = "#fff7ed" if is_warm_action else "#eff6ff"
                    action_hover_bg = "#ffedd5" if is_warm_action else "#dbeafe"
                    action_border = "#fed7aa" if is_warm_action else "#bfdbfe"
                    codex_btn.hide()
                    codex_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    codex_btn.setFixedSize(72, 22)
                    codex_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                    codex_btn.setStyleSheet(f"""
                        QPushButton {{
                            background: {action_bg};
                            color: {action_color};
                            border: 1px solid {action_border};
                            border-radius: 4px;
                            font-size: 11px;
                            font-weight: 700;
                            padding: 1px 4px;
                        }}
                        QPushButton:hover {{
                            background: {action_hover_bg};
                            color: {action_color};
                        }}
                    """)
                    codex_btn.pressed.connect(
                        lambda model_name=str(text), provider_name=item_provider: self._codex_action(model_name, provider_name)
                    )
                    codex_btn.setProperty("model_codex_button", True)
                    codex_btn.setProperty("model_container", container)
                    codex_btn.setProperty("model_provider", item_provider)
                    codex_btn.installEventFilter(self)
                    codex_x = (del_x - codex_btn.width() - 4) if is_deletable else (container_w - codex_btn.width() - 4)
                    codex_btn.move(max(4, codex_x), del_y)
                    codex_btn.raise_()
                    container.setProperty("codex_btn", codex_btn)

                container.installEventFilter(self)
                container.setProperty("is_model_container", True)

                list_layout.addWidget(container)
                self._list_items.append({
                    "kind": "model",
                    "text": text,
                    "search_text": self._search_texts.get(str(text), str(text)),
                    "provider": current_provider,
                    "widget": container,
                    "button": button,
                    "checkbox": selection_check,
                    "deletable": is_deletable,
                    "highlighted": is_highlighted,
                })

        empty_label = QLabel("暂无可用模型配置", content)
        empty_label.setObjectName("OcrModelMenuEmpty")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_label.setWordWrap(True)
        empty_label.setFixedHeight(64)
        empty_label.setVisible(not bool(rows))
        list_layout.addWidget(empty_label)
        self._empty_state_label = empty_label

        list_layout.addStretch(1)
        if rows:
            self._on_search_text_changed("")
        self._focus_search_edit()

    def _focus_search_edit(self) -> None:
        try:
            if not self.search_edit.isVisible():
                return
            self.search_edit.setFocus(Qt.FocusReason.PopupFocusReason)
            input_method = QGuiApplication.inputMethod()
            input_method.update(
                Qt.InputMethodQuery.ImEnabled
                | Qt.InputMethodQuery.ImHints
                | Qt.InputMethodQuery.ImCursorRectangle
                | Qt.InputMethodQuery.ImSurroundingText
                | Qt.InputMethodQuery.ImCurrentSelection
            )
        except Exception:
            pass

    def _hide_model_delete_button(self, container: QWidget | None) -> None:
        if container is None:
            return
        try:
            del_btn = container.property("del_btn")
            if del_btn is not None:
                del_btn.hide()
        except Exception:
            pass
        try:
            move_btn = container.property("move_btn")
            if move_btn is not None:
                move_btn.hide()
        except Exception:
            pass
        try:
            codex_btn = container.property("codex_btn")
            if codex_btn is not None:
                codex_btn.hide()
        except Exception:
            pass
        try:
            test_btn = container.property("test_btn")
            if test_btn is not None and hasattr(test_btn, "state") and test_btn.state() == "idle":
                test_btn.hide()
        except Exception:
            pass
        try:
            group_test_btn = container.property("group_test_btn")
            if group_test_btn is not None and hasattr(group_test_btn, "state") and group_test_btn.state() == "idle":
                group_test_btn.hide()
        except Exception:
            pass

    def _hide_other_model_buttons(
        self,
        keep_del: QWidget | None = None,
        keep_test: QWidget | None = None,
        keep_codex: QWidget | None = None,
    ) -> None:
        for item in getattr(self, "_list_items", []):
            widget = item.get("widget")
            if widget is None:
                continue
            try:
                del_btn = widget.property("del_btn")
            except Exception:
                del_btn = None
            try:
                codex_btn = widget.property("codex_btn")
            except Exception:
                codex_btn = None
            try:
                test_btn = widget.property("test_btn")
            except Exception:
                test_btn = None

            if del_btn is not None and del_btn is not keep_del:
                del_btn.hide()
            if codex_btn is not None and codex_btn is not keep_codex:
                codex_btn.hide()
            if test_btn is not None and test_btn is not keep_test:
                if hasattr(test_btn, "state") and test_btn.state() == "idle":
                    test_btn.hide()

    def _cursor_over_model_row(
        self,
        container: QWidget | None,
        del_btn: QWidget | None,
        test_btn: QWidget | None,
        codex_btn: QWidget | None = None,
    ) -> bool:
        for widget in (container, del_btn, test_btn, codex_btn):
            if widget is None:
                continue
            try:
                if widget.isVisible() and widget.rect().contains(widget.mapFromGlobal(QCursor.pos())):
                    return True
            except Exception:
                pass
        return False

    def _schedule_model_buttons_hide(
        self,
        container: QWidget | None,
        del_btn: QWidget | None,
        test_btn: QWidget | None,
        codex_btn: QWidget | None = None,
    ) -> None:
        def hide_if_outside() -> None:
            if not self._cursor_over_model_row(container, del_btn, test_btn, codex_btn):
                try:
                    if del_btn is not None:
                        del_btn.hide()
                except Exception:
                    pass
                try:
                    if codex_btn is not None:
                        codex_btn.hide()
                except Exception:
                    pass
                try:
                    if test_btn is not None and hasattr(test_btn, "state") and test_btn.state() == "idle":
                        test_btn.hide()
                except Exception:
                    pass

        QTimer.singleShot(0, hide_if_outside)

    def _hide_other_group_buttons(
        self,
        keep_del: QWidget | None = None,
        keep_test: QWidget | None = None,
        keep_move: QWidget | None = None,
    ) -> None:
        for item in getattr(self, "_list_items", []):
            widget = item.get("widget")
            if widget is None:
                continue
            try:
                del_btn = widget.property("del_btn")
            except Exception:
                del_btn = None
            try:
                move_btn = widget.property("move_btn")
            except Exception:
                move_btn = None
            try:
                group_test_btn = widget.property("group_test_btn")
            except Exception:
                group_test_btn = None

            if del_btn is not None and del_btn is not keep_del:
                del_btn.hide()
            if move_btn is not None and move_btn is not keep_move:
                move_btn.hide()
            if group_test_btn is not None and group_test_btn is not keep_test:
                group_test_btn.hide()

    def _cursor_over_group_row(
        self,
        container: QWidget | None,
        del_btn: QWidget | None,
        group_test_btn: QWidget | None,
        move_btn: QWidget | None = None,
    ) -> bool:
        for widget in (container, move_btn, del_btn, group_test_btn):
            if widget is None:
                continue
            try:
                if widget.isVisible() and widget.rect().contains(widget.mapFromGlobal(QCursor.pos())):
                    return True
            except Exception:
                pass
        return False

    def _schedule_group_buttons_hide(
        self,
        container: QWidget | None,
        del_btn: QWidget | None,
        group_test_btn: QWidget | None,
        move_btn: QWidget | None = None,
    ) -> None:
        def hide_if_outside() -> None:
            if not self._cursor_over_group_row(container, del_btn, group_test_btn, move_btn):
                try:
                    if move_btn is not None:
                        move_btn.hide()
                except Exception:
                    pass
                try:
                    if del_btn is not None:
                        del_btn.hide()
                except Exception:
                    pass
                try:
                    if group_test_btn is not None:
                        group_test_btn.hide()
                except Exception:
                    pass

        QTimer.singleShot(0, hide_if_outside)

    def eventFilter(self, obj, event) -> bool:
        if obj is self and event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
            self._hide_other_model_buttons()
            self._hide_other_group_buttons()
            mark_anchor_popup_closed(self.parentWidget(), self)
            return super().eventFilter(obj, event)
        if self._scrollbar is not None and obj is self._scrollbar:
            if event.type() == QEvent.Type.Enter:
                self._scrollbar.setFixedWidth(self.SCROLLBAR_HOVER_WIDTH)
            elif event.type() == QEvent.Type.Leave:
                self._scrollbar.setFixedWidth(self.SCROLLBAR_IDLE_WIDTH)
            return super().eventFilter(obj, event)
        if isinstance(obj, (QToolButton, QPushButton)) and obj.property("model_active") is not None:
            if event.type() == QEvent.Type.Enter:
                if bool(getattr(self, "_delete_selection_mode", False)):
                    return super().eventFilter(obj, event)
                container = obj.property("context_menu_container")
                if container is not None:
                    del_btn = container.property("del_btn")
                    test_btn = container.property("test_btn")
                    codex_btn = container.property("codex_btn")
                    self._hide_other_model_buttons(keep_del=del_btn, keep_test=test_btn, keep_codex=codex_btn)
                    if del_btn is not None:
                        del_btn.show()
                    if codex_btn is not None:
                        codex_btn.show()
                    if test_btn is not None:
                        test_btn.show()
            if event.type() == QEvent.Type.ContextMenu:
                container = obj.property("context_menu_container")
                item = self._item_for_container(container) if container is not None else None
                if item is not None:
                    global_pos = event.globalPos() if hasattr(event, "globalPos") else QCursor.pos()
                    return self._show_model_context_menu(item, global_pos)
            if event.type() == QEvent.Type.Leave and not bool(getattr(self, "_delete_selection_mode", False)):
                obj.setIcon(self._checked_icon_for_button(obj) if bool(obj.property("model_active")) else self._empty_icon)
                container = obj.property("context_menu_container")
                if container is not None:
                    self._schedule_model_buttons_hide(
                        container,
                        container.property("del_btn"),
                        container.property("test_btn"),
                        container.property("codex_btn"),
                    )

        if obj is not None and obj.property("context_menu_container") is not None:
            if event.type() == QEvent.Type.ContextMenu:
                container = obj.property("context_menu_container")
                item = self._item_for_container(container) if container is not None else None
                if item is not None:
                    global_pos = event.globalPos() if hasattr(event, "globalPos") else QCursor.pos()
                    return self._show_model_context_menu(item, global_pos)

        if obj is not None and obj.property("is_model_container") is not None:
            del_btn = obj.property("del_btn")
            test_btn = obj.property("test_btn")
            codex_btn = obj.property("codex_btn")
            if event.type() == QEvent.Type.ContextMenu:
                item = self._item_for_container(obj)
                if item is not None:
                    global_pos = event.globalPos() if hasattr(event, "globalPos") else QCursor.pos()
                    return self._show_model_context_menu(item, global_pos)
            elif event.type() == QEvent.Type.Enter:
                if bool(getattr(self, "_delete_selection_mode", False)):
                    return super().eventFilter(obj, event)
                self._hide_other_model_buttons(keep_del=del_btn, keep_test=test_btn, keep_codex=codex_btn)
                if del_btn is not None:
                    del_btn.show()
                if codex_btn is not None:
                    codex_btn.show()
                if test_btn is not None:
                    test_btn.show()
            elif event.type() == QEvent.Type.Leave:
                self._schedule_model_buttons_hide(obj, del_btn, test_btn, codex_btn)
            elif event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
                if del_btn is not None:
                    del_btn.hide()
                if codex_btn is not None:
                    codex_btn.hide()
                if test_btn is not None and hasattr(test_btn, "state") and test_btn.state() == "idle":
                    test_btn.hide()

        if obj is not None and obj.property("is_group_container") is not None:
            del_btn = obj.property("del_btn")
            move_btn = obj.property("move_btn")
            group_test_btn = obj.property("group_test_btn")
            if event.type() == QEvent.Type.ContextMenu:
                item = self._item_for_container(obj)
                if item is not None:
                    global_pos = event.globalPos() if hasattr(event, "globalPos") else QCursor.pos()
                    return self._show_model_context_menu(item, global_pos)
            elif event.type() == QEvent.Type.Enter:
                if bool(getattr(self, "_delete_selection_mode", False)):
                    return super().eventFilter(obj, event)
                self._hide_other_group_buttons(keep_del=del_btn, keep_test=group_test_btn, keep_move=move_btn)
                if move_btn is not None:
                    move_btn.show()
                if del_btn is not None:
                    del_btn.show()
                if group_test_btn is not None and hasattr(group_test_btn, "state") and group_test_btn.state() == "idle":
                    group_test_btn.show()
            elif event.type() == QEvent.Type.Leave:
                self._schedule_group_buttons_hide(obj, del_btn, group_test_btn, move_btn)
            elif event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
                if move_btn is not None:
                    move_btn.hide()
                if del_btn is not None:
                    del_btn.hide()
                if group_test_btn is not None:
                    group_test_btn.hide()

        if obj is not None and obj.property("model_delete_button") is not None:
            container = obj.property("model_container")
            test_btn = container.property("test_btn") if container is not None else None
            codex_btn = container.property("codex_btn") if container is not None else None
            group_test_btn = container.property("group_test_btn") if container is not None else None
            move_btn = container.property("move_btn") if container is not None else None
            if event.type() == QEvent.Type.Enter:
                if bool(getattr(self, "_delete_selection_mode", False)):
                    return super().eventFilter(obj, event)
                if container is not None and container.property("is_model_container") is not None:
                    self._hide_other_model_buttons(keep_del=obj, keep_test=test_btn, keep_codex=codex_btn)
                else:
                    self._hide_other_group_buttons(keep_del=obj, keep_test=group_test_btn, keep_move=move_btn)
                obj.show()
                if codex_btn is not None:
                    codex_btn.show()
                if move_btn is not None:
                    move_btn.show()
                if test_btn is not None:
                    test_btn.show()
                if group_test_btn is not None and hasattr(group_test_btn, "state") and group_test_btn.state() == "idle":
                    group_test_btn.show()
            elif event.type() == QEvent.Type.Leave:
                if container is not None and container.property("is_model_container") is not None:
                    self._schedule_model_buttons_hide(container, obj, test_btn, codex_btn)
                else:
                    self._schedule_group_buttons_hide(container, obj, group_test_btn, move_btn)
            elif event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
                obj.hide()

        if obj is not None and obj.property("model_codex_button") is not None:
            container = obj.property("model_container")
            del_btn = container.property("del_btn") if container is not None else None
            test_btn = container.property("test_btn") if container is not None else None
            if event.type() == QEvent.Type.Enter:
                if bool(getattr(self, "_delete_selection_mode", False)):
                    return super().eventFilter(obj, event)
                self._hide_other_model_buttons(keep_del=del_btn, keep_test=test_btn, keep_codex=obj)
                obj.show()
                if del_btn is not None:
                    del_btn.show()
                if test_btn is not None:
                    test_btn.show()
            elif event.type() == QEvent.Type.Leave:
                self._schedule_model_buttons_hide(container, del_btn, test_btn, obj)
            elif event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
                obj.hide()

        if obj is not None and obj.property("model_test_button") is not None:
            container = obj.property("model_container")
            del_btn = container.property("del_btn") if container is not None else None
            codex_btn = container.property("codex_btn") if container is not None else None
            if event.type() == QEvent.Type.Enter:
                if bool(getattr(self, "_delete_selection_mode", False)):
                    return super().eventFilter(obj, event)
                self._hide_other_model_buttons(keep_del=del_btn, keep_test=obj, keep_codex=codex_btn)
                obj.show()
                if del_btn is not None:
                    del_btn.show()
                if codex_btn is not None:
                    codex_btn.show()
            elif event.type() == QEvent.Type.Leave:
                self._schedule_model_buttons_hide(container, del_btn, obj, codex_btn)
            elif event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
                if obj.state() == "idle":
                    obj.hide()

        if obj is not None and obj.property("group_test_button") is not None:
            container = obj.property("model_container")
            del_btn = container.property("del_btn") if container is not None else None
            move_btn = container.property("move_btn") if container is not None else None
            if event.type() == QEvent.Type.Enter:
                if bool(getattr(self, "_delete_selection_mode", False)):
                    return super().eventFilter(obj, event)
                self._hide_other_group_buttons(keep_del=del_btn, keep_test=obj, keep_move=move_btn)
                if obj.state() == "idle":
                    obj.show()
                if move_btn is not None:
                    move_btn.show()
                if del_btn is not None:
                    del_btn.show()
            elif event.type() == QEvent.Type.Leave:
                self._schedule_group_buttons_hide(container, del_btn, obj, move_btn)
            elif event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
                obj.hide()

        if obj is not None and obj.property("group_move_button") is not None:
            container = obj.property("model_container")
            del_btn = container.property("del_btn") if container is not None else None
            group_test_btn = container.property("group_test_btn") if container is not None else None
            if event.type() == QEvent.Type.Enter:
                if bool(getattr(self, "_delete_selection_mode", False)):
                    return super().eventFilter(obj, event)
                self._hide_other_group_buttons(keep_del=del_btn, keep_test=group_test_btn, keep_move=obj)
                obj.show()
                if del_btn is not None:
                    del_btn.show()
                if group_test_btn is not None and hasattr(group_test_btn, "state") and group_test_btn.state() == "idle":
                    group_test_btn.show()
            elif event.type() == QEvent.Type.Leave:
                self._schedule_group_buttons_hide(container, del_btn, group_test_btn, obj)
            elif event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
                obj.hide()

        return super().eventFilter(obj, event)

    def enqueue_model_test(self, model_name: str, group_name: Optional[str] = None) -> None:
        test_btn = self._model_test_buttons.get(model_name)
        if test_btn is None:
            return
        if test_btn.state() == "testing":
            return

        test_btn.set_state("testing")
        test_btn.show()

        self._test_queue.append((model_name, group_name))
        self._process_test_queue()

    def _process_test_queue(self) -> None:
        global _local_gemini_test_next_at

        from deepcat.local_gemini_web2api_server import identify_spec as identify_gemini
        from deepcat.translator_engine import load_translator_runtime

        active_workers = [
            btn.worker() for btn in self._model_test_buttons.values()
            if btn.state() == "testing" and btn.has_active_worker()
        ]
        active_count = len(active_workers)

        active_gemini_count = 0
        for worker in active_workers:
            if hasattr(worker, "_cfg") and identify_gemini(worker._cfg) is not None:
                active_gemini_count += 1

        now = time.monotonic()
        retry_after = 0.0
        i = 0
        while active_count < self._max_active_workers and i < len(self._test_queue):
            model_name, group_name = self._test_queue[i]
            test_btn = self._model_test_buttons.get(model_name)
            if test_btn is None or test_btn.state() != "testing":
                self._test_queue.pop(i)
                continue

            runtime = None
            cfg = {}
            try:
                runtime = load_translator_runtime(model_name, purpose=getattr(self, "_purpose", "translate"))
                cfg = {
                    "base_url": runtime.base_url,
                    "model_name": runtime.model_name,
                    "api_key": runtime.api_key,
                    "use_proxy": runtime.use_proxy,
                    "model_type": runtime.model_type,
                }
                is_gemini_web = identify_gemini(cfg) is not None
            except Exception:
                is_gemini_web = False
                logger.exception("Failed to load translator runtime for model test: %s", model_name)

            if not cfg:
                self._test_queue.pop(i)
                test_btn.set_state("failed")
                if group_name:
                    self._update_group_batch_status(group_name, model_name, False)
                continue

            if is_gemini_web and active_gemini_count >= 1:
                i += 1
                continue
            gemini_next_test_at = max(self._gemini_next_test_at, _local_gemini_test_next_at)
            if is_gemini_web and now < gemini_next_test_at:
                retry_after = max(retry_after, gemini_next_test_at - now)
                i += 1
                continue

            self._test_queue.pop(i)

            try:
                from deepcat.ui.settings_dialog import TranslatorConnectionTestWorker

                use_proxy = cfg.get("use_proxy", False) if cfg else False
                proxy_url = getattr(runtime, "proxy_url", "") if runtime is not None else ""

                worker = TranslatorConnectionTestWorker(cfg, use_proxy=use_proxy, proxy_url=proxy_url)
                worker.tested.connect(
                    lambda success, msg, elapsed, name=model_name, g=group_name:
                    self._on_model_test_finished(name, success, msg, g)
                )
                worker.finished.connect(worker.deleteLater)

                test_btn.set_worker(worker)
                worker.start()
                active_count += 1
                if is_gemini_web:
                    active_gemini_count += 1
            except Exception as e:
                test_btn.set_state("failed")
                if group_name:
                    self._update_group_batch_status(group_name, model_name, False)
                logger.error(f"Failed to start connection test for {model_name}: {e}")

        if retry_after > 0:
            self._schedule_test_queue_retry(retry_after)

    def _on_model_test_finished(self, model_name: str, success: bool, message: str, group_name: Optional[str] = None) -> None:
        global _local_gemini_test_next_at

        from deepcat.local_gemini_web2api_server import identify_spec as identify_gemini

        test_btn = self._model_test_buttons.get(model_name)
        is_gemini_web = False
        if test_btn is not None:
            worker = test_btn.worker()
            try:
                if worker is not None and hasattr(worker, "_cfg"):
                    is_gemini_web = identify_gemini(getattr(worker, "_cfg")) is not None
            except Exception:
                is_gemini_web = False
            test_btn.set_state("success" if success else "failed")
            if hasattr(test_btn, "setToolTip"):
                if not success:
                    test_btn.setToolTip(wrap_error_message(message))
                else:
                    test_btn.setToolTip("")
            test_btn.set_worker(None)

        if is_gemini_web:
            cooldown = (
                _LOCAL_GEMINI_TEST_SUCCESS_COOLDOWN_SECONDS
                if bool(success)
                else _LOCAL_GEMINI_TEST_FAILURE_COOLDOWN_SECONDS
            )
            next_test_at = time.monotonic() + cooldown
            self._gemini_next_test_at = max(self._gemini_next_test_at, next_test_at)
            _local_gemini_test_next_at = max(_local_gemini_test_next_at, next_test_at)

        if group_name:
            self._update_group_batch_status(group_name, model_name, success)

        self._process_test_queue()

    def _schedule_test_queue_retry(self, delay_seconds: float) -> None:
        if bool(getattr(self, "_test_queue_retry_scheduled", False)):
            return
        self._test_queue_retry_scheduled = True

        def retry() -> None:
            self._test_queue_retry_scheduled = False
            self._process_test_queue()

        QTimer.singleShot(max(1, int(float(delay_seconds) * 1000)), retry)

    def start_group_batch_test(self, group_name: str) -> None:
        models_in_group = []
        for g_name, m_list in self._grouped_models:
            if g_name == group_name:
                models_in_group = m_list
                break

        if not models_in_group:
            return

        group_btn = self._group_test_buttons.get(group_name)
        if group_btn is not None:
            group_btn.set_state("testing")
            group_btn.hide()

        self._group_batch_tests[group_name] = {
            "total": len(models_in_group),
            "model_states": {},
            "btn": group_btn
        }

        for model_name in models_in_group:
            self.enqueue_model_test(model_name, group_name)

    def _update_group_batch_status(self, group_name: str, model_name: str, success: bool) -> None:
        batch_info = self._group_batch_tests.get(group_name)
        if not batch_info:
            return

        model_states = batch_info["model_states"]
        model_states[model_name] = "success" if success else "failed"

        completed = len(model_states)
        total = batch_info["total"]

        if completed >= total:
            all_success = all(state == "success" for state in model_states.values())
            group_btn = batch_info["btn"]
            if group_btn is not None:
                group_btn.set_state("success" if all_success else "failed")
                group_btn.hide()
            self._group_batch_tests.pop(group_name, None)

    def closeEvent(self, event) -> None:
        for btn in self._model_test_buttons.values():
            btn.stop_timer()
            worker = btn.worker()
            if worker is not None:
                try:
                    worker.tested.disconnect()
                except Exception:
                    pass
        for btn in self._group_test_buttons.values():
            btn.stop_timer()
        super().closeEvent(event)

    def _make_check_icon(self, color: str) -> QIcon:
        pixmap = QPixmap(self.CHECK_ICON_SIZE, self.CHECK_ICON_SIZE)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(color), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QPainterPath()
        path.moveTo(3.0, 7.2)
        path.lineTo(6.0, 10.0)
        path.lineTo(11.2, 4.2)
        painter.drawPath(path)
        painter.end()
        return QIcon(pixmap)

    def _activate_model_row(self, model_name: str) -> None:
        if bool(getattr(self, "_delete_selection_mode", False)):
            self._toggle_delete_selection(model_name)
            return
        self._select(model_name)

    def _activate_group_row(self, group_name: str) -> None:
        if bool(getattr(self, "_delete_selection_mode", False)):
            return
        self._select_group(group_name)

    def _select(self, model_name: str) -> None:
        self.close()
        self._on_selected(model_name)

    def _select_group(self, group_name: str) -> None:
        if self._on_group_selected is None:
            return
        self.close()
        self._on_group_selected(group_name)

    def _adjust_position(self) -> None:
        if not hasattr(self, "_anchor") or self._anchor is None:
            return
        anchor = self._anchor
        global_pos = self._global_pos
        margin = 6
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        screen_geo = screen.availableGeometry() if screen is not None else QRect(0, 0, 1200, 800)
        bounds = screen_geo

        if getattr(self, "_match_parent_width", False):
            x = int(global_pos.x()) - 8
        else:
            x = int(global_pos.x())

        y = int(global_pos.y())

        if y + self.height() > bounds.bottom() + 1 - margin:
            y = anchor.mapToGlobal(QPoint(0, 0)).y() - self.height()

        if x + self.width() > bounds.right() + 1 - margin:
            x = bounds.right() + 1 - margin - self.width()
        x = max(bounds.left() + margin, x)
        y = max(bounds.top() + margin, min(y, bounds.bottom() + 1 - margin - self.height()))
        self.move(QPoint(x, y))

    def show_at(self, anchor: QWidget, global_pos: QPoint) -> None:
        if should_skip_anchor_popup(anchor, self):
            self.close()
            return
        self.installEventFilter(self)
        self._anchor = anchor
        self._global_pos = global_pos
        self._adjust_position()
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self._focus_search_edit)
        QTimer.singleShot(80, self._focus_search_edit)
        self._scroll_to_active()

    def _scroll_to_active(self) -> None:
        if self._scroll is None or self._active_button is None:
            return

        def ensure_active_visible() -> None:
            try:
                if self._scroll is not None and self._active_button is not None:
                    self._scroll.ensureWidgetVisible(self._active_button, 0, 0)
            except RuntimeError:
                pass

        QTimer.singleShot(0, ensure_active_visible)


class OcrGenericMenuPopup(QWidget):
    def __init__(
        self,
        items: list[tuple[str, Optional[Callable[[], None]], bool, ...]],
        parent: QWidget | None = None,
        active_index: Optional[int] = None,
        match_parent_width: bool = False,
        match_parent_width_exact: bool = False,
        active_indicator: str = "check",
    ) -> None:
        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        try:
            flags |= Qt.WindowType.NoDropShadowWindowHint
        except AttributeError:
            pass
        super().__init__(parent, flags)
        self.setObjectName("OcrGenericMenuPopupRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._items = items
        self._active_index = active_index
        self._match_parent_width = match_parent_width
        self._match_parent_width_exact = match_parent_width_exact
        self._active_indicator = "background" if str(active_indicator).strip().lower() == "background" else "check"
        self._build()

    def paintEvent(self, event) -> None:
        from PyQt6.QtGui import QPainter, QColor, QPen
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(QPen(QColor("#dfe4ec"), 1))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
        painter.end()
        super().paintEvent(event)

    def _build(self) -> None:
        from PyQt6.QtWidgets import QVBoxLayout, QPushButton, QFrame, QSizePolicy, QHBoxLayout, QScrollArea, QWidget
        from PyQt6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QFontMetrics, QGuiApplication
        from PyQt6.QtCore import Qt, QSize, QPointF, QRect, QPoint

        ITEM_HEIGHT = 30
        metrics = QFontMetrics(self.font())

        max_text_w = 0
        non_separator_count = 0
        separator_count = 0
        for item_data in self._items:
            name = item_data[0]
            if name == "-":
                separator_count += 1
            else:
                non_separator_count += 1
                w = int(metrics.horizontalAdvance(name))
                if len(item_data) >= 4 and isinstance(item_data[3], QIcon):
                    w += 24
                has_actions = False
                if len(item_data) >= 4:
                    if isinstance(item_data[3], list) and item_data[3]:
                        has_actions = True
                    elif isinstance(item_data[3], str) and len(item_data) >= 5:
                        has_actions = True
                if has_actions:
                    w += 120
                if len(item_data) >= 5 and item_data[4]:
                    w += int(metrics.horizontalAdvance(str(item_data[4]))) + 12
                if w > max_text_w:
                    max_text_w = w

        has_active = self._active_index is not None and self._active_index >= 0
        has_checks = has_active and self._active_indicator == "check"

        if has_checks:
            popup_width = max(110, max_text_w + 48)
        else:
            popup_width = max(120, max_text_w + 32)

        if self._match_parent_width and self.parentWidget() and hasattr(self.parentWidget(), "width"):
            parent_w = self.parentWidget().width()
            if self._match_parent_width_exact:
                popup_width = max(48, int(parent_w))
            elif parent_w > popup_width:
                popup_width = parent_w

        full_height = non_separator_count * ITEM_HEIGHT + separator_count * 6 + 12

        try:
            screen = QGuiApplication.screenAt(self.mapToGlobal(QPoint(0, 0))) or QGuiApplication.primaryScreen()
            screen_h = screen.availableGeometry().height() if screen else 800
        except Exception:
            screen_h = 800

        max_h = min(400, screen_h - 100)
        need_scroll = full_height > max_h
        if need_scroll:
            popup_height = max_h
            popup_width += 14
        else:
            popup_height = full_height

        self.setFixedSize(popup_width, popup_height)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(0)

        if need_scroll:
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setStyleSheet("""
                QScrollArea { border: none; background: transparent; }
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

            container = QWidget()
            container.setObjectName("OcrGenericMenuPopupContainer")
            container.setStyleSheet("QWidget#OcrGenericMenuPopupContainer { background: transparent; }")

            root_layout = QVBoxLayout(container)
            root_layout.setContentsMargins(0, 0, 0, 0)
            root_layout.setSpacing(2)
        else:
            root_layout = QVBoxLayout()
            root_layout.setContentsMargins(0, 0, 0, 0)
            root_layout.setSpacing(2)
            main_layout.addLayout(root_layout)

        check_icon = None
        empty_icon = None
        if has_checks:
            ratio = self.devicePixelRatioF()
            size = 16
            check_pixmap = QPixmap(int(size * ratio), int(size * ratio))
            check_pixmap.setDevicePixelRatio(ratio)
            check_pixmap.fill(Qt.GlobalColor.transparent)

            painter = QPainter(check_pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(QColor("#1e293b"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(QPointF(3.5, 8.0), QPointF(6.5, 11.0))
            painter.drawLine(QPointF(6.5, 11.0), QPointF(12.5, 4.5))
            painter.end()
            check_icon = QIcon(check_pixmap)

            empty_pixmap = QPixmap(int(size * ratio), int(size * ratio))
            empty_pixmap.setDevicePixelRatio(ratio)
            empty_pixmap.fill(Qt.GlobalColor.transparent)
            empty_icon = QIcon(empty_pixmap)

        btn_idx = 0
        for item_data in self._items:
            name = item_data[0]
            slot = item_data[1]
            enabled = item_data[2]

            actions = []
            remark = ""
            item_icon = None
            if len(item_data) >= 4:
                if isinstance(item_data[3], QIcon):
                    item_icon = item_data[3]
                    if len(item_data) >= 5:
                        remark = str(item_data[4] or "").strip()
                elif isinstance(item_data[3], list):
                    actions = item_data[3]
                elif isinstance(item_data[3], str) and len(item_data) >= 5:
                    actions = [(item_data[3], item_data[4], "normal")]
            if len(item_data) >= 5 and not isinstance(item_data[3], (QIcon, str)):
                remark = str(item_data[4] or "").strip()

            if name == "-":
                line = QWidget()
                line.setFixedHeight(7)
                line.setStyleSheet(
                    "background: transparent; border: none; "
                    "border-top: 1px solid #eef2f7; margin: 3px 8px 2px 8px;"
                )
                root_layout.addWidget(line)
            else:
                btn = QPushButton()
                btn.setFixedHeight(ITEM_HEIGHT)
                btn.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed))

                is_active = bool(has_active and btn_idx == self._active_index)
                active_background = "#f3f4f6" if self._active_indicator == "background" and is_active else "transparent"
                hover_background = "#e5e7eb" if self._active_indicator == "background" and is_active else "#f3f4f6"

                if has_checks:
                    btn.setIconSize(QSize(16, 16))
                    if is_active:
                        btn.setIcon(check_icon)
                        font_style = "font-weight: 800; color: #111827;"
                    else:
                        btn.setIcon(empty_icon)
                        font_style = "font-weight: 500; color: #4b5563;"
                elif is_active:
                    font_style = "font-weight: 700; color: #111827;"
                else:
                    font_style = "font-weight: 500; color: #374151;"

                if item_icon is not None and not has_checks:
                    btn.setIcon(item_icon)
                    btn.setIconSize(QSize(16, 16))

                btn.setText(name)
                btn.setEnabled(enabled)
                btn.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)

                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {active_background};
                        border: none;
                        border-radius: 6px;
                        font-family: 'Microsoft YaHei', 'Segoe UI', system-ui;
                        font-size: 12px;
                        min-height: {ITEM_HEIGHT}px;
                        max-height: {ITEM_HEIGHT}px;
                        padding: 0px 12px;
                        text-align: left;
                        {font_style}
                    }}
                    QPushButton:hover {{
                        background: {hover_background};
                        color: #111827;
                    }}
                    QPushButton:pressed {{
                        background: #e5e7eb;
                    }}
                    QPushButton:disabled {{
                        color: #9ca3af;
                    }}
                """)

                if remark or actions:
                    sub_layout = QHBoxLayout(btn)
                    sub_layout.setContentsMargins(0, 0, 6, 0)
                    sub_layout.setSpacing(6)
                    sub_layout.addStretch(1)

                    if remark:
                        from PyQt6.QtWidgets import QLabel
                        remark_label = QLabel(remark, btn)
                        remark_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                        remark_label.setStyleSheet("color: #94a3b8; font-size: 10px; background: transparent; font-weight: normal;")
                        sub_layout.addWidget(remark_label)

                    sub_buttons = []
                    for act_text, act_slot, act_type in actions:
                        sub_btn = QPushButton(act_text, btn)
                        sub_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                        sub_btn.hide()

                        if act_type == "danger":
                            sub_btn.setObjectName("MenuSubActionBtnDanger")
                            sub_btn.setStyleSheet("""
                                QPushButton#MenuSubActionBtnDanger {
                                    background: transparent;
                                    color: #ef4444;
                                    border: none;
                                    border-radius: 4px;
                                    font-size: 14px;
                                    font-weight: bold;
                                    padding: 0px 4px;
                                }
                                QPushButton#MenuSubActionBtnDanger:hover {
                                    background: #fee2e2;
                                    color: #dc2626;
                                }
                            """)
                        else:
                            sub_btn.setObjectName("MenuSubActionBtn")
                            sub_btn.setFixedHeight(22)
                            sub_btn.setStyleSheet("""
                                QPushButton#MenuSubActionBtn {
                                    background: #e2e8f0;
                                    color: #475569;
                                    border: none;
                                    border-radius: 4px;
                                    font-size: 12px;
                                    padding: 3px 10px;
                                    font-weight: 600;
                                    min-height: 22px;
                                    max-height: 22px;
                                }
                                QPushButton#MenuSubActionBtn:hover {
                                    background: #cbd5e1;
                                    color: #1e293b;
                                }
                            """)

                        def make_click_handler(func=act_slot):
                            def handler(*args):
                                if func:
                                    func()
                                self.close()
                            return handler

                        sub_btn.clicked.connect(make_click_handler())
                        sub_layout.addWidget(sub_btn)
                        sub_buttons.append(sub_btn)

                    orig_enter = btn.enterEvent
                    orig_leave = btn.leaveEvent

                    def btn_enter(event, b=btn, sbs=sub_buttons, o_enter=orig_enter):
                        for sb in sbs:
                            sb.show()
                        if o_enter:
                            try:
                                o_enter(event)
                            except Exception:
                                pass

                    def btn_leave(event, b=btn, sbs=sub_buttons, o_leave=orig_leave):
                        for sb in sbs:
                            sb.hide()
                        if o_leave:
                            try:
                                o_leave(event)
                            except Exception:
                                pass

                    btn.enterEvent = btn_enter
                    btn.leaveEvent = btn_leave

                if slot is not None:
                    btn.clicked.connect(lambda _checked=False, callback=slot: callback())
                btn.clicked.connect(self.close)
                root_layout.addWidget(btn)
                btn_idx += 1

        if need_scroll:
            scroll.setWidget(container)
            main_layout.addWidget(scroll)

    def _trigger(self, slot: Callable[[], None]) -> None:
        self.close()
        try:
            slot()
        except Exception:
            pass

    def eventFilter(self, obj, event) -> bool:
        if obj is self and event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
            mark_anchor_popup_closed(self.parentWidget(), self)
        return super().eventFilter(obj, event)

    def show_at_pos(self, global_pos: QPoint) -> None:
        if should_skip_anchor_popup(self.parentWidget(), self):
            self.close()
            return
        self.installEventFilter(self)
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
