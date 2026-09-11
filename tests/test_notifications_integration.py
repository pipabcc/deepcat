import time
import unittest


from deepcat.ui.notifications import CaptureNotificationState


class TestNotificationIntegration(unittest.TestCase):
    def test_fast_repeated_events_still_notifies_once(self) -> None:
        s = CaptureNotificationState()
        for _ in range(5):
            s.on_stopped("用户手动停止")
            time.sleep(0.001)
        n1 = s.build_saved_notification(["a.png"])
        time.sleep(0.001)
        n2 = s.build_saved_notification(["b.png"])
        self.assertIsNotNone(n1)
        self.assertIsNone(n2)
        assert n1 is not None
        self.assertEqual(n1.title, "已保存（手动停止）")

    def test_stopped_then_failed_no_notification(self) -> None:
        s = CaptureNotificationState()
        s.on_stopped("已到达底部，自动停止")
        time.sleep(0.002)
        s.on_failed()
        n = s.build_saved_notification("a.png")
        self.assertIsNone(n)


if __name__ == "__main__":
    unittest.main()
