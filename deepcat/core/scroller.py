"""
滚动控制模块。

算法参考说明：
- scroll_down / scroll_to_top 的多滚动方式（滚轮、方向键、PageDown、Win32 ScrollMessage）
  设计参考自 ShareX 开源项目：https://github.com/ShareX/ShareX
- 参考文件：ShareX.ScreenCaptureLib/ScrollingCaptureManager.cs（Scroll 方法）
- ShareX 许可证：GNU General Public License v3.0 (GPLv3)
"""

from __future__ import annotations

import time


def scroll_down(amount: int = -5, delay: float = 0.4, method: str = "mouse_wheel") -> None:
    """
    执行一次向下滚动并等待渲染。

    method:
      - mouse_wheel: pyautogui.scroll（默认）
      - down_arrow:  发送 Down 键
      - page_down:   发送 PageDown 键
      - scroll_message: Win32 SendMessage WM_VSCROLL
    """
    if method == "mouse_wheel":
        try:
            import pyautogui
            pyautogui.PAUSE = 0.0
        except Exception as e:
            raise RuntimeError("未安装 pyautogui，请先 pip install pyautogui") from e
        pyautogui.scroll(int(amount))

    elif method == "down_arrow":
        _send_key_press("down", abs(int(amount)))

    elif method == "page_down":
        _send_key_press("pagedown", 1)

    elif method == "scroll_message":
        _send_scroll_message(abs(int(amount)))

    else:
        # 默认回退到鼠标滚轮
        try:
            import pyautogui
            pyautogui.PAUSE = 0.0
        except Exception as e:
            raise RuntimeError("未安装 pyautogui，请先 pip install pyautogui") from e
        pyautogui.scroll(int(amount))

    time.sleep(max(0.0, float(delay)))


def scroll_to_top(method: str = "home_key", delay: float = 0.3) -> None:
    """滚动到顶部（截图前先把目标区域回顶部）。"""
    if method == "home_key":
        _send_key_press("home", 1)
    elif method == "scroll_message":
        _send_scroll_message_to_top()
    time.sleep(max(0.0, float(delay)))


def _send_key_press(key: str, count: int = 1) -> None:
    """发送键盘按键，优先使用 pyautogui，回退到 pynput。"""
    key_lower = key.lower()

    # 尝试 pyautogui（更轻量，通常已安装）
    try:
        import pyautogui

        pg_map = {
            "down": "down",
            "pagedown": "pagedown",
            "home": "home",
        }
        pg_key = pg_map.get(key_lower)
        if pg_key:
            for _ in range(int(count)):
                pyautogui.press(pg_key)
                time.sleep(0.01)
            return
    except Exception:
        pass

    # 回退到 pynput
    try:
        from pynput.keyboard import Controller, Key
    except Exception as e:
        raise RuntimeError("未安装键盘控制库（pyautogui / pynput）") from e

    kb = Controller()
    key_map = {
        "down": Key.down,
        "pagedown": Key.page_down,
        "home": Key.home,
    }
    key_obj = key_map.get(key_lower)
    if key_obj is None:
        raise ValueError(f"不支持的按键: {key}")

    for _ in range(int(count)):
        kb.press(key_obj)
        kb.release(key_obj)
        time.sleep(0.01)


_SMTO_ABORTIFHUNG = 0x0002
_SCROLL_SEND_TIMEOUT_MS = 600


def _send_scroll_message(lines: int = 1) -> None:
    """通过 Win32 SendMessageTimeoutW 向鼠标下方窗口发送垂直滚动消息。

    失败不再静默吞掉：抛出 RuntimeError 供上层计数并显式停止，
    避免把滚动失败误判成"已到达底部"；SMTO_ABORTIFHUNG 保证
    目标窗口挂起时立即返回而不是无限阻塞工作线程。
    """
    try:
        import ctypes
        from ctypes import wintypes

        WM_VSCROLL = 0x0115
        SB_LINEDOWN = 1

        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            raise RuntimeError("滚动失败：GetCursorPos 无法获取鼠标位置")
        hwnd = ctypes.windll.user32.WindowFromPoint(point)
        if not hwnd:
            raise RuntimeError("滚动失败：鼠标下方没有可接收滚动消息的窗口")

        for _ in range(int(lines)):
            result = wintypes.LRESULT(0)
            sent = ctypes.windll.user32.SendMessageTimeoutW(
                wintypes.HWND(hwnd),
                wintypes.UINT(WM_VSCROLL),
                wintypes.WPARAM(SB_LINEDOWN),
                wintypes.LPARAM(0),
                wintypes.UINT(_SMTO_ABORTIFHUNG),
                wintypes.UINT(_SCROLL_SEND_TIMEOUT_MS),
                ctypes.byref(result),
            )
            if not sent:
                raise RuntimeError("滚动失败：目标窗口挂起或未响应 WM_VSCROLL")
            time.sleep(0.005)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"滚动消息发送异常: {e}") from e


def _send_scroll_message_to_top() -> None:
    try:
        import ctypes
        from ctypes import wintypes

        WM_VSCROLL = 0x0115
        SB_TOP = 6

        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            raise RuntimeError("回顶失败：GetCursorPos 无法获取鼠标位置")
        hwnd = ctypes.windll.user32.WindowFromPoint(point)
        if not hwnd:
            raise RuntimeError("回顶失败：鼠标下方没有可接收滚动消息的窗口")

        result = wintypes.LRESULT(0)
        sent = ctypes.windll.user32.SendMessageTimeoutW(
            wintypes.HWND(hwnd),
            wintypes.UINT(WM_VSCROLL),
            wintypes.WPARAM(SB_TOP),
            wintypes.LPARAM(0),
            wintypes.UINT(_SMTO_ABORTIFHUNG),
            wintypes.UINT(_SCROLL_SEND_TIMEOUT_MS),
            ctypes.byref(result),
        )
        if not sent:
            raise RuntimeError("回顶失败：目标窗口挂起或未响应 WM_VSCROLL")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"回顶消息发送异常: {e}") from e
