"""长图保存与原图预览的端到端回归，不以最终分块文件代替完整长图。"""

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image
import pytest

from deepcat.core.stitcher import PiecewiseStitcher
from deepcat.output.png_stream import save_png_chunks
from deepcat.output import scroll_saver


def settings():
    return SimpleNamespace(
        auto_save=True, output_format="png", dual_output=False, merge_image=False, merge_pdf=False, jpg_quality=95
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_spilled_capture_saves_one_complete_png_without_materializing(tmp_path, monkeypatch, reverse):
    expected = np.random.default_rng(11).integers(0, 256, (97, 35, 3), dtype=np.uint8)
    stitcher = PiecewiseStitcher(max_memory_bytes=64)
    for start in range(0, 97, 13):
        stitcher._append_piece(expected[start : start + 13])
    monkeypatch.setattr(scroll_saver, "get_image_output_dir", lambda **kwargs: tmp_path)
    monkeypatch.setattr(stitcher, "result_image", lambda **kwargs: pytest.fail("PNG 保存不应物化完整数组"))
    try:
        path = scroll_saver.save_scroll_result(stitcher, settings(), "完整长图", chunked=True, vertical_flip=reverse)
    finally:
        stitcher.close()
    assert isinstance(path, str) and Path(path).suffix == ".png"
    assert list(tmp_path.iterdir()) == [Path(path)]
    assert "part" not in Path(path).name
    with Image.open(path) as image:
        image.verify()
    actual = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    np.testing.assert_array_equal(actual, expected[::-1] if reverse else expected)


@pytest.mark.parametrize("actual_height", [3, 5])
def test_incomplete_png_does_not_replace_previous_file(tmp_path, actual_height):
    path = tmp_path / "existing.png"
    path.write_bytes(b"previous-file")
    with pytest.raises(ValueError):
        save_png_chunks([np.zeros((actual_height, 8, 3), np.uint8)], path, 8, 4)
    assert path.read_bytes() == b"previous-file"
    assert list(tmp_path.iterdir()) == [path]


def test_long_jpeg_is_saved_as_one_full_jpeg(tmp_path, monkeypatch):
    image = np.full((18001, 16, 3), 99, np.uint8)
    options = settings()
    options.output_format = "jpg"
    monkeypatch.setattr(scroll_saver, "get_image_output_dir", lambda **kwargs: tmp_path)
    path = scroll_saver.save_scroll_result(image, options, "jpeg")
    decoded = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    assert Path(path).suffix == ".jpg"
    assert decoded.shape == image.shape
    assert list(tmp_path.iterdir()) == [Path(path)]


def test_oversized_jpeg_falls_back_to_one_complete_png(tmp_path, monkeypatch):
    image = np.full((70000, 4, 3), 77, np.uint8)
    options = settings()
    options.output_format = "jpg"
    monkeypatch.setattr(scroll_saver, "get_image_output_dir", lambda **kwargs: tmp_path)
    path = scroll_saver.save_scroll_result(image, options, "huge-jpeg")
    assert Path(path).suffix == ".png"
    np.testing.assert_array_equal(cv2.imdecode(np.fromfile(path, np.uint8), 1), image)


def test_manual_save_produces_complete_image_with_reported_path(tmp_path):
    image = np.full((70000, 4, 3), 123, np.uint8)
    existing = tmp_path / "手动保存.png"
    existing.write_bytes(b"previous")
    path = scroll_saver.save_full_image_to_path(image, str(tmp_path / "手动保存.jpg"), "jpg")
    assert Path(path).suffix == ".png" and Path(path) != existing
    assert existing.read_bytes() == b"previous"
    np.testing.assert_array_equal(cv2.imdecode(np.fromfile(path, np.uint8), 1), image)


def test_pinned_save_failure_keeps_original_window_open(monkeypatch, tmp_path):
    from deepcat.ui import pinned_image_window as pinned

    calls = []
    dummy = SimpleNamespace(
        _image_bgr=np.zeros((4, 8, 3), np.uint8),
        _default_dir=str(tmp_path),
        _default_format="png",
        _jpg_quality=95,
        _save_in_progress=False,
        _finalize_overlay_annotations=lambda: None,
        _on_toast=lambda *args: calls.append(args),
        close=lambda: calls.append("closed"),
    )
    # 绑定真实处理器，覆盖完整回调链路
    dummy._on_close_save_finished = lambda tag, path: pinned.PinnedImageWindow._on_close_save_finished(dummy, tag, path)
    dummy._on_close_save_failed = lambda tag, error: pinned.PinnedImageWindow._on_close_save_failed(dummy, tag, error)

    def fake_start_image_save(image, path, fmt, quality, *, saver=None, on_saved=None, on_failed=None, **kwargs):
        # 模拟后台线程保存失败，回调直接同步触发
        on_failed(None, "磁盘空间不足")
        return None

    monkeypatch.setattr(pinned, "start_image_save", fake_start_image_save)
    pinned.PinnedImageWindow._do_close_save(dummy)
    assert "closed" not in calls
    assert calls[0][0] == "保存失败"
    assert dummy._save_in_progress is False


def test_pinned_save_success_reports_path_and_closes(monkeypatch, tmp_path):
    from deepcat.ui import pinned_image_window as pinned

    calls = []
    dummy = SimpleNamespace(
        _image_bgr=np.zeros((4, 8, 3), np.uint8),
        _default_dir=str(tmp_path),
        _default_format="png",
        _jpg_quality=95,
        _save_in_progress=False,
        _finalize_overlay_annotations=lambda: None,
        _on_toast=lambda *args: calls.append(args),
        close=lambda: calls.append("closed"),
    )
    dummy._on_close_save_finished = lambda tag, path: pinned.PinnedImageWindow._on_close_save_finished(dummy, tag, path)
    dummy._on_close_save_failed = lambda tag, error: pinned.PinnedImageWindow._on_close_save_failed(dummy, tag, error)

    target = tmp_path / "out.png"

    def fake_start_image_save(image, path, fmt, quality, *, saver=None, on_saved=None, on_failed=None, **kwargs):
        assert saver is pinned.save_full_image_to_path
        on_saved(None, str(target))
        return None

    monkeypatch.setattr(pinned, "start_image_save", fake_start_image_save)
    monkeypatch.setattr(pinned, "unique_output_path", lambda _dir, _fmt: target)
    pinned.PinnedImageWindow._do_close_save(dummy)
    assert "closed" in calls
    assert any(args and args[0] == "提示" for args in calls)


def test_image_save_worker_writes_file_and_reports(tmp_path):
    import gc

    from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

    from deepcat.ui.image_save_worker import ImageSaveWorker

    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    image = np.full((8, 8, 3), 128, np.uint8)
    results = {}
    loop = QEventLoop()
    worker = ImageSaveWorker(image, str(tmp_path / "out.png"), "png", 95)
    worker.saved.connect(lambda tag, p: (results.setdefault("path", p), loop.quit()))
    worker.failed.connect(lambda tag, err: (results.setdefault("error", err), loop.quit()))
    QTimer.singleShot(15000, loop.quit)
    worker.start()
    loop.exec()
    worker.wait(5000)
    worker.deleteLater()
    gc.collect()
    assert "error" not in results
    assert Path(results["path"]).exists()
    assert np.asarray(Image.open(results["path"])).shape[:2] == (8, 8)


def test_image_save_worker_reports_failure_without_file(tmp_path):
    import gc

    from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

    from deepcat.ui.image_save_worker import ImageSaveWorker

    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    results = {}
    loop = QEventLoop()

    def broken_saver(_image, _path, _fmt, _quality):
        raise OSError("磁盘空间不足")

    worker = ImageSaveWorker(np.zeros((4, 4, 3), np.uint8), str(tmp_path / "out.png"), "png", 95, saver=broken_saver)
    worker.saved.connect(lambda tag, p: (results.setdefault("path", p), loop.quit()))
    worker.failed.connect(lambda tag, err: (results.setdefault("error", err), loop.quit()))
    QTimer.singleShot(15000, loop.quit)
    worker.start()
    loop.exec()
    worker.wait(5000)
    worker.deleteLater()
    gc.collect()
    assert results.get("error") == "磁盘空间不足"
    assert "path" not in results


def test_pdf_capture_keeps_full_image_for_original_preview(tmp_path, monkeypatch):
    from pypdf import PdfReader
    from deepcat.ui.capture_worker import CaptureSettings, CaptureWorker

    image = np.full((19000, 16, 3), 55, np.uint8)
    stitcher = PiecewiseStitcher(max_memory_bytes=64)
    stitcher._append_piece(image)
    options = CaptureSettings(
        region=None,
        scroll_delay=0,
        scroll_amount=-1,
        scroll_method="mouse_wheel",
        max_frames=1,
        adaptive_wait=False,
        boost_scroll=False,
        reverse_scroll=False,
        merge_pdf=False,
        merge_image=False,
        dual_output=False,
        strip_height=0,
        min_confidence=0,
        bottom_diff_mean_threshold=0,
        bottom_confirm_count=1,
        detect_fixed_header=False,
        detect_fixed_footer=False,
        output_format="pdf",
        jpg_quality=95,
        auto_save=True,
    )
    monkeypatch.setattr(scroll_saver, "get_pdf_output_dir", lambda **kwargs: tmp_path)
    worker = CaptureWorker(options)
    outcomes, errors = [], []
    worker.finished.connect(outcomes.append)
    worker.failed.connect(lambda *args: errors.append(args))
    try:
        worker._finish_capture(options, stitcher, False, "pdf", None, False, 0, 1)
    finally:
        stitcher.close()
    assert not errors
    result = outcomes[0]
    np.testing.assert_array_equal(result.preview_image_bgr, image)
    with PdfReader(result.result) as reader:
        assert len(reader.pages) == 1
        assert float(reader.pages[0].mediabox.height) <= 14400
        embedded = reader.pages[0].images[0].image
        assert embedded.size == (16, 19000)


def test_auto_save_off_keeps_original_image_without_writing(tmp_path, monkeypatch):
    image = np.full((10, 8, 3), 31, np.uint8)
    stitcher = PiecewiseStitcher(max_memory_bytes=8)
    stitcher._append_piece(image)
    options = settings()
    options.auto_save = False
    monkeypatch.setattr(scroll_saver, "get_image_output_dir", lambda **kwargs: pytest.fail("不应自动保存"))
    try:
        result = scroll_saver.save_scroll_result(stitcher, options, "manual", chunked=True)
    finally:
        stitcher.close()
    np.testing.assert_array_equal(result, image)
    assert not list(tmp_path.iterdir())


def test_original_pinned_window_receives_full_image_without_extra_copy(tmp_path, monkeypatch):
    from PyQt6.QtGui import QImage
    from PyQt6.QtWidgets import QApplication
    from deepcat.ui.main_window import capture
    from deepcat.ui import pinned_image_window
    from deepcat.ui.scroll_result import ScrollResultPrepareWorker

    app = QApplication.instance() or QApplication([])
    image = np.full((17001, 12, 3), (10, 30, 90), np.uint8)
    payloads = []
    worker = ScrollResultPrepareWorker(image, False, None)
    worker.prepared.connect(payloads.append)
    worker.run()
    payloads[0]["source_paths"] = [str(tmp_path / "whole.png")]
    captured = {}

    class Pinned:
        def __init__(self, qimage, **kwargs):
            captured.update(qimage=qimage, **kwargs)

        def show(self):
            captured["shown"] = True

        def raise_(self):
            pass

        def activateWindow(self):
            pass

    dummy = SimpleNamespace(
        devicePixelRatioF=lambda: 1.0,
        _current_format=lambda: "png",
        _cfg=SimpleNamespace(JPG_QUALITY=95),
        _last_frame_index=3,
        _send_tray_notification=lambda *args: None,
        _finish_scroll_capture_cleanup=lambda: None,
    )
    monkeypatch.setattr(capture, "get_image_output_dir", lambda **kwargs: tmp_path)
    monkeypatch.setattr(pinned_image_window, "PinnedImageWindow", Pinned)
    capture.CaptureMixin._on_scroll_result_prepared(dummy, payloads[0])
    assert captured["shown"]
    assert captured["image_bgr"] is image
    assert isinstance(captured["qimage"], QImage)
    assert captured["qimage"].size() == payloads[0]["qimage"].size()
    assert captured["qimage"].height() == 17001
    assert captured["qimage"].pixelColor(0, 0).getRgb()[:3] == (90, 30, 10)
