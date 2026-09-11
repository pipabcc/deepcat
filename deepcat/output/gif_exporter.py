from __future__ import annotations

import logging
import math
import tempfile
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Iterable, Optional, Sequence

import numpy as np

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

# GIF 的延迟以 1/100 秒存储，每个图像块最多表示 65535 个单位。
_MAX_FRAME_DURATION_MS = 655_350


def _resize_frame(img: Image.Image, max_long_side: Optional[int]) -> Image.Image:
    """默认保留源分辨率，仅在调用方明确指定上限时缩放。"""
    from PIL import Image

    if max_long_side and max_long_side > 0 and max(img.size) > max_long_side:
        scale = float(max_long_side) / float(max(img.size))
        img = img.resize(
            (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return img


def _quantize_frame(img: Image.Image) -> Image.Image:
    """用当前帧的完整像素选色，再进行误差扩散，保留渐变和细小彩色内容。"""
    from PIL import Image

    # 纯色界面、文字等不超过 256 色的帧可以精确表示，无需引入抖动。
    if img.getcolors(maxcolors=256) is not None:
        return img.quantize(colors=256, method=Image.Quantize.MEDIANCUT)

    # 覆盖当前帧的颜色范围，避免缩略图/全片共用调色板遗漏细线与新场景颜色。
    palette = img.quantize(colors=256, method=Image.Quantize.MAXCOVERAGE)
    # Pillow 在直接生成调色板时会忽略 dither，必须单独映射才能真正启用抖动。
    return img.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG)


def _effective_fps(fps: Optional[float], default: float = 12.0) -> float:
    if fps is None or not math.isfinite(float(fps)) or fps <= 0:
        return default
    return min(100.0, max(0.1, float(fps)))


def _write_gif_frame(
    stream: BinaryIO,
    frame: Image.Image,
    bbox: Optional[tuple[int, int, int, int]],
    duration_ms: int,
) -> None:
    from PIL import GifImagePlugin

    if bbox is None:
        offset = (0, 0)
    else:
        offset = bbox[:2]
        frame = frame.crop(bbox)

    # 合并后的长静止画面可能超过 GIF 的单帧延迟上限，拆块但不缩短播放时长。
    while duration_ms > 0:
        block_duration = min(duration_ms, _MAX_FRAME_DURATION_MS)
        stream.writelines(
            GifImagePlugin.getdata(
                frame,
                offset=offset,
                duration=block_duration,
                disposal=1,
                include_color_table=bbox is not None,
            )
        )
        duration_ms -= block_duration


def _save_gif(
    frames: Iterable[Image.Image],
    out_file: Path,
    fps: float,
    source_desc: str,
) -> bool:
    """逐帧量化并写出，只保留相邻帧；帧差分和重复帧合并均不损失像素。

    Pillow 的 save_all 会缓存整段动画。这里复用其 GIF 编码器按图像块写入，
    使原分辨率长录制也无需为了内存预算而缩图、抽帧。
    """
    temp_file: Optional[Path] = None
    try:
        from PIL import GifImagePlugin, ImageChops

        effective_fps = _effective_fps(fps)
        iterator = iter(frames)
        first_frame = next(iterator, None)
        if first_frame is None:
            logger.warning("_save_gif: 没有有效帧，取消导出 (%s)", source_desc)
            return False

        out_file.parent.mkdir(parents=True, exist_ok=True)
        # 全部编码成功后再替换目标，避免失败时破坏已有的 GIF。
        with tempfile.NamedTemporaryFile(
            dir=out_file.parent, prefix=f".{out_file.name}.", suffix=".tmp", delete=False
        ) as stream:
            temp_file = Path(stream.name)
            header, _ = GifImagePlugin.getheader(first_frame, info={"loop": 0})
            stream.writelines(header)

            pending_frame = first_frame
            previous_rgb = first_frame.convert("RGB")
            pending_bbox = None
            pending_duration = 0
            elapsed_centiseconds = 0
            frame_count = 0

            for index, frame in enumerate(chain((first_frame,), iterator)):
                if frame.size != first_frame.size:
                    raise ValueError("GIF 帧尺寸不一致，无法保留完整画面")
                # 按累计时间取整，避免 12fps 全部写为 80ms 后播放越来越快。
                end_centiseconds = round((index + 1) * 100.0 / effective_fps)
                duration_ms = (end_centiseconds - elapsed_centiseconds) * 10
                elapsed_centiseconds = end_centiseconds
                frame_count += 1

                current_rgb = frame.convert("RGB")
                # 局部调色板的索引含义会变化，必须比较实际 RGB 才能正确做帧差分。
                bbox = ImageChops.difference(previous_rgb, current_rgb).getbbox()
                if bbox is None:
                    pending_duration += duration_ms
                    continue

                _write_gif_frame(stream, pending_frame, pending_bbox, pending_duration)
                pending_frame = frame
                pending_bbox = bbox
                pending_duration = duration_ms
                previous_rgb = current_rgb

            _write_gif_frame(stream, pending_frame, pending_bbox, pending_duration)
            stream.write(b";")

        temp_file.replace(out_file)
        logger.info(
            "_save_gif: 成功生成原分辨率无限循环 GIF: %s (%s, %d 帧, %.3f fps)",
            out_file,
            source_desc,
            frame_count,
            effective_fps,
        )
        return True
    except Exception:
        logger.exception("_save_gif: 保存 GIF 失败 (%s)", source_desc)
        return False
    finally:
        if temp_file is not None:
            try:
                temp_file.unlink(missing_ok=True)
            except OSError:
                logger.warning("清理 GIF 临时文件失败: %s", temp_file, exc_info=True)


def export_frames_to_gif(
    frames: Sequence[np.ndarray],
    output_path: str | Path,
    fps: float = 12.0,
    max_long_side: Optional[int] = None,
) -> bool:
    """将 BGR 帧序列导出为无限循环 GIF，默认保留原始分辨率和播放时长。

    GIF 每帧最多 256 色，复杂画面通过逐帧选色和误差扩散接近源画面。
    max_long_side 仅用于调用方主动选择缩小输出的情况。
    """
    valid = [frame for frame in frames if frame is not None and getattr(frame, "size", 0)]
    if not valid:
        logger.warning("export_frames_to_gif: 传入的帧序列为空，取消导出")
        return False

    def _iter_quantized():
        from PIL import Image

        for frame in valid:
            rgb = frame[:, :, 2::-1] if frame.ndim == 3 else frame
            img = Image.fromarray(rgb).convert("RGB")
            yield _quantize_frame(_resize_frame(img, max_long_side))

    return _save_gif(_iter_quantized(), Path(output_path).resolve(), fps, f"内存帧: {len(valid)}")


def export_frame_files_to_gif(
    frame_paths: Sequence[str | Path],
    output_path: str | Path,
    fps: float = 12.0,
    max_long_side: Optional[int] = None,
) -> bool:
    """从 PNG/JPEG 等录制缓存逐帧读盘导出 GIF，默认保留原始分辨率。"""
    if not frame_paths:
        logger.warning("export_frame_files_to_gif: 帧文件列表为空，取消导出")
        return False

    def _iter_quantized():
        from PIL import Image

        for path in frame_paths:
            with Image.open(path) as source:
                img = _resize_frame(source.convert("RGB"), max_long_side)
            yield _quantize_frame(img)

    return _save_gif(_iter_quantized(), Path(output_path).resolve(), fps, f"磁盘帧: {len(frame_paths)}")


def export_video_to_gif(
    video_path: str | Path,
    output_path: str | Path,
    fps: Optional[float] = None,
    max_long_side: Optional[int] = None,
) -> bool:
    """逐帧解码视频并导出无限循环 GIF，默认保留源分辨率和源帧率。

    不预读全片，也不按内存预算抽帧；每帧独立选色，后续场景不会受首帧限制。
    fps 可显式指定播放帧率；max_long_side 可显式限制输出尺寸。
    """
    src = Path(video_path).resolve()
    if not src.is_file() or src.stat().st_size == 0:
        logger.warning("export_video_to_gif: 源视频文件不存在或为空: %s", src)
        return False

    try:
        import cv2
    except Exception:
        logger.exception("export_video_to_gif: 导入 cv2 失败")
        return False

    cap = cv2.VideoCapture(str(src))
    try:
        if not cap.isOpened():
            logger.warning("export_video_to_gif: 无法打开视频文件: %s", src)
            return False

        source_fps = _effective_fps(cap.get(cv2.CAP_PROP_FPS))
        target_fps = _effective_fps(fps, default=source_fps)

        def _iter_quantized():
            from PIL import Image

            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break
                img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                yield _quantize_frame(_resize_frame(img, max_long_side))

        return _save_gif(_iter_quantized(), Path(output_path).resolve(), target_fps, f"视频: {src.name}")
    finally:
        cap.release()
