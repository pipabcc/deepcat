from __future__ import annotations

import time

from PyQt6 import sip
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QWidget

POPUP_EXACT_WIDTH_PROPERTY = "matchPopupWidthToParent"
DISABLE_GLOBAL_TOOLTIP_PROPERTY = "disableGlobalSmoothTooltip"
POPUP_TOGGLE_SUPPRESS_SECONDS = 0.28


def cursor_over_widget(widget: QWidget | None) -> bool:
    if widget is None:
        return False
    try:
        return widget.rect().contains(widget.mapFromGlobal(QCursor.pos()))
    except Exception:
        return False


def live_popup(popup: object) -> QWidget | None:
    if popup is None:
        return None
    try:
        if sip.isdeleted(popup):
            return None
    except Exception:
        pass
    return popup if isinstance(popup, QWidget) else None


def should_skip_anchor_popup(anchor: QWidget | None, popup: QWidget) -> bool:
    if anchor is None:
        return False
    now = time.monotonic()
    try:
        suppress_until = float(getattr(anchor, "_deepcat_popup_suppress_until", 0.0) or 0.0)
    except Exception:
        suppress_until = 0.0
    if now < suppress_until:
        return True

    active = live_popup(getattr(anchor, "_deepcat_active_popup", None))
    if active is not None and active is not popup and active.isVisible():
        try:
            active.close()
        except Exception:
            pass
        setattr(anchor, "_deepcat_popup_suppress_until", now + POPUP_TOGGLE_SUPPRESS_SECONDS)
        return True
    setattr(anchor, "_deepcat_active_popup", popup)

    def clear_active() -> None:
        if getattr(anchor, "_deepcat_active_popup", None) is popup:
            setattr(anchor, "_deepcat_active_popup", None)

    try:
        popup.destroyed.connect(lambda *_: clear_active())
    except Exception:
        pass
    return False


def mark_anchor_popup_closed(anchor: QWidget | None, popup: QWidget) -> None:
    if anchor is None:
        return
    if cursor_over_widget(anchor):
        setattr(anchor, "_deepcat_popup_suppress_until", time.monotonic() + POPUP_TOGGLE_SUPPRESS_SECONDS)
    if getattr(anchor, "_deepcat_active_popup", None) is popup:
        setattr(anchor, "_deepcat_active_popup", None)


def set_disable_global_tooltip(widget: QWidget | None, disabled: bool = True) -> None:
    if widget is None:
        return
    try:
        widget.setProperty(DISABLE_GLOBAL_TOOLTIP_PROPERTY, bool(disabled))
    except Exception:
        pass


def is_global_tooltip_disabled(widget: QWidget | None) -> bool:
    current = widget
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        try:
            value = current.property(DISABLE_GLOBAL_TOOLTIP_PROPERTY)
        except Exception:
            value = False
        if value is True or str(value).strip().lower() in {"1", "true", "yes"}:
            return True
        try:
            current = current.parentWidget()
        except Exception:
            current = None
    return False


def find_parent_with_attr(widget: QWidget, attr_name: str) -> QWidget | None:
    parent = widget.parentWidget()
    seen: set[int] = set()
    while parent is not None and id(parent) not in seen:
        seen.add(id(parent))
        if hasattr(parent, attr_name):
            return parent
        try:
            window = parent.window()
        except Exception:
            window = None
        if window is not None and window is not parent and hasattr(window, attr_name):
            return window
        parent = parent.parentWidget()
    return None
