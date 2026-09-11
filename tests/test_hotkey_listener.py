from __future__ import annotations

import unittest
from types import SimpleNamespace


class TestGlobalStartHotkey(unittest.TestCase):
    def test_single_hotkey_vk_only_for_single_keys(self) -> None:
        from pynput import keyboard

        from deepcat.input.hotkey_listener import _single_hotkey_vk

        self.assertEqual(_single_hotkey_vk(keyboard, "<f1>"), 112)
        self.assertIsNone(_single_hotkey_vk(keyboard, "<ctrl>+<shift>+s"))

    def test_single_key_filter_suppresses_and_debounces(self) -> None:
        from deepcat.input import hotkey_listener as hl
        from deepcat.input.hotkey_listener import GlobalStartHotkey

        calls: list[int] = []

        class Suppressed(Exception):
            pass

        class Listener:
            def suppress_event(self) -> None:
                raise Suppressed()

        hotkey = GlobalStartHotkey(lambda: calls.append(1), "<f1>")
        hotkey._listener = Listener()  # type: ignore[assignment]
        # 回调经队列异步分发；测试中改为同步执行以便断言。
        # _modifiers_match 读取真实键盘状态，测试中固定为匹配。
        orig_dispatch = hl._dispatch
        orig_match = hl._modifiers_match
        hl._dispatch = lambda cb: cb()
        hl._modifiers_match = lambda required: True
        try:
            event_filter = hotkey._single_key_filter(112)
            f1 = SimpleNamespace(vkCode=112, flags=0)
            f2 = SimpleNamespace(vkCode=113, flags=0)

            self.assertTrue(event_filter(0x0100, f2))
            with self.assertRaises(Suppressed):
                event_filter(0x0100, f1)
            self.assertEqual(calls, [1])
            with self.assertRaises(Suppressed):
                event_filter(0x0100, f1)
            self.assertEqual(calls, [1])
            with self.assertRaises(Suppressed):
                event_filter(0x0101, f1)
            with self.assertRaises(Suppressed):
                event_filter(0x0100, f1)
            self.assertEqual(calls, [1, 1])
        finally:
            hl._dispatch = orig_dispatch
            hl._modifiers_match = orig_match


if __name__ == "__main__":
    unittest.main()
