"""系统热键注册、冲突反馈及修改失败时保留原绑定。"""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pynput import keyboard

from deepcat.input.hotkey_listener import GlobalStartHotkey, _parse_hotkey
from deepcat.input.windows_hotkey import MOD_NOREPEAT, WM_HOTKEY, WindowsHotkey
from deepcat.ui.main_window.capture import CaptureMixin
from deepcat.ui.main_window.later_read import LaterReadMixin
from deepcat.ui.main_window.window_embedded_settings import WindowEmbeddedSettingsMixin
from deepcat.ui.main_window.window_shell import WindowShellMixin


@pytest.mark.parametrize(
    "hotkey,modifiers,vk",
    [
        ("<f1>", set(), 0x70),
        ("<f2>", set(), 0x71),
        ("<f3>", set(), 0x72),
        ("<alt>+<space>", {0x12}, 0x20),
        ("<ctrl_l>+b", {0x11}, 0x42),
        ("<alt_r>+<space>", {0x12}, 0x20),
    ],
)
def test_windows_hotkeys_use_system_registration(hotkey, modifiers, vk):
    assert _parse_hotkey(keyboard, hotkey) == (modifiers, vk)
    listener = GlobalStartHotkey(lambda: None, hotkey)
    with (
        patch("deepcat.input.hotkey_listener.os.name", "nt"),
        patch("deepcat.input.windows_hotkey.WindowsHotkey") as native_type,
    ):
        listener.start()
        listener.start()
        native_type.assert_called_once()
        assert native_type.call_args.args[:2] == (vk, modifiers)
        native_type.return_value.start.assert_called_once()
        listener.cleanup()
        native_type.return_value.cleanup.assert_called_once()


@pytest.mark.parametrize("hotkey", ["<ctrl>", "a+b", "invalid key"])
def test_invalid_hotkeys_fail_instead_of_installing_a_different_binding(hotkey):
    listener = GlobalStartHotkey(lambda: None, hotkey)
    with pytest.raises(ValueError):
        listener.start()


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 支持系统热键消息")
def test_native_registration_reports_conflicts_and_releases_binding():
    fired = threading.Event()
    first = WindowsHotkey(0x87, {0x11, 0x12, 0x10}, fired.set)
    second = WindowsHotkey(0x87, {0x11, 0x12, 0x10}, lambda: None)
    try:
        try:
            first.start()
        except RuntimeError as error:
            if "占用" in str(error):
                pytest.skip("测试用 Ctrl+Alt+Shift+F24 已被外部程序占用")
            raise
        assert first._modifiers & MOD_NOREPEAT
        with pytest.raises(RuntimeError, match="占用"):
            second.start()
        # 只向测试线程投递消息，不向用户正在使用的程序发送按键。
        assert first._user32.PostThreadMessageW(first._thread_id, WM_HOTKEY, 1, 0)
        assert fired.wait(1.0)
        first.cleanup()
        assert first._thread is None
        second.start()
        second.cleanup()
        assert second._thread is None
    finally:
        first.cleanup()
        second.cleanup()


@pytest.mark.parametrize(
    "method,attribute,key_attribute,old_key,new_key",
    [
        (
            WindowEmbeddedSettingsMixin._change_start_hotkey_from_settings,
            "_start_hotkey",
            "_start_hotkey_str",
            "<f1>",
            "<f4>",
        ),
        (CaptureMixin._change_scroll_hotkey_from_settings, "_scroll_hotkey", "_scroll_hotkey_str", "<f2>", "<f5>"),
        (
            LaterReadMixin._change_later_read_hotkey_from_settings,
            "_later_read_hotkey",
            "_later_read_hotkey_str",
            "<f3>",
            "<f6>",
        ),
    ],
)
def test_failed_shortcut_change_keeps_old_binding(method, attribute, key_attribute, old_key, new_key):
    previous = MagicMock()
    owner = SimpleNamespace(
        _start_hotkey_triggered=MagicMock(),
        _scroll_hotkey_triggered=MagicMock(),
        _later_read_hotkey_signal=MagicMock(),
        _refresh_hotkey_label=MagicMock(),
    )
    setattr(owner, attribute, previous)
    setattr(owner, key_attribute, old_key)
    owner._replace_global_hotkey = lambda *args: WindowShellMixin._replace_global_hotkey(owner, *args)
    with patch("deepcat.ui.main_window.window_shell.GlobalStartHotkey") as factory:
        factory.return_value.start.side_effect = RuntimeError("快捷键被占用")
        ok, message = method(owner, new_key)
    assert not ok and "占用" in message
    assert getattr(owner, attribute) is previous
    assert getattr(owner, key_attribute) == old_key
    previous.cleanup.assert_not_called()


def test_new_binding_is_ready_before_old_binding_is_released():
    operations = []
    previous = MagicMock()
    previous.cleanup.side_effect = lambda: operations.append("旧绑定关闭")
    owner = SimpleNamespace(_shortcut=previous, _shortcut_text="<f1>")
    with patch("deepcat.ui.main_window.window_shell.GlobalStartHotkey") as factory:
        factory.return_value.start.side_effect = lambda: operations.append("新绑定就绪")
        assert WindowShellMixin._replace_global_hotkey(owner, "_shortcut", "_shortcut_text", lambda: None, "<f4>") == (
            True,
            "",
        )
    assert operations == ["新绑定就绪", "旧绑定关闭"]
    assert owner._shortcut_text == "<f4>"


def test_ai_hotkey_failure_does_not_save_settings_or_report_success():
    owner = SimpleNamespace(
        _current=SimpleNamespace(ui={"ai_qa_hotkey": "<alt>+<space>"}),
        _ai_qa_hotkey_edit=MagicMock(),
        _ai_qa_hotkey_signal=MagicMock(),
        _replace_global_hotkey=MagicMock(return_value=(False, "已被占用")),
        _show_capture_status=MagicMock(),
        _sync_settings_after_change=MagicMock(),
    )
    owner._ai_qa_hotkey_edit.keySequence.return_value.toString.return_value = "Alt+F6"
    with patch("deepcat.ui.main_window.window_embedded_settings.update_ui_settings") as save:
        WindowEmbeddedSettingsMixin._apply_ai_qa_hotkey(owner)
    save.assert_not_called()
    owner._sync_settings_after_change.assert_not_called()
    assert owner._show_capture_status.call_args.kwargs["tone"] == "error"


def test_startup_reports_failed_binding_and_keeps_other_hotkeys_working():
    owner = SimpleNamespace(
        _start_hotkey_str="<f1>",
        _scroll_hotkey_str="<f2>",
        _later_read_hotkey_str="<f3>",
        _ai_qa_hotkey_str="<alt>+<space>",
        _start_hotkey_triggered=MagicMock(),
        _scroll_hotkey_triggered=MagicMock(),
        _show_general_status=MagicMock(),
    )
    for name in ("todo", "later_read", "ai_qa", "pinned", "note_float", "clipboard_float", "later_read_float"):
        setattr(owner, f"_{name}_hotkey_signal", MagicMock())

    def make_listener(callback, hotkey):
        listener = MagicMock()
        if hotkey == "<f1>":
            listener.start.side_effect = RuntimeError("快捷键已被占用")
        return listener

    with patch("deepcat.ui.main_window.window_shell.GlobalStartHotkey", side_effect=make_listener):
        WindowShellMixin._start_global_hotkeys(owner)
    assert getattr(owner, "_start_hotkey", None) is None
    assert owner._scroll_hotkey is not None and owner._ai_qa_hotkey is not None
    assert "框选截图" in owner._show_general_status.call_args.args[0]
    assert owner._show_general_status.call_args.kwargs["tone"] == "error"


def test_native_conflict_falls_back_to_keyboard_hook_with_suppression():
    """当系统原生热键被外部程序占用时，自动回退到低级键盘钩子，优先本软件并抑制外部事件。"""
    calls = []
    listener = GlobalStartHotkey(lambda: calls.append("triggered"), "<f3>")

    mock_native = MagicMock()
    mock_native.start.side_effect = RuntimeError("该快捷键已被其他功能或程序占用，请选择其他组合")

    mock_keyboard_listener = MagicMock()

    with (
        patch("deepcat.input.hotkey_listener.os.name", "nt"),
        patch("deepcat.input.windows_hotkey.WindowsHotkey", return_value=mock_native),
        patch("pynput.keyboard.Listener", return_value=mock_keyboard_listener) as mock_listener_cls,
    ):
        listener.start()
        # 验证尝试了 Windows 原生热键注册
        mock_native.start.assert_called_once()
        mock_native.cleanup.assert_called_once()
        assert listener._native_hotkey is None

        # 验证自动切换为键盘钩子
        mock_listener_cls.assert_called_once()
        mock_keyboard_listener.start.assert_called_once()
        assert listener._listener is mock_keyboard_listener

        # 提取传入的 event_filter，验证对 F3 按键的拦截与抑制逻辑
        filter_fn = mock_listener_cls.call_args.kwargs["win32_event_filter"]
        f3_data = SimpleNamespace(vkCode=0x72, flags=0)

        # 模拟工作队列同步执行以方便断言
        from deepcat.input import hotkey_listener as hl
        orig_dispatch = hl._dispatch
        orig_match = hl._modifiers_match
        hl._dispatch = lambda cb: cb()
        hl._modifiers_match = lambda req: True
        try:
            # 首次按下 F3 (WM_KEYDOWN 0x0100)
            res = filter_fn(0x0100, f3_data)
            assert res is False
            assert calls == ["triggered"]
            mock_keyboard_listener.suppress_event.assert_called()

            # 抬起 F3 (WM_KEYUP 0x0101) 同样被抑制
            res_up = filter_fn(0x0101, f3_data)
            assert res_up is False
        finally:
            hl._dispatch = orig_dispatch
            hl._modifiers_match = orig_match

        # 验证清理
        listener.cleanup()
        mock_keyboard_listener.stop.assert_called_once()
