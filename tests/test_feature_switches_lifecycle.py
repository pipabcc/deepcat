import os
import sys
import unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QTimer, QObject
from PyQt6.QtWidgets import QApplication, QCheckBox
from PyQt6.QtGui import QAction

from deepcat.ui.main_window.window_embedded_settings import WindowEmbeddedSettingsMixin
from deepcat.ui.main_window.later_read import LaterReadMixin
from deepcat.ui.main_window.notes import NotesMixin
from deepcat.ui.main_window.tray import TrayMixin
from deepcat.ui.main_window.window_search import WindowSearchMixin


def _get_app() -> QApplication:
    return QApplication.instance() or QApplication(["deepcat_test"])


class DummyMainWindow(
    WindowEmbeddedSettingsMixin,
    LaterReadMixin,
    NotesMixin,
    TrayMixin,
    WindowSearchMixin,
    QObject,
):
    def __init__(self):
        super().__init__()
        self._current = MagicMock()
        self._app_settings = self._current
        self._feature_visibility = {
            "todo": True,
            "later_read": True,
            "clipboard_history": True,
            "table_notes": True,
        }

        self._feature_todo_switch = QCheckBox()
        self._feature_later_read_switch = QCheckBox()
        self._feature_clipboard_history_switch = QCheckBox()
        self._feature_table_notes_switch = QCheckBox()

        self._feature_todo_switch.setChecked(True)
        self._feature_later_read_switch.setChecked(True)
        self._feature_clipboard_history_switch.setChecked(True)
        self._feature_table_notes_switch.setChecked(True)

        self._cat_reminder_timer = QTimer(self)
        self._todo_timer = QTimer(self)
        self._todo_timer.setInterval(30000)
        self._todo_timer.start()

        self._todo_popup = MagicMock()
        self._cat_pre_popup = MagicMock()
        self._cat_reminder_session = MagicMock()
        self._in_pre_notify_stage = True

        self._todo_list = MagicMock()
        self._later_read_list = MagicMock()
        self._table_notes_store = MagicMock()
        self._table_notes_save_timer = QTimer(self)
        self._table_notes_save_timer.start(350)
        self._pinned_tab_windows = [MagicMock(), MagicMock()]

        self._compact_window_clipboard = MagicMock()
        self._compact_window_later_read = MagicMock()

        self._stack = MagicMock()
        self._stack.currentIndex.return_value = 0

        self._tray_new_todo_action = QAction("新建待办", self)
        self._tray_later_read_action = QAction("稍后阅读", self)
        self._tray_clipboard_action = QAction("复制记录", self)
        self._tray_notes_action = QAction("表格记事", self)
        self._tray_note_float_action = QAction("便签", self)
        self._tray_clipboard_float_action = QAction("复制记录悬浮窗", self)
        self._tray_later_read_float_action = QAction("稍后阅读悬浮窗", self)
        self._tray_show_action = QAction("开始截图", self)
        self._tray_ai_qa_action = QAction("AI对话", self)

        self._switched_pages = []

    def _feature_enabled(self, key: str) -> bool:
        return bool(self._feature_visibility.get(str(key), True))

    def _format_hotkey_for_tray(self, s: str) -> str:
        return str(s or "")

    def _sync_settings_after_change(self) -> None:
        self._app_settings = self._current

    def _apply_feature_visibility_to_nav(self) -> None:
        pass

    def _schedule_cat_reminder(self) -> None:
        self._cat_reminder_scheduled = True

    def _schedule_todo_checks(self) -> None:
        self._todo_timer.start()
        self._todo_checks_scheduled = True

    def _refresh_todo_list(self) -> None:
        self._todo_list_refreshed = True

    def _close_later_read_probe_overlay(self, *, restore_cursor: bool = True) -> None:
        self._probe_overlay_closed = True

    def _refresh_later_read_list(self) -> None:
        self._later_read_list_refreshed = True

    def _save_table_notes_settings(self) -> None:
        self._table_notes_saved = True

    def _load_table_notes_settings(self) -> None:
        self._table_notes_loaded = True

    def _switch_page(self, index: int) -> None:
        self._switched_pages.append(index)

    def _is_tray_menu_click_throttled(self) -> bool:
        return False

    def _show_from_tray(self) -> None:
        pass

    def _later_read_enabled_now(self) -> bool:
        return True


class FeatureSwitchesLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_todo_switch_disable_and_reopen_lifecycle(self):
        win = DummyMainWindow()
        self.assertTrue(win._todo_timer.isActive())

        # 1. 关闭 todo 功能
        win._feature_todo_switch.setChecked(False)
        win._feature_visibility["todo"] = False
        win._apply_feature_visibility()

        self.assertFalse(win._todo_timer.isActive())
        self.assertIsNone(win._todo_popup)
        self.assertIsNone(win._cat_pre_popup)
        self.assertIsNone(win._cat_reminder_session)
        self.assertFalse(win._in_pre_notify_stage)

        # 2. 重新开启 todo 功能
        win._feature_todo_switch.setChecked(True)
        win._feature_visibility["todo"] = True
        win._apply_feature_visibility()

        # 必须恢复启动定时器与检查调度
        self.assertTrue(win._todo_timer.isActive())
        self.assertTrue(getattr(win, "_todo_checks_scheduled", False))
        self.assertTrue(getattr(win, "_todo_list_refreshed", False))

    def test_later_read_switch_lifecycle(self):
        win = DummyMainWindow()

        # 关闭稍后阅读
        win._feature_later_read_switch.setChecked(False)
        win._feature_visibility["later_read"] = False
        win._apply_feature_visibility()

        self.assertTrue(getattr(win, "_probe_overlay_closed", False))
        win._compact_window_later_read.close.assert_called_once()

        # 重新开启
        win._feature_later_read_switch.setChecked(True)
        win._feature_visibility["later_read"] = True
        win._apply_feature_visibility()

        self.assertTrue(getattr(win, "_later_read_list_refreshed", False))

    def test_table_notes_switch_lifecycle(self):
        win = DummyMainWindow()
        old_pinned = list(win._pinned_tab_windows)

        # 关闭表格记事
        win._feature_table_notes_switch.setChecked(False)
        win._feature_visibility["table_notes"] = False
        win._apply_feature_visibility()

        self.assertTrue(getattr(win, "_table_notes_saved", False))
        self.assertFalse(win._table_notes_save_timer.isActive())
        for p in old_pinned:
            p.close.assert_called_once()
        self.assertEqual(len(win._pinned_tab_windows), 0)

        # 重新开启
        win._feature_table_notes_switch.setChecked(True)
        win._feature_visibility["table_notes"] = True
        win._apply_feature_visibility()

        self.assertTrue(getattr(win, "_table_notes_loaded", False))

    def test_tray_menu_floating_actions_visibility_sync(self):
        win = DummyMainWindow()

        # 初始全开启状态
        win._refresh_tray_later_read_action()
        self.assertTrue(win._tray_note_float_action.isVisible())
        self.assertTrue(win._tray_clipboard_float_action.isVisible())
        self.assertTrue(win._tray_later_read_float_action.isVisible())

        # 关闭 table_notes 和 clipboard_history
        win._feature_visibility["table_notes"] = False
        win._feature_visibility["clipboard_history"] = False
        win._refresh_tray_later_read_action()

        self.assertFalse(win._tray_note_float_action.isVisible())
        self.assertFalse(win._tray_clipboard_float_action.isVisible())
        self.assertTrue(win._tray_later_read_float_action.isVisible())

        # 重新开启 table_notes
        win._feature_visibility["table_notes"] = True
        win._refresh_tray_later_read_action()
        self.assertTrue(win._tray_note_float_action.isVisible())

    def test_guards_when_feature_disabled(self):
        win = DummyMainWindow()

        # 禁用 clipboard_history
        win._feature_visibility["clipboard_history"] = False
        win._open_clipboard_from_tray()
        self.assertNotIn(4, win._switched_pages)

        # 禁用 table_notes
        win._feature_visibility["table_notes"] = False
        win._open_table_notes_from_tray()
        self.assertNotIn(5, win._switched_pages)

        with patch.object(win, "_create_or_get_float_note_index") as mock_create:
            win._trigger_note_float()
            mock_create.assert_not_called()

        # 悬浮窗打开保护
        with patch("deepcat.ui.main_window.compact._CompactListWindow") as mock_compact_cls:
            win._open_compact_list_window("clipboard")
            mock_compact_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
