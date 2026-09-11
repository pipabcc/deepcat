from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageSequence

from deepcat.output.gif_exporter import export_frame_files_to_gif, export_frames_to_gif, export_video_to_gif


def test_export_frames_to_gif_infinite_loop(tmp_path: Path):
    """测试多帧图像导出为 GIF 时具有无限循环属性 (loop=0) 以及正确的分辨率和帧数。"""
    h, w = 120, 160
    # 构造 4 帧不同色彩的图像 (BGR)
    f1 = np.full((h, w, 3), [255, 0, 0], dtype=np.uint8)  # 蓝色
    f2 = np.full((h, w, 3), [0, 255, 0], dtype=np.uint8)  # 绿色
    f3 = np.full((h, w, 3), [0, 0, 255], dtype=np.uint8)  # 红色
    f4 = np.full((h, w, 3), [255, 255, 255], dtype=np.uint8)  # 白色

    frames = [f1, f2, f3, f4]
    out_gif = tmp_path / "test_output.gif"

    success = export_frames_to_gif(frames, out_gif, fps=10.0)
    assert success is True
    assert out_gif.exists()
    assert out_gif.stat().st_size > 0

    with Image.open(out_gif) as img:
        assert getattr(img, "n_frames", 1) == 4
        assert img.size == (w, h)
        # GIF89a 规范中 Netscape 循环标记: 0 表示无限循环
        assert img.info.get("loop") == 0


def test_export_frames_to_gif_empty_input(tmp_path: Path):
    """测试空输入列表或全 None 时的防御性处理。"""
    out_gif = tmp_path / "empty.gif"
    assert export_frames_to_gif([], out_gif) is False
    assert not out_gif.exists()


def test_export_video_to_gif(tmp_path: Path):
    """测试从实际 MP4 视频转码为高清无限循环 GIF。"""
    import cv2

    h, w = 80, 100
    fps = 12.0
    mp4_path = tmp_path / "test_video.mp4"
    gif_path = tmp_path / "test_video.gif"

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(mp4_path), fourcc, fps, (w, h))
    assert writer.isOpened()

    for i in range(6):
        color = (int(i * 40) % 256, int(255 - i * 30) % 256, 128)
        frame = np.full((h, w, 3), color, dtype=np.uint8)
        writer.write(frame)
    writer.release()

    assert mp4_path.exists()
    assert mp4_path.stat().st_size > 0

    success = export_video_to_gif(mp4_path, gif_path, fps=fps)
    assert success is True
    assert gif_path.exists()
    assert gif_path.stat().st_size > 0

    with Image.open(gif_path) as img:
        assert getattr(img, "n_frames", 1) == 6
        assert img.size == (w, h)
        assert img.info.get("loop") == 0


def test_export_video_to_gif_nonexistent(tmp_path: Path):
    """测试不存在的视频文件防御性处理。"""
    assert export_video_to_gif(tmp_path / "not_found.mp4", tmp_path / "out.gif") is False


def test_tray_notification_path_parsing(tmp_path: Path):
    """测试系统托盘对包含 GIF 说明的录制保存通知路径的解析能力。"""
    mp4_file = tmp_path / "record_20260829_080000.mp4"
    mp4_file.write_text("dummy")
    gif_file = tmp_path / "record_20260829_080000.gif"
    gif_file.write_text("dummy")

    msg = f"录制已保存：{mp4_file} (含高清GIF: {gif_file.name})"
    assert "录制已保存：" in msg
    raw_path = msg.split("录制已保存：", 1)[-1].strip()
    first_token = raw_path.split(" (", 1)[0].strip() if " (" in raw_path else raw_path
    p = Path(first_token)
    assert p.parent.exists()
    assert str(p.parent) == str(tmp_path)


def _build_screen_like_frames(count: int, h: int = 270, w: int = 480) -> list[np.ndarray]:
    """构造类屏幕录制帧：静态桌面 + 移动窗口，大部分像素逐帧不变。"""
    base = np.full((h, w, 3), 245, dtype=np.uint8)
    base[20:40, :, :] = (60, 60, 60)
    base[:, :30, :] = (200, 205, 210)
    base[50:200, 50:380, :] = (255, 255, 255)
    frames = []
    for i in range(count):
        f = base.copy()
        y0 = 60 + (i * 4) % 120
        f[y0 : y0 + 40, 60:320, :] = (30, 90, 160)
        frames.append(f)
    return frames


def test_export_frames_to_gif_delta_encoding_shrinks_static_recording(tmp_path: Path):
    """静态为主的录屏应启用 Pillow 帧差分（disposal=1），显著小于旧的全帧编码。"""
    from PIL import Image as PILImage
    from PIL import ImageSequence

    frames = _build_screen_like_frames(24)
    out_gif = tmp_path / "delta.gif"
    assert export_frames_to_gif(frames, out_gif, fps=12.0) is True

    # 复现旧实现的编码方式（disposal=2 + 全帧）作为基线
    quantized = [
        PILImage.fromarray(f[:, :, ::-1]).quantize(
            colors=256, method=PILImage.Quantize.MEDIANCUT, dither=PILImage.Dither.NONE
        )
        for f in frames
    ]
    baseline = tmp_path / "baseline.gif"
    quantized[0].save(
        str(baseline),
        format="GIF",
        save_all=True,
        append_images=quantized[1:],
        duration=83,
        loop=0,
        disposal=2,
        optimize=False,
    )

    assert out_gif.stat().st_size < baseline.stat().st_size * 0.8

    # 帧差分不得损失内容：逐帧解码与原图比对（BGR→RGB）
    with PILImage.open(out_gif) as img:
        assert getattr(img, "n_frames", 1) == len(frames)
        total_err = 0.0
        n = 0
        for i, fr in enumerate(ImageSequence.Iterator(img)):
            decoded = np.asarray(fr.convert("RGB")).astype(int)
            original = frames[i][:, :, ::-1].astype(int)
            total_err += float(np.abs(decoded - original).mean())
            n += 1
        assert total_err / n < 6.0


def test_export_frames_to_gif_downscales_oversized_frames(tmp_path: Path):
    """仅在调用方明确设置上限时缩小，保留原有的可选压缩能力。"""
    f = np.full((1500, 2400, 3), 128, dtype=np.uint8)
    out_gif = tmp_path / "big.gif"
    assert export_frames_to_gif([f], out_gif, fps=12.0, max_long_side=1920) is True

    with Image.open(out_gif) as img:
        long_side = max(img.size)
        assert long_side == 1920
        # 0.8 缩放比例：2400x1500 → 1920x1200
        assert img.size == (1920, 1200)


@pytest.mark.parametrize("source_kind", ["frames", "files"])
def test_native_resolution_preserves_one_pixel_details(tmp_path: Path, source_kind: str):
    """原分辨率的细线和小面积颜色不能被缩放或缩略图调色板抹去。"""
    frame = np.full((64, 2400, 3), 240, dtype=np.uint8)
    frame[8:56, 101::13] = (15, 25, 220)
    frame[10:30, 403] = (30, 210, 50)
    frame[40, 407:423] = (250, 249, 248)
    out_gif = tmp_path / "native.gif"
    if source_kind == "frames":
        success = export_frames_to_gif([frame], out_gif)
    else:
        source = tmp_path / "原始帧.png"
        Image.fromarray(frame[:, :, ::-1]).save(source)
        success = export_frame_files_to_gif([source], out_gif)

    assert success is True
    with Image.open(out_gif) as img:
        assert img.size == (2400, 64)
        np.testing.assert_array_equal(np.asarray(img.convert("RGB")), frame[:, :, ::-1])


def test_local_palettes_preserve_new_colors_and_restore_background(tmp_path: Path):
    """局部调色板变化时，帧差分仍须正确还原新颜色和被移动内容覆盖的背景。"""
    frames = []
    for index in range(24):
        frame = np.full((32, 64, 3), (30, 60, 90), dtype=np.uint8)
        frame[10:20, index : index + 12] = (index * 10, 255 - index * 8, index * 7)
        frame[0, index] = (1, 2, index)
        frames.append(frame)
    out_gif = tmp_path / "palette_changes.gif"
    assert export_frames_to_gif(frames, out_gif) is True

    with Image.open(out_gif) as img:
        assert img.n_frames == len(frames)
        for frame, original in zip(ImageSequence.Iterator(img), frames):
            np.testing.assert_array_equal(np.asarray(frame.convert("RGB")), original[:, :, ::-1])


@pytest.mark.parametrize("source_kind", ["frames", "png", "jpg"])
def test_gradient_preserves_local_average_color(tmp_path: Path, source_kind: str):
    """抖动应保留渐变的局部平均颜色，避免大片颜色偏移形成水渍感。"""
    x, y = np.meshgrid(np.linspace(0, 255, 384), np.linspace(0, 255, 192))
    rgb = np.stack((x, y, 50 + x * 0.3 + y * 0.3), axis=2).astype(np.uint8)
    # 小面积高饱和度内容也必须参与选色。
    rgb[40:46, 80:86] = (255, 0, 255)
    out_gif = tmp_path / "gradient.gif"
    reference = Image.fromarray(rgb)
    if source_kind == "frames":
        success = export_frames_to_gif([rgb[:, :, ::-1]], out_gif)
    else:
        source = tmp_path / f"录制缓存.{source_kind}"
        reference.save(source, **({"quality": 92} if source_kind == "jpg" else {}))
        # JPEG 缓存本身已压缩，比较实际解码出的源像素。
        with Image.open(source) as img:
            reference = img.convert("RGB")
        success = export_frame_files_to_gif([source], out_gif)
    assert success is True

    reference = reference.resize((96, 48), Image.Resampling.BOX)
    with Image.open(out_gif) as img:
        decoded = img.convert("RGB").resize(reference.size, Image.Resampling.BOX)
    difference = np.asarray(decoded, dtype=np.float32) - np.asarray(reference, dtype=np.float32)
    assert float(np.sqrt(np.mean(difference**2))) < 2.0


def test_all_256_palette_colors_remain_opaque(tmp_path: Path):
    """用满 256 个颜色时也不能挪用某个颜色作透明索引。"""
    ramp = np.arange(256, dtype=np.uint8).reshape(16, 16)
    rgb = np.stack((ramp, np.flipud(ramp), np.fliplr(ramp)), axis=2)
    frames = [rgb[:, :, ::-1], np.roll(rgb, 3, axis=0)[:, :, ::-1]]
    out_gif = tmp_path / "all_colors.gif"
    assert export_frames_to_gif(frames, out_gif) is True

    with Image.open(out_gif) as img:
        assert img.n_frames == len(frames)
        for decoded, original in zip(ImageSequence.Iterator(img), frames):
            np.testing.assert_array_equal(np.asarray(decoded.convert("RGB")), original[:, :, ::-1])


@pytest.mark.parametrize("fps", [12.0, 24.0, 29.97])
def test_fractional_frame_delays_preserve_duration(tmp_path: Path, fps: float):
    """累计时长与源帧率一致，10ms 精度的舍入误差不能逐帧积累。"""
    frames = [np.full((8, 8, 3), index * 3, dtype=np.uint8) for index in range(60)]
    out_gif = tmp_path / "timing.gif"
    assert export_frames_to_gif(frames, out_gif, fps=fps) is True

    with Image.open(out_gif) as img:
        assert img.n_frames == len(frames)
        durations = [frame.info["duration"] for frame in ImageSequence.Iterator(img)]
    assert abs(sum(durations) - len(frames) * 1000 / fps) <= 5
    assert all(duration >= 10 and duration % 10 == 0 for duration in durations)


def test_repeated_frames_merge_without_losing_duration(tmp_path: Path):
    red = np.full((16, 24, 3), (0, 0, 255), dtype=np.uint8)
    blue = np.full((16, 24, 3), (255, 0, 0), dtype=np.uint8)
    frames = [red, red, red, blue, blue, red]
    out_gif = tmp_path / "repeated.gif"
    assert export_frames_to_gif(frames, out_gif, fps=12) is True

    with Image.open(out_gif) as img:
        assert img.n_frames == 3
        durations = [frame.info["duration"] for frame in ImageSequence.Iterator(img)]
        assert durations == [250, 170, 80]
        assert img.info.get("loop") == 0


def test_long_still_frame_preserves_duration_beyond_gif_block_limit(tmp_path: Path):
    frame = np.full((8, 8, 3), 80, dtype=np.uint8)
    out_gif = tmp_path / "long_still.gif"
    assert export_frames_to_gif([frame] * 66, out_gif, fps=0.1) is True

    with Image.open(out_gif) as img:
        assert img.n_frames == 2
        assert sum(frame.info["duration"] for frame in ImageSequence.Iterator(img)) == 660_000


def test_file_export_failure_preserves_existing_output(tmp_path: Path):
    """后续缓存损坏时应明确失败，保留旧文件并清理本次临时输出。"""
    source = tmp_path / "frame.png"
    Image.new("RGB", (16, 16), "red").save(source)
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    out_gif = tmp_path / "previous.gif"
    out_gif.write_bytes(b"previous output")

    assert export_frame_files_to_gif([source, corrupt], out_gif) is False
    assert out_gif.read_bytes() == b"previous output"
    assert not list(tmp_path.glob(".previous.gif.*.tmp"))


def test_file_export_empty_input(tmp_path: Path):
    out_gif = tmp_path / "empty.gif"
    assert export_frame_files_to_gif([], out_gif) is False
    assert not out_gif.exists()


def test_frame_size_mismatch_does_not_leave_partial_gif(tmp_path: Path):
    out_gif = tmp_path / "mismatched.gif"
    frames = [np.zeros((8, 8, 3), dtype=np.uint8), np.zeros((16, 16, 3), dtype=np.uint8)]
    assert export_frames_to_gif(frames, out_gif) is False
    assert not out_gif.exists()
    assert not list(tmp_path.glob(".mismatched.gif.*.tmp"))


def test_video_new_scene_after_static_intro_keeps_its_colors(tmp_path: Path):
    """前 16 帧全黑的视频，其后出现的彩色场景不能沿用黑色调色板。"""
    import cv2

    source = tmp_path / "scene_change.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 12.0, (96, 64))
    assert writer.isOpened()
    try:
        for index in range(24):
            color = (0, 0, 0) if index < 18 else (40, 170, 230)
            writer.write(np.full((64, 96, 3), color, dtype=np.uint8))
    finally:
        writer.release()

    out_gif = tmp_path / "scene_change.gif"
    assert export_video_to_gif(source, out_gif) is True
    with Image.open(out_gif) as img:
        durations = [frame.info["duration"] for frame in ImageSequence.Iterator(img)]
        assert sum(durations) == 2000
        img.seek(img.n_frames - 1)
        decoded_color = np.asarray(img.convert("RGB"), dtype=float).mean(axis=(0, 1))
        np.testing.assert_allclose(decoded_color, (230, 170, 40), atol=6)


def test_video_export_preserves_native_resolution(tmp_path: Path):
    import cv2

    source = tmp_path / "wide.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 12.0, (2400, 64))
    assert writer.isOpened()
    try:
        writer.write(np.full((64, 2400, 3), 128, dtype=np.uint8))
    finally:
        writer.release()

    out_gif = tmp_path / "wide.gif"
    assert export_video_to_gif(source, out_gif) is True
    with Image.open(out_gif) as img:
        assert img.size == (2400, 64)
