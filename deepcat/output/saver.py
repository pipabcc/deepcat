from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np

from deepcat.utils.image_utils import bgr_to_rgb
from deepcat.settings_store import get_image_output_dir, get_pdf_output_dir
from deepcat.output.naming import open_exclusive, unique_output_path


def _default_output_dir() -> str:
    return str(get_image_output_dir(validate_writable=True))


def save_image(
    image_bgr: np.ndarray,
    output_dir: Optional[str] = None,
    format: str = "png",
    quality: int = 95,
) -> str:
    """
    保存图像并返回文件路径。
    format: png / jpg / pdf
    """
    fmt = (format or "png").lower().strip()
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in {"png", "jpg", "pdf"}:
        raise ValueError(f"不支持的输出格式: {format}")

    out_dir = output_dir or _default_output_dir()
    if output_dir is None and fmt == "pdf":
        out_dir = str(get_pdf_output_dir(validate_writable=True))
    os.makedirs(out_dir, exist_ok=True)

    path = str(unique_output_path(out_dir, fmt))

    if fmt in {"png", "jpg"}:
        try:
            import cv2
        except Exception:
            cv2 = None

        if cv2 is not None:
            params = []
            if fmt == "jpg":
                params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
            elif fmt == "png":
                params = [int(cv2.IMWRITE_PNG_COMPRESSION), 3]
            # cv2.imwrite 在 Windows 非 ASCII 路径（中文/emoji 目录）下会失败，
            # 改为先在内存中编码，再自行写入目标文件
            ok, buf = cv2.imencode(f".{fmt}", image_bgr, params)
            if not ok:
                raise RuntimeError("cv2.imencode 编码失败")
            data = buf.tobytes()
            # 用 O_CREAT|O_EXCL 独占创建文件，规避探测与写入之间的跨进程竞态；
            # 命名冲突时复用 unique_output_path 逻辑重新取名后重试
            path = ""
            for _ in range(100):
                path = str(unique_output_path(out_dir, fmt))
                try:
                    fobj = open_exclusive(path)
                except FileExistsError:
                    continue
                try:
                    fobj.write(data)
                finally:
                    fobj.close()
                break
            else:
                raise RuntimeError("cv2 保存失败：输出文件命名持续冲突")
        else:
            try:
                from PIL import Image
            except Exception as e:
                raise RuntimeError("未安装 pillow，请先 pip install pillow") from e
            rgb = bgr_to_rgb(image_bgr)
            img = Image.fromarray(rgb)
            if fmt == "png":
                img.save(path, format="PNG")
            else:
                img.save(path, format="JPEG", quality=int(quality), optimize=True)
    else:
        try:
            from PIL import Image
        except Exception as e:
            raise RuntimeError("未安装 pillow，请先 pip install pillow") from e
        rgb = bgr_to_rgb(image_bgr)
        img = Image.fromarray(rgb)
        img.save(path, format="PDF")

    return path


def save_image_to_path(
    image_bgr: np.ndarray,
    path: str,
    format: Optional[str] = None,
    quality: int = 95,
) -> str:
    p = str(path)
    fmt = (format or "").lower().strip()
    if not fmt:
        ext = Path(p).suffix.lower().lstrip(".")
        fmt = ext or "png"
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in {"png", "jpg", "pdf"}:
        raise ValueError(f"不支持的输出格式: {fmt}")

    out_dir = str(Path(p).resolve().parent)
    os.makedirs(out_dir, exist_ok=True)

    if fmt in {"png", "jpg"}:
        try:
            import cv2
        except Exception:
            cv2 = None

        if cv2 is not None:
            params = []
            if fmt == "jpg":
                params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
            elif fmt == "png":
                params = [int(cv2.IMWRITE_PNG_COMPRESSION), 3]
            # cv2.imwrite 在 Windows 非 ASCII 路径（中文/emoji 目录）下会失败，
            # 改为先在内存中编码，再用 numpy tofile 写入（支持 unicode 路径）
            ok, buf = cv2.imencode(f".{fmt}", image_bgr, params)
            if not ok:
                raise RuntimeError("cv2.imencode 编码失败")
            buf.tofile(p)
        else:
            try:
                from PIL import Image
            except Exception as e:
                raise RuntimeError("未安装 pillow，请先 pip install pillow") from e
            rgb = bgr_to_rgb(image_bgr)
            img = Image.fromarray(rgb)
            if fmt == "png":
                img.save(p, format="PNG")
            else:
                img.save(p, format="JPEG", quality=int(quality), optimize=True)
    else:
        try:
            from PIL import Image
        except Exception as e:
            raise RuntimeError("未安装 pillow，请先 pip install pillow") from e
        rgb = bgr_to_rgb(image_bgr)
        img = Image.fromarray(rgb)
        img.save(p, format="PDF")

    return p
