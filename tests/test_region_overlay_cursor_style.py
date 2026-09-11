"""截图十字光标样式回归：2px 笔画 + 白色描边（实心中心，不挖空）。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_app = None


def setup_module(module) -> None:
    global _app
    from PyQt6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])


def test_cross_cursor_is_outlined_and_solid():
    from PyQt6.QtGui import QCursor

    from deepcat.ui.region_overlay import RegionOverlay

    cursor = RegionOverlay._build_cross_cursor(None, "#1E6BFF")
    assert isinstance(cursor, QCursor)
    image = cursor.pixmap().toImage()
    assert image.width() == 32 and image.height() == 32

    c = 16
    # 中心是实心的（不再是挖空）：交叉点有主题色像素
    center = image.pixelColor(c, c)
    assert center.alpha() > 0
    # 距中心 3px 处仍在水平臂中心线上，为蓝色主题色核心（非纯白描边）
    core = image.pixelColor(c + 3, c)
    assert core.alpha() > 0
    assert core.blue() > 120 and core.red() < 120  # #1E6BFF：蓝高红低
    # 白色描边在笔画核心两侧各 1px（4px 白带 15..16 上叠 2px 蓝）：
    # 核心 [15,16]，白边在 y=14 与 y=17
    outline = image.pixelColor(c + 5, c - 2)
    assert outline.alpha() > 150
    assert outline.red() > 220 and outline.green() > 220 and outline.blue() > 220
    # 横臂外侧另一侧同样有白色描边（y=17）
    outline_other = image.pixelColor(c - 6, c + 1)
    assert outline_other.red() > 220 and outline_other.green() > 220 and outline_other.blue() > 220
    # 垂直方向远离笔画处（y=22，超出白描边带）保持透明
    assert image.pixelColor(c - 10, c + 6).alpha() == 0
    # 中心外侧列的笔画总厚度 = 白描边带宽（核心 2px 叠在 4px 白带上，共 4 行）
    probe_x = c - 8
    run = sum(1 for y in range(32) if image.pixelColor(probe_x, y).alpha() > 0)
    assert 4 <= run <= 6
