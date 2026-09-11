from __future__ import annotations

from dataclasses import dataclass
import math
import os
import queue
import re
import threading
import time
from typing import Callable, Optional

from PyQt6.QtCore import QEasingCurve, QPoint, QRect, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from deepcat.settings_store import normalize_annotation_style
from deepcat.ui.timer_scope import single_shot_scoped
import concurrent.futures

# 全局单工作线程执行器，确保 UIA 跨进程操作都在同一常驻线程中顺序执行。
# pywinauto/comtypes 的 UIA 对象不能跨线程复用；Desktop 实例必须归属于这个常驻线程。
_uia_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="DeepCat-UIA-Worker")
_uia_thread_local = threading.local()
# 记录 UIA 后端初始化失败的诊断信息，供上层判断是否需要回退或提示用户。
_uia_init_error: Optional[str] = None

def _ensure_com_initialized() -> bool:
    """在工作线程内初始化 COM（STA 单元），pywinauto UIA 后端依赖 comtypes 必须先 CoInitialize。

    主线程的 COM 初始化不会继承到 ThreadPoolExecutor 创建的工作线程，
    打包后 windowed 模式下尤为明显——调用 desktop.from_point() 会抛
    CO_E_NOTINITIALIZED (0x800401F0)，链接探测完全失效。
    """
    if getattr(_uia_thread_local, "com_initialized", False):
        return True
    try:
        import pythoncom
        pythoncom.CoInitialize()
        _uia_thread_local.com_initialized = True
        _uia_thread_local._pythoncom = pythoncom
        return True
    except Exception:
        # pywin32 不可用时退回 ole32，确保仅依赖系统 DLL 也能初始化 COM
        try:
            import ctypes
            # COINIT_APARTMENTTHREADED = 0x2
            ctypes.OleDLL("ole32").CoInitializeEx(None, 0x2)
            _uia_thread_local.com_initialized = True
            _uia_thread_local._ole32 = True
            return True
        except Exception:
            return False

def _get_uia_desktop_instance():
    global _uia_init_error
    desktop = getattr(_uia_thread_local, "desktop", None)
    if desktop is None:
        if not _ensure_com_initialized():
            _uia_init_error = "CoInitialize failed on UIA worker thread"
            return None
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            _uia_thread_local.desktop = desktop
            _uia_init_error = None
        except Exception as exc:
            _uia_init_error = f"{type(exc).__name__}: {exc}"
            return None
    return desktop


def _is_worker_alive(worker) -> bool:
    if worker is None:
        return False
    if hasattr(worker, "done"):
        return not worker.done()
    if hasattr(worker, "is_alive"):
        return worker.is_alive()
    return False


def _rect_contains_px_tuple(rect: tuple[int, int, int, int], x: int, y: int) -> bool:
    l, t, r, b = rect
    return int(l) <= int(x) < int(r) and int(t) <= int(y) < int(b)


def _rect_contains_px_tuple_with_margin(rect: tuple[int, int, int, int], x: int, y: int) -> bool:
    l, t, r, b = rect
    w = int(r) - int(l)
    h = int(b) - int(t)
    if w <= 0 or h <= 0:
        return False
    if w < 60 and h < 60:
        margin = 12
    elif w < 150 and h < 150:
        margin = 8
    elif w < 300 and h < 300:
        margin = 4
    else:
        margin = 0
    return int(l) - margin <= int(x) < int(r) + margin and int(t) - margin <= int(y) < int(b) + margin


class _SnapSpatialIndex:
    """O(1) 空间网格索引：将缓存矩形按 64x64 像素网格分桶，查询时只检查鼠标所在格的 3x3 邻域 + 大矩形列表。

    替代原先每次 mouseMove 的 O(N) 线性遍历。对于网页等密集界面（400-900 个矩形），
    单格内候选数通常 <50，查询性能提升约 15 倍。
    """

    __slots__ = ("_cell_size", "_big_threshold", "_cell", "_big")

    def __init__(self, rects: list[tuple[int, int, int, int]], cell_size: int = 64, big_threshold: int = 16) -> None:
        self._cell_size = int(cell_size)
        self._big_threshold = int(big_threshold)
        self._cell: dict[tuple[int, int], list[int]] = {}
        self._big: list[int] = []
        cs = self._cell_size
        for idx, rect in enumerate(rects):
            l, t, r, b = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
            if r <= l or b <= t:
                continue
            cx0 = l // cs
            cx1 = max(cx0, (r - 1) // cs)
            cy0 = t // cs
            cy1 = max(cy0, (b - 1) // cs)
            ncells = (cx1 - cx0 + 1) * (cy1 - cy0 + 1)
            if ncells > self._big_threshold:
                self._big.append(idx)
                continue
            for cx in range(cx0, cx1 + 1):
                for cy in range(cy0, cy1 + 1):
                    self._cell.setdefault((cx, cy), []).append(idx)

    def candidates(self, x: int, y: int) -> list[int]:
        """返回可能包含 (x,y) 的矩形索引（3x3 邻域 + big list，已去重）。

        3x3 邻域覆盖最大 8px 磁性边距（8px < 64px 单格尺寸），确保边缘吸附不丢失。
        """
        cs = self._cell_size
        cx = int(x) // cs
        cy = int(y) // cs
        out: list[int] = []
        seen: set[int] = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                lst = self._cell.get((cx + dx, cy + dy))
                if lst:
                    for idx in lst:
                        if idx not in seen:
                            seen.add(idx)
                            out.append(idx)
        for idx in self._big:
            if idx not in seen:
                seen.add(idx)
                out.append(idx)
        return out


def _rect_inside_tuple(child: tuple[int, int, int, int], parent: tuple[int, int, int, int]) -> bool:
    cl, ct, cr, cb = child
    pl, pt, pr, pb = parent
    return int(cl) >= int(pl) - 12 and int(ct) >= int(pt) - 12 and int(cr) <= int(pr) + 12 and int(cb) <= int(pb) + 12


def _snap_rect_is_tiny_glyph_like(rect: tuple[int, int, int, int]) -> bool:
    w = int(rect[2] - rect[0])
    h = int(rect[3] - rect[1])
    if w <= 0 or h <= 0:
        return False
    min_side = min(w, h)
    max_side = max(w, h)
    area = int(w * h)
    # 只排除单字/单字母/标点这类极小字形，避免把文字轮廓当成独立控件吸附。
    if max_side <= 10:
        return True
    if min_side <= 12 and max_side <= 42 and area <= 480:
        return True
    if max_side <= 22 and area <= 484:
        return True
    if w <= 28 and h <= 18 and area <= 504:
        return True
    return False


def _valid_device_pixel_ratio(value: float) -> float:
    try:
        dpr = float(value)
        if dpr > 0:
            return dpr
    except Exception:
        pass
    return 1.0


@dataclass(frozen=True)
class _ScreenDpiMapping:
    logical: QRect
    physical: QRect
    dpr: float


def _physical_rect_for_logical_rect(rect: QRect, dpr: float) -> QRect:
    dpr = _valid_device_pixel_ratio(dpr)
    left = int(math.floor(float(rect.x()) * dpr))
    top = int(math.floor(float(rect.y()) * dpr))
    right = int(math.ceil(float(rect.x() + rect.width()) * dpr))
    bottom = int(math.ceil(float(rect.y() + rect.height()) * dpr))
    return QRect(left, top, max(1, right - left), max(1, bottom - top))


def _build_screen_dpi_mappings(screens: Optional[list[object]] = None) -> list[_ScreenDpiMapping]:
    try:
        screen_list = list(screens if screens is not None else QGuiApplication.screens())
    except Exception:
        screen_list = []
    if not screen_list:
        try:
            primary = QGuiApplication.primaryScreen()
            if primary is not None:
                screen_list = [primary]
        except Exception:
            screen_list = []

    mappings: list[_ScreenDpiMapping] = []
    for screen in screen_list:
        try:
            logical = QRect(screen.geometry())
            if logical.isNull() or logical.width() <= 0 or logical.height() <= 0:
                continue
            dpr = _valid_device_pixel_ratio(screen.devicePixelRatio())
            mappings.append(_ScreenDpiMapping(logical=logical, physical=_physical_rect_for_logical_rect(logical, dpr), dpr=dpr))
        except Exception:
            continue
    if not mappings:
        logical = QRect(0, 0, 800, 600)
        mappings.append(_ScreenDpiMapping(logical=logical, physical=QRect(0, 0, 800, 600), dpr=1.0))
    return mappings


def _union_rect(rects: list[QRect]) -> QRect:
    if not rects:
        return QRect(0, 0, 1, 1)
    out = QRect(rects[0])
    for rect in rects[1:]:
        out = out.united(rect)
    return out


def _mapping_for_logical_point(point: QPoint, mappings: list[_ScreenDpiMapping]) -> _ScreenDpiMapping:
    for mapping in mappings:
        if mapping.logical.contains(point):
            return mapping
    best = mappings[0]
    best_dist = float("inf")
    for mapping in mappings:
        center = mapping.logical.center()
        dist = abs(int(center.x()) - int(point.x())) + abs(int(center.y()) - int(point.y()))
        if dist < best_dist:
            best = mapping
            best_dist = float(dist)
    return best


def _mapping_for_physical_point(point: QPoint, mappings: list[_ScreenDpiMapping]) -> _ScreenDpiMapping:
    for mapping in mappings:
        if mapping.physical.contains(point):
            return mapping
    best = mappings[0]
    best_dist = float("inf")
    for mapping in mappings:
        center = mapping.physical.center()
        dist = abs(int(center.x()) - int(point.x())) + abs(int(center.y()) - int(point.y()))
        if dist < best_dist:
            best = mapping
            best_dist = float(dist)
    return best


def _logical_point_to_physical(point: QPoint, mappings: list[_ScreenDpiMapping]) -> QPoint:
    mapping = _mapping_for_logical_point(point, mappings)
    x = int(round(float(mapping.physical.x()) + (float(point.x()) - float(mapping.logical.x())) * mapping.dpr))
    y = int(round(float(mapping.physical.y()) + (float(point.y()) - float(mapping.logical.y())) * mapping.dpr))
    return QPoint(x, y)


def logical_rect_to_physical_tuple(rect: QRect, mappings: Optional[list[_ScreenDpiMapping]] = None) -> tuple[int, int, int, int]:
    mapping_list = mappings if mappings is not None else _build_screen_dpi_mappings()
    logical_rect = QRect(rect).normalized()
    physical_parts: list[QRect] = []
    for mapping in mapping_list:
        part = logical_rect.intersected(mapping.logical)
        if part.width() <= 0 or part.height() <= 0:
            continue
        p1 = _logical_point_to_physical(part.topLeft(), mapping_list)
        p2 = _logical_point_to_physical(QPoint(part.x() + part.width(), part.y() + part.height()), mapping_list)
        physical_parts.append(QRect(min(p1.x(), p2.x()), min(p1.y(), p2.y()), max(1, abs(p2.x() - p1.x())), max(1, abs(p2.y() - p1.y()))))
    if not physical_parts:
        p1 = _logical_point_to_physical(logical_rect.topLeft(), mapping_list)
        p2 = _logical_point_to_physical(QPoint(logical_rect.x() + logical_rect.width(), logical_rect.y() + logical_rect.height()), mapping_list)
        physical_parts.append(QRect(min(p1.x(), p2.x()), min(p1.y(), p2.y()), max(1, abs(p2.x() - p1.x())), max(1, abs(p2.y() - p1.y()))))
    physical = _union_rect(physical_parts)
    return (int(physical.x()), int(physical.y()), int(max(1, physical.width())), int(max(1, physical.height())))


def _physical_rect_to_logical_rect(
    left: int,
    top: int,
    right: int,
    bottom: int,
    mappings: list[_ScreenDpiMapping],
) -> QRect:
    physical_rect = QRect(int(left), int(top), max(1, int(right) - int(left)), max(1, int(bottom) - int(top)))
    logical_parts: list[QRect] = []
    for mapping in mappings:
        part = physical_rect.intersected(mapping.physical)
        if part.width() <= 0 or part.height() <= 0:
            continue
        l = int(math.floor(float(mapping.logical.x()) + (float(part.x()) - float(mapping.physical.x())) / mapping.dpr))
        t = int(math.floor(float(mapping.logical.y()) + (float(part.y()) - float(mapping.physical.y())) / mapping.dpr))
        r = int(math.ceil(float(mapping.logical.x()) + (float(part.x() + part.width()) - float(mapping.physical.x())) / mapping.dpr))
        b = int(math.ceil(float(mapping.logical.y()) + (float(part.y() + part.height()) - float(mapping.physical.y())) / mapping.dpr))
        logical_parts.append(QRect(l, t, max(1, r - l), max(1, b - t)))
    if not logical_parts:
        mapping = _mapping_for_physical_point(QPoint(int(left), int(top)), mappings)
        l = int(math.floor(float(mapping.logical.x()) + (float(left) - float(mapping.physical.x())) / mapping.dpr))
        t = int(math.floor(float(mapping.logical.y()) + (float(top) - float(mapping.physical.y())) / mapping.dpr))
        r = int(math.ceil(float(mapping.logical.x()) + (float(right) - float(mapping.physical.x())) / mapping.dpr))
        b = int(math.ceil(float(mapping.logical.y()) + (float(bottom) - float(mapping.physical.y())) / mapping.dpr))
        logical_parts.append(QRect(l, t, max(1, r - l), max(1, b - t)))
    return _union_rect(logical_parts)


def _uia_rect_reasonable_tuple(
    rect: tuple[int, int, int, int],
    top_rect: tuple[int, int, int, int],
    x: Optional[int] = None,
    y: Optional[int] = None,
) -> bool:
    w = int(rect[2] - rect[0])
    h = int(rect[3] - rect[1])
    area = int(w * h)
    top_area = max(1, int(top_rect[2] - top_rect[0]) * int(top_rect[3] - top_rect[1]))
    if w < 6 or h < 6 or area < 36 or area >= int(top_area * 0.985):
        return False
    if not _rect_inside_tuple(rect, top_rect):
        return False
    if x is not None and y is not None and not _rect_contains_px_tuple_with_margin(rect, int(x), int(y)):
        return False
    return True


def _scan_uia_rects_for_window(
    seq: int,
    top_hwnd: int,
    top_rect: tuple[int, int, int, int],
    x: int,
    y: int,
    result_queue: "queue.Queue[dict]",
) -> None:
    started = float(time.monotonic())
    rects: list[tuple[int, int, int, int]] = []
    target: Optional[tuple[int, int, int, int]] = None
    error = ""

    def publish(*, done: bool) -> None:
        try:
            result_queue.put_nowait(
                {
                    "seq": int(seq),
                    "hwnd": int(top_hwnd),
                    "rects": list(rects),
                    "target": target,
                    "error": error,
                    "elapsed": float(time.monotonic() - started),
                    "done": bool(done),
                    "x": int(x),
                    "y": int(y),
                }
            )
        except Exception:
            pass

    try:
        desktop = _get_uia_desktop_instance()
        if desktop is None:
            return rects
        seen: set[tuple[int, int, int, int]] = set()
        elem = None
        try:
            elem = desktop.from_point(int(x), int(y))
            candidates: list[tuple[int, int, int, int]] = []

            def add_candidate(obj) -> None:
                try:
                    r = obj.rectangle()
                    rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
                    if (
                        _uia_rect_reasonable_tuple(rect, top_rect, int(x), int(y))
                        and not _uia_wrapper_is_tiny_text_target(obj, rect)
                        and rect not in seen
                    ):
                        seen.add(rect)
                        candidates.append(rect)
                except Exception:
                    pass

            cur = elem
            for _ in range(8):
                add_candidate(cur)
                try:
                    parent = cur.parent()
                except Exception:
                    break
                if parent is None or parent == cur:
                    break
                cur = parent
            if candidates:
                target = min(candidates, key=lambda r: int(max(1, r[2] - r[0]) * max(1, r[3] - r[1])))
                rects.extend(candidates)
                rects.sort(key=lambda r: int(max(1, r[2] - r[0]) * max(1, r[3] - r[1])))
                publish(done=False)
        except Exception:
            pass
        try:
            top_wrapper = desktop.window(handle=int(top_hwnd)).wrapper_object()
            seen = set(rects)
            # 改用 BFS 逐层展开，均匀扫描同级和浅层控件，每个节点处检查 deadline，复杂网页也不卡死漏扫
            scan_deadline = started + 1.5  # 1.5s 充裕的时间预算，确保慢速窗口也能深入扫完叶子小元素
            scan_max_rects = 1000
            scan_max_depth = 32

            def _try_collect(node) -> None:
                try:
                    r = node.rectangle()
                    rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
                    if (
                        rect not in seen
                        and _uia_rect_reasonable_tuple(rect, top_rect)
                        and not _uia_wrapper_is_tiny_text_target(node, rect)
                    ):
                        seen.add(rect)
                        rects.append(rect)
                except Exception:
                    pass

            from collections import deque

            def _bfs_from(root, depth_start: int) -> None:
                queue_nodes = deque([(root, int(depth_start))])
                while queue_nodes and time.monotonic() <= scan_deadline and len(rects) < scan_max_rects:
                    node, depth = queue_nodes.popleft()
                    _try_collect(node)
                    if depth < scan_max_depth:
                        try:
                            children = node.children()
                            for ch in children:
                                if time.monotonic() > scan_deadline or len(rects) >= scan_max_rects:
                                    break
                                queue_nodes.append((ch, depth + 1))
                        except Exception:
                            pass

            # 优先扫描光标附近子树（from_point 元素），确保即使时间预算用完也覆盖光标区域
            if elem is not None:
                _bfs_from(elem, 0)
            # 时间有剩余则补扫 top_wrapper 其他分支
            if time.monotonic() <= scan_deadline:
                _bfs_from(top_wrapper, 0)
            rects.sort(key=lambda r: int(max(1, r[2] - r[0]) * max(1, r[3] - r[1])))
        except Exception:
            pass
    except ImportError:
        error = "import"
    except Exception as exc:
        error = type(exc).__name__
    finally:
        publish(done=True)


_URL_RE = re.compile(r"(?i)\b((?:https?://|www\.)[^\s<>'\"，。；、]+)")


def _clean_uia_text(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _find_url_in_texts(texts: list[str]) -> str:
    for text in texts:
        match = _URL_RE.search(str(text or ""))
        if match is None:
            continue
        url = match.group(1).strip().rstrip(").,;，。；")
        if url.lower().startswith("www."):
            url = "https://" + url
        return url
    return ""


def _wrapper_rect_contains(wrapper, x: int, y: int, *, margin: int = 0) -> bool:
    try:
        rect = wrapper.rectangle()
        pad = max(0, int(margin))
        return (
            int(rect.left) - pad <= int(x) < int(rect.right) + pad
            and int(rect.top) - pad <= int(y) < int(rect.bottom) + pad
        )
    except Exception:
        return False


def _wrapper_control_type(wrapper) -> str:
    try:
        return str(getattr(wrapper.element_info, "control_type", "") or "")
    except Exception:
        return ""


def _wrapper_text_candidates(wrapper) -> list[str]:
    texts: list[str] = []

    def add(value: object) -> None:
        text = _clean_uia_text(value)
        if text and text not in texts:
            texts.append(text)

    try:
        add(wrapper.window_text())
    except Exception:
        pass
    try:
        add(getattr(wrapper.element_info, "name", ""))
    except Exception:
        pass
    try:
        element_info = wrapper.element_info
        for attr in ("help_text", "rich_text", "value", "description"):
            add(getattr(element_info, attr, ""))
    except Exception:
        pass
    try:
        for text in wrapper.texts():
            add(text)
    except Exception:
        pass
    try:
        props = wrapper.legacy_properties()
        for key in ("Name", "Value", "Description", "Help", "DefaultAction", "KeyboardShortcut"):
            add(props.get(key, ""))
    except Exception:
        pass
    try:
        value_iface = wrapper.iface_value
        add(getattr(value_iface, "CurrentValue", ""))
    except Exception:
        pass
    return texts


def _uia_wrapper_is_tiny_text_target(wrapper, rect: tuple[int, int, int, int]) -> bool:
    if not _snap_rect_is_tiny_glyph_like(rect):
        return False
    try:
        control_type = _wrapper_control_type(wrapper).lower()
    except Exception:
        control_type = ""
    texts = _wrapper_text_candidates(wrapper)
    meaningful = [text for text in texts if text and text.lower() not in {"text", "document", "pane"}]
    if not meaningful:
        return False
    shortest = min(meaningful, key=len)
    if len(shortest) <= 1:
        return True
    if "text" in control_type and len(shortest) <= 2 and _snap_rect_is_tiny_glyph_like(rect):
        return True
    return False


def _build_link_payload(wrapper, x: int, y: int, *, margin: int = 0, allow_text_url: bool = False) -> Optional[dict[str, str]]:
    if not _wrapper_rect_contains(wrapper, int(x), int(y), margin=int(margin)):
        return None
    texts = _wrapper_text_candidates(wrapper)
    href = _find_url_in_texts(texts)
    control_type = _wrapper_control_type(wrapper)
    is_link = "hyperlink" in control_type.lower() or "link" in control_type.lower()
    if not is_link:
        try:
            props = wrapper.legacy_properties()
            role = str(props.get("Role", "") or "").lower()
            default_action = str(props.get("DefaultAction", "") or "").lower()
            is_link = "link" in role or "jump" in default_action or "open" in default_action
        except Exception:
            pass
    if not href or (not is_link and not bool(allow_text_url)):
        return None
    # 提取并精准清洗超链接本身的原生 Title，确保高纯度
    # 1. 优先获取超链接自身的 name 或 window_text (最准确)
    self_name = ""
    try:
        self_name = _clean_uia_text(getattr(wrapper.element_info, "name", ""))
        if not self_name:
            self_name = _clean_uia_text(wrapper.window_text())
    except Exception:
        pass

    title = ""
    if self_name and href not in self_name and not _find_url_in_texts([self_name]):
        # 排除常见无意义的空描述/占位符，若有真实名称直接采用
        if self_name.lower() not in {"not found", "link", "hyperlink", "image", "button"}:
            title = self_name

    # 2. 如果自身名字为空或无意义，再从所有 candidates 中寻找第一个非 URL 且合规的文字
    if not title:
        for text in texts:
            if text and href not in text and not _find_url_in_texts([text]):
                if text.lower() not in {"not found", "link", "hyperlink", "image", "button"}:
                    title = text
                    break

    # 3. 仍为空时尝试回溯父节点获取真实文字（如父级超链接容器外部写有文字描述）
    if not title:
        try:
            parent = wrapper.parent()
            for _ in range(3):
                if parent is None:
                    break
                for text in _wrapper_text_candidates(parent):
                    if text and href not in text and len(text) <= 120 and not _find_url_in_texts([text]):
                        if text.lower() not in {"not found", "link", "hyperlink", "image", "button"}:
                            title = text
                            raise StopIteration
                parent = parent.parent()
        except StopIteration:
            pass
        except Exception:
            pass

    # 4. 兜底策略：如果仍为空，采用原始名或 href
    if not title:
        title = self_name or href

    return {"title": str(title)[:160], "url": str(href)[:2000]}


def _probe_uia_link_at_point(
    seq: int,
    top_hwnd: int,
    x: int,
    y: int,
    result_queue: "queue.Queue[dict]",
) -> None:
    payload: Optional[dict[str, str]] = None
    error = ""
    # 先在 try 外检查 desktop 是否可用：不可用时直接归类为 import 错误，
    # 避免被下方 except Exception 覆盖为 RuntimeError 类名，从而让上层能正确识别并停止重试。
    desktop = _get_uia_desktop_instance()
    if desktop is None:
        error = "import"
        try:
            result_queue.put_nowait({"seq": int(seq), "payload": None, "error": error})
        except Exception:
            pass
        return
    try:
        checked: set[int] = set()

        def try_wrapper(wrapper, *, allow_text_url: bool = False) -> Optional[dict[str, str]]:
            key = id(wrapper)
            try:
                key = int(getattr(wrapper.element_info, "handle", 0) or key)
            except Exception:
                pass
            if key in checked:
                return None
            checked.add(key)
            found = _build_link_payload(wrapper, int(x), int(y), margin=0)
            if found is not None:
                return found
            found = _build_link_payload(wrapper, int(x), int(y), margin=4)
            if found is not None:
                return found
            if bool(allow_text_url):
                found = _build_link_payload(wrapper, int(x), int(y), margin=0, allow_text_url=True)
                if found is not None:
                    return found
                return _build_link_payload(wrapper, int(x), int(y), margin=8, allow_text_url=True)
            return None

        def wrapper_contains_point(wrapper, *, margin: int = 0) -> bool:
            return _wrapper_rect_contains(wrapper, int(x), int(y), margin=int(margin))

        def try_nearby_tree(root, *, deadline: float, max_nodes: int = 160) -> Optional[dict[str, str]]:
            try:
                from collections import deque

                nodes = deque([root])
                visited = 0
                while nodes and time.monotonic() <= float(deadline) and visited < int(max_nodes):
                    node = nodes.popleft()
                    visited += 1
                    near = wrapper_contains_point(node, margin=18)
                    if near:
                        found = try_wrapper(node, allow_text_url=True)
                        if found is not None:
                            return found
                    if not near and visited > 1:
                        continue
                    try:
                        for child in node.children():
                            if time.monotonic() > float(deadline) or visited + len(nodes) >= int(max_nodes):
                                break
                            nodes.append(child)
                    except Exception:
                        pass
            except Exception:
                pass
            return None

        try:
            current = desktop.from_point(int(x), int(y))
            for _ in range(10):
                found = try_wrapper(current, allow_text_url=True)
                if found is not None:
                    payload = found
                    break
                try:
                    parent = current.parent()
                except Exception:
                    break
                if parent is None or parent == current:
                    break
                current = parent
        except Exception:
            pass

        if payload is None and int(top_hwnd) != 0:
            try:
                top_wrapper = desktop.window(handle=int(top_hwnd)).wrapper_object()
                # 150ms 硬性截止时间，防止在复杂网页中陷入长达 1.5 秒的全量 UIA 查找
                deadline = time.monotonic() + 0.15
                for item in top_wrapper.descendants(control_type="Hyperlink"):
                    if time.monotonic() > deadline:
                        break
                    found = try_wrapper(item)
                    if found is not None:
                        payload = found
                        break
                if payload is None and time.monotonic() <= deadline:
                    payload = try_nearby_tree(top_wrapper, deadline=deadline)
            except Exception:
                pass
    except ImportError:
        error = "import"
    except Exception as exc:
        error = type(exc).__name__
    try:
        result_queue.put_nowait({"seq": int(seq), "payload": payload, "error": error})
    except Exception:
        pass


@dataclass(frozen=True)
class SelectedRegion:
    left: int
    top: int
    width: int
    height: int
    logical_left: int
    logical_top: int
    logical_width: int
    logical_height: int
    frozen_screen_bgr: object | None = None
    frozen_screen_left: int = 0
    frozen_screen_top: int = 0

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.width, self.height)

    def logical_tuple(self) -> tuple[int, int, int, int]:
        return (self.logical_left, self.logical_top, self.logical_width, self.logical_height)


class RegionOverlay(QWidget):
    confirmed = pyqtSignal(object)
    canceled = pyqtSignal()
    linkHovered = pyqtSignal(object)
    _DIMENSION_TIP_MIN_WIDTH = 108
    _DIMENSION_TIP_HEIGHT = 26
    _DIMENSION_TIP_GAP = 6

    def __init__(
        self,
        confirm_delay_ms: int = 250,
        close_on_confirm: bool = True,
        freeze_on_start: bool = False,
        auto_snap: bool = False,
        annotation_style: object = None,
        cursor_shape_provider: Optional[Callable[[QPoint], Optional[Qt.CursorShape]]] = None,
        link_probe_enabled: bool = True,
        link_probe_only: bool = False,
        show_magnifier: bool = True,
        cursor_color: str = "#1E6BFF",
        is_scroll_capture: bool = False,
        show_frozen_background: bool = False,
    ) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(0, 0, 0, 0))
        self.setPalette(palette)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._screen_mappings = _build_screen_dpi_mappings()
        self._screen_geo = _union_rect([mapping.logical for mapping in self._screen_mappings])
        self.setGeometry(QRect(self._screen_geo))

        self._rect: Optional[QRect] = None
        self._drag_mode: Optional[str] = None
        self._drag_anchor: Optional[QPoint] = None
        self._rect_at_press: Optional[QRect] = None
        self._overlay_closed = False
        self._manual_drag_threshold = 4
        self._dot_r = 3
        self._confirm_delay_ms = int(max(0, confirm_delay_ms))
        self._close_on_confirm = bool(close_on_confirm)
        self._freeze_on_start = bool(freeze_on_start)
        self._link_probe_only = bool(link_probe_only)
        self._auto_snap = bool(auto_snap) and not bool(self._link_probe_only)
        self._link_probe_enabled = bool(link_probe_enabled)
        self._show_magnifier = bool(show_magnifier) and not bool(self._link_probe_only)
        self._show_frozen_background = bool(show_frozen_background)
        self._cursor_color = str(cursor_color or "#1E6BFF")
        self._is_scroll_capture = bool(is_scroll_capture)
        self._annotation_style = normalize_annotation_style(annotation_style)
        self._cursor_shape_provider = cursor_shape_provider
        self._external_cursor_active = False
        self._uia_desktop = None
        self._uia_unavailable = False
        self._snap_locked_rect: Optional[QRect] = None
        self._snap_locked_refined = False
        self._snap_cache_hwnd = 0
        self._snap_cache_rects_px: list[tuple[int, int, int, int]] = []
        self._snap_spatial_index: Optional[_SnapSpatialIndex] = None
        self._snap_cache_ts = 0.0
        self._snap_cache_ttl = 3600.0
        self._snap_last_scan_point: Optional[tuple[int, int]] = None
        self._snap_mouse_history: list[tuple[int, int, float]] = []  # 存储 (x, y, timestamp) 轨迹历史
        self._snap_current_velocity = 0.0  # 鼠标当前运动速度 (px/s)
        self._snap_parent_area_switch_ratio = 1.12
        self._snap_large_fallback_area_ratio = 0.64
        self._snap_last_point_px: Optional[tuple[int, int]] = None
        self._snap_last_move_ts = 0.0
        self._snap_last_update_point_px: Optional[tuple[int, int]] = None
        self._snap_last_update_ts = 0.0
        self._snap_min_update_interval_s = 0.035
        self._snap_hard_min_update_interval_s = 0.016
        self._snap_min_update_distance_px = 12
        self._snap_last_probe_point_px: Optional[tuple[int, int]] = None
        self._snap_last_probe_ts = 0.0
        self._snap_inside_rect_probe_interval_s = 0.085
        self._snap_worker_poll_ms = 45
        self._snap_idle_delay = 0.0
        self._snap_show_ts = float(time.monotonic())
        self._snap_fast_start_ms = 0.0
        self._snap_top_window_hwnd = 0
        self._snap_top_window_rect_px: Optional[tuple[int, int, int, int]] = None
        self._snap_top_window_ts = 0.0
        self._snap_top_window_cache_ttl_s = 0.35
        self._snap_child_window_parent_hwnd = 0
        self._snap_child_window_hwnd = 0
        self._snap_child_window_rect_px: Optional[tuple[int, int, int, int]] = None
        self._snap_child_window_ts = 0.0
        self._snap_child_window_cache_ttl_s = 0.25
        self._cv_edge_cache_key: Optional[tuple[int, int]] = None
        self._cv_edge_cache_result: Optional[tuple[int, int, int, int]] = None
        self._cv_edge_cache_ts = 0.0
        self._cv_edge_cache_bucket_px = 8
        self._cv_edge_cache_ttl_s = 0.08
        self._cv_edge_tile_px = 96
        self._cv_edge_tile_key: Optional[tuple[int, int, int]] = None
        self._cv_edge_tile_rects: list[tuple[int, int, int, int]] = []
        self._snap_anim: Optional[QVariantAnimation] = None
        self._snap_anim_target: Optional[QRect] = None
        self._snap_anim_duration_ms = 110
        self._snap_worker_queue: "queue.Queue[dict]" = queue.Queue()
        self._snap_worker_thread: Optional[object] = None
        self._snap_worker_seq = 0
        self._snap_worker_hwnd = 0
        self._snap_worker_point_px: Optional[tuple[int, int]] = None
        self._snap_worker_started_ts = 0.0
        self._snap_worker_timeout = 0.45
        self._snap_worker_retarget_s = 0.08
        self._snap_precise_last_request_ts = 0.0
        self._snap_precise_min_request_s = 0.025
        self._snap_precise_start_delay_s = 0.28
        self._snap_precise_ready_ts = self._snap_show_ts + float(self._snap_precise_start_delay_s)
        self._snap_precise_blocked_until: dict[int, float] = {}
        self._link_probe_queue: "queue.Queue[dict]" = queue.Queue()
        self._link_probe_thread: Optional[object] = None
        self._link_probe_seq = 0
        self._link_probe_started_ts = 0.0
        self._link_probe_hwnd = 0
        self._link_probe_last_request_ts = 0.0
        self._link_probe_last_point_px: Optional[tuple[int, int]] = None
        self._link_probe_same_point_retry_s = 0.08 if bool(self._link_probe_only) else 0.75
        self._link_probe_min_request_s = 0.02 if bool(self._link_probe_only) else 0.18
        self._link_probe_point_epsilon = 1 if bool(self._link_probe_only) else 4
        self._link_probe_empty_retries = 0
        self._link_probe_empty_retry_limit = 18 if bool(self._link_probe_only) else 0
        self._link_probe_unavailable = False
        self._link_last_key = ""
        self._link_last_seen_ts = 0.0
        self._cursor_override_active = False
        self._cursor_updates_enabled = False
        self._freeze_pixmap: Optional[QPixmap] = None
        self._freeze_qimage: Optional[QImage] = None
        self._freeze_segments: list[tuple[QRect, QPixmap]] = []
        self._freeze_logical_rect = QRect(self._screen_geo)
        self._freeze_screen_bgr = None
        self._freeze_screen_left = 0
        self._freeze_screen_top = 0
        self._confirmed = False
        self._pending_confirm = False
        self._selection_visual_visible = not bool(self._link_probe_only)
        self._mouse_passthrough = False
        self._mouse_passthrough_prev_style: Optional[int] = None
        self._wheel_passthrough_active = False
        self._wheel_opacity_before: Optional[float] = None
        self._confirm_timer = QTimer(self)
        self._confirm_timer.setSingleShot(True)
        self._confirm_timer.timeout.connect(self._confirm_current_selection)
        self._input_restore_timer = QTimer(self)
        self._input_restore_timer.setSingleShot(True)
        self._input_restore_timer.timeout.connect(self._restore_mouse_input_passthrough)
        self._freeze_refresh_timer = QTimer(self)
        self._freeze_refresh_timer.setSingleShot(True)
        self._freeze_refresh_timer.timeout.connect(self._refresh_freeze_snapshot_after_scroll)
        self._snap_timer = QTimer(self)
        self._snap_timer.setSingleShot(True)
        self._snap_timer.setInterval(int(self._snap_worker_poll_ms))
        self._snap_timer.timeout.connect(self._refresh_auto_snap_rect)
        self._external_cursor_timer = QTimer(self)
        self._external_cursor_timer.setInterval(35)
        self._external_cursor_timer.timeout.connect(
            lambda: self._force_link_probe_cursor() if bool(self._link_probe_only) else self._sync_external_cursor(QCursor.pos())
        )
        if bool(self._link_probe_only):
            self._external_cursor_timer.start()
        self._link_probe_timer = QTimer(self)
        self._link_probe_timer.setInterval(55 if bool(self._link_probe_only) else 120)
        self._link_probe_timer.timeout.connect(self._poll_hover_link)
        if bool(self._link_probe_enabled):
            self._link_probe_timer.start()
        self._cursor_cross = self._build_cross_cursor(self._cursor_color)
        self._magnifier = None
        self._magnifier_last_update_ts = 0.0
        self._prev_active_hwnd = 0
        if os.name == "nt":
            try:
                import ctypes
                fg = ctypes.windll.user32.GetForegroundWindow()
                if fg and int(fg) != int(self.winId()):
                    self._prev_active_hwnd = int(fg)
            except Exception:
                pass
        self._magnifier_update_min_interval_s = 0.033
        if bool(self._freeze_on_start):
            self._capture_freeze_snapshot()
        if bool(self._show_magnifier):
            from deepcat.ui.magnifier_overlay import MagnifierOverlay

            self._magnifier = MagnifierOverlay(self)
            self._sync_magnifier_source()
            self._magnifier.show()

        # 稍后阅读预探测：在覆盖层初始化的瞬间，直接同步触发首次链接探测，
        # 完全跳过事件循环和定时器空等，快捷键按下的千分之一秒内后台线程即已启动。
        if bool(self._link_probe_enabled) and bool(self._link_probe_only):
            try:
                self._poll_hover_link(force=True)
            except Exception:
                pass
            self._schedule_link_probe_burst()

    def _restore_screen_geometry(self) -> None:
        try:
            if bool(self.windowState() & Qt.WindowState.WindowFullScreen):
                self.setWindowState(self.windowState() & ~Qt.WindowState.WindowFullScreen)
        except Exception:
            pass
        try:
            self.setGeometry(QRect(self._screen_geo))
        except Exception:
            pass

    def show_capture_overlay(self) -> None:
        self._restore_screen_geometry()
        self.show()
        self._enable_link_probe_input_passthrough()
        QTimer.singleShot(0, self._restore_screen_geometry)
        QTimer.singleShot(30, self._restore_screen_geometry)

    def _release_cursor_override(self) -> None:
        """只释放本覆盖层持有的一层应用级光标，重复调用不会破坏其他状态。"""
        if not bool(getattr(self, "_cursor_override_active", False)):
            return
        self._cursor_override_active = False
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass

    def _suspend_cursor_updates(self) -> None:
        self._cursor_updates_enabled = False
        try:
            self._external_cursor_timer.stop()
        except Exception:
            pass
        self._release_cursor_override()
        try:
            self.unsetCursor()
        except Exception:
            pass

    def _resume_cursor_updates(self) -> bool:
        enabled = not bool(getattr(self, "_confirmed", False))
        self._cursor_updates_enabled = enabled
        if enabled and bool(getattr(self, "_link_probe_only", False)):
            try:
                self._external_cursor_timer.start()
            except Exception:
                pass
        return enabled

    def _restore_cursor_after_modal(self, was_override: bool) -> None:
        if not bool(was_override):
            return
        try:
            can_restore = bool(
                not self._overlay_closed
                and self._cursor_updates_enabled
                and not self._confirmed
                and self.isVisible()
            )
        except (AttributeError, RuntimeError):
            return
        if not can_restore:
            return
        cursor = getattr(self, "_cursor_cross", None)
        if cursor is not None:
            self._set_overlay_cursor(cursor)

    def _enable_link_probe_input_passthrough(self) -> None:
        if not bool(getattr(self, "_link_probe_only", False)):
            return
        try:
            self._set_mouse_input_passthrough(True)
        except Exception:
            pass

    def _exclude_window_from_capture(self) -> None:
        try:
            import ctypes

            hwnd = int(self.winId())
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            ctypes.windll.user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), ctypes.c_uint(WDA_EXCLUDEFROMCAPTURE))
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        self._overlay_closed = True
        self._suspend_cursor_updates()
        try:
            self._snap_worker_seq += 1
            self._snap_worker_thread = None
            self._snap_worker_hwnd = 0
            self._snap_worker_point_px = None
            self._snap_worker_started_ts = 0.0
        except Exception:
            pass
        try:
            self._link_probe_seq += 1
            self._link_probe_thread = None
            self._link_probe_hwnd = 0
            self._link_probe_started_ts = 0.0
        except Exception:
            pass
        try:
            self._confirm_timer.stop()
        except Exception:
            pass
        try:
            self._snap_timer.stop()
        except Exception:
            pass
        try:
            self._link_probe_timer.stop()
        except Exception:
            pass
        try:
            self._freeze_refresh_timer.stop()
        except Exception:
            pass
        try:
            self._input_restore_timer.stop()
            self._restore_mouse_input_passthrough()
        except Exception:
            pass
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        try:
            if getattr(self, "_magnifier", None) is not None:
                self._magnifier.close()
        except Exception:
            pass
        event.accept()

    def hideEvent(self, event) -> None:
        self._suspend_cursor_updates()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        self._overlay_closed = False
        self._restore_screen_geometry()
        cursor_updates_enabled = self._resume_cursor_updates()
        if cursor_updates_enabled:
            self._set_overlay_cursor(self._cursor_cross)
        self._exclude_window_from_capture()
        try:
            if self._magnifier is not None:
                self._magnifier.update_at(QCursor.pos())
                self._magnifier.show()
                self._magnifier.raise_()
        except Exception:
            pass
        super().showEvent(event)
        try:
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            self.grabKeyboard()
        except Exception:
            pass
        if bool(self._auto_snap):
            self._refresh_auto_snap_rect(allow_precise=False)
            QTimer.singleShot(0, lambda: self._refresh_auto_snap_rect(allow_precise=False))
            QTimer.singleShot(48, lambda: self._refresh_auto_snap_rect(allow_precise=False))
            # UIA from_point 在 Windows 输入同步阶段偶发触发 0x8001010d 原生崩溃；
            # 覆盖层显示稳定后再启动精确扫描，避免和全局快捷键/鼠标钩子回调重叠。
            delay_ms = max(120, int(float(getattr(self, "_snap_precise_start_delay_s", 0.28) or 0.28) * 1000))
            QTimer.singleShot(delay_ms, lambda: self._refresh_auto_snap_rect(allow_precise=True))
        if cursor_updates_enabled and bool(self._link_probe_only):
            self._force_link_probe_cursor()
            QTimer.singleShot(0, self._force_link_probe_cursor)
            QTimer.singleShot(80, self._force_link_probe_cursor)
            self._schedule_link_probe_burst()

    def _sync_magnifier_source(self) -> None:
        magnifier = getattr(self, "_magnifier", None)
        if magnifier is None:
            return
        try:
            if bool(self._freeze_on_start) and self._freeze_qimage is not None and not self._freeze_qimage.isNull():
                magnifier.set_source_image(
                    self._freeze_qimage,
                    int(self._freeze_screen_left),
                    int(self._freeze_screen_top),
                    float(_mapping_for_logical_point(QCursor.pos(), self._screen_mappings).dpr),
                )
            else:
                magnifier.set_source_image(None)
        except Exception:
            pass

    def _build_cross_cursor(self, color: str = "#1E6BFF", *, width: int = 2) -> QCursor:
        size = 32
        c = size // 2
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        stroke = max(1, int(width))
        # 先画一圈白色描边，再叠主题色核心，深浅截图背景下都保持清晰轮廓
        outline_width = stroke + 2
        painter.setPen(
            QPen(
                QColor(255, 255, 255, 245),
                outline_width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.SquareCap,
            )
        )
        painter.drawLine(0, c, size, c)
        painter.drawLine(c, 0, c, size)
        painter.setPen(
            QPen(
                QColor(str(color or "#1E6BFF")),
                stroke,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.SquareCap,
            )
        )
        painter.drawLine(0, c, size, c)
        painter.drawLine(c, 0, c, size)
        painter.end()
        return QCursor(pm, c, c)

    def _force_link_probe_cursor(self) -> None:
        if bool(getattr(self, "_overlay_closed", False)) or not bool(
            getattr(self, "_cursor_updates_enabled", False)
        ):
            return
        if not bool(getattr(self, "_link_probe_only", False)):
            return
        cursor = getattr(self, "_cursor_cross", None)
        if cursor is None:
            return
        external_shape = self._external_cursor_shape(QCursor.pos())
        if external_shape is not None:
            try:
                external_cursor = QCursor(external_shape)
                self.setCursor(external_cursor)
                if bool(getattr(self, "_cursor_override_active", False)):
                    QApplication.changeOverrideCursor(external_cursor)
                else:
                    QApplication.setOverrideCursor(external_cursor)
                    self._cursor_override_active = True
            except Exception:
                pass
            return
        try:
            self.setCursor(cursor)
        except Exception:
            pass
        try:
            if bool(getattr(self, "_cursor_override_active", False)):
                QApplication.changeOverrideCursor(cursor)
            else:
                QApplication.setOverrideCursor(cursor)
                self._cursor_override_active = True
        except Exception:
            pass
        return

    def _selection_rect(self) -> Optional[QRect]:
        if self._rect is None:
            return None
        r = self._rect.normalized()
        if r.width() <= 2 or r.height() <= 2:
            return None
        return QRect(int(r.x()), int(r.y()), int(r.width()), int(r.height()))

    def _selection_repaint_rect(self, previous: Optional[QRect], current: Optional[QRect]) -> QRect:
        if previous is None and current is None:
            return QRect()
        if previous is None or current is None:
            return QRect(self.rect())
        dirty = QRect(previous).normalized().united(QRect(current).normalized())
        return dirty.adjusted(-160, -72, 160, 112).intersected(self.rect())

    def _request_selection_repaint(self, previous: Optional[QRect], current: Optional[QRect]) -> None:
        try:
            dirty = self._selection_repaint_rect(previous, current)
        except Exception:
            dirty = QRect()
        try:
            if dirty.isNull() or dirty.isEmpty():
                self.update()
            else:
                self.update(dirty)
        except TypeError:
            try:
                self.update()
            except Exception:
                pass
        except Exception:
            pass

    def _set_selection_rect_for_overlay(self, rect: Optional[QRect]) -> None:
        previous = self._selection_rect()
        self._rect = QRect(rect) if rect is not None else None
        current = self._selection_rect()
        if previous == current:
            return
        self._request_selection_repaint(previous, current)

    def _snap_anim_running(self) -> bool:
        anim = self.__dict__.get("_snap_anim")
        try:
            return anim is not None and anim.state() == QVariantAnimation.State.Running
        except Exception:
            return False

    def _stop_snap_animation(self, *, commit: bool = True) -> None:
        anim = self.__dict__.get("_snap_anim")
        target = self.__dict__.get("_snap_anim_target")
        self._snap_anim_target = None
        if anim is None:
            return
        try:
            anim.stop()
        except Exception:
            pass
        if bool(commit) and target is not None:
            self._set_selection_rect_for_overlay(QRect(target))

    def _snap_logical_selection_rect(self) -> Optional[QRect]:
        """吸附判定用的选区：动画进行中取目标矩形，避免用中间帧做滞后判断。"""
        if self._snap_anim_running():
            target = self.__dict__.get("_snap_anim_target")
            if target is not None:
                return QRect(target)
        return self._selection_rect()

    def _animate_snap_rect(self, rect: QRect) -> None:
        """自动吸附切换目标时做一段短促的补间，让选区“滑”过去而不是跳过去。"""
        target = QRect(rect)
        start = self._selection_rect()
        duration = int(self.__dict__.get("_snap_anim_duration_ms", 110) or 0)
        if start is None or duration <= 0:
            self._stop_snap_animation(commit=False)
            self._set_selection_rect_for_overlay(target)
            return
        if self._snap_anim_running() and self.__dict__.get("_snap_anim_target") == target:
            return
        if start == target:
            self._stop_snap_animation(commit=False)
            return
        anim = self.__dict__.get("_snap_anim")
        if anim is None:
            try:
                anim = QVariantAnimation(self)
                anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                anim.valueChanged.connect(self._on_snap_anim_value)
                anim.finished.connect(self._on_snap_anim_finished)
            except Exception:
                self._snap_anim_target = None
                self._set_selection_rect_for_overlay(target)
                return
            self._snap_anim = anim
        else:
            try:
                anim.stop()
            except Exception:
                pass
        self._snap_anim_target = target
        anim.setDuration(duration)
        anim.setStartValue(QRect(start))
        anim.setEndValue(target)
        anim.start()

    def _on_snap_anim_value(self, value) -> None:
        if bool(self.__dict__.get("_overlay_closed", False)) or self._drag_mode is not None:
            return
        rect = value.toRect() if hasattr(value, "toRect") else value
        if isinstance(rect, QRect):
            self._set_selection_rect_for_overlay(rect)

    def _on_snap_anim_finished(self) -> None:
        target = self.__dict__.get("_snap_anim_target")
        self._snap_anim_target = None
        if target is not None and self._drag_mode is None and not bool(self.__dict__.get("_overlay_closed", False)):
            self._set_selection_rect_for_overlay(QRect(target))

    def _style_hex(self, key: str, default: str = "#FF0000") -> str:
        color = QColor(str(self._annotation_style.get(str(key), default) or default))
        return color.name(QColor.NameFormat.HexRgb).upper() if color.isValid() else str(default)

    def _dimension_tip_rect(self, anchor_rect: QRectF, text: str, painter: QPainter) -> QRectF:
        font = painter.font()
        font.setPointSize(9)
        font.setBold(False)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tip_w = max(float(self._DIMENSION_TIP_MIN_WIDTH), float(fm.horizontalAdvance(text)) + 22.0)
        tip_h = max(float(self._DIMENSION_TIP_HEIGHT), float(fm.height()) + 10.0)
        x = float(anchor_rect.left() + (anchor_rect.width() - tip_w) / 2.0)
        x = max(2.0, min(x, max(2.0, float(self.width()) - tip_w - 2.0)))
        above_y = float(anchor_rect.top()) - tip_h - float(self._DIMENSION_TIP_GAP)
        y = above_y if above_y >= 2.0 else float(anchor_rect.top()) + float(self._DIMENSION_TIP_GAP)
        y = max(2.0, min(y, max(2.0, float(self.height()) - tip_h - 2.0)))
        return QRectF(x, y, tip_w, tip_h)

    def _draw_dimension_tip(self, painter: QPainter, anchor_rect: QRectF, text: str) -> None:
        tip_rect = self._dimension_tip_rect(anchor_rect, text, painter)
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 23, 42, 28))
            painter.drawRoundedRect(tip_rect.translated(0.0, 1.0), 8.0, 8.0)
            painter.setBrush(QColor(255, 255, 255, 246))
            painter.drawRoundedRect(tip_rect, 8.0, 8.0)
            painter.setPen(QPen(QColor(203, 213, 225, 230), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(tip_rect.adjusted(0.5, 0.5, -0.5, -0.5), 8.0, 8.0)
            painter.setPen(QColor(51, 65, 85))
            painter.drawText(tip_rect.adjusted(10, 0, -10, 0), int(Qt.AlignmentFlag.AlignCenter), text)
        finally:
            painter.restore()

    def _apply_pen_line_style(self, pen: QPen) -> None:
        if str(self._annotation_style.get("line_style", "solid")) == "solid":
            pen.setStyle(Qt.PenStyle.SolidLine)
        else:
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([5.0, 4.0])

    def set_selection_visual_visible(self, visible: bool) -> None:
        self._selection_visual_visible = bool(visible)
        try:
            rect = self._selection_rect()
            if rect is None:
                self.update()
            else:
                self._request_selection_repaint(rect, rect)
        except Exception:
            pass

    def set_selection_rect(self, rect: QRect) -> None:
        self._set_selection_rect_for_overlay(QRect(rect))

    def _hit_test(self, p: QPoint) -> Optional[str]:
        rect = self._selection_rect()
        if rect is None:
            return None
        hs = 10
        tl = QRect(rect.left() - hs, rect.top() - hs, hs * 2, hs * 2)
        tr = QRect(rect.right() - hs, rect.top() - hs, hs * 2, hs * 2)
        bl = QRect(rect.left() - hs, rect.bottom() - hs, hs * 2, hs * 2)
        br = QRect(rect.right() - hs, rect.bottom() - hs, hs * 2, hs * 2)
        if tl.contains(p):
            return "resize_tl"
        if tr.contains(p):
            return "resize_tr"
        if bl.contains(p):
            return "resize_bl"
        if br.contains(p):
            return "resize_br"
        if rect.contains(p):
            return "move"
        return None

    def _update_cursor(self, p: QPoint) -> None:
        hit = self._hit_test(p)
        if hit in {"resize_tl", "resize_br"}:
            self._set_overlay_cursor(Qt.CursorShape.SizeFDiagCursor)
        elif hit in {"resize_tr", "resize_bl"}:
            self._set_overlay_cursor(Qt.CursorShape.SizeBDiagCursor)
        elif hit == "move":
            self._set_overlay_cursor(Qt.CursorShape.SizeAllCursor)
        else:
            self._set_overlay_cursor(self._cursor_cross)

    def _set_overlay_cursor(self, cursor) -> None:
        if bool(getattr(self, "_overlay_closed", False)) or not bool(
            getattr(self, "_cursor_updates_enabled", False)
        ):
            return
        qcursor = cursor if isinstance(cursor, QCursor) else QCursor(cursor)
        try:
            self.setCursor(qcursor)
        except Exception:
            pass
        if bool(getattr(self, "_link_probe_only", False)):
            return
        try:
            if bool(getattr(self, "_cursor_override_active", False)):
                QApplication.changeOverrideCursor(qcursor)
            else:
                QApplication.setOverrideCursor(qcursor)
                self._cursor_override_active = True
        except Exception:
            pass

    def _external_cursor_shape(self, global_pos: QPoint) -> Optional[Qt.CursorShape]:
        provider = self._cursor_shape_provider
        if provider is None:
            return None
        try:
            return provider(QPoint(global_pos))
        except Exception:
            return None

    def _sync_external_cursor(self, global_pos: QPoint) -> bool:
        if bool(getattr(self, "_overlay_closed", False)) or not bool(
            getattr(self, "_cursor_updates_enabled", False)
        ):
            return False
        if bool(getattr(self, "_link_probe_only", False)):
            if self._external_cursor_shape(global_pos) is not None:
                self._force_link_probe_cursor()
                return True
            self._force_link_probe_cursor()
            return False
        shape = self._external_cursor_shape(global_pos)
        if shape is None:
            if bool(self._external_cursor_active):
                self._external_cursor_active = False
                self._set_overlay_cursor(self._cursor_cross)
            return False
        self._external_cursor_active = True
        self._set_overlay_cursor(QCursor(shape))
        try:
            if getattr(self, "_magnifier", None) is not None:
                self._magnifier.hide()
        except Exception:
            pass
        return True

    def _global_logical_point(self, p: QPoint) -> QPoint:
        origin = self.geometry().topLeft()
        return QPoint(int(origin.x() + p.x()), int(origin.y() + p.y()))

    def _global_logical_point_to_physical(self, point: QPoint) -> QPoint:
        return _logical_point_to_physical(QPoint(point), self._screen_mappings)

    def _local_point_to_physical(self, point: QPoint) -> QPoint:
        return self._global_logical_point_to_physical(self._global_logical_point(point))

    def _dpr_for_local_rect(self, rect: QRect) -> float:
        try:
            center = self._global_logical_point(rect.center())
            return float(_mapping_for_logical_point(center, self._screen_mappings).dpr)
        except Exception:
            try:
                return float(self._screen_mappings[0].dpr)
            except Exception:
                try:
                    from PyQt6.QtWidgets import QApplication
                    primary = QApplication.primaryScreen()
                    if primary is not None:
                        return float(primary.devicePixelRatio())
                except Exception:
                    pass
                return 1.0

    def _logical_rect_to_local(self, left: int, top: int, right: int, bottom: int) -> Optional[QRect]:
        origin = self.geometry().topLeft()
        logical = _physical_rect_to_logical_rect(int(left), int(top), int(right), int(bottom), self._screen_mappings)
        rect = QRect(
            int(logical.x() - origin.x()),
            int(logical.y() - origin.y()),
            max(1, int(logical.width())),
            max(1, int(logical.height())),
        ).intersected(self.rect())
        if rect.width() <= 4 or rect.height() <= 4:
            return None
        return rect

    def _window_rect_px(self, hwnd: int, *, top_level: bool) -> Optional[tuple[int, int, int, int]]:
        if hwnd == 0:
            return None
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            if bool(top_level):
                try:
                    DWMWA_EXTENDED_FRAME_BOUNDS = 9
                    hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                        wintypes.HWND(hwnd),
                        ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
                        ctypes.byref(rect),
                        ctypes.sizeof(rect),
                    )
                    if int(hr) == 0:
                        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
                except Exception:
                    pass
            if not ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return None
            return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        except Exception:
            return None

    def _window_is_usable(self, hwnd: int, x: int, y: int) -> bool:
        if hwnd == 0:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            if not ctypes.windll.user32.IsWindowVisible(wintypes.HWND(hwnd)):
                return False
            if ctypes.windll.user32.IsIconic(wintypes.HWND(hwnd)):
                return False
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            if int(pid.value) == int(ctypes.windll.kernel32.GetCurrentProcessId()):
                return False
            try:
                cloaked = ctypes.c_int(0)
                DWMWA_CLOAKED = 14
                hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                    wintypes.HWND(hwnd),
                    ctypes.c_uint(DWMWA_CLOAKED),
                    ctypes.byref(cloaked),
                    ctypes.sizeof(cloaked),
                )
                if int(hr) == 0 and int(cloaked.value) != 0:
                    return False
            except Exception:
                pass
            rect = self._window_rect_px(hwnd, top_level=True)
            if rect is None:
                return False
            l, t, r, b = rect
            return int(r - l) > 8 and int(b - t) > 8 and int(l) <= int(x) < int(r) and int(t) <= int(y) < int(b)
        except Exception:
            return False

    def _top_window_at_px(self, x: int, y: int) -> int:
        if not bool(self._auto_snap):
            return 0
        now = float(time.monotonic())
        state = self.__dict__
        cached_hwnd = int(state.get("_snap_top_window_hwnd", 0) or 0)
        cached_rect = state.get("_snap_top_window_rect_px")
        cache_ts = float(state.get("_snap_top_window_ts", 0.0) or 0.0)
        cache_ttl = float(state.get("_snap_top_window_cache_ttl_s", 0.35) or 0.35)
        if (
            cached_hwnd
            and cached_rect is not None
            and now - cache_ts <= cache_ttl
            and _rect_contains_px_tuple(tuple(map(int, cached_rect)), int(x), int(y))
        ):
            return cached_hwnd
        hwnd = self._top_window_at_px_for_input(x, y)
        if int(hwnd) != 0:
            rect = self._window_rect_px(int(hwnd), top_level=True)
            if rect is not None:
                state["_snap_top_window_hwnd"] = int(hwnd)
                state["_snap_top_window_rect_px"] = tuple(map(int, rect))
                state["_snap_top_window_ts"] = now
            else:
                state["_snap_top_window_hwnd"] = 0
                state["_snap_top_window_rect_px"] = None
                state["_snap_top_window_ts"] = 0.0
        return int(hwnd)

    def _top_window_rect_for_snap(self, hwnd: int, x: int, y: int) -> Optional[tuple[int, int, int, int]]:
        if int(hwnd) == 0:
            return None
        now = float(time.monotonic())
        state = self.__dict__
        cached_rect = state.get("_snap_top_window_rect_px")
        if (
            int(state.get("_snap_top_window_hwnd", 0) or 0) == int(hwnd)
            and cached_rect is not None
            and now - float(state.get("_snap_top_window_ts", 0.0) or 0.0)
            <= float(state.get("_snap_top_window_cache_ttl_s", 0.35) or 0.35)
            and _rect_contains_px_tuple(tuple(map(int, cached_rect)), int(x), int(y))
        ):
            return tuple(map(int, cached_rect))
        rect = self._window_rect_px(int(hwnd), top_level=True)
        if rect is not None:
            state["_snap_top_window_hwnd"] = int(hwnd)
            state["_snap_top_window_rect_px"] = tuple(map(int, rect))
            state["_snap_top_window_ts"] = now
        return rect

    def _top_window_at_px_for_input(self, x: int, y: int) -> int:
        try:
            import ctypes
            from ctypes import wintypes

            found = ctypes.c_void_p(0)
            enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def _enum_proc(hwnd, _lparam):
                if self._window_is_usable(int(hwnd), int(x), int(y)):
                    found.value = int(hwnd)
                    return False
                return True

            ctypes.windll.user32.EnumWindows(enum_proc_type(_enum_proc), 0)
            return int(found.value or 0)
        except Exception:
            return 0

    def _pack_mouse_screen_lparam(self, x: int, y: int) -> int:
        return int(((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF))

    def _pack_mouse_wparam(self, delta: int, modifiers: Qt.KeyboardModifier) -> int:
        keys = 0
        try:
            if bool(modifiers & Qt.KeyboardModifier.ShiftModifier):
                keys |= 0x0004
            if bool(modifiers & Qt.KeyboardModifier.ControlModifier):
                keys |= 0x0008
        except Exception:
            pass
        return int(((int(delta) & 0xFFFF) << 16) | keys)

    def _set_mouse_input_passthrough(self, enabled: bool) -> bool:
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = int(self.winId())
            if hwnd == 0:
                return False
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_LAYERED = 0x00080000
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020

            try:
                get_long = user32.GetWindowLongPtrW
                set_long = user32.SetWindowLongPtrW
            except AttributeError:
                get_long = user32.GetWindowLongW
                set_long = user32.SetWindowLongW
            get_long.restype = ctypes.c_ssize_t
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            set_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]

            current = int(get_long(wintypes.HWND(hwnd), GWL_EXSTYLE))
            if bool(enabled):
                if not bool(self._mouse_passthrough):
                    self._mouse_passthrough_prev_style = int(current)
                next_style = int(current | WS_EX_TRANSPARENT | WS_EX_LAYERED)
            else:
                prev = self._mouse_passthrough_prev_style
                next_style = int(prev if prev is not None else (current & ~WS_EX_TRANSPARENT))
            if int(next_style) != int(current):
                set_long(wintypes.HWND(hwnd), GWL_EXSTYLE, ctypes.c_ssize_t(int(next_style)))
                user32.SetWindowPos(
                    wintypes.HWND(hwnd),
                    wintypes.HWND(0),
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
                )
            self._mouse_passthrough = bool(enabled)
            if not bool(enabled):
                self._mouse_passthrough_prev_style = None
            return True
        except Exception:
            return False

    def _restore_mouse_input_passthrough(self) -> None:
        refresh_freeze = bool(self._wheel_passthrough_active and self._freeze_on_start and not self._confirmed)
        opacity_before = self._wheel_opacity_before
        if bool(refresh_freeze):
            was_visible = bool(self.isVisible())
            try:
                if bool(was_visible):
                    self.setVisible(False)
                    QApplication.processEvents()
                self._capture_freeze_snapshot()
            finally:
                if bool(was_visible) and not bool(self._confirmed):
                    self.setVisible(True)
        if opacity_before is not None:
            try:
                self.setWindowOpacity(float(opacity_before))
            except Exception:
                pass
        self._wheel_passthrough_active = False
        self._wheel_opacity_before = None
        self._set_mouse_input_passthrough(False)
        if bool(self._confirmed):
            return
        if bool(refresh_freeze) and bool(self._auto_snap):
            self._invalidate_snap_after_scroll()
            self._refresh_auto_snap_rect(allow_precise=False)
            self.update()
        try:
            self.raise_()
            self.activateWindow()
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            self.grabKeyboard()
        except Exception:
            pass
        try:
            self.setCursor(self._cursor_cross)
        except Exception:
            pass

    def _send_native_wheel_input(self, delta: int, *, vertical: bool, focus_hwnd: int = 0) -> bool:
        try:
            import ctypes
            from ctypes import wintypes

            was_passthrough = bool(self._mouse_passthrough)
            self._set_mouse_input_passthrough(True)
            self._wheel_passthrough_active = True
            if bool(self._freeze_on_start) and self._wheel_opacity_before is None:
                try:
                    self._wheel_opacity_before = float(self.windowOpacity())
                    self.setWindowOpacity(0.01)
                except Exception:
                    self._wheel_opacity_before = None
            try:
                if not bool(was_passthrough) and int(focus_hwnd or 0):
                    target_hwnd = int(focus_hwnd)
                    def _async_set_fg():
                        try:
                            ctypes.windll.user32.SetForegroundWindow(wintypes.HWND(target_hwnd))
                        except Exception:
                            pass
                    QTimer.singleShot(0, _async_set_fg)
            except Exception:
                pass

            ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

            class MOUSEINPUT(ctypes.Structure):
                _fields_ = [
                    ("dx", wintypes.LONG),
                    ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR),
                ]

            class INPUT_UNION(ctypes.Union):
                _fields_ = [("mi", MOUSEINPUT)]

            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]

            MOUSEEVENTF_WHEEL = 0x0800
            MOUSEEVENTF_HWHEEL = 0x01000
            flag = MOUSEEVENTF_WHEEL if bool(vertical) else MOUSEEVENTF_HWHEEL
            inp = INPUT()
            inp.type = 0
            inp.union.mi = MOUSEINPUT(0, 0, int(delta), int(flag), 0, 0)
            sent = int(ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))) == 1
            if not bool(sent):
                ctypes.windll.user32.mouse_event(int(flag), 0, 0, int(delta), 0)
                sent = True
            self._input_restore_timer.start(700)
            return bool(sent)
        except Exception:
            self._restore_mouse_input_passthrough()
            return False

    def _forward_wheel_to_underlying_window(self, event) -> bool:
        try:
            angle = event.angleDelta()
            delta_y = int(angle.y())
            delta_x = int(angle.x())
            if delta_y == 0 and delta_x == 0:
                return False
            vertical = abs(delta_y) >= abs(delta_x)
            delta = int(delta_y if vertical else delta_x)
            p = event.position().toPoint()
            pp = self._local_point_to_physical(p)
            x = int(pp.x())
            y = int(pp.y())
            top_hwnd = self._top_window_at_px_for_input(x, y)
            if top_hwnd == 0:
                return False
            child_hwnd = self._child_window_at_px(top_hwnd, x, y)
            if self._send_native_wheel_input(delta, vertical=bool(vertical), focus_hwnd=int(top_hwnd)):
                return True

            import ctypes
            from ctypes import wintypes

            WM_MOUSEWHEEL = 0x020A
            WM_MOUSEHWHEEL = 0x020E
            SMTO_ABORTIFHUNG = 0x0002
            msg = WM_MOUSEWHEEL if vertical else WM_MOUSEHWHEEL
            wparam = self._pack_mouse_wparam(delta, event.modifiers())
            lparam = self._pack_mouse_screen_lparam(x, y)

            candidates: list[int] = []
            for hwnd in (int(top_hwnd), int(child_hwnd or 0)):
                if hwnd and hwnd not in candidates:
                    candidates.append(hwnd)
            if int(child_hwnd or 0):
                try:
                    GA_ROOT = 2
                    root = int(ctypes.windll.user32.GetAncestor(wintypes.HWND(int(child_hwnd)), ctypes.c_uint(GA_ROOT)) or 0)
                    if root and root not in candidates:
                        candidates.append(root)
                except Exception:
                    pass

            sent = False
            for hwnd in candidates:
                result = ctypes.c_ulonglong(0)
                ok = False
                try:
                    ok = bool(
                        ctypes.windll.user32.SendMessageTimeoutW(
                            wintypes.HWND(int(hwnd)),
                            ctypes.c_uint(int(msg)),
                            wintypes.WPARAM(int(wparam)),
                            wintypes.LPARAM(int(lparam)),
                            ctypes.c_uint(SMTO_ABORTIFHUNG),
                            ctypes.c_uint(60),
                            ctypes.byref(result),
                        )
                    )
                except Exception:
                    ok = False
                if bool(ok):
                    sent = True
                    continue
                try:
                    sent = bool(
                        ctypes.windll.user32.PostMessageW(
                            wintypes.HWND(int(hwnd)),
                            ctypes.c_uint(int(msg)),
                            wintypes.WPARAM(int(wparam)),
                            wintypes.LPARAM(int(lparam)),
                        )
                    ) or sent
                except Exception:
                    pass
            return bool(sent)
        except Exception:
            return False

    def _invalidate_snap_after_scroll(self) -> None:
        self._stop_snap_animation(commit=False)
        self._snap_cache_hwnd = 0
        self._snap_cache_rects_px = []
        self._snap_spatial_index = None
        self._snap_cache_ts = 0.0
        self._snap_locked_rect = None
        self._snap_locked_refined = False
        self._snap_top_window_hwnd = 0
        self._snap_top_window_rect_px = None
        self._snap_top_window_ts = 0.0
        self._snap_child_window_parent_hwnd = 0
        self._snap_child_window_hwnd = 0
        self._snap_child_window_rect_px = None
        self._snap_child_window_ts = 0.0
        self._cv_edge_cache_key = None
        self._cv_edge_cache_result = None
        self._cv_edge_cache_ts = 0.0
        self._cv_edge_tile_key = None
        self._cv_edge_tile_rects = []

    def _refresh_freeze_snapshot_after_scroll(self) -> None:
        if bool(self._confirmed) or not bool(self._freeze_on_start):
            return
        was_visible = bool(self.isVisible())
        try:
            if bool(was_visible):
                self.setVisible(False)
                QApplication.processEvents()
            self._capture_freeze_snapshot()
        finally:
            if bool(was_visible) and not bool(self._confirmed):
                self.setVisible(True)
                self.raise_()
                self._restore_mouse_input_passthrough()
        if bool(self._auto_snap):
            self._invalidate_snap_after_scroll()
            self._refresh_auto_snap_rect(allow_precise=False)
        self.update()

    def _child_window_at_px(self, parent_hwnd: int, x: int, y: int) -> int:
        if parent_hwnd == 0:
            return 0
        now = float(time.monotonic())
        state = self.__dict__
        cached_rect = state.get("_snap_child_window_rect_px")
        cached_hwnd = int(state.get("_snap_child_window_hwnd", 0) or 0)
        if (
            cached_hwnd
            and int(state.get("_snap_child_window_parent_hwnd", 0) or 0) == int(parent_hwnd)
            and cached_rect is not None
            and now - float(state.get("_snap_child_window_ts", 0.0) or 0.0)
            <= float(state.get("_snap_child_window_cache_ttl_s", 0.25) or 0.25)
            and _rect_contains_px_tuple(tuple(map(int, cached_rect)), int(x), int(y))
        ):
            return cached_hwnd
        try:
            import ctypes
            from ctypes import wintypes

            best_hwnd = 0
            best_area = 0
            parent_rect = self._window_rect_px(parent_hwnd, top_level=True)
            parent_area = 0
            if parent_rect is not None:
                parent_area = max(1, int(parent_rect[2] - parent_rect[0]) * int(parent_rect[3] - parent_rect[1]))
            enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def _enum_proc(hwnd, _lparam):
                nonlocal best_hwnd, best_area
                hwnd_i = int(hwnd)
                try:
                    if not ctypes.windll.user32.IsWindowVisible(wintypes.HWND(hwnd_i)):
                        return True
                    rect = self._window_rect_px(hwnd_i, top_level=False)
                    if rect is None:
                        return True
                    l, t, r, b = rect
                    w = int(r - l)
                    h = int(b - t)
                    if w <= 8 or h <= 8:
                        return True
                    if not (int(l) <= int(x) < int(r) and int(t) <= int(y) < int(b)):
                        return True
                    area = int(w * h)
                    if parent_area and area >= int(parent_area * 0.98):
                        return True
                    if best_hwnd == 0 or area < best_area:
                        best_hwnd = hwnd_i
                        best_area = area
                except Exception:
                    return True
                return True

            ctypes.windll.user32.EnumChildWindows(wintypes.HWND(parent_hwnd), enum_proc_type(_enum_proc), 0)
            if int(best_hwnd) != 0:
                rect = self._window_rect_px(int(best_hwnd), top_level=False)
                if rect is not None:
                    state["_snap_child_window_parent_hwnd"] = int(parent_hwnd)
                    state["_snap_child_window_hwnd"] = int(best_hwnd)
                    state["_snap_child_window_rect_px"] = tuple(map(int, rect))
                    state["_snap_child_window_ts"] = now
            return int(best_hwnd)
        except Exception:
            return 0

    def _rect_contains_px(self, rect: tuple[int, int, int, int], x: int, y: int) -> bool:
        l, t, r, b = rect
        return int(l) <= int(x) < int(r) and int(t) <= int(y) < int(b)

    def _rect_contains_px_with_margin(self, rect: tuple[int, int, int, int], x: int, y: int) -> bool:
        l, t, r, b = rect
        w = int(r) - int(l)
        h = int(b) - int(t)
        if w <= 0 or h <= 0:
            return False
        if w < 60 and h < 60:
            margin = 12
        elif w < 150 and h < 150:
            margin = 8
        elif w < 300 and h < 300:
            margin = 4
        else:
            margin = 0
        return int(l) - margin <= int(x) < int(r) + margin and int(t) - margin <= int(y) < int(b) + margin

    def _rect_inside(self, child: tuple[int, int, int, int], parent: tuple[int, int, int, int]) -> bool:
        cl, ct, cr, cb = child
        pl, pt, pr, pb = parent
        return int(cl) >= int(pl) - 12 and int(ct) >= int(pt) - 12 and int(cr) <= int(pr) + 12 and int(cb) <= int(pb) + 12

    def _uia_rect_reasonable(
        self,
        rect: tuple[int, int, int, int],
        top_rect: tuple[int, int, int, int],
        x: Optional[int] = None,
        y: Optional[int] = None,
    ) -> bool:
        w = int(rect[2] - rect[0])
        h = int(rect[3] - rect[1])
        area = int(w * h)
        top_area = max(1, int(top_rect[2] - top_rect[0]) * int(top_rect[3] - top_rect[1]))
        if w < 6 or h < 6 or area < 36 or area >= int(top_area * 0.985):
            return False
        if not self._rect_inside(rect, top_rect):
            return False
        if x is not None and y is not None and not self._rect_contains_px_with_margin(rect, int(x), int(y)):
            return False
        return True

    def _uia_cached_rect_at_px(
        self,
        top_hwnd: int,
        top_rect: tuple[int, int, int, int],
        x: int,
        y: int,
        *,
        allow_scan: bool,
    ) -> Optional[tuple[int, int, int, int]]:
        now = float(time.monotonic())
        cache_valid = (
            int(self._snap_cache_hwnd) == int(top_hwnd)
            and bool(self._snap_cache_rects_px)
            and now - float(self._snap_cache_ts) < float(self._snap_cache_ttl)
        )
        if not bool(cache_valid):
            return None
        is_fast_moving = float(self.__dict__.get("_snap_current_velocity", 0.0) or 0.0) > 600.0
        index = getattr(self, "_snap_spatial_index", None)
        if index is not None:
            best: Optional[tuple[int, int, int, int]] = None
            best_area = -1
            for idx in index.candidates(int(x), int(y)):
                rect = self._snap_cache_rects_px[idx]
                if is_fast_moving:
                    rw = int(rect[2] - rect[0])
                    rh = int(rect[3] - rect[1])
                    if rw < 100 or rh < 100:
                        continue
                if self._rect_contains_px_with_margin(rect, int(x), int(y)):
                    area = int(max(1, rect[2] - rect[0]) * max(1, rect[3] - rect[1]))
                    if best is None or area < best_area:
                        best = rect
                        best_area = area
            return best
        for rect in self._snap_cache_rects_px:
            if is_fast_moving:
                rw = int(rect[2] - rect[0])
                rh = int(rect[3] - rect[1])
                if rw < 100 or rh < 100:
                    continue
            if self._rect_contains_px_with_margin(rect, int(x), int(y)):
                return rect
        return None

    def _cv_edge_rect_at_px(self, x: int, y: int) -> Optional[tuple[int, int, int, int]]:
        try:
            now = float(time.monotonic())
            bucket = int(max(1, int(getattr(self, "_cv_edge_cache_bucket_px", 8) or 8)))
            cache_key = (int(x) // bucket, int(y) // bucket)
            if (
                getattr(self, "_cv_edge_cache_key", None) == cache_key
                and now - float(getattr(self, "_cv_edge_cache_ts", 0.0) or 0.0)
                <= float(getattr(self, "_cv_edge_cache_ttl_s", 0.08) or 0.08)
            ):
                return getattr(self, "_cv_edge_cache_result", None)

            def remember(value: Optional[tuple[int, int, int, int]]) -> Optional[tuple[int, int, int, int]]:
                self._cv_edge_cache_key = cache_key
                self._cv_edge_cache_result = value
                self._cv_edge_cache_ts = now
                return value

            candidates = self._cv_edge_candidates_for_px(int(x), int(y))
            if not candidates:
                return remember(None)

            best_rect = None
            best_area = float('inf')
            is_fast_moving = float(self.__dict__.get("_snap_current_velocity", 0.0) or 0.0) > 600.0
            # 给它增加 4 像素的吸附裕度，使用户对准小线条或文字行边缘时更容易吸附
            margin = 4
            for gx, gy, cw, ch in candidates:
                # 速度过滤：若正在快速移动，则过滤小元素
                if is_fast_moving and (cw < 100 or ch < 100):
                    continue
                if (gx - margin) <= x < (gx + cw + margin) and (gy - margin) <= y < (gy + ch + margin):
                    area = float(cw * ch)
                    if area < best_area:
                        best_rect = (gx, gy, cw, ch)
                        best_area = area

            if best_rect is not None:
                gx, gy, cw, ch = best_rect
                return remember((gx, gy, gx + cw, gy + ch))
            return remember(None)
        except Exception:
            return None

    def _cv_edge_candidates_for_px(self, x: int, y: int) -> list[tuple[int, int, int, int]]:
        """返回鼠标附近的 CV 候选矩形（全局物理坐标 (x, y, w, h)）。

        边缘检测结果按 96px 网格分块缓存：鼠标在同一块内滑动时只做 Python 端的
        包含判定，不再逐帧重跑滤波/Canny/findContours，避免主线程掉帧。
        """
        import cv2
        import numpy as np

        raw = self.__dict__.get("_freeze_screen_bgr")
        if raw is None or not isinstance(raw, np.ndarray) or raw.ndim != 3:
            return []

        left = int(self.__dict__.get("_freeze_screen_left", 0) or 0)
        top = int(self.__dict__.get("_freeze_screen_top", 0) or 0)
        rx = int(x) - left
        ry = int(y) - top
        h, w = int(raw.shape[0]), int(raw.shape[1])
        if not (0 <= rx < w and 0 <= ry < h):
            return []

        tile = int(max(16, int(getattr(self, "_cv_edge_tile_px", 96) or 96)))
        tile_key = (id(raw), rx // tile, ry // tile)
        if getattr(self, "_cv_edge_tile_key", None) == tile_key:
            return list(getattr(self, "_cv_edge_tile_rects", []) or [])

        # 以所在网格块为中心向外扩 150px，保证块内任意点都有足够上下文
        crop_half = 150
        tx0 = (rx // tile) * tile
        ty0 = (ry // tile) * tile
        x_start = max(0, tx0 - crop_half)
        y_start = max(0, ty0 - crop_half)
        x_end = min(w, tx0 + tile + crop_half)
        y_end = min(h, ty0 + tile + crop_half)

        crop = raw[y_start:y_end, x_start:x_end]
        rects: list[tuple[int, int, int, int]] = []
        if crop.shape[0] < 10 or crop.shape[1] < 10:
            self._cv_edge_tile_key = tile_key
            self._cv_edge_tile_rects = rects
            return rects

        if crop.shape[2] == 4:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGRA2GRAY)
        elif crop.shape[2] == 3:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        else:
            self._cv_edge_tile_key = tile_key
            self._cv_edge_tile_rects = rects
            return rects

        # 高斯模糊代替双边滤波：UI 截图边缘锐利，前者快一个量级且检测结果一致
        filtered = cv2.GaussianBlur(gray, (3, 3), 0)
        edges = cv2.Canny(filtered, 30, 100)

        crop_w = int(x_end - x_start)
        crop_h = int(y_end - y_start)

        # 通道一：普通几何边缘 (无膨胀)
        contours_raw, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours_raw:
            cx, cy, cw, ch = cv2.boundingRect(cnt)
            if cw < 6 or ch < 6 or cw > crop_w - 10 or ch > crop_h - 10:
                continue
            if _snap_rect_is_tiny_glyph_like((0, 0, int(cw), int(ch))):
                continue
            rects.append((x_start + cx, y_start + cy, int(cw), int(ch)))

        # 通道二：水平粘连文字行 (15x1 横向膨胀)，只保留扁平长条并回缩膨胀误差
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 1))
        dilated = cv2.dilate(edges, kernel, iterations=1)
        contours_text, _ = cv2.findContours(dilated, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours_text:
            cx, cy, cw, ch = cv2.boundingRect(cnt)
            if cw < 6 or ch < 6 or cw > crop_w - 10 or ch > crop_h - 10:
                continue
            if float(cw) / float(ch) >= 3.0:
                cx_corr = max(0, cx + 7)
                cw_corr = max(1, cw - 14)
                if _snap_rect_is_tiny_glyph_like((0, 0, int(cw_corr), int(ch))):
                    continue
                rects.append((x_start + cx_corr, y_start + cy, int(cw_corr), int(ch)))

        self._cv_edge_tile_key = tile_key
        self._cv_edge_tile_rects = rects
        return list(rects)

    def _drain_snap_worker_results(self) -> None:
        if bool(self.__dict__.get("_overlay_closed", False)):
            return
        now = float(time.monotonic())
        has_new_result = False
        while True:
            try:
                result = self._snap_worker_queue.get_nowait()
            except queue.Empty:
                break
            except Exception:
                break
            try:
                seq = int(result.get("seq", 0))
                hwnd = int(result.get("hwnd", 0))
                if seq != int(self._snap_worker_seq):
                    continue
                done = bool(result.get("done", True))
                if bool(done):
                    self._snap_worker_thread = None
                    self._snap_worker_hwnd = 0
                    self._snap_worker_point_px = None
                    self._snap_worker_started_ts = 0.0
                if str(result.get("error", "")) == "import":
                    self._uia_unavailable = True
                    continue
                rects = list(result.get("rects") or [])
                target = result.get("target")
                if target is not None and target not in rects:
                    rects.append(target)
                rects = [tuple(map(int, r)) for r in rects]
                rects.sort(key=lambda r: int(max(1, r[2] - r[0]) * max(1, r[3] - r[1])))
                self._snap_cache_hwnd = int(hwnd)
                self._snap_cache_ts = now
                self._snap_cache_rects_px = rects
                self._snap_spatial_index = _SnapSpatialIndex(rects)
                scan_x = result.get("x")
                scan_y = result.get("y")
                if scan_x is not None and scan_y is not None:
                    self._snap_last_scan_point = (int(scan_x), int(scan_y))
                has_new_result = True
                # 彻底移除原先扫描耗时 > 1.2 秒即拉黑降级为大窗口吸附的惩罚判定，因为现在已有 200px 去重机制，无需超时拉黑
                # if bool(done) and float(result.get("elapsed", 0.0) or 0.0) > 1.2:
                #     self._snap_precise_blocked_until[int(hwnd)] = now + 2.0
            except Exception:
                continue
        if has_new_result:
            try:
                # 扫描结果一旦返回，立即主动触发一次刷新，消除定时器延迟，实现即时吸附
                p = self.mapFromGlobal(QCursor.pos())
                self._update_auto_snap_rect(p, allow_precise=False)
            except Exception:
                pass

    def _snap_worker_active(self) -> bool:
        thread = self.__dict__.get("_snap_worker_thread")
        return thread is not None and _is_worker_alive(thread)

    def _schedule_snap_worker_poll(self, delay_ms: Optional[int] = None) -> None:
        state = self.__dict__
        if (
            bool(state.get("_overlay_closed", False))
            or not bool(state.get("_auto_snap", False))
            or bool(state.get("_confirmed", False))
        ):
            return
        timer = state.get("_snap_timer")
        if timer is None:
            return
        try:
            delay = int(delay_ms if delay_ms is not None else state.get("_snap_worker_poll_ms", 45))
            if timer.isActive():
                return
            timer.start(max(15, delay))
        except Exception:
            pass

    def _request_uia_scan_async(self, top_hwnd: int, top_rect: tuple[int, int, int, int], x: int, y: int) -> None:
        if bool(self.__dict__.get("_overlay_closed", False)) or bool(self._uia_unavailable) or int(top_hwnd) == 0:
            return
        now = float(time.monotonic())
        if not self._snap_precise_scan_ready(now):
            return
        blocked_until = float(self._snap_precise_blocked_until.get(int(top_hwnd), 0.0) or 0.0)
        if blocked_until > now:
            return
        if now - float(self._snap_precise_last_request_ts) < float(self._snap_precise_min_request_s):
            return
        thread = self._snap_worker_thread
        if thread is not None:
            if _is_worker_alive(thread):
                elapsed = now - float(self._snap_worker_started_ts)
                point = self._snap_worker_point_px
                if int(self._snap_worker_hwnd or 0) == int(top_hwnd) and point is not None:
                    if abs(int(point[0]) - int(x)) <= 4 and abs(int(point[1]) - int(y)) <= 4:
                        return
                # 若鼠标已移动到另一个不同的窗口，无视防抖限制，立即触发新扫描以确保即时响应
                if int(self._snap_worker_hwnd or 0) == int(top_hwnd) and elapsed <= float(getattr(self, "_snap_worker_retarget_s", 0.08)):
                    return
                if elapsed > float(self._snap_worker_timeout):
                    if int(self._snap_worker_hwnd or 0):
                        self._snap_precise_blocked_until[int(self._snap_worker_hwnd)] = now + 3.0
                    return
                self._schedule_snap_worker_poll()
                return
            else:
                self._snap_worker_thread = None
                self._snap_worker_hwnd = 0
                self._snap_worker_point_px = None
                self._snap_worker_started_ts = 0.0
        if now - float(self._snap_precise_last_request_ts) < float(self._snap_precise_min_request_s):
            return
        self._snap_worker_seq += 1
        seq = int(self._snap_worker_seq)
        self._snap_worker_hwnd = int(top_hwnd)
        self._snap_worker_point_px = (int(x), int(y))
        self._snap_worker_started_ts = now
        self._snap_precise_last_request_ts = now
        try:
            worker = _uia_executor.submit(
                _scan_uia_rects_for_window,
                seq,
                int(top_hwnd),
                tuple(map(int, top_rect)),
                int(x),
                int(y),
                self._snap_worker_queue,
            )
            self._snap_worker_thread = worker
        except Exception:
            self._snap_worker_thread = None
            self._snap_worker_hwnd = 0
            self._snap_worker_point_px = None
            self._snap_worker_started_ts = 0.0
            return
        try:
            self._schedule_snap_worker_poll()
        except Exception:
            pass

    def _schedule_link_probe_burst(self) -> None:
        if not bool(getattr(self, "_link_probe_enabled", True)) or not bool(getattr(self, "_link_probe_only", False)):
            return
        for delay_ms in (15, 40, 80, 150, 260):
            try:
                QTimer.singleShot(int(delay_ms), lambda: self._poll_hover_link(force=True))
            except Exception:
                pass

    def _schedule_empty_link_probe_retry(self) -> None:
        if not bool(getattr(self, "_link_probe_only", False)):
            return
        retries = int(getattr(self, "_link_probe_empty_retries", 0) or 0) + 1
        self._link_probe_empty_retries = retries
        if retries > int(getattr(self, "_link_probe_empty_retry_limit", 0) or 0):
            return
        delay_ms = 35 if retries <= 6 else 90
        try:
            QTimer.singleShot(int(delay_ms), lambda: self._poll_hover_link(force=True))
        except Exception:
            pass

    def _drain_link_probe_results(self) -> None:
        while True:
            try:
                result = self._link_probe_queue.get_nowait()
            except queue.Empty:
                break
            except Exception:
                break
            try:
                seq = int(result.get("seq", 0))
                if seq != int(self._link_probe_seq):
                    continue
                self._link_probe_thread = None
                self._link_probe_hwnd = 0
                self._link_probe_started_ts = 0.0
                # 探测结束，恢复为 55ms 的节能轮询间隔
                timer = getattr(self, "_link_probe_timer", None)
                if timer is not None:
                    timer.setInterval(55 if bool(self._link_probe_only) else 120)
                if str(result.get("error", "")) == "import":
                    if not bool(getattr(self, "_link_probe_unavailable", False)):
                        # 首次进入不可用状态时记录诊断信息（含 comtypes/pywinauto 初始化错误），
                        # 此前此处完全静默，导致打包后稍后阅读功能失效但用户无任何提示。
                        try:
                            from deepcat.utils.logger import get_logger
                            get_logger("region_overlay").warning(
                                "UIA 链接探测不可用：%s",
                                _uia_init_error or "pywinauto/comtypes 未正确装配",
                            )
                        except Exception:
                            pass
                    self._link_probe_unavailable = True
                    continue
                err_other = str(result.get("error", "") or "")
                if err_other:
                    # 非 import 错误（如 COMError）也记录一次，便于定位打包后链路故障
                    if not bool(getattr(self, "_link_probe_unavailable", False)):
                        try:
                            from deepcat.utils.logger import get_logger
                            get_logger("region_overlay").warning(
                                "UIA 链接探测异常：%s", err_other,
                            )
                        except Exception:
                            pass
                    self._link_probe_unavailable = True
                    continue
                payload = result.get("payload")
                now = float(time.monotonic())
                if not isinstance(payload, dict) or not str(payload.get("url", "")).strip():
                    if now - float(self._link_last_seen_ts) > 0.6:
                        self._link_last_key = ""
                    self._schedule_empty_link_probe_retry()
                    continue
                url = str(payload.get("url", "")).strip()
                title = str(payload.get("title", "")).strip() or url
                key = f"{title}\n{url}"
                self._link_last_seen_ts = now
                self._link_probe_empty_retries = 0
                if key == self._link_last_key:
                    continue
                self._link_last_key = key
                self.linkHovered.emit({"title": title, "url": url})
            except Exception:
                continue

    def _request_link_probe_async(self, top_hwnd: int, x: int, y: int, *, force: bool = False) -> None:
        if bool(self._link_probe_unavailable) or int(top_hwnd) == 0:
            return
        now = float(time.monotonic())
        if not bool(force) and now - float(self._link_probe_last_request_ts) < float(getattr(self, "_link_probe_min_request_s", 0.18)):
            return
        thread = self._link_probe_thread
        if thread is not None:
            if _is_worker_alive(thread):
                if now - float(self._link_probe_started_ts) > 0.4:
                    self._link_probe_thread = None
                    self._link_probe_hwnd = 0
                    self._link_probe_started_ts = 0.0
                else:
                    return
            else:
                self._link_probe_thread = None
                self._link_probe_hwnd = 0
                self._link_probe_started_ts = 0.0
        self._link_probe_seq += 1
        seq = int(self._link_probe_seq)
        self._link_probe_last_request_ts = now
        self._link_probe_started_ts = now
        self._link_probe_hwnd = int(top_hwnd)
        try:
            worker = _uia_executor.submit(
                _probe_uia_link_at_point,
                seq,
                int(top_hwnd),
                int(x),
                int(y),
                self._link_probe_queue,
            )
            self._link_probe_thread = worker
            # 开启探测后，将定时器间隔设为 10ms 进行高频极速轮询
            timer = getattr(self, "_link_probe_timer", None)
            if timer is not None:
                timer.setInterval(10)
        except Exception:
            self._link_probe_thread = None
            self._link_probe_hwnd = 0
            self._link_probe_started_ts = 0.0

    def _poll_hover_link(self, *, force: bool = False) -> None:
        if not bool(getattr(self, "_link_probe_enabled", True)):
            return
        self._drain_link_probe_results()
        if bool(self._confirmed) or self._drag_mode is not None:
            return
        if bool(self._mouse_passthrough) and not bool(self._link_probe_only):
            return
        try:
            local = self.mapFromGlobal(QCursor.pos())
            if not self.rect().contains(local):
                return
            pp = self._local_point_to_physical(local)
            x = int(pp.x())
            y = int(pp.y())
            point = (int(x), int(y))
            last = self._link_probe_last_point_px
            same_point = False
            if last is not None:
                epsilon = int(getattr(self, "_link_probe_point_epsilon", 4))
                same_point = abs(int(last[0]) - int(x)) <= epsilon and abs(int(last[1]) - int(y)) <= epsilon
            if (
                not bool(force)
                and bool(same_point)
                and float(time.monotonic()) - float(self._link_probe_last_request_ts) < float(getattr(self, "_link_probe_same_point_retry_s", 0.75))
            ):
                return
            self._link_probe_last_point_px = point
            top_hwnd = self._top_window_at_px_for_input(int(x), int(y))
            if int(top_hwnd) == 0:
                return
            self._request_link_probe_async(int(top_hwnd), int(x), int(y), force=bool(force))
        except Exception:
            return

    def _uia_rect_at_px(self, top_hwnd: int, x: int, y: int, *, allow_scan: bool) -> Optional[tuple[int, int, int, int]]:
        if bool(self._uia_unavailable) or top_hwnd == 0:
            return None
        try:
            top_rect = self._window_rect_px(top_hwnd, top_level=True)
            if top_rect is None:
                return None
            cached = self._uia_cached_rect_at_px(int(top_hwnd), top_rect, int(x), int(y), allow_scan=False)
            if cached is not None:
                return cached
            if not bool(allow_scan):
                return None
            self._request_uia_scan_async(int(top_hwnd), top_rect, int(x), int(y))
            return None
        except Exception:
            return None

    def _snap_precise_available_for_hwnd(self, top_hwnd: int) -> bool:
        if bool(self._uia_unavailable) or int(top_hwnd) == 0:
            return False
        if not self._snap_precise_scan_ready():
            return False
        blocked_until = float(self._snap_precise_blocked_until.get(int(top_hwnd), 0.0) or 0.0)
        return blocked_until <= float(time.monotonic())

    def _snap_precise_scan_ready(self, now: Optional[float] = None) -> bool:
        try:
            current = float(time.monotonic() if now is None else now)
            return current >= float(getattr(self, "_snap_precise_ready_ts", 0.0) or 0.0)
        except Exception:
            return True

    def _snap_rect_area_px(self, rect: tuple[int, int, int, int]) -> int:
        return int(max(1, int(rect[2]) - int(rect[0])) * max(1, int(rect[3]) - int(rect[1])))

    def _snap_rect_is_large_fallback_px(
        self,
        rect: tuple[int, int, int, int],
        top_rect: tuple[int, int, int, int],
    ) -> bool:
        top_area = max(1, self._snap_rect_area_px(top_rect))
        area = self._snap_rect_area_px(rect)
        ratio = float(getattr(self, "_snap_large_fallback_area_ratio", 0.64))
        return area >= int(top_area * ratio)

    def _detect_snap_rect(self, p: QPoint, *, allow_precise: bool = True) -> Optional[tuple[QRect, bool]]:
        if not bool(self._auto_snap):
            return None
        try:
            pp = self._local_point_to_physical(p)
            x = int(pp.x())
            y = int(pp.y())
            top_hwnd = self._top_window_at_px(x, y)
            if top_hwnd == 0:
                return None
            top_rect = self._top_window_rect_for_snap(top_hwnd, x, y)
            if top_rect is None:
                return None
            # 计算运动预测坐标 (pred_x, pred_y)
            history = self.__dict__.get("_snap_mouse_history")
            pred_x, pred_y = int(x), int(y)
            if history is not None and len(history) >= 2:
                p_last = history[-2]
                p_curr = history[-1]
                dt = float(p_curr[2] - p_last[2])
                if dt > 0.005:
                    vx = float(p_curr[0] - p_last[0]) / dt
                    vy = float(p_curr[1] - p_last[1]) / dt
                    velocity = float(self.__dict__.get("_snap_current_velocity", 0.0) or 0.0)
                    # 仅在适当滑行手速下预测外推 60ms，防止抖动
                    if 100.0 < velocity < 800.0:
                        pred_x = int(round(float(x) + vx * 0.060))
                        pred_y = int(round(float(y) + vy * 0.060))

            # 1. 尝试用真实坐标 UIA 匹配
            uia_rect = self._uia_rect_at_px(top_hwnd, x, y, allow_scan=bool(allow_precise))

            # 2. 预测 UIA 匹配：若真实未命中，且预测点有效，用预测点匹配 (不重复触发扫描)
            if uia_rect is None and (pred_x != x or pred_y != y):
                pred_uia = self._uia_rect_at_px(top_hwnd, pred_x, pred_y, allow_scan=False)
                if pred_uia is not None:
                    # 强校验真实坐标到该控件边界最近曼哈顿距离 <= 15 像素，防空吸
                    l, t, r, b = pred_uia
                    dist_x = max(0, int(l) - int(x), int(x) - int(r))
                    dist_y = max(0, int(t) - int(y), int(y) - int(b))
                    if dist_x <= 15 and dist_y <= 15:
                        uia_rect = pred_uia

            if uia_rect is not None:
                rect = self._logical_rect_to_local(*uia_rect)
                if rect is not None:
                    return (rect, True)

            # 3. 尝试真实坐标 CV 匹配
            cv_rect = self._cv_edge_rect_at_px(int(x), int(y))

            # 4. 预测 CV 匹配：若真实未命中，且预测点有效，用预测点匹配
            if cv_rect is None and (pred_x != x or pred_y != y):
                pred_cv = self._cv_edge_rect_at_px(int(pred_x), int(pred_y))
                if pred_cv is not None:
                    l, t, r, b = pred_cv
                    dist_x = max(0, int(l) - int(x), int(x) - int(r))
                    dist_y = max(0, int(t) - int(y), int(y) - int(b))
                    if dist_x <= 15 and dist_y <= 15:
                        cv_rect = pred_cv

            if cv_rect is not None:
                rect = self._logical_rect_to_local(*cv_rect)
                if rect is not None:
                    return (rect, True)
            child_hwnd = self._child_window_at_px(top_hwnd, x, y)
            hwnd = int(child_hwnd or top_hwnd)
            rect_px = self._window_rect_px(hwnd, top_level=(hwnd == top_hwnd))
            if rect_px is None:
                return None
            large_fallback = self._snap_rect_is_large_fallback_px(rect_px, top_rect)
            precise_available = bool(allow_precise) and self._snap_precise_available_for_hwnd(int(top_hwnd))
            snap_cache_hwnd = self.__dict__.get("_snap_cache_hwnd", 0)
            snap_cache_rects = self.__dict__.get("_snap_cache_rects_px", [])
            snap_last_scan_point = self.__dict__.get("_snap_last_scan_point")
            dist_exceeded = True
            if snap_last_scan_point is not None:
                dx = int(snap_last_scan_point[0]) - int(x)
                dy = int(snap_last_scan_point[1]) - int(y)
                if dx * dx + dy * dy <= 40000:  # 200 * 200 像素范围内
                    dist_exceeded = False
            cache_valid = (
                int(snap_cache_hwnd) == int(top_hwnd)
                and bool(snap_cache_rects)
                and not dist_exceeded
            )
            if cache_valid:
                precise_available = False
            if bool(large_fallback) and bool(precise_available):
                self._request_uia_scan_async(int(top_hwnd), top_rect, int(x), int(y))
                return None
            rect = self._logical_rect_to_local(*rect_px)
            if rect is None:
                return None
            return (rect, bool(child_hwnd and hwnd != top_hwnd and not bool(large_fallback)))
        except Exception:
            return None

    def _snap_allow_precise_for_point(self, p: QPoint) -> bool:
        try:
            pp = self._local_point_to_physical(p)
            x = int(pp.x())
            y = int(pp.y())
            now = float(time.monotonic())

            # 维护鼠标历史轨迹 (120ms 时间窗)
            history = getattr(self, "_snap_mouse_history", None)
            if history is not None:
                history.append((x, y, now))
                # 过滤出 120ms 内的坐标
                cutoff = now - 0.12
                while history and history[0][2] < cutoff:
                    history.pop(0)

                # 计算瞬时速度 (像素/秒)
                if len(history) >= 2:
                    total_dist = 0.0
                    for i in range(len(history) - 1):
                        dx = history[i+1][0] - history[i][0]
                        dy = history[i+1][1] - history[i][1]
                        total_dist += (dx * dx + dy * dy) ** 0.5
                    dt = history[-1][2] - history[0][2]
                    if dt > 0.01:
                        self._snap_current_velocity = float(total_dist / dt)
                    else:
                        self._snap_current_velocity = 0.0
                else:
                    self._snap_current_velocity = 0.0

            last = self._snap_last_point_px
            if last is None or abs(int(last[0]) - x) > 2 or abs(int(last[1]) - y) > 2:
                self._snap_last_point_px = (x, y)
                self._snap_last_move_ts = now
            if not self._snap_precise_scan_ready(now):
                return False
            return now - float(self._snap_show_ts) >= float(self._snap_fast_start_ms)
        except Exception:
            return True

    def _snap_should_process_pointer_update(self, p: QPoint) -> bool:
        try:
            now = float(time.monotonic())
            pp = self._local_point_to_physical(p)
            point = (int(pp.x()), int(pp.y()))
            last = getattr(self, "_snap_last_update_point_px", None)
            last_ts = float(getattr(self, "_snap_last_update_ts", 0.0) or 0.0)
            if last is None:
                self._snap_last_update_point_px = point
                self._snap_last_update_ts = now
                return True
            dt = now - last_ts
            dx = int(point[0]) - int(last[0])
            dy = int(point[1]) - int(last[1])
            hard_min = float(getattr(self, "_snap_hard_min_update_interval_s", 0.016) or 0.016)
            min_interval = float(getattr(self, "_snap_min_update_interval_s", 0.035) or 0.035)
            min_distance = int(getattr(self, "_snap_min_update_distance_px", 12) or 12)
            if dt < hard_min:
                return False
            if dt < min_interval and dx * dx + dy * dy < min_distance * min_distance:
                return False
            self._snap_last_update_point_px = point
            self._snap_last_update_ts = now
            return True
        except Exception:
            return True

    def _snap_mark_probe_point(self, p: QPoint) -> None:
        try:
            pp = self._local_point_to_physical(p)
            self._snap_last_probe_point_px = (int(pp.x()), int(pp.y()))
            self._snap_last_probe_ts = float(time.monotonic())
        except Exception:
            self._snap_last_probe_ts = float(time.monotonic())

    def _snap_can_reuse_current_rect(self, p: QPoint, current: Optional[QRect], *, allow_precise: Optional[bool]) -> bool:
        if current is None or allow_precise is not None:
            return False
        if not bool(getattr(self, "_snap_locked_refined", False)):
            return False
        if not self._hysteresis_contains_local(current, p):
            return False
        try:
            now = float(time.monotonic())
            interval = float(getattr(self, "_snap_inside_rect_probe_interval_s", 0.085) or 0.085)
            if now - float(getattr(self, "_snap_last_probe_ts", 0.0) or 0.0) < interval:
                return True
        except Exception:
            return False
        return False

    def _rect_area_local(self, rect: QRect) -> int:
        return int(max(1, rect.width()) * max(1, rect.height()))

    def _rect_contains_rect_local(self, outer: QRect, inner: QRect) -> bool:
        expanded = QRect(outer).adjusted(-2, -2, 2, 2)
        return expanded.contains(inner.topLeft()) and expanded.contains(inner.bottomRight())

    def _hysteresis_contains_local(self, current: QRect, p: QPoint) -> bool:
        """滞后判断：边距 >= 磁性边距，确保“能吸附就能保持”。

        原先用固定 6px，但磁性检测对小矩形用 8px，导致 2px 闪烁缝。
        现改为分档边距：小 12px / 中 8px / 大 4px（均在磁性边距基础上 +4px 死区）。
        """
        w = int(current.width())
        h = int(current.height())
        if w < 120 and h < 120:
            margin = 12
        elif w < 300 and h < 300:
            margin = 8
        else:
            margin = 4
        return QRect(current).adjusted(-margin, -margin, margin, margin).contains(p)

    def _update_auto_snap_rect(self, p: QPoint, *, allow_precise: Optional[bool] = None) -> None:
        if (
            bool(self.__dict__.get("_overlay_closed", False))
            or not bool(self._auto_snap)
            or self._drag_mode is not None
            or bool(self._confirmed)
            or bool(self._pending_confirm)
        ):
            return
        self._drain_snap_worker_results()
        if not self.rect().contains(p):
            return
        if allow_precise is None and not self._snap_should_process_pointer_update(p):
            if self._snap_worker_active():
                self._schedule_snap_worker_poll()
            return
        current = self._snap_logical_selection_rect()
        current_contains_pointer = current is not None and self._hysteresis_contains_local(current, p)
        if self._snap_can_reuse_current_rect(p, current, allow_precise=allow_precise):
            if self._snap_worker_active():
                self._schedule_snap_worker_poll()
            self.setCursor(self._cursor_cross)
            return
        if allow_precise is None:
            allow_precise = self._snap_allow_precise_for_point(p)
        self._snap_mark_probe_point(p)
        detected = self._detect_snap_rect(p, allow_precise=bool(allow_precise))
        if detected is None:
            # 仅当当前已吸附选区是精确的子元素（非大窗口兜底），且鼠标在范围内、扫描线程在工作时，为了防止闪烁保留选区；否则立即清除以便瞬间脱离
            thread = self.__dict__.get("_snap_worker_thread")
            current_is_refined = bool(self.__dict__.get("_snap_locked_refined", False))
            if bool(current_contains_pointer) and bool(current_is_refined) and thread is not None and _is_worker_alive(thread):
                return
            if bool(current_contains_pointer) and bool(current_is_refined):
                self.setCursor(self._cursor_cross)
                return
            self._snap_locked_rect = None
            self._snap_locked_refined = False
            self._stop_snap_animation(commit=False)
            if self._rect is not None:
                self._set_selection_rect_for_overlay(None)
            self.setCursor(self._cursor_cross)
            return
        rect, refined = detected
        if current is not None and bool(current_contains_pointer):
            current_area = self._rect_area_local(current)
            next_area = self._rect_area_local(rect)
            # 滞后防抖：仅当新目标是当前目标的更小子元素时才切换（精准钻取），
            # 否则保持当前目标（兄弟元素/父元素均保持，需移出边距区才释放）
            is_smaller_child = (
                next_area < current_area
                and self._rect_contains_rect_local(current, rect)
            )
            if not is_smaller_child:
                self._snap_locked_rect = QRect(current)
                self._snap_locked_refined = bool(getattr(self, "_snap_locked_refined", False) or refined)
                self.setCursor(self._cursor_cross)
                return
        if bool(refined):
            self._snap_locked_rect = QRect(rect)
            self._snap_locked_refined = True
        else:
            self._snap_locked_rect = None
            self._snap_locked_refined = False
        if current is not None:
            self._animate_snap_rect(rect)
        elif self._selection_rect() != rect:
            self._stop_snap_animation(commit=False)
            self._set_selection_rect_for_overlay(rect)
        self.setCursor(self._cursor_cross)

    def _refresh_auto_snap_rect(self, *, allow_precise: Optional[bool] = None) -> None:
        if (
            bool(self.__dict__.get("_overlay_closed", False))
            or not bool(self._auto_snap)
            or bool(self._confirmed)
            or bool(self._pending_confirm)
        ):
            return
        try:
            p = self.mapFromGlobal(QCursor.pos())
            self._update_auto_snap_rect(p, allow_precise=allow_precise)
        except Exception:
            pass
        if self._snap_worker_active():
            self._schedule_snap_worker_poll()

    def _update_magnifier_from_event(self, event, *, force: bool = False) -> None:
        if self._magnifier is None:
            return
        try:
            gp = event.globalPosition().toPoint()
        except Exception:
            gp = QCursor.pos()
        if not bool(force):
            try:
                now = float(time.monotonic())
                min_interval = float(getattr(self, "_magnifier_update_min_interval_s", 0.033) or 0.033)
                if now - float(getattr(self, "_magnifier_last_update_ts", 0.0) or 0.0) < min_interval:
                    return
                self._magnifier_last_update_ts = now
            except Exception:
                pass
        else:
            try:
                self._magnifier_last_update_ts = float(time.monotonic())
            except Exception:
                pass
        try:
            self._magnifier.update_at(gp)
            self._magnifier.show()
            self._magnifier.raise_()
        except Exception:
            pass

    def mousePressEvent(self, event):
        if bool(self._confirmed):
            return
        try:
            gp = event.globalPosition().toPoint()
        except Exception:
            gp = QCursor.pos()
        if self._sync_external_cursor(gp):
            event.ignore()
            return
        if bool(self._link_probe_only):
            if event.button() == Qt.MouseButton.RightButton:
                self.canceled.emit()
                self.close()
                event.accept()
                return
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._confirm_timer.stop()
            self._pending_confirm = False
            self.canceled.emit()
            try:
                self._magnifier.close()
            except Exception:
                pass
            self.close()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._confirm_timer.stop()
            self._pending_confirm = False
            self._stop_snap_animation(commit=True)
            p = event.position().toPoint()
            self._update_magnifier_from_event(event, force=True)
            if bool(self._auto_snap) and self._selection_rect() is not None:
                self._drag_mode = "pending_auto"
            else:
                self._drag_mode = "new"
            self._drag_anchor = p
            self._rect_at_press = self._selection_rect()
            if self._drag_mode == "new":
                self._set_selection_rect_for_overlay(QRect(p, p))

    def mouseMoveEvent(self, event):
        if bool(self._confirmed):
            return
        try:
            gp = event.globalPosition().toPoint()
        except Exception:
            gp = QCursor.pos()
        if self._sync_external_cursor(gp):
            return
        if bool(self._link_probe_only):
            self._force_link_probe_cursor()
            self._poll_hover_link()
            return
        p = event.position().toPoint()
        self._update_magnifier_from_event(event)
        if self._drag_mode is None:
            if bool(self._auto_snap):
                self._update_auto_snap_rect(p)
                return
            self._update_cursor(p)
            return
        if self._drag_anchor is None:
            return
        rect0 = self._rect_at_press or self._selection_rect() or QRect(self._drag_anchor, self._drag_anchor)
        dx = p.x() - self._drag_anchor.x()
        dy = p.y() - self._drag_anchor.y()
        if self._drag_mode == "pending_auto":
            if abs(dx) < int(self._manual_drag_threshold) and abs(dy) < int(self._manual_drag_threshold):
                return
            self._drag_mode = "new"
            self._snap_locked_rect = None
            self._snap_locked_refined = False
            self._set_selection_rect_for_overlay(QRect(self._drag_anchor, p))
            return
        if self._drag_mode == "new":
            next_rect = QRect(self._drag_anchor, p)
        elif self._drag_mode == "move":
            next_rect = QRect(rect0)
            next_rect.translate(dx, dy)
        elif self._drag_mode == "resize_tl":
            next_rect = QRect(rect0)
            next_rect.setTopLeft(rect0.topLeft() + QPoint(dx, dy))
        elif self._drag_mode == "resize_tr":
            next_rect = QRect(rect0)
            next_rect.setTopRight(rect0.topRight() + QPoint(dx, dy))
        elif self._drag_mode == "resize_bl":
            next_rect = QRect(rect0)
            next_rect.setBottomLeft(rect0.bottomLeft() + QPoint(dx, dy))
        elif self._drag_mode == "resize_br":
            next_rect = QRect(rect0)
            next_rect.setBottomRight(rect0.bottomRight() + QPoint(dx, dy))
        else:
            return
        self._set_selection_rect_for_overlay(next_rect)

    def wheelEvent(self, event) -> None:
        if bool(self._confirmed):
            return
        if bool(self._link_probe_only):
            if self._forward_wheel_to_underlying_window(event):
                event.accept()
                return
            event.ignore()
            return
        if self._forward_wheel_to_underlying_window(event):
            self._invalidate_snap_after_scroll()
            if not bool(self._freeze_on_start) and bool(self._auto_snap):
                single_shot_scoped(260, self, lambda: self._refresh_auto_snap_rect(allow_precise=False))
            event.accept()
            return
        try:
            d = int(event.angleDelta().y())
            if d == 0:
                return
            step = 0.25 if abs(d) < 240 else 0.5
            curr = float(getattr(self._magnifier, "_zoom", 4.0))
            next_zoom = curr + (step if d > 0 else -step)
            self._magnifier.set_zoom(next_zoom)
        except Exception:
            pass

    def mouseReleaseEvent(self, event):
        if bool(self._confirmed):
            return
        if bool(self._link_probe_only):
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            pending_auto = self._drag_mode == "pending_auto"
            self._drag_mode = None
            self._drag_anchor = None
            self._rect_at_press = None
            if self._selection_rect() is not None:
                if getattr(self, "_is_scroll_capture", False):
                    h = self._selection_rect().height()
                    if h < 300:
                        from PyQt6.QtWidgets import QMessageBox
                        was_override = bool(getattr(self, "_cursor_override_active", False))
                        if was_override:
                            self._release_cursor_override()
                        QMessageBox.warning(
                            self,
                            "提示",
                            f"滚动截图选区高度不能小于 300px（当前为 {h}px），请重新选取。",
                            QMessageBox.StandardButton.Ok
                        )
                        self._restore_cursor_after_modal(was_override)
                        self._set_selection_rect_for_overlay(None)
                        event.accept()
                        return
                # 先设置等待确认标志，阻止 _snap_timer 在等待期间修改选取框
                self._pending_confirm = True
                self.repaint()
                self._confirm_timer.start(0 if bool(pending_auto) else self._confirm_delay_ms)
            else:
                self._request_selection_repaint(None, None)

    def _confirm_current_selection(self) -> None:
        self._pending_confirm = False
        rect = self._selection_rect()
        if rect is None:
            return
        if getattr(self, "_is_scroll_capture", False) and rect.height() < 300:
            from PyQt6.QtWidgets import QMessageBox
            was_override = bool(getattr(self, "_cursor_override_active", False))
            if was_override:
                self._release_cursor_override()
            QMessageBox.warning(
                self,
                "提示",
                f"滚动截图选区高度不能小于 300px（当前为 {rect.height()}px），请重新选取。",
                QMessageBox.StandardButton.Ok
            )
            self._restore_cursor_after_modal(was_override)
            self._set_selection_rect_for_overlay(None)
            return
        origin = self.geometry().topLeft()
        logical_left = int(origin.x() + rect.x())
        logical_top = int(origin.y() + rect.y())
        logical_width = int(rect.width())
        logical_height = int(rect.height())
        left, top, width, height = logical_rect_to_physical_tuple(
            QRect(logical_left, logical_top, logical_width, logical_height),
            self._screen_mappings,
        )
        region = SelectedRegion(
            left=left,
            top=top,
            width=width,
            height=height,
            logical_left=logical_left,
            logical_top=logical_top,
            logical_width=logical_width,
            logical_height=logical_height,
            frozen_screen_bgr=self._freeze_screen_bgr,
            frozen_screen_left=int(self._freeze_screen_left),
            frozen_screen_top=int(self._freeze_screen_top),
        )
        try:
            self._magnifier.close()
        except Exception:
            pass
        try:
            self._snap_timer.stop()
        except Exception:
            pass
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        self._confirmed = True
        self._suspend_cursor_updates()
        # 确认选区后，覆盖层必须在 Win32 层级启用鼠标穿透（WS_EX_TRANSPARENT），
        # 而非仅在 Qt 层级设置 WA_TransparentForMouseEvents。
        # 原因：pyautogui.scroll() 使用 SendInput 发送系统级 MOUSEEVENTF_WHEEL 事件，
        # Windows 通过 hit-test 决定投递给哪个 HWND。若覆盖层未设置 WS_EX_TRANSPARENT，
        # Windows 会将滚轮事件投递给这个全屏置顶的覆盖层 HWND，底层浏览器永远收不到。
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if not bool(self._close_on_confirm):
            try:
                self._set_mouse_input_passthrough(True)
            except Exception:
                pass
        if os.name == "nt" and getattr(self, "_prev_active_hwnd", 0):
            try:
                import ctypes
                prev_hwnd = int(self._prev_active_hwnd)
                # 异步将焦点交还给原网页窗口，确保键盘和输入焦点到位（例如在使用按键模式滚动时）
                def restore_fg():
                    try:
                        ctypes.windll.user32.SetForegroundWindow(prev_hwnd)
                    except Exception:
                        pass
                QTimer.singleShot(50, restore_fg)
            except Exception:
                pass
        try:
            self.repaint()
        except Exception:
            pass
        self.confirmed.emit(region)
        if bool(self._close_on_confirm):
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._confirm_timer.stop()
            self.canceled.emit()
            try:
                self._magnifier.close()
            except Exception:
                pass
            self.close()
            event.accept()
            return
        if bool(self._link_probe_only):
            event.ignore()
            return
        if bool(self._confirmed):
            event.ignore()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._confirm_timer.stop()
            self._confirm_current_selection()
            return

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            segments = list(getattr(self, "_freeze_segments", []) or [])
            if (
                bool(self._show_frozen_background)
                and bool(segments)
            ):
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                origin = self.geometry().topLeft()
                for logical_rect, pixmap in segments:
                    if pixmap is None or pixmap.isNull():
                        continue
                    target = QRect(logical_rect)
                    target.translate(-origin)
                    painter.drawPixmap(target.topLeft(), pixmap)
            elif (
                bool(self._show_frozen_background)
                and self._freeze_pixmap is not None
                and not self._freeze_pixmap.isNull()
            ):
                target = QRect(self._freeze_logical_rect)
                target.translate(-self.geometry().topLeft())
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                painter.drawPixmap(target.topLeft(), self._freeze_pixmap)
            elif (
                bool(self._show_frozen_background)
                and self._freeze_qimage is not None
                and not self._freeze_qimage.isNull()
            ):
                target = QRect(self._freeze_logical_rect)
                target.translate(-self.geometry().topLeft())
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                painter.drawImage(target.topLeft(), self._freeze_qimage)
            else:
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

            if bool(self._link_probe_only):
                return

            rect = self._selection_rect()
            if rect is None:
                return

            dpr = float(self._dpr_for_local_rect(rect))
            logical_left = float(rect.x())
            logical_top = float(rect.y())
            logical_width = float(rect.width())
            logical_height = float(rect.height())

            left_px = int(math.floor(logical_left * dpr))
            top_px = int(math.floor(logical_top * dpr))
            right_px = int(math.ceil((logical_left + logical_width) * dpr))
            bottom_px = int(math.ceil((logical_top + logical_height) * dpr))
            width_px = int(max(1, right_px - left_px))
            height_px = int(max(1, bottom_px - top_px))

            dx = (float(left_px) - logical_left * dpr) / dpr
            dy = (float(top_px) - logical_top * dpr) / dpr
            dw = (float(width_px) - logical_width * dpr) / dpr
            dh = (float(height_px) - logical_height * dpr) / dpr

            rect_aligned = QRectF(logical_left + dx, logical_top + dy, logical_width + dw, logical_height + dh)

            shade_color = QColor(0, 0, 0, 105)
            shade_rect_f = QRectF(self.rect())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            if not rect_aligned.isEmpty():
                if rect_aligned.top() > shade_rect_f.top():
                    painter.fillRect(
                        QRectF(
                            shade_rect_f.left(),
                            shade_rect_f.top(),
                            shade_rect_f.width(),
                            rect_aligned.top() - shade_rect_f.top(),
                        ),
                        shade_color,
                    )
                if rect_aligned.bottom() < shade_rect_f.bottom():
                    painter.fillRect(
                        QRectF(
                            shade_rect_f.left(),
                            rect_aligned.bottom(),
                            shade_rect_f.width(),
                            shade_rect_f.bottom() - rect_aligned.bottom(),
                        ),
                        shade_color,
                    )
                if rect_aligned.left() > shade_rect_f.left():
                    painter.fillRect(
                        QRectF(
                            shade_rect_f.left(),
                            rect_aligned.top(),
                            rect_aligned.left() - shade_rect_f.left(),
                            rect_aligned.height(),
                        ),
                        shade_color,
                    )
                if rect_aligned.right() < shade_rect_f.right():
                    painter.fillRect(
                        QRectF(
                            rect_aligned.right(),
                            rect_aligned.top(),
                            shade_rect_f.right() - rect_aligned.right(),
                            rect_aligned.height(),
                        ),
                        shade_color,
                    )

            if not bool(self._selection_visual_visible):
                return

            border_color = QColor(self._style_hex("selection_border_color", "#FF0000"))
            pen = QPen(border_color)
            pen.setWidth(3)
            pen.setCosmetic(True)
            self._apply_pen_line_style(pen)
            painter.setPen(pen)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            border_rect = QRectF(
                float(rect_aligned.x()),
                float(rect_aligned.y()),
                float(max(1.0, rect_aligned.width() - 1.0)),
                float(max(1.0, rect_aligned.height() - 1.0)),
            )
            painter.drawRect(border_rect)

            w = rect.width()
            h = rect.height()
            text = f"宽高：{w} x {h}"
            self._draw_dimension_tip(painter, rect_aligned, text)

            # 开启抗锯齿，使选取框时的 8 个圆点渲染出与松开后完全一致的高质感圆滑样式，彻底消除样式不一致导致的闪现突跳
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            x_left = float(rect_aligned.left())
            x_right = float(rect_aligned.right())
            x_center = x_left + float(rect_aligned.width() / 2.0)

            y_top = float(rect_aligned.top())
            y_bottom = float(rect_aligned.bottom())
            y_center = y_top + float(rect_aligned.height() / 2.0)

            dots_pts = [
                (x_left, y_top),
                (x_center, y_top),
                (x_right, y_top),
                (x_right, y_center),
                (x_right, y_bottom),
                (x_center, y_bottom),
                (x_left, y_bottom),
                (x_left, y_center),
            ]
            d = int(self._dot_r * 2)
            for px, py in dots_pts:
                painter.setBrush(QColor(255, 255, 255, 230))
                painter.drawEllipse(QRectF(px - float(self._dot_r) - 1.0, py - float(self._dot_r) - 1.0, float(d + 2), float(d + 2)))
                painter.setBrush(border_color)
                painter.drawEllipse(QRectF(px - float(self._dot_r), py - float(self._dot_r), float(d), float(d)))
        finally:
            if painter.isActive():
                painter.end()

    def _capture_freeze_snapshot(self) -> None:
        magnifier = getattr(self, "_magnifier", None)
        magnifier_was_visible = False
        try:
            if magnifier is not None:
                try:
                    magnifier_was_visible = bool(magnifier.isVisible())
                    if bool(magnifier_was_visible):
                        magnifier.hide()
                        QApplication.processEvents()
                except Exception:
                    magnifier_was_visible = False
            import mss
            import numpy as np

            physical_geo = _union_rect([mapping.physical for mapping in self._screen_mappings])
            left = int(physical_geo.x())
            top = int(physical_geo.y())
            width = int(max(1, physical_geo.width()))
            height = int(max(1, physical_geo.height()))
            with mss.mss() as sct:
                raw = np.asarray(sct.grab({"left": left, "top": top, "width": width, "height": height}), dtype=np.uint8)
            if raw.ndim != 3 or raw.shape[2] < 4:
                return
            h, w = int(raw.shape[0]), int(raw.shape[1])
            qimg = QImage(raw.data, w, h, int(raw.strides[0]), QImage.Format.Format_RGB32).copy()
            dpr = _valid_device_pixel_ratio(_mapping_for_logical_point(QCursor.pos(), self._screen_mappings).dpr)
            qimg.setDevicePixelRatio(dpr)
            pixmap = QPixmap.fromImage(qimg)
            pixmap.setDevicePixelRatio(dpr)
            segments: list[tuple[QRect, QPixmap]] = []
            for mapping in self._screen_mappings:
                rel = QRect(
                    int(mapping.physical.x() - left),
                    int(mapping.physical.y() - top),
                    int(mapping.physical.width()),
                    int(mapping.physical.height()),
                ).intersected(QRect(0, 0, width, height))
                if rel.width() <= 0 or rel.height() <= 0:
                    continue
                part = qimg.copy(rel)
                part.setDevicePixelRatio(float(mapping.dpr))
                part_pixmap = QPixmap.fromImage(part)
                part_pixmap.setDevicePixelRatio(float(mapping.dpr))
                segments.append((QRect(mapping.logical), part_pixmap))
            self._freeze_logical_rect = QRect(self._screen_geo)
            self._freeze_qimage = qimg
            self._freeze_pixmap = pixmap
            self._freeze_segments = segments
            self._freeze_screen_bgr = raw
            self._freeze_screen_left = int(left)
            self._freeze_screen_top = int(top)
            self._cv_edge_cache_key = None
            self._cv_edge_cache_result = None
            self._cv_edge_cache_ts = 0.0
            self._cv_edge_tile_key = None
            self._cv_edge_tile_rects = []
            self._sync_magnifier_source()
        except Exception:
            self._freeze_pixmap = None
            self._freeze_qimage = None
            self._freeze_segments = []
            self._freeze_screen_bgr = None
            self._cv_edge_cache_key = None
            self._cv_edge_cache_result = None
            self._cv_edge_cache_ts = 0.0
            self._cv_edge_tile_key = None
            self._cv_edge_tile_rects = []
            self._sync_magnifier_source()
        finally:
            if magnifier is not None and bool(magnifier_was_visible) and not bool(self._confirmed):
                try:
                    magnifier.show()
                    magnifier.raise_()
                except Exception:
                    pass
