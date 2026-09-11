"""通过 Windows 消息队列接收全局热键，不依赖低级键盘钩子的抬键状态。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
from typing import Callable

from deepcat.utils.crash_reporter import write_crash_breadcrumb


WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
_MODIFIER_FLAGS = {0x12: 0x0001, 0x11: 0x0002, 0x10: 0x0004, 0x5B: 0x0008}


def _load_user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    return user32


class WindowsHotkey:
    def __init__(self, main_vk: int, modifiers: set[int], callback: Callable[[], None]) -> None:
        self._main_vk = main_vk
        self._modifiers = MOD_NOREPEAT
        for modifier in modifiers:
            self._modifiers |= _MODIFIER_FLAGS[modifier]
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._error: Exception | None = None
        self._user32 = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._stopping.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="windows-hotkey", daemon=True)
        self._thread.start()
        if not self._ready.wait(2.0):
            self.cleanup()
            raise RuntimeError("等待系统热键注册超时")
        if self._error is not None:
            error = self._error
            self.cleanup()
            raise error

    def _run(self) -> None:
        registered = False
        try:
            self._user32 = _load_user32()
            self._thread_id = int(ctypes.windll.kernel32.GetCurrentThreadId())
            message = wintypes.MSG()
            # 先创建消息队列，确保启动失败或立即关闭时也能投递退出消息。
            self._user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            if not self._user32.RegisterHotKey(None, 1, self._modifiers, self._main_vk):
                error_code = ctypes.get_last_error()
                if error_code == 1409:
                    raise RuntimeError("该快捷键已被其他功能或程序占用，请选择其他组合")
                raise OSError(error_code, f"无法注册系统快捷键：{ctypes.FormatError(error_code).strip()}")
            registered = True
            self._ready.set()
            while not self._stopping.is_set():
                result = self._user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result == 0:
                    break
                if message.message == WM_HOTKEY and message.wParam == 1 and not self._stopping.is_set():
                    self._callback()
        except Exception as error:
            self._error = error
            write_crash_breadcrumb("hotkey.native_failed", virtual_key=self._main_vk, error=repr(error))
        finally:
            if registered:
                self._user32.UnregisterHotKey(None, 1)
            self._thread_id = 0
            self._ready.set()

    def cleanup(self) -> None:
        self._stopping.set()
        if self._thread_id and self._user32 is not None:
            self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
            if thread.is_alive():
                write_crash_breadcrumb("hotkey.native_cleanup_timeout", virtual_key=self._main_vk)
            else:
                self._thread = None
