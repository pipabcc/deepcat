from __future__ import annotations

import sys
from types import SimpleNamespace


def test_region_capture_entry_enables_frozen_background(monkeypatch):
    try:
        import deepcat.ui.main_window as mw
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    captured: dict[str, object] = {}
    calls = {"hide": 0, "flush": 0, "minimize": 0}

    class FakeQApplication:
        @staticmethod
        def activeModalWidget():
            return None

    class FakeQTimer:
        @staticmethod
        def singleShot(*args, **kwargs):
            return None

    monkeypatch.setattr(mw, "QApplication", FakeQApplication)
    monkeypatch.setattr(mw, "QTimer", FakeQTimer)

    def capture_select_region(on_confirmed=None, **kwargs):
        captured.update(kwargs)

    def hide_main_window():
        calls["hide"] += 1
        dummy._visible = False

    def flush_hide():
        calls["flush"] += 1

    def show_minimized():
        calls["minimize"] += 1

    def close_later_read_probe(*, restore_cursor=True):
        captured["probe_restore_cursor"] = bool(restore_cursor)
        dummy._later_read_probe_overlay = None

    dummy = SimpleNamespace(
        _thread=None,
        _cat_reminder_session=None,
        _todo_popup=None,
        _region_overlay=None,
        _later_read_probe_overlay=object(),
        _app_settings=SimpleNamespace(ui={}),
        _region_capture_token=0,
        _cancelled_region_capture_token=-1,
        _stop_left_click_retake_listener=lambda: None,
        _hide_settings_for_capture=lambda: None,
        _handle_previous_capture_before_new_capture=lambda: None,
        _previous_capture_action=lambda: "close",
        _clear_stashed_captures=lambda: None,
        _restore_normal_cursor=lambda: None,
        _close_later_read_probe_overlay=close_later_read_probe,
        _set_selection_translate_suspended=lambda value: None,
        _visible=True,
        isVisible=lambda: dummy._visible,
        hide=hide_main_window,
        showMinimized=show_minimized,
        _flush_hide_for_capture=flush_hide,
        _hide_for_capture_without_animation=lambda: mw.MainWindow._hide_for_capture_without_animation(dummy),
        _select_region=capture_select_region,
        _preload_region_capture_tools=lambda: None,
    )

    mw.MainWindow._start_capture_clicked(dummy, mode_override="框选截图")

    assert captured["freeze_on_start"] is True
    assert captured["show_frozen_background"] is True
    assert captured["immediate_on_confirmed"] is True
    assert captured["close_on_confirm"] is False
    assert captured["probe_restore_cursor"] is False
    assert dummy._later_read_probe_overlay is None
    assert calls["hide"] == 0
    assert calls["flush"] == 1
    assert calls["minimize"] == 1


def test_capture_transition_release_closes_confirmed_overlay():
    try:
        import deepcat.ui.main_window as mw
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    calls = {"close": 0}

    class FakeOverlay:
        _confirmed = True

        def close(self):
            calls["close"] += 1

    overlay = FakeOverlay()
    dummy = SimpleNamespace(
        _capture_transition_pending=True,
        _region_overlay=overlay,
        _border_overlay=None,
        _prepare_selection_shade_handoff=lambda _overlay: None,
    )

    mw.MainWindow._release_capture_transition_overlay(dummy)

    assert calls["close"] == 1
    assert dummy._region_overlay is None
    assert dummy._capture_transition_pending is False


def test_capture_transition_keeps_confirmed_overlay_as_live_shade(monkeypatch):
    try:
        import deepcat.ui.main_window as mw
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    events: list[str] = []
    scheduled: list[tuple[int, object]] = []

    class FakeTimer:
        @staticmethod
        def singleShot(delay_ms, callback):
            scheduled.append((int(delay_ms), callback))

    class FakeOverlay:
        _confirmed = True

        def set_selection_visual_visible(self, visible):
            events.append(f"overlay_visual:{visible}")

        def repaint(self):
            events.append("overlay_repaint")

        def close(self):
            events.append("overlay_close")

    class FakeWidget:
        def __init__(self, name: str) -> None:
            self._name = name
            self._on_first_paint = None

        def repaint(self):
            events.append(f"{self._name}_repaint")
            callback = self._on_first_paint
            if callback is not None:
                self._on_first_paint = None
                callback()

    overlay = FakeOverlay()
    dummy = SimpleNamespace(
        _capture_transition_pending=True,
        _region_overlay=overlay,
        _border_overlay=FakeWidget("border"),
        _selection_shade_overlay=FakeWidget("shade"),
        _on_shade_first_paint=lambda: mw.MainWindow._on_shade_first_paint(dummy),
        _sync_selection_shade_for_border=lambda: events.append("shade_sync"),
        _flush_fast_ui=lambda _ms=1: events.append("flush"),
    )
    monkeypatch.setattr(mw, "QTimer", FakeTimer)
    mw.MainWindow._release_capture_transition_overlay(dummy)

    assert "overlay_visual:False" in events
    assert "shade_sync" in events
    assert "overlay_close" not in events
    assert "flush" not in events
    assert not scheduled
    assert dummy._region_overlay is overlay


def test_selection_shade_handoff_hides_old_visual_after_first_paint(monkeypatch):
    try:
        import deepcat.ui.main_window as mw
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    events: list[str] = []
    scheduled: list[tuple[int, object]] = []

    class FakeTimer:
        @staticmethod
        def singleShot(delay_ms, callback):
            scheduled.append((int(delay_ms), callback))

    class FakeOverlay:
        def set_selection_visual_visible(self, visible):
            events.append(f"overlay_visual:{visible}")

        def repaint(self):
            events.append("overlay_repaint")

    class FakeWidget:
        def __init__(self, name: str) -> None:
            self._name = name
            self._on_first_paint = None

        def repaint(self):
            events.append(f"{self._name}_repaint")
            callback = self._on_first_paint
            if callback is not None:
                self._on_first_paint = None
                callback()

    overlay = FakeOverlay()
    dummy = SimpleNamespace(
        _region_overlay=overlay,
        _border_overlay=FakeWidget("border"),
        _selection_shade_overlay=FakeWidget("shade"),
        _on_shade_first_paint=lambda: mw.MainWindow._on_shade_first_paint(dummy),
        _schedule_region_overlay_visual_hide=lambda target, delay_ms=32: mw.MainWindow._schedule_region_overlay_visual_hide(
            dummy, target, delay_ms
        ),
        _hide_region_overlay_visual_if_current=lambda target: mw.MainWindow._hide_region_overlay_visual_if_current(
            dummy, target
        ),
        _hide_overlay_visual_safe=lambda target: target.set_selection_visual_visible(False),
        _sync_selection_shade_for_border=lambda: events.append("shade_sync"),
        _flush_fast_ui=lambda _ms=1: events.append("flush"),
    )

    monkeypatch.setattr(mw.capture, "QTimer", FakeTimer)

    mw.MainWindow._prepare_selection_shade_handoff(dummy, overlay)

    assert "overlay_visual:False" not in events
    assert scheduled
    for _, callback in list(scheduled):
        callback()

    assert "overlay_visual:False" in events


def test_region_overlay_shade_tracks_border_resize(monkeypatch):
    try:
        from PyQt6.QtCore import QRect
        import deepcat.ui.main_window as mw
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    events: list[str] = []

    class FakeSignal:
        def __init__(self) -> None:
            self.callbacks: list[object] = []

        def connect(self, callback) -> None:
            self.callbacks.append(callback)

        def emit(self, *args) -> None:
            for callback in list(self.callbacks):
                callback(*args)

    class FakeRegionOverlay:
        def __init__(self) -> None:
            self.rects: list[QRect] = []

        def set_selection_rect(self, rect) -> None:
            self.rects.append(QRect(rect))

        def set_selection_visual_visible(self, visible) -> None:
            events.append(f"overlay_visual:{visible}")

        def isHidden(self) -> bool:
            return False

        def show(self) -> None:
            events.append("overlay_show")

    class FakeBorder:
        def __init__(self) -> None:
            self.destroyed = FakeSignal()
            self.rect_changed = FakeSignal()
            self.rect_released = FakeSignal()

        def isHidden(self) -> bool:
            return False

        def show(self) -> None:
            events.append("border_show")

        def raise_(self) -> None:
            events.append("border_raise")

    region_overlay = FakeRegionOverlay()
    border = FakeBorder()
    dummy = SimpleNamespace(
        _region_overlay=region_overlay,
        _border_overlay=border,
        _selection_shade_overlay=None,
        _selection_shade_bound_border=None,
        _post_actions=None,
        _close_selection_shade_overlay=lambda: events.append("shade_close"),
    )
    dummy._sync_selection_shade_for_border = lambda rect=None: mw.MainWindow._sync_selection_shade_for_border(dummy, rect)
    dummy._bind_selection_shade_to_border = lambda target: mw.MainWindow._bind_selection_shade_to_border(dummy, target)

    mw.MainWindow._sync_selection_shade_for_border(dummy, QRect(10, 20, 30, 40))
    assert dummy._selection_shade_bound_border is border
    assert len(border.rect_changed.callbacks) == 1
    assert region_overlay.rects[-1] == QRect(10, 20, 30, 40)

    border.rect_changed.emit(QRect(12, 22, 33, 44))
    assert region_overlay.rects[-1] == QRect(12, 22, 33, 44)
    assert "shade_close" not in events


def test_transition_region_overlay_keeps_border_visual_until_release(monkeypatch):
    try:
        from PyQt6.QtCore import QRect
        import deepcat.ui.main_window as mw
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    events: list[str] = []

    class FakeSignal:
        def connect(self, _callback) -> None:
            return None

    class FakeRegionOverlay:
        def set_selection_rect(self, rect) -> None:
            events.append(f"overlay_rect:{int(QRect(rect).width())}x{int(QRect(rect).height())}")

        def set_selection_visual_visible(self, visible) -> None:
            events.append(f"overlay_visual:{visible}")

        def isHidden(self) -> bool:
            return False

        def show(self) -> None:
            events.append("overlay_show")

    class FakeBorder:
        def __init__(self) -> None:
            self.destroyed = FakeSignal()
            self.rect_changed = FakeSignal()
            self.rect_released = FakeSignal()

        def isHidden(self) -> bool:
            return False

        def show(self) -> None:
            events.append("border_show")

        def raise_(self) -> None:
            events.append("border_raise")

    dummy = SimpleNamespace(
        _capture_transition_pending=True,
        _region_overlay=FakeRegionOverlay(),
        _border_overlay=FakeBorder(),
        _selection_shade_overlay=None,
        _selection_shade_bound_border=None,
        _post_actions=None,
        _close_selection_shade_overlay=lambda: events.append("shade_close"),
    )
    dummy._bind_selection_shade_to_border = lambda target: mw.MainWindow._bind_selection_shade_to_border(dummy, target)

    mw.MainWindow._sync_selection_shade_for_border(dummy, QRect(10, 20, 30, 40))

    assert "overlay_rect:30x40" in events
    assert "overlay_visual:True" in events
    assert "overlay_visual:False" not in events
    assert "shade_close" not in events


def test_region_confirm_primes_shade_during_transition(monkeypatch):
    try:
        import deepcat.ui.main_window as mw
        import deepcat.ui.region_overlay as ro
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    events: list[str] = []
    captured: dict[str, object] = {}

    class FakeSignal:
        def __init__(self) -> None:
            self._callback = None

        def connect(self, callback):
            self._callback = callback
            return None

        def emit(self, *args):
            if self._callback is not None:
                self._callback(*args)

    class FakeOverlay:
        def __init__(self, **kwargs) -> None:
            captured["overlay_kwargs"] = kwargs
            self.confirmed = FakeSignal()
            self.canceled = FakeSignal()
            self.destroyed = FakeSignal()

        def setAttribute(self, *args, **kwargs):
            return None

        def show_capture_overlay(self):
            events.append("overlay_show")

        def activateWindow(self):
            return None

        def raise_(self):
            events.append("overlay_raise")

        def set_selection_visual_visible(self, visible):
            events.append(f"overlay_visual:{visible}")

        def repaint(self):
            events.append("overlay_repaint")

    class FakeBorder:
        def __init__(self, rect, interactive=False) -> None:
            events.append(f"border_create:{bool(interactive)}")
            self.destroyed = FakeSignal()
            self.rect_changed = FakeSignal()

        def show(self):
            events.append("border_show")

        def raise_(self):
            events.append("border_raise")

        def repaint(self):
            events.append("border_repaint")

        def close(self):
            events.append("border_close")

    class FakeTimer:
        @staticmethod
        def singleShot(*args, **kwargs):
            return None

    monkeypatch.setattr(ro, "RegionOverlay", FakeOverlay)
    monkeypatch.setattr(mw.capture, "SelectionBorderOverlay", FakeBorder)
    monkeypatch.setattr(mw.capture, "QTimer", FakeTimer)
    monkeypatch.setattr(mw.NotificationPopup, "raise_active_popups", lambda: None)

    dummy = SimpleNamespace(
        _region_overlay=None,
        _border_overlay=None,
        _capture_region=None,
        _capture_region_logical=None,
        _capture_frozen_screen_bgr=None,
        _capture_frozen_screen_origin_px=(0, 0),
        _capture_transition_pending=False,
        _current_annotation_style=lambda: {},
        _apply_annotation_style_to_border=lambda: events.append("apply_style"),
        _connect_border_escape=lambda: events.append("connect_escape"),
        _close_selection_shade_overlay=lambda: events.append("shade_close"),
        _prepare_selection_shade_handoff=lambda overlay: events.append("handoff"),
        _sync_selection_shade_for_border=lambda: events.append("shade_sync"),
        _flush_fast_ui=lambda _ms=1: events.append("flush"),
        _start_right_click_cancel_listener=lambda: events.append("right_click_listener"),
        _arm_left_click_finish_window=lambda: events.append("finish_click"),
        _cleanup_failed_region_selection=lambda: events.append("cleanup_failed"),
    )

    mw.MainWindow._select_region(
        dummy,
        on_confirmed=lambda: events.append("confirmed_callback"),
        show_border=True,
        border_interactive=True,
        start_cancel_listener=True,
        arm_finish_click=False,
        close_on_confirm=False,
        freeze_on_start=True,
        show_frozen_background=True,
        immediate_on_confirmed=True,
    )

    overlay = dummy._region_overlay
    overlay.confirmed.emit(
        SimpleNamespace(
            as_tuple=lambda: (10, 20, 30, 40),
            logical_tuple=lambda: (10, 20, 30, 40),
            frozen_screen_bgr="frozen",
            frozen_screen_left=0,
            frozen_screen_top=0,
        )
    )

    assert dummy._capture_transition_pending is True
    assert "handoff" in events
    assert events.index("handoff") < events.index("confirmed_callback")
    assert "shade_sync" not in events


def test_post_capture_toolbar_bottom_region_positions_above():
    try:
        from PyQt6.QtCore import QRect, QSize
        from deepcat.ui.post_capture_actions import PostCaptureActions
    except Exception:
        import pytest

        pytest.skip("PyQt6 is unavailable")

    screen_geo = QRect(0, 0, 800, 600)
    bottom_region = QRect(120, 548, 240, 48)

    toolbar_geo = PostCaptureActions.toolbar_geometry_for_region(bottom_region, QSize(620, 60), screen_geo)

    assert toolbar_geo.top() < bottom_region.top()
    assert toolbar_geo.bottom() <= screen_geo.bottom()


def test_crop_frozen_region_uses_frozen_frame_copy():
    try:
        import numpy as np
        import deepcat.ui.main_window as mw
    except Exception:
        import pytest

        pytest.skip("required UI dependencies are unavailable")

    frozen = np.arange(5 * 6 * 3, dtype=np.uint8).reshape((5, 6, 3))
    dummy = SimpleNamespace(
        _capture_frozen_screen_bgr=frozen,
        _capture_frozen_screen_origin_px=(10, 20),
    )

    cropped = mw.MainWindow._crop_frozen_region_bgr(dummy, (12, 21, 3, 2))

    assert np.array_equal(cropped, frozen[1:3, 2:5, :3])
    assert cropped is not None
    cropped[0, 0, 0] = 255
    assert frozen[1, 2, 0] != 255


def test_crop_frozen_region_accepts_bgra_freeze_frame():
    try:
        import numpy as np
        import deepcat.ui.main_window as mw
    except Exception:
        import pytest

        pytest.skip("required UI dependencies are unavailable")

    frozen = np.arange(5 * 6 * 4, dtype=np.uint8).reshape((5, 6, 4))
    dummy = SimpleNamespace(
        _capture_frozen_screen_bgr=frozen,
        _capture_frozen_screen_origin_px=(10, 20),
    )

    cropped = mw.MainWindow._crop_frozen_region_bgr(dummy, (12, 21, 3, 2))

    assert np.array_equal(cropped, frozen[1:3, 2:5, :3])
    assert cropped.shape == (2, 3, 3)


def test_freeze_snapshot_keeps_screen_device_pixel_ratio(monkeypatch):
    try:
        import numpy as np
        import pytest
        from PyQt6.QtGui import QCursor, QGuiApplication
        from PyQt6.QtWidgets import QApplication

        import deepcat.ui.region_overlay as region_module
        from deepcat.ui.region_overlay import RegionOverlay
    except Exception:
        import pytest

        pytest.skip("required UI dependencies are unavailable")

    app = QApplication.instance() or QApplication(sys.argv)
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        pytest.skip("primary screen is unavailable")

    captured_monitor: dict[str, int] = {}

    class FakeMss:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def grab(self, monitor):
            captured_monitor.update({key: int(value) for key, value in monitor.items()})
            return np.zeros((int(monitor["height"]), int(monitor["width"]), 4), dtype=np.uint8)

    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=lambda: FakeMss()))

    overlay = RegionOverlay(
        freeze_on_start=True,
        show_frozen_background=True,
        show_magnifier=False,
        link_probe_enabled=False,
    )

    try:
        dpr = float(region_module._mapping_for_logical_point(QCursor.pos(), overlay._screen_mappings).dpr)
        physical_geo = overlay._screen_mappings[0].physical
        for mapping in overlay._screen_mappings[1:]:
            physical_geo = physical_geo.united(mapping.physical)
        expected_width = int(max(1, physical_geo.width()))
        expected_height = int(max(1, physical_geo.height()))

        assert captured_monitor["width"] == expected_width
        assert captured_monitor["height"] == expected_height
        assert overlay._freeze_qimage.devicePixelRatio() == pytest.approx(dpr)
        assert overlay._freeze_pixmap.devicePixelRatio() == pytest.approx(dpr)
        assert overlay._freeze_pixmap.deviceIndependentSize().width() == pytest.approx(expected_width / dpr)
        assert overlay._freeze_pixmap.deviceIndependentSize().height() == pytest.approx(expected_height / dpr)
    finally:
        overlay.close()
        app.processEvents()


def test_regular_region_overlay_does_not_start_idle_cursor_polling():
    try:
        import pytest
        from PyQt6.QtWidgets import QApplication

        from deepcat.ui.region_overlay import RegionOverlay
    except Exception:
        import pytest

        pytest.skip("required UI dependencies are unavailable")

    app = QApplication.instance() or QApplication(sys.argv)
    overlay = RegionOverlay(
        auto_snap=False,
        show_magnifier=False,
        link_probe_enabled=False,
    )

    try:
        assert not overlay._external_cursor_timer.isActive()
        assert overlay._snap_timer.isSingleShot()
        assert not overlay._snap_timer.isActive()
    finally:
        overlay.close()
        app.processEvents()


def test_logical_rect_to_physical_tuple_uses_per_screen_dpi():
    try:
        import pytest
        from PyQt6.QtCore import QRect

        from deepcat.ui.region_overlay import _build_screen_dpi_mappings, logical_rect_to_physical_tuple
    except Exception:
        import pytest

        pytest.skip("required UI dependencies are unavailable")

    class FakeScreen:
        def __init__(self, rect: QRect, dpr: float) -> None:
            self._rect = QRect(rect)
            self._dpr = float(dpr)

        def geometry(self) -> QRect:
            return QRect(self._rect)

        def devicePixelRatio(self) -> float:
            return self._dpr

    mappings = _build_screen_dpi_mappings(
        [
            FakeScreen(QRect(0, 0, 100, 100), 1.0),
            FakeScreen(QRect(100, 0, 100, 100), 1.5),
        ]
    )

    assert logical_rect_to_physical_tuple(QRect(120, 10, 20, 10), mappings) == (180, 15, 30, 15)
    assert logical_rect_to_physical_tuple(QRect(90, 10, 20, 10), mappings) == (90, 10, 75, 20)
