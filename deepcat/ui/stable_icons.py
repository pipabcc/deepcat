from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QIcon, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer


_DEFAULT_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64)
_ICON_CACHE: dict[tuple[str, tuple[int, ...]], QIcon] = {}
_SVG_DATA_CACHE: dict[str, bytes] = {}


def _normalized_sizes(sizes: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted({int(size) for size in sizes if int(size) > 0}))


def _render_svg_icon(svg_data: bytes, sizes: tuple[int, ...]) -> QIcon:
    renderer = QSvgRenderer(QByteArray(svg_data))
    if not renderer.isValid():
        return QIcon()

    icon = QIcon()
    for size in sizes:
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            renderer.render(painter, QRectF(0.0, 0.0, float(size), float(size)))
        finally:
            painter.end()
        if not image.isNull():
            icon.addPixmap(QPixmap.fromImage(image), QIcon.Mode.Normal, QIcon.State.Off)
    return icon


def load_stable_icon(
    path: Path | str,
    fallback: Optional[QIcon] = None,
    *,
    sizes: Iterable[int] = _DEFAULT_ICON_SIZES,
) -> QIcon:
    """主动渲染并缓存图标，避免 SVG 延迟加载在休眠恢复后失效。"""

    icon_path = Path(path).resolve()
    normalized_sizes = _normalized_sizes(sizes)
    cache_key = (str(icon_path), normalized_sizes)
    cached = _ICON_CACHE.get(cache_key)
    if cached is not None and not cached.isNull():
        return QIcon(cached)

    icon = QIcon()
    path_key = str(icon_path)
    if icon_path.suffix.lower() == ".svg":
        svg_data = _SVG_DATA_CACHE.get(path_key)
        if svg_data is None and icon_path.is_file():
            try:
                svg_data = icon_path.read_bytes()
            except OSError:
                svg_data = b""
            if svg_data:
                _SVG_DATA_CACHE[path_key] = svg_data
        if svg_data:
            icon = _render_svg_icon(svg_data, normalized_sizes)

    if icon.isNull() and icon_path.is_file():
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            icon = QIcon(pixmap)

    if icon.isNull() and fallback is not None:
        icon = QIcon(fallback)
    if not icon.isNull():
        _ICON_CACHE[cache_key] = QIcon(icon)
    return icon


def clear_rendered_icon_cache() -> None:
    """仅清理已渲染对象；保留 SVG 字节，后续恢复无需再次读取磁盘。"""

    _ICON_CACHE.clear()
