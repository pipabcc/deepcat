from __future__ import annotations

from typing import Optional


def pynput_to_qt(pynput_hotkey: str) -> str:
    s = str(pynput_hotkey or "").lower().strip()
    parts = [p.strip() for p in s.split("+") if p.strip()]
    mods = []
    key: Optional[str] = None
    for p in parts:
        if p in {"<ctrl>", "<control>"}:
            mods.append("Ctrl")
        elif p == "<shift>":
            mods.append("Shift")
        elif p == "<alt>":
            mods.append("Alt")
        elif p in {"<cmd>", "<win>", "<meta>"}:
            mods.append("Meta")
        else:
            key = p.strip("<>").upper() if len(p.strip("<>")) == 1 else p.strip("<>").upper()
    if not key:
        key = ""
    seq = "+".join([*mods, key]).strip("+")
    return seq


def qt_to_pynput(qt_seq: str) -> Optional[str]:
    s = str(qt_seq or "").strip()
    if not s:
        return None
    parts = [p.strip() for p in s.replace(" ", "").split("+") if p.strip()]
    mods = []
    key: Optional[str] = None
    for p in parts:
        u = p.upper()
        if u in {"CTRL", "CONTROL"}:
            mods.append("<ctrl>")
        elif u == "SHIFT":
            mods.append("<shift>")
        elif u == "ALT":
            mods.append("<alt>")
        elif u in {"META", "WIN", "CMD"}:
            mods.append("<cmd>")
        else:
            key = p
    if not key:
        return None
    k = str(key).strip()
    if not k:
        return None
    if len(k) == 1:
        return "+".join([*mods, k.lower()])
    n = k.lower().replace(" ", "").replace("-", "_")
    aliases = {
        "esc": "esc",
        "escape": "esc",
        "enter": "enter",
        "return": "enter",
        "space": "space",
        "tab": "tab",
        "backtab": "tab",
        "backspace": "backspace",
        "delete": "delete",
        "del": "delete",
        "insert": "insert",
        "ins": "insert",
        "home": "home",
        "end": "end",
        "pgup": "page_up",
        "pageup": "page_up",
        "pgdown": "page_down",
        "pagedown": "page_down",
        "left": "left",
        "right": "right",
        "up": "up",
        "down": "down",
        "capslock": "caps_lock",
        "numlock": "num_lock",
        "scrolllock": "scroll_lock",
        "pause": "pause",
        "print": "print_screen",
        "printscreen": "print_screen",
        "prtsc": "print_screen",
        "menu": "menu",
    }
    if n in aliases:
        n = aliases[n]
    elif n.startswith("f") and n[1:].isdigit():
        pass
    return "+".join([*mods, f"<{n}>"])
