import os
import sys
import unittest
from pathlib import Path
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from deepcat.settings_store import (
    COPY_ON_CAPTURE_MODE_OFF,
    COPY_ON_CAPTURE_MODE_COPY_CLOSE,
    COPY_ON_CAPTURE_MODE_COPY_KEEP,
)
from deepcat.ui.main_window.capture import CaptureMixin
from deepcat.ui.main_window.window_embedded_settings import WindowEmbeddedSettingsMixin
from deepcat.ui.main_window.window_navigation import WindowNavigationMixin
from PyQt6.QtCore import QObject


class DummyCaptureWindow(CaptureMixin, WindowEmbeddedSettingsMixin, WindowNavigationMixin, QObject):
    def __init__(self):
        super().__init__()
        self._style_sheet = ""

    def setStyleSheet(self, sheet: str) -> None:
        self._style_sheet = sheet


class UICaptureOptionsAndStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_copy_on_capture_options_display_text(self):
        from deepcat.ui.post_capture_actions.combos import ModernPopupComboBox

        combo = ModernPopupComboBox()
        combo.addItem("不自动复制", COPY_ON_CAPTURE_MODE_OFF)
        combo.addItem("复制后关闭", COPY_ON_CAPTURE_MODE_COPY_CLOSE)
        combo.addItem("复制后保留", COPY_ON_CAPTURE_MODE_COPY_KEEP)

        self.assertEqual(combo.count(), 3)
        self.assertEqual(combo.itemText(0), "不自动复制")
        self.assertEqual(combo.itemData(0), COPY_ON_CAPTURE_MODE_OFF)
        self.assertEqual(combo.itemText(1), "复制后关闭")
        self.assertEqual(combo.itemData(1), COPY_ON_CAPTURE_MODE_COPY_CLOSE)
        self.assertEqual(combo.itemText(2), "复制后保留")
        self.assertEqual(combo.itemData(2), COPY_ON_CAPTURE_MODE_COPY_KEEP)

    def test_checkbox_disabled_style_and_svg_asset(self):
        win = DummyCaptureWindow()
        win._style_main_window()

        # 验证样式表中包含 disabled 与 checked:disabled 的 indicator 规则
        self.assertIn("QCheckBox::indicator:disabled", win._style_sheet)
        self.assertIn("QCheckBox::indicator:checked:disabled", win._style_sheet)
        self.assertNotIn("__CHECKBOX_DISABLED_ICON__", win._style_sheet)

        # 验证 SVG 图标存在且包含 #9CA3AF 灰色
        svg_path = win._assets_dir() / "icon_checkbox_check_disabled.svg"
        self.assertTrue(svg_path.exists(), f"SVG not found at {svg_path}")
        svg_content = svg_path.read_text(encoding="utf-8")
        self.assertIn("#9CA3AF", svg_content.upper())

    def test_scroll_speed_ui_hidden_and_default_delay(self):
        from deepcat.config import Config
        from deepcat.ui import main_window as mw

        # 1. 验证全局配置默认 SCROLL_DELAY 为 0.03
        cfg = Config()
        self.assertEqual(cfg.SCROLL_DELAY, 0.03)

        # 2. 验证 MainWindow 初始化后 speed_row 是隐藏的，且 cfg.SCROLL_DELAY 为 0.03
        dummy_tray = type("DummyTray", (), {"setToolTip": lambda *_: None, "setIcon": lambda *_: None})()
        orig_init_tray = mw.MainWindow._init_tray
        orig_deferred = mw.MainWindow._start_deferred_startup_services
        orig_close_event = mw.MainWindow.closeEvent
        mw.MainWindow._init_tray = lambda self: dummy_tray
        mw.MainWindow._start_deferred_startup_services = lambda self: None
        mw.MainWindow.closeEvent = lambda self, e: e.accept()
        try:
            window = mw.MainWindow()
            self.assertTrue(hasattr(window, "_speed_row"))
            self.assertTrue(window._speed_row.isHidden())
            self.assertEqual(round(window._cfg.SCROLL_DELAY, 2), 0.03)
            window.close()
        finally:
            mw.MainWindow._init_tray = orig_init_tray
            mw.MainWindow._start_deferred_startup_services = orig_deferred
            mw.MainWindow.closeEvent = orig_close_event

    def test_combo_styles_do_not_take_ownership_of_application_style(self):
        from PyQt6.QtWidgets import QComboBox
        from PyQt6 import sip
        from deepcat.ui.main_window.misc_widgets import _ModernComboStyle

        application_style = self.app.style()
        original_parent = application_style.parent()
        first_combo, second_combo = QComboBox(), QComboBox()
        try:
            first_style = _ModernComboStyle(first_combo)
            second_style = _ModernComboStyle(second_combo)
            first_combo.setStyle(first_style)
            second_combo.setStyle(second_style)
            self.assertIsNot(first_style.baseStyle(), application_style)
            self.assertIsNot(second_style.baseStyle(), application_style)
            self.assertIsNot(first_style.baseStyle(), second_style.baseStyle())
            self.assertIs(application_style.parent(), original_parent)
        finally:
            sip.delete(first_combo)
            sip.delete(second_combo)
        self.assertFalse(sip.isdeleted(application_style))

    def test_capture_settings_layout_alignment_and_auto_snap_position(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QLabel
        from deepcat.ui import main_window as mw

        dummy_tray = type("DummyTray", (), {"setToolTip": lambda *_: None, "setIcon": lambda *_: None})()
        orig_init_tray = mw.MainWindow._init_tray
        orig_deferred = mw.MainWindow._start_deferred_startup_services
        orig_close_event = mw.MainWindow.closeEvent
        mw.MainWindow._init_tray = lambda self: dummy_tray
        mw.MainWindow._start_deferred_startup_services = lambda self: None
        mw.MainWindow.closeEvent = lambda self, e: e.accept()
        try:
            window = mw.MainWindow()
            top_controls = window._capture_top_controls
            top_grid = top_controls.layout()

            # 1. 验证 mode_label（截图模式:）为左对齐
            mode_label_item = top_grid.itemAtPosition(0, 0)
            self.assertIsNotNone(mode_label_item)
            mode_label = mode_label_item.widget()
            self.assertIsInstance(mode_label, QLabel)
            self.assertEqual(mode_label.text(), "截图模式:")
            self.assertTrue(bool(mode_label.alignment() & Qt.AlignmentFlag.AlignLeft))

            # 2. 验证第二行从第 0 列开始放置（左对齐），横跨 6 列
            second_row_item = top_grid.itemAtPosition(1, 0)
            self.assertIsNotNone(second_row_item)
            scroll_opts_layout = second_row_item.layout()
            self.assertIsNotNone(scroll_opts_layout)

            # 3. 验证自动吸附位于反向滚动之后
            widgets_in_row = []
            for i in range(scroll_opts_layout.count()):
                w = scroll_opts_layout.itemAt(i).widget()
                if w is not None:
                    widgets_in_row.append(w)

            self.assertIn(window._adaptive_wait, widgets_in_row)
            self.assertIn(window._boost_scroll, widgets_in_row)
            self.assertIn(window._reverse_scroll, widgets_in_row)
            self.assertIn(window._auto_snap_enabled, widgets_in_row)
            rev_idx = widgets_in_row.index(window._reverse_scroll)
            snap_idx = widgets_in_row.index(window._auto_snap_enabled)
            self.assertEqual(snap_idx, rev_idx + 1)

            # 4. 验证动作标签文案为"框选后动作："
            copy_action_row = widgets_in_row[-1]
            labels = [c for c in copy_action_row.children() if isinstance(c, QLabel)]
            self.assertTrue(any("框选后动作" in lbl.text() for lbl in labels))

            window.close()
        finally:
            mw.MainWindow._init_tray = orig_init_tray
            mw.MainWindow._start_deferred_startup_services = orig_deferred
            mw.MainWindow.closeEvent = orig_close_event

    def test_settings_page_and_quick_action_buttons(self):
        from PyQt6.QtWidgets import QCheckBox, QLabel, QPushButton
        from deepcat.ui import main_window as mw

        dummy_tray = type("DummyTray", (), {"setToolTip": lambda *_: None, "setIcon": lambda *_: None})()
        orig_init_tray = mw.MainWindow._init_tray
        orig_deferred = mw.MainWindow._start_deferred_startup_services
        orig_close_event = mw.MainWindow.closeEvent
        mw.MainWindow._init_tray = lambda self: dummy_tray
        mw.MainWindow._start_deferred_startup_services = lambda self: None
        mw.MainWindow.closeEvent = lambda self, e: e.accept()
        try:
            window = mw.MainWindow()

            # 1. 验证底部快捷按钮排列：第1位开始截图，第2位截图目录，第3位AI对话，第4位设置
            self.assertEqual(window._btn_quick_capture.property("tipText"), "开始截图")
            self.assertEqual(window._btn_quick_folder.property("tipText"), "截图目录")
            self.assertEqual(window._btn_quick_ai.property("tipText"), "AI对话")
            self.assertIsNotNone(getattr(window, "_btn_quick_settings", None))
            self.assertEqual(window._btn_quick_settings.property("tipText"), "设置")

            quick_layout = window._quick_actions_widget.layout()
            self.assertEqual(quick_layout.itemAt(0).widget(), window._btn_quick_capture)
            self.assertEqual(quick_layout.itemAt(1).widget(), window._btn_quick_folder)
            self.assertEqual(quick_layout.itemAt(2).widget(), window._btn_quick_ai)
            self.assertEqual(quick_layout.itemAt(3).widget(), window._btn_quick_settings)

            # 2. 验证截图设置页面行间距与“智能问答”文案
            self.assertEqual(window._capture_settings_group.layout().spacing(), 14)
            labels = window.findChildren(QLabel)
            smart_qa_labels = [l for l in labels if l.text() == "智能问答:"]
            self.assertTrue(len(smart_qa_labels) >= 1)
            ai_qa_labels = [l for l in labels if l.text() == "AI问答:"]
            self.assertEqual(len(ai_qa_labels), 0)

            # 3. 验证点击设置按钮切换到设置页面 (index 10)
            self.assertEqual(window._SETTINGS_PAGE_INDEX, 10)
            window._btn_quick_settings.click()
            self.assertEqual(window._stack.currentIndex(), 10)

            # 3. 验证设置页面中的开关控件
            self.assertIsInstance(window._autostart, QCheckBox)
            self.assertIsInstance(window._notifications, QCheckBox)
            self.assertIsInstance(window._feature_clipboard_history_switch, QCheckBox)
            self.assertIsInstance(window._feature_table_notes_switch, QCheckBox)
            self.assertIsInstance(window._feature_later_read_switch, QCheckBox)
            self.assertIsInstance(window._feature_todo_switch, QCheckBox)
            self.assertIsInstance(window._selection_translate_switch, QCheckBox)
            self.assertIsInstance(window._selection_popup_switch, QCheckBox)
            self.assertIsInstance(window._auto_copy_switch, QCheckBox)

            # 4. 验证设置页面控件的文案与层级归属
            settings_page = window._stack.widget(10)
            switches_in_settings = settings_page.findChildren(QCheckBox)
            self.assertIn(window._autostart, switches_in_settings)
            self.assertIn(window._notifications, switches_in_settings)
            self.assertIn(window._feature_clipboard_history_switch, switches_in_settings)
            self.assertIn(window._selection_translate_switch, switches_in_settings)
            self.assertIn(window._selection_popup_switch, switches_in_settings)
            self.assertIn(window._auto_copy_switch, switches_in_settings)

            # 5. 验证模型管理页面已移除划词开关
            translator_page = window._stack.widget(1)
            switches_in_translator = translator_page.findChildren(QCheckBox)
            self.assertNotIn(window._selection_translate_switch, switches_in_translator)
            self.assertNotIn(window._selection_popup_switch, switches_in_translator)
            self.assertNotIn(window._auto_copy_switch, switches_in_translator)
            self.assertIn(window._local_translation_service_switch, switches_in_translator)
            self.assertIn(window._codex_config_switch, switches_in_translator)

            window.close()
        finally:
            mw.MainWindow._init_tray = orig_init_tray
            mw.MainWindow._start_deferred_startup_services = orig_deferred
            mw.MainWindow.closeEvent = orig_close_event


if __name__ == "__main__":
    unittest.main()
