import unittest


from deepcat.ui.notifications import CaptureNotificationState


class TestNotifications(unittest.TestCase):
    def test_manual_stop_saved_once(self) -> None:
        s = CaptureNotificationState()
        s.on_stopped("用户手动停止")
        n1 = s.build_saved_notification("a.png")
        n2 = s.build_saved_notification("b.png")
        self.assertIsNotNone(n1)
        self.assertIsNone(n2)
        assert n1 is not None
        self.assertEqual(n1.title, "已保存（手动停止）")
        self.assertEqual(n1.message, "已保存到：a.png")

    def test_bottom_reached_saved_once(self) -> None:
        s = CaptureNotificationState()
        s.on_stopped("已到达底部，自动停止")
        n1 = s.build_saved_notification(["a.png", "b.pdf"])
        n2 = s.build_saved_notification(["c.png"])
        self.assertIsNotNone(n1)
        self.assertIsNone(n2)
        assert n1 is not None
        self.assertEqual(n1.title, "已保存（已到达底部）")
        self.assertEqual(n1.message, "已保存 2 个文件：a.png")

    def test_saved_without_stop_reason(self) -> None:
        s = CaptureNotificationState()
        n1 = s.build_saved_notification("a.png")
        self.assertIsNotNone(n1)
        assert n1 is not None
        self.assertEqual(n1.title, "已保存")
        self.assertEqual(n1.message, "已保存到：a.png")

    def test_failed_never_notifies(self) -> None:
        s = CaptureNotificationState()
        s.on_stopped("用户手动停止")
        s.on_failed()
        n1 = s.build_saved_notification("a.png")
        self.assertIsNone(n1)


if __name__ == "__main__":
    unittest.main()
