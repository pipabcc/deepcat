from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QBrush, QIcon, QPainter, QPixmap, QImage


def trim_transparent_margins(pixmap: QPixmap) -> QPixmap:
    image = pixmap.toImage()
    width = image.width()
    height = image.height()

    # 寻找非透明像素的边界
    left = width
    right = 0
    top = height
    bottom = 0

    for y in range(height):
        for x in range(width):
            alpha = image.pixelColor(x, y).alpha()
            if alpha > 15:  # 略微过滤极为微弱的虚化边缘
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y

    # 如果没有找到任何非透明像素，返回原图
    if left > right or top > bottom:
        return pixmap

    # 计算实际内容的区域，加 0px 保护边距，确保最大面积
    padding = 0
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(width - 1, right + padding)
    bottom = min(height - 1, bottom + padding)
    rect = QRect(left, top, right - left + 1, bottom - top + 1)

    # 裁剪并返回
    trimmed = pixmap.copy(rect)
    return trimmed


def fit_pixmap_to_canvas(pixmap: QPixmap, size: int) -> QPixmap:
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    if pixmap.isNull():
        return canvas

    fitted = pixmap.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(canvas)
    try:
        x = max(0, (size - fitted.width()) // 2)
        y = max(0, (size - fitted.height()) // 2)
        painter.drawPixmap(x, y, fitted)
    finally:
        painter.end()
    return canvas


def create_app_icon() -> QIcon:
    icon_path = Path(__file__).resolve().parent / "assets" / "app.ico"
    if icon_path.exists():
        raw_icon = QIcon(str(icon_path))
        if not raw_icon.isNull():
            trimmed_icon = QIcon()
            # 256 是超大高清帧，128, 96, 64, 48, 40, 32, 24, 20, 16 分别是任务栏、托盘在各种缩放下的常见像素
            sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
            for sz in sizes:
                pixmap = raw_icon.pixmap(sz, sz)
                if not pixmap.isNull():
                    trimmed_pix = trim_transparent_margins(pixmap)
                    trimmed_icon.addPixmap(trimmed_pix)
            if not trimmed_icon.isNull():
                return trimmed_icon
            return raw_icon

    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    body = QRect(8, 16, 48, 36)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(28, 30, 34)))
    p.drawRoundedRect(body, 11, 11)
    p.setBrush(QBrush(QColor(42, 45, 50)))
    p.drawRoundedRect(QRect(17, 10, 30, 10), 5, 5)
    p.setBrush(QBrush(QColor(245, 245, 245)))
    p.drawEllipse(QRect(19, 21, 26, 26))
    p.setBrush(QBrush(QColor(28, 30, 34)))
    p.drawEllipse(QRect(25, 27, 14, 14))
    p.setBrush(QBrush(QColor(110, 113, 118)))
    p.drawEllipse(QRect(28, 30, 8, 8))
    p.setBrush(QBrush(QColor(220, 220, 220)))
    p.drawEllipse(QRect(43, 21, 6, 6))
    p.end()
    return QIcon(pm)
