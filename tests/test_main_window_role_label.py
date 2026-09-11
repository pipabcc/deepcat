import unittest
from unittest.mock import patch

from deepcat.ui.main_window import MainWindow
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


class _FakeCombo:
    def __init__(self, text: str) -> None:
        self._text = text

    def currentText(self) -> str:
        return self._text


class TestMainWindowRoleLabel(unittest.TestCase):
    def test_main_window_reuses_hidden_role_label_behavior(self):
        self.assertIs(MainWindow._update_api_url_role_label, SettingsDialog._update_api_url_role_label)

        label = _FakeLabel("问答使用")
        dummy = type("Dummy", (), {})()
        dummy._translator_api_url_role_label = label

        MainWindow._update_api_url_role_label(dummy)

        self.assertEqual(label.text(), "")
        self.assertFalse(label.isVisible())

    def test_main_window_reuses_model_api_helpers(self):
        self.assertEqual(MainWindow._TRANSLATOR_FORM_LABEL_WIDTH, SettingsDialog._TRANSLATOR_FORM_LABEL_WIDTH)
        self.assertEqual(MainWindow._MODEL_ROLE_LABEL_WIDTH, SettingsDialog._MODEL_ROLE_LABEL_WIDTH)
        self.assertEqual(MainWindow._MODEL_ROLE_COMBO_WIDTH, SettingsDialog._MODEL_ROLE_COMBO_WIDTH)
        self.assertEqual(MainWindow._MODEL_ID_COMBO_MIN_WIDTH, SettingsDialog._MODEL_ID_COMBO_MIN_WIDTH)
        self.assertEqual(MainWindow._MODEL_ID_COMBO_MAX_WIDTH, SettingsDialog._MODEL_ID_COMBO_MAX_WIDTH)
        self.assertIs(MainWindow._repaint_model_combos, SettingsDialog._repaint_model_combos)
        self.assertIs(MainWindow._normalized_api_url, SettingsDialog._normalized_api_url)
        self.assertIs(MainWindow._on_new_api_model_clicked, SettingsDialog._on_new_api_model_clicked)
        self.assertIs(MainWindow._position_translator_provider_hover_hint, SettingsDialog._position_translator_provider_hover_hint)
        self.assertIs(MainWindow._sync_model_role_combo_widths, SettingsDialog._sync_model_role_combo_widths)
        self.assertIs(MainWindow._is_current_fetched_model_id, SettingsDialog._is_current_fetched_model_id)
        self.assertIs(MainWindow._sync_model_note_from_fetched_model_id, SettingsDialog._sync_model_note_from_fetched_model_id)
        self.assertIs(MainWindow._on_model_id_activated, SettingsDialog._on_model_id_activated)
        self.assertIs(MainWindow._select_model_id_from_list, SettingsDialog._select_model_id_from_list)
        self.assertIs(MainWindow._set_translator_provider_value, SettingsDialog._set_translator_provider_value)
        self.assertIs(MainWindow._select_provider_for_new_model, SettingsDialog._select_provider_for_new_model)
        self.assertIs(MainWindow._move_current_model_to_provider, SettingsDialog._move_current_model_to_provider)
        self.assertIs(MainWindow._save_current_model_with_provider, SettingsDialog._save_current_model_with_provider)
        self.assertIs(MainWindow._on_provider_selected_for_new_model, SettingsDialog._on_provider_selected_for_new_model)
        self.assertIs(MainWindow._sync_provider_popup_model_cache, SettingsDialog._sync_provider_popup_model_cache)
        self.assertIs(MainWindow._provider_popup_models_by_provider, SettingsDialog._provider_popup_models_by_provider)
        self.assertIs(MainWindow._provider_popup_current_model, SettingsDialog._provider_popup_current_model)
        self.assertIs(MainWindow._provider_highlighted_model_colors, SettingsDialog._provider_highlighted_model_colors)
        self.assertIs(MainWindow._codex_action_label_for_model, SettingsDialog._codex_action_label_for_model)
        self.assertIs(MainWindow._claude_code_action_label_for_model, SettingsDialog._claude_code_action_label_for_model)
        self.assertIs(MainWindow._apply_current_model_codex_config_for_model, SettingsDialog._apply_current_model_codex_config_for_model)
        self.assertIs(MainWindow._apply_current_model_claude_code_config_for_model, SettingsDialog._apply_current_model_claude_code_config_for_model)

    def test_previous_capture_action_unknown_text_falls_back_to_pin(self):
        captured: dict[str, str] = {}
        dummy = type("Dummy", (), {})()
        dummy._previous_capture_action_combo = _FakeCombo("关闭前图")
        dummy._sync_settings_after_change = lambda: None

        with patch("deepcat.ui.main_window.capture.update_ui_settings", lambda **kwargs: captured.update(kwargs) or kwargs):
            MainWindow._apply_previous_capture_action(dummy)

        self.assertEqual(captured.get("previous_capture_action"), "pin")

        captured.clear()
        dummy2 = type("Dummy", (), {})()
        dummy2._previous_capture_action = _FakeCombo("关闭前图")
        dummy2._show_general_status = lambda *args, **kwargs: None

        with patch("deepcat.ui.settings_dialog.dialog.update_ui_settings", lambda **kwargs: captured.update(kwargs) or kwargs):
            SettingsDialog._apply_previous_capture_action(dummy2)

        self.assertEqual(captured.get("previous_capture_action"), "pin")

    def test_previous_capture_action_stash_text_maps_to_stash(self):
        captured: dict[str, str] = {}
        dummy = type("Dummy", (), {})()
        dummy._previous_capture_action_combo = _FakeCombo("暂存前图")
        dummy._sync_settings_after_change = lambda: None

        with patch("deepcat.ui.main_window.capture.update_ui_settings", lambda **kwargs: captured.update(kwargs) or kwargs):
            MainWindow._apply_previous_capture_action(dummy)

        self.assertEqual(captured.get("previous_capture_action"), "stash")

        captured.clear()
        dummy2 = type("Dummy", (), {})()
        dummy2._previous_capture_action = _FakeCombo("暂存前图")
        dummy2._show_general_status = lambda *args, **kwargs: None

        with patch("deepcat.ui.settings_dialog.dialog.update_ui_settings", lambda **kwargs: captured.update(kwargs) or kwargs):
            SettingsDialog._apply_previous_capture_action(dummy2)

        self.assertEqual(captured.get("previous_capture_action"), "stash")


if __name__ == "__main__":
    unittest.main()
