from __future__ import annotations

import os
import queue
import threading
from typing import Callable, Optional

from deepcat.utils.crash_reporter import write_crash_breadcrumb


_dispatch_lock = threading.Lock()
_dispatch_queue: Optional["queue.SimpleQueue[Callable[[], None]]"] = None


def _dispatch(callback: Callable[[], None]) -> None:
    """把热键回调投递到常驻工作线程执行。

    低级键盘钩子回调若超过 LowLevelHooksTimeout，Windows 会静默摘除钩子且无任何
    告警，所以钩子内绝不能同步执行业务回调——这里只做一次入队后立即返回。
    """
    global _dispatch_queue
    with _dispatch_lock:
        if _dispatch_queue is None:
            _dispatch_queue = queue.SimpleQueue()
            worker = threading.Thread(target=_dispatch_loop, name="hotkey-dispatch", daemon=True)
            worker.start()
    _dispatch_queue.put(callback)


def _dispatch_loop() -> None:
    while True:
        callback = _dispatch_queue.get()  # type: ignore[union-attr]
        try:
            callback()
        except Exception as e:
            write_crash_breadcrumb("hotkey.callback_failed", error=repr(e))


def _is_pynput_suppress_exception(e: BaseException) -> bool:
    # pynput 通过抛 SuppressException 阻止按键继续传播，安全包装必须放行它
    return any(cls.__name__ == "SuppressException" for cls in type(e).__mro__)


def _safe_event_filter(inner: Callable) -> Callable:
    """包装 win32_event_filter：钩子内抛出的异常会杀死 pynput 监听线程，
    导致热键永久失效且无告警，这里兜底吞掉并放行事件。"""

    def wrapped(msg, data):
        try:
            return inner(msg, data)
        except Exception as e:
            if _is_pynput_suppress_exception(e):
                raise
            write_crash_breadcrumb("hotkey.event_filter_failed", error=repr(e))
            return True

    return wrapped


def _stop_listener(listener, owner: str) -> None:
    try:
        if listener is None:
            return
        listener.stop()
        listener.join(timeout=1.0)
        if listener.is_alive():
            write_crash_breadcrumb("hotkey.listener_join_timeout", owner=owner)
    except Exception as e:
        write_crash_breadcrumb("hotkey.listener_cleanup_failed", owner=owner, error=repr(e))


def _single_hotkey_vk(keyboard_module, hotkey: str) -> Optional[int]:
    try:
        parts = keyboard_module.HotKey.parse(str(hotkey or "").strip())
    except Exception:
        return None
    if len(parts) != 1:
        return None
    key = parts[0]
    vk = getattr(key, "vk", None)
    if vk is None and hasattr(key, "value"):
        vk = getattr(key.value, "vk", None)
    try:
        return int(vk) if vk is not None else None
    except Exception:
        return None


def _key_to_vk(keyboard_module, key) -> Optional[int]:
    try:
        if hasattr(key, "vk") and key.vk is not None:
            return int(key.vk)
        if hasattr(key, "char") and key.char:
            return ord(key.char.upper())
        special = {
            keyboard_module.Key.space: 0x20,
            keyboard_module.Key.enter: 0x0D,
            keyboard_module.Key.tab: 0x09,
            keyboard_module.Key.backspace: 0x08,
            keyboard_module.Key.esc: 0x1B,
            keyboard_module.Key.ctrl: 0x11,
            keyboard_module.Key.ctrl_l: 0xA2,
            keyboard_module.Key.ctrl_r: 0xA3,
            keyboard_module.Key.alt: 0x12,
            keyboard_module.Key.alt_l: 0xA4,
            keyboard_module.Key.alt_r: 0xA5,
            keyboard_module.Key.shift: 0x10,
            keyboard_module.Key.shift_l: 0xA0,
            keyboard_module.Key.shift_r: 0xA1,
            keyboard_module.Key.cmd: 0x5B,
            keyboard_module.Key.cmd_l: 0x5B,
            keyboard_module.Key.cmd_r: 0x5C,
        }
        if key in special:
            return special[key]

        f_keys = {
            keyboard_module.Key.f1: 0x70,
            keyboard_module.Key.f2: 0x71,
            keyboard_module.Key.f3: 0x72,
            keyboard_module.Key.f4: 0x73,
            keyboard_module.Key.f5: 0x74,
            keyboard_module.Key.f6: 0x75,
            keyboard_module.Key.f7: 0x76,
            keyboard_module.Key.f8: 0x77,
            keyboard_module.Key.f9: 0x78,
            keyboard_module.Key.f10: 0x79,
            keyboard_module.Key.f11: 0x7A,
            keyboard_module.Key.f12: 0x7B,
        }
        if key in f_keys:
            return f_keys[key]
    except Exception:
        pass
    return None


def _parse_hotkey(keyboard_module, hotkey_str: str) -> tuple[set[int], int]:
    modifiers = set()
    main_vk = 0
    try:
        parts = keyboard_module.HotKey.parse(str(hotkey_str or "").strip().lower())
        modifier_vks = {0x11, 0x12, 0x10, 0x5B}  # VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN
        modifier_aliases = {
            0xA2: 0x11, 0xA3: 0x11, 0xA4: 0x12, 0xA5: 0x12,
            0xA0: 0x10, 0xA1: 0x10, 0x5C: 0x5B,
        }
        for part in parts:
            vk = _key_to_vk(keyboard_module, part)
            if vk is not None:
                vk = modifier_aliases.get(vk, vk)
                if vk in modifier_vks:
                    modifiers.add(vk)
                else:
                    if main_vk:
                        return set(), 0
                    main_vk = vk
    except Exception:
        pass
    return modifiers, main_vk


def _modifier_down(mod_vk: int) -> bool:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        vks = [mod_vk]
        if mod_vk == 0x11:  # Ctrl
            vks = [0x11, 0xA2, 0xA3]
        elif mod_vk == 0x12:  # Alt
            vks = [0x12, 0xA4, 0xA5]
        elif mod_vk == 0x10:  # Shift
            vks = [0x10, 0xA0, 0xA1]
        elif mod_vk == 0x5B:  # Win
            vks = [0x5B, 0x5C]

        for vk in vks:
            if int(user32.GetAsyncKeyState(int(vk))) & 0x8000:
                return True
    except Exception:
        return False
    return False


def _modifiers_match(required_modifiers: set[int]) -> bool:
    all_modifiers = {0x11, 0x12, 0x10, 0x5B}
    for mod in all_modifiers:
        is_pressed = _modifier_down(mod)
        if mod in required_modifiers:
            if not is_pressed:
                return False
        else:
            if is_pressed:
                return False
    return True


class HotkeyListener:
    def __init__(self, pause_hotkey: str = "<ctrl>+d", *, stop_event: threading.Event | None = None) -> None:
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.clear()
        self._listener = None
        self.pause_hotkey = pause_hotkey
        self._suppress_until_release = False
        self._suppress_vk = 0

    def start(self) -> None:
        try:
            from pynput import keyboard
        except Exception as e:
            raise RuntimeError("未安装 pynput，请先 pip install pynput") from e

        def toggle_pause():
            if self.pause_event.is_set():
                self.pause_event.clear()
            else:
                self.pause_event.set()

        modifiers, main_vk = _parse_hotkey(keyboard, self.pause_hotkey)

        press_messages = {0x0100, 0x0104}
        release_messages = {0x0101, 0x0105}
        injected_flags = 0x00000010 | 0x00000002

        def event_filter(msg, data):
            try:
                current_vk = int(getattr(data, "vkCode", -1))
                flags = int(getattr(data, "flags", 0))
                message = int(msg)
            except Exception:
                return True

            if bool(flags & injected_flags):
                return True

            if current_vk == 0x1B:  # VK_ESCAPE
                if message in press_messages:
                    self.stop_event.set()
                return True

            if not main_vk:
                return True

            if current_vk == main_vk:
                if message in press_messages:
                    if _modifiers_match(modifiers):
                        self._suppress_until_release = True
                        self._suppress_vk = current_vk
                        toggle_pause()
                        return False
                elif message in release_messages:
                    if bool(self._suppress_until_release) and current_vk == int(self._suppress_vk):
                        self._suppress_until_release = False
                        self._suppress_vk = 0
                        return False

            if bool(self._suppress_until_release) and current_vk == int(self._suppress_vk):
                if message in release_messages:
                    self._suppress_until_release = False
                    self._suppress_vk = 0
                    return False
                return False

            return True

        self._listener = keyboard.Listener(win32_event_filter=_safe_event_filter(event_filter))
        self._listener.daemon = True
        self._listener.start()

    def should_stop(self) -> bool:
        return self.stop_event.is_set()

    def is_paused(self) -> bool:
        return self.pause_event.is_set()

    def wait_if_paused(self, poll_interval: float = 0.1) -> None:
        if not self.is_paused():
            return
        while self.is_paused() and not self.should_stop():
            self.stop_event.wait(timeout=poll_interval)

    def cleanup(self) -> None:
        listener = self._listener
        self._listener = None
        self._suppress_until_release = False
        self._suppress_vk = 0
        _stop_listener(listener, "HotkeyListener")


class GlobalStartHotkey:
    def __init__(self, on_start: Callable[[], None], hotkey: str = "<f1>") -> None:
        self._on_start = on_start
        self._hotkey = hotkey
        self._listener = None
        self._native_hotkey = None
        self._single_key_down = False
        self._suppress_until_release = False
        self._suppress_vk = 0

    def start(self) -> None:
        if self._native_hotkey is not None or self._listener is not None:
            return
        try:
            from pynput import keyboard
        except Exception as e:
            raise RuntimeError("未安装 pynput，请先 pip install pynput") from e

        modifiers, main_vk = _parse_hotkey(keyboard, self._hotkey)

        if not main_vk:
            raise ValueError("快捷键必须包含一个主键，可配合 Ctrl、Alt、Shift 或 Win")

        if os.name == "nt":
            from deepcat.input.windows_hotkey import WindowsHotkey

            native_hotkey = WindowsHotkey(main_vk, modifiers, lambda: _dispatch(self._on_start))
            try:
                native_hotkey.start()
                self._native_hotkey = native_hotkey
                return
            except Exception as error:
                try:
                    native_hotkey.cleanup()
                except Exception:
                    pass
                self._native_hotkey = None
                write_crash_breadcrumb(
                    "hotkey.native_conflict_fallback_hook",
                    hotkey=str(self._hotkey),
                    virtual_key=main_vk,
                    error=repr(error),
                )

        event_filter = self._single_key_filter(main_vk, modifiers)

        self._listener = keyboard.Listener(win32_event_filter=_safe_event_filter(event_filter))
        self._listener.daemon = True
        self._listener.start()

    def _single_key_filter(self, main_vk: int, modifiers: Optional[set[int]] = None):
        required_modifiers = modifiers or set()
        press_messages = {0x0100, 0x0104}
        release_messages = {0x0101, 0x0105}
        injected_flags = 0x00000010 | 0x00000002

        def event_filter(msg, data):
            try:
                current_vk = int(getattr(data, "vkCode", -1))
                flags = int(getattr(data, "flags", 0))
                message = int(msg)
            except Exception:
                return True

            if bool(flags & injected_flags):
                return True

            if current_vk == main_vk:
                if message in press_messages:
                    if _modifiers_match(required_modifiers):
                        if not self._single_key_down:
                            self._single_key_down = True
                            self._suppress_until_release = True
                            self._suppress_vk = current_vk
                            # 回调异步分发，钩子立即返回，避免超时被 Windows 摘除钩子
                            _dispatch(self._on_start)
                        self._suppress_current_event()
                        return False
                elif message in release_messages:
                    self._single_key_down = False
                    if bool(self._suppress_until_release) and current_vk == int(self._suppress_vk):
                        self._suppress_until_release = False
                        self._suppress_vk = 0
                        self._suppress_current_event()
                        return False

            if bool(self._suppress_until_release) and current_vk == int(self._suppress_vk):
                if message in release_messages:
                    self._suppress_until_release = False
                    self._suppress_vk = 0
                self._suppress_current_event()
                return False

            return True

        return event_filter

    def _suppress_current_event(self) -> None:
        listener = self._listener
        suppress = getattr(listener, "suppress_event", None)
        if callable(suppress):
            suppress()

    def cleanup(self) -> None:
        native_hotkey = self._native_hotkey
        self._native_hotkey = None
        if native_hotkey is not None:
            native_hotkey.cleanup()
        listener = self._listener
        self._listener = None
        self._single_key_down = False
        self._suppress_until_release = False
        self._suppress_vk = 0
        _stop_listener(listener, "GlobalStartHotkey")
