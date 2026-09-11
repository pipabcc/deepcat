from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import QApplication

from deepcat.ui.stable_icons import clear_rendered_icon_cache, load_stable_icon


_APP: QApplication | None = None


def _app() -> QApplication:
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def test_svg_icon_is_eagerly_rendered_and_survives_source_removal(tmp_path: Path) -> None:
    _app()
    svg_path = tmp_path / "icon.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
        '<rect width="24" height="24" fill="#2563eb"/></svg>',
        encoding="utf-8",
    )

    first = load_stable_icon(svg_path)
    svg_path.unlink()
    second = load_stable_icon(svg_path)

    assert not first.isNull()
    assert not first.pixmap(24, 24).isNull()
    assert not second.isNull()
    assert not second.pixmap(24, 24).isNull()


def test_svg_bytes_allow_rebuild_after_render_cache_clear(tmp_path: Path) -> None:
    _app()
    svg_path = tmp_path / "cached.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
        '<circle cx="8" cy="8" r="7" fill="#16a34a"/></svg>',
        encoding="utf-8",
    )
    assert not load_stable_icon(svg_path).isNull()
    svg_path.unlink()

    clear_rendered_icon_cache()
    rebuilt = load_stable_icon(svg_path)

    assert not rebuilt.isNull()
    assert not rebuilt.pixmap(32, 32).isNull()


def test_invalid_svg_uses_fallback_icon(tmp_path: Path) -> None:
    _app()
    invalid_path = tmp_path / "invalid.svg"
    invalid_path.write_text("not svg", encoding="utf-8")
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor("#ef4444"))
    fallback = QIcon(pixmap)

    icon = load_stable_icon(invalid_path, fallback)

    assert not icon.isNull()
    assert not icon.pixmap(16, 16).isNull()
