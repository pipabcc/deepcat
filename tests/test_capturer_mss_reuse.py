from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np


class FakeMssInstance:
    def __init__(self) -> None:
        self.monitors = [None, {"left": 0, "top": 0, "width": 4, "height": 3}]
        self.grabbed: list[dict[str, int]] = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False

    def close(self) -> None:
        self.closed = True

    def grab(self, monitor):
        monitor = dict(monitor)
        self.grabbed.append(monitor)
        return np.zeros((int(monitor["height"]), int(monitor["width"]), 4), dtype=np.uint8)


def test_capture_screen_reuses_supplied_mss_instance(monkeypatch):
    from deepcat.core.capturer import capture_screen

    created: list[FakeMssInstance] = []

    def make_mss():
        inst = FakeMssInstance()
        created.append(inst)
        return inst

    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=make_mss))
    supplied = FakeMssInstance()

    image = capture_screen((10, 20, 4, 3), sct=supplied)

    assert created == []
    assert supplied.grabbed == [{"left": 10, "top": 20, "width": 4, "height": 3}]
    assert image.shape == (3, 4, 3)


def test_capture_worker_holds_one_mss_instance_for_run(monkeypatch):
    import deepcat.ui.capture_worker as capture_worker
    from deepcat.ui.capture_worker import CaptureSettings, CaptureWorker

    created: list[FakeMssInstance] = []
    seen_sct: list[object] = []

    def make_mss():
        inst = FakeMssInstance()
        created.append(inst)
        return inst

    class FakeHotkeyListener:
        def __init__(self, *args, **kwargs) -> None:
            self.cleaned = False

        def start(self) -> None:
            return None

        def cleanup(self) -> None:
            self.cleaned = True

        def should_stop(self) -> bool:
            return False

        def wait_if_paused(self) -> None:
            return None

    class FakeStitcher:
        def __init__(self) -> None:
            self.total_height = 0
            self._frame = None

        @property
        def is_empty(self) -> bool:
            return self._frame is None

        def add(self, frame):
            self._frame = frame
            self.total_height = int(frame.shape[0])
            return "successful"

        def result_image(self):
            return self._frame

    def fake_capture_screen(region=None, *, sct=None):
        seen_sct.append(sct)
        return np.zeros((3, 4, 3), dtype=np.uint8)

    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=make_mss))
    monkeypatch.setattr(capture_worker, "HotkeyListener", FakeHotkeyListener)
    monkeypatch.setattr(capture_worker, "PiecewiseStitcher", FakeStitcher)
    monkeypatch.setattr(capture_worker, "capture_screen", fake_capture_screen)
    monkeypatch.setattr(capture_worker, "scroll_down", lambda *args, **kwargs: None)

    settings = CaptureSettings(
        region=None,
        scroll_delay=0.0,
        scroll_amount=1,
        scroll_method="mouse_wheel",
        max_frames=1,
        adaptive_wait=False,
        boost_scroll=False,
        reverse_scroll=False,
        merge_pdf=False,
        merge_image=False,
        dual_output=False,
        strip_height=0,
        min_confidence=0.0,
        bottom_diff_mean_threshold=0.0,
        bottom_confirm_count=1,
        detect_fixed_header=False,
        detect_fixed_footer=False,
        output_format="png",
        jpg_quality=95,
        auto_save=False,
    )
    worker = CaptureWorker(settings)
    finished: list[object] = []
    worker.finished.connect(finished.append)

    worker.run()

    assert len(created) == 1
    assert seen_sct == [created[0]]
    assert created[0].closed is True
    assert finished
    assert isinstance(finished[0].result, np.ndarray)
    assert finished[0].status.value == "partial_success"
