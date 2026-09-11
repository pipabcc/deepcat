import unittest
from types import MethodType
from types import SimpleNamespace


class TestLaterReadToast(unittest.TestCase):
    def test_region_link_hovered_uses_cursor_position_for_toast(self) -> None:
        try:
            from PyQt6.QtCore import QPoint
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过稍后阅读鼠标位置通知测试")

        captured: dict[str, object] = {}
        cursor_pos = QPoint(320, 240)

        class FakeClipboard:
            def setText(self, text: str) -> None:
                raise AssertionError("自动采集不应写入系统剪贴板")

        dummy = SimpleNamespace(
            _last_later_read_capture_key="",
            _later_read_enabled_now=lambda: True,
            _close_later_read_probe_overlay=lambda: captured.update(closed=True),
        )

        def add_later_read_link_at(title: str, url: str, *, toast_pos=None) -> None:
            captured["title"] = title
            captured["url"] = url
            captured["toast_pos"] = QPoint(toast_pos)

        dummy._add_later_read_link_at = add_later_read_link_at

        old_qcursor = mw.capture.QCursor
        old_qapplication = mw.capture.QApplication
        mw.capture.QCursor = SimpleNamespace(pos=lambda: cursor_pos)
        mw.capture.QApplication = SimpleNamespace(clipboard=lambda: FakeClipboard())
        try:
            mw.MainWindow._on_region_link_hovered(
                dummy,
                {"title": "示例页面", "url": "https://example.com/article"},
            )
        finally:
            mw.capture.QCursor = old_qcursor
            mw.capture.QApplication = old_qapplication

        self.assertNotIn("clipboard", captured)
        self.assertNotIn("ignored", captured)
        self.assertEqual(captured["title"], "示例页面")
        self.assertEqual(captured["url"], "https://example.com/article")
        self.assertEqual(captured["toast_pos"], cursor_pos)
        self.assertTrue(captured["closed"])

    def test_later_read_probe_timeout_closes_active_overlay(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过稍后阅读探测超时测试")

        captured: dict[str, object] = {}

        class FakeOverlay:
            def close(self) -> None:
                captured["closed"] = True

        dummy = SimpleNamespace(
            _later_read_probe_generation=7,
            _later_read_probe_overlay=FakeOverlay(),
            _restore_later_read_probe_cursor=lambda: captured.update(restored=captured.get("restored", 0) + 1),
            _show_later_read_capture_status=lambda text, *, tone, auto_hide_ms: captured.update(
                status=(text, tone, auto_hide_ms)
            ),
        )
        dummy._close_later_read_probe_overlay = lambda: mw.MainWindow._close_later_read_probe_overlay(dummy)
        dummy._later_read_probe_cursor_moved_for_timeout_extension = MethodType(
            mw.MainWindow._later_read_probe_cursor_moved_for_timeout_extension, dummy
        )
        dummy._extend_later_read_probe_timeout_once = MethodType(
            mw.MainWindow._extend_later_read_probe_timeout_once, dummy
        )
        dummy._schedule_later_read_probe_timeout = MethodType(
            mw.MainWindow._schedule_later_read_probe_timeout, dummy
        )

        mw.MainWindow._close_later_read_probe_after_timeout(dummy, 7)

        self.assertTrue(captured["closed"])
        self.assertGreaterEqual(captured["restored"], 1)
        self.assertIsNone(dummy._later_read_probe_overlay)
        self.assertEqual(captured["status"], ("3 秒内未识别到链接，已退出稍后阅读采集。", "info", 2600))

    def test_later_read_probe_timeout_extends_once_when_cursor_moves(self) -> None:
        try:
            from PyQt6.QtCore import QPoint
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过稍后阅读探测超时延长测试")

        captured: dict[str, object] = {}

        class FakeOverlay:
            def _schedule_link_probe_burst(self) -> None:
                captured["burst"] = int(captured.get("burst", 0)) + 1

            def close(self) -> None:
                captured["closed"] = True

        dummy = SimpleNamespace(
            _later_read_probe_generation=7,
            _later_read_probe_overlay=FakeOverlay(),
            _later_read_probe_timeout_anchor_pos=QPoint(10, 10),
            _later_read_probe_timeout_extensions=0,
            _show_later_read_capture_status=lambda *args, **kwargs: captured.update(status=True),
            _close_later_read_probe_overlay=lambda: captured.update(closed=True),
        )
        dummy._later_read_probe_cursor_moved_for_timeout_extension = MethodType(
            mw.MainWindow._later_read_probe_cursor_moved_for_timeout_extension, dummy
        )
        dummy._extend_later_read_probe_timeout_once = MethodType(
            mw.MainWindow._extend_later_read_probe_timeout_once, dummy
        )
        dummy._schedule_later_read_probe_timeout = MethodType(
            mw.MainWindow._schedule_later_read_probe_timeout, dummy
        )

        scheduled: list[tuple[int, object]] = []
        old_qcursor = mw.later_read.QCursor
        old_qtimer = mw.later_read.QTimer
        mw.later_read.QCursor = SimpleNamespace(pos=lambda: QPoint(80, 10))
        mw.later_read.QTimer = SimpleNamespace(singleShot=lambda ms, cb: scheduled.append((ms, cb)))
        try:
            mw.MainWindow._close_later_read_probe_after_timeout(dummy, 7)
        finally:
            mw.later_read.QCursor = old_qcursor
            mw.later_read.QTimer = old_qtimer

        self.assertNotIn("status", captured)
        self.assertNotIn("closed", captured)
        self.assertEqual(captured["burst"], 1)
        self.assertEqual(dummy._later_read_probe_timeout_extensions, 1)
        self.assertEqual(dummy._later_read_probe_timeout_anchor_pos, QPoint(80, 10))
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][0], 3000)

    def test_later_read_capture_status_keeps_inline_status_and_nearby_toast(self) -> None:
        try:
            from PyQt6.QtCore import QPoint
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过稍后阅读鼠标位置通知测试")

        captured: dict[str, object] = {}
        anchor = QPoint(500, 360)

        dummy = SimpleNamespace(
            _show_later_read_status=lambda text, *, tone, auto_hide_ms: captured.update(
                inline=(text, tone, auto_hide_ms)
            ),
            _show_notification_failure_status=lambda text: captured.update(failure=text),
        )

        old_show_near = mw.NotificationPopup.__dict__["show_notification_near"]

        def fake_show_near(title: str, message: str, global_pos, duration_ms: int = 2000, auto_close: bool = True) -> None:
            captured["toast"] = (title, message, QPoint(global_pos), duration_ms, auto_close)

        mw.NotificationPopup.show_notification_near = fake_show_near
        try:
            mw.MainWindow._show_later_read_capture_status(
                dummy,
                "已保存链接：示例页面",
                tone="success",
                auto_hide_ms=2600,
                toast_pos=anchor,
            )
        finally:
            mw.NotificationPopup.show_notification_near = old_show_near

        self.assertEqual(captured["inline"], ("已保存链接：示例页面", "success", 2600))
        self.assertEqual(captured["toast"], ("稍后阅读", "已保存链接：示例页面", anchor, 2600, True))
        self.assertNotIn("failure", captured)


if __name__ == "__main__":
    unittest.main()
