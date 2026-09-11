"""滚动截图输出策略，与屏幕采集和滚动状态分离。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from deepcat.core.image_budget import bounded_chunk_rows
from deepcat.core.stitcher import PiecewiseStitcher
from deepcat.output.naming import unique_output_path
from deepcat.output.png_stream import save_png_chunks
from deepcat.output.saver import save_image_to_path
from deepcat.settings_store import get_image_output_dir, get_pdf_output_dir
from deepcat.utils.logger import get_logger


logger = get_logger()
_JPEG_MAX_DIMENSION = 65500


def _image_chunks(image: np.ndarray):
    rows = bounded_chunk_rows(image.shape[1], 14000, image.shape[0])
    for start in range(0, image.shape[0], rows):
        yield image[start : start + rows]


def _complete_format(format: str, width: int, height: int) -> str:
    fmt = str(format or "png").strip().lower()
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in {"png", "jpg", "pdf"}:
        raise ValueError(f"不支持的截图格式: {fmt}")
    if fmt == "jpg" and max(width, height) > _JPEG_MAX_DIMENSION:
        logger.warning("长图超过 JPEG 尺寸上限，改用完整 PNG 保存")
        return "png"
    return fmt


def save_full_image_to_path(image: np.ndarray, path: str, format: str | None = None, quality: int = 95) -> str:
    """原图另存为与自动保存共用完整输出，JPEG 超限时保留完整 PNG。"""
    target = Path(path)
    requested = str(format or target.suffix.lstrip(".") or "png").lower()
    height, width = image.shape[:2]
    actual = _complete_format(requested, width, height)
    if actual == "png" and requested in {"jpg", "jpeg"}:
        target = target.with_suffix(".png")
        if target.exists():
            target = unique_output_path(target.parent, "png", prefix=target.stem)
    if actual == "png":
        return save_png_chunks(_image_chunks(image), target, width, height)
    if actual == "pdf":
        from PIL import Image

        # PDF 页面边长有物理尺寸限制；只提高 DPI，不减少嵌入图像的像素。
        resolution = max(72.0, max(width, height) * 72.0 / 14000.0)
        with Image.fromarray(np.ascontiguousarray(image[:, :, ::-1])) as pdf_image:
            pdf_image.save(str(target), format="PDF", resolution=resolution)
        return str(target)
    return save_image_to_path(image, str(target), actual, int(quality))


def save_scroll_result(
    source: np.ndarray | PiecewiseStitcher,
    settings: Any,
    token: str,
    *,
    chunked: bool = False,
    vertical_flip: bool = False,
) -> object:
    """分块仅用于读取和编码，最终输出始终是完整图像文件。"""
    image = source if isinstance(source, np.ndarray) else None

    def full_image() -> np.ndarray:
        nonlocal image
        if image is None:
            image = source.result_image(vertical_flip=vertical_flip)
        if image is None or image.size == 0:
            raise ValueError("没有可保存的长图")
        return image

    if not settings.auto_save:
        return full_image()
    fmt = str(settings.output_format or "png").strip().lower()
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in {"png", "jpg", "pdf"}:
        raise ValueError(f"不支持的截图格式: {fmt}")
    formats = [fmt] if not settings.dual_output else [fmt if fmt != "pdf" else "png", "pdf"]
    outputs: list[str] = []
    if image is not None:
        height, width = image.shape[:2]
    else:
        height, width = source.total_height, source.width
        if height <= 0 or width <= 0:
            raise ValueError("没有可保存的长图")

    def chunks():
        if not isinstance(source, np.ndarray):
            yield from source.iter_result_chunks(14000, vertical_flip=vertical_flip)
        else:
            yield from _image_chunks(source)

    for output_format in formats:
        directory = (
            get_pdf_output_dir(validate_writable=True)
            if output_format == "pdf"
            else get_image_output_dir(validate_writable=True)
        )
        output_format = _complete_format(output_format, width, height)
        path = unique_output_path(directory, output_format, token=token)
        if output_format == "png":
            outputs.append(save_png_chunks(chunks(), path, width, height))
        else:
            outputs.append(save_full_image_to_path(full_image(), str(path), output_format, int(settings.jpg_quality)))
    return outputs[0] if len(outputs) == 1 else outputs
