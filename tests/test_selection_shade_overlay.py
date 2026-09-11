import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


def _app():
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PyQt6 not available: {exc}")
    return QApplication.instance() or QApplication(sys.argv)


def test_selection_shade_keeps_inside_transparent_and_shades_outside():
    app = _app()
    from PyQt6.QtCore import QRect, Qt
    from PyQt6.QtGui import QColor, QImage, QPainter
    from deepcat.ui.selection_border_overlay import SelectionShadeOverlay

    overlay = SelectionShadeOverlay(QRect(20, 20, 40, 30), shade_alpha=72)
    overlay.setGeometry(0, 0, 100, 80)

    image = QImage(100, 80, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        overlay.render(painter)
    finally:
        if painter.isActive():
            painter.end()

    app.processEvents()

    assert QColor(image.pixelColor(30, 30)).alpha() == 0
    assert QColor(image.pixelColor(5, 5)).alpha() == 72
