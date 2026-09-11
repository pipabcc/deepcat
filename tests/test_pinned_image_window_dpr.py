from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest


def test_pinned_image_preserves_device_pixel_ratio_for_one_to_one_display():
    try:
        from PyQt6.QtGui import QGuiApplication, QPixmap
        from PyQt6.QtWidgets import QApplication

        from deepcat.ui.pinned_image_window import PinnedImageWindow
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication(sys.argv)
    screen = QGuiApplication.primaryScreen()
    dpr = float(screen.devicePixelRatio() if screen is not None else 1.0)
    physical_width = max(1, round(120 * dpr))
    physical_height = max(1, round(48 * dpr))
    pixmap = QPixmap(physical_width, physical_height)
    pixmap.setDevicePixelRatio(dpr)
    image_bgr = np.zeros((physical_height, physical_width, 3), dtype=np.uint8)

    window = PinnedImageWindow(
        pixmap,
        image_bgr=image_bgr,
        default_dir="",
        default_format="png",
        jpg_quality=95,
    )

    try:
        expected_size = window._orig_pix.deviceIndependentSize()
        assert window._orig_pix.devicePixelRatio() == pytest.approx(dpr)
        assert window._display_pixmap().devicePixelRatio() == pytest.approx(dpr)
        assert window._label.width() == int(expected_size.width())
        assert window._label.height() == int(expected_size.height())
        assert window._scale_badge.text() == "100%"
        assert not window._scale_badge.isVisible()
    finally:
        window.close()
        app.processEvents()


def test_pinned_image_text_editor_caret_top_right_stays_on_click_point():
    try:
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QPixmap
        from PyQt6.QtWidgets import QApplication

        from deepcat.ui.pinned_image_window import PinnedImageWindow
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication(sys.argv)
    pixmap = QPixmap(160, 96)
    image_bgr = np.zeros((96, 160, 3), dtype=np.uint8)
    window = PinnedImageWindow(
        pixmap,
        image_bgr=image_bgr,
        default_dir="",
        default_format="png",
        jpg_quality=95,
    )

    try:
        click_point = QPoint(72, 72)
        window._begin_text_editor(click_point)
        app.processEvents()

        editor = window._text_editor
        assert editor is not None
        caret_top_right = editor.mapTo(window._label, editor.cursorRect().topRight())
        label_point = window._label.mapFromGlobal(window.mapToGlobal(click_point))
        expected_top_y = int(label_point.y() - round(float(editor.cursorRect().height()) / 2.0))
        assert abs(caret_top_right.x() - label_point.x()) <= 1
        assert abs(caret_top_right.y() - expected_top_y) <= 1

        class DummySignal:
            def emit(self):
                pass

        overlay = SimpleNamespace(
            _commands=[],
            _redo=[],
            update=lambda: None,
            changed=DummySignal(),
        )
        window._edit_actions = SimpleNamespace(_annotation_overlay=overlay)
        editor.setText("Hi")
        editor.setCursorPosition(0)
        start_rect = editor.cursorRect()
        text_start_before_commit = editor.mapTo(
            window._label,
            QPoint(
                int(start_rect.x() + round(float(start_rect.width()) / 2.0)),
                int(start_rect.y()),
            ),
        )
        editor.setCursorPosition(len(editor.text()))
        window._commit_text_editor()

        assert len(overlay._commands) == 1
        saved_x, saved_y = overlay._commands[0]["pos"]
        saved_point = QPoint(
            int(round(float(saved_x) * float(window._label.width()))),
            int(round(float(saved_y) * float(window._label.height()))),
        )
        assert abs(saved_point.x() - text_start_before_commit.x()) <= 1
        assert abs(saved_point.y() - text_start_before_commit.y()) <= 1
    finally:
        window.close()
        app.processEvents()


def test_stitch_finalize_passes_device_pixel_ratio_to_pinned_image(monkeypatch):
    try:
        import deepcat.ui.main_window as mw
        import deepcat.ui.pinned_image_window as pinned_module
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    captured: dict[str, object] = {}

    class FakePinnedImageWindow:
        def __init__(self, image, **kwargs) -> None:
            captured["dpr"] = float(image.devicePixelRatio())
            captured["size"] = (int(image.width()), int(image.height()))
            captured["image_bgr"] = kwargs.get("image_bgr")

        def show(self) -> None:
            captured["shown"] = True

        def raise_(self) -> None:
            captured["raised"] = True

        def activateWindow(self) -> None:
            captured["activated"] = True

    monkeypatch.setattr(pinned_module, "PinnedImageWindow", FakePinnedImageWindow)
    monkeypatch.setattr(mw, "get_image_output_dir", lambda: "")

    image_bgr = np.zeros((24, 48, 3), dtype=np.uint8)
    dummy = SimpleNamespace(
        _stitch_buffer_bgr=image_bgr,
        _stitch_buffer_dpr=2.0,
        _current_format=lambda: "png",
        _cfg=SimpleNamespace(JPG_QUALITY=95),
        _send_tray_notification=lambda *args, **kwargs: None,
        _border_overlay=None,
        _close_selection_shade_overlay=lambda: None,
        _region_overlay=None,
        _capture_region=None,
        _capture_region_logical=None,
        devicePixelRatioF=lambda: 1.0,
        _clear_stitch_buffer=lambda: mw.MainWindow._clear_stitch_buffer(dummy),
        _defer_show_stashed_captures=lambda: None,
        close=lambda: captured.update(closed=True),
    )

    mw.MainWindow._finalize_stitch_buffer(dummy)

    assert captured["dpr"] == pytest.approx(2.0)
    assert captured["size"] == (48, 24)
    assert captured["image_bgr"] is image_bgr
    assert captured["shown"] is True
    assert dummy._stitch_buffer_bgr is None
    assert dummy._stitch_buffer_dpr == pytest.approx(0.0)


def test_post_close_after_manual_pin_consumes_stitch_buffer_without_auto_pin(monkeypatch):
    try:
        import deepcat.ui.main_window as mw
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    finalized: list[str] = []
    monkeypatch.setattr(mw.MainWindow, "_finalize_stitch_buffer", lambda self: finalized.append("finalize"))

    dummy = SimpleNamespace(
        _post_actions=SimpleNamespace(_last_pinned=object()),
        _stitch_buffer_bgr=np.zeros((12, 24, 3), dtype=np.uint8),
        _stitch_buffer_dpr=2.0,
    )
    dummy._post_actions_has_manual_pin = lambda: mw.MainWindow._post_actions_has_manual_pin(dummy)
    dummy._finalize_stitch_buffer = lambda: finalized.append("finalize")
    dummy._clear_stitch_buffer = lambda: mw.MainWindow._clear_stitch_buffer(dummy)
    dummy._defer_show_stashed_captures = lambda: None

    mw.MainWindow._finalize_or_clear_stitch_buffer_for_post_close(dummy)

    assert finalized == []
    assert dummy._stitch_buffer_bgr is None
    assert dummy._stitch_buffer_dpr == pytest.approx(0.0)


def test_post_close_without_manual_pin_still_finalizes_stitch_buffer(monkeypatch):
    try:
        import deepcat.ui.main_window as mw
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    finalized: list[str] = []
    monkeypatch.setattr(mw.MainWindow, "_finalize_stitch_buffer", lambda self: finalized.append("finalize"))

    dummy = SimpleNamespace(
        _post_actions=SimpleNamespace(_last_pinned=None),
        _stitch_buffer_bgr=np.zeros((12, 24, 3), dtype=np.uint8),
        _stitch_buffer_dpr=2.0,
    )
    dummy._post_actions_has_manual_pin = lambda: mw.MainWindow._post_actions_has_manual_pin(dummy)
    dummy._finalize_stitch_buffer = lambda: finalized.append("finalize")

    mw.MainWindow._finalize_or_clear_stitch_buffer_for_post_close(dummy)

    assert finalized == ["finalize"]
    assert dummy._stitch_buffer_bgr is not None
    assert dummy._stitch_buffer_dpr == pytest.approx(2.0)


def test_previous_capture_stash_stores_image_before_next_capture(monkeypatch):
    try:
        import deepcat.ui.main_window as mw
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    events: list[str] = []
    image_bgr = np.full((12, 24, 3), 37, dtype=np.uint8)

    class FakeQApplication:
        @staticmethod
        def processEvents():
            events.append("process")

    class FakeRect:
        def left(self):
            return 0

        def top(self):
            return 0

        def width(self):
            return 12

        def height(self):
            return 6

    class FakePostActions:
        _region = FakeRect()
        _region_px = (0, 0, 24, 12)

        def export_image_bgr(self):
            events.append("export")
            return image_bgr

        def close(self):
            events.append("post_close")

    class FakeWidget:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            events.append(f"{self.name}_close")

    monkeypatch.setattr(mw, "QApplication", FakeQApplication)

    post_actions = FakePostActions()
    dummy = SimpleNamespace(
        _post_actions=post_actions,
        _border_overlay=FakeWidget("border"),
        _region_overlay=FakeWidget("region"),
        _stashed_capture_items=[],
        _capture_region=(0, 0, 24, 12),
        _capture_region_logical=(0, 0, 24, 12),
        _previous_capture_action=lambda: "stash",
        _stop_left_click_retake_listener=lambda: events.append("stop_left"),
        _continuous_retake_suppressed=True,
        _close_selection_shade_overlay=lambda: events.append("shade_close"),
        _restore_normal_cursor=lambda: events.append("cursor"),
        devicePixelRatioF=lambda: 1.0,
    )
    dummy._rect_tuple_from_qrect_like = mw.MainWindow._rect_tuple_from_qrect_like
    dummy._device_pixel_ratio_from_sizes = mw.MainWindow._device_pixel_ratio_from_sizes
    dummy._capture_image_device_pixel_ratio = lambda: mw.MainWindow._capture_image_device_pixel_ratio(dummy)
    dummy._post_actions_capture_device_pixel_ratio = (
        lambda target, image=None: mw.MainWindow._post_actions_capture_device_pixel_ratio(dummy, target, image)
    )
    dummy._stash_post_actions_capture = lambda target=None: mw.MainWindow._stash_post_actions_capture(dummy, target)
    mw.MainWindow._handle_previous_capture_before_new_capture(dummy)

    assert events[:2] == ["stop_left", "export"]
    assert "post_close" in events
    assert "border_close" in events
    assert "region_close" in events
    assert "shade_close" in events
    assert dummy._post_actions is None
    assert dummy._border_overlay is None
    assert dummy._region_overlay is None
    assert len(dummy._stashed_capture_items) == 1
    assert dummy._stashed_capture_items[0]["dpr"] == pytest.approx(2.0)
    assert np.array_equal(dummy._stashed_capture_items[0]["image_bgr"], image_bgr)
    assert dummy._stashed_capture_items[0]["image_bgr"] is not image_bgr
    assert getattr(post_actions, "_continuous_stash_captured") is True


def test_stashed_pin_passes_device_pixel_ratio_to_pinned_image(monkeypatch):
    try:
        import deepcat.ui.main_window as mw
        import deepcat.ui.pinned_image_window as pinned_module
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    captured: dict[str, object] = {}

    class FakePinnedImageWindow:
        def __init__(self, image, **kwargs) -> None:
            captured["dpr"] = float(image.devicePixelRatio())
            captured["size"] = (int(image.width()), int(image.height()))
            captured["image_bgr"] = kwargs.get("image_bgr")

        def show(self) -> None:
            captured["shown"] = True

        def raise_(self) -> None:
            captured["raised"] = True

        def activateWindow(self) -> None:
            captured["activated"] = True

    monkeypatch.setattr(pinned_module, "PinnedImageWindow", FakePinnedImageWindow)
    monkeypatch.setattr(mw, "get_image_output_dir", lambda: "")

    image_bgr = np.zeros((24, 48, 3), dtype=np.uint8)
    dummy = SimpleNamespace(
        _current_format=lambda: "png",
        _cfg=SimpleNamespace(JPG_QUALITY=95),
        _send_tray_notification=lambda *args, **kwargs: None,
    )

    mw.MainWindow._pin_stashed_capture(dummy, image_bgr, 2.0, 1)

    assert captured["dpr"] == pytest.approx(2.0)
    assert captured["size"] == (48, 24)
    assert captured["image_bgr"] is not image_bgr
    assert captured["shown"] is True


def test_stashed_dialog_thumbnail_and_release_cached_images(monkeypatch):
    try:
        from PyQt6.QtWidgets import QApplication, QLabel

        from deepcat.ui.main_window import _StashedCapturesDialog
        from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
        import deepcat.ui.main_window.stashed_captures as stashed_module
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication(sys.argv)
    image_bgr = np.zeros((24, 48, 3), dtype=np.uint8)
    dialog = _StashedCapturesDialog(
        [{"image_bgr": image_bgr, "dpr": 2.0}],
        default_format="png",
        jpg_quality=95,
        on_pin=lambda *args: None,
        on_toast=lambda *args, **kwargs: None,
    )

    try:
        monkeypatch.setattr(stashed_module, "_present_window", lambda _target: None)
        assert MAIN_WINDOW_BACKGROUND in dialog.styleSheet()
        assert not dialog.isVisible()
        assert not dialog.isMinimized()

        item = dialog._list.item(0)
        row_widget = dialog._list.itemWidget(item)
        thumbnail = row_widget.findChild(QLabel, "StashedCaptureThumbnail")
        row_text = row_widget.findChild(QLabel, "StashedCaptureText")

        assert item.text() == ""
        assert row_text is not None
        assert "第 1 张" in row_text.text()
        assert thumbnail is not None
        assert thumbnail.pixmap() is not None
        assert not thumbnail.pixmap().isNull()

        dialog._show_preview(0)
        assert len(dialog._preview_dialogs) == 1
        assert MAIN_WINDOW_BACKGROUND in dialog._preview_dialogs[0].styleSheet()
        assert not dialog._preview_dialogs[0].isMinimized()

        dialog._release_cached_images()

        assert dialog._items == []
        assert dialog._list.count() == 0
        assert dialog._preview_dialogs == []
    finally:
        try:
            dialog.close()
            app.processEvents()
        except RuntimeError:
            pass


def test_show_stashed_captures_presents_dialog_instead_of_leaving_it_minimized(monkeypatch):
    try:
        import deepcat.ui.main_window as mw
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    captured: dict[str, object] = {}

    class FakeSignal:
        def connect(self, callback) -> None:
            captured["destroyed_callback"] = callback

    class FakeDialog:
        def __init__(self, items, **kwargs) -> None:
            captured["items"] = items
            captured["parent"] = kwargs.get("parent")
            self.destroyed = FakeSignal()

        def close(self) -> None:
            captured["closed"] = True

        def setWindowIcon(self, icon) -> None:
            captured["icon"] = icon

        def width(self) -> int:
            return 560

        def height(self) -> int:
            return 420

        def move(self, x: int, y: int) -> None:
            captured["position"] = (x, y)

        def present(self) -> None:
            captured["presented"] = True

    show_stashed = mw.MainWindow._show_stashed_captures_if_any
    monkeypatch.setitem(show_stashed.__globals__, "_StashedCapturesDialog", FakeDialog)

    dummy = SimpleNamespace(
        _stashed_capture_items=[{"image_bgr": np.zeros((8, 12, 3), dtype=np.uint8), "dpr": 1.0}],
        _stashed_captures_show_pending=True,
        _stashed_captures_dialog=None,
        _cfg=SimpleNamespace(JPG_QUALITY=95),
        _current_format=lambda: "png",
        _send_tray_notification=lambda *args, **kwargs: None,
        _pin_stashed_capture=lambda *args: None,
        windowIcon=lambda: "window-icon",
    )

    assert show_stashed(dummy) is True
    assert captured["presented"] is True
    assert captured["parent"] is None
    assert dummy._stashed_capture_items == []
    assert dummy._stashed_captures_dialog is not None


def test_defer_show_stashed_captures_waits_for_overlay_teardown(monkeypatch):
    try:
        import deepcat.ui.main_window as mw
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    scheduled: list[tuple[int, object]] = []
    shown: list[bool] = []
    defer_show = mw.MainWindow._defer_show_stashed_captures
    monkeypatch.setitem(
        defer_show.__globals__,
        "QTimer",
        SimpleNamespace(singleShot=lambda delay, callback: scheduled.append((delay, callback))),
    )
    dummy = SimpleNamespace(
        _stashed_capture_items=[{"image_bgr": object()}],
        _stashed_captures_show_pending=False,
        _show_stashed_captures_if_any=lambda: shown.append(True),
    )

    defer_show(dummy)

    assert dummy._stashed_captures_show_pending is True
    assert len(scheduled) == 1
    assert scheduled[0][0] == 80
    assert shown == []

    scheduled[0][1]()
    assert shown == [True]


def test_stashed_native_foreground_restores_and_activates_window(monkeypatch):
    import importlib

    stashed_module = importlib.import_module("deepcat.ui.main_window.stashed_captures")
    calls: list[tuple[object, ...]] = []

    class FakeUser32:
        def __init__(self) -> None:
            self.foreground_hwnd = 101

        def GetForegroundWindow(self):
            return self.foreground_hwnd

        def ShowWindow(self, hwnd, command):
            calls.append(("show", int(hwnd.value or 0), command))
            return 1

        def BringWindowToTop(self, hwnd):
            calls.append(("bring", int(hwnd.value or 0)))
            return 1

        def SetForegroundWindow(self, hwnd):
            self.foreground_hwnd = int(hwnd.value or 0)
            calls.append(("foreground", self.foreground_hwnd))
            return 1

    fake_user32 = FakeUser32()
    fake_windll = SimpleNamespace(user32=fake_user32)
    monkeypatch.setattr(stashed_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(stashed_module.ctypes, "windll", fake_windll, raising=False)
    window = SimpleNamespace(winId=lambda: 909)

    assert stashed_module._force_native_foreground(window) is True
    assert ("show", 909, 9) in calls
    assert ("bring", 909) in calls
    assert ("foreground", 909) in calls


def test_stashed_restore_avoids_focus_request_for_foreign_foreground(monkeypatch):
    import importlib

    stashed_module = importlib.import_module("deepcat.ui.main_window.stashed_captures")
    calls: list[tuple[object, ...]] = []

    class FakeWindow:
        def testAttribute(self, attribute):
            calls.append(("test_attribute", attribute))
            return False

        def setAttribute(self, attribute, enabled):
            calls.append(("set_attribute", attribute, enabled))

        def showNormal(self):
            calls.append(("show_normal",))

        def raise_(self):
            calls.append(("raise",))

        def activateWindow(self):
            calls.append(("activate",))

        def windowHandle(self):
            calls.append(("window_handle",))
            return SimpleNamespace(requestActivate=lambda: calls.append(("request_activate",)))

    monkeypatch.setattr(
        stashed_module,
        "_foreground_belongs_to_window_process",
        lambda _window: False,
    )
    monkeypatch.setattr(
        stashed_module,
        "_force_native_foreground",
        lambda _window: calls.append(("native_foreground",)),
    )

    stashed_module._restore_and_activate_window(FakeWindow())

    assert ("show_normal",) in calls
    assert ("raise",) in calls
    assert ("activate",) not in calls
    assert ("request_activate",) not in calls
    assert ("native_foreground",) not in calls
    assert calls[-1] == (
        "set_attribute",
        stashed_module.Qt.WidgetAttribute.WA_ShowWithoutActivating,
        False,
    )


def test_stashed_present_uses_and_releases_transient_topmost(monkeypatch):
    import importlib

    stashed_module = importlib.import_module("deepcat.ui.main_window.stashed_captures")
    calls: list[tuple[object, ...]] = []
    scheduled: list[tuple[int, object]] = []
    window = object()

    monkeypatch.setattr(
        stashed_module,
        "_set_native_topmost",
        lambda target, topmost: calls.append(("topmost", target, topmost)),
    )
    monkeypatch.setattr(
        stashed_module,
        "_restore_and_activate_window",
        lambda target: calls.append(("activate", target)),
    )
    monkeypatch.setattr(
        stashed_module,
        "_apply_main_window_caption_color",
        lambda target: calls.append(("caption", target)),
    )
    monkeypatch.setattr(
        stashed_module,
        "QTimer",
        SimpleNamespace(singleShot=lambda delay, callback: scheduled.append((delay, callback))),
    )

    stashed_module._present_window(window)

    assert calls == [
        ("activate", window),
        ("topmost", window, True),
        ("caption", window),
    ]
    assert [delay for delay, _callback in scheduled] == [80, 600]

    for _delay, callback in scheduled:
        callback()
    assert calls[-2:] == [
        ("topmost", window, True),
        ("topmost", window, False),
    ]


def test_stashed_dialog_copy_selected_uses_all_selected_rows():
    try:
        from PyQt6.QtWidgets import QApplication

        from deepcat.ui.main_window import _StashedCapturesDialog
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication(sys.argv)
    images = [
        np.full((10, 20, 3), 40, dtype=np.uint8),
        np.full((12, 18, 3), 90, dtype=np.uint8),
        np.full((14, 16, 3), 140, dtype=np.uint8),
    ]
    dialog = _StashedCapturesDialog(
        [{"image_bgr": image, "dpr": 1.0} for image in images],
        default_format="png",
        jpg_quality=95,
        on_pin=lambda *args: None,
        on_toast=lambda *args, **kwargs: None,
    )
    copied: list[dict[str, object]] = []

    try:
        dialog._copy_multiple_images = lambda items: copied.extend(items) or True
        dialog._set_row_selected(0, True, exclusive=True)
        dialog._set_row_selected(1, True)

        dialog._copy_selected()

        assert len(copied) == 2
        assert copied[0]["image_bgr"] is images[0]
        assert copied[1]["image_bgr"] is images[1]
    finally:
        try:
            dialog.close()
            app.processEvents()
        except RuntimeError:
            pass


def test_post_close_stash_is_idempotent(monkeypatch):
    try:
        import deepcat.ui.main_window as mw
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    image_bgr = np.zeros((10, 20, 3), dtype=np.uint8)
    calls = {"export": 0}
    finalized: list[str] = []

    class FakePostActions:
        _last_pinned = None

        def export_image_bgr(self):
            calls["export"] += 1
            return image_bgr

    monkeypatch.setattr(mw.MainWindow, "_finalize_stitch_buffer", lambda self: finalized.append("finalize"))

    dummy = SimpleNamespace(
        _post_actions=FakePostActions(),
        _app_settings=SimpleNamespace(ui={"previous_capture_action": "stash"}),
        _stashed_capture_items=[],
        _stashed_captures_show_pending=False,
        _stitch_buffer_bgr=None,
        _stitch_buffer_dpr=0.0,
        _capture_region=(0, 0, 20, 10),
        _capture_region_logical=(0, 0, 10, 5),
        devicePixelRatioF=lambda: 1.0,
    )
    dummy._previous_capture_action = lambda: "stash"
    dummy._post_actions_has_manual_pin = lambda: mw.MainWindow._post_actions_has_manual_pin(dummy)
    dummy._rect_tuple_from_qrect_like = mw.MainWindow._rect_tuple_from_qrect_like
    dummy._device_pixel_ratio_from_sizes = mw.MainWindow._device_pixel_ratio_from_sizes
    dummy._capture_image_device_pixel_ratio = lambda: mw.MainWindow._capture_image_device_pixel_ratio(dummy)
    dummy._post_actions_capture_device_pixel_ratio = (
        lambda target, image=None: mw.MainWindow._post_actions_capture_device_pixel_ratio(dummy, target, image)
    )
    dummy._stash_post_actions_capture = lambda target=None: mw.MainWindow._stash_post_actions_capture(dummy, target)
    dummy._finalize_stitch_buffer = lambda: finalized.append("finalize")

    mw.MainWindow._finalize_or_clear_stitch_buffer_for_post_close(dummy)
    mw.MainWindow._finalize_or_clear_stitch_buffer_for_post_close(dummy)

    assert calls["export"] == 1
    assert finalized == ["finalize", "finalize"]
    assert len(dummy._stashed_capture_items) == 1
    assert dummy._stashed_capture_items[0]["dpr"] == pytest.approx(2.0)


def test_pinned_image_uses_custom_context_popup(monkeypatch):
    try:
        from PyQt6.QtCore import QPoint
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QPixmap

        from deepcat.ui import post_capture_actions
        from deepcat.ui.pinned_image_window import PinnedImageWindow
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication(sys.argv)
    image_bgr = np.zeros((20, 30, 3), dtype=np.uint8)
    pixmap = QPixmap(30, 20)
    shown: dict[str, object] = {}

    class FakePopup:
        def __init__(self, items, parent=None, **kwargs):
            shown["items"] = items
            shown["parent"] = parent
            shown["kwargs"] = kwargs

        def show_at_pos(self, pos):
            shown["pos"] = QPoint(pos)

    monkeypatch.setattr(post_capture_actions, "OcrGenericMenuPopup", FakePopup)

    window = PinnedImageWindow(
        pixmap,
        image_bgr=image_bgr,
        default_dir="",
        default_format="png",
        jpg_quality=95,
    )

    try:
        window._show_context_popup(QPoint(10, 20))

        assert shown["parent"] is window
        assert shown["kwargs"]["active_indicator"] == "background"
        assert [item[0] for item in shown["items"]] == [
            "裁剪",
            "最小化",
            "-",
            "关闭（保存）",
            "另存为...",
            "复制",
            "-",
            "销毁（不保存）",
        ]
        assert shown["pos"] == QPoint(10, 20)
    finally:
        window.close()
        app.processEvents()


def test_pinned_image_shadow_and_scale_badge_update():
    try:
        from PyQt6.QtGui import QPixmap
        from PyQt6.QtWidgets import QApplication

        from deepcat.ui.pinned_image_window import PinnedImageWindow
    except Exception:
        pytest.skip("PyQt6 is unavailable")

    app = QApplication.instance() or QApplication(sys.argv)
    image_bgr = np.zeros((20, 30, 3), dtype=np.uint8)
    pixmap = QPixmap(30, 20)
    window = PinnedImageWindow(
        pixmap,
        image_bgr=image_bgr,
        default_dir="",
        default_format="png",
        jpg_quality=95,
    )

    try:
        window.show()
        app.processEvents()
        assert window._scroll_shadow is not None
        assert window._scroll_shadow.blurRadius() == pytest.approx(34)
        assert window._scroll_shadow.yOffset() == pytest.approx(10)

        window._scale = 1.25
        window._show_scale_badge()

        assert window._scale_badge.text() == "125%"
        assert not window._scale_badge.isHidden()
        assert window._scale_badge.y() + window._scale_badge.height() <= window._scroll_area.y()

        window._hide_scale_badge()
        assert not window._scale_badge.isVisible()
    finally:
        window.close()
        app.processEvents()
