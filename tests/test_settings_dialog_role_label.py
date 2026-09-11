import unittest

from deepcat.ui.settings_dialog import SettingsDialog


class _FakeLabel:
    def __init__(self, text: str) -> None:
        self._text = text
        self._visible = True

    def setText(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        return self._text

    def setVisible(self, visible: bool) -> None:
        self._visible = bool(visible)

    def isVisible(self) -> bool:
        return self._visible


class TestSettingsDialogRoleLabel(unittest.TestCase):
    def test_settings_dialog_role_label_stays_hidden(self):
        label = _FakeLabel("翻译使用")
        dummy = type("Dummy", (), {})()
        dummy._translator_api_url_role_label = label

        SettingsDialog._update_api_url_role_label(dummy)

        self.assertEqual(label.text(), "")
        self.assertFalse(label.isVisible())


if __name__ == "__main__":
    unittest.main()
