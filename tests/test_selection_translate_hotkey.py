import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from deepcat.ui.selection_translate import (
    GlobalSelectionTranslateListener,
    suppress_selection_reuse_for_text_input,
)


def _get_app() -> QApplication:
    return QApplication.instance() or QApplication(["deepcat_test"])


class TestSelectionTranslateHotkey(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _get_app()

    def setUp(self):
        self.listener = GlobalSelectionTranslateListener()
        self.listener.set_hotkeys("<ctrl>+<space>", "<ctrl>+b")
        # 钩子测试仅验证事件分发，禁止向当前桌面发送真实的模拟复制。
        self.listener._triggerAccepted.disconnect(self.listener._copy_selection_and_emit)
        key_state = patch.object(self.listener, "_key_down", return_value=False)
        key_state.start()
        self.addCleanup(key_state.stop)

    def tearDown(self):
        self.listener.stop()
        # 清理全局文本输入抑制状态，避免污染其他测试用例。
        import deepcat.ui.selection_translate as st

        st._text_input_suppressed_until = 0.0

    def test_is_modifier_pressed_from_physical_vks(self):
        with self.listener._lock:
            self.listener._physical_down_vks.add(0xA2)
        self.assertTrue(self.listener._is_modifier_pressed(0x11))

        with self.listener._lock:
            self.listener._physical_down_vks.discard(0xA2)
        with patch.object(self.listener, "_key_down", return_value=False):
            with patch("ctypes.windll.user32.GetAsyncKeyState", return_value=0):
                with patch("ctypes.windll.user32.GetKeyState", return_value=0):
                    self.assertFalse(self.listener._is_modifier_pressed(0x11))

    def test_modifiers_match(self):
        with patch.object(self.listener, "_is_modifier_pressed") as mock_pressed:
            mock_pressed.side_effect = lambda mod: mod == 0x11
            self.assertTrue(self.listener._modifiers_match({0x11}))

            mock_pressed.side_effect = lambda mod: mod in {0x11, 0x12}
            self.assertFalse(self.listener._modifiers_match({0x11}))

            mock_pressed.side_effect = lambda mod: False
            self.assertFalse(self.listener._modifiers_match({0x11}))

    def test_suppress_deadlock_recovery_on_physical_release(self):
        self.listener._suppress_trigger_until_release = True
        self.listener._suppress_trigger_vk = 0x42
        self.listener._suppress_trigger_time = time.monotonic()

        filter_func = self.listener._keyboard_event_filter()
        dummy_data = MagicMock()
        # Ctrl 先按下：此时触发主键 0x42 已物理抬起，应解除吞键状态，
        # 否则 release 丢失后主键会被永久吞掉。
        dummy_data.vkCode = 0x11
        dummy_data.flags = 0

        with patch.object(self.listener, "_key_down", return_value=False):
            with patch.object(self.listener, "_modifiers_match", return_value=True):
                self.listener._enabled = True
                filter_func(0x0100, dummy_data)
                self.assertFalse(self.listener._suppress_trigger_until_release)
                self.assertEqual(self.listener._suppress_trigger_vk, 0)

    def test_hotkey_press_bypasses_text_input_suppression_in_filter(self):
        suppress_selection_reuse_for_text_input(1.5)
        self.listener._enabled = True
        self.listener._suppress_trigger_until_release = False
        self.listener._suppress_trigger_vk = 0

        filter_func = self.listener._keyboard_event_filter()
        dummy_data = MagicMock()
        dummy_data.vkCode = 0x42
        dummy_data.flags = 0

        with patch.object(self.listener, "_key_down", return_value=False):
            with patch.object(self.listener, "_modifiers_match", return_value=True):
                filter_func(0x0100, dummy_data)

        self.assertTrue(self.listener._suppress_trigger_until_release)
        self.assertEqual(self.listener._suppress_trigger_vk, 0x42)

    def test_explicit_hotkey_bypasses_text_input_suppression(self):
        suppress_selection_reuse_for_text_input(1.5)

        received = []
        self.listener.translateRequested.connect(lambda text, x, y, act: received.append((text, act)))
        self.listener._enabled = True

        with patch.object(self.listener, "_copy_selected_text", return_value="hello world"):
            self.listener._copy_selection_and_emit(100, 100, action="translate")

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0], ("hello world", "translate"))

    def test_send_copy_shortcut_with_physical_ctrl_down(self):
        with patch.object(self.listener, "_physical_ctrl_key_down", return_value=True):
            with patch.object(self.listener, "_physical_copy_paste_active_or_recent", return_value=False):
                with patch("ctypes.windll.user32.keybd_event") as mock_keybd:
                    result = self.listener._send_copy_shortcut("c", settle=0.01, is_hotkey=True)
                    self.assertTrue(result)
                    self.assertTrue(mock_keybd.called)
                    self.assertEqual(
                        [call.args for call in mock_keybd.call_args_list],
                        [(0x43, 0, 0, 0), (0x43, 0, 0x0002, 0)],
                    )

    def test_both_hotkeys_use_physical_modifiers_during_input_suppression(self):
        suppress_selection_reuse_for_text_input(1.5)
        self.listener._enabled = True
        accepted = []
        self.listener._triggerAccepted.connect(lambda x, y, action: accepted.append(action))
        event_filter = self.listener._keyboard_event_filter()

        with patch.object(self.listener, "_cursor_pos", return_value=(100, 100)):
            event_filter(0x0100, SimpleNamespace(vkCode=0xA3, flags=0))
            for main_key in (0x20, 0x42):
                self.assertFalse(event_filter(0x0100, SimpleNamespace(vkCode=main_key, flags=0)))
                self.assertFalse(event_filter(0x0101, SimpleNamespace(vkCode=main_key, flags=0)))

        self.assertEqual(accepted, ["translate", "manual"])

    def test_held_hotkey_does_not_repeat_during_input_suppression(self):
        suppress_selection_reuse_for_text_input(1.5)
        self.listener._enabled = True
        accepted = []
        self.listener._triggerAccepted.connect(lambda x, y, action: accepted.append(action))
        event_filter = self.listener._keyboard_event_filter()
        event_filter(0x0100, SimpleNamespace(vkCode=0xA2, flags=0))

        with patch.object(self.listener, "_cursor_pos", return_value=(100, 100)):
            event_filter(0x0100, SimpleNamespace(vkCode=0x42, flags=0))
            with patch.object(self.listener, "_key_down", side_effect=lambda vk: vk in {0x11, 0x42}):
                self.listener._poll_input_state()
                event_filter(0x0100, SimpleNamespace(vkCode=0x42, flags=0))
            event_filter(0x0101, SimpleNamespace(vkCode=0x42, flags=0))
            event_filter(0x0100, SimpleNamespace(vkCode=0x42, flags=0))

        self.assertEqual(accepted, ["manual", "manual"])

    def test_automatic_popup_remains_suppressed_while_editing(self):
        suppress_selection_reuse_for_text_input(1.5)
        self.listener._enabled = True
        with patch.object(self.listener, "_copy_selected_text") as copy_text:
            self.listener._copy_selection_and_emit(100, 100, action="popup_detect")
        copy_text.assert_not_called()

    def test_manual_hotkey_can_follow_a_completed_copy(self):
        self.listener._enabled = True
        self.listener._last_physical_copy_paste_time = time.monotonic()
        received = []
        self.listener.translateRequested.connect(lambda text, x, y, action: received.append((text, action)))
        with patch.object(self.listener, "_copy_selected_text", return_value="新选中文字") as copy_text:
            self.listener._copy_selection_and_emit(100, 100, action="manual")
        copy_text.assert_called_once_with(fast=False, is_hotkey=True)
        self.assertEqual(received, [("新选中文字", "manual")])

    def test_active_physical_copy_or_paste_prevents_synthetic_copy(self):
        with patch("ctypes.windll.user32.keybd_event") as keybd:
            for key_vk in (0x43, 0x56):
                with self.subTest(key_vk=key_vk):
                    with self.listener._lock:
                        self.listener._physical_down_vks = {0xA2, key_vk}
                    self.assertFalse(self.listener._send_copy_shortcut("c", is_hotkey=True))
        keybd.assert_not_called()

    def test_physical_copy_still_cancels_popup_during_input_suppression(self):
        suppress_selection_reuse_for_text_input(1.5)
        cancelled = []
        self.listener.forceSelectionCancelled.connect(lambda: cancelled.append(True))
        event_filter = self.listener._keyboard_event_filter()
        event_filter(0x0100, SimpleNamespace(vkCode=0xA2, flags=0))
        self.assertTrue(event_filter(0x0100, SimpleNamespace(vkCode=0x43, flags=0)))
        self.assertEqual(cancelled, [True])
        self.assertEqual(self.listener._last_physical_copy_paste_action, "copy")

    def test_pynput_copy_reuses_ctrl_held_for_hotkey(self):
        with patch.object(self.listener, "_physical_ctrl_key_down", return_value=True):
            with patch("pynput.keyboard.Controller") as controller_type:
                self.assertTrue(self.listener._send_pynput_copy_shortcut("c", settle=0.01, is_hotkey=True))
        controller = controller_type.return_value
        controller.press.assert_called_once_with("c")
        controller.release.assert_called_once_with("c")

    def test_hotkey_copies_selection_when_ctrl_is_still_held(self):
        suppress_selection_reuse_for_text_input(1.5)
        self.listener._enabled = True
        self.listener._physical_down_vks.add(0xA2)
        received = []
        self.listener.translateRequested.connect(lambda text, x, y, action: received.append((text, action)))
        clipboard = SimpleNamespace(mimeData=lambda: None)

        for action in ("translate", "manual"):
            with self.subTest(action=action):
                with (
                    patch("deepcat.ui.selection_translate.QGuiApplication.clipboard", return_value=clipboard),
                    patch.object(self.listener, "_selected_text_from_qt_focus", return_value=""),
                    patch.object(self.listener, "_clipboard_sequence_number", return_value=123),
                    patch.object(self.listener, "_send_wm_copy"),
                    patch.object(self.listener, "_wait_for_clipboard_text", side_effect=["", "选中文本"]),
                    patch("ctypes.windll.user32.keybd_event") as keybd,
                ):
                    self.listener._copy_selection_and_emit(100, 100, action=action)
                self.assertEqual([call.args[0] for call in keybd.call_args_list], [0x43, 0x43])

        self.assertEqual(received, [("选中文本", "translate"), ("选中文本", "manual")])

    def test_suspended_listener_does_not_copy_even_for_explicit_hotkey(self):
        self.listener._enabled = True
        self.listener.set_suspended(True)
        with patch.object(self.listener, "_copy_selected_text") as copy_text:
            self.listener._copy_selection_and_emit(100, 100, action="manual")
        copy_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
