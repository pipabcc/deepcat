"""限制内部片段缓存，同时保持原图预览和完整输出。"""

import threading
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from deepcat.core.stitcher import PiecewiseStitcher
from deepcat.ui import scroll_result


@pytest.mark.parametrize("reverse", [False, True])
def test_spilled_pieces_preserve_pixels_after_trim_and_width_change(reverse):
    stitcher = PiecewiseStitcher(max_memory_bytes=120)
    rng = np.random.default_rng(7)
    first = rng.integers(0, 255, (10, 8, 3), dtype=np.uint8)
    second = rng.integers(0, 255, (9, 6, 3), dtype=np.uint8)
    try:
        stitcher._append_piece(first)
        stitcher._trim_bottom(2)
        stitcher._append_piece(second)
        stitcher._rebuild_tail(9)
        expected = np.concatenate([first[:-2, :6], second])
        if reverse:
            expected = expected[::-1]
        assert stitcher.memory_profile().piece_bytes <= 120
        np.testing.assert_array_equal(stitcher.result_image(vertical_flip=reverse), expected)
        chunks = list(stitcher.iter_result_chunks(4, vertical_flip=reverse))
        np.testing.assert_array_equal(np.concatenate(chunks), expected)
        stream = stitcher._pieces._stream
    finally:
        stitcher.close()
    assert stream.closed


def save_part(path, shape, value):
    image = np.full(shape, value, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    path.write_bytes(encoded.tobytes())
    return str(path)


def test_saved_preview_keeps_all_pixels_and_image_copy(tmp_path):
    paths = [save_part(tmp_path / f"{i}.png", (80, 40, 3), i * 80) for i in range(3)]
    results = []
    worker = scroll_result.ScrollResultPrepareWorker(paths, True, None)
    worker.prepared.connect(results.append)
    worker.run()
    result = results[0]
    assert result["image_bgr"].shape == (240, 40, 3)
    assert result["source_paths"] == paths
    assert result["original_size"] == (40, 240)
    assert result["copy_on_capture"] is True
    assert result["preview_scaled"] is False
    assert result["qimage"].width() == 40
    assert result["qimage"].height() == 240
    assert np.shares_memory(result["rgb"], result["image_bgr"])
    for i in range(3):
        assert np.all(result["image_bgr"][i * 80 : (i + 1) * 80] == i * 80)
    assert [cv2.imdecode(np.fromfile(p, np.uint8), 1).shape for p in paths] == [(80, 40, 3)] * 3


def test_preview_exceeding_old_pixel_and_height_limits_stays_original(tmp_path):
    path = save_part(tmp_path / "large.png", (18000, 640, 3), 99)
    image, size, scaled = scroll_result.load_saved_preview([path], lambda: False)
    assert image.shape == (18000, 640, 3)
    assert image.nbytes > 32 * 1024 * 1024
    assert size == (640, 18000) and not scaled
    assert np.all(image == 99)


def capture_settings():
    from deepcat.ui.capture_worker import CaptureSettings

    return CaptureSettings(
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
        output_format="png",
        jpg_quality=95,
        auto_save=False,
    )


def fake_capture(monkeypatch):
    import sys
    from deepcat.ui import capture_worker

    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=lambda: SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(capture_worker, "capture_screen", lambda *a, **k: np.zeros((8, 8, 3), dtype=np.uint8))
    monkeypatch.setattr(capture_worker, "scroll_down", lambda *a, **k: None)
    return capture_worker


def test_capture_elapsed_uses_one_clock(monkeypatch):
    from deepcat.input.hotkey_listener import HotkeyListener

    module = fake_capture(monkeypatch)
    monkeypatch.setattr(HotkeyListener, "start", lambda self: None)
    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: 20.0, time=lambda: 1_800_000_000, sleep=lambda _: None)
    )
    values = []
    worker = module.CaptureWorker(capture_settings())
    worker.metrics.connect(lambda frame, height, elapsed: values.append(elapsed))
    worker.run()
    assert values == [0]


def test_cancel_capture_while_paused_exits_worker(monkeypatch):
    from deepcat.input.hotkey_listener import HotkeyListener

    module = fake_capture(monkeypatch)
    entered = threading.Event()
    original_wait = HotkeyListener.wait_if_paused

    def start(self):
        self.pause_event.set()

    def wait(self):
        entered.set()
        original_wait(self)

    monkeypatch.setattr(HotkeyListener, "start", start)
    monkeypatch.setattr(HotkeyListener, "wait_if_paused", wait)
    worker = module.CaptureWorker(capture_settings())
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    try:
        assert entered.wait(2)
        worker.request_cancel()
        thread.join(1)
        assert not thread.is_alive()
    finally:
        worker.request_cancel()
        thread.join(2)
