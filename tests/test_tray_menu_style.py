import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMenu

from deepcat.ui.main_window.tray import TrayMixin


def test_tray_menu_uses_stable_fusion_style_and_geometry_tokens():
    app = QApplication.instance() or QApplication(sys.argv)
    menu = QMenu()

    TrayMixin._style_tray_menu(object(), menu)

    assert "fusion" in menu._deepcat_tray_style.metaObject().className().lower()
    style_sheet = menu.styleSheet()
    assert "min-width: 196px" in style_sheet
    assert "min-height: 34px" in style_sheet
    menu.close()
