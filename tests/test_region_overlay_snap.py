from __future__ import annotations

import queue
from types import SimpleNamespace


def test_snap_precise_allowed_without_mouse_idle(monkeypatch):
    import deepcat.ui.region_overlay as region_overlay
    from PyQt6.QtCore import QPoint
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._snap_last_point_px = None
    overlay._snap_last_move_ts = 0.0
    overlay._snap_show_ts = 100.0
    overlay._snap_fast_start_ms = 0.0
    overlay._local_point_to_physical = lambda point: QPoint(point)

    monkeypatch.setattr(region_overlay.time, "monotonic", lambda: 100.001)

    assert overlay._snap_allow_precise_for_point(QPoint(30, 40)) is True


def test_snap_precise_waits_until_startup_delay(monkeypatch):
    import deepcat.ui.region_overlay as region_overlay
    from PyQt6.QtCore import QPoint
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._snap_last_point_px = None
    overlay._snap_last_move_ts = 0.0
    overlay._snap_show_ts = 100.0
    overlay._snap_fast_start_ms = 0.0
    overlay._snap_precise_ready_ts = 100.28
    overlay._snap_mouse_history = []
    overlay._snap_current_velocity = 0.0
    overlay._local_point_to_physical = lambda point: QPoint(point)

    now = {"value": 100.10}
    monkeypatch.setattr(region_overlay.time, "monotonic", lambda: now["value"])

    assert overlay._snap_allow_precise_for_point(QPoint(30, 40)) is False

    now["value"] = 100.30
    assert overlay._snap_allow_precise_for_point(QPoint(30, 40)) is True


def test_snap_pointer_update_throttles_tiny_fast_moves(monkeypatch):
    import deepcat.ui.region_overlay as region_overlay
    from PyQt6.QtCore import QPoint
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._snap_last_update_point_px = None
    overlay._snap_last_update_ts = 0.0
    overlay._snap_min_update_interval_s = 0.035
    overlay._snap_hard_min_update_interval_s = 0.016
    overlay._snap_min_update_distance_px = 12
    overlay._local_point_to_physical = lambda point: QPoint(point)

    now = {"value": 100.0}
    monkeypatch.setattr(region_overlay.time, "monotonic", lambda: now["value"])

    assert overlay._snap_should_process_pointer_update(QPoint(30, 40)) is True
    now["value"] = 100.010
    assert overlay._snap_should_process_pointer_update(QPoint(32, 41)) is False
    now["value"] = 100.040
    assert overlay._snap_should_process_pointer_update(QPoint(33, 42)) is True


def test_auto_snap_tiny_glyph_filter_only_rejects_glyph_sized_rects():
    from deepcat.ui.region_overlay import _snap_rect_is_tiny_glyph_like

    assert _snap_rect_is_tiny_glyph_like((0, 0, 9, 17)) is True
    assert _snap_rect_is_tiny_glyph_like((0, 0, 18, 18)) is True
    assert _snap_rect_is_tiny_glyph_like((0, 0, 24, 24)) is False
    assert _snap_rect_is_tiny_glyph_like((0, 0, 80, 60)) is False


def test_auto_snap_uia_filters_tiny_single_character_text_target():
    from deepcat.ui.region_overlay import _uia_wrapper_is_tiny_text_target

    class FakeWrapper:
        def __init__(self, text: str, control_type: str) -> None:
            self.element_info = SimpleNamespace(name=text, control_type=control_type)
            self._text = text

        def window_text(self) -> str:
            return self._text

    tiny_text = FakeWrapper("A", "Text")
    tiny_icon_button = FakeWrapper("", "Button")
    small_ok_button = FakeWrapper("OK", "Button")

    assert _uia_wrapper_is_tiny_text_target(tiny_text, (0, 0, 9, 17)) is True
    assert _uia_wrapper_is_tiny_text_target(tiny_icon_button, (0, 0, 18, 18)) is False
    assert _uia_wrapper_is_tiny_text_target(small_ok_button, (0, 0, 28, 18)) is False


def test_top_window_cache_skips_reenumeration_for_same_rect(monkeypatch):
    import deepcat.ui.region_overlay as region_overlay
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._auto_snap = True
    overlay._snap_top_window_hwnd = 42
    overlay._snap_top_window_rect_px = (0, 0, 400, 300)
    overlay._snap_top_window_ts = 200.0
    overlay._snap_top_window_cache_ttl_s = 0.35
    overlay._top_window_at_px_for_input = lambda _x, _y: (_ for _ in ()).throw(AssertionError("should use cache"))

    monkeypatch.setattr(region_overlay.time, "monotonic", lambda: 200.1)

    assert overlay._top_window_at_px(120, 80) == 42


def test_uia_scan_request_is_throttled_for_nearby_points(monkeypatch):
    import deepcat.ui.region_overlay as region_overlay
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._uia_unavailable = False
    overlay._snap_precise_blocked_until = {}
    overlay._snap_worker_thread = None
    overlay._snap_worker_hwnd = 0
    overlay._snap_worker_point_px = None
    overlay._snap_worker_started_ts = 0.0
    overlay._snap_worker_timeout = 0.45
    overlay._snap_worker_seq = 0
    overlay._snap_worker_queue = queue.Queue()
    overlay._snap_precise_last_request_ts = 0.0
    overlay._snap_precise_min_request_s = 0.07

    submits: list[tuple[object, ...]] = []

    class FakeFuture:
        def done(self):
            return False

    class FakeExecutor:
        def submit(self, *args):
            submits.append(args)
            return FakeFuture()

    monkeypatch.setattr(region_overlay, "_uia_executor", FakeExecutor())

    now = {"value": 100.0}
    monkeypatch.setattr(region_overlay.time, "monotonic", lambda: now["value"])

    overlay._request_uia_scan_async(10, (0, 0, 500, 500), 100, 100)
    overlay._request_uia_scan_async(10, (0, 0, 500, 500), 102, 103)

    assert len(submits) == 1
    assert overlay._snap_worker_point_px == (100, 100)


def test_uia_scan_request_skips_during_startup_delay(monkeypatch):
    import deepcat.ui.region_overlay as region_overlay
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._uia_unavailable = False
    overlay._snap_precise_blocked_until = {}
    overlay._snap_worker_thread = None
    overlay._snap_worker_hwnd = 0
    overlay._snap_worker_point_px = None
    overlay._snap_worker_started_ts = 0.0
    overlay._snap_worker_timeout = 0.45
    overlay._snap_worker_seq = 0
    overlay._snap_worker_queue = queue.Queue()
    overlay._snap_precise_last_request_ts = 0.0
    overlay._snap_precise_min_request_s = 0.025
    overlay._snap_precise_ready_ts = 100.28

    submits: list[tuple[object, ...]] = []

    class FakeExecutor:
        def submit(self, *args):
            submits.append(args)
            raise AssertionError("UIA scan should wait until startup delay has elapsed")

    monkeypatch.setattr(region_overlay, "_uia_executor", FakeExecutor())
    monkeypatch.setattr(region_overlay.time, "monotonic", lambda: 100.10)

    overlay._request_uia_scan_async(10, (0, 0, 500, 500), 100, 100)

    assert submits == []
    assert overlay._snap_worker_thread is None


def test_uia_scan_waits_for_single_executor_worker_before_retarget(monkeypatch):
    import deepcat.ui.region_overlay as region_overlay
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._uia_unavailable = False
    overlay._snap_precise_blocked_until = {}
    overlay._snap_worker_thread = None
    overlay._snap_worker_hwnd = 0
    overlay._snap_worker_point_px = None
    overlay._snap_worker_started_ts = 0.0
    overlay._snap_worker_timeout = 0.45
    overlay._snap_worker_retarget_s = 0.08
    overlay._snap_worker_seq = 0
    overlay._snap_worker_queue = queue.Queue()
    overlay._snap_precise_last_request_ts = 0.0
    overlay._snap_precise_min_request_s = 0.025

    submits: list[tuple[object, ...]] = []

    class FakeFuture:
        def done(self):
            return False

    class FakeExecutor:
        def submit(self, *args):
            submits.append(args)
            return FakeFuture()

    monkeypatch.setattr(region_overlay, "_uia_executor", FakeExecutor())

    now = {"value": 100.0}
    monkeypatch.setattr(region_overlay.time, "monotonic", lambda: now["value"])

    overlay._request_uia_scan_async(10, (0, 0, 500, 500), 100, 100)
    now["value"] = 100.11
    overlay._request_uia_scan_async(10, (0, 0, 500, 500), 220, 220)

    assert len(submits) == 1
    assert overlay._snap_worker_seq == 1
    assert overlay._snap_worker_point_px == (100, 100)


def test_auto_snap_keeps_small_element_when_parent_rect_arrives():
    from PyQt6.QtCore import QPoint, QRect
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._auto_snap = True
    overlay._drag_mode = None
    overlay._confirmed = False
    overlay._pending_confirm = False
    overlay._rect = QRect(10, 10, 20, 20)
    overlay._snap_locked_rect = QRect(10, 10, 20, 20)
    overlay._snap_locked_refined = True
    overlay._snap_parent_area_switch_ratio = 1.12
    overlay._cursor_cross = object()
    overlay._drain_snap_worker_results = lambda: None
    overlay.rect = lambda: QRect(0, 0, 240, 160)
    overlay.setCursor = lambda _cursor: None
    overlay.update = lambda: None
    overlay._detect_snap_rect = lambda _p, allow_precise=True: (QRect(0, 0, 120, 80), False)

    RegionOverlay._update_auto_snap_rect(overlay, QPoint(15, 15), allow_precise=False)

    assert overlay._rect == QRect(10, 10, 20, 20)
    assert overlay._snap_locked_rect == QRect(10, 10, 20, 20)


def test_detect_snap_suppresses_large_child_fallback_while_precise_available():
    from PyQt6.QtCore import QPoint, QRect
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._auto_snap = True
    overlay._uia_unavailable = False
    overlay._snap_precise_blocked_until = {}
    overlay._snap_large_fallback_area_ratio = 0.64
    overlay._local_point_to_physical = lambda point: QPoint(point)
    overlay._top_window_at_px = lambda _x, _y: 10
    overlay._uia_rect_at_px = lambda *_args, **_kwargs: None
    overlay._child_window_at_px = lambda *_args: 11
    overlay._window_rect_px = lambda hwnd, top_level=False: (
        (0, 0, 1000, 800) if int(hwnd) == 10 and bool(top_level) else (20, 20, 980, 780)
    )
    overlay._logical_rect_to_local = lambda left, top, right, bottom: QRect(left, top, right - left, bottom - top)
    requests: list[tuple[int, tuple[int, int, int, int], int, int]] = []
    overlay._request_uia_scan_async = lambda hwnd, top_rect, x, y: requests.append((hwnd, top_rect, x, y))

    assert RegionOverlay._detect_snap_rect(overlay, QPoint(120, 120), allow_precise=True) is None
    assert requests == [(10, (0, 0, 1000, 800), 120, 120)]


def test_detect_snap_allows_small_child_fallback_immediately():
    from PyQt6.QtCore import QPoint, QRect
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._auto_snap = True
    overlay._uia_unavailable = False
    overlay._snap_precise_blocked_until = {}
    overlay._snap_large_fallback_area_ratio = 0.64
    overlay._local_point_to_physical = lambda point: QPoint(point)
    overlay._top_window_at_px = lambda _x, _y: 10
    overlay._uia_rect_at_px = lambda *_args, **_kwargs: None
    overlay._child_window_at_px = lambda *_args: 11
    overlay._window_rect_px = lambda hwnd, top_level=False: (
        (0, 0, 1000, 800) if int(hwnd) == 10 and bool(top_level) else (100, 100, 180, 160)
    )
    overlay._logical_rect_to_local = lambda left, top, right, bottom: QRect(left, top, right - left, bottom - top)
    overlay._request_uia_scan_async = lambda *_args: None

    rect, refined = RegionOverlay._detect_snap_rect(overlay, QPoint(120, 120), allow_precise=True)

    assert rect == QRect(100, 100, 80, 60)
    assert refined is True


def test_auto_snap_clears_non_refined_large_rect_while_waiting_for_precise():
    from PyQt6.QtCore import QPoint, QRect
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._auto_snap = True
    overlay._drag_mode = None
    overlay._confirmed = False
    overlay._pending_confirm = False
    overlay._rect = QRect(0, 0, 120, 80)
    overlay._snap_locked_rect = None
    overlay._snap_locked_refined = False
    overlay._cursor_cross = object()
    overlay._drain_snap_worker_results = lambda: None
    overlay.rect = lambda: QRect(0, 0, 240, 160)
    overlay.setCursor = lambda _cursor: None
    updates = {"count": 0}
    overlay.update = lambda: updates.__setitem__("count", updates["count"] + 1)
    overlay._detect_snap_rect = lambda _p, allow_precise=True: None

    RegionOverlay._update_auto_snap_rect(overlay, QPoint(30, 30), allow_precise=True)

    assert overlay._rect is None
    assert updates["count"] == 1


def test_uia_scan_request_respects_min_interval(monkeypatch):
    import deepcat.ui.region_overlay as region_overlay
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._uia_unavailable = False
    overlay._snap_precise_blocked_until = {}
    overlay._snap_worker_thread = None
    overlay._snap_worker_hwnd = 0
    overlay._snap_worker_point_px = None
    overlay._snap_worker_started_ts = 0.0
    overlay._snap_worker_timeout = 0.45
    overlay._snap_worker_seq = 0
    overlay._snap_worker_queue = queue.Queue()
    overlay._snap_precise_last_request_ts = 200.0
    overlay._snap_precise_min_request_s = 0.07

    submits: list[tuple[object, ...]] = []

    class FakeExecutor:
        def submit(self, *args):
            submits.append(args)
            return object()

    monkeypatch.setattr(region_overlay, "_uia_executor", FakeExecutor())
    monkeypatch.setattr(region_overlay.time, "monotonic", lambda: 200.03)

    overlay._request_uia_scan_async(10, (0, 0, 500, 500), 180, 180)

    assert submits == []


# ---- 空间网格索引测试 ----


def test_spatial_index_returns_all_containing_rects():
    """空间网格索引：查询点返回所有包含该点的矩形索引"""
    from deepcat.ui.region_overlay import _SnapSpatialIndex

    rects = [
        (0, 0, 200, 200),    # idx 0, large
        (10, 10, 100, 100),  # idx 1, medium
        (20, 20, 50, 50),    # idx 2, small
    ]
    index = _SnapSpatialIndex(rects)
    candidates = index.candidates(30, 30)
    assert 0 in candidates
    assert 1 in candidates
    assert 2 in candidates


def test_spatial_index_excludes_far_rects():
    """空间网格索引：远处的矩形不在候选中"""
    from deepcat.ui.region_overlay import _SnapSpatialIndex

    rects = [
        (0, 0, 60, 60),        # idx 0, near origin
        (500, 500, 560, 560),  # idx 1, far away
    ]
    index = _SnapSpatialIndex(rects)
    candidates = index.candidates(30, 30)
    assert 0 in candidates
    assert 1 not in candidates


def test_spatial_index_big_list_always_returns():
    """空间网格索引：超大矩形进入 big list，任意位置查询都返回"""
    from deepcat.ui.region_overlay import _SnapSpatialIndex

    rects = [
        (0, 0, 2000, 2000),   # idx 0, huge → big list
        (100, 100, 160, 160),  # idx 1, small
    ]
    index = _SnapSpatialIndex(rects)
    candidates = index.candidates(500, 500)
    assert 0 in candidates  # big list


def test_spatial_index_neighborhood_covers_adjacent_cell():
    """空间网格索引：3x3 邻域覆盖相邻格中的矩形"""
    from deepcat.ui.region_overlay import _SnapSpatialIndex

    rects = [(0, 0, 30, 30)]  # cell (0,0)
    index = _SnapSpatialIndex(rects)
    candidates = index.candidates(65, 15)  # cell (1,0), neighbor of (0,0)
    assert 0 in candidates


def test_uia_cached_rect_uses_spatial_index_for_smallest():
    """缓存查找使用空间网格索引，返回最小包含矩形"""
    import deepcat.ui.region_overlay as ro
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._snap_cache_hwnd = 10
    overlay._snap_cache_rects_px = [
        (0, 0, 200, 200),    # large
        (10, 10, 100, 100),  # medium
        (20, 20, 50, 50),    # small
    ]
    overlay._snap_spatial_index = ro._SnapSpatialIndex(overlay._snap_cache_rects_px)
    overlay._snap_cache_ts = float(ro.time.monotonic())
    overlay._snap_cache_ttl = 10.0

    result = overlay._uia_cached_rect_at_px(10, (0, 0, 500, 500), 30, 30, allow_scan=False)
    assert result == (20, 20, 50, 50)  # smallest containing


def test_drain_snap_worker_builds_spatial_index():
    """扫描结果返回时构建空间网格索引"""
    import queue
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._snap_worker_seq = 1
    overlay._snap_worker_queue = queue.Queue()
    overlay._snap_worker_thread = None
    overlay._snap_worker_hwnd = 0
    overlay._snap_worker_point_px = None
    overlay._snap_worker_started_ts = 0.0
    overlay._snap_precise_blocked_until = {}
    overlay._uia_unavailable = False
    overlay._snap_cache_hwnd = 0
    overlay._snap_cache_rects_px = []
    overlay._snap_spatial_index = None
    overlay._snap_cache_ts = 0.0

    overlay._snap_worker_queue.put_nowait({
        "seq": 1, "hwnd": 10,
        "rects": [(0, 0, 100, 100), (50, 50, 150, 150)],
        "target": (0, 0, 100, 100),
        "error": "", "elapsed": 0.05, "done": True,
    })

    overlay._drain_snap_worker_results()

    assert overlay._snap_cache_hwnd == 10
    assert overlay._snap_spatial_index is not None
    assert len(overlay._snap_cache_rects_px) == 2


# ---- 滞后防抖算法测试 ----


def test_hysteresis_margin_small_rect():
    """滞后边距：小矩形(<120px)用12px边距，大于磁性8px"""
    from PyQt6.QtCore import QPoint, QRect
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    rect = QRect(10, 10, 80, 80)  # 80x80 < 120 → margin=12
    # 11px outside right edge (90+12=102): point at 101 → inside
    assert overlay._hysteresis_contains_local(rect, QPoint(101, 50)) is True
    # 13px outside: point at 103 → outside
    assert overlay._hysteresis_contains_local(rect, QPoint(103, 50)) is False


def test_hysteresis_keeps_sibling_while_pointer_inside():
    """滞后防抖：鼠标在当前目标内时，兄弟元素不切换"""
    from PyQt6.QtCore import QPoint, QRect
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._auto_snap = True
    overlay._drag_mode = None
    overlay._confirmed = False
    overlay._pending_confirm = False
    overlay._rect = QRect(10, 10, 80, 80)
    overlay._snap_locked_rect = QRect(10, 10, 80, 80)
    overlay._snap_locked_refined = True
    overlay._cursor_cross = object()
    overlay._drain_snap_worker_results = lambda: None
    overlay.rect = lambda: QRect(0, 0, 500, 500)
    overlay.setCursor = lambda _cursor: None
    overlay.update = lambda: None
    overlay._detect_snap_rect = lambda _p, allow_precise=True: (QRect(100, 10, 80, 80), True)

    RegionOverlay._update_auto_snap_rect(overlay, QPoint(50, 50), allow_precise=False)

    assert overlay._rect == QRect(10, 10, 80, 80)  # kept A


def test_hysteresis_switches_to_smaller_child():
    """滞后防抖：更小子元素允许切换（精准钻取）"""
    from PyQt6.QtCore import QPoint, QRect
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._auto_snap = True
    overlay._drag_mode = None
    overlay._confirmed = False
    overlay._pending_confirm = False
    overlay._rect = QRect(10, 10, 200, 200)
    overlay._snap_locked_rect = QRect(10, 10, 200, 200)
    overlay._snap_locked_refined = True
    overlay._cursor_cross = object()
    overlay._drain_snap_worker_results = lambda: None
    overlay.rect = lambda: QRect(0, 0, 500, 500)
    overlay.setCursor = lambda _cursor: None
    overlay.update = lambda: None
    overlay._detect_snap_rect = lambda _p, allow_precise=True: (QRect(50, 50, 40, 40), True)

    RegionOverlay._update_auto_snap_rect(overlay, QPoint(70, 70), allow_precise=False)

    assert overlay._rect == QRect(50, 50, 40, 40)  # switched to child


def test_hysteresis_releases_when_pointer_leaves_margin():
    """滞后防抖：鼠标移出边距区后释放，切换到新目标"""
    from PyQt6.QtCore import QPoint, QRect
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._auto_snap = True
    overlay._drag_mode = None
    overlay._confirmed = False
    overlay._pending_confirm = False
    overlay._rect = QRect(10, 10, 80, 80)  # 80x80 → margin=12, edge at 102
    overlay._snap_locked_rect = QRect(10, 10, 80, 80)
    overlay._snap_locked_refined = True
    overlay._cursor_cross = object()
    overlay._drain_snap_worker_results = lambda: None
    overlay.rect = lambda: QRect(0, 0, 500, 500)
    overlay.setCursor = lambda _cursor: None
    overlay.update = lambda: None
    overlay._detect_snap_rect = lambda _p, allow_precise=True: (QRect(100, 10, 80, 80), True)

    # 103 > 102 → outside hysteresis → switch
    RegionOverlay._update_auto_snap_rect(overlay, QPoint(103, 50), allow_precise=False)

    assert overlay._rect == QRect(100, 10, 80, 80)  # switched to B


def test_auto_snap_reuses_refined_rect_inside_probe_interval(monkeypatch):
    import deepcat.ui.region_overlay as region_overlay
    from PyQt6.QtCore import QPoint, QRect
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._auto_snap = True
    overlay._drag_mode = None
    overlay._confirmed = False
    overlay._pending_confirm = False
    overlay._overlay_closed = False
    overlay._rect = QRect(10, 10, 80, 80)
    overlay._snap_locked_refined = True
    overlay._snap_last_probe_ts = 100.0
    overlay._snap_inside_rect_probe_interval_s = 0.085
    overlay._cursor_cross = object()
    overlay._drain_snap_worker_results = lambda: None
    overlay._snap_should_process_pointer_update = lambda _p: True
    overlay._snap_worker_active = lambda: False
    overlay.rect = lambda: QRect(0, 0, 500, 500)
    overlay.setCursor = lambda _cursor: None
    detected = {"count": 0}

    def detect(_p, allow_precise=True):
        detected["count"] += 1
        return (QRect(20, 20, 30, 30), True)

    overlay._detect_snap_rect = detect
    monkeypatch.setattr(region_overlay.time, "monotonic", lambda: 100.02)

    RegionOverlay._update_auto_snap_rect(overlay, QPoint(50, 50))

    assert detected["count"] == 0
    assert overlay._rect == QRect(10, 10, 80, 80)


def test_selection_rect_change_uses_dirty_repaint_region():
    from PyQt6.QtCore import QRect
    from deepcat.ui.region_overlay import RegionOverlay

    overlay = RegionOverlay.__new__(RegionOverlay)
    overlay._rect = QRect(10, 10, 80, 80)
    overlay.rect = lambda: QRect(0, 0, 500, 500)
    calls: list[tuple] = []
    overlay.update = lambda *args: calls.append(args)

    RegionOverlay._set_selection_rect_for_overlay(overlay, QRect(20, 20, 80, 80))

    assert len(calls) == 1
    assert len(calls[0]) == 1
    assert isinstance(calls[0][0], QRect)
