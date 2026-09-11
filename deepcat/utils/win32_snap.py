from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Set


@dataclass(frozen=True)
class WinRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return int(self.right - self.left)

    @property
    def height(self) -> int:
        return int(self.bottom - self.top)


def rect_from_point(x: int, y: int, ignore_hwnds: Optional[set[int]] = None) -> Optional[WinRect]:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    user32 = ctypes.windll.user32
    pt = wintypes.POINT(int(x), int(y))
    hwnd = None
    try:
        desktop = user32.GetDesktopWindow()
        pt2 = wintypes.POINT(int(x), int(y))
        user32.ScreenToClient(desktop, ctypes.byref(pt2))
        cwp_skipinvisible = 0x0001
        cwp_skipdisabled = 0x0002
        cwp_skiptransparent = 0x0004
        hwnd = user32.ChildWindowFromPointEx(desktop, pt2, cwp_skipinvisible | cwp_skipdisabled | cwp_skiptransparent)
    except Exception:
        hwnd = None
    if not hwnd:
        hwnd = user32.WindowFromPoint(pt)
    if not hwnd:
        return None
    ignores: Set[int] = set(int(h) for h in (ignore_hwnds or set()))
    try:
        gw_hwndnext = 2
        while hwnd and int(hwnd) in ignores:
            hwnd = user32.GetWindow(hwnd, gw_hwndnext)
    except Exception:
        pass

    def get_rect(h) -> Optional[WinRect]:
        r = RECT()
        ok = user32.GetWindowRect(h, ctypes.byref(r))
        if not ok:
            return None
        if int(r.right - r.left) <= 2 or int(r.bottom - r.top) <= 2:
            return None
        return WinRect(int(r.left), int(r.top), int(r.right), int(r.bottom))

    r_child = get_rect(hwnd)
    try:
        ga_root = 2
        hwnd_root = user32.GetAncestor(hwnd, ga_root)
    except Exception:
        hwnd_root = None
    r_root = get_rect(hwnd_root) if hwnd_root else None

    if r_child is None:
        return r_root
    if r_root is None:
        return r_child

    area_child = float(max(1, r_child.width) * max(1, r_child.height))
    area_root = float(max(1, r_root.width) * max(1, r_root.height))
    if area_child <= area_root * 0.70:
        return r_child
    return r_root
