import sys
import unittest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QMoveEvent
from deepcat.ui.post_capture_actions import OcrTextPanel

class TestOcrPanelPosition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._patchers = [
            patch.object(OcrTextPanel, "_save_current_chat_draft", lambda *_args, **_kwargs: None),
            patch.object(OcrTextPanel, "_restore_chat_draft", lambda *_args, **_kwargs: None),
            patch.object(OcrTextPanel, "_save_last_pos_to_settings", staticmethod(lambda: None)),
            patch.object(OcrTextPanel, "_save_history_sidebar_open_to_settings", staticmethod(lambda _opened: None)),
        ]
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self._patchers):
            patcher.stop()

    def test_ocr_text_panel_position_memory(self):
        panel = OcrTextPanel()
        previous_last_pos = OcrTextPanel._last_pos
        previous_user_moved = OcrTextPanel._last_pos_user_moved
        try:
            remembered_pos = QPoint(60, 80)
            OcrTextPanel._last_pos = remembered_pos
            OcrTextPanel._last_pos_user_moved = True

            # 程序性移动不能覆盖用户上次拖动的位置。
            panel._bubble_view.hide()
            panel._dragging_window = False

            pos1 = QPoint(100, 200)
            panel.setGeometry(pos1.x(), pos1.y(), 100, 100)
            event1 = QMoveEvent(pos1, QPoint(0, 0))
            panel.moveEvent(event1)

            self.assertEqual(OcrTextPanel._last_pos, remembered_pos)

            panel._bubble_view.show()
            pos2 = QPoint(100, 100)
            panel.setGeometry(pos2.x(), pos2.y(), 100, 200)
            event2 = QMoveEvent(pos2, pos1)
            panel.moveEvent(event2)

            self.assertEqual(OcrTextPanel._last_pos, remembered_pos)

            # 真实拖动仍应更新记忆位置。
            panel._dragging_window = True
            pos3 = QPoint(300, 400)
            panel.setGeometry(pos3.x(), pos3.y(), 100, 200)
            event3 = QMoveEvent(pos3, pos2)
            panel.moveEvent(event3)

            self.assertEqual(OcrTextPanel._last_pos, pos3)
            self.assertTrue(OcrTextPanel._last_pos_user_moved)

        finally:
            OcrTextPanel._last_pos = previous_last_pos
            OcrTextPanel._last_pos_user_moved = previous_user_moved
            panel.close()
            panel.deleteLater()
            QApplication.processEvents()

    def test_initial_manual_ai_panel_is_centered_without_user_position(self):
        panel = OcrTextPanel(restore_history_sidebar=False)
        previous_last_pos = OcrTextPanel._last_pos
        previous_user_moved = OcrTextPanel._last_pos_user_moved
        try:
            OcrTextPanel._last_pos = None
            OcrTextPanel._last_pos_user_moved = False
            panel._load_last_pos_from_settings = lambda: None

            panel.set_text_and_reposition(
                "",
                QRect(20, 20, 1, 1),
                center_on_first_show=True,
            )
            QApplication.processEvents()

            screen = panel.screen() or QApplication.primaryScreen()
            available = screen.availableGeometry()
            self.assertLessEqual(abs(panel.geometry().center().x() - available.center().x()), 1)
            self.assertLessEqual(abs(panel.geometry().center().y() - available.center().y()), 1)
            self.assertFalse(OcrTextPanel._last_pos_user_moved)
        finally:
            OcrTextPanel._last_pos = previous_last_pos
            OcrTextPanel._last_pos_user_moved = previous_user_moved
            panel.close()
            panel.deleteLater()
            QApplication.processEvents()

    def test_ocr_text_panel_maximize_and_restore(self):
        panel = OcrTextPanel()
        try:
            # mock 掉 isVisible 接口让其直接返回 True，从而执行 visible reposition 分支
            panel.isVisible = lambda: True
            # 初始状态
            panel._is_maximized = False
            panel._bubble_view.hide()

            # 动态计算绝对安全的偏置坐标，避免测试环境屏幕宽度过窄导致安全限幅错位
            from PyQt6.QtGui import QGuiApplication
            from PyQt6.QtCore import QRect
            screen = QGuiApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else QRect(0, 0, 800, 600)
            max_w = int(max(260, geo.width() - 16))
            w = int(min(max_w, min(760, max(360, max(620, int(geo.width() * 0.42))))))
            w += 16
            safe_min_x = geo.x() + 8
            safe_max_x = geo.x() + geo.width() - w - 8
            if safe_max_x < safe_min_x:
                safe_max_x = safe_min_x

            # 使用一个非居中但安全的偏移 x 坐标
            initial_x = int(safe_min_x + (safe_max_x - safe_min_x) // 3)
            initial_y = 150
            initial_w = 400
            initial_h = 300
            panel.setGeometry(initial_x, initial_y, initial_w, initial_h)

            # 模拟初始稳定几何记录
            panel._pre_max_geometry = panel.geometry()

            # 1. 触发最大化
            panel.toggle_maximize()
            self.assertTrue(panel._is_maximized)

            # 2. 触发还原
            panel.toggle_maximize()
            self.assertFalse(panel._is_maximized)

            # 还原后的位置 x 坐标应该变回 initial_x，而不会遗留在最大化时的居中位置
            self.assertEqual(panel.geometry().x(), initial_x)

        finally:
            panel.close()
            panel.deleteLater()
            QApplication.processEvents()

    def test_ocr_text_panel_history_toggle_restore(self):
        panel = OcrTextPanel()
        try:
            # mock 掉 isVisible 接口让其直接返回 True，从而执行 visible reposition 分支
            panel.isVisible = lambda: True
            # 初始状态
            panel._is_maximized = False
            panel._history_showing = False
            panel._bubble_view.hide()

            # 动态计算绝对安全的偏置坐标，避免测试环境屏幕宽度过窄导致安全限幅错位
            from PyQt6.QtGui import QGuiApplication
            from PyQt6.QtCore import QRect
            screen = QGuiApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else QRect(0, 0, 800, 600)
            max_w = int(max(260, geo.width() - 16))
            w = int(min(max_w, min(760, max(360, max(620, int(geo.width() * 0.42))))))
            w += 16
            safe_min_x = geo.x() + 8
            safe_max_x = geo.x() + geo.width() - w - 8
            if safe_max_x < safe_min_x:
                safe_max_x = safe_min_x

            # 特意模拟用户点击时，窗口处于历史侧栏展开大窗口后的右下角偏出范围位置，以强行触发安全限幅向左上推
            initial_w = w # 使用计算所得的非最大化标准宽
            initial_h = 210 # 空历史时矮窗口的高度
            initial_x = geo.x() + geo.width() - initial_w - 20
            initial_y = geo.y() + geo.height() - initial_h - 20
            panel.setGeometry(initial_x, initial_y, initial_w, initial_h)
            # 包装 setGeometry 以追踪调用轨迹，验证展开是否无水平/垂直跳闪
            geometry_calls = []
            original_set_geometry = panel.setGeometry

            def track_set_geometry(*args):
                if len(args) == 1:
                    rect = args[0]
                else:
                    rect = QRect(*args)
                geometry_calls.append(QRect(rect))
                original_set_geometry(rect)

            panel.setGeometry = track_set_geometry

            # 1. 触发展示历史侧栏
            panel._show_history()
            self.assertTrue(panel._history_showing)

            # 历史侧栏展开时应直接应用最终目标矩形，不再先在旧左上角拉大一个临时矩形，
            # 否则右下角场景会出现可见跳闪。
            self.assertEqual(len(geometry_calls), 1)
            first_step_rect = geometry_calls[0]
            expected_x = max(
                geo.x() + 8,
                min(
                    geo.x() + geo.width() - first_step_rect.width() - 8,
                    initial_x,
                ),
            )
            expected_y = max(
                geo.y() + 8,
                min(
                    geo.y() + geo.height() - first_step_rect.height() - 8,
                    initial_y,
                ),
            )
            self.assertEqual(first_step_rect.x(), expected_x)
            self.assertEqual(first_step_rect.y(), expected_y)

            # 清空轨迹以继续测试收缩还原
            geometry_calls.clear()

            # 2. 触发隐藏历史侧栏并收回
            panel._hide_history()
            self.assertFalse(panel._history_showing)

            # 收起侧栏时保持展开后的左边界不动，仅缩窄宽度，且底边 y 坐标应该变回原来的底边 y 坐标
            self.assertEqual(panel.geometry().x(), first_step_rect.x())
            self.assertEqual(panel.geometry().y() + panel.geometry().height(), initial_y + initial_h)

        finally:
            panel.close()
            panel.deleteLater()
            QApplication.processEvents()

    def test_remembered_history_hide_keeps_current_y_after_initial_restore(self):
        panel = OcrTextPanel(restore_history_sidebar=False)
        previous_last_pos = OcrTextPanel._last_pos
        try:
            from PyQt6.QtGui import QGuiApplication

            screen = QGuiApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)
            remembered_y = max(geo.y() + 32, geo.y() + geo.height() - 760)
            OcrTextPanel._last_pos = QPoint(geo.x() + 64, remembered_y)

            # 模拟“记住历史侧栏打开状态”时，窗口尚未 show() 就先恢复历史侧栏。
            # 这里的默认几何不是用户真正点击收起时应使用的锚点。
            panel.setGeometry(geo.x() + 40, geo.y() + 40, 640, 480)
            panel._is_maximized = False
            panel._history_showing = False
            panel._bubble_view.hide()

            panel._show_history(persist_state=False)
            self.assertTrue(panel._history_showing)
            self.assertIsNone(panel._pre_history_geometry)

            panel.show()
            QApplication.processEvents()
            open_y = panel.geometry().y()

            panel._hide_history(persist_state=False)
            QApplication.processEvents()

            self.assertFalse(panel._history_showing)
            self.assertEqual(panel.geometry().y(), open_y)
        finally:
            OcrTextPanel._last_pos = previous_last_pos
            panel.close()
            panel.deleteLater()
            QApplication.processEvents()

    def test_hide_history_after_record_selection_does_not_jump_up(self):
        panel = OcrTextPanel(restore_history_sidebar=False)
        try:
            from PyQt6.QtGui import QGuiApplication

            screen = QGuiApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)
            pre_history_geo = QRect(geo.x() + 80, geo.y() + geo.height() - 230, 620, 210)
            history_geo = QRect(geo.x() + 8, geo.y() + 72, min(920, geo.width() - 16), 720)

            panel._is_maximized = False
            panel._history_showing = True
            panel._pre_history_geometry = QRect(pre_history_geo)
            panel._history_forced_output_area = False
            panel._chat_history = [
                {"role": "user", "content": "测试问题"},
                {"role": "assistant", "content": "测试回答"},
            ]
            panel._bubble_view.show()
            panel.show()
            panel.setGeometry(history_geo)
            QApplication.processEvents()
            open_y = panel.geometry().y()

            panel._hide_history(persist_state=False)
            QApplication.processEvents()

            self.assertFalse(panel._history_showing)
            self.assertEqual(panel.geometry().y(), open_y)
        finally:
            panel.close()
            panel.deleteLater()
            QApplication.processEvents()

    def test_history_panel_reposition_keeps_current_origin(self):
        panel = OcrTextPanel()
        try:
            from PyQt6.QtGui import QGuiApplication

            screen = QGuiApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else QRect(0, 0, 1200, 800)
            current_geo = QRect(geo.x() + 8, geo.y() + 56, min(900, geo.width() - 16), 640)

            panel._history_showing = True
            panel._is_maximized = False
            panel._bubble_view.hide()
            panel._region = QRect(geo.x() + geo.width() - 20, geo.y() + geo.height() - 20, 1, 1)
            panel.show()
            panel.setGeometry(current_geo)
            QApplication.processEvents()
            current_origin = panel.pos()

            panel._reposition()

            self.assertEqual(panel.pos(), current_origin)

        finally:
            panel.close()
            panel.deleteLater()
            QApplication.processEvents()

if __name__ == "__main__":
    unittest.main()
