import unittest


class TestHotkeyFormat(unittest.TestCase):
    def test_qt_to_pynput(self) -> None:
        from deepcat.input.hotkey_format import qt_to_pynput

        self.assertEqual(qt_to_pynput("Ctrl+Shift+S"), "<ctrl>+<shift>+s")
        self.assertEqual(qt_to_pynput("Alt+A"), "<alt>+a")
        self.assertEqual(qt_to_pynput("F1"), "<f1>")
        self.assertEqual(qt_to_pynput("Ctrl+F5"), "<ctrl>+<f5>")
        self.assertIsNone(qt_to_pynput(""))

    def test_pynput_to_qt(self) -> None:
        from deepcat.input.hotkey_format import pynput_to_qt

        self.assertEqual(pynput_to_qt("<ctrl>+<shift>+s"), "Ctrl+Shift+S")
        self.assertEqual(pynput_to_qt("<alt>+a"), "Alt+A")
        self.assertEqual(pynput_to_qt("<f1>"), "F1")


if __name__ == "__main__":
    unittest.main()
