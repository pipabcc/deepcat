from __future__ import annotations

from types import SimpleNamespace
import unittest


class TestSelectionTranslateShortcutSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from PyQt6.QtWidgets import QApplication
        except Exception:
            raise unittest.SkipTest("缺少 PyQt6，跳过划词快捷键安全测试")

        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from deepcat.ui.selection_translate import GlobalSelectionTranslateListener

        self.listener = GlobalSelectionTranslateListener()

    def test_physical_ctrl_c_clears_pending_and_passes_through(self) -> None:
        import deepcat.ui.selection_translate as st

        event_filter = self.listener._keyboard_event_filter()
        with self.listener._lock:
            self.listener._pending_until = 999999999.0
            self.listener._pending_pos = (10, 20)

        self.assertTrue(event_filter(0x0100, SimpleNamespace(vkCode=st.VK_LCONTROL, flags=0)))
        self.assertTrue(event_filter(0x0100, SimpleNamespace(vkCode=st.VK_C, flags=0)))

        with self.listener._lock:
            self.assertEqual(self.listener._last_physical_copy_paste_action, "copy")
            self.assertIsNone(self.listener._pending_pos)

    def test_ctrl_a_still_arms_selection_popup_lookup(self) -> None:
        import deepcat.ui.selection_translate as st

        delays: list[int] = []
        self.listener._pendingArmDelayed.connect(lambda delay: delays.append(int(delay)))
        with self.listener._lock:
            self.listener._enabled = True

        event_filter = self.listener._keyboard_event_filter()
        old_control_pressed = self.listener._control_pressed
        self.listener._control_pressed = lambda: True  # type: ignore[method-assign]
        try:
            self.assertTrue(event_filter(0x0100, SimpleNamespace(vkCode=st.VK_LCONTROL, flags=0)))
            self.assertTrue(event_filter(0x0100, SimpleNamespace(vkCode=st.VK_A, flags=0)))
        finally:
            self.listener._control_pressed = old_control_pressed  # type: ignore[method-assign]

        with self.listener._lock:
            self.assertTrue(self.listener._ctrl_a_popup_down)
        self.assertEqual(delays, [80])

    def test_win32_copy_shortcut_skips_when_physical_ctrl_is_down(self) -> None:
        import deepcat.ui.selection_translate as st

        fake_user32 = SimpleNamespace(keybd_event=lambda *args: self.fail("不应发送模拟按键"))
        old_windll = getattr(st.ctypes, "windll", None)
        st.ctypes.windll = SimpleNamespace(user32=fake_user32)  # type: ignore[attr-defined]
        try:
            with self.listener._lock:
                self.listener._physical_down_vks.add(st.VK_LCONTROL)

            self.assertFalse(self.listener._send_copy_shortcut("c", settle=0.01))
        finally:
            if old_windll is None:
                delattr(st.ctypes, "windll")
            else:
                st.ctypes.windll = old_windll  # type: ignore[attr-defined]

    def test_win32_copy_shortcut_does_not_release_physical_ctrl(self) -> None:
        import deepcat.ui.selection_translate as st

        events: list[tuple[int, int]] = []

        def keybd_event(vk: int, _scan: int, flags: int, _extra: int) -> None:
            events.append((int(vk), int(flags)))
            if int(vk) == st.VK_C and int(flags) == 0:
                self.listener._track_physical_key_state(
                    st.VK_LCONTROL,
                    0x0100,
                    {0x0100, 0x0104},
                    {0x0101, 0x0105},
                )

        fake_user32 = SimpleNamespace(keybd_event=keybd_event)
        old_windll = getattr(st.ctypes, "windll", None)
        st.ctypes.windll = SimpleNamespace(user32=fake_user32)  # type: ignore[attr-defined]
        try:
            self.assertTrue(self.listener._send_copy_shortcut("c", settle=0.01))
        finally:
            if old_windll is None:
                delattr(st.ctypes, "windll")
            else:
                st.ctypes.windll = old_windll  # type: ignore[attr-defined]

        self.assertIn((st.VK_CONTROL, 0), events)
        self.assertNotIn((st.VK_CONTROL, st.KEYEVENTF_KEYUP), events)


if __name__ == "__main__":
    unittest.main()
