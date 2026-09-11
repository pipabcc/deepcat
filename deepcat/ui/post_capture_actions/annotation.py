from __future__ import annotations

import math
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import numpy as np
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QCursor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QWidget, QLineEdit
from deepcat.settings_store import load_settings
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPixmap, QPen, QColor, QFont
from PyQt6.QtCore import QPointF, QRectF, QPoint, QRect, Qt
from PyQt6.QtWidgets import QWidget


class AnnotationCanvasOverlay(QWidget):
    changed = pyqtSignal()
    history_changed = pyqtSignal(bool, bool)

    def __init__(self, region: QRect, image_bgr: np.ndarray, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._is_embedded = parent is not None
        if not self._is_embedded:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setStyleSheet("background: transparent;")
        self.setMouseTracking(True)
        self._region = QRect(region)
        self._image_size = (int(image_bgr.shape[1]), int(image_bgr.shape[0]))
        self._mode = ""
        self._commands: list[dict[str, object]] = []
        self._redo: list[dict[str, object]] = []
        self._temp: Optional[dict[str, object]] = None
        self._drawing = False
        self._eraser_changed = False
        self._tool_cursor_cache: dict[str, QCursor] = {}
        self._eraser_pos: Optional[QPoint] = None
        self._commands_rendered_to_parent = False
        self._next_number = 1
        self._text_editor: Optional[QLineEdit] = None
        self._text_editor_anchor: Optional[tuple[float, float]] = None
        if self._is_embedded:
            self.setGeometry(0, 0, parent.width(), parent.height())
        else:
            self.setGeometry(QRect(self._region))
        self.hide()

    def set_region(self, region: QRect, image_bgr: Optional[np.ndarray] = None, clear: bool = False) -> None:
        if self._is_embedded:
            p = self.parentWidget()
            if p is not None:
                self.setGeometry(0, 0, p.width(), p.height())
            self._region = QRect(0, 0, p.width() if p else 1, p.height() if p else 1)
        else:
            self._region = QRect(region)
            self.setGeometry(QRect(self._region))
        if image_bgr is not None:
            self._image_size = (int(image_bgr.shape[1]), int(image_bgr.shape[0]))
        if bool(clear):
            self.clear()
        else:
            self.update()

    def set_mode(self, mode: str) -> None:
        self._commit_text_editor()
        self._mode = str(mode or "")
        # blur 仍由宿主窗口直接处理；text 需要由标注层承接，才能让输入框坐标和最终文字坐标保持一致。
        is_transparent = not bool(self._mode) or self._mode == "blur"
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, is_transparent)
        if self._mode or self._commands:
            self.show()
            self.raise_()
        else:
            self.hide()
        if self._mode in {"arrow", "pen", "marker", "rect", "eraser", "number"}:
            self.setCursor(self._tool_cursor(self._mode))
        elif self._mode == "text":
            self.setCursor(Qt.CursorShape.IBeamCursor)
        else:
            self.unsetCursor()

    def clear(self) -> None:
        self._commit_text_editor()
        self._commands.clear()
        self._redo.clear()
        self._temp = None
        self._eraser_pos = None
        self._next_number = 1
        self.update()
        self.changed.emit()
        self._emit_history()
        if not self._mode:
            self.hide()

    def set_commands_rendered_to_parent(self, active: bool) -> None:
        active = bool(active)
        if self._commands_rendered_to_parent == active:
            return
        self._commands_rendered_to_parent = active
        self.update()

    def can_undo(self) -> bool:
        return bool(self._commands)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> None:
        self._commit_text_editor()
        if not self._commands:
            return
        self._redo.append(self._commands.pop())
        self._recount_numbers()
        self.update()
        if not self._commands and not self._mode:
            self.hide()
        self.changed.emit()
        self._emit_history()

    def redo(self) -> None:
        self._commit_text_editor()
        if not self._redo:
            return
        self._commands.append(self._redo.pop())
        self._recount_numbers()
        if self._commands:
            self.show()
        self.update()
        self.changed.emit()
        self._emit_history()

    def commit_pending(self) -> None:
        self._commit_text_editor()

    def render_to_bgr(self, base_bgr: np.ndarray, commit_text: bool = True) -> np.ndarray:
        import numpy as np

        if commit_text:
            self._commit_text_editor()
        if not self._commands and self._temp is None:
            return base_bgr.copy()
        rgb = base_bgr[:, :, ::-1].copy()
        h, w = int(rgb.shape[0]), int(rgb.shape[1])
        qimg = QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format.Format_RGB888).copy()
        painter = QPainter(qimg)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            for cmd in self._commands:
                self._draw_command(painter, cmd, w, h)
            if self._temp is not None:
                self._draw_command(painter, self._temp, w, h)
        finally:
            if painter.isActive():
                painter.end()
        qimg = qimg.convertToFormat(QImage.Format.Format_RGB888)
        ptr = qimg.bits()
        ptr.setsize(int(qimg.sizeInBytes()))
        stride = int(qimg.bytesPerLine())
        row_bytes = int(w * 3)
        out_rgb = np.frombuffer(ptr, dtype=np.uint8).reshape((h, stride))[:, :row_bytes].reshape((h, w, 3)).copy()
        return out_rgb[:, :, ::-1].copy()

    def _emit_history(self) -> None:
        self.history_changed.emit(bool(self._commands), bool(self._redo))

    def _recount_numbers(self) -> None:
        max_n = 0
        for cmd in self._commands:
            if cmd.get("type") == "number":
                try:
                    max_n = max(max_n, int(cmd.get("n", 0)))
                except Exception:
                    pass
        self._next_number = int(max_n + 1)

    def _get_style_color(self, cmd_type: str) -> QColor:
        try:
            from deepcat.settings_store import load_settings
            style = load_settings().ui.get("annotation_style", {})
        except Exception:
            style = {}
        color_hex = "#E53935"
        if cmd_type == "arrow":
            color_hex = style.get("arrow_color", "#E53935")
        elif cmd_type == "pen":
            color_hex = style.get("pen_color", "#E53935")
        elif cmd_type == "marker":
            color_hex = style.get("marker_color", "#FFD600")
        elif cmd_type == "number":
            color_hex = style.get("number_color", "#E53935")
        elif cmd_type == "text":
            color_hex = style.get("text_color", "#E53935")
        elif cmd_type == "rect":
            color_hex = style.get("rect_color", "#E53935")
        if cmd_type == "marker":
            c = QColor(color_hex)
            c.setAlpha(120)
            return c
        return QColor(color_hex)

    def _draw_rect(self, painter: QPainter, start: object, end: object, w: int, h: int) -> None:
        p1 = self._to_point(start, w, h)
        p2 = self._to_point(end, w, h)
        base_color = self._get_style_color("rect")
        lw = max(3.0, float(min(w, h)) * 0.005)
        rx = ry = max(4.0, float(lw) * 1.5)

        x0 = min(p1.x(), p2.x())
        y0 = min(p1.y(), p2.y())
        rw = abs(p1.x() - p2.x())
        rh = abs(p1.y() - p2.y())
        from PyQt6.QtCore import QRectF
        rect_f = QRectF(x0, y0, rw, rh)

        painter.setBrush(Qt.BrushStyle.NoBrush)

        # 1. 绘制外部白色高对比度发光层 (双色设计，极具国际范)
        white_pen = QPen(QColor(255, 255, 255, 210))
        white_pen.setWidthF(lw + 1.6)
        white_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        white_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(white_pen)
        painter.drawRoundedRect(rect_f, rx, ry)

        # 2. 绘制内部主色层
        main_pen = QPen(base_color)
        main_pen.setWidthF(lw)
        main_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        main_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(main_pen)
        painter.drawRoundedRect(rect_f, rx, ry)


    def _norm_point(self, p: QPoint) -> tuple[float, float]:
        w = max(1, int(self.width()))
        h = max(1, int(self.height()))
        return (float(max(0, min(w, int(p.x())))) / float(w), float(max(0, min(h, int(p.y())))) / float(h))

    def _to_point(self, pt: object, w: int, h: int) -> QPointF:
        try:
            x, y = pt  # type: ignore[misc]
            return QPointF(float(x) * float(w), float(y) * float(h))
        except Exception:
            return QPointF(0.0, 0.0)

    def _draw_polyline(self, painter: QPainter, points: object, w: int, h: int, color: QColor, width: int) -> None:
        pts = [self._to_point(p, w, h) for p in list(points or [])]  # type: ignore[arg-type]
        if len(pts) < 2:
            return
        pen = QPen(color)
        pen.setWidth(int(width))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for i in range(1, len(pts)):
            painter.drawLine(pts[i - 1], pts[i])

    def _tool_cursor(self, mode: str) -> QCursor:
        key = str(mode or "")
        cached = self._tool_cursor_cache.get(key)
        if cached is not None:
            return cached
        cursor = self._build_tool_cursor(key)
        self._tool_cursor_cache[key] = cursor
        return cursor

    @staticmethod
    def _cursor_accent_color(mode: str) -> QColor:
        if mode == "marker":
            return QColor("#FACC15")
        if mode == "eraser":
            return QColor("#38BDF8")
        if mode == "rect":
            return QColor("#22C55E")
        if mode == "arrow":
            return QColor("#F97316")
        return QColor("#2563EB")

    @staticmethod
    def _build_tool_cursor(mode: str) -> QCursor:
        if mode == "number":
            return AnnotationCanvasOverlay._build_number_tool_cursor()
        size = 42
        hotspot = QPointF(13.0, 13.0)
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            AnnotationCanvasOverlay._draw_tool_cursor_crosshair(painter, hotspot, AnnotationCanvasOverlay._cursor_accent_color(mode))
            AnnotationCanvasOverlay._draw_tool_cursor_badge(painter, mode)
        finally:
            if painter.isActive():
                painter.end()
        return QCursor(pixmap, int(hotspot.x()), int(hotspot.y()))

    @staticmethod
    def _build_number_tool_cursor() -> QCursor:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor("#FFFFFF"), 2))
            painter.setBrush(QBrush(QColor("#E53935")))
            painter.drawEllipse(7, 7, 18, 18)
            font = QFont()
            font.setBold(True)
            font.setPixelSize(14)
            painter.setFont(font)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(QRect(7, 7, 18, 18), int(Qt.AlignmentFlag.AlignCenter), "1")
        finally:
            if painter.isActive():
                painter.end()
        return QCursor(pixmap, 16, 16)

    @staticmethod
    def _draw_tool_cursor_crosshair(painter: QPainter, center: QPointF, accent: QColor) -> None:
        x = float(center.x())
        y = float(center.y())
        painter.setPen(QPen(QColor(255, 255, 255, 245), 3.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(x - 7.0, y), QPointF(x - 2.4, y))
        painter.drawLine(QPointF(x + 2.4, y), QPointF(x + 7.0, y))
        painter.drawLine(QPointF(x, y - 7.0), QPointF(x, y - 2.4))
        painter.drawLine(QPointF(x, y + 2.4), QPointF(x, y + 7.0))
        painter.setPen(QPen(QColor("#0F172A"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(x - 7.0, y), QPointF(x - 2.4, y))
        painter.drawLine(QPointF(x + 2.4, y), QPointF(x + 7.0, y))
        painter.drawLine(QPointF(x, y - 7.0), QPointF(x, y - 2.4))
        painter.drawLine(QPointF(x, y + 2.4), QPointF(x, y + 7.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 250)))
        painter.drawEllipse(center, 3.0, 3.0)
        painter.setBrush(QBrush(accent))
        painter.drawEllipse(center, 1.7, 1.7)

    @staticmethod
    def _draw_tool_cursor_badge(painter: QPainter, mode: str) -> None:
        badge = QRectF(20.0, 19.0, 18.0, 18.0)
        accent = AnnotationCanvasOverlay._cursor_accent_color(mode)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(15, 23, 42, 48)))
        painter.drawRoundedRect(badge.translated(1.2, 1.4), 5.0, 5.0)
        painter.setBrush(QBrush(QColor(248, 250, 252, 248)))
        painter.drawRoundedRect(badge, 5.0, 5.0)
        painter.setPen(QPen(QColor(148, 163, 184, 210), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(badge.adjusted(0.5, 0.5, -0.5, -0.5), 5.0, 5.0)

        if mode == "arrow":
            AnnotationCanvasOverlay._draw_cursor_arrow_glyph(painter, accent)
        elif mode == "rect":
            AnnotationCanvasOverlay._draw_cursor_rect_glyph(painter, accent)
        elif mode == "marker":
            AnnotationCanvasOverlay._draw_cursor_marker_glyph(painter, accent)
        elif mode == "eraser":
            AnnotationCanvasOverlay._draw_cursor_eraser_glyph(painter, accent)
        else:
            AnnotationCanvasOverlay._draw_cursor_pen_glyph(painter, accent)

    @staticmethod
    def _draw_cursor_arrow_glyph(painter: QPainter, accent: QColor) -> None:
        painter.setPen(QPen(accent, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QPointF(24.0, 32.0), QPointF(34.0, 23.0))
        painter.drawLine(QPointF(34.0, 23.0), QPointF(32.8, 29.0))
        painter.drawLine(QPointF(34.0, 23.0), QPointF(28.0, 24.4))

    @staticmethod
    def _draw_cursor_rect_glyph(painter: QPainter, accent: QColor) -> None:
        painter.setPen(QPen(accent, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(24.4, 24.1, 9.6, 7.8), 2.0, 2.0)

    @staticmethod
    def _draw_cursor_pen_glyph(painter: QPainter, accent: QColor) -> None:
        painter.setPen(QPen(QColor("#334155"), 1.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QBrush(QColor("#E2E8F0")))
        painter.drawPolygon(QPolygonF([QPointF(25.0, 32.0), QPointF(31.9, 24.0), QPointF(35.0, 27.0), QPointF(27.1, 34.0)]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(accent))
        painter.drawPolygon(QPolygonF([QPointF(23.4, 34.0), QPointF(25.0, 32.0), QPointF(27.1, 34.0)]))

    @staticmethod
    def _draw_cursor_marker_glyph(painter: QPainter, accent: QColor) -> None:
        marker_color = QColor(accent)
        marker_color.setAlpha(190)
        painter.setPen(QPen(marker_color, 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(24.0, 31.0), QPointF(35.0, 25.0))
        painter.setPen(QPen(QColor("#334155"), 1.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QBrush(QColor("#F8FAFC")))
        painter.drawPolygon(QPolygonF([QPointF(24.6, 30.6), QPointF(30.7, 23.4), QPointF(34.8, 27.0), QPointF(28.2, 33.4)]))

    @staticmethod
    def _draw_cursor_eraser_glyph(painter: QPainter, accent: QColor) -> None:
        painter.setPen(QPen(QColor("#334155"), 1.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QBrush(QColor("#F8FAFC")))
        painter.drawPolygon(QPolygonF([QPointF(24.0, 29.8), QPointF(29.4, 24.2), QPointF(35.0, 29.6), QPointF(29.5, 35.0)]))
        painter.setBrush(QBrush(accent))
        painter.drawPolygon(QPolygonF([QPointF(23.0, 28.8), QPointF(26.9, 24.8), QPointF(31.3, 29.1), QPointF(27.3, 33.1)]))
        painter.setPen(QPen(QColor("#94A3B8"), 1.0))
        painter.drawLine(QPointF(29.5, 35.0), QPointF(35.0, 29.6))

    def _text_font_for_size(self, w: int, h: int) -> QFont:
        font = QFont()
        font.setPixelSize(max(18, int(min(w, h) * 0.035)))
        return font

    def _text_editor_visual_offset(self, font: QFont, editor_height: int) -> QPoint:
        metrics = QFontMetrics(font)
        x_offset = 6
        y_offset = max(0, int(round((max(1, int(editor_height)) - int(metrics.height())) / 2.0)))
        return QPoint(int(x_offset), int(y_offset))

    def _text_editor_text_top_left(self, editor: QLineEdit, text: str) -> QPoint:
        try:
            current_rect = editor.cursorRect()
            original_pos = int(editor.cursorPosition())
            editor.setCursorPosition(0)
            start_rect = editor.cursorRect()
            editor.setCursorPosition(original_pos)
            x = int(editor.x() + start_rect.x() + round(float(start_rect.width()) / 2.0))
            y = int(editor.y() + current_rect.y())
            return QPoint(x, y)
        except Exception:
            font = self._text_font_for_size(max(1, int(self.width())), max(1, int(self.height())))
            visual_offset = self._text_editor_visual_offset(font, int(editor.height()))
            return QPoint(int(editor.x() + visual_offset.x()), int(editor.y() + visual_offset.y()))

    def _move_text_editor_caret_to_point(self, editor: QLineEdit, p: QPoint, font: QFont, editor_height: int) -> None:
        try:
            cursor_rect = editor.cursorRect()
            caret_top_right = cursor_rect.topRight()
            target_y = int(p.y() - round(float(cursor_rect.height()) / 2.0))
            editor.move(int(p.x() - caret_top_right.x()), int(target_y - caret_top_right.y()))
        except Exception:
            visual_offset = self._text_editor_visual_offset(font, editor_height)
            metrics = QFontMetrics(font)
            editor.move(int(p.x() - visual_offset.x()), int(p.y() - visual_offset.y() - round(float(metrics.height()) / 2.0)))

    def _eraser_radius(self) -> float:
        side = float(max(1, min(int(self.width()), int(self.height()))))
        return max(18.0, min(34.0, side * 0.018))

    def _draw_eraser_preview(self, painter: QPainter) -> None:
        if self._mode != "eraser" or not bool(self._drawing) or self._eraser_pos is None:
            return
        radius = float(self._eraser_radius())
        center = QPointF(float(self._eraser_pos.x()), float(self._eraser_pos.y()))

        # 1. 外部白色高对比度底圆 (含轻微半透明黑色遮罩，便于擦除时辨认)
        white_pen = QPen(QColor(255, 255, 255, 220))
        white_pen.setWidthF(2.5)
        painter.setPen(white_pen)
        painter.setBrush(QBrush(QColor(15, 23, 42, 16)))
        painter.drawEllipse(center, radius, radius)

        # 2. 内部蓝色虚线环 (极具现代感和精密操纵感)
        blue_pen = QPen(QColor(59, 130, 246, 230))
        blue_pen.setWidthF(1.2)
        blue_pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(blue_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius, radius)

        # 3. 中心细十字丝准星，引导精确操作
        cross_pen = QPen(QColor(59, 130, 246, 200))
        cross_pen.setWidthF(1.0)
        painter.setPen(cross_pen)
        painter.drawLine(QPointF(center.x() - 4.5, center.y()), QPointF(center.x() + 4.5, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - 4.5), QPointF(center.x(), center.y() + 4.5))


    def _stroke_width_for_command(self, cmd_type: str, w: int, h: int) -> float:
        if cmd_type == "marker":
            return float(max(12, int(min(w, h) * 0.025)))
        return float(max(3, int(min(w, h) * 0.005)))

    def _line_outside_eraser_ranges(
        self,
        start: QPointF,
        end: QPointF,
        px: float,
        py: float,
        radius: float,
    ) -> tuple[list[tuple[float, float]], bool]:
        x1, y1 = float(start.x()), float(start.y())
        x2, y2 = float(end.x()), float(end.y())
        dx = x2 - x1
        dy = y2 - y1
        length_sq = dx * dx + dy * dy
        radius_sq = radius * radius
        if length_sq <= 0.000001:
            inside = (x1 - px) * (x1 - px) + (y1 - py) * (y1 - py) <= radius_sq
            return ([], True) if inside else ([(0.0, 1.0)], False)

        fx = x1 - px
        fy = y1 - py
        b = 2.0 * (fx * dx + fy * dy)
        c = fx * fx + fy * fy - radius_sq
        discriminant = b * b - 4.0 * length_sq * c
        cuts = [0.0, 1.0]
        if discriminant > 0.000001:
            root = math.sqrt(discriminant)
            t1 = (-b - root) / (2.0 * length_sq)
            t2 = (-b + root) / (2.0 * length_sq)
            for t in (t1, t2):
                if 0.0 < t < 1.0:
                    cuts.append(float(t))
        cuts = sorted(set(round(t, 6) for t in cuts))

        ranges: list[tuple[float, float]] = []
        erased = False
        for left, right in zip(cuts, cuts[1:]):
            if right - left <= 0.000001:
                continue
            mid = (left + right) * 0.5
            mx = x1 + dx * mid
            my = y1 + dy * mid
            if (mx - px) * (mx - px) + (my - py) * (my - py) <= radius_sq:
                erased = True
            else:
                ranges.append((left, right))
        return ranges, erased

    def _split_polyline_by_eraser(
        self,
        cmd: dict[str, object],
        px: float,
        py: float,
        w: int,
        h: int,
        radius: float,
    ) -> tuple[bool, list[dict[str, object]]]:
        cmd_type = str(cmd.get("type", ""))
        raw_points = list(cmd.get("points") or [])  # type: ignore[arg-type]
        if len(raw_points) < 2:
            return False, [cmd]

        pts = [self._to_point(pt, w, h) for pt in raw_points]
        hit_radius = radius + self._stroke_width_for_command(cmd_type, w, h) * 0.5
        pieces: list[dict[str, object]] = []
        current: list[tuple[float, float]] = []
        erased = False

        def add_point(point: QPointF) -> None:
            x = float(max(0.0, min(float(w), float(point.x()))))
            y = float(max(0.0, min(float(h), float(point.y()))))
            if current and math.hypot(current[-1][0] - x, current[-1][1] - y) < 0.5:
                return
            current.append((x, y))

        def point_at(start: QPointF, end: QPointF, t: float) -> QPointF:
            return QPointF(
                float(start.x() + (end.x() - start.x()) * t),
                float(start.y() + (end.y() - start.y()) * t),
            )

        def flush_current() -> None:
            nonlocal current
            if len(current) >= 2:
                norm_points = [
                    (float(x) / float(max(1, w)), float(y) / float(max(1, h)))
                    for x, y in current
                ]
                new_cmd = dict(cmd)
                new_cmd["points"] = norm_points
                pieces.append(new_cmd)
            current = []

        for index in range(1, len(pts)):
            start = pts[index - 1]
            end = pts[index]
            ranges, segment_erased = self._line_outside_eraser_ranges(start, end, px, py, hit_radius)
            erased = bool(erased or segment_erased)
            if not ranges:
                flush_current()
                continue
            for left, right in ranges:
                left_point = point_at(start, end, left)
                right_point = point_at(start, end, right)
                if not current:
                    add_point(left_point)
                elif math.hypot(current[-1][0] - left_point.x(), current[-1][1] - left_point.y()) >= 0.5:
                    flush_current()
                    add_point(left_point)
                add_point(right_point)
                if right < 0.999999:
                    flush_current()

        if erased:
            flush_current()
            return True, pieces
        return False, [cmd]

    def _draw_arrow(self, painter: QPainter, start: object, end: object, w: int, h: int) -> None:
        p1 = self._to_point(start, w, h)
        p2 = self._to_point(end, w, h)
        arrow_color = self._get_style_color("arrow")

        lw = max(3.0, float(min(w, h)) * 0.006)

        dx = float(p2.x() - p1.x())
        dy = float(p2.y() - p1.y())
        dist = math.hypot(dx, dy)
        if dist < 1.0:
            return
        angle = math.atan2(dy, dx)
        head_len = max(14.0, float(min(w, h)) * 0.035)
        spread = math.radians(26.0)

        # 精确构造内凹燕尾箭头的四个顶点
        pt_a = QPointF(float(p2.x() - head_len * math.cos(angle - spread)), float(p2.y() - head_len * math.sin(angle - spread)))
        pt_b = QPointF(float(p2.x() - head_len * math.cos(angle + spread)), float(p2.y() - head_len * math.sin(angle + spread)))
        pt_c = QPointF(float(p2.x() - head_len * 0.72 * math.cos(angle)), float(p2.y() - head_len * 0.72 * math.sin(angle)))

        poly = QPolygonF([p2, pt_a, pt_c, pt_b])

        # 1. 绘制外部白色高对比度图层 (双色设计)
        # 绘制主干线白底
        white_pen = QPen(QColor(255, 255, 255, 210))
        white_pen.setWidthF(lw + 1.6)
        white_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(white_pen)
        painter.drawLine(p1, p2)

        # 绘制并填充箭帽白底，实现完美的白描边包裹
        painter.setBrush(QBrush(QColor(255, 255, 255, 210)))
        painter.setPen(QPen(QColor(255, 255, 255, 210), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPolygon(poly)

        # 2. 绘制内部主色图层
        # 绘制主干线前景色 (连结到燕尾内凹点，规避接缝凸出)
        main_pen = QPen(arrow_color)
        main_pen.setWidthF(lw)
        main_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(main_pen)
        painter.drawLine(p1, pt_c)

        # 填充并绘制前景色箭帽
        painter.setBrush(QBrush(arrow_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(poly)


    def _draw_number(self, painter: QPainter, pos: object, n: object, w: int, h: int) -> None:
        p = self._to_point(pos, w, h)
        r = max(13, int(min(w, h) * 0.025))
        rect = QRect(int(p.x() - r), int(p.y() - r), int(r * 2), int(r * 2))
        painter.setPen(QPen(QColor("#FFFFFF"), max(2, int(r * 0.12))))
        painter.setBrush(QBrush(self._get_style_color("number")))
        painter.drawEllipse(rect)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(13, int(r * 1.05)))
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), str(n))

    def _draw_text(self, painter: QPainter, pos: object, text: object, w: int, h: int) -> None:
        p = self._to_point(pos, w, h)
        font = self._text_font_for_size(w, h)
        painter.setFont(font)
        painter.setPen(QPen(self._get_style_color("text")))
        metrics = QFontMetrics(font)
        painter.drawText(QPointF(float(p.x()), float(p.y() + metrics.ascent())), str(text))

    def _draw_command(self, painter: QPainter, cmd: dict[str, object], w: int, h: int) -> None:
        typ = str(cmd.get("type", ""))
        if typ == "arrow":
            self._draw_arrow(painter, cmd.get("start"), cmd.get("end"), w, h)
        elif typ == "rect":
            self._draw_rect(painter, cmd.get("start"), cmd.get("end"), w, h)
        elif typ == "pen":
            self._draw_polyline(painter, cmd.get("points"), w, h, self._get_style_color("pen"), max(3, int(min(w, h) * 0.005)))
        elif typ == "marker":
            self._draw_polyline(painter, cmd.get("points"), w, h, self._get_style_color("marker"), max(12, int(min(w, h) * 0.025)))
        elif typ == "number":
            self._draw_number(painter, cmd.get("pos"), cmd.get("n"), w, h)
        elif typ == "text":
            self._draw_text(painter, cmd.get("pos"), cmd.get("text"), w, h)

    def _dist_to_segment(self, x: float, y: float, x1: float, y1: float, x2: float, y2: float) -> float:
        dx = x2 - x1
        dy = y2 - y1
        l2 = dx*dx + dy*dy
        if l2 == 0:
            return math.hypot(x - x1, y - y1)
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / l2))
        proj_x = x1 + t * dx
        proj_y = y1 + t * dy
        return math.hypot(x - proj_x, y - proj_y)

    def _distance_to_command(self, cmd: dict[str, object], px: float, py: float, w: float, h: float) -> float:
        typ = str(cmd.get("type", ""))
        if typ == "arrow":
            p1 = self._to_point(cmd.get("start"), w, h)
            p2 = self._to_point(cmd.get("end"), w, h)
            return self._dist_to_segment(px, py, p1.x(), p1.y(), p2.x(), p2.y())
        elif typ == "rect":
            p1 = self._to_point(cmd.get("start"), w, h)
            p2 = self._to_point(cmd.get("end"), w, h)
            x1, y1 = p1.x(), p1.y()
            x2, y2 = p2.x(), p2.y()
            d1 = self._dist_to_segment(px, py, x1, y1, x2, y1)
            d2 = self._dist_to_segment(px, py, x2, y1, x2, y2)
            d3 = self._dist_to_segment(px, py, x2, y2, x1, y2)
            d4 = self._dist_to_segment(px, py, x1, y2, x1, y1)
            return min(d1, d2, d3, d4)
        elif typ in {"pen", "marker"}:
            pts = [self._to_point(pt, w, h) for pt in list(cmd.get("points") or [])]  # type: ignore[arg-type]
            if not pts:
                return 999999.0
            if len(pts) == 1:
                return math.hypot(px - pts[0].x(), py - pts[0].y())
            min_d = 999999.0
            for i in range(1, len(pts)):
                d = self._dist_to_segment(px, py, pts[i - 1].x(), pts[i - 1].y(), pts[i].x(), pts[i].y())
                if d < min_d:
                    min_d = d
            return min_d
        elif typ == "number":
            pos = self._to_point(cmd.get("pos"), w, h)
            return math.hypot(px - pos.x(), py - pos.y())
        elif typ == "text":
            pos = self._to_point(cmd.get("pos"), w, h)
            return self._dist_to_segment(px, py, pos.x(), pos.y(), pos.x() + 80, pos.y())
        return 999999.0

    def _erase_at(self, p: QPoint) -> bool:
        w = max(1, int(self.width()))
        h = max(1, int(self.height()))
        px, py = float(p.x()), float(p.y())
        threshold = self._eraser_radius()
        changed = False
        remaining: list[dict[str, object]] = []
        for cmd in self._commands:
            cmd_type = str(cmd.get("type", ""))
            if cmd_type in {"pen", "marker"}:
                split_changed, pieces = self._split_polyline_by_eraser(cmd, px, py, w, h, threshold)
                if split_changed:
                    changed = True
                    remaining.extend(pieces)
                    continue
            else:
                dist = self._distance_to_command(cmd, px, py, w, h)
                if dist < threshold:
                    changed = True
                    continue
            remaining.append(cmd)
        if changed:
            self._commands = remaining
            self._redo.clear()
            self._recount_numbers()
            self.update()
            self.changed.emit()
            self._emit_history()
        return bool(changed)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._mode:
            return super().mousePressEvent(event)
        p = event.position().toPoint()
        if self._mode == "eraser":
            self._drawing = True
            self._eraser_pos = QPoint(p)
            self._eraser_changed = bool(self._erase_at(p))
            if not bool(self._eraser_changed):
                self.update()
            event.accept()
            return
        if self._mode == "number":
            self._commands.append({"type": "number", "pos": self._norm_point(p), "n": int(self._next_number)})
            self._next_number += 1
            self._redo.clear()
            self.update()
            self.changed.emit()
            self._emit_history()
            event.accept()
            return
        if self._mode == "text":
            self._begin_text_editor(p)
            event.accept()
            return
        self._drawing = True
        np0 = self._norm_point(p)
        if self._mode in {"arrow", "rect"}:
            self._temp = {"type": self._mode, "start": np0, "end": np0}
        elif self._mode in {"pen", "marker"}:
            self._temp = {"type": self._mode, "points": [np0]}
        self.update()
        self.changed.emit()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._mode == "eraser" and self._drawing:
            p = event.position().toPoint()
            self._eraser_pos = QPoint(p)
            if bool(self._erase_at(p)):
                self._eraser_changed = True
            else:
                self.update()
            event.accept()
            return
        if not bool(self._drawing) or self._temp is None:
            return super().mouseMoveEvent(event)
        p = self._norm_point(event.position().toPoint())
        if self._temp.get("type") in {"arrow", "rect"}:
            self._temp["end"] = p
        else:
            pts = self._temp.get("points")
            if isinstance(pts, list):
                pts.append(p)
        self.update()
        self.changed.emit()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._mode == "eraser" and event.button() == Qt.MouseButton.LeftButton:
            should_flush = bool(getattr(self, "_eraser_changed", False))
            self._drawing = False
            self._eraser_changed = False
            self._eraser_pos = None
            if should_flush:
                self.changed.emit()
            self.update()
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton or self._temp is None:
            return super().mouseReleaseEvent(event)
        cmd = dict(self._temp)
        self._drawing = False
        self._temp = None
        ok = True
        if cmd.get("type") in {"arrow", "rect"}:
            s = cmd.get("start")
            e = cmd.get("end")
            try:
                ok = math.hypot(float(e[0]) - float(s[0]), float(e[1]) - float(s[1])) > 0.01  # type: ignore[index]
            except Exception:
                ok = False
        else:
            pts = cmd.get("points")
            ok = isinstance(pts, list) and len(pts) >= 2
        if bool(ok):
            self._commands.append(cmd)
            self._redo.clear()
            self.changed.emit()
            self._emit_history()
        self.update()
        event.accept()

    def _begin_text_editor(self, p: QPoint) -> None:
        self._commit_text_editor()
        editor = QLineEdit(self)
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        font = self._text_font_for_size(max(1, int(self.width())), max(1, int(self.height())))
        metrics = QFontMetrics(font)
        editor_height = max(34, int(metrics.height()) + 12)
        self._text_editor_anchor = self._norm_point(p)
        editor.setFont(font)
        editor.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.96); border: 1px solid rgba(229,57,53,0.80);"
            f" border-radius: 4px; padding: 4px 6px; font-size: {font.pixelSize()}px; color: #E53935; }}"
        )
        editor.resize(180, editor_height)
        editor.show()
        editor.raise_()
        editor.setFocus()
        self._move_text_editor_caret_to_point(editor, p, font, editor_height)
        editor.returnPressed.connect(self._commit_text_editor)
        editor.editingFinished.connect(self._commit_text_editor)
        self._text_editor = editor

    def _commit_text_editor(self) -> None:
        editor = self._text_editor
        if editor is None:
            return
        self._text_editor = None
        try:
            text = str(editor.text()).strip()
            if text:
                pos = self._norm_point(self._text_editor_text_top_left(editor, text))
            elif self._text_editor_anchor is not None:
                pos = self._text_editor_anchor
            else:
                pos = self._norm_point(editor.pos())
            editor.close()
        except Exception:
            text = ""
            pos = (0.0, 0.0)
        self._text_editor_anchor = None
        if not text:
            return
        self._commands.append({"type": "text", "pos": pos, "text": text})
        self._redo.clear()
        self.update()
        self.changed.emit()
        self._emit_history()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w = int(max(1, self.width()))
            h = int(max(1, self.height()))
            if not bool(self._commands_rendered_to_parent):
                for cmd in self._commands:
                    self._draw_command(painter, cmd, w, h)
            if self._temp is not None:
                self._draw_command(painter, self._temp, w, h)
            self._draw_eraser_preview(painter)
        finally:
            if painter.isActive():
                painter.end()
