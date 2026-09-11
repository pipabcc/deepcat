from __future__ import annotations

from types import SimpleNamespace


def test_main_window_cursor_recovery_runs_when_no_overlay_is_active() -> None:
    from deepcat.ui.main_window.window import MainWindow

    calls: list[str] = []
    dummy = SimpleNamespace(
        isVisible=lambda: True,
        isMinimized=lambda: False,
        _cursor_refresh_blocked=lambda: False,
        _restore_normal_cursor=lambda: calls.append("restore"),
        _refresh_hover_cursor=lambda: calls.append("refresh"),
    )

    MainWindow._restore_and_refresh_hover_cursor(dummy)

    assert calls == ["restore", "refresh"]


def test_main_window_cursor_recovery_is_blocked_by_active_overlay() -> None:
    from deepcat.ui.main_window.window import MainWindow

    overlay = SimpleNamespace(isVisible=lambda: True, _overlay_closed=False)
    dummy = SimpleNamespace(
        _region_overlay=overlay,
        _later_read_probe_overlay=None,
    )

    assert MainWindow._cursor_refresh_blocked(dummy) is True


def test_confirmed_live_shade_does_not_block_main_window_cursor_recovery() -> None:
    from deepcat.ui.main_window.window import MainWindow

    overlay = SimpleNamespace(
        isVisible=lambda: True,
        _overlay_closed=False,
        _confirmed=True,
        _link_probe_only=False,
        _cursor_override_active=False,
    )
    calls: list[str] = []
    dummy = SimpleNamespace(
        isVisible=lambda: True,
        isMinimized=lambda: False,
        _region_overlay=overlay,
        _later_read_probe_overlay=None,
        _cursor_refresh_blocked=lambda: MainWindow._cursor_refresh_blocked(dummy),
        _restore_normal_cursor=lambda: calls.append("restore"),
        _refresh_hover_cursor=lambda: calls.append("refresh"),
    )

    MainWindow._restore_and_refresh_hover_cursor(dummy)

    assert calls == ["restore", "refresh"]


def test_visible_link_probe_blocks_cursor_recovery_while_updates_are_enabled() -> None:
    from deepcat.ui.main_window.window import MainWindow

    overlay = SimpleNamespace(
        isVisible=lambda: True,
        _overlay_closed=False,
        _confirmed=True,
        _link_probe_only=True,
        _cursor_updates_enabled=True,
        _cursor_override_active=False,
    )
    dummy = SimpleNamespace(
        _region_overlay=None,
        _later_read_probe_overlay=overlay,
    )

    assert MainWindow._cursor_refresh_blocked(dummy) is True


def test_confirmed_link_probe_without_cursor_updates_does_not_block_recovery() -> None:
    from deepcat.ui.main_window.window import MainWindow

    overlay = SimpleNamespace(
        isVisible=lambda: True,
        _overlay_closed=False,
        _confirmed=True,
        _link_probe_only=True,
        _cursor_updates_enabled=False,
        _cursor_override_active=False,
    )
    dummy = SimpleNamespace(
        _region_overlay=None,
        _later_read_probe_overlay=overlay,
    )

    assert MainWindow._cursor_refresh_blocked(dummy) is False


def test_hidden_overlay_with_stale_override_does_not_block_recovery() -> None:
    from deepcat.ui.main_window.window import MainWindow

    overlay = SimpleNamespace(
        isVisible=lambda: False,
        _overlay_closed=False,
        _confirmed=False,
        _link_probe_only=False,
        _cursor_override_active=True,
    )
    dummy = SimpleNamespace(
        _region_overlay=overlay,
        _later_read_probe_overlay=None,
    )

    assert MainWindow._cursor_refresh_blocked(dummy) is False


def test_main_window_cursor_refresh_uses_immediate_and_delayed_passes(monkeypatch) -> None:
    import deepcat.ui.main_window.window as window_module
    from deepcat.ui.main_window.window import MainWindow

    scheduled: list[tuple[int, object]] = []
    callback = lambda: None
    dummy = SimpleNamespace(_restore_and_refresh_hover_cursor=callback)
    monkeypatch.setattr(
        window_module,
        "QTimer",
        SimpleNamespace(singleShot=lambda delay, fn: scheduled.append((delay, fn))),
    )

    MainWindow._schedule_hover_cursor_refresh(dummy)

    assert scheduled == [(0, callback), (80, callback)]


def test_non_clickable_hover_area_reapplies_arrow_without_persisting_cursor(monkeypatch) -> None:
    import deepcat.ui.main_window.window as window_module
    from PyQt6.QtCore import QPoint, Qt
    from deepcat.ui.main_window.window import MainWindow

    calls: list[tuple[str, object] | tuple[str]] = []
    target = SimpleNamespace(
        testAttribute=lambda attribute: False,
        cursor=lambda: Qt.CursorShape.ArrowCursor,
        setCursor=lambda cursor: calls.append(("set", cursor)),
        unsetCursor=lambda: calls.append(("unset",)),
    )
    dummy = SimpleNamespace(isAncestorOf=lambda widget: widget is target)
    monkeypatch.setattr(
        window_module,
        "QApplication",
        SimpleNamespace(widgetAt=lambda pos: target),
    )
    monkeypatch.setattr(window_module, "QCursor", lambda cursor: cursor)

    refreshed = MainWindow._reapply_hovered_widget_cursor(dummy, QPoint(10, 20))

    assert refreshed is True
    assert calls == [("set", Qt.CursorShape.ArrowCursor), ("unset",)]


def test_clickable_hover_area_keeps_explicit_hand_cursor(monkeypatch) -> None:
    import deepcat.ui.main_window.window as window_module
    from PyQt6.QtCore import QPoint, Qt
    from deepcat.ui.main_window.window import MainWindow

    calls: list[tuple[str, object] | tuple[str]] = []
    target = SimpleNamespace(
        testAttribute=lambda attribute: True,
        cursor=lambda: Qt.CursorShape.PointingHandCursor,
        setCursor=lambda cursor: calls.append(("set", cursor)),
        unsetCursor=lambda: calls.append(("unset",)),
    )
    dummy = SimpleNamespace(isAncestorOf=lambda widget: widget is target)
    monkeypatch.setattr(
        window_module,
        "QApplication",
        SimpleNamespace(widgetAt=lambda pos: target),
    )
    monkeypatch.setattr(window_module, "QCursor", lambda cursor: cursor)

    refreshed = MainWindow._reapply_hovered_widget_cursor(dummy, QPoint(10, 20))

    assert refreshed is True
    assert calls == [("unset",), ("set", Qt.CursorShape.PointingHandCursor)]


def test_show_from_tray_schedules_cursor_refresh(monkeypatch) -> None:
    import deepcat.ui.main_window.tray as tray_module
    from deepcat.ui.main_window.tray import TrayMixin

    calls: list[str] = []
    monkeypatch.setattr(tray_module, "write_crash_breadcrumb", lambda *args, **kwargs: None)
    dummy = SimpleNamespace(
        isVisible=lambda: True,
        isMinimized=lambda: False,
        _apply_solid_window_backgrounds=lambda: None,
        _is_resizable_page_index=lambda: False,
        _set_window_resize_mode=lambda *args, **kwargs: None,
        showNormal=lambda: calls.append("show"),
        raise_=lambda: calls.append("raise"),
        activateWindow=lambda: calls.append("activate"),
        _schedule_hover_cursor_refresh=lambda: calls.append("cursor"),
    )

    TrayMixin._show_from_tray(dummy)

    assert calls == ["show", "raise", "activate", "cursor"]


def test_closed_region_overlay_ignores_queued_cursor_updates() -> None:
    from PyQt6.QtCore import Qt

    from deepcat.ui.region_overlay import RegionOverlay

    calls: list[str] = []
    dummy = SimpleNamespace(
        _overlay_closed=True,
        _link_probe_only=True,
        _cursor_cross=object(),
        setCursor=lambda cursor: calls.append("set"),
    )

    RegionOverlay._force_link_probe_cursor(dummy)
    RegionOverlay._set_overlay_cursor(dummy, Qt.CursorShape.IBeamCursor)

    assert calls == []


def test_suspended_region_overlay_ignores_queued_cursor_updates() -> None:
    from PyQt6.QtCore import Qt

    from deepcat.ui.region_overlay import RegionOverlay

    calls: list[str] = []
    dummy = SimpleNamespace(
        _overlay_closed=False,
        _cursor_updates_enabled=False,
        _link_probe_only=True,
        _cursor_cross=object(),
        setCursor=lambda cursor: calls.append("set"),
    )

    RegionOverlay._force_link_probe_cursor(dummy)
    RegionOverlay._set_overlay_cursor(dummy, Qt.CursorShape.IBeamCursor)

    assert calls == []


def test_region_overlay_cursor_update_lifecycle_is_explicit() -> None:
    from deepcat.ui.region_overlay import RegionOverlay

    calls: list[str] = []
    timer = SimpleNamespace(
        start=lambda: calls.append("start"),
        stop=lambda: calls.append("stop"),
    )
    dummy = SimpleNamespace(
        _confirmed=False,
        _link_probe_only=True,
        _cursor_updates_enabled=False,
        _external_cursor_timer=timer,
        _release_cursor_override=lambda: calls.append("release"),
        unsetCursor=lambda: calls.append("unset"),
    )

    assert RegionOverlay._resume_cursor_updates(dummy) is True
    RegionOverlay._suspend_cursor_updates(dummy)

    assert calls == ["start", "stop", "release", "unset"]
    assert dummy._cursor_updates_enabled is False


def test_confirmed_region_overlay_does_not_resume_cursor_updates() -> None:
    from deepcat.ui.region_overlay import RegionOverlay

    calls: list[str] = []
    dummy = SimpleNamespace(
        _confirmed=True,
        _link_probe_only=False,
        _cursor_updates_enabled=True,
        _external_cursor_timer=SimpleNamespace(start=lambda: calls.append("start")),
    )

    assert RegionOverlay._resume_cursor_updates(dummy) is False
    assert dummy._cursor_updates_enabled is False
    assert calls == []


def test_modal_cursor_restore_does_not_reactivate_suspended_overlay() -> None:
    from deepcat.ui.region_overlay import RegionOverlay

    calls: list[str] = []
    dummy = SimpleNamespace(
        _overlay_closed=False,
        _cursor_updates_enabled=False,
        _confirmed=False,
        isVisible=lambda: True,
        _cursor_cross=object(),
        _set_overlay_cursor=lambda cursor: calls.append("set"),
    )

    RegionOverlay._restore_cursor_after_modal(dummy, True)

    assert calls == []


def test_modal_cursor_restore_reactivates_live_selecting_overlay() -> None:
    from deepcat.ui.region_overlay import RegionOverlay

    calls: list[str] = []
    cursor = object()
    dummy = SimpleNamespace(
        _overlay_closed=False,
        _cursor_updates_enabled=True,
        _confirmed=False,
        isVisible=lambda: True,
        _cursor_cross=cursor,
        _set_overlay_cursor=lambda value: calls.append(value),
    )

    RegionOverlay._restore_cursor_after_modal(dummy, True)

    assert calls == [cursor]


def test_region_overlay_cursor_release_is_idempotent(monkeypatch) -> None:
    import deepcat.ui.region_overlay as region_overlay_module
    from deepcat.ui.region_overlay import RegionOverlay

    calls: list[str] = []
    monkeypatch.setattr(
        region_overlay_module,
        "QApplication",
        SimpleNamespace(restoreOverrideCursor=lambda: calls.append("restore")),
    )
    dummy = SimpleNamespace(_cursor_override_active=True)

    RegionOverlay._release_cursor_override(dummy)
    RegionOverlay._release_cursor_override(dummy)

    assert calls == ["restore"]
    assert dummy._cursor_override_active is False


def test_ai_panel_close_guard_refreshes_only_an_active_main_window() -> None:
    from deepcat.ui.main_window.window_shell import WindowShellMixin

    cases = (
        (True, True, ["restore_state", "cursor"]),
        (True, False, ["restore_state"]),
        (False, False, ["restore_state"]),
    )
    for snapshot_active, current_active, expected in cases:
        calls: list[str] = []
        state = {"visible": True, "minimized": False, "active": snapshot_active}
        dummy = SimpleNamespace(
            _main_window_visibility_snapshot=lambda state=state: dict(state),
            _restore_main_window_visibility_snapshot=lambda value: calls.append("restore_state"),
            _restore_and_refresh_hover_cursor=lambda: calls.append("cursor"),
            isActiveWindow=lambda current_active=current_active: current_active,
        )
        panel = SimpleNamespace()

        WindowShellMixin._install_ai_panel_close_guard(dummy, panel)
        panel._restore_main_window_visibility_after_close()

        assert calls == expected


def test_later_read_probe_close_can_suppress_delayed_cursor_recovery() -> None:
    from deepcat.ui.main_window.later_read import LaterReadMixin

    calls: list[str] = []
    overlay = SimpleNamespace(close=lambda: calls.append("close"))
    dummy = SimpleNamespace(
        _later_read_probe_overlay=overlay,
        _later_read_probe_generation=0,
        _later_read_probe_timeout_anchor_pos=object(),
        _later_read_probe_timeout_extensions=1,
        _restore_later_read_probe_cursor=lambda: calls.append("restore"),
    )

    LaterReadMixin._close_later_read_probe_overlay(dummy, restore_cursor=False)

    assert dummy._later_read_probe_overlay is None
    assert calls == ["close"]


def test_stale_later_read_cursor_restore_does_not_clear_active_capture_cursor() -> None:
    from deepcat.ui.main_window.later_read import LaterReadMixin

    calls: list[str] = []
    dummy = SimpleNamespace(
        _cursor_refresh_blocked=lambda: True,
        _restore_normal_cursor=lambda: calls.append("restore"),
        _refresh_hover_cursor=lambda: calls.append("refresh"),
    )

    LaterReadMixin._restore_later_read_probe_cursor(dummy)

    assert calls == []


def test_old_later_read_overlay_destruction_does_not_clear_new_overlay() -> None:
    from deepcat.ui.main_window.later_read import LaterReadMixin

    old_overlay = object()
    new_overlay = object()
    calls: list[str] = []
    dummy = SimpleNamespace(
        _later_read_probe_overlay=new_overlay,
        _restore_later_read_probe_cursor=lambda: calls.append("restore"),
    )

    LaterReadMixin._on_later_read_probe_destroyed(dummy, old_overlay)

    assert dummy._later_read_probe_overlay is new_overlay
    assert calls == []


def test_active_later_read_overlay_destruction_restores_cursor() -> None:
    from deepcat.ui.main_window.later_read import LaterReadMixin

    overlay = object()
    calls: list[str] = []
    dummy = SimpleNamespace(
        _later_read_probe_overlay=overlay,
        _restore_later_read_probe_cursor=lambda: calls.append("restore"),
    )

    LaterReadMixin._on_later_read_probe_destroyed(dummy, overlay)

    assert dummy._later_read_probe_overlay is None
    assert calls == ["restore"]
