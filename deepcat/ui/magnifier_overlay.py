from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget


class MagnifierOverlay(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._embedded = parent is not None
        if not bool(self._embedded):
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._size = 112
        self.setFixedSize(int(self._size), int(self._size))
        self._zoom = 4.0
        self._img: QImage | None = None
        self._source_img: QImage | None = None
        self._source_left_px = 0
        self._source_top_px = 0
        self._source_dpr = 1.0
        self._pos = QCursor.pos()
        self._color_text = "#------"
        self.update_at(self._pos)

    def set_source_image(self, image: QImage | None, left_px: int = 0, top_px: int = 0, dpr: float = 1.0) -> None:
        self._source_img = image if image is not None and not image.isNull() else None
        self._source_left_px = int(left_px)
        self._source_top_px = int(top_px)
        self._source_dpr = float(max(0.01, dpr))
        self.update_at(self._pos)

    def set_zoom(self, zoom: float) -> None:
        z = float(zoom)
        z = max(2.0, min(8.0, z))
        self._zoom = z
        self.update_at(self._pos)

    def update_at(self, global_pos: QPoint) -> None:
        self._pos = QPoint(int(global_pos.x()), int(global_pos.y()))
        if self._update_from_source_image():
            self._reposition()
            self.update()
            return
        screen = QGuiApplication.screenAt(self._pos) or QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.geometry()
            crop = int(max(18, min(120, round(float(self._size) / float(self._zoom)))))
            crop = int(min(crop, max(1, int(geo.width())), max(1, int(geo.height()))))
            x = int(self._pos.x() - crop // 2)
            y = int(self._pos.y() - crop // 2)
            x = max(int(geo.x()), min(int(x), int(geo.x() + geo.width() - crop)))
            y = max(int(geo.y()), min(int(y), int(geo.y() + geo.height() - crop)))
            pm = screen.grabWindow(0, x, y, int(crop), int(crop))
            try:
                pm.setDevicePixelRatio(1.0)
            except Exception:
                pass
            self._img = pm.toImage()
            self._update_color_from_image(self._img, int(self._img.width() / 2), int(self._img.height() / 2))
        self._reposition()
        self.update()

    def _update_from_source_image(self) -> bool:
        src = self._source_img
        if src is None or src.isNull():
            return False
        cx = int(round(float(self._pos.x()) * self._source_dpr)) - int(self._source_left_px)
        cy = int(round(float(self._pos.y()) * self._source_dpr)) - int(self._source_top_px)
        if cx < 0 or cy < 0 or cx >= int(src.width()) or cy >= int(src.height()):
            return False
        crop = int(max(18, min(120, round(float(self._size) / float(self._zoom)))))
        crop_px = int(max(8, round(float(crop) * self._source_dpr)))
        crop_px = int(min(crop_px, max(1, int(src.width())), max(1, int(src.height()))))
        x = int(max(0, min(int(cx - crop_px // 2), int(src.width() - crop_px))))
        y = int(max(0, min(int(cy - crop_px // 2), int(src.height() - crop_px))))
        self._img = src.copy(x, y, crop_px, crop_px)
        self._update_color_from_image(src, cx, cy)
        return True

    def _update_color_from_image(self, img: QImage | None, x: int, y: int) -> None:
        if img is None or img.isNull() or x < 0 or y < 0 or x >= int(img.width()) or y >= int(img.height()):
            self._color_text = "#------"
            return
        try:
            c = img.pixelColor(int(x), int(y))
            self._color_text = f"#{c.red():02X}{c.green():02X}{c.blue():02X}"
        except Exception:
            self._color_text = "#------"

    def _reposition(self) -> None:
        screen = QGuiApplication.screenAt(self._pos) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 800, 600)
        offset = 18
        x = int(self._pos.x() + offset)
        y = int(self._pos.y() + offset)
        if x + self.width() > geo.x() + geo.width():
            x = int(self._pos.x() - self.width() - offset)
        if y + self.height() > geo.y() + geo.height():
            y = int(self._pos.y() - self.height() - offset)
        x = max(int(geo.x()), min(int(x), int(geo.x() + geo.width() - self.width())))
        y = max(int(geo.y()), min(int(y), int(geo.y() + geo.height() - self.height())))
        if bool(self._embedded) and self.parentWidget() is not None:
            local = self.parentWidget().mapFromGlobal(QPoint(int(x), int(y)))
            self.move(local)
        else:
            self.move(x, y)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        img_rect = self.rect()

        if self._img is not None and not self._img.isNull():
            scaled = self._img.scaled(
                img_rect.size(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            painter.drawImage(0, 0, scaled)

        label_h = 18
        painter.fillRect(QRect(0, 0, int(self._size), int(label_h)), QColor(0, 0, 0, 70))
        painter.fillRect(QRect(0, int(self._size) - int(label_h), int(self._size), int(label_h)), QColor(0, 0, 0, 70))

        center = int(self._size / 2)
        painter.setPen(QPen(QColor(255, 40, 40, 180), 1))
        painter.drawLine(center, 0, center, int(self._size))
        painter.drawLine(0, center, int(self._size), center)

        pen = QPen(QColor(0, 0, 0, 110))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(QRect(6, 0, int(self._size) - 12, int(label_h)), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), f"X:{int(self._pos.x())} Y:{int(self._pos.y())}")
        painter.drawText(
            QRect(6, int(self._size) - int(label_h), int(self._size) - 12, int(label_h)),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            str(self._color_text),
        )
