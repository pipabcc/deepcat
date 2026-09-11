from __future__ import annotations

import math
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QRect, QRectF, QPoint, QPointF, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont, QFontMetrics, QGuiApplication, QImage, QPainter, QPainterPath, QPainterPathStroker, QPen, QCursor, QPixmap, QPolygonF
from PyQt6.QtWidgets import QLineEdit, QWidget

from deepcat.settings_store import normalize_annotation_style


def _virtual_desktop_geometry() -> QRect:
    screens = list(QGuiApplication.screens() or [])
    if not screens:
        return QRect(0, 0, 800, 600)
    geo = QRect(screens[0].geometry())
    for screen in screens[1:]:
        geo = geo.united(QRect(screen.geometry()))
    return geo


class SelectionShadeOverlay(QWidget):
    """Mouse-transparent shade around a confirmed selection."""

    def __init__(self, selection_rect: Optional[QRect] = None, shade_alpha: int = 105) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(0, 0, 0, 0))
        self.setPalette(palette)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._selection_rect = QRect(selection_rect) if selection_rect is not None else QRect()
        self._shade_alpha = int(max(0, min(255, shade_alpha)))
        self._on_first_paint: Optional[Callable[[], None]] = None
        self._sync_widget_geometry()
        self.setWindowOpacity(0.01)
        self._is_first_paint = True
        self.winId()

    def _exclude_window_from_capture(self) -> None:
        try:
            import ctypes

            hwnd = int(self.winId())
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            ctypes.windll.user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), ctypes.c_uint(WDA_EXCLUDEFROMCAPTURE))
        except Exception:
            pass

    def showEvent(self, event) -> None:
        self._exclude_window_from_capture()
        super().showEvent(event)

    def set_selection_rect(self, rect: QRect) -> None:
        self._selection_rect = QRect(rect)
        self._sync_widget_geometry()
        self.update()

    def selection_rect(self) -> QRect:
        return QRect(self._selection_rect)

    def _sync_widget_geometry(self) -> None:
        geo = _virtual_desktop_geometry()
        super().setGeometry(int(geo.x()), int(geo.y()), int(max(1, geo.width())), int(max(1, geo.height())))

    @staticmethod
    def _fill_outside_rect(painter: QPainter, canvas_rect: QRect, selection_rect: QRect, color: QColor) -> None:
        selection = QRect(selection_rect).normalized().intersected(canvas_rect)
        if selection.isEmpty():
            painter.fillRect(canvas_rect, color)
            return
        if selection.top() > canvas_rect.top():
            painter.fillRect(
                QRect(canvas_rect.left(), canvas_rect.top(), canvas_rect.width(), int(selection.top() - canvas_rect.top())),
                color,
            )
        if selection.left() > canvas_rect.left():
            painter.fillRect(
                QRect(canvas_rect.left(), selection.top(), int(selection.left() - canvas_rect.left()), selection.height()),
                color,
            )
        right_x = int(selection.right() + 1)
        if right_x <= canvas_rect.right():
            painter.fillRect(
                QRect(right_x, selection.top(), int(canvas_rect.right() - right_x + 1), selection.height()),
                color,
            )
        bottom_y = int(selection.bottom() + 1)
        if bottom_y <= canvas_rect.bottom():
            painter.fillRect(
                QRect(canvas_rect.left(), bottom_y, canvas_rect.width(), int(canvas_rect.bottom() - bottom_y + 1)),
                color,
            )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(Qt.PenStyle.NoPen)
            shade_rect_f = QRectF(self.rect())

            dpr = float(self.devicePixelRatioF())
            logical_left = float(self._selection_rect.x())
            logical_top = float(self._selection_rect.y())
            logical_width = float(self._selection_rect.width())
            logical_height = float(self._selection_rect.height())

            left_px = int(math.floor(logical_left * dpr))
            top_px = int(math.floor(logical_top * dpr))
            right_px = int(math.ceil((logical_left + logical_width) * dpr))
            bottom_px = int(math.ceil((logical_top + logical_height) * dpr))
            width_px = int(max(1, right_px - left_px))
            height_px = int(max(1, bottom_px - top_px))

            win_pos = self.geometry().topLeft()
            win_x_px = int(round(float(win_pos.x()) * dpr))
            win_y_px = int(round(float(win_pos.y()) * dpr))

            target_x_px = left_px - win_x_px
            target_y_px = top_px - win_y_px

            sel_left_aligned = float(target_x_px) / dpr
            sel_top_aligned = float(target_y_px) / dpr
            sel_width_aligned = float(width_px) / dpr
            sel_height_aligned = float(height_px) / dpr

            rect_aligned = QRectF(sel_left_aligned, sel_top_aligned, sel_width_aligned, sel_height_aligned)
            shade_color = QColor(0, 0, 0, self._shade_alpha)

            if not rect_aligned.isEmpty():
                if rect_aligned.top() > shade_rect_f.top():
                    painter.fillRect(
                        QRectF(
                            shade_rect_f.left(),
                            shade_rect_f.top(),
                            shade_rect_f.width(),
                            rect_aligned.top() - shade_rect_f.top(),
                        ),
                        shade_color,
                    )
                if rect_aligned.bottom() < shade_rect_f.bottom():
                    painter.fillRect(
                        QRectF(
                            shade_rect_f.left(),
                            rect_aligned.bottom(),
                            shade_rect_f.width(),
                            shade_rect_f.bottom() - rect_aligned.bottom(),
                        ),
                        shade_color,
                    )
                if rect_aligned.left() > shade_rect_f.left():
                    painter.fillRect(
                        QRectF(
                            shade_rect_f.left(),
                            rect_aligned.top(),
                            rect_aligned.left() - shade_rect_f.left(),
                            rect_aligned.height(),
                        ),
                        shade_color,
                    )
                if rect_aligned.right() < shade_rect_f.right():
                    painter.fillRect(
                        QRectF(
                            rect_aligned.right(),
                            rect_aligned.top(),
                            shade_rect_f.right() - rect_aligned.right(),
                            rect_aligned.height(),
                        ),
                        shade_color,
                    )
        finally:
            if painter.isActive():
                painter.end()
            if getattr(self, "_is_first_paint", False):
                self._is_first_paint = False
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(40, lambda: self.setWindowOpacity(1.0))
            if getattr(self, "_on_first_paint", None) is not None:
                try:
                    self._on_first_paint()
                except Exception:
                    pass
                self._on_first_paint = None


class SelectionBorderOverlay(QWidget):
    rect_changed = pyqtSignal(object)
    rect_released = pyqtSignal(object)
    escape_pressed = pyqtSignal()
    annotation_changed = pyqtSignal()
    annotation_history_changed = pyqtSignal(bool, bool)
    pin_requested = pyqtSignal()
    save_requested = pyqtSignal()
    copy_requested = pyqtSignal()
    _BORDER_PAD = 1
    _DIMENSION_TIP_MIN_WIDTH = 108
    _DIMENSION_TIP_HEIGHT = 26
    _DIMENSION_TIP_GAP = 6


    def __init__(self, rect: QRect, interactive: bool = False) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(0, 0, 0, 0))
        self.setPalette(palette)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._interactive = bool(interactive)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not bool(self._interactive))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMouseTracking(bool(self._interactive))
        self._interaction_enabled = bool(self._interactive)
        self._selection_rect = QRect(rect)
        self._margin = 12 if bool(self._interactive) else 0
        self._dimension_tip_inside = False
        self._geometry_top_reserve = 0
        self._geometry_side_reserve = 0
        self._drag_handle: Optional[str] = None
        self._drag_start_global = None
        self._drag_start_geo = None
        self._pending_rect_changed = False
        self._rect_changed_timer = QTimer(self)
        self._rect_changed_timer.setSingleShot(True)
        self._rect_changed_timer.setInterval(24)
        self._rect_changed_timer.timeout.connect(self._flush_pending_rect_changed)
        self._edge_grip = 12
        self._dot_r = 3
        self._dot_hit = 12
        self._min_w = 40
        self._min_h = 40
        self._cursors = self._build_cursors()
        self._tool_cursors = self._build_tool_cursors()
        self._annotation_style = normalize_annotation_style(None)
        self._annotation_mode = ""
        self._annotation_commands: list[dict[str, object]] = []
        self._annotation_redo: list[dict[str, object]] = []
        self._annotation_temp: Optional[dict[str, object]] = None
        self._annotation_drawing = False
        self._annotation_drag_index: Optional[int] = None
        self._annotation_drag_start: Optional[tuple[float, float]] = None
        self._annotation_drag_original: Optional[dict[str, object]] = None
        self._annotation_next_number = 1
        self._text_editor: Optional[QLineEdit] = None
        self._text_editor_anchor: Optional[tuple[float, float]] = None
        self._annotation_image_bgr = None
        self._annotation_blurred_bgr = None
        self._annotation_blurred_image: Optional[QImage] = None
        self._sync_widget_geometry()

    def set_annotation_style(self, style: object) -> None:
        self._annotation_style = normalize_annotation_style(style)
        self.update()

    def _style_hex(self, key: str, default: str = "#E53935") -> str:
        value = str(self._annotation_style.get(str(key), default) or default)
        color = QColor(value)
        return color.name(QColor.NameFormat.HexRgb).upper() if color.isValid() else str(default)

    def _line_style(self) -> str:
        return "solid" if str(self._annotation_style.get("line_style", "dash")) == "solid" else "dash"

    def _color_from_command(self, cmd: dict[str, object], key: str, default: str = "#E53935", alpha: Optional[int] = None) -> QColor:
        color = QColor(str(cmd.get("color", "") or self._style_hex(key, default)))
        if not color.isValid():
            color = QColor(default)
        try:
            if "alpha" in cmd:
                color.setAlpha(max(0, min(255, int(cmd.get("alpha", 255)))))
            elif alpha is not None:
                color.setAlpha(max(0, min(255, int(alpha))))
        except Exception:
            if alpha is not None:
                color.setAlpha(max(0, min(255, int(alpha))))
        return color

    def _apply_pen_line_style(self, pen: QPen, line_style: object = None) -> None:
        style = str(line_style or self._line_style()).strip().lower()
        if style in {"solid", "实线"}:
            pen.setStyle(Qt.PenStyle.SolidLine)
        else:
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([5.0, 4.0])

    def _exclude_window_from_capture(self) -> None:
        try:
            import ctypes

            hwnd = int(self.winId())
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            ctypes.windll.user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), ctypes.c_uint(WDA_EXCLUDEFROMCAPTURE))
        except Exception:
            pass

    def showEvent(self, event) -> None:
        self._exclude_window_from_capture()
        super().showEvent(event)
        try:
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        except Exception:
            pass

    def set_annotation_mode(self, mode: str) -> None:
        self._commit_text_editor()
        self._annotation_mode = str(mode or "")
        if bool(self._annotation_mode):
            cursor = self._tool_cursors.get(str(self._annotation_mode))
            if cursor is not None:
                self.setCursor(cursor)
            elif self._annotation_mode == "text":
                self.setCursor(Qt.CursorShape.IBeamCursor)
            self.raise_()
        else:
            self.unsetCursor()

    def clear_annotations(self) -> None:
        self._commit_text_editor()
        self._annotation_commands.clear()
        self._annotation_redo.clear()
        self._annotation_temp = None
        self._annotation_drawing = False
        self._annotation_drag_index = None
        self._annotation_drag_start = None
        self._annotation_drag_original = None
        self._annotation_next_number = 1
        self.update()
        self.annotation_changed.emit()
        self._emit_annotation_history()

    def undo_annotation(self) -> None:
        self._commit_text_editor()
        if not self._annotation_commands:
            return
        self._annotation_redo.append(self._annotation_commands.pop())
        self._recount_numbers()
        self.update()
        self.annotation_changed.emit()
        self._emit_annotation_history()

    def redo_annotation(self) -> None:
        self._commit_text_editor()
        if not self._annotation_redo:
            return
        self._annotation_commands.append(self._annotation_redo.pop())
        self._recount_numbers()
        self.update()
        self.annotation_changed.emit()
        self._emit_annotation_history()

    def commit_pending_annotation(self) -> None:
        self._commit_text_editor()

    def keyPressEvent(self, event) -> None:
        key = int(event.key())
        modifiers = event.modifiers()
        if key == int(Qt.Key.Key_Escape):
            self.escape_pressed.emit()
            event.accept()
            return
        elif (modifiers & Qt.KeyboardModifier.AltModifier) and key == int(Qt.Key.Key_T):
            self.pin_requested.emit()
            event.accept()
            return
        elif (modifiers & Qt.KeyboardModifier.AltModifier) and key == int(Qt.Key.Key_S):
            self.save_requested.emit()
            event.accept()
            return
        elif (modifiers & Qt.KeyboardModifier.AltModifier) and key == int(Qt.Key.Key_C):
            self.copy_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


    def set_annotation_image(self, image_bgr) -> None:
        try:
            self._annotation_image_bgr = image_bgr
            self._annotation_blurred_bgr = None
            self._annotation_blurred_image = None
            self.update()
        except Exception:
            self._annotation_image_bgr = None
            self._annotation_blurred_bgr = None
            self._annotation_blurred_image = None

    def _bgr_to_qimage(self, image_bgr) -> QImage:
        rgb = image_bgr[:, :, ::-1].copy()
        h, w = int(rgb.shape[0]), int(rgb.shape[1])
        return QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format.Format_RGB888).copy()

    def render_annotations_to_bgr(self, base_bgr):
        self._commit_text_editor()
        if base_bgr is None:
            return None
        try:
            import numpy as np
        except Exception:
            return base_bgr.copy()
        if not self._annotation_commands:
            return base_bgr.copy()
        rendered_bgr = self._apply_blur_commands_to_bgr(base_bgr)
        rgb = rendered_bgr[:, :, ::-1].copy()
        h, w = int(rgb.shape[0]), int(rgb.shape[1])
        qimg = QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format.Format_RGB888).copy()
        painter = QPainter(qimg)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            for cmd in self._annotation_commands:
                if cmd.get("type") == "blur":
                    continue
                self._draw_annotation_command(painter, cmd, QRect(0, 0, w, h))
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

    def _blur_commands(self, include_temp: bool = False) -> list[dict[str, object]]:
        cmds = [cmd for cmd in self._annotation_commands if cmd.get("type") == "blur"]
        if bool(include_temp) and self._annotation_temp is not None and self._annotation_temp.get("type") == "blur":
            cmds.append(self._annotation_temp)
        return [dict(cmd) for cmd in cmds]

    def _blur_mask_for_commands(self, w: int, h: int, blur_cmds: list[dict[str, object]]):
        import cv2
        import numpy as np

        radius = self._blur_radius_for_size(w, h)
        mask = np.zeros((h, w), dtype=np.uint8)
        for cmd in blur_cmds:
            px_points: list[tuple[int, int]] = []
            for x, y in list(cmd.get("points") or []):
                px = int(max(0, min(w - 1, round(float(x) * float(w)))))
                py = int(max(0, min(h - 1, round(float(y) * float(h)))))
                px_points.append((px, py))
                cv2.circle(mask, (px, py), int(radius), 255, -1, lineType=cv2.LINE_AA)
            for i in range(1, len(px_points)):
                cv2.line(mask, px_points[i - 1], px_points[i], 255, int(radius * 2), lineType=cv2.LINE_AA)
        if bool(mask.any()):
            block = self._mosaic_block_for_size(w, h)
            kernel_size = max(3, int(block // 8) * 2 + 1)
            kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
            mask = cv2.dilate(mask, kernel, iterations=1)
        return mask

    def _apply_blur_commands_to_bgr(self, base_bgr, blur_cmds: Optional[list[dict[str, object]]] = None):
        blur_cmds = self._blur_commands(include_temp=False) if blur_cmds is None else list(blur_cmds)
        if not blur_cmds:
            return base_bgr.copy()
        try:
            import numpy as np  # noqa: F401
            h, w = int(base_bgr.shape[0]), int(base_bgr.shape[1])
            if w <= 0 or h <= 0:
                return base_bgr.copy()
            blurred = self._blurred_bgr_for_base(base_bgr)
            mask = self._blur_mask_for_commands(w, h, blur_cmds)
        except Exception:
            return base_bgr.copy()
        if not bool(mask.any()):
            return base_bgr.copy()
        out = base_bgr.copy()
        out[mask > 12] = blurred[mask > 12]
        return out

    def _emit_annotation_history(self) -> None:
        self.annotation_history_changed.emit(bool(self._annotation_commands), bool(self._annotation_redo))

    def _recount_numbers(self) -> None:
        max_n = 0
        for cmd in self._annotation_commands:
            if cmd.get("type") == "number":
                try:
                    max_n = max(max_n, int(cmd.get("n", 0)))
                except Exception:
                    pass
        self._annotation_next_number = int(max_n + 1)

    def _annotation_rect(self) -> QRect:
        return QRect(self._edge_rect()).adjusted(1, 1, -1, -1)

    def _norm_annotation_point(self, p: QPoint) -> tuple[float, float]:
        r = self._annotation_rect()
        w = max(1, int(r.width()))
        h = max(1, int(r.height()))
        x = max(0, min(w, int(p.x()) - int(r.left())))
        y = max(0, min(h, int(p.y()) - int(r.top())))
        return (float(x) / float(w), float(y) / float(h))

    def _append_annotation_path_point(self, points: list, p: tuple[float, float], typ: str) -> None:
        if not points:
            points.append(p)
            return
        try:
            last = points[-1]
            lx = float(last[0])
            ly = float(last[1])
            px = float(p[0])
            py = float(p[1])
        except Exception:
            points.append(p)
            return
        r = self._annotation_rect()
        w = max(1.0, float(r.width()))
        h = max(1.0, float(r.height()))
        dx = (px - lx) * w
        dy = (py - ly) * h
        dist = math.hypot(dx, dy)
        if dist < 0.8:
            return
        if str(typ) == "blur":
            max_step = max(3.0, float(self._blur_radius_for_size(int(w), int(h))) * 0.35)
        elif str(typ) == "marker":
            max_step = max(3.0, float(min(w, h)) * 0.010)
        else:
            max_step = max(2.0, float(min(w, h)) * 0.006)
        steps = max(1, int(math.ceil(dist / max_step)))
        for i in range(1, steps + 1):
            t = float(i) / float(steps)
            points.append((lx + (px - lx) * t, ly + (py - ly) * t))

    def _copy_annotation_command(self, cmd: dict[str, object]) -> dict[str, object]:
        copied = dict(cmd)
        if isinstance(copied.get("points"), list):
            pts = []
            for p in copied.get("points") or []:
                try:
                    pts.append((float(p[0]), float(p[1])))  # type: ignore[index]
                except Exception:
                    pass
            copied["points"] = pts
        for key in ("start", "end", "pos"):
            if key in copied:
                try:
                    p = copied.get(key)
                    copied[key] = (float(p[0]), float(p[1]))  # type: ignore[index]
                except Exception:
                    pass
        return copied

    def _annotation_command_points(self, cmd: dict[str, object]) -> list[tuple[float, float]]:
        typ = str(cmd.get("type", ""))
        if typ in {"pen", "marker", "blur"}:
            pts: list[tuple[float, float]] = []
            for p in list(cmd.get("points") or []):
                try:
                    pts.append((float(p[0]), float(p[1])))  # type: ignore[index]
                except Exception:
                    pass
            return pts
        if typ in {"arrow", "rect"}:
            pts = []
            for key in ("start", "end"):
                try:
                    p = cmd.get(key)
                    pts.append((float(p[0]), float(p[1])))  # type: ignore[index]
                except Exception:
                    pass
            return pts
        if typ in {"number", "text"}:
            try:
                p = cmd.get("pos")
                return [(float(p[0]), float(p[1]))]  # type: ignore[index]
            except Exception:
                return []
        return []

    def _annotation_command_bounds(self, cmd: dict[str, object]) -> Optional[tuple[float, float, float, float]]:
        pts = self._annotation_command_points(cmd)
        if not pts:
            return None
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def _offset_annotation_command(self, cmd: dict[str, object], dx: float, dy: float) -> dict[str, object]:
        out = self._copy_annotation_command(cmd)
        bounds = self._annotation_command_bounds(out)
        if bounds is not None:
            min_x, min_y, max_x, max_y = bounds
            dx = max(-float(min_x), min(1.0 - float(max_x), float(dx)))
            dy = max(-float(min_y), min(1.0 - float(max_y), float(dy)))

        def moved(p: object) -> tuple[float, float]:
            try:
                return (max(0.0, min(1.0, float(p[0]) + dx)), max(0.0, min(1.0, float(p[1]) + dy)))  # type: ignore[index]
            except Exception:
                return (0.0, 0.0)

        typ = str(out.get("type", ""))
        if typ in {"pen", "marker", "blur"}:
            out["points"] = [moved(p) for p in list(out.get("points") or [])]
        elif typ in {"arrow", "rect"}:
            out["start"] = moved(out.get("start"))
            out["end"] = moved(out.get("end"))
        elif typ in {"number", "text"}:
            out["pos"] = moved(out.get("pos"))
        return out

    def _distance_to_segment(self, p: QPointF, a: QPointF, b: QPointF) -> float:
        px, py = float(p.x()), float(p.y())
        ax, ay = float(a.x()), float(a.y())
        bx, by = float(b.x()), float(b.y())
        vx, vy = bx - ax, by - ay
        denom = vx * vx + vy * vy
        if denom <= 0.001:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / denom))
        cx = ax + t * vx
        cy = ay + t * vy
        return math.hypot(px - cx, py - cy)

    def _annotation_hit_index(self, p: QPoint) -> Optional[int]:
        rect = self._annotation_rect()
        pf = QPointF(float(p.x()), float(p.y()))
        min_side = max(1, min(int(rect.width()), int(rect.height())))
        for idx in range(len(self._annotation_commands) - 1, -1, -1):
            cmd = self._annotation_commands[idx]
            typ = str(cmd.get("type", ""))
            if typ in {"pen", "marker", "blur"}:
                pts = [self._annotation_point(pt, rect) for pt in list(cmd.get("points") or [])]  # type: ignore[arg-type]
                if not pts:
                    continue
                if typ == "blur":
                    tol = float(self._blur_radius_for_size(int(rect.width()), int(rect.height())) + 8)
                elif typ == "marker":
                    tol = float(max(12, int(min_side * 0.025)) + 8)
                else:
                    tol = float(max(8, int(min_side * 0.012)))
                if any(math.hypot(float(pf.x() - pt.x()), float(pf.y() - pt.y())) <= tol for pt in pts):
                    return idx
                for i in range(1, len(pts)):
                    if self._distance_to_segment(pf, pts[i - 1], pts[i]) <= tol:
                        return idx
            elif typ == "arrow":
                p1 = self._annotation_point(cmd.get("start"), rect)
                p2 = self._annotation_point(cmd.get("end"), rect)
                tol = float(max(9, int(min_side * 0.018)))
                if self._distance_to_segment(pf, p1, p2) <= tol:
                    return idx
            elif typ == "rect":
                p1 = self._annotation_point(cmd.get("start"), rect)
                p2 = self._annotation_point(cmd.get("end"), rect)
                rr = QRectF(p1, p2).normalized()
                tol = float(max(8, int(min_side * 0.012)))
                if rr.adjusted(-tol, -tol, tol, tol).contains(pf):
                    inner = rr.adjusted(tol, tol, -tol, -tol)
                    if not inner.isValid() or not inner.contains(pf):
                        return idx
            elif typ == "number":
                center = self._annotation_point(cmd.get("pos"), rect)
                radius = float(max(13, int(min_side * 0.025)) + 8)
                if math.hypot(float(pf.x() - center.x()), float(pf.y() - center.y())) <= radius:
                    return idx
            elif typ == "text":
                anchor = self._annotation_point(cmd.get("pos"), rect)
                font = QFont()
                font.setPixelSize(max(18, int(min_side * 0.035)))
                fm = QFontMetrics(font)
                text = str(cmd.get("text", ""))
                tw = max(24, int(fm.horizontalAdvance(text)))
                th = max(20, int(fm.height()))
                hit_rect = QRectF(float(anchor.x()) - 5.0, float(anchor.y()) - 6.0, float(tw) + 10.0, float(th) + 12.0)
                if hit_rect.contains(pf):
                    return idx
        return None

    def _annotation_point(self, pt: object, rect: QRect) -> QPointF:
        try:
            x, y = pt  # type: ignore[misc]
            return QPointF(float(rect.left()) + float(x) * float(rect.width()), float(rect.top()) + float(y) * float(rect.height()))
        except Exception:
            return QPointF(float(rect.left()), float(rect.top()))

    def _annotation_text_font(self, rect: QRect) -> QFont:
        font = QFont()
        font.setPixelSize(max(18, int(min(rect.width(), rect.height()) * 0.035)))
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
            font = self._annotation_text_font(self._annotation_rect())
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

    def _blur_radius_for_size(self, w: int, h: int) -> int:
        return max(14, int(min(int(w), int(h)) * 0.035))

    def _mosaic_block_for_size(self, w: int, h: int) -> int:
        return max(12, min(32, int(min(int(w), int(h)) * 0.026)))

    def _make_mosaic_bgr(self, image_bgr):
        import cv2

        h, w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
        block = self._mosaic_block_for_size(w, h)
        sw = max(1, int(round(float(w) / float(block))))
        sh = max(1, int(round(float(h) / float(block))))
        small = cv2.resize(image_bgr, (sw, sh), interpolation=cv2.INTER_AREA)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)

    def _blurred_bgr_for_base(self, base_bgr):
        try:
            if self._annotation_blurred_bgr is not None and tuple(self._annotation_blurred_bgr.shape[:2]) == tuple(base_bgr.shape[:2]):
                return self._annotation_blurred_bgr
            self._annotation_blurred_bgr = self._make_mosaic_bgr(base_bgr)
            self._annotation_blurred_image = self._bgr_to_qimage(self._annotation_blurred_bgr)
            return self._annotation_blurred_bgr
        except Exception:
            return base_bgr.copy()

    def _blur_preview_image(self) -> Optional[QImage]:
        if self._annotation_blurred_image is not None:
            return self._annotation_blurred_image
        if self._annotation_image_bgr is None:
            return None
        try:
            blurred = self._blurred_bgr_for_base(self._annotation_image_bgr)
            self._annotation_blurred_image = self._bgr_to_qimage(blurred)
            return self._annotation_blurred_image
        except Exception:
            return None

    def _blur_patch_image(self) -> Optional[QImage]:
        if self._annotation_image_bgr is None:
            return None
        blur_cmds = self._blur_commands(include_temp=True)
        if not blur_cmds:
            return None
        try:
            import numpy as np

            base_bgr = self._annotation_image_bgr
            h, w = int(base_bgr.shape[0]), int(base_bgr.shape[1])
            if w <= 0 or h <= 0:
                return None
            mask = self._blur_mask_for_commands(w, h, blur_cmds)
            if not bool(mask.any()):
                return None
            rendered_bgr = self._apply_blur_commands_to_bgr(base_bgr, blur_cmds)
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            rgba[:, :, 0:3] = rendered_bgr[:, :, ::-1]
            rgba[:, :, 3] = np.where(mask > 12, 255, 0).astype(np.uint8)
            return QImage(rgba.data, w, h, int(rgba.strides[0]), QImage.Format.Format_RGBA8888).copy()
        except Exception:
            return None

    def _draw_annotation_blurs(self, painter: QPainter, rect: QRect) -> None:
        blur_cmds = self._blur_commands(include_temp=True)
        if not blur_cmds:
            return
        img = self._blur_patch_image()
        if img is not None:
            painter.save()
            painter.setClipRect(rect)
            painter.drawImage(rect, img)
            painter.restore()
            return
        for cmd in blur_cmds:
            self._draw_annotation_blur(painter, cmd.get("points"), rect)

    def _draw_annotation_blur(self, painter: QPainter, points: object, rect: QRect) -> None:
        pts = [self._annotation_point(p, rect) for p in list(points or [])]  # type: ignore[arg-type]
        if not pts:
            return
        img = self._blur_preview_image()
        radius = self._blur_radius_for_size(int(rect.width()), int(rect.height()))
        if img is None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 80))
            for p in pts:
                painter.drawEllipse(p, float(radius), float(radius))
            return
        path = QPainterPath()
        step = max(1, len(pts) // 700)
        sampled = pts[::step]
        if sampled:
            line_path = QPainterPath(sampled[0])
            for p in sampled[1:]:
                line_path.lineTo(p)
            stroker = QPainterPathStroker()
            stroker.setWidth(float(radius * 2))
            stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
            stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            path.addPath(stroker.createStroke(line_path))
        for p in sampled:
            path.addEllipse(QRectF(float(p.x() - radius), float(p.y() - radius), float(radius * 2), float(radius * 2)))
        painter.save()
        painter.setClipRect(rect)
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        painter.drawImage(rect, img)
        painter.restore()

    def _draw_annotation_polyline(self, painter: QPainter, points: object, rect: QRect, color: QColor, width: int) -> None:
        pts = [self._annotation_point(p, rect) for p in list(points or [])]  # type: ignore[arg-type]
        if len(pts) < 2:
            return
        pen = QPen(color)
        pen.setWidthF(float(width))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for i in range(1, len(pts)):
            if abs(float(pts[i].x() - pts[i - 1].x())) < 0.01 and abs(float(pts[i].y() - pts[i - 1].y())) < 0.01:
                continue
            painter.drawLine(pts[i - 1], pts[i])

    def _draw_annotation_arrow(self, painter: QPainter, cmd: dict[str, object], rect: QRect) -> None:
        start = cmd.get("start")
        end = cmd.get("end")
        p1 = self._annotation_point(start, rect)
        p2 = self._annotation_point(end, rect)
        color = self._color_from_command(cmd, "arrow_color", "#E53935")
        pen = QPen(color)
        pen.setWidthF(float(max(3, int(min(rect.width(), rect.height()) * 0.006))))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(p1, p2)
        dx = float(p2.x() - p1.x())
        dy = float(p2.y() - p1.y())
        dist = math.hypot(dx, dy)
        if dist < 1.0:
            return
        angle = math.atan2(dy, dx)
        head = max(14.0, float(min(rect.width(), rect.height())) * 0.035)
        spread = math.radians(28.0)
        pts = [
            p2,
            QPointF(float(p2.x() - head * math.cos(angle - spread)), float(p2.y() - head * math.sin(angle - spread))),
            QPointF(float(p2.x() - head * math.cos(angle + spread)), float(p2.y() - head * math.sin(angle + spread))),
        ]
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF(pts))

    def _draw_annotation_rect(self, painter: QPainter, cmd: dict[str, object], rect: QRect) -> None:
        p1 = self._annotation_point(cmd.get("start"), rect)
        p2 = self._annotation_point(cmd.get("end"), rect)
        rr = QRectF(p1, p2).normalized()
        if rr.width() < 2.0 or rr.height() < 2.0:
            return
        pen = QPen(self._color_from_command(cmd, "rect_color", "#E53935"))
        pen.setWidthF(float(max(2, int(min(rect.width(), rect.height()) * 0.0045))))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self._apply_pen_line_style(pen, cmd.get("line_style"))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rr)

    def _draw_annotation_number(self, painter: QPainter, cmd: dict[str, object], rect: QRect) -> None:
        pos = cmd.get("pos")
        n = cmd.get("n")
        p = self._annotation_point(pos, rect)
        r = max(13, int(min(rect.width(), rect.height()) * 0.025))
        rr = QRect(int(p.x() - r), int(p.y() - r), int(r * 2), int(r * 2))
        pen = QPen(QColor("#FFFFFF"))
        pen.setWidthF(float(max(2, int(r * 0.12))))
        painter.setPen(pen)
        painter.setBrush(QBrush(self._color_from_command(cmd, "number_color", "#E53935")))
        painter.drawEllipse(rr)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(13, int(r * 1.05)))
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(rr, int(Qt.AlignmentFlag.AlignCenter), str(n))

    def _draw_annotation_text(self, painter: QPainter, cmd: dict[str, object], rect: QRect) -> None:
        pos = cmd.get("pos")
        text = cmd.get("text")
        p = self._annotation_point(pos, rect)
        font = self._annotation_text_font(rect)
        painter.setFont(font)
        painter.setPen(QPen(self._color_from_command(cmd, "text_color", "#E53935")))
        fm = QFontMetrics(font)
        painter.drawText(QPointF(float(p.x()), float(p.y()) + float(fm.ascent())), str(text))

    def _draw_annotation_command(self, painter: QPainter, cmd: dict[str, object], rect: QRect) -> None:
        typ = str(cmd.get("type", ""))
        if typ == "arrow":
            self._draw_annotation_arrow(painter, cmd, rect)
        elif typ == "rect":
            self._draw_annotation_rect(painter, cmd, rect)
        elif typ == "blur":
            self._draw_annotation_blur(painter, cmd.get("points"), rect)
        elif typ == "pen":
            self._draw_annotation_polyline(painter, cmd.get("points"), rect, self._color_from_command(cmd, "pen_color", "#E53935"), max(3, int(min(rect.width(), rect.height()) * 0.005)))
        elif typ == "marker":
            self._draw_annotation_polyline(painter, cmd.get("points"), rect, self._color_from_command(cmd, "marker_color", "#FFD600", 120), max(12, int(min(rect.width(), rect.height()) * 0.025)))
        elif typ == "number":
            self._draw_annotation_number(painter, cmd, rect)
        elif typ == "text":
            self._draw_annotation_text(painter, cmd, rect)

    def _eraser_radius_px(self, rect: QRect) -> float:
        return float(max(14, int(min(int(rect.width()), int(rect.height())) * 0.028)))

    def _erase_path_command(self, cmd: dict[str, object], p: QPoint, rect: QRect, radius: float) -> Optional[list[dict[str, object]]]:
        pts_raw = list(cmd.get("points") or [])
        if len(pts_raw) < 2:
            return []
        pts_norm: list[tuple[float, float]] = []
        pts_px: list[QPointF] = []
        for item in pts_raw:
            try:
                norm = (float(item[0]), float(item[1]))  # type: ignore[index]
            except Exception:
                continue
            pts_norm.append(norm)
            pts_px.append(self._annotation_point(norm, rect))
        if len(pts_norm) < 2:
            return []

        eraser = QPointF(float(p.x()), float(p.y()))
        keep: list[bool] = []
        touched = False
        for pt in pts_px:
            ok = math.hypot(float(pt.x() - eraser.x()), float(pt.y() - eraser.y())) > float(radius)
            keep.append(ok)
            touched = bool(touched or not ok)
        if not bool(touched):
            return None

        segments: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []
        for ok, norm in zip(keep, pts_norm):
            if bool(ok):
                current.append(norm)
            else:
                if len(current) >= 2:
                    segments.append(current)
                current = []
        if len(current) >= 2:
            segments.append(current)

        out: list[dict[str, object]] = []
        for segment in segments:
            next_cmd = self._copy_annotation_command(cmd)
            next_cmd["points"] = segment
            out.append(next_cmd)
        return out

    def _erase_annotation_at(self, p: QPoint) -> bool:
        idx = self._annotation_hit_index(p)
        if idx is None or idx < 0 or idx >= len(self._annotation_commands):
            return False
        rect = self._annotation_rect()
        cmd = self._annotation_commands[int(idx)]
        typ = str(cmd.get("type", ""))
        if typ in {"pen", "marker", "blur"}:
            replacement = self._erase_path_command(cmd, p, rect, self._eraser_radius_px(rect))
            if replacement is None:
                return False
            self._annotation_commands[int(idx) : int(idx) + 1] = replacement
        else:
            self._annotation_commands.pop(int(idx))
        self._annotation_redo.clear()
        self._recount_numbers()
        self.update()
        self.annotation_changed.emit()
        self._emit_annotation_history()
        return True

    def _annotation_mouse_press(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self._commit_text_editor()
        p = event.position().toPoint()
        if not self._annotation_rect().contains(p):
            return False
        if self._annotation_mode == "eraser":
            self._annotation_drawing = True
            self._annotation_temp = {"type": "eraser"}
            self._erase_annotation_at(p)
            event.accept()
            return True
        hit_idx = self._annotation_hit_index(p)
        if hit_idx is not None:
            self._annotation_drag_index = int(hit_idx)
            self._annotation_drag_start = self._norm_annotation_point(p)
            self._annotation_drag_original = self._copy_annotation_command(self._annotation_commands[int(hit_idx)])
            self._annotation_drawing = False
            self._annotation_temp = None
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            event.accept()
            return True
        if not bool(self._annotation_mode):
            return False
        if self._annotation_mode == "number":
            self._annotation_commands.append({
                "type": "number",
                "pos": self._norm_annotation_point(p),
                "n": int(self._annotation_next_number),
                "color": self._style_hex("number_color"),
            })
            self._annotation_next_number += 1
            self._annotation_redo.clear()
            self.update()
            self.annotation_changed.emit()
            self._emit_annotation_history()
            event.accept()
            return True
        if self._annotation_mode == "text":
            self._begin_text_editor(p)
            event.accept()
            return True
        self._annotation_drawing = True
        p0 = self._norm_annotation_point(p)
        if self._annotation_mode == "arrow":
            self._annotation_temp = {"type": "arrow", "start": p0, "end": p0, "color": self._style_hex("arrow_color")}
        elif self._annotation_mode == "rect":
            self._annotation_temp = {
                "type": "rect",
                "start": p0,
                "end": p0,
                "color": self._style_hex("rect_color"),
                "line_style": self._line_style(),
            }
        elif self._annotation_mode in {"pen", "marker", "blur"}:
            color_key = "pen_color" if self._annotation_mode == "pen" else "marker_color"
            cmd = {"type": self._annotation_mode, "points": [p0]}
            if self._annotation_mode in {"pen", "marker"}:
                cmd["color"] = self._style_hex(color_key)
                if self._annotation_mode == "marker":
                    cmd["alpha"] = 120
            self._annotation_temp = cmd
        self.update()
        event.accept()
        return True

    def _annotation_mouse_move(self, event) -> bool:
        if self._annotation_drag_index is not None:
            if self._annotation_drag_start is None or self._annotation_drag_original is None:
                return False
            p = self._norm_annotation_point(event.position().toPoint())
            dx = float(p[0]) - float(self._annotation_drag_start[0])
            dy = float(p[1]) - float(self._annotation_drag_start[1])
            idx = int(self._annotation_drag_index)
            if idx >= 0 and idx < len(self._annotation_commands):
                self._annotation_commands[idx] = self._offset_annotation_command(self._annotation_drag_original, dx, dy)
                self.update()
            event.accept()
            return True
        if not bool(self._annotation_drawing):
            p0 = event.position().toPoint()
            if self._annotation_mode != "eraser" and self._annotation_rect().contains(p0) and self._annotation_hit_index(p0) is not None:
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            elif bool(self._annotation_mode):
                cursor = self._tool_cursors.get(str(self._annotation_mode))
                if cursor is not None:
                    self.setCursor(cursor)
                elif self._annotation_mode == "text":
                    self.setCursor(Qt.CursorShape.IBeamCursor)
        if not bool(self._annotation_drawing) or self._annotation_temp is None:
            return False
        p = self._norm_annotation_point(event.position().toPoint())
        if self._annotation_temp.get("type") == "eraser":
            self._erase_annotation_at(event.position().toPoint())
        elif self._annotation_temp.get("type") in {"arrow", "rect"}:
            self._annotation_temp["end"] = p
        else:
            pts = self._annotation_temp.get("points")
            if isinstance(pts, list):
                self._append_annotation_path_point(pts, p, str(self._annotation_temp.get("type", "")))
        self.update()
        event.accept()
        return True

    def _annotation_mouse_release(self, event) -> bool:
        if self._annotation_drag_index is not None:
            self._annotation_drag_index = None
            self._annotation_drag_start = None
            self._annotation_drag_original = None
            if bool(self._annotation_mode):
                cursor = self._tool_cursors.get(str(self._annotation_mode))
                if cursor is not None:
                    self.setCursor(cursor)
            else:
                self.unsetCursor()
            self._annotation_redo.clear()
            self.annotation_changed.emit()
            self._emit_annotation_history()
            event.accept()
            return True
        if event.button() != Qt.MouseButton.LeftButton or self._annotation_temp is None:
            return False
        if self._annotation_temp.get("type") == "eraser":
            self._erase_annotation_at(event.position().toPoint())
            self._annotation_drawing = False
            self._annotation_temp = None
            self.update()
            event.accept()
            return True
        if bool(self._annotation_drawing) and self._annotation_temp.get("type") in {"pen", "marker", "blur"}:
            pts0 = self._annotation_temp.get("points")
            if isinstance(pts0, list):
                self._append_annotation_path_point(pts0, self._norm_annotation_point(event.position().toPoint()), str(self._annotation_temp.get("type", "")))
        cmd = dict(self._annotation_temp)
        self._annotation_drawing = False
        self._annotation_temp = None
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
            self._annotation_commands.append(cmd)
            self._annotation_redo.clear()
            self.annotation_changed.emit()
            self._emit_annotation_history()
        self.update()
        event.accept()
        return True

    def _begin_text_editor(self, p: QPoint) -> None:
        self._commit_text_editor()
        color = self._style_hex("text_color")
        font = self._annotation_text_font(self._annotation_rect())
        metrics = QFontMetrics(font)
        editor_height = max(34, int(metrics.height()) + 12)
        self._text_editor_anchor = self._norm_annotation_point(p)
        editor = QLineEdit(self)
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        editor.setFont(font)
        editor.setStyleSheet(
            f"QLineEdit {{ background: rgba(255,255,255,0.98); border: 1px solid {color};"
            f" border-radius: 4px; padding: 4px 6px; font-size: {font.pixelSize()}px; color: {color}; }}"
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
                pos = self._norm_annotation_point(self._text_editor_text_top_left(editor, text))
            elif self._text_editor_anchor is not None:
                pos = self._text_editor_anchor
            else:
                pos = self._norm_annotation_point(editor.pos())
            editor.close()
        except Exception:
            text = ""
            pos = (0.0, 0.0)
        self._text_editor_anchor = None
        if not text:
            return
        self._annotation_commands.append({"type": "text", "pos": pos, "text": text, "color": self._style_hex("text_color")})
        self._annotation_redo.clear()
        self.update()
        self.annotation_changed.emit()
        self._emit_annotation_history()

    def set_interaction_enabled(self, enabled: bool) -> None:
        self._interaction_enabled = bool(enabled)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            not (bool(self._interactive) and bool(self._interaction_enabled)),
        )
        self.setMouseTracking(bool(self._interactive) and bool(self._interaction_enabled))
        if not bool(self._interaction_enabled):
            self._drag_handle = None
            self._drag_start_global = None
            self._drag_start_geo = None
            self.unsetCursor()
        self.update()

    def interaction_enabled(self) -> bool:
        return bool(self._interactive) and bool(self._interaction_enabled)

    def _build_tool_cursors(self) -> dict[str, QCursor]:
        from deepcat.ui.post_capture_actions import AnnotationCanvasOverlay

        def cursor_from(draw, hot_x: int = 12, hot_y: int = 12, size: int = 32) -> QCursor:
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            draw(p, size)
            p.end()
            return QCursor(pm, int(hot_x), int(hot_y))

        def draw_blur(p: QPainter, s: int) -> None:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            pen = QPen(QColor(0, 0, 0, 230))
            pen.setWidth(1)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(1, 1, s - 3, s - 3)

        return {
            "arrow": AnnotationCanvasOverlay._build_tool_cursor("arrow"),
            "pen": AnnotationCanvasOverlay._build_tool_cursor("pen"),
            "marker": AnnotationCanvasOverlay._build_tool_cursor("marker"),
            "number": AnnotationCanvasOverlay._build_tool_cursor("number"),
            "text": QCursor(Qt.CursorShape.IBeamCursor),
            "blur": cursor_from(draw_blur, 14, 14, 28),
            "rect": AnnotationCanvasOverlay._build_tool_cursor("rect"),
            "eraser": AnnotationCanvasOverlay._build_tool_cursor("eraser"),
        }

    def _build_cursors(self) -> dict[str, QCursor]:
        def make_cursor(kind: str) -> QCursor:
            s = 24
            pm = QPixmap(s, s)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(QColor(20, 20, 20, 230))
            pen.setWidth(1)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            c = s // 2
            if kind == "ver":
                p.drawLine(c, 4, c, s - 5)
                p.setBrush(QColor(20, 20, 20, 230))
                p.drawPolygon([QPoint(c, 2), QPoint(c - 3, 6), QPoint(c + 3, 6)])
                p.drawPolygon([QPoint(c, s - 3), QPoint(c - 3, s - 7), QPoint(c + 3, s - 7)])
                p.setBrush(Qt.BrushStyle.NoBrush)
            elif kind == "hor":
                p.drawLine(4, c, s - 5, c)
                p.setBrush(QColor(20, 20, 20, 230))
                p.drawPolygon([QPoint(2, c), QPoint(6, c - 3), QPoint(6, c + 3)])
                p.drawPolygon([QPoint(s - 3, c), QPoint(s - 7, c - 3), QPoint(s - 7, c + 3)])
                p.setBrush(Qt.BrushStyle.NoBrush)
            elif kind == "fdiag":
                p.drawLine(5, 5, s - 6, s - 6)
                p.drawLine(4, 4, 9, 5)
                p.drawLine(4, 4, 5, 9)
                p.drawLine(9, 5, 5, 9)
                p.drawLine(s - 5, s - 5, s - 10, s - 6)
                p.drawLine(s - 5, s - 5, s - 6, s - 10)
                p.drawLine(s - 10, s - 6, s - 6, s - 10)
            else:
                p.drawLine(s - 6, 5, 5, s - 6)
                p.drawLine(s - 5, 4, s - 10, 5)
                p.drawLine(s - 5, 4, s - 6, 9)
                p.drawLine(s - 10, 5, s - 6, 9)
                p.drawLine(4, s - 5, 9, s - 6)
                p.drawLine(4, s - 5, 5, s - 10)
                p.drawLine(9, s - 6, 5, s - 10)
            p.end()
            return QCursor(pm, c, c)
        return {
            "ver": make_cursor("ver"),
            "hor": make_cursor("hor"),
            "fdiag": make_cursor("fdiag"),
            "bdiag": make_cursor("bdiag"),
        }

    def _sync_widget_geometry(self) -> None:
        top_reserve = self._dimension_tip_top_reserve()
        side_reserve = self._dimension_tip_side_reserve()
        self._geometry_top_reserve = int(top_reserve)
        self._geometry_side_reserve = int(side_reserve)
        pad = int(self._margin if bool(self._interactive) else self._BORDER_PAD)
        x = int(self._selection_rect.left()) - pad - int(side_reserve)
        y = int(self._selection_rect.top()) - pad - int(top_reserve)
        w = int(self._selection_rect.width()) + (pad + int(side_reserve)) * 2
        h = int(self._selection_rect.height()) + pad * 2 + int(top_reserve)
        super().setGeometry(int(x), int(y), int(max(1, w)), int(max(1, h)))

    def set_selection_rect(self, rect: QRect, emit_change: bool = False) -> None:
        self._selection_rect = QRect(rect)
        self._sync_widget_geometry()
        self.update()
        if bool(emit_change):
            self._schedule_rect_changed()

    def selection_rect(self) -> QRect:
        return QRect(self._selection_rect)

    def set_dimension_tip_inside(self, inside: bool) -> None:
        next_inside = bool(inside)
        if bool(self._dimension_tip_inside) == next_inside:
            return
        self._dimension_tip_inside = next_inside
        self._sync_widget_geometry()
        self.update()

    def _edge_rect(self) -> QRect:
        top_reserve = int(getattr(self, "_geometry_top_reserve", 0))
        side_reserve = int(getattr(self, "_geometry_side_reserve", 0))
        if not bool(self._interactive):
            pad = int(self._BORDER_PAD)
            return QRect(pad + side_reserve, pad + top_reserve, int(self._selection_rect.width()), int(self._selection_rect.height()))
        return QRect(int(self._margin + side_reserve), int(self._margin + top_reserve), int(self._selection_rect.width()), int(self._selection_rect.height()))

    def _dimension_tip_full_height(self) -> int:
        return int(self._DIMENSION_TIP_HEIGHT + self._DIMENSION_TIP_GAP + 2)

    def _dimension_tip_text(self) -> str:
        return f"宽高：{int(round(self._selection_rect.width()))} x {int(round(self._selection_rect.height()))}"

    def _dimension_tip_estimated_width(self) -> int:
        text = self._dimension_tip_text()
        return max(int(self._DIMENSION_TIP_MIN_WIDTH), int(len(text) * 8) + 22)

    def _dimension_tip_side_reserve(self) -> int:
        extra = int(self._dimension_tip_estimated_width()) - int(self._selection_rect.width())
        return max(0, int(math.ceil(max(0, extra) / 2)) + 2)

    def _dimension_tip_top_reserve(self) -> int:
        if bool(self._dimension_tip_inside):
            return 0
        reserve = self._dimension_tip_full_height()
        try:
            screen = QGuiApplication.screenAt(self._selection_rect.center()) or QGuiApplication.primaryScreen()
            geo = screen.geometry() if screen is not None else QRect(0, 0, 800, 600)
            if int(self._selection_rect.top()) - reserve >= int(geo.y()) + 2:
                return reserve
        except Exception:
            if int(self._selection_rect.top()) >= reserve + 2:
                return reserve
        return 0

    def _dimension_tip_rect(self, anchor_rect: QRectF, text: str, painter: QPainter) -> QRectF:
        font = painter.font()
        font.setPointSize(9)
        font.setBold(False)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tip_w = max(float(self._DIMENSION_TIP_MIN_WIDTH), float(fm.horizontalAdvance(text)) + 22.0)
        tip_h = max(float(self._DIMENSION_TIP_HEIGHT), float(fm.height()) + 10.0)
        x = float(anchor_rect.left() + (anchor_rect.width() - tip_w) / 2.0)
        x = max(2.0, min(x, max(2.0, float(self.width()) - tip_w - 2.0)))
        if bool(self._dimension_tip_inside):
            y = float(anchor_rect.top()) + float(self._DIMENSION_TIP_GAP)
        else:
            above_y = float(anchor_rect.top()) - tip_h - float(self._DIMENSION_TIP_GAP)
            y = above_y if above_y >= 2.0 else float(anchor_rect.top()) + float(self._DIMENSION_TIP_GAP)
        y = max(2.0, min(y, max(2.0, float(self.height()) - tip_h - 2.0)))
        return QRectF(x, y, tip_w, tip_h)

    def _draw_dimension_tip(self, painter: QPainter, anchor_rect: QRectF, text: str) -> None:
        tip_rect = self._dimension_tip_rect(anchor_rect, text, painter)
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 23, 42, 28))
            painter.drawRoundedRect(tip_rect.translated(0.0, 1.0), 8.0, 8.0)
            painter.setBrush(QColor(255, 255, 255, 246))
            painter.drawRoundedRect(tip_rect, 8.0, 8.0)
            painter.setPen(QPen(QColor(203, 213, 225, 230), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(tip_rect.adjusted(0.5, 0.5, -0.5, -0.5), 8.0, 8.0)
            painter.setPen(QColor(51, 65, 85))
            painter.drawText(tip_rect.adjusted(10, 0, -10, 0), int(Qt.AlignmentFlag.AlignCenter), text)
        finally:
            painter.restore()

    def _hit_handle(self, pos) -> Optional[str]:
        r = self._edge_rect()
        x = int(pos.x())
        y = int(pos.y())
        g = int(self._edge_grip)

        x_left = int(r.left())
        x_right = int(r.right())
        x_center = x_left + int(r.width() // 2)

        y_top = int(r.top())
        y_bottom = int(r.bottom())
        y_center = y_top + int(r.height() // 2)

        points = {
            "top_left": (x_left, y_top),
            "top": (x_center, y_top),
            "top_right": (x_right, y_top),
            "right": (x_right, y_center),
            "bottom_right": (x_right, y_bottom),
            "bottom": (x_center, y_bottom),
            "bottom_left": (x_left, y_bottom),
            "left": (x_left, y_center),
        }
        for name, (px, py) in points.items():
            if abs(x - int(px)) <= self._dot_hit and abs(y - int(py)) <= self._dot_hit:
                return name
        on_l = abs(x - int(r.left())) <= g
        on_r = abs(x - int(r.right())) <= g
        on_t = abs(y - int(r.top())) <= g
        on_b = abs(y - int(r.bottom())) <= g
        if on_l and on_t:
            return "top_left"
        if on_r and on_t:
            return "top_right"
        if on_l and on_b:
            return "bottom_left"
        if on_r and on_b:
            return "bottom_right"
        if on_t and x >= int(r.left()) - g and x <= int(r.right()) + g:
            return "top"
        if on_b and x >= int(r.left()) - g and x <= int(r.right()) + g:
            return "bottom"
        if on_l and y >= int(r.top()) - g and y <= int(r.bottom()) + g:
            return "left"
        if on_r and y >= int(r.top()) - g and y <= int(r.bottom()) + g:
            return "right"
        return None

    def _emit_rect(self) -> None:
        self.rect_changed.emit(QRect(self._selection_rect))

    def _schedule_rect_changed(self) -> None:
        if self._drag_handle is None:
            self._emit_rect()
            return
        self._pending_rect_changed = True
        try:
            if not self._rect_changed_timer.isActive():
                self._rect_changed_timer.start()
        except Exception:
            self._flush_pending_rect_changed()

    def _flush_pending_rect_changed(self) -> None:
        if not bool(getattr(self, "_pending_rect_changed", False)):
            return
        self._pending_rect_changed = False
        try:
            if self._rect_changed_timer.isActive():
                self._rect_changed_timer.stop()
        except Exception:
            pass
        self._emit_rect()

    def mousePressEvent(self, event) -> None:
        if self._annotation_mouse_press(event):
            return
        if bool(self._annotation_mode):
            event.accept()
            return
        if not bool(self.interaction_enabled()):
            return super().mousePressEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        p = event.position().toPoint()
        h = self._hit_handle(p)
        if h is None and self._edge_rect().contains(p):
            h = "move"
        if h is None:
            return
        self._drag_handle = str(h)
        self._drag_start_global = event.globalPosition().toPoint()
        self._drag_start_geo = QRect(self._selection_rect)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._annotation_mouse_move(event):
            return
        if bool(self._annotation_mode):
            event.accept()
            return
        if not bool(self.interaction_enabled()):
            return super().mouseMoveEvent(event)
        if self._drag_handle is None:
            p = event.position().toPoint()
            if self._annotation_hit_index(p) is not None:
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
                return
            h = self._hit_handle(p)
            if h is None and self._edge_rect().contains(p):
                h = "move"
            if h in {"top", "bottom"}:
                self.setCursor(self._cursors["ver"])
            elif h in {"left", "right"}:
                self.setCursor(self._cursors["hor"])
            elif h in {"top_left", "bottom_right"}:
                self.setCursor(self._cursors["fdiag"])
            elif h in {"top_right", "bottom_left"}:
                self.setCursor(self._cursors["bdiag"])
            elif h == "move":
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            else:
                self.unsetCursor()
            return
        if self._drag_start_global is None or self._drag_start_geo is None:
            return
        g0 = QRect(self._drag_start_geo)
        delta = event.globalPosition().toPoint() - self._drag_start_global
        x, y, w, h = int(g0.x()), int(g0.y()), int(g0.width()), int(g0.height())
        if self._drag_handle == "top":
            y2 = int(y + delta.y())
            y2 = min(y + h - self._min_h, y2)
            h = int(h + (y - y2))
            y = int(y2)
        elif self._drag_handle == "bottom":
            h = int(max(self._min_h, h + delta.y()))
        elif self._drag_handle == "left":
            x2 = int(x + delta.x())
            x2 = min(x + w - self._min_w, x2)
            w = int(w + (x - x2))
            x = int(x2)
        elif self._drag_handle == "right":
            w = int(max(self._min_w, w + delta.x()))
        elif self._drag_handle == "top_left":
            x2 = int(x + delta.x())
            y2 = int(y + delta.y())
            x2 = min(x + w - self._min_w, x2)
            y2 = min(y + h - self._min_h, y2)
            w = int(w + (x - x2))
            h = int(h + (y - y2))
            x = int(x2)
            y = int(y2)
        elif self._drag_handle == "top_right":
            y2 = int(y + delta.y())
            y2 = min(y + h - self._min_h, y2)
            h = int(h + (y - y2))
            y = int(y2)
            w = int(max(self._min_w, w + delta.x()))
        elif self._drag_handle == "bottom_left":
            x2 = int(x + delta.x())
            x2 = min(x + w - self._min_w, x2)
            w = int(w + (x - x2))
            x = int(x2)
            h = int(max(self._min_h, h + delta.y()))
        elif self._drag_handle == "bottom_right":
            w = int(max(self._min_w, w + delta.x()))
            h = int(max(self._min_h, h + delta.y()))
        elif self._drag_handle == "move":
            x = int(x + delta.x())
            y = int(y + delta.y())
        self.set_selection_rect(QRect(int(x), int(y), int(w), int(h)), emit_change=True)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._annotation_mouse_release(event):
            return
        if bool(self._annotation_mode):
            event.accept()
            return
        if not bool(self.interaction_enabled()):
            return super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            had_drag = self._drag_handle is not None
            if had_drag:
                self._flush_pending_rect_changed()
                self.rect_released.emit(QRect(self._selection_rect))
            self._drag_handle = None
            self._drag_start_global = None
            self._drag_start_geo = None
            event.accept()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            er = self._edge_rect()

            dpr = float(self.devicePixelRatioF())
            logical_left = float(self._selection_rect.x())
            logical_top = float(self._selection_rect.y())
            logical_width = float(self._selection_rect.width())
            logical_height = float(self._selection_rect.height())

            left_px = int(math.floor(logical_left * dpr))
            top_px = int(math.floor(logical_top * dpr))
            right_px = int(math.ceil((logical_left + logical_width) * dpr))
            bottom_px = int(math.ceil((logical_top + logical_height) * dpr))
            width_px = int(max(1, right_px - left_px))
            height_px = int(max(1, bottom_px - top_px))

            win_pos = self.geometry().topLeft()
            win_x_px = int(round(float(win_pos.x()) * dpr))
            win_y_px = int(round(float(win_pos.y()) * dpr))

            target_x_px = left_px - win_x_px
            target_y_px = top_px - win_y_px

            dx = (float(target_x_px) - float(er.x()) * dpr) / dpr
            dy = (float(target_y_px) - float(er.y()) * dpr) / dpr
            dw = (float(width_px) - float(er.width()) * dpr) / dpr
            dh = (float(height_px) - float(er.height()) * dpr) / dpr

            er_aligned = QRectF(float(er.x()) + dx, float(er.y()) + dy, float(er.width()) + dw, float(er.height()) + dh)

            if bool(self._interactive):
                painter.fillRect(er_aligned, QColor(0, 0, 0, 1))

            border_color = QColor(self._style_hex("selection_border_color", "#FF0000"))
            pen = QPen(border_color)
            pen.setWidth(3)
            pen.setCosmetic(True)
            self._apply_pen_line_style(pen)
            painter.setPen(pen)

            # 用 QRectF 明确包含边界，避免 QRect.right()/bottom() 的包含式坐标造成 1px 视觉偏差。
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            border_rect = QRectF(
                float(er_aligned.x()),
                float(er_aligned.y()),
                float(max(1.0, er_aligned.width() - 1.0)),
                float(max(1.0, er_aligned.height() - 1.0)),
            )
            painter.drawRect(border_rect)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            ar_aligned = QRectF(er_aligned).adjusted(1.0, 1.0, -1.0, -1.0)
            if self._drag_handle is None:
                painter.save()
                try:
                    painter.setClipRect(ar_aligned)
                    self._draw_annotation_blurs(painter, ar_aligned)
                    for cmd in self._annotation_commands:
                        if cmd.get("type") != "blur":
                            self._draw_annotation_command(painter, cmd, ar_aligned)
                    if self._annotation_temp is not None and self._annotation_temp.get("type") != "blur":
                        self._draw_annotation_command(painter, self._annotation_temp, ar_aligned)
                finally:
                    painter.restore()

            text = self._dimension_tip_text()
            self._draw_dimension_tip(painter, er_aligned, text)

            if bool(self._interactive) and bool(self._interaction_enabled):
                painter.setPen(Qt.PenStyle.NoPen)

                x_left = float(er_aligned.left())
                x_right = float(er_aligned.right())
                x_center = x_left + float(er_aligned.width() / 2.0)

                y_top = float(er_aligned.top())
                y_bottom = float(er_aligned.bottom())
                y_center = y_top + float(er_aligned.height() / 2.0)

                pts = [
                    (x_left, y_top),
                    (x_center, y_top),
                    (x_right, y_top),
                    (x_right, y_center),
                    (x_right, y_bottom),
                    (x_center, y_bottom),
                    (x_left, y_bottom),
                    (x_left, y_center),
                ]

                d = int(self._dot_r * 2)
                for px, py in pts:
                    painter.setBrush(QColor(255, 255, 255, 230))
                    painter.drawEllipse(QRectF(px - float(self._dot_r) - 1.0, py - float(self._dot_r) - 1.0, float(d + 2), float(d + 2)))
                    painter.setBrush(border_color)
                    painter.drawEllipse(QRectF(px - float(self._dot_r), py - float(self._dot_r), float(d), float(d)))
        finally:
            if painter.isActive():
                painter.end()
