from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QMimeData, QByteArray, QEventLoop
from PyQt6.QtGui import QClipboard, QCursor, QGuiApplication
from PyQt6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit

from deepcat.utils.logger import get_logger


VK_LBUTTON = 0x01
VK_SPACE = 0x20
VK_CONTROL = 0x11
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_MENU = 0x12
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_A = 0x41
VK_C = 0x43
VK_V = 0x56
VK_INSERT = 0x2D
MODIFIER_KEY_GROUPS = {
    VK_CONTROL: (VK_CONTROL, VK_LCONTROL, VK_RCONTROL),
    VK_MENU: (VK_MENU, VK_LMENU, VK_RMENU),
    0x10: (0x10, 0xA0, 0xA1),  # Shift
    0x5B: (0x5B, 0x5C),  # Win
}
KEYEVENTF_KEYUP = 0x0002
WM_COPY = 0x0301
WM_NCHITTEST = 0x0084
SMTO_ABORTIFHUNG = 0x0002
GA_ROOT = 2
WINDOW_CHROME_HITTEST_CODES = {
    2,   # HTCAPTION
    3,   # HTSYSMENU
    4,   # HTGROWBOX / HTSIZE
    5,   # HTMENU
    6,   # HTHSCROLL
    7,   # HTVSCROLL
    8,   # HTMINBUTTON
    9,   # HTMAXBUTTON
    10,  # HTLEFT
    11,  # HTRIGHT
    12,  # HTTOP
    13,  # HTTOPLEFT
    14,  # HTTOPRIGHT
    15,  # HTBOTTOM
    16,  # HTBOTTOMLEFT
    17,  # HTBOTTOMRIGHT
    18,  # HTBORDER
    20,  # HTCLOSE
    21,  # HTHELP
}
WINDOW_MOVE_BAND_FALLBACK_PX = 32
WINDOW_DRAG_COPY_BLOCK_SECONDS = 0.9


_text_input_suppression_lock = threading.Lock()
_text_input_suppressed_until = 0.0


def suppress_selection_reuse_for_text_input(duration: float = 1.0) -> None:
    global _text_input_suppressed_until
    until = time.monotonic() + max(0.1, float(duration))
    with _text_input_suppression_lock:
        _text_input_suppressed_until = max(float(_text_input_suppressed_until), float(until))


def selection_reuse_suppressed_for_text_input() -> bool:
    with _text_input_suppression_lock:
        return time.monotonic() < float(_text_input_suppressed_until)


class GlobalSelectionTranslateListener(QObject):
    triggerStarted = pyqtSignal(int, int)
    popupRequested = pyqtSignal(int, int)
    translateRequested = pyqtSignal(str, int, int, str)
    lookupFailed = pyqtSignal(int, int, str)
    errorOccurred = pyqtSignal(str)
    selectionCancelled = pyqtSignal()
    forceSelectionCancelled = pyqtSignal()
    _triggerAccepted = pyqtSignal(int, int, str)
    _triggerAcceptedDelayed = pyqtSignal(int, int, str, int)
    _pendingArmDelayed = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._enabled = False
        self._lock = threading.RLock()
        self._left_pressed = False
        self._dragging = False
        self._press_pos: Optional[tuple[int, int]] = None
        self._last_pos: Optional[tuple[int, int]] = None
        self._press_window_hwnd = 0
        self._press_window_rect: Optional[tuple[int, int, int, int]] = None
        self._mouse_drag_blocked = False
        self._selection_copy_blocked_until = 0.0
        self._pending_until = 0.0
        self._pending_pos: Optional[tuple[int, int]] = None
        self._pending_rect_pos: Optional[tuple[int, int]] = None
        self._trigger_key_down = False
        self._ctrl_a_popup_down = False
        self._keyboard_listener = None
        self._suppress_trigger_until_release = False
        self._suppress_trigger_vk = 0
        self._min_drag_pixels = 5
        self._pending_seconds = 3.0
        self._max_text_len = 8000
        self._selection_popup_enabled = False
        self._selection_popup_blocked = False
        self._suspended = False
        self._last_selection_html = ""
        self._poll = QTimer(self)
        self._poll.setInterval(50)
        self._poll.timeout.connect(self._poll_input_state)
        self._triggerAccepted.connect(self._copy_selection_and_emit)
        self._triggerAcceptedDelayed.connect(self._emit_trigger_after_delay)
        self._pendingArmDelayed.connect(self._arm_pending_at_cursor_after_delay)
        self._translate_modifiers: set[int] = set()
        self._translate_main_vk: int = 0
        self._popup_modifiers: set[int] = set()
        self._popup_main_vk: int = 0
        self._translate_hotkey_str = "<alt>+<space>"
        self._popup_hotkey_str = "<alt>+b"
        self._last_physical_copy_paste_time = 0.0
        self._last_physical_copy_paste_action = ""
        self._physical_down_vks: set[int] = set()
        try:
            self.set_hotkeys(self._translate_hotkey_str, self._popup_hotkey_str)
        except Exception:
            pass

    def _key_to_vk(self, key) -> Optional[int]:
        try:
            from pynput import keyboard
            if key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                return 0x11  # VK_CONTROL
            if key == keyboard.Key.alt or key == keyboard.Key.alt_l or key == keyboard.Key.alt_r or key == keyboard.Key.alt_gr:
                return 0x12  # VK_MENU
            if key == keyboard.Key.shift or key == keyboard.Key.shift_l or key == keyboard.Key.shift_r:
                return 0x10  # VK_SHIFT
            if key == keyboard.Key.cmd or key == keyboard.Key.cmd_l or key == keyboard.Key.cmd_r:
                return 0x5B  # VK_LWIN
            if key == keyboard.Key.space:
                return 0x20  # VK_SPACE

            if isinstance(key, keyboard.Key):
                vk = getattr(key, "vk", None)
                if vk is None and hasattr(key, "value"):
                    vk = getattr(key.value, "vk", None)
                if vk is not None:
                    return int(vk)
                f_keys = {
                    keyboard.Key.f1: 0x70,
                    keyboard.Key.f2: 0x71,
                    keyboard.Key.f3: 0x72,
                    keyboard.Key.f4: 0x73,
                    keyboard.Key.f5: 0x74,
                    keyboard.Key.f6: 0x75,
                    keyboard.Key.f7: 0x76,
                    keyboard.Key.f8: 0x77,
                    keyboard.Key.f9: 0x78,
                    keyboard.Key.f10: 0x79,
                    keyboard.Key.f11: 0x7A,
                    keyboard.Key.f12: 0x7B,
                }
                if key in f_keys:
                    return f_keys[key]

            if hasattr(key, "vk") and key.vk is not None:
                return int(key.vk)
            if hasattr(key, "char") and key.char:
                return ord(key.char.upper())
        except Exception:
            pass
        return None

    def _parse_hotkey(self, hotkey_str: str) -> tuple[set[int], int]:
        modifiers = set()
        main_vk = 0
        try:
            from pynput import keyboard
            parts = keyboard.HotKey.parse(str(hotkey_str or "").strip().lower())
            modifier_vks = {0x11, 0x12, 0x10, 0x5B}  # VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN
            for part in parts:
                vk = self._key_to_vk(part)
                if vk is not None:
                    if vk in modifier_vks:
                        modifiers.add(vk)
                    else:
                        main_vk = vk
        except Exception:
            pass
        return modifiers, main_vk

    def set_hotkeys(self, translate_hotkey: str, popup_hotkey: str) -> None:
        with self._lock:
            self._translate_hotkey_str = translate_hotkey
            self._popup_hotkey_str = popup_hotkey
            self._translate_modifiers, self._translate_main_vk = self._parse_hotkey(translate_hotkey)
            self._popup_modifiers, self._popup_main_vk = self._parse_hotkey(popup_hotkey)

    def _modifiers_match(self, required_modifiers: set[int]) -> bool:
        for mod in MODIFIER_KEY_GROUPS:
            is_pressed = self._is_modifier_pressed(mod)
            if mod in required_modifiers:
                if not is_pressed:
                    return False
            else:
                if is_pressed:
                    return False
        return True

    def _is_modifier_pressed(self, modifier_vk: int) -> bool:
        key_codes = MODIFIER_KEY_GROUPS.get(modifier_vk, (modifier_vk,))
        with self._lock:
            if any(vk in self._physical_down_vks for vk in key_codes):
                return True
        # 低级键盘钩子先于 Windows 更新异步键态，优先使用已经收到的物理事件。
        return any(self._key_down(vk) for vk in key_codes)

    def _recover_released_trigger(self) -> None:
        with self._lock:
            trigger_vk = self._suppress_trigger_vk
            if self._suppress_trigger_until_release and not self._key_down(trigger_vk):
                # 键盘钩子漏收抬键后，下一次按键或轮询应能恢复触发。
                self._physical_down_vks.discard(trigger_vk)
                self._suppress_trigger_until_release = False
                self._suppress_trigger_vk = 0
                self._trigger_key_down = False

    def start(self) -> bool:
        if bool(self._enabled):
            return True
        self._reset_state()
        self._enabled = True
        try:
            self._start_keyboard_listener()
            self._poll.start()
            return True
        except Exception as exc:
            self.stop()
            self.errorOccurred.emit(f"启用划词功能失败：{exc}")
            get_logger().warning("启用划词功能失败: %s", exc)
            return False

    def stop(self) -> None:
        self._enabled = False
        try:
            self._poll.stop()
        except Exception:
            pass
        listener = self._keyboard_listener
        self._keyboard_listener = None
        try:
            if listener is not None:
                listener.stop()
                listener.join(timeout=1.0)
                if listener.is_alive():
                    time.sleep(0.1)
        except Exception:
            pass
        self._reset_state()

    def cleanup(self) -> None:
        self.stop()

    def set_selection_popup_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._selection_popup_enabled = bool(enabled)
        try:
            self._poll.setInterval(16 if bool(enabled) else 50)
        except Exception:
            pass

    def set_selection_popup_blocked(self, blocked: bool) -> None:
        with self._lock:
            self._selection_popup_blocked = bool(blocked)
            if bool(blocked):
                self._pending_until = 0.0
                self._pending_pos = None
                self._pending_rect_pos = None
                self._left_pressed = False
                self._dragging = False
                self._press_pos = None
                self._last_pos = None
                self._clear_window_drag_guard_locked()
                self._ctrl_a_popup_down = False
                self._trigger_key_down = False
                self._suppress_trigger_until_release = False
                self._suppress_trigger_vk = 0

    def set_suspended(self, suspended: bool) -> None:
        with self._lock:
            self._suspended = bool(suspended)
            if bool(suspended):
                self._pending_until = 0.0
                self._pending_pos = None
                self._pending_rect_pos = None
                self._left_pressed = False
                self._dragging = False
                self._press_pos = None
                self._last_pos = None
                self._clear_window_drag_guard_locked()
                self._trigger_key_down = False
                self._ctrl_a_popup_down = False
                self._suppress_trigger_until_release = False
                self._suppress_trigger_vk = 0

    def _reset_state(self) -> None:
        with self._lock:
            self._left_pressed = False
            self._dragging = False
            self._press_pos = None
            self._last_pos = None
            self._clear_window_drag_guard_locked()
            self._pending_until = 0.0
            self._pending_pos = None
            self._pending_rect_pos = None
            self._trigger_key_down = False
            self._ctrl_a_popup_down = False
            self._suppress_trigger_until_release = False
            self._suppress_trigger_vk = 0
            self._physical_down_vks.clear()
            self._last_physical_copy_paste_action = ""

    def _start_keyboard_listener(self) -> None:
        if self._keyboard_listener is not None:
            return
        from pynput import keyboard

        self._keyboard_listener = keyboard.Listener(win32_event_filter=self._keyboard_event_filter())
        self._keyboard_listener.daemon = True
        self._keyboard_listener.start()

    def _keyboard_event_filter(self):
        press_messages = {0x0100, 0x0104}
        release_messages = {0x0101, 0x0105}
        injected_flags = 0x00000010 | 0x00000002
        ctrl_keys = {VK_CONTROL, VK_LCONTROL, VK_RCONTROL}
        alt_keys = {VK_MENU, VK_LMENU, VK_RMENU}

        def event_filter(msg, data):
            try:
                current_vk = int(getattr(data, "vkCode", -1))
                flags = int(getattr(data, "flags", 0))
                message = int(msg)
            except Exception:
                return True

            with self._lock:
                trigger_keys = {self._translate_main_vk, self._popup_main_vk, VK_A, VK_C, VK_V} | ctrl_keys | alt_keys | {0x10, 0xA0, 0xA1, 0x5B, 0x5C}

            if current_vk not in trigger_keys or bool(flags & injected_flags):
                return True
            if message in press_messages:
                self._recover_released_trigger()
            self._track_physical_key_state(current_vk, message, press_messages, release_messages)
            input_suppressed = selection_reuse_suppressed_for_text_input()

            # 物理 Ctrl+C 或 Ctrl+V 瞬间强制销毁浮窗并彻底透传，绝不拦截物理复制粘贴，且完全不受挂起状态的限制
            if message in press_messages:
                if (current_vk == VK_C or current_vk == VK_V) and self._physical_or_logical_ctrl_pressed():
                    with self._lock:
                        self._last_physical_copy_paste_time = time.monotonic()
                        self._last_physical_copy_paste_action = "copy" if current_vk == VK_C else "paste"
                    self._clear_pending()
                    self.forceSelectionCancelled.emit()
                    return True

            with self._lock:
                if bool(self._suspended):
                    return True
                popup_blocked = bool(self._selection_popup_blocked)

            if message in press_messages:
                delayed_popup = False
                trigger_pos: Optional[tuple[int, int]] = None
                trigger_action = ""
                with self._lock:
                    if bool(self._suppress_trigger_until_release) and current_vk == int(self._suppress_trigger_vk):
                        return self._suppress_keyboard_event()
                    if current_vk == VK_A and self._control_pressed():
                        if input_suppressed:
                            self._clear_text_input_selection_tracking()
                            return True
                        if self._qt_focus_is_text_editing_widget():
                            suppress_selection_reuse_for_text_input(1.5)
                            self._clear_text_input_selection_tracking()
                            return True
                        if bool(self._enabled) and not bool(popup_blocked) and not bool(self._ctrl_a_popup_down):
                            self._pendingArmDelayed.emit(80)
                            if bool(self._selection_popup_enabled):
                                delayed_popup = True
                        self._ctrl_a_popup_down = True
                    else:
                        auto_translate = (current_vk == self._translate_main_vk) and self._modifiers_match(self._translate_modifiers)
                        manual_open = (current_vk == self._popup_main_vk) and self._modifiers_match(self._popup_modifiers)

                        if not bool(self._enabled) or not (auto_translate or manual_open):
                            return True
                        if input_suppressed or not self._has_pending_locked():
                            try:
                                x_cur, y_cur = self._cursor_pos()
                            except Exception:
                                x_cur, y_cur = (0, 0)
                            self._arm_pending_locked((x_cur, y_cur))
                        trigger_pos = self._pending_pos
                        trigger_action = "translate" if bool(auto_translate) else "manual"
                        self._trigger_key_down = True
                        self._suppress_trigger_until_release = True
                        self._suppress_trigger_vk = current_vk
                if bool(delayed_popup):
                    self._triggerAcceptedDelayed.emit(0, 0, "popup_detect", 80)
                    return True
                if trigger_pos is not None:
                    self._clear_pending()
                    self.triggerStarted.emit(int(trigger_pos[0]), int(trigger_pos[1]))
                    self._triggerAccepted.emit(int(trigger_pos[0]), int(trigger_pos[1]), str(trigger_action))
                    return self._suppress_keyboard_event()
                if trigger_action in {"translate", "manual"}:
                    return self._suppress_keyboard_event()
                return True

            if message in release_messages:
                with self._lock:
                    if current_vk == VK_A:
                        self._ctrl_a_popup_down = False
                    suppress_vk = int(self._suppress_trigger_vk)
                    should_suppress = bool(self._suppress_trigger_until_release) and (
                        suppress_vk <= 0 or current_vk == suppress_vk
                    )
                    self._trigger_key_down = False
                    if should_suppress:
                        self._suppress_trigger_until_release = False
                        self._suppress_trigger_vk = 0
                if bool(should_suppress):
                    return self._suppress_keyboard_event()
            return True

        return event_filter

    def _suppress_keyboard_event(self) -> bool:
        return False

    def _poll_input_state(self) -> None:
        if not bool(self._enabled):
            return
        self._recover_released_trigger()
        input_suppressed = selection_reuse_suppressed_for_text_input()
        if input_suppressed:
            self._clear_text_input_selection_tracking()
        if self._qt_focus_is_text_editing_widget() and self._key_down(VK_LBUTTON):
            suppress_selection_reuse_for_text_input(0.3)
            self._clear_text_input_selection_tracking()
            input_suppressed = True
        with self._lock:
            if bool(self._suspended):
                return
            popup_blocked = bool(self._selection_popup_blocked)
        try:
            left_pressed = not input_suppressed and self._key_down(VK_LBUTTON)
            with self._lock:
                t_vk = self._translate_main_vk
                t_mods = self._translate_modifiers
                p_vk = self._popup_main_vk
                p_mods = self._popup_modifiers
            auto_pressed = self._key_down(t_vk) and self._modifiers_match(t_mods)
            manual_pressed = self._key_down(p_vk) and self._modifiers_match(p_mods)
            trigger_pressed = bool(auto_pressed or manual_pressed)
            x, y = self._cursor_pos()
        except Exception:
            return

        trigger_pos: Optional[tuple[int, int]] = None
        trigger_action = "translate"
        should_cancel_popup = False

        # 拦截：如果当前鼠标位置或按下时的位置在划词弹窗内，不激活划词
        in_popup = False
        try:
            from PyQt6.QtCore import QPoint
            main_win = self.parent()
            if main_win is not None:
                p_pos = QPoint(int(x), int(y))
                for attr in ("_selection_translate_action_popup", "_selection_hover_translation_popup"):
                    popup = getattr(main_win, attr, None)
                    if popup is not None and popup.isVisible():
                        if popup.geometry().contains(p_pos):
                            in_popup = True
                            break
                        if self._press_pos is not None:
                            press_pos = QPoint(int(self._press_pos[0]), int(self._press_pos[1]))
                            if popup.geometry().contains(press_pos):
                                in_popup = True
                                break
        except Exception:
            pass

        with self._lock:
            if left_pressed and not bool(self._left_pressed):
                self._start_window_drag_guard_locked(int(x), int(y))
                self._left_pressed = True
                self._dragging = False
                self._press_pos = (int(x), int(y))
                self._last_pos = (int(x), int(y))
            elif left_pressed and bool(self._left_pressed):
                self._last_pos = (int(x), int(y))
                self._update_window_drag_guard_locked()
            elif (not left_pressed) and bool(self._left_pressed):
                self._left_pressed = False
                end_pos = self._last_pos or (int(x), int(y))
                if self._press_pos is not None:
                    dx = abs(int(end_pos[0]) - int(self._press_pos[0]))
                    dy = abs(int(end_pos[1]) - int(self._press_pos[1]))
                    self._dragging = dx >= self._min_drag_pixels or dy >= self._min_drag_pixels

                # 如果在划词弹窗内部操作，强制取消拖拽划词手势的判定
                if in_popup:
                    self._dragging = False

                window_drag_gesture = bool(self._mouse_drag_blocked) or self._window_drag_active_or_moved_locked()
                if bool(window_drag_gesture):
                    self._block_selection_copy_locked()
                    self._pending_until = 0.0
                    self._pending_pos = None
                    self._pending_rect_pos = None
                selection_gesture = bool(self._dragging) and not bool(window_drag_gesture)
                if not bool(popup_blocked) and bool(selection_gesture):
                    self._arm_pending_locked((int(end_pos[0]), int(end_pos[1])))
                if bool(self._selection_popup_enabled) and not bool(popup_blocked) and bool(selection_gesture):
                    trigger_pos = (int(end_pos[0]), int(end_pos[1]))
                    trigger_action = "popup_detect"
                # 非拖拽点击（即单击取消选中），若浮窗功能开启则通知关闭浮窗
                if bool(self._selection_popup_enabled) and not bool(selection_gesture):
                    # 如果点击是在弹窗内部，坚决不触发取消/关闭浮窗动作！
                    if not in_popup:
                        should_cancel_popup = True
                self._dragging = False
                self._press_pos = None
                self._clear_window_drag_guard_locked()

            if trigger_pressed and not bool(self._trigger_key_down):
                self._trigger_key_down = True
                if not self._has_pending_locked():
                    self._arm_pending_locked((int(x), int(y)))
                trigger_pos = self._pending_pos
                trigger_action = "translate" if bool(auto_pressed) else "manual"
            elif not trigger_pressed:
                self._trigger_key_down = False

        if trigger_pos is not None:
            x0 = int(trigger_pos[0])
            y0 = int(trigger_pos[1])
            if str(trigger_action) in {"popup", "popup_detect"}:
                self._triggerAccepted.emit(x0, y0, "popup_detect")
            else:
                self._clear_pending()
                self.triggerStarted.emit(x0, y0)
                self._triggerAccepted.emit(x0, y0, str(trigger_action))

        if should_cancel_popup:
            self.selectionCancelled.emit()

    def _clear_pending(self) -> None:
        with self._lock:
            self._pending_until = 0.0
            self._pending_pos = None
            self._pending_rect_pos = None

    def _arm_pending_locked(self, pos: tuple[int, int]) -> None:
        x = int(pos[0])
        y = int(pos[1])
        self._pending_until = time.monotonic() + float(self._pending_seconds)
        self._pending_pos = (x, y)
        self._pending_rect_pos = (x, y)

    def _key_down(self, vk: int) -> bool:
        try:
            return bool(ctypes.windll.user32.GetAsyncKeyState(int(vk)) & 0x8000)
        except Exception:
            return False

    def _track_physical_key_state(
        self,
        current_vk: int,
        message: int,
        press_messages: set[int],
        release_messages: set[int],
    ) -> None:
        vk = int(current_vk)
        with self._lock:
            if int(message) in press_messages:
                self._physical_down_vks.add(vk)
            elif int(message) in release_messages:
                self._physical_down_vks.discard(vk)
                if vk == VK_CONTROL:
                    self._physical_down_vks.discard(VK_LCONTROL)
                    self._physical_down_vks.discard(VK_RCONTROL)
                elif vk in {VK_LCONTROL, VK_RCONTROL}:
                    self._physical_down_vks.discard(VK_CONTROL)

    def _physical_ctrl_down_locked(self) -> bool:
        return any(vk in self._physical_down_vks for vk in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL))

    def _physical_ctrl_key_down(self) -> bool:
        with self._lock:
            return self._physical_ctrl_down_locked()

    def _physical_copy_paste_active_locked(self) -> bool:
        return self._physical_ctrl_down_locked() and any(vk in self._physical_down_vks for vk in (VK_C, VK_V))

    def _physical_or_logical_ctrl_pressed(self) -> bool:
        with self._lock:
            if self._physical_ctrl_down_locked():
                return True
        return self._control_pressed()

    def _physical_copy_paste_active_or_recent(self, since: float = 0.0, grace: float = 0.5) -> bool:
        now = time.monotonic()
        with self._lock:
            active = self._physical_copy_paste_active_locked()
            last_time = float(self._last_physical_copy_paste_time)
        if bool(active):
            return True
        if float(since or 0.0) > 0.0 and last_time >= float(since):
            return True
        return now - last_time < max(0.0, float(grace))

    def _physical_copy_after(self, since: float) -> bool:
        with self._lock:
            return (
                float(self._last_physical_copy_paste_time) >= float(since)
                and str(self._last_physical_copy_paste_action) == "copy"
            )

    def _control_pressed(self) -> bool:
        try:
            ctrl_down = bool(ctypes.windll.user32.GetKeyState(0x11) & 0x8000) or \
                        bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000)
            if ctrl_down:
                return True
        except Exception:
            pass
        return self._key_down(VK_CONTROL) or self._key_down(VK_LCONTROL) or self._key_down(VK_RCONTROL)

    def _alt_pressed(self) -> bool:
        return self._key_down(VK_MENU) or self._key_down(VK_LMENU) or self._key_down(VK_RMENU)

    def _cursor_pos(self) -> tuple[int, int]:
        try:
            pos = QCursor.pos()
            return (int(pos.x()), int(pos.y()))
        except Exception:
            pass
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return (0, 0)
        return (int(point.x), int(point.y))

    def _clear_window_drag_guard_locked(self) -> None:
        self._press_window_hwnd = 0
        self._press_window_rect = None
        self._mouse_drag_blocked = False

    def _block_selection_copy_locked(self) -> None:
        self._selection_copy_blocked_until = max(
            float(self._selection_copy_blocked_until),
            time.monotonic() + float(WINDOW_DRAG_COPY_BLOCK_SECONDS),
        )

    def _selection_copy_temporarily_blocked(self) -> bool:
        with self._lock:
            return time.monotonic() < float(self._selection_copy_blocked_until)

    def _start_window_drag_guard_locked(self, x: int, y: int) -> None:
        hwnd, rect, starts_on_window_chrome = self._window_drag_context(int(x), int(y))
        self._press_window_hwnd = int(hwnd or 0)
        self._press_window_rect = rect
        self._mouse_drag_blocked = bool(starts_on_window_chrome or self._system_move_size_hwnd())

    def _update_window_drag_guard_locked(self) -> None:
        if bool(self._mouse_drag_blocked):
            return
        if self._window_drag_active_or_moved_locked():
            self._mouse_drag_blocked = True

    def _window_drag_active_or_moved_locked(self) -> bool:
        if self._system_move_size_hwnd():
            return True
        return self._press_window_moved(int(self._press_window_hwnd), self._press_window_rect)

    def _window_drag_context(self, x: int, y: int) -> tuple[int, Optional[tuple[int, int, int, int]], bool]:
        try:
            hwnd = int(self._root_window_from_point(int(x), int(y)) or 0)
            if not hwnd:
                hwnd = int(self._foreground_hwnd() or 0)
            if not hwnd:
                return (0, None, False)
            rect = self._window_rect(hwnd)
            starts_on_window_chrome = self._hit_test_window_chrome(hwnd, int(x), int(y))
            if not bool(starts_on_window_chrome):
                starts_on_window_chrome = self._point_in_window_move_band(rect, int(x), int(y))
            return (hwnd, rect, bool(starts_on_window_chrome))
        except Exception:
            return (0, None, False)

    def _root_window_from_point(self, x: int, y: int) -> int:
        try:
            user32 = ctypes.windll.user32
            window_from_point = user32.WindowFromPoint
            window_from_point.restype = wintypes.HWND
            hwnd = int(window_from_point(wintypes.POINT(int(x), int(y))) or 0)
            if not hwnd:
                return 0
            get_ancestor = user32.GetAncestor
            get_ancestor.restype = wintypes.HWND
            root = int(get_ancestor(wintypes.HWND(hwnd), ctypes.c_uint(GA_ROOT)) or hwnd)
            return int(root or 0)
        except Exception:
            return 0

    def _foreground_hwnd(self) -> int:
        try:
            get_foreground_window = ctypes.windll.user32.GetForegroundWindow
            get_foreground_window.restype = wintypes.HWND
            return int(get_foreground_window() or 0)
        except Exception:
            return 0

    def _window_rect(self, hwnd: int) -> Optional[tuple[int, int, int, int]]:
        if int(hwnd or 0) <= 0:
            return None
        try:
            rect = wintypes.RECT()
            if not ctypes.windll.user32.GetWindowRect(wintypes.HWND(int(hwnd)), ctypes.byref(rect)):
                return None
            return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        except Exception:
            return None

    def _hit_test_window_chrome(self, hwnd: int, x: int, y: int) -> bool:
        if int(hwnd or 0) <= 0:
            return False
        try:
            lparam = ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)
            result = ctypes.c_size_t(0)
            ok = ctypes.windll.user32.SendMessageTimeoutW(
                wintypes.HWND(int(hwnd)),
                ctypes.c_uint(WM_NCHITTEST),
                wintypes.WPARAM(0),
                wintypes.LPARAM(int(lparam)),
                ctypes.c_uint(SMTO_ABORTIFHUNG),
                ctypes.c_uint(70),
                ctypes.byref(result),
            )
            if not ok:
                return False
            return int(result.value) in WINDOW_CHROME_HITTEST_CODES
        except Exception:
            return False

    def _point_in_window_move_band(
        self,
        rect: Optional[tuple[int, int, int, int]],
        x: int,
        y: int,
    ) -> bool:
        if rect is None:
            return False
        left, top, right, bottom = (int(v) for v in rect)
        if right <= left or bottom <= top:
            return False
        if int(x) < left or int(x) >= right:
            return False
        band = min(int(WINDOW_MOVE_BAND_FALLBACK_PX), max(0, int(bottom - top)))
        return top <= int(y) < top + band

    def _press_window_moved(
        self,
        hwnd: int,
        start_rect: Optional[tuple[int, int, int, int]],
    ) -> bool:
        if int(hwnd or 0) <= 0 or start_rect is None:
            return False
        current_rect = self._window_rect(int(hwnd))
        if current_rect is None:
            return False
        return any(abs(int(a) - int(b)) > 1 for a, b in zip(start_rect, current_rect))

    def _system_move_size_hwnd(self) -> int:
        seen: set[int] = set()
        for hwnd in (int(self._press_window_hwnd or 0), self._foreground_hwnd()):
            hwnd = int(hwnd or 0)
            if hwnd <= 0 or hwnd in seen:
                continue
            seen.add(hwnd)
            move_hwnd = self._thread_move_size_hwnd(hwnd)
            if move_hwnd:
                return int(move_hwnd)
        return 0

    def _thread_move_size_hwnd(self, hwnd: int) -> int:
        try:
            user32 = ctypes.windll.user32

            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("flags", ctypes.c_uint),
                    ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND),
                    ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND),
                    ("hwndCaret", wintypes.HWND),
                    ("rcCaret", wintypes.RECT),
                ]

            get_window_thread_process_id = user32.GetWindowThreadProcessId
            get_window_thread_process_id.restype = wintypes.DWORD
            tid = int(get_window_thread_process_id(wintypes.HWND(int(hwnd)), None) or 0)
            if tid <= 0:
                return 0
            info = GUITHREADINFO()
            info.cbSize = ctypes.sizeof(info)
            if user32.GetGUIThreadInfo(wintypes.DWORD(tid), ctypes.byref(info)):
                return int(info.hwndMoveSize or 0)
        except Exception:
            return 0
        return 0

    def _has_pending_locked(self) -> bool:
        if self._pending_pos is None:
            return False
        if time.monotonic() > float(self._pending_until):
            self._pending_until = 0.0
            self._pending_pos = None
            self._pending_rect_pos = None
            return False
        return True

    def _emit_trigger_after_delay(self, x: int, y: int, action: str, delay_ms: int) -> None:
        def emit_later() -> None:
            if selection_reuse_suppressed_for_text_input():
                self._clear_text_input_selection_tracking()
                return
            if self._selection_copy_temporarily_blocked():
                self._clear_pending()
                return
            with self._lock:
                if not bool(self._enabled) or bool(self._suspended):
                    return
                if str(action or "") == "popup_detect" and bool(self._selection_popup_blocked):
                    return
            x0 = int(x)
            y0 = int(y)
            if str(action or "") == "popup_detect":
                try:
                    x0, y0 = self._cursor_pos()
                except Exception:
                    pass
            self._triggerAccepted.emit(int(x0), int(y0), str(action or "translate"))

        QTimer.singleShot(max(0, int(delay_ms)), emit_later)

    def _arm_pending_at_cursor_after_delay(self, delay_ms: int) -> None:
        def arm_later() -> None:
            if selection_reuse_suppressed_for_text_input():
                self._clear_text_input_selection_tracking()
                return
            if self._selection_copy_temporarily_blocked():
                self._clear_pending()
                return
            with self._lock:
                if not bool(self._enabled) or bool(self._suspended) or bool(self._selection_popup_blocked):
                    return
            try:
                x0, y0 = self._cursor_pos()
            except Exception:
                x0, y0 = (0, 0)
            with self._lock:
                if not bool(self._enabled) or bool(self._suspended) or bool(self._selection_popup_blocked):
                    return
                self._arm_pending_locked((int(x0), int(y0)))

        QTimer.singleShot(max(0, int(delay_ms)), arm_later)

    def _copy_selection_and_emit(self, x: int, y: int, action: str = "translate") -> None:
        if not bool(self._enabled):
            return
        is_hotkey = str(action or "") in {"translate", "manual"}
        if not is_hotkey and selection_reuse_suppressed_for_text_input():
            self._clear_text_input_selection_tracking()
            return
        if not is_hotkey and self._selection_copy_temporarily_blocked():
            self._clear_pending()
            return

        # 拦截：如果当前鼠标物理位置在划词弹窗内，不激活划词
        try:
            main_win = self.parent()
            if not is_hotkey and main_win is not None:
                pos = QCursor.pos()
                for attr in ("_selection_translate_action_popup", "_selection_hover_translation_popup"):
                    popup = getattr(main_win, attr, None)
                    if popup is None or not popup.isVisible():
                        continue
                    if popup.geometry().contains(pos):
                        return
        except Exception:
            pass

        with self._lock:
            if bool(self._suspended):
                return
            if str(action or "") == "popup_detect" and bool(self._selection_popup_blocked):
                return
        # 主动快捷键可以紧接上一次复制；仍须让正在进行的物理复制、粘贴优先完成。
        if self._physical_copy_paste_active_or_recent(grace=0.0 if is_hotkey else 0.5):
            return
        try:
            text = self._copy_selected_text(
                fast=str(action or "") in {"popup_ready", "popup_detect"},
                is_hotkey=is_hotkey,
            )
        except KeyboardInterrupt:
            try:
                get_logger().warning("划词取词被中断，已忽略")
            except Exception:
                pass
            return
        with self._lock:
            if bool(self._suspended):
                return
        text = str(text or "").strip()
        if not text:
            message = "未获取到选中文字"
            try:
                get_logger().warning("划词触发后未获取到选中文本，可能目标软件不支持模拟复制或权限更高")
            except Exception:
                pass
            self.lookupFailed.emit(int(x), int(y), message)
            return
        if len(text) > int(self._max_text_len):
            text = text[: int(self._max_text_len)]
        out_action = "popup" if str(action or "") == "popup_detect" else str(action or "translate")
        self.translateRequested.emit(text, int(x), int(y), out_action)

    def _copy_selected_text(self, fast: bool = False, *, is_hotkey: bool = False) -> str:
        self._last_selection_html = ""
        copy_started_at = time.monotonic()
        app = QApplication.instance()
        clipboard = QGuiApplication.clipboard()
        if app is None or clipboard is None:
            return ""
        if self._physical_copy_paste_active_or_recent(grace=0.0 if is_hotkey else 0.2):
            return ""
        widget_text = self._selected_text_from_qt_focus()
        if widget_text.strip():
            self._last_selection_html = self._selected_html_from_qt_focus()
            return widget_text.strip()

        # 深度备份剪贴板，以防模拟键盘复制污染它
        backup_mime = QMimeData()
        original_mime = clipboard.mimeData()
        if original_mime is not None:
            for fmt in original_mime.formats():
                # 深拷贝 QByteArray，防止 Qt 内部回收临时 mime 数据后引发 access violation
                try:
                    data = original_mime.data(fmt)
                    if data is not None and not data.isEmpty():
                        backup_mime.setData(fmt, QByteArray(data))
                except Exception:
                    pass

        mode = QClipboard.Mode.Clipboard
        start_seq = self._clipboard_sequence_number()
        try:
            wm_timeout = 0.12 if bool(fast) else 0.35
            shortcut_timeout = 0.38 if bool(fast) else 1.4
            pynput_timeout = 0.28 if bool(fast) else 0.8
            settle = 0.04 if bool(fast) else 0.12

            self._send_wm_copy(settle=0.025 if bool(fast) else 0.05)
            text = self._wait_for_clipboard_text(clipboard, mode, wm_timeout, start_seq=start_seq)
            if text:
                return text
            if self._physical_copy_paste_active_or_recent(since=copy_started_at, grace=0.0):
                return ""

            if self._send_copy_shortcut("c", settle=settle, is_hotkey=is_hotkey):
                text = self._wait_for_clipboard_text(clipboard, mode, shortcut_timeout, start_seq=start_seq)
                if text:
                    return text
            if self._physical_copy_paste_active_or_recent(since=copy_started_at, grace=0.0):
                return ""
            if self._send_copy_shortcut("insert", settle=settle, is_hotkey=is_hotkey):
                text = self._wait_for_clipboard_text(clipboard, mode, shortcut_timeout, start_seq=start_seq)
                if text:
                    return text
            if self._physical_copy_paste_active_or_recent(since=copy_started_at, grace=0.0):
                return ""

            if self._send_pynput_copy_shortcut("c", settle=0.04 if bool(fast) else 0.08, is_hotkey=is_hotkey):
                text = self._wait_for_clipboard_text(clipboard, mode, pynput_timeout, start_seq=start_seq)
                if text:
                    return text
            if self._physical_copy_paste_active_or_recent(since=copy_started_at, grace=0.0):
                return ""
            if self._send_pynput_copy_shortcut("insert", settle=0.04 if bool(fast) else 0.08, is_hotkey=is_hotkey):
                return self._wait_for_clipboard_text(clipboard, mode, pynput_timeout, start_seq=start_seq)
            return ""
        except Exception as exc:
            get_logger().warning("划词取词失败: %s", exc)
            return ""
        finally:
            # 无论取词成功还是失败，均毫秒级将备份的多格式数据还原回剪切板
            try:
                # 只有当原始剪切板中确实有格式时才回填，防止清空空剪切板引发的不必要动作
                if backup_mime.formats() and not self._physical_copy_after(copy_started_at):
                    clipboard.setMimeData(backup_mime)
            except Exception as e:
                get_logger().warning("还原剪切板失败: %s", e)

    def _clipboard_sequence_number(self) -> int:
        try:
            return int(ctypes.windll.user32.GetClipboardSequenceNumber())
        except Exception:
            return 0

    def _wait_for_clipboard_text(self, clipboard, mode, timeout: float, start_seq: int = 0) -> str:
        app = QApplication.instance()
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            with self._lock:
                if bool(self._suspended):
                    return ""
            if app is not None:
                app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            if int(start_seq or 0) > 0 and self._clipboard_sequence_number() == int(start_seq):
                time.sleep(0.03)
                continue
            text = self._read_clipboard_text(clipboard, mode).strip()
            if text:
                try:
                    mime = clipboard.mimeData()
                    if mime and mime.hasHtml():
                        self._last_selection_html = str(mime.html() or "")
                except Exception:
                    pass
                return text
            time.sleep(0.03)
        return ""

    def _send_copy_shortcut(self, key_name: str, settle: float = 0.12, *, is_hotkey: bool = False) -> bool:
        ctrl_already_down = self._physical_ctrl_key_down()
        if (ctrl_already_down and not is_hotkey) or self._physical_copy_paste_active_or_recent(
            grace=0.0 if is_hotkey else 0.2
        ):
            return False
        key_vk = VK_INSERT if str(key_name).lower() == "insert" else VK_C
        ctrl_pressed_by_us = False
        try:
            user32 = ctypes.windll.user32
            try:
                # Ctrl 本身可能就是触发快捷键的一部分，不要求用户先松手。
                if not ctrl_already_down:
                    user32.keybd_event(VK_CONTROL, 0, 0, 0)
                    ctrl_pressed_by_us = True
                    time.sleep(0.015)
                user32.keybd_event(int(key_vk), 0, 0, 0)
                time.sleep(0.02)
            finally:
                try:
                    user32.keybd_event(int(key_vk), 0, KEYEVENTF_KEYUP, 0)
                finally:
                    time.sleep(0.01)
                    if bool(ctrl_pressed_by_us) and not self._physical_ctrl_key_down():
                        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(max(0.01, float(settle)))
            return True
        except Exception as exc:
            get_logger().warning("发送 Win32 复制快捷键失败: %s", exc)
        return self._send_pynput_copy_shortcut(key_name, settle=max(0.01, float(settle)), is_hotkey=is_hotkey)

    def _send_pynput_copy_shortcut(self, key_name: str, settle: float = 0.08, *, is_hotkey: bool = False) -> bool:
        ctrl_already_down = self._physical_ctrl_key_down()
        if (ctrl_already_down and not is_hotkey) or self._physical_copy_paste_active_or_recent(
            grace=0.0 if is_hotkey else 0.2
        ):
            return False
        try:
            from pynput import keyboard

            controller = keyboard.Controller()
            ctrl_pressed_by_us = False
            copy_key = keyboard.Key.insert if key_name == "insert" else "c"
            try:
                if not ctrl_already_down:
                    controller.press(keyboard.Key.ctrl)
                    ctrl_pressed_by_us = True
                controller.press(copy_key)
            finally:
                try:
                    controller.release(copy_key)
                finally:
                    if ctrl_pressed_by_us and not self._physical_ctrl_key_down():
                        controller.release(keyboard.Key.ctrl)
            time.sleep(max(0.01, float(settle)))
            return True
        except Exception as exc:
            get_logger().warning("发送复制快捷键失败: %s", exc)

        return False

    def _send_wm_copy(self, settle: float = 0.05) -> None:
        try:
            user32 = ctypes.windll.user32
            hwnds = []
            focused = self._focused_hwnd()
            foreground = int(user32.GetForegroundWindow() or 0)
            for hwnd in (focused, foreground):
                hwnd = int(hwnd or 0)
                if hwnd and hwnd not in hwnds:
                    hwnds.append(hwnd)
            result = ctypes.c_size_t(0)
            for hwnd in hwnds:
                try:
                    user32.SendMessageTimeoutW(
                        ctypes.c_void_p(hwnd),
                        ctypes.c_uint(WM_COPY),
                        ctypes.c_void_p(0),
                        ctypes.c_void_p(0),
                        ctypes.c_uint(SMTO_ABORTIFHUNG),
                    ctypes.c_uint(70),
                        ctypes.byref(result),
                    )
                except Exception:
                    continue
            time.sleep(max(0.01, float(settle)))
        except Exception as exc:
            get_logger().debug("发送 WM_COPY 失败: %s", exc)

    def _focused_hwnd(self) -> int:
        try:
            user32 = ctypes.windll.user32

            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("flags", ctypes.c_uint),
                    ("hwndActive", ctypes.c_void_p),
                    ("hwndFocus", ctypes.c_void_p),
                    ("hwndCapture", ctypes.c_void_p),
                    ("hwndMenuOwner", ctypes.c_void_p),
                    ("hwndMoveSize", ctypes.c_void_p),
                    ("hwndCaret", ctypes.c_void_p),
                    ("rcCaret", ctypes.c_long * 4),
                ]

            tid = int(user32.GetWindowThreadProcessId(ctypes.c_void_p(int(user32.GetForegroundWindow() or 0)), None))
            info = GUITHREADINFO()
            info.cbSize = ctypes.sizeof(info)
            if user32.GetGUIThreadInfo(ctypes.c_uint(tid), ctypes.byref(info)):
                return int(info.hwndFocus or info.hwndActive or 0)
        except Exception:
            return 0
        return 0

    def _selected_text_from_qt_focus(self) -> str:
        try:
            widget = QApplication.focusWidget()
            if isinstance(widget, QLineEdit):
                return str(widget.selectedText() or "")
            if isinstance(widget, (QTextEdit, QPlainTextEdit)):
                cursor = widget.textCursor()
                if cursor and cursor.hasSelection():
                    return str(cursor.selectedText() or "").replace("\u2029", "\n")
        except Exception:
            return ""
        return ""

    def _selected_html_from_qt_focus(self) -> str:
        try:
            from PyQt6.QtWidgets import QTextEdit, QPlainTextEdit
            widget = QApplication.focusWidget()
            if isinstance(widget, (QTextEdit, QPlainTextEdit)):
                cursor = widget.textCursor()
                if cursor and cursor.hasSelection():
                    return str(cursor.selection().toHtml() or "")
        except Exception:
            return ""
        return ""

    def _qt_focus_is_text_editing_widget(self) -> bool:
        try:
            if QApplication.activeWindow() is None:
                return False
            widget = QApplication.focusWidget()
            while widget is not None:
                if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
                    return True
                widget = widget.parent()
        except Exception:
            return False
        return False

    def _clear_text_input_selection_tracking(self) -> None:
        with self._lock:
            self._pending_until = 0.0
            self._pending_pos = None
            self._pending_rect_pos = None
            self._left_pressed = False
            self._dragging = False
            self._press_pos = None
            self._last_pos = None
            self._clear_window_drag_guard_locked()
            self._ctrl_a_popup_down = False

    def _read_clipboard_text(self, clipboard, mode) -> str:
        try:
            text = str(clipboard.text(mode) or "")
            if text.strip():
                return text
        except Exception:
            pass
        try:
            text = str(clipboard.text() or "")
            if text.strip():
                return text
        except Exception:
            pass
        return self._read_win32_clipboard_text()

    def _read_win32_clipboard_text(self) -> str:
        try:
            import win32clipboard  # type: ignore
            import win32con  # type: ignore
        except Exception:
            return ""
        opened = False
        try:
            for _ in range(6):
                try:
                    win32clipboard.OpenClipboard()
                    opened = True
                    break
                except Exception:
                    time.sleep(0.025)
            if not opened:
                return ""
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                return str(data or "")
        except Exception:
            return ""
        finally:
            if opened:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass
        return ""
