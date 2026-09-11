import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QDate, QEvent, QPoint, Qt
from PyQt6.QtGui import QIcon, QStandardItem, QStandardItemModel, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from deepcat.ui.settings_dialog import (
    CLAUDE_CODE_CURRENT_MODEL_CHECK_COLOR,
    CODEX_CURRENT_MODEL_CHECK_COLOR,
    FetchModelsWorker,
    ModernPopupComboBox,
    PasteAwareComboBox,
    PromptEditDialog,
    SettingsDialog,
    TRANSLATOR_MODEL_CODEX_BOUND_ROLE,
    TRANSLATOR_MODEL_SEARCH_ROLE,
    TranslatorConnectionTestWorker,
    TranslatorModelComboBox,
    _FetchedModelIdPopup,
)


class _Response:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP 状态码: {self.status_code}")


class _FakeField:
    def __init__(self, text: str = "") -> None:
        self.value = text
        self.focused = False

    def clear(self) -> None:
        self.value = ""

    def text(self) -> str:
        return self.value

    def setCurrentText(self, value: str) -> None:
        self.value = str(value or "")

    def setFocus(self) -> None:
        self.focused = True


class TestSettingsDialogModels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication(sys.argv)

    def test_ocr_translate_switch_stays_hidden_on_model_management_tab(self):
        import deepcat.settings_store as settings_store

        parent = QWidget()
        with patch("deepcat.ui.settings_dialog.dialog.is_autostart_enabled", return_value=False), patch.object(
            SettingsDialog, "_restore_window_geometry"
        ):
            dialog = SettingsDialog(parent, settings_store._defaults())

        tabs = dialog.findChild(QTabWidget)
        self.assertIsNotNone(tabs)
        model_tab_index = next(index for index in range(tabs.count()) if tabs.tabText(index) == "模型管理")

        self.assertFalse(dialog._ocr_translate_switch.isChecked())
        self.assertNotIn(dialog._ocr_translate_switch, dialog._translator_footer_widgets)
        tabs.setCurrentIndex(model_tab_index)
        QApplication.processEvents()

        self.assertTrue(dialog._ocr_translate_switch.isHidden())
        self.assertFalse(dialog._auto_copy_switch.isHidden())
        dialog.deleteLater()
        parent.deleteLater()
        QApplication.processEvents()

    def test_fetch_models_keeps_models_endpoint_exact(self):
        worker = FetchModelsWorker(" https://api.example.com/v1/models ", "", False, "")

        self.assertEqual(worker._candidate_model_urls(), ["https://api.example.com/v1/models"])

    def test_translator_test_missing_fields_highlights_inputs_without_popup(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_test_worker = None
        dummy._translator_testing = False
        dummy._translator_api_url = QLineEdit()
        dummy._translator_model_name = QLineEdit()
        dummy._translator_api_key = QLineEdit()
        dummy._translator_proxy_url = QLineEdit()
        dummy._translator_test_btn = QPushButton("模型测试")
        dummy._current_translator_runtime_config = lambda: (
            "测试模型",
            {"base_url": "", "model_name": "", "api_key": "", "model_type": "glm"},
            False,
            "",
        )
        dummy._persist_translator_settings = lambda: None
        dummy._clear_translator_proxy_selection = lambda: None
        dummy._translator_required_fields = SettingsDialog._translator_required_fields.__get__(dummy, type(dummy))

        with patch("deepcat.ui.settings_dialog.SettingsDialog._show_feedback_message") as feedback, \
             patch("deepcat.ui.settings_dialog.dialog.TranslatorConnectionTestWorker") as worker_cls:
            SettingsDialog._test_translator_connection(dummy)

        feedback.assert_not_called()
        worker_cls.assert_not_called()
        self.assertTrue(dummy._translator_api_url.property("missingRequired"))
        self.assertTrue(dummy._translator_model_name.property("missingRequired"))
        self.assertTrue(dummy._translator_api_key.property("missingRequired"))
        self.assertIn("API地址", dummy._translator_test_btn.toolTip())

    def test_fetched_model_id_popup_supports_count_filter_active_and_select(self):
        selected = []
        parent = QLineEdit()
        parent.resize(220, 30)
        popup = _FetchedModelIdPopup(
            ["model-a", "model-b", "vision-model"],
            "model-b",
            selected.append,
            parent=parent,
        )

        self.assertEqual(popup._title.text(), "已获取 3 个模型 ID")
        buttons = [widget for widget in popup._row_widgets if isinstance(widget, QPushButton)]
        self.assertEqual([button.text() for button in buttons], ["model-a", "model-b", "vision-model"])
        self.assertFalse(buttons[0].property("activeModel"))
        self.assertTrue(buttons[1].property("activeModel"))

        popup._search.setText("vision")
        buttons = [widget for widget in popup._row_widgets if isinstance(widget, QPushButton)]
        self.assertEqual([button.text() for button in buttons], ["vision-model"])
        popup._copy_model_id("vision-model")
        self.assertEqual(QApplication.clipboard().text(), "vision-model")
        buttons[0].click()
        self.assertEqual(selected, ["vision-model"])

    def test_translator_test_result_messages_are_classified(self):
        self.assertEqual(
            TranslatorConnectionTestWorker._format_test_result(True, ""),
            "连通成功：模型可正常响应。",
        )
        self.assertIn(
            "鉴权失败",
            TranslatorConnectionTestWorker._format_test_result(False, "HTTP 状态码: 401"),
        )
        self.assertIn(
            "模型不存在",
            TranslatorConnectionTestWorker._format_test_result(False, "model_not_found"),
        )
        self.assertIn(
            "网络超时",
            TranslatorConnectionTestWorker._format_test_result(False, "Read timed out"),
        )

    def test_translator_save_status_marks_dirty_and_auto_saved(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator_save_status = QLabel("已保存")
        dummy._translator_save_status_token = 0
        calls = []
        dummy._remember_current_translator_model = lambda: True
        dummy._clear_completed_translator_required_field_highlights = lambda: None
        dummy._fill_model_name_combobox_items = lambda: None
        dummy._persist_translator_settings = lambda: calls.append("persist")

        SettingsDialog._mark_translator_settings_dirty(dummy)
        self.assertEqual(dummy._translator_save_status.text(), "未保存")

        SettingsDialog._apply_translator_settings(dummy)

        self.assertEqual(calls, ["persist"])
        self.assertEqual(dummy._translator_save_status.text(), "已自动保存")

    def test_fetch_models_adds_common_candidates_for_api_root(self):
        worker = FetchModelsWorker("https://api.example.com", "", False, "")

        self.assertEqual(
            worker._candidate_model_urls(),
            ["https://api.example.com", "https://api.example.com/v1/models", "https://api.example.com/models"],
        )

    def test_fetch_models_falls_back_after_html_root_response(self):
        worker = FetchModelsWorker("https://api.example.com", "", False, "")
        results = []
        urls = []
        worker.finished.connect(lambda success, models, message: results.append((success, models, message)))

        def fake_get(url, **kwargs):
            urls.append(url)
            if len(urls) == 1:
                return _Response(200, ValueError("html"), "<!DOCTYPE html>")
            return _Response(200, {"data": [{"id": "fallback-model"}]})

        with patch("requests.get", side_effect=fake_get):
            worker.run()

        self.assertEqual(urls, ["https://api.example.com", "https://api.example.com/v1/models"])
        self.assertEqual(results, [(True, ["fallback-model"], "")])

    def test_new_api_model_button_clears_all_fields(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator_api_url = _FakeField("https://api.example.com")
        dummy._translator_model_name = _FakeField("model-id")
        dummy._translator_model_note = _FakeField("note")
        dummy._translator_api_key = _FakeField("secret")
        dummy._translator_current_model = "note"
        dummy._model_id_selection_creates_config = True
        dummy._fetched_models_list = ["model-id"]
        dummy._fetched_models_base_url = "https://api.example.com"
        calls = []
        dummy._save_current_translator_model_config = lambda: calls.append("save")
        dummy._sync_translator_config_controls = lambda model_type: calls.append(("sync", model_type))
        dummy._fill_model_name_combobox_items = lambda: calls.append("fill")
        dummy._update_api_url_role_label = lambda: calls.append("role")
        dummy._position_translator_get_models_btn = lambda: calls.append("position")

        SettingsDialog._on_new_api_model_clicked(dummy)

        self.assertEqual(dummy._translator_api_url.text(), "")
        self.assertEqual(dummy._translator_model_name.text(), "")
        self.assertEqual(dummy._translator_model_note.text(), "")
        self.assertEqual(dummy._translator_api_key.text(), "")
        self.assertEqual(dummy._translator_current_model, "")
        self.assertFalse(dummy._model_id_selection_creates_config)
        self.assertEqual(dummy._fetched_models_list, [])
        self.assertEqual(dummy._fetched_models_base_url, "")
        self.assertTrue(dummy._translator_model_name.focused)
        self.assertEqual(calls[0], "save")

    def test_model_settings_initial_detail_uses_qa_model(self):
        self.assertEqual(
            SettingsDialog._initial_translator_detail_model(
                {"translate_model": "翻译模型", "qa_model": "问答模型", "current_model": "翻译模型"}
            ),
            "问答模型",
        )
        self.assertEqual(
            SettingsDialog._initial_translator_detail_model({"translate_model": "翻译模型"}),
            "gemini-3.5-flash-thinking",
        )
        self.assertEqual(
            SettingsDialog._initial_translator_detail_model({"qa_model": "自定义模型"}),
            "gemini-3.5-flash-thinking",
        )

    def test_fetch_models_starts_local_gemini_service_and_skips_empty_root_json(self):
        worker = FetchModelsWorker("http://127.0.0.1:8081", "123456", False, "")
        results = []
        urls = []
        worker.finished.connect(lambda success, models, message: results.append((success, models, message)))

        def fake_inprocess(method, url, **kwargs):
            urls.append(url)
            if len(urls) == 1:
                return _Response(200, {"status": "ok"})
            return _Response(200, {"data": [{"id": "gemini-3.5-flash-thinking"}]})

        with patch("deepcat.local_gemini_web2api_server.ensure_server") as ensure_server, \
             patch("deepcat.local_gemini_web2api_server.release_server") as release_server, \
             patch("deepcat.local_gemini_web2api_server.local_gemini_response", side_effect=fake_inprocess), \
             patch("requests.get") as requests_get:
            worker.run()

        ensure_server.assert_called_once()
        release_server.assert_called_once()
        # 内置 Gemini 服务走进程内直调，不再经过 requests
        requests_get.assert_not_called()
        self.assertEqual(urls, ["http://127.0.0.1:8081", "http://127.0.0.1:8081/v1/models"])
        self.assertEqual(results, [(True, ["gemini-3.5-flash-thinking"], "")])

    def test_fetch_models_starts_local_chatgpt_web2api_service(self):
        worker = FetchModelsWorker("http://127.0.0.1:8082", "cookie", False, "")
        results = []
        urls = []
        worker.finished.connect(lambda success, models, message: results.append((success, models, message)))

        def fake_get(url, **kwargs):
            urls.append(url)
            if len(urls) == 1:
                return _Response(200, {"status": "ok", "service": "chatgpt_web2api"})
            return _Response(200, {"data": [{"id": "gpt-5-3"}]})

        with patch("deepcat.local_chatgpt_web2api_server.ensure_server") as ensure_server, \
             patch("deepcat.local_chatgpt_web2api_server.release_server") as release_server, \
             patch("requests.get", side_effect=fake_get):
            worker.run()

        ensure_server.assert_called_once()
        release_server.assert_called_once()
        self.assertEqual(urls, ["http://127.0.0.1:8082", "http://127.0.0.1:8082/v1/models"])
        self.assertEqual(results, [(True, ["gpt-5-3"], "")])

    def test_connection_test_uses_local_gemini_health_check_without_chat_post(self):
        worker = TranslatorConnectionTestWorker(
            {
                "base_url": "http://127.0.0.1:8081",
                "model_name": "gemini-3.5-flash-thinking",
                "api_key": "123456",
                "model_type": "glm",
            },
            use_proxy=False,
            proxy_url="",
        )
        results = []
        worker.tested.connect(lambda success, message, elapsed: results.append((success, message)))

        with patch("deepcat.local_gemini_web2api_server.ensure_server") as ensure_server, \
             patch("deepcat.local_gemini_web2api_server.release_server") as release_server, \
             patch("requests.request") as requests_request:
            worker.run()

        ensure_server.assert_called_once()
        release_server.assert_called_once()
        # 内置 Gemini 服务走进程内直调：既不占用端口，也不发起任何 HTTP 请求
        requests_request.assert_not_called()
        self.assertEqual(results, [(True, "连通成功：模型可正常响应。")])

    def test_connection_test_uses_local_chatgpt_web2api_chat_post(self):
        worker = TranslatorConnectionTestWorker(
            {
                "base_url": "http://127.0.0.1:8082",
                "model_name": "gpt-5.5",
                "api_key": "cookie",
                "model_type": "chatgpt_web",
            },
            use_proxy=False,
            proxy_url="",
        )
        results = []
        urls = []
        payloads = []
        worker.tested.connect(lambda success, message, elapsed: results.append((success, message)))

        def fake_request(method, url, **kwargs):
            urls.append((method, url))
            if method == "POST":
                payloads.append(kwargs.get("json"))
            if method == "GET":
                return _Response(
                    200,
                    {
                        "status": "ok",
                        "service": "chatgpt_web2api",
                        "models": ["gpt-5-3", "gpt-5-5-thinking"],
                    },
                )
            return _Response(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "OK",
                            }
                        }
                    ]
                },
            )

        with patch("deepcat.local_chatgpt_web2api_server.ensure_server") as ensure_server, \
             patch("deepcat.local_chatgpt_web2api_server.release_server") as release_server, \
             patch("requests.request", side_effect=fake_request):
            worker.run()

        ensure_server.assert_called_once()
        release_server.assert_called_once()
        self.assertEqual(
            urls,
            [
                ("GET", "http://127.0.0.1:8082/"),
                ("POST", "http://127.0.0.1:8082/v1/chat/completions"),
            ],
        )
        self.assertEqual(results, [(True, "连通成功：模型可正常响应。")])
        self.assertEqual(payloads[0]["upstream_auth_timeout_sec"], 6)
        self.assertEqual(payloads[0]["upstream_warmup_timeout_sec"], 2)
        self.assertEqual(payloads[0]["upstream_timeout_sec"], 12)

    def test_fetch_models_401_prompts_for_correct_key(self):
        worker = FetchModelsWorker("https://api.example.com/models", "bad-key", False, "")
        results = []
        worker.finished.connect(lambda success, models, message: results.append((success, models, message)))

        with patch("requests.get", return_value=_Response(401)):
            worker.run()

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0][0])
        self.assertIn("HTTP 状态码: 401", results[0][2])
        self.assertIn("\n请提供正确密钥。", results[0][2])

    def test_model_id_candidates_only_use_fetched_api_models(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItem("Google Gemini")
        dummy._translator_provider.setCurrentText("Google Gemini")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator = {
            "model_configs": {
                "same-api": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "same-api-id",
                    "provider": "Google Gemini",
                },
                "模型别名": {
                    "base_url": "https://api.example.com/v1",
                    "provider": "Google Gemini",
                },
                "other-api": {
                    "base_url": "https://other.example.com/v1",
                    "model_name": "other-api-id",
                    "provider": "Google Gemini",
                },
            }
        }
        dummy._fetched_models_base_url = "https://api.example.com/v1"
        dummy._fetched_models_list = ["online-id"]
        dummy._normalized_api_url = SettingsDialog._normalized_api_url.__get__(dummy, type(dummy))

        SettingsDialog._fill_model_name_combobox_items(dummy)

        items = [dummy._translator_model_name.itemText(i) for i in range(dummy._translator_model_name.count())]
        self.assertEqual(items, ["online-id"])
        self.assertNotIn("same-api-id", items)
        self.assertNotIn("模型别名", items)
        self.assertNotIn("other-api-id", items)
        self.assertNotIn("gemini-1.5-flash", items)

    def test_configured_model_id_remains_visible_without_adding_to_dropdown(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.setEditable(False)
        dummy._translator_model_name.setDisplayText("saved-model-id")
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItem("Google Gemini")
        dummy._translator_provider.setCurrentText("Google Gemini")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator = {
            "model_configs": {
                "模型显示名": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "saved-model-id",
                    "provider": "Google Gemini",
                },
            }
        }
        dummy._fetched_models_base_url = ""
        dummy._fetched_models_list = []
        dummy._normalized_api_url = SettingsDialog._normalized_api_url.__get__(dummy, type(dummy))
        dummy._preferred_model_id_placeholder = SettingsDialog._preferred_model_id_placeholder.__get__(dummy, type(dummy))
        dummy._sync_model_id_combo_empty_state = SettingsDialog._sync_model_id_combo_empty_state.__get__(dummy, type(dummy))

        SettingsDialog._fill_model_name_combobox_items(dummy)

        self.assertEqual(dummy._translator_model_name.currentText(), "saved-model-id")
        self.assertEqual(dummy._translator_model_name.count(), 0)

    def test_online_model_selection_keeps_existing_note(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.addItem("online-id")
        dummy._translator_model_name.setCurrentText("online-id")
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("旧备注")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._fetched_models_base_url = "https://api.example.com/v1"
        dummy._fetched_models_list = ["online-id"]
        dummy._normalized_api_url = SettingsDialog._normalized_api_url.__get__(dummy, type(dummy))
        dummy._is_current_fetched_model_id = SettingsDialog._is_current_fetched_model_id.__get__(dummy, type(dummy))
        dummy._sync_model_note_from_fetched_model_id = SettingsDialog._sync_model_note_from_fetched_model_id.__get__(dummy, type(dummy))
        calls = []
        dummy._apply_translator_settings = lambda: calls.append("apply")

        SettingsDialog._on_model_id_activated(dummy)

        self.assertEqual(dummy._translator_model_note.text(), "旧备注")
        self.assertTrue(dummy._model_id_selection_creates_config)
        self.assertEqual(calls, ["apply"])

    def test_online_model_selection_fills_empty_note_with_model_id(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.addItem("online-id")
        dummy._translator_model_name.setCurrentText("online-id")
        dummy._translator_model_note = QLineEdit()
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._fetched_models_base_url = "https://api.example.com/v1"
        dummy._fetched_models_list = ["online-id"]
        dummy._model_id_selection_creates_config = False
        dummy._normalized_api_url = SettingsDialog._normalized_api_url.__get__(dummy, type(dummy))
        dummy._is_current_fetched_model_id = SettingsDialog._is_current_fetched_model_id.__get__(dummy, type(dummy))
        dummy._sync_model_note_from_fetched_model_id = SettingsDialog._sync_model_note_from_fetched_model_id.__get__(dummy, type(dummy))
        calls = []
        dummy._apply_translator_settings = lambda: calls.append("apply")

        SettingsDialog._on_model_id_activated(dummy)

        self.assertEqual(dummy._translator_model_note.text(), "online-id")
        self.assertTrue(dummy._model_id_selection_creates_config)
        self.assertEqual(calls, ["apply"])

    def test_online_model_selection_keeps_note_when_saved_identity_matches(self):
        dummy = type("Dummy", (), {})()
        dummy._translator = {
            "model_configs": {
                "当前备注": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "online-id",
                    "api_key": "same-key",
                },
            }
        }
        dummy._translator_current_model = "当前备注"
        dummy._translator_model_name = QLineEdit()
        dummy._translator_model_name.setText("online-id")
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("当前备注")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("same-key")
        dummy._fetched_models_base_url = "https://api.example.com/v1"
        dummy._fetched_models_list = ["online-id"]
        dummy._model_id_selection_creates_config = True
        dummy._normalized_api_url = SettingsDialog._normalized_api_url.__get__(dummy, type(dummy))
        dummy._is_current_fetched_model_id = SettingsDialog._is_current_fetched_model_id.__get__(dummy, type(dummy))
        calls = []
        dummy._apply_translator_settings = lambda: calls.append("apply")

        SettingsDialog._on_model_id_activated(dummy)

        self.assertEqual(dummy._translator_model_note.text(), "当前备注")
        self.assertFalse(dummy._model_id_selection_creates_config)
        self.assertEqual(calls, ["apply"])

    def test_online_model_selection_overwrites_note_when_same_api_key_but_new_model_id(self):
        dummy = type("Dummy", (), {})()
        dummy._translator = {
            "model_configs": {
                "当前备注": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "old-id",
                    "api_key": "same-key",
                },
            }
        }
        dummy._translator_current_model = "当前备注"
        dummy._translator_model_name = QLineEdit()
        dummy._translator_model_name.setText("new-id")
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("当前备注")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("same-key")
        dummy._fetched_models_base_url = "https://api.example.com/v1"
        dummy._fetched_models_list = ["new-id"]
        dummy._model_id_selection_creates_config = False
        dummy._normalized_api_url = SettingsDialog._normalized_api_url.__get__(dummy, type(dummy))
        dummy._is_current_fetched_model_id = SettingsDialog._is_current_fetched_model_id.__get__(dummy, type(dummy))
        calls = []
        dummy._apply_translator_settings = lambda: calls.append("apply")

        SettingsDialog._on_model_id_activated(dummy)

        self.assertEqual(dummy._translator_model_note.text(), "new-id")
        self.assertTrue(dummy._model_id_selection_creates_config)
        self.assertEqual(calls, ["apply"])

    def test_fetch_models_finished_keeps_model_id_empty_until_user_selects(self):
        dummy = type("Dummy", (), {})()
        dummy._get_models_btn = type(
            "Btn",
            (),
            {
                "setToolTip": lambda self, value: setattr(self, "tooltip", value),
                "setEnabled": lambda self, value: setattr(self, "enabled", bool(value)),
            },
        )()
        dummy._new_api_model_btn = type(
            "Btn",
            (),
            {"setEnabled": lambda self, value: setattr(self, "enabled", bool(value))},
        )()
        dummy._model_list_plus_btn = type(
            "Btn",
            (),
            {"setEnabled": lambda self, value: setattr(self, "enabled", bool(value))},
        )()
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.setEditable(False)
        dummy._translator = {"model_configs": {}}
        dummy._fetched_models_list = []
        dummy._fetched_models_base_url = ""
        dummy._normalized_api_url = SettingsDialog._normalized_api_url.__get__(dummy, type(dummy))
        dummy._preferred_model_id_placeholder = SettingsDialog._preferred_model_id_placeholder.__get__(dummy, type(dummy))
        dummy._sync_model_id_combo_empty_state = SettingsDialog._sync_model_id_combo_empty_state.__get__(dummy, type(dummy))
        dummy._fill_model_name_combobox_items = SettingsDialog._fill_model_name_combobox_items.__get__(dummy, type(dummy))

        with patch("deepcat.ui.settings_dialog.QMessageBox.information") as information:
            SettingsDialog._on_fetch_models_finished(dummy, True, ["model-a", "model-b"], "")

        information.assert_called_once()
        self.assertEqual(dummy._translator_model_name.currentText(), "")
        self.assertEqual(dummy._translator_model_name.currentIndex(), -1)
        self.assertEqual(dummy._translator_model_name.placeholderText(), "点击选择接口返回的模型 ID")
        self.assertEqual(
            [dummy._translator_model_name.itemText(i) for i in range(dummy._translator_model_name.count())],
            ["model-a", "model-b"],
        )

    def test_selecting_same_fetched_model_reuses_saved_config(self):
        dummy = type("Dummy", (), {})()
        dummy._translator = {
            "model_configs": {
                "当前备注": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "current-id",
                    "api_key": "same-key",
                },
                "已保存备注": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "reused-id",
                    "api_key": "same-key",
                },
            }
        }
        dummy._translator_current_model = "当前备注"
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("same-key")
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("当前备注")
        dummy._normalized_api_url = SettingsDialog._normalized_api_url.__get__(dummy, type(dummy))
        dummy._find_matching_saved_model_note = SettingsDialog._find_matching_saved_model_note.__get__(dummy, type(dummy))
        calls = []
        dummy._save_current_translator_model_config = lambda: calls.append("save_current")
        dummy._current_translator_role = lambda: "translate"
        dummy._on_translator_model_changed = lambda *args, **kwargs: calls.append(("change", args, kwargs))
        dummy._apply_translator_settings = lambda: calls.append("apply")

        SettingsDialog._handle_selected_model_id(dummy, "reused-id")

        self.assertFalse(dummy._model_id_selection_creates_config)
        self.assertIn("save_current", calls)
        self.assertIn(("change", ("已保存备注", "translate"), {"save_current": False}), calls)
        self.assertNotIn("apply", calls)

    def test_selecting_fetched_model_with_empty_key_keeps_existing_note(self):
        dummy = type("Dummy", (), {})()
        dummy._translator = {
            "model_configs": {
                "online-id": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "online-id",
                    "api_key": "",
                    "provider": "默认分组",
                },
                "online-id-A": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "online-id",
                    "api_key": "old-key",
                    "provider": "默认分组",
                },
            }
        }
        dummy._translator_current_model = "当前备注"
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("")
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("当前备注")
        dummy._fetched_models_list = ["online-id"]
        dummy._fetched_models_base_url = "https://api.example.com/v1"
        dummy._model_id_selection_creates_config = False
        dummy._normalized_api_url = SettingsDialog._normalized_api_url.__get__(dummy, type(dummy))
        dummy._is_current_fetched_model_id = SettingsDialog._is_current_fetched_model_id.__get__(dummy, type(dummy))
        calls = []
        dummy._on_translator_model_changed = lambda *args, **kwargs: calls.append(("change", args, kwargs))
        dummy._apply_translator_settings = lambda: calls.append("apply")

        SettingsDialog._handle_selected_model_id(dummy, "online-id")

        self.assertTrue(dummy._model_id_selection_creates_config)
        self.assertEqual(dummy._translator_model_name.text(), "online-id")
        self.assertEqual(dummy._translator_model_note.text(), "当前备注")
        self.assertEqual(calls, ["apply"])

    def test_selecting_fetched_model_with_new_key_reuses_empty_key_note(self):
        dummy = type("Dummy", (), {})()
        dummy._translator = {
            "model_configs": {
                "online-id": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "online-id",
                    "api_key": "",
                    "provider": "默认分组",
                },
                "online-id-A": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "online-id",
                    "api_key": "old-key",
                    "provider": "默认分组",
                },
            }
        }
        dummy._translator_current_model = "当前备注"
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("new-key")
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("当前备注")
        dummy._fetched_models_list = ["online-id"]
        dummy._fetched_models_base_url = "https://api.example.com/v1"
        dummy._model_id_selection_creates_config = False
        dummy._normalized_api_url = SettingsDialog._normalized_api_url.__get__(dummy, type(dummy))
        dummy._is_current_fetched_model_id = SettingsDialog._is_current_fetched_model_id.__get__(dummy, type(dummy))
        calls = []
        dummy._on_translator_model_changed = lambda *args, **kwargs: calls.append(("change", args, kwargs))
        dummy._apply_translator_settings = lambda: calls.append("apply")

        SettingsDialog._handle_selected_model_id(dummy, "online-id")

        self.assertTrue(dummy._model_id_selection_creates_config)
        self.assertEqual(dummy._translator_model_name.text(), "online-id")
        self.assertEqual(dummy._translator_model_note.text(), "当前备注")
        self.assertEqual(calls, ["apply"])

    def test_selecting_fetched_model_does_not_reuse_empty_key_note_from_other_provider(self):
        dummy = type("Dummy", (), {})()
        dummy._translator = {
            "model_configs": {
                "online-id": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "online-id",
                    "api_key": "",
                    "provider": "其它分组",
                },
            }
        }
        dummy._translator_current_model = "当前备注"
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("new-key")
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItem("当前分组")
        dummy._translator_provider.setCurrentText("当前分组")
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("当前备注")
        dummy._fetched_models_list = ["online-id"]
        dummy._fetched_models_base_url = "https://api.example.com/v1"
        dummy._model_id_selection_creates_config = False
        dummy._normalized_api_url = SettingsDialog._normalized_api_url.__get__(dummy, type(dummy))
        dummy._is_current_fetched_model_id = SettingsDialog._is_current_fetched_model_id.__get__(dummy, type(dummy))
        calls = []
        dummy._on_translator_model_changed = lambda *args, **kwargs: calls.append(("change", args, kwargs))
        dummy._apply_translator_settings = lambda: calls.append("apply")

        SettingsDialog._handle_selected_model_id(dummy, "online-id")

        self.assertTrue(dummy._model_id_selection_creates_config)
        self.assertEqual(dummy._translator_model_name.text(), "online-id")
        self.assertEqual(dummy._translator_model_note.text(), "当前备注")
        self.assertEqual(calls, ["apply"])

    def test_selecting_fetched_model_with_different_key_suffixes_current_duplicate_note(self):
        dummy = type("Dummy", (), {})()
        dummy._translator = {
            "model_configs": {
                "online-id": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "online-id",
                    "api_key": "old-key",
                    "provider": "默认分组",
                },
                "online-id-A": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "online-id",
                    "api_key": "another-key",
                    "provider": "默认分组",
                },
            }
        }
        dummy._translator_current_model = "online-id"
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("new-key")
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("online-id")
        dummy._fetched_models_list = ["online-id"]
        dummy._fetched_models_base_url = "https://api.example.com/v1"
        dummy._model_id_selection_creates_config = False
        dummy._normalized_api_url = SettingsDialog._normalized_api_url.__get__(dummy, type(dummy))
        dummy._is_current_fetched_model_id = SettingsDialog._is_current_fetched_model_id.__get__(dummy, type(dummy))
        calls = []
        dummy._on_translator_model_changed = lambda *args, **kwargs: calls.append(("change", args, kwargs))
        dummy._apply_translator_settings = lambda: calls.append("apply")

        SettingsDialog._handle_selected_model_id(dummy, "online-id")

        self.assertTrue(dummy._model_id_selection_creates_config)
        self.assertEqual(dummy._translator_model_name.text(), "online-id")
        self.assertEqual(dummy._translator_model_note.text(), "online-id")
        self.assertEqual(calls, ["apply"])

    def test_duplicate_model_note_name_gets_letter_suffix(self):
        existing = {"gpt-5.5": {}, "gpt-5.5-A": {}}

        self.assertEqual(
            SettingsDialog._unique_model_note_name("gpt-5.5", existing),
            "gpt-5.5-B",
        )
        self.assertEqual(
            SettingsDialog._unique_model_note_name("gpt-5.5", existing, current_name="gpt-5.5"),
            "gpt-5.5",
        )

    def test_add_model_auto_suffixes_duplicate_note_without_prompt(self):
        dummy = type("Dummy", (), {})()
        dummy._translator = {
            "model_configs": {
                "gpt-5.5": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "gpt-5.5",
                    "api_key": "old",
                    "use_proxy": False,
                    "provider": "默认分组",
                },
                "gpt-5.5-A": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "gpt-5.5",
                    "api_key": "old-a",
                    "use_proxy": False,
                    "provider": "默认分组",
                },
            }
        }
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItem("默认分组")
        dummy._translator_provider.setCurrentText("默认分组")
        dummy._translator_current_model = "gpt-5.5"
        dummy._current_translator_role = lambda: "translate"
        dummy._combo_selected_value = lambda combo, default="": "gpt-5.5"
        calls = []
        dummy._save_current_translator_model_config = lambda: calls.append("save")
        dummy._refresh_model_combos_after_catalog_change = lambda note, role: calls.append(("refresh", note, role))
        dummy._on_translator_model_changed = lambda note, role: calls.append(("change", note, role))
        dummy._load_translator_model_config = lambda note: calls.append(("load", note))

        with patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("gpt-5.5", True)), \
             patch("deepcat.ui.settings_dialog.QMessageBox.warning") as warning:
            SettingsDialog._on_add_model_clicked(dummy)

        warning.assert_not_called()
        configs = dummy._translator["model_configs"]
        self.assertIn("gpt-5.5-B", configs)
        self.assertEqual(configs["gpt-5.5-B"]["model_name"], "gpt-5.5")
        self.assertIn(("refresh", "gpt-5.5-B", "translate"), calls)
        self.assertIn(("change", "gpt-5.5-B", "translate"), calls)

    def test_api_key_change_same_api_and_model_creates_suffixed_note(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator_current_role = "translate"
        dummy._translator_current_model = "ChatGPT Web 生图"
        dummy._translator = {
            "translate_model": "ChatGPT Web 生图",
            "qa_model": "问答模型",
            "current_model": "ChatGPT Web 生图",
            "removed_models": [],
            "proxy_url": "socks5://127.0.0.1:1080",
            "model_configs": {
                "ChatGPT Web 生图": {
                    "base_url": "http://127.0.0.1:8082/v1/images/generations",
                    "model_name": "gpt-image-2",
                    "api_key": "old-key",
                    "use_proxy": False,
                    "model_type": "openai_images",
                    "provider": "ChatGPT Web",
                },
                "问答模型": {
                    "base_url": "http://127.0.0.1:8082",
                    "model_name": "gpt-5",
                    "api_key": "qa-key",
                    "use_proxy": False,
                    "model_type": "chatgpt_web",
                    "provider": "ChatGPT Web",
                },
            },
        }
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.setText("gpt-image-2")
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("ChatGPT Web 生图")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("http://127.0.0.1:8082/v1/images/generations")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("new-key")
        dummy._translator_proxy_url = QLineEdit()
        dummy._translator_proxy_url.setText("socks5://127.0.0.1:1080")
        dummy._translator_use_proxy = QCheckBox()
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItem("ChatGPT Web")
        dummy._translator_provider.setCurrentText("ChatGPT Web")
        dummy._model_id_selection_creates_config = False
        dummy._normalized_api_url = SettingsDialog._normalized_api_url.__get__(dummy, type(dummy))
        dummy._translator_field_config = SettingsDialog._translator_field_config.__get__(dummy, type(dummy))
        dummy._translator_required_fields = SettingsDialog._translator_required_fields.__get__(dummy, type(dummy))
        dummy._translator_fields_complete = SettingsDialog._translator_fields_complete.__get__(dummy, type(dummy))
        dummy._combo_selected_value = SettingsDialog._combo_selected_value.__get__(dummy, type(dummy))
        dummy._set_combo_selected_value = SettingsDialog._set_combo_selected_value.__get__(dummy, type(dummy))
        dummy._fill_model_combos = SettingsDialog._fill_model_combos.__get__(dummy, type(dummy))
        dummy._sync_role_models_from_combos = SettingsDialog._sync_role_models_from_combos.__get__(dummy, type(dummy))
        dummy._model_combos = SettingsDialog._model_combos.__get__(dummy, type(dummy))
        dummy._current_translator_role = SettingsDialog._current_translator_role.__get__(dummy, type(dummy))
        dummy._refresh_model_combos_after_catalog_change = SettingsDialog._refresh_model_combos_after_catalog_change.__get__(dummy, type(dummy))
        dummy._should_clone_model_config_for_api_key_change = (
            SettingsDialog._should_clone_model_config_for_api_key_change.__get__(dummy, type(dummy))
        )
        dummy._save_current_translator_model_config = SettingsDialog._save_current_translator_model_config.__get__(dummy, type(dummy))
        dummy._remember_current_translator_model = SettingsDialog._remember_current_translator_model.__get__(dummy, type(dummy))
        dummy._fill_model_name_combobox_items = lambda: None
        dummy._update_api_url_role_label = lambda: None
        dummy._fill_model_combos("ChatGPT Web 生图", "问答模型")

        changed = SettingsDialog._remember_current_translator_model(dummy)

        configs = dummy._translator["model_configs"]
        self.assertTrue(changed)
        self.assertEqual(configs["ChatGPT Web 生图"]["api_key"], "old-key")
        self.assertIn("ChatGPT Web 生图-A", configs)
        self.assertEqual(configs["ChatGPT Web 生图-A"]["api_key"], "new-key")
        self.assertEqual(configs["ChatGPT Web 生图-A"]["model_name"], "gpt-image-2")
        self.assertEqual(dummy._translator_model_note.text(), "ChatGPT Web 生图-A")
        self.assertEqual(dummy._translator_current_model, "ChatGPT Web 生图-A")
        self.assertEqual(dummy._translator["translate_model"], "ChatGPT Web 生图-A")
        self.assertEqual(dummy._translator["qa_model"], "问答模型")

    def test_api_key_change_updates_empty_key_model_without_suffix(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator_current_role = "translate"
        dummy._translator_current_model = "Google Gemini"
        dummy._translator = {
            "translate_model": "Google Gemini",
            "qa_model": "问答模型",
            "current_model": "Google Gemini",
            "removed_models": [],
            "proxy_url": "socks5://127.0.0.1:1080",
            "model_configs": {
                "Google Gemini": {
                    "base_url": "https://generativelanguage.googleapis.com/v1beta",
                    "model_name": "gemini-3.5-flash",
                    "api_key": "",
                    "use_proxy": False,
                    "model_type": "glm",
                    "provider": "Google Gemini",
                },
                "问答模型": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "qa-id",
                    "api_key": "qa-key",
                    "use_proxy": False,
                    "model_type": "glm",
                    "provider": "默认分组",
                },
            },
        }
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.setText("gemini-3.5-flash")
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("Google Gemini")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://generativelanguage.googleapis.com/v1beta")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("new-google-key")
        dummy._translator_use_proxy = QCheckBox()
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItem("Google Gemini")
        dummy._translator_provider.setCurrentText("Google Gemini")
        dummy._model_id_selection_creates_config = False
        dummy._translator_field_config = SettingsDialog._translator_field_config.__get__(dummy, type(dummy))
        dummy._combo_selected_value = SettingsDialog._combo_selected_value.__get__(dummy, type(dummy))
        dummy._set_combo_selected_value = SettingsDialog._set_combo_selected_value.__get__(dummy, type(dummy))
        dummy._fill_model_combos = SettingsDialog._fill_model_combos.__get__(dummy, type(dummy))
        dummy._sync_role_models_from_combos = SettingsDialog._sync_role_models_from_combos.__get__(dummy, type(dummy))
        dummy._model_combos = SettingsDialog._model_combos.__get__(dummy, type(dummy))
        dummy._current_translator_role = SettingsDialog._current_translator_role.__get__(dummy, type(dummy))
        dummy._refresh_model_combos_after_catalog_change = SettingsDialog._refresh_model_combos_after_catalog_change.__get__(dummy, type(dummy))
        dummy._fill_model_name_combobox_items = lambda: None
        dummy._fill_model_combos("Google Gemini", "问答模型")

        SettingsDialog._save_current_translator_model_config(dummy)

        configs = dummy._translator["model_configs"]
        self.assertIn("Google Gemini", configs)
        self.assertNotIn("Google Gemini-A", configs)
        self.assertEqual(configs["Google Gemini"]["api_key"], "new-google-key")
        self.assertEqual(configs["Google Gemini"]["model_name"], "gemini-3.5-flash")
        self.assertEqual(dummy._translator_current_model, "Google Gemini")
        self.assertEqual(dummy._translator["translate_model"], "Google Gemini")
        self.assertEqual(dummy._translator["qa_model"], "问答模型")

    def test_api_key_change_reuses_existing_same_api_model_and_key_note(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator_current_role = "translate"
        dummy._translator_current_model = "旧生图"
        dummy._translator = {
            "translate_model": "旧生图",
            "qa_model": "问答模型",
            "current_model": "旧生图",
            "removed_models": [],
            "proxy_url": "socks5://127.0.0.1:1080",
            "model_configs": {
                "旧生图": {
                    "base_url": "http://127.0.0.1:8082/v1/images/generations",
                    "model_name": "gpt-image-2",
                    "api_key": "old-key",
                    "provider": "ChatGPT Web",
                },
                "已有新密钥生图": {
                    "base_url": "http://127.0.0.1:8082/v1/images/generations",
                    "model_name": "gpt-image-2",
                    "api_key": "new-key",
                    "provider": "ChatGPT Web",
                },
                "问答模型": {
                    "base_url": "http://127.0.0.1:8082",
                    "model_name": "gpt-5",
                    "api_key": "qa-key",
                    "provider": "ChatGPT Web",
                },
            },
        }
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.setText("gpt-image-2")
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("旧生图")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("http://127.0.0.1:8082/v1/images/generations")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("new-key")
        dummy._translator_proxy_url = QLineEdit()
        dummy._translator_proxy_url.setText("socks5://127.0.0.1:1080")
        dummy._translator_use_proxy = QCheckBox()
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItem("ChatGPT Web")
        dummy._translator_provider.setCurrentText("ChatGPT Web")
        dummy._model_id_selection_creates_config = False
        dummy._translator_field_config = SettingsDialog._translator_field_config.__get__(dummy, type(dummy))
        dummy._translator_required_fields = SettingsDialog._translator_required_fields.__get__(dummy, type(dummy))
        dummy._translator_fields_complete = SettingsDialog._translator_fields_complete.__get__(dummy, type(dummy))
        dummy._combo_selected_value = SettingsDialog._combo_selected_value.__get__(dummy, type(dummy))
        dummy._set_combo_selected_value = SettingsDialog._set_combo_selected_value.__get__(dummy, type(dummy))
        dummy._fill_model_combos = SettingsDialog._fill_model_combos.__get__(dummy, type(dummy))
        dummy._sync_role_models_from_combos = SettingsDialog._sync_role_models_from_combos.__get__(dummy, type(dummy))
        dummy._model_combos = SettingsDialog._model_combos.__get__(dummy, type(dummy))
        dummy._current_translator_role = SettingsDialog._current_translator_role.__get__(dummy, type(dummy))
        dummy._refresh_model_combos_after_catalog_change = SettingsDialog._refresh_model_combos_after_catalog_change.__get__(dummy, type(dummy))
        dummy._remember_current_translator_model = SettingsDialog._remember_current_translator_model.__get__(dummy, type(dummy))
        dummy._repaint_model_combos = lambda: None
        dummy._fill_model_name_combobox_items = lambda: None
        dummy._update_api_url_role_label = lambda: None
        dummy._load_translator_model_config = lambda note: dummy._translator_model_note.setText(note)
        dummy._fill_model_combos("旧生图", "问答模型")

        changed = SettingsDialog._remember_current_translator_model(dummy)

        configs = dummy._translator["model_configs"]
        self.assertTrue(changed)
        self.assertNotIn("旧生图-A", configs)
        self.assertEqual(configs["旧生图"]["api_key"], "old-key")
        self.assertEqual(dummy._translator_current_model, "已有新密钥生图")
        self.assertEqual(dummy._translator_model_note.text(), "已有新密钥生图")
        self.assertEqual(dummy._translator["translate_model"], "已有新密钥生图")
        self.assertEqual(dummy._translator["qa_model"], "问答模型")

    def test_add_model_keeps_current_api_fields(self):
        dummy = type("Dummy", (), {})()
        dummy._translator = {
            "model_configs": {
                "当前模型": {
                    "base_url": "https://old.example.com/v1",
                    "model_name": "old-id",
                    "api_key": "old-key",
                    "use_proxy": False,
                    "provider": "默认分组",
                },
            }
        }
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItem("OpenAI")
        dummy._translator_provider.setCurrentText("OpenAI")
        dummy._translator_current_model = "当前模型"
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.openai.com/v1/responses")
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.setText("old-id")
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("当前模型")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("fresh-key")
        dummy._translator_use_proxy = QCheckBox()
        dummy._translator_field_config = SettingsDialog._translator_field_config.__get__(dummy, type(dummy))
        dummy._current_translator_role = lambda: "translate"
        dummy._combo_selected_value = lambda combo, default="": "当前模型"
        dummy._save_current_translator_model_config = lambda: None
        dummy._refresh_model_combos_after_catalog_change = lambda note, role: None
        dummy._on_translator_model_changed = lambda note, role: None
        dummy._load_translator_model_config = lambda note: None

        with patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("gpt-5.1", True)):
            SettingsDialog._on_add_model_clicked(dummy)

        cfg = dummy._translator["model_configs"]["gpt-5.1"]
        self.assertEqual(cfg["base_url"], "https://api.openai.com/v1/responses")
        self.assertEqual(cfg["api_key"], "fresh-key")
        self.assertEqual(cfg["model_name"], "gpt-5.1")
        self.assertEqual(cfg["model_type"], "openai_responses")

    def test_fetched_model_id_selection_adds_config_without_removing_old_one(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator_current_role = "translate"
        dummy._translator_current_model = "旧备注"
        dummy._translator = {
            "translate_model": "旧备注",
            "qa_model": "问答备注",
            "current_model": "旧备注",
            "removed_models": [],
            "model_configs": {
                "旧备注": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "old-id",
                    "api_key": "key",
                    "use_proxy": False,
                    "model_type": "glm",
                    "provider": "默认分组",
                },
                "问答备注": {
                    "base_url": "https://qa.example.com/v1",
                    "model_name": "qa-id",
                    "api_key": "key",
                    "use_proxy": False,
                    "model_type": "glm",
                    "provider": "默认分组",
                },
            },
        }
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.setText("new-id")
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("new-id")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("key")
        dummy._translator_use_proxy = QCheckBox()
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItem("默认分组")
        dummy._translator_provider.setCurrentText("默认分组")
        dummy._model_id_selection_creates_config = True
        dummy._translator_field_config = SettingsDialog._translator_field_config.__get__(dummy, type(dummy))
        dummy._combo_selected_value = SettingsDialog._combo_selected_value.__get__(dummy, type(dummy))
        dummy._set_combo_selected_value = SettingsDialog._set_combo_selected_value.__get__(dummy, type(dummy))
        dummy._fill_model_combos = SettingsDialog._fill_model_combos.__get__(dummy, type(dummy))
        dummy._sync_role_models_from_combos = SettingsDialog._sync_role_models_from_combos.__get__(dummy, type(dummy))
        dummy._current_translator_role = SettingsDialog._current_translator_role.__get__(dummy, type(dummy))
        dummy._fill_model_name_combobox_items = lambda: None
        dummy._fill_model_combos("旧备注", "问答备注")

        SettingsDialog._save_current_translator_model_config(dummy)

        configs = dummy._translator["model_configs"]
        self.assertIn("旧备注", configs)
        self.assertIn("new-id", configs)
        self.assertEqual(configs["new-id"]["model_name"], "new-id")
        self.assertEqual(dummy._translator["translate_model"], "new-id")
        self.assertEqual(dummy._translator["qa_model"], "问答备注")
        combo_values = {
            str(dummy._translator_model.itemData(i, Qt.ItemDataRole.UserRole) or "").strip()
            for i in range(dummy._translator_model.count())
        }
        self.assertIn("旧备注", combo_values)
        self.assertIn("new-id", combo_values)

    def test_note_only_rename_refreshes_translate_and_qa_combos(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator_current_role = "translate"
        dummy._translator_current_model = "旧备注"
        dummy._translator = {
            "translate_model": "旧备注",
            "qa_model": "旧备注",
            "current_model": "旧备注",
            "removed_models": [],
            "model_configs": {
                "旧备注": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "same-id",
                    "api_key": "key",
                    "use_proxy": False,
                    "model_type": "glm",
                    "provider": "默认分组",
                },
            },
        }
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.setText("same-id")
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("新备注")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("key")
        dummy._translator_use_proxy = QCheckBox()
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItem("默认分组")
        dummy._translator_provider.setCurrentText("默认分组")
        dummy._model_id_selection_creates_config = False
        dummy._translator_field_config = SettingsDialog._translator_field_config.__get__(dummy, type(dummy))
        dummy._combo_selected_value = SettingsDialog._combo_selected_value.__get__(dummy, type(dummy))
        dummy._set_combo_selected_value = SettingsDialog._set_combo_selected_value.__get__(dummy, type(dummy))
        dummy._fill_model_combos = SettingsDialog._fill_model_combos.__get__(dummy, type(dummy))
        dummy._sync_role_models_from_combos = SettingsDialog._sync_role_models_from_combos.__get__(dummy, type(dummy))
        dummy._current_translator_role = SettingsDialog._current_translator_role.__get__(dummy, type(dummy))
        dummy._fill_model_name_combobox_items = lambda: None
        dummy._broadcast_model_rename = lambda old_note, new_note, cfg: None
        dummy._fill_model_combos("旧备注", "旧备注")

        SettingsDialog._save_current_translator_model_config(dummy)

        self.assertNotIn("旧备注", dummy._translator["model_configs"])
        self.assertIn("新备注", dummy._translator["model_configs"])
        self.assertEqual(dummy._translator["translate_model"], "新备注")
        self.assertEqual(dummy._translator["qa_model"], "新备注")
        self.assertEqual(dummy._combo_selected_value(dummy._translator_model), "新备注")
        self.assertEqual(dummy._combo_selected_value(dummy._qa_model), "新备注")

    def test_model_role_combo_search_index_includes_config_fields(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator = {
            "model_configs": {
                "模型别名A": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "actual-model-id",
                    "api_key": "secret-key-123",
                    "model_type": "glm",
                    "provider": "AI分组",
                },
            }
        }
        dummy._combo_selected_value = SettingsDialog._combo_selected_value.__get__(dummy, type(dummy))
        dummy._set_combo_selected_value = SettingsDialog._set_combo_selected_value.__get__(dummy, type(dummy))

        SettingsDialog._fill_model_combos(dummy, "模型别名A", "模型别名A")

        search_text = ""
        for idx in range(dummy._translator_model.model().rowCount()):
            item = dummy._translator_model.model().item(idx)
            if item is not None and item.text() == "模型别名A":
                search_text = str(item.data(TRANSLATOR_MODEL_SEARCH_ROLE) or "")
                break

        self.assertIn("模型别名A", search_text)
        self.assertIn("https://api.example.com/v1", search_text)
        self.assertIn("actual-model-id", search_text)
        self.assertIn("secret-key-123", search_text)
        self.assertIn("AI分组", search_text)

    def test_fill_model_combos_marks_codex_bound_qa_model_blue(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator = {
            "model_configs": {
                "模型A": {"provider": "默认分组", "model_name": "model-a"},
                "模型B": {"provider": "默认分组", "model_name": "model-b"},
            },
            "qa_model": "模型A",
            "codex_current_model_config_enabled": True,
            "codex_current_model_config_model": "模型A",
        }
        dummy._combo_selected_value = SettingsDialog._combo_selected_value.__get__(dummy, type(dummy))
        dummy._set_combo_selected_value = SettingsDialog._set_combo_selected_value.__get__(dummy, type(dummy))
        dummy._codex_highlighted_model_names = SettingsDialog._codex_highlighted_model_names.__get__(dummy, type(dummy))
        dummy._apply_codex_highlight_to_item_model = SettingsDialog._apply_codex_highlight_to_item_model.__get__(dummy, type(dummy))

        SettingsDialog._fill_model_combos(dummy, "模型A", "模型A")

        qa_item = None
        translate_item = None
        for idx in range(dummy._qa_model.model().rowCount()):
            item = dummy._qa_model.model().item(idx)
            if item is not None and item.text() == "模型A":
                qa_item = item
                break
        for idx in range(dummy._translator_model.model().rowCount()):
            item = dummy._translator_model.model().item(idx)
            if item is not None and item.text() == "模型A":
                translate_item = item
                break

        self.assertIsNotNone(qa_item)
        self.assertIsNotNone(translate_item)
        self.assertTrue(bool(qa_item.data(TRANSLATOR_MODEL_CODEX_BOUND_ROLE)))
        self.assertEqual(qa_item.data(Qt.ItemDataRole.ForegroundRole).color().name(), CODEX_CURRENT_MODEL_CHECK_COLOR)
        self.assertFalse(bool(translate_item.data(TRANSLATOR_MODEL_CODEX_BOUND_ROLE)))
        self.assertIsNone(translate_item.data(Qt.ItemDataRole.ForegroundRole))

    def test_translator_model_popup_passes_codex_highlighted_models(self):
        combo = TranslatorModelComboBox()
        combo.setProperty("purpose", "qa")
        model = QStandardItemModel(combo)
        header = QStandardItem("[默认分组]")
        header.setEnabled(False)
        model.appendRow(header)
        item = QStandardItem("模型A")
        item.setData("模型A", Qt.ItemDataRole.UserRole)
        item.setData(True, TRANSLATOR_MODEL_CODEX_BOUND_ROLE)
        model.appendRow(item)
        combo.setModel(model)

        captured = {}

        class _FakePopup:
            def __init__(self, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs

            def show_at(self, *_args):
                captured["shown"] = True

        with patch("deepcat.ui.settings_dialog.field_widgets._OcrModelMenuPopup", _FakePopup):
            combo.showPopup()

        self.assertEqual(captured["kwargs"]["highlighted_models"], {"模型A"})
        self.assertEqual(captured["kwargs"]["highlighted_text_color"], CODEX_CURRENT_MODEL_CHECK_COLOR)
        self.assertIsNone(captured["kwargs"]["on_codex_action"])
        self.assertNotIn("show_group_move_action", captured["kwargs"])
        self.assertEqual(captured["kwargs"]["codex_action_label_for_model"]("模型A"), "还原Codex")
        self.assertEqual(captured["kwargs"]["codex_action_label_for_model"]("模型B"), "填入Codex")
        self.assertTrue(captured.get("shown", False))

    def test_model_popup_group_headers_hide_move_action_by_default(self):
        from deepcat.ui.post_capture_actions import _OcrModelMenuPopup

        class _ProviderDialog(QWidget):
            def _move_current_model_to_provider(self, provider):
                raise AssertionError(f"不应移动到分组: {provider}")

        parent = _ProviderDialog()
        popup = _OcrModelMenuPopup(
            [("ChatGPT Web", ["模型A"])],
            "模型A",
            lambda model_name: None,
            parent=parent,
        )

        group_move_buttons = [
            widget
            for widget in popup.findChildren(QWidget)
            if widget.property("group_move_button") is not None
        ]

        self.assertEqual(group_move_buttons, [])

    def test_move_current_model_to_provider_refreshes_grouped_model_combos(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator_current_role = "translate"
        dummy._translator_current_model = "当前模型"
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItems(["旧分组", "新分组"])
        dummy._translator_provider.setCurrentText("新分组")
        dummy._translator = {
            "translate_model": "当前模型",
            "qa_model": "当前模型",
            "current_model": "当前模型",
            "removed_models": [],
            "model_configs": {
                "当前模型": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "current-id",
                    "api_key": "key",
                    "use_proxy": False,
                    "model_type": "glm",
                    "provider": "旧分组",
                },
                "新分组第一个模型": {
                    "base_url": "https://other.example.com/v1",
                    "model_name": "other-id",
                    "api_key": "key",
                    "use_proxy": False,
                    "model_type": "glm",
                    "provider": "新分组",
                },
            }
        }
        calls = []
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.setText("current-id")
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("当前模型")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("key")
        dummy._translator_proxy_url = QLineEdit()
        dummy._translator_proxy_url.setText("socks5://127.0.0.1:1080")
        dummy._translator_use_proxy = QCheckBox()
        dummy._model_id_selection_creates_config = False
        dummy._translator_field_config = SettingsDialog._translator_field_config.__get__(dummy, type(dummy))
        dummy._combo_selected_value = SettingsDialog._combo_selected_value.__get__(dummy, type(dummy))
        dummy._set_combo_selected_value = SettingsDialog._set_combo_selected_value.__get__(dummy, type(dummy))
        dummy._fill_model_combos = SettingsDialog._fill_model_combos.__get__(dummy, type(dummy))
        dummy._refresh_model_combos_after_catalog_change = SettingsDialog._refresh_model_combos_after_catalog_change.__get__(dummy, type(dummy))
        dummy._repaint_model_combos = SettingsDialog._repaint_model_combos.__get__(dummy, type(dummy))
        dummy._sync_role_models_from_combos = SettingsDialog._sync_role_models_from_combos.__get__(dummy, type(dummy))
        dummy._model_combos = SettingsDialog._model_combos.__get__(dummy, type(dummy))
        dummy._current_translator_role = SettingsDialog._current_translator_role.__get__(dummy, type(dummy))
        dummy._remember_current_translator_model = SettingsDialog._remember_current_translator_model.__get__(dummy, type(dummy))
        dummy._save_current_translator_model_config = SettingsDialog._save_current_translator_model_config.__get__(dummy, type(dummy))
        dummy._set_translator_provider_value = SettingsDialog._set_translator_provider_value.__get__(dummy, type(dummy))
        dummy._move_current_model_to_provider = SettingsDialog._move_current_model_to_provider.__get__(dummy, type(dummy))
        dummy._update_api_url_role_label = lambda: None
        dummy._fill_model_name_combobox_items = lambda: calls.append(("fill",))
        dummy._persist_translator_settings = lambda: calls.append(("persist",))
        dummy._on_translator_model_changed = lambda *args, **kwargs: calls.append(("unexpected_change", args, kwargs))
        dummy._fill_model_combos("当前模型", "当前模型")

        SettingsDialog._move_current_model_to_provider(dummy, "新分组")

        self.assertEqual(dummy._translator["model_configs"]["当前模型"]["provider"], "新分组")
        self.assertEqual(dummy._translator["translate_model"], "当前模型")
        self.assertEqual(dummy._translator["qa_model"], "当前模型")
        self.assertEqual(dummy._translator_provider.currentText(), "新分组")
        translate_items = [dummy._translator_model.itemText(i) for i in range(dummy._translator_model.count())]
        qa_items = [dummy._qa_model.itemText(i) for i in range(dummy._qa_model.count())]
        self.assertIn("[新分组]", translate_items)
        self.assertIn("[新分组]", qa_items)
        self.assertNotIn("[旧分组]", translate_items)
        self.assertNotIn("[旧分组]", qa_items)
        self.assertNotIn("unexpected_change", [call[0] for call in calls])
        self.assertIn(("persist",), calls)

    def test_provider_popup_models_by_provider_lists_group_models(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator = {
            "model_configs": {
                "Google翻译": {"provider": "Google", "model_name": "google-free"},
                "模型A": {"provider": "ChatGPT Web", "model_name": "model-a"},
                "模型B": {"provider": "ChatGPT Web", "model_name": "model-b"},
            }
        }
        dummy._combo_selected_value = SettingsDialog._combo_selected_value.__get__(dummy, type(dummy))
        dummy._set_combo_selected_value = SettingsDialog._set_combo_selected_value.__get__(dummy, type(dummy))
        dummy._codex_highlighted_model_names = SettingsDialog._codex_highlighted_model_names.__get__(dummy, type(dummy))
        dummy._apply_codex_highlight_to_item_model = SettingsDialog._apply_codex_highlight_to_item_model.__get__(dummy, type(dummy))

        SettingsDialog._fill_model_combos(dummy, "模型A", "模型A")

        translate_grouped = SettingsDialog._provider_popup_models_by_provider(dummy, "translate")
        qa_grouped = SettingsDialog._provider_popup_models_by_provider(dummy, "qa")

        self.assertEqual(translate_grouped["ChatGPT Web"], ["模型A", "模型B"])
        self.assertEqual(translate_grouped["免费翻译"], ["Google翻译"])
        self.assertEqual(qa_grouped["ChatGPT Web"], ["模型A", "模型B"])
        self.assertNotIn("免费翻译", qa_grouped)

    def test_provider_popup_models_by_provider_uses_visible_role_combo_groups_first(self):
        dummy = type("Dummy", (), {})()
        dummy._translator = {"model_configs": {}}
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        role_model = QStandardItemModel(dummy._qa_model)
        header = QStandardItem("[ChatGPT Web]")
        header.setEnabled(False)
        role_model.appendRow(header)
        item = QStandardItem("模型A")
        item.setData("模型A", Qt.ItemDataRole.UserRole)
        role_model.appendRow(item)
        dummy._qa_model.setModel(role_model)

        grouped = SettingsDialog._provider_popup_models_by_provider(dummy, "qa")

        self.assertEqual(grouped, {"ChatGPT Web": ["模型A"]})

    def test_refresh_translator_providers_caches_group_models_on_combo(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_provider = ModernPopupComboBox()
        dummy._translator = {
            "model_configs": {
                "Google翻译": {"provider": "Google", "model_name": "google-free"},
                "模型A": {"provider": "ChatGPT Web", "model_name": "model-a"},
                "模型B": {"provider": "公益组", "model_name": "model-b"},
            },
            "translate_model": "模型B",
            "qa_model": "模型A",
        }
        dummy._sync_provider_popup_model_cache = SettingsDialog._sync_provider_popup_model_cache.__get__(dummy, type(dummy))
        dummy._apply_current_model_codex_config_for_model = lambda model_name: None
        dummy._codex_action_label_for_model = lambda model_name: "填入Codex"

        SettingsDialog._refresh_translator_providers(dummy)

        cached_models = dummy._translator_provider.property("providerModelsByProvider")
        cached_current = dummy._translator_provider.property("providerCurrentModels")
        cached_highlighted = dummy._translator_provider.property("providerHighlightedModels")
        self.assertEqual(cached_models["translate"]["ChatGPT Web"], ["模型A"])
        self.assertEqual(cached_models["translate"]["公益组"], ["模型B"])
        self.assertNotIn("免费翻译", cached_models["qa"])
        self.assertEqual(cached_current, {"translate": "模型B", "qa": "模型A"})
        self.assertEqual(cached_highlighted, [])
        self.assertTrue(callable(getattr(dummy._translator_provider, "_codex_action_handler", None)))
        self.assertTrue(callable(getattr(dummy._translator_provider, "_codex_label_handler", None)))

    def test_refresh_translator_providers_includes_claude_code_default_group(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_provider = ModernPopupComboBox()
        dummy._translator = {"model_configs": {}, "provider_remarks": {}}
        dummy._sync_provider_popup_model_cache = lambda: None

        SettingsDialog._refresh_translator_providers(dummy)

        providers = [dummy._translator_provider.itemText(i) for i in range(dummy._translator_provider.count())]
        self.assertIn("Claude Code", providers)

    def test_provider_popup_model_cache_includes_codex_highlighted_model(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_provider = ModernPopupComboBox()
        dummy._translator = {
            "model_configs": {
                "模型A": {"provider": "ChatGPT Web", "model_name": "model-a"},
            },
            "qa_model": "模型A",
            "codex_current_model_config_enabled": True,
            "codex_current_model_config_model": "模型A",
        }

        SettingsDialog._sync_provider_popup_model_cache(dummy)

        self.assertEqual(dummy._translator_provider.property("providerHighlightedModels"), ["模型A"])

    def test_provider_popup_model_selection_switches_current_role_model(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        calls = []
        dummy._current_translator_role = lambda: "qa"
        dummy._on_translator_model_changed = lambda *args, **kwargs: calls.append((args, kwargs))

        SettingsDialog._on_provider_popup_model_selected(dummy, "模型B")

        self.assertEqual(calls, [(("模型B", "qa"), {})])

    def test_provider_combo_popup_includes_group_models_and_keeps_provider_click_logic(self):
        class _ProviderDialog(QWidget):
            def __init__(self):
                super().__init__()
                self.calls = []
                self._translator = {
                    "provider_remarks": {
                        "ChatGPT Web": "备注",
                    }
                }

            def _current_translator_role(self):
                return "qa"

            def _provider_popup_models_by_provider(self, role=""):
                self.calls.append(("grouped", role))
                return {
                    "默认分组": ["模型C"],
                    "ChatGPT Web": ["模型A", "模型B"],
                    "Claude Code": ["模型CC"],
                }

            def _provider_popup_current_model(self, role=""):
                self.calls.append(("current_model", role))
                return "模型B"

            def _codex_highlighted_model_names(self, role=""):
                self.calls.append(("codex_highlight", role))
                return {"模型B"}

            def _provider_highlighted_model_colors(self, role=""):
                self.calls.append(("highlight_colors", role))
                return {"模型B": CODEX_CURRENT_MODEL_CHECK_COLOR, "模型CC": CLAUDE_CODE_CURRENT_MODEL_CHECK_COLOR}

            def _on_provider_popup_model_selected(self, model_name):
                self.calls.append(("model", model_name))

            def _apply_current_model_codex_config_for_model(self, model_name):
                self.calls.append(("codex", model_name))

            def _codex_action_label_for_model(self, model_name):
                return "还原Codex" if model_name == "模型B" else "填入Codex"

            def _apply_current_model_claude_code_config_for_model(self, model_name):
                self.calls.append(("cc", model_name))

            def _claude_code_action_label_for_model(self, model_name):
                return "填入CC"

            def _delete_translator_model(self, model_name, role="translate"):
                self.calls.append(("delete_model", model_name, role))

            def _delete_translator_models(self, model_names, role="translate"):
                self.calls.append(("delete_models", list(model_names), role))

            def _move_current_model_to_provider(self, provider):
                self.calls.append(("move", provider))

            def delete_provider_group(self, provider):
                self.calls.append(("delete", provider))

            def _on_provider_selected_for_new_model(self):
                self.calls.append(("provider", self.combo.currentText()))

        dialog = _ProviderDialog()
        combo = ModernPopupComboBox(dialog)
        dialog.combo = combo
        combo.setObjectName("TranslatorProviderCombo")
        combo.addItems(["默认分组", "ChatGPT Web", "Claude Code"])
        combo.setCurrentText("ChatGPT Web")

        captured = {}

        class _FakePopup:
            def __init__(self, grouped_models, current_model, on_selected, **kwargs):
                captured["grouped_models"] = grouped_models
                captured["current_model"] = current_model
                captured["on_selected"] = on_selected
                captured["kwargs"] = kwargs

            def show_at(self, *_args):
                captured["shown"] = True

        with patch("deepcat.ui.post_capture_actions.combos._OcrModelMenuPopup", _FakePopup):
            combo.showPopup()

        self.assertEqual(
            captured["grouped_models"],
            [("默认分组", ["模型C"]), ("ChatGPT Web", ["模型A", "模型B"]), ("Claude Code", ["模型CC"])],
        )
        self.assertEqual(captured["current_model"], "模型B")
        self.assertEqual(captured["kwargs"]["purpose"], "qa")
        self.assertTrue(captured["kwargs"]["match_parent_width"])
        self.assertEqual(captured["kwargs"]["highlighted_models"], {"模型B", "模型CC"})
        self.assertEqual(
            captured["kwargs"]["highlighted_model_colors"],
            {"模型B": CODEX_CURRENT_MODEL_CHECK_COLOR, "模型CC": CLAUDE_CODE_CURRENT_MODEL_CHECK_COLOR},
        )
        self.assertEqual(captured["kwargs"]["highlighted_text_color"], CODEX_CURRENT_MODEL_CHECK_COLOR)
        self.assertEqual(captured["kwargs"]["active_check_color"], CODEX_CURRENT_MODEL_CHECK_COLOR)
        self.assertTrue(captured["kwargs"]["show_group_move_action"])
        self.assertTrue(callable(captured["kwargs"]["on_codex_action"]))
        self.assertEqual(captured["kwargs"]["codex_action_label_for_model"]("模型B"), "还原Codex")
        self.assertEqual(captured["kwargs"]["codex_action_label_for_model"]("模型A"), "填入Codex")
        self.assertEqual(captured["kwargs"]["codex_action_label_for_model"]("模型CC", "Claude Code"), "填入CC")
        self.assertEqual(captured["kwargs"]["codex_action_color_for_model"]("模型CC", "Claude Code"), CLAUDE_CODE_CURRENT_MODEL_CHECK_COLOR)
        self.assertTrue(captured.get("shown", False))
        self.assertIn(("grouped", "qa"), dialog.calls)
        self.assertIn(("current_model", "qa"), dialog.calls)
        self.assertIn(("codex_highlight", "qa"), dialog.calls)
        self.assertIn(("highlight_colors", "qa"), dialog.calls)

        captured["kwargs"]["on_group_selected"]("ChatGPT Web")
        self.assertIn(("provider", "ChatGPT Web"), dialog.calls)

        captured["on_selected"]("模型A")
        self.assertIn(("model", "模型A"), dialog.calls)

        captured["kwargs"]["on_delete"]("模型A")
        self.assertIn(("delete_model", "模型A", "qa"), dialog.calls)

        captured["kwargs"]["on_batch_delete"](["模型A", "模型B"])
        self.assertIn(("delete_models", ["模型A", "模型B"], "qa"), dialog.calls)

        captured["kwargs"]["on_codex_action"]("模型B")
        self.assertIn(("codex", "模型B"), dialog.calls)
        captured["kwargs"]["on_codex_action"]("模型CC", "Claude Code")
        self.assertIn(("cc", "模型CC"), dialog.calls)

    def test_provider_combo_popup_uses_cached_group_models_when_getter_empty(self):
        class _ProviderDialog(QWidget):
            def __init__(self):
                super().__init__()
                self.calls = []
                self._translator = {"provider_remarks": {}}

            def _current_translator_role(self):
                return "qa"

            def _provider_popup_models_by_provider(self, role=""):
                self.calls.append(("grouped", role))
                return {}

            def _on_provider_popup_model_selected(self, model_name):
                self.calls.append(("model", model_name))

            def _on_provider_selected_for_new_model(self):
                self.calls.append(("provider", self.combo.currentText()))

        dialog = _ProviderDialog()
        combo = ModernPopupComboBox(dialog)
        dialog.combo = combo
        combo.setObjectName("TranslatorProviderCombo")
        combo.addItems(["ChatGPT Web"])
        combo.setCurrentText("ChatGPT Web")
        combo.setProperty("providerModelsByProvider", {"qa": {"ChatGPT Web": ["模型A"]}})
        combo.setProperty("providerCurrentModels", {"qa": "模型A"})
        combo.setProperty("providerHighlightedModels", ["模型A"])

        captured = {}

        class _FakePopup:
            def __init__(self, grouped_models, current_model, on_selected, **kwargs):
                captured["grouped_models"] = grouped_models
                captured["current_model"] = current_model
                captured["on_selected"] = on_selected
                captured["kwargs"] = kwargs

            def show_at(self, *_args):
                captured["shown"] = True

        with patch("deepcat.ui.post_capture_actions.combos._OcrModelMenuPopup", _FakePopup):
            combo.showPopup()

        self.assertEqual(captured["grouped_models"], [("ChatGPT Web", ["模型A"])])
        self.assertEqual(captured["current_model"], "模型A")
        self.assertEqual(captured["kwargs"]["highlighted_models"], {"模型A"})
        self.assertEqual(captured["kwargs"]["active_check_color"], CODEX_CURRENT_MODEL_CHECK_COLOR)
        self.assertTrue(captured["kwargs"]["show_group_move_action"])
        self.assertIn(("grouped", "qa"), dialog.calls)

        captured["on_selected"]("模型A")
        self.assertIn(("model", "模型A"), dialog.calls)

    def test_provider_combo_popup_finds_codex_action_on_outer_settings_window(self):
        class _OuterSettings(QWidget):
            def __init__(self):
                super().__init__()
                self.calls = []

            def _apply_current_model_codex_config_for_model(self, model_name):
                self.calls.append(("codex", model_name))

            def _codex_action_label_for_model(self, model_name):
                return "填入Codex"

        class _ProviderPanel(QWidget):
            def __init__(self, parent):
                super().__init__(parent)
                self.calls = []
                self._translator = {"provider_remarks": {}}

            def _current_translator_role(self):
                return "translate"

            def _provider_popup_models_by_provider(self, role=""):
                self.calls.append(("grouped", role))
                return {"公益组": ["claude-opus-4-6"]}

            def _provider_popup_current_model(self, role=""):
                return "claude-opus-4-6"

            def _on_provider_popup_model_selected(self, model_name):
                self.calls.append(("model", model_name))

        outer = _OuterSettings()
        panel = _ProviderPanel(outer)
        combo = ModernPopupComboBox(panel)
        combo.setObjectName("TranslatorProviderCombo")
        combo.addItem("公益组")

        captured = {}

        class _FakePopup:
            def __init__(self, grouped_models, current_model, on_selected, **kwargs):
                captured["grouped_models"] = grouped_models
                captured["current_model"] = current_model
                captured["on_selected"] = on_selected
                captured["kwargs"] = kwargs

            def show_at(self, *_args):
                captured["shown"] = True

        with patch("deepcat.ui.post_capture_actions.combos._OcrModelMenuPopup", _FakePopup):
            combo.showPopup()

        self.assertEqual(captured["grouped_models"], [("公益组", ["claude-opus-4-6"])])
        self.assertTrue(captured["kwargs"]["show_group_move_action"])
        self.assertTrue(callable(captured["kwargs"]["on_codex_action"]))
        self.assertEqual(captured["kwargs"]["codex_action_label_for_model"]("claude-opus-4-6"), "填入Codex")

        captured["kwargs"]["on_codex_action"]("claude-opus-4-6")
        self.assertIn(("codex", "claude-opus-4-6"), outer.calls)

    def test_provider_model_popup_group_headers_are_styled_and_have_hover_actions(self):
        from deepcat.ui.post_capture_actions import _OcrModelMenuPopup

        class _ProviderDialog(QWidget):
            def __init__(self):
                super().__init__()
                self.calls = []

            def delete_provider_group(self, provider):
                self.calls.append(("delete_group", provider))

            def _move_current_model_to_provider(self, provider):
                self.calls.append(("move_group", provider))

        parent = _ProviderDialog()
        popup = _OcrModelMenuPopup(
            [("ChatGPT Web", ["模型A", "模型B"])],
            "模型A",
            lambda model_name: None,
            parent=parent,
            on_delete=lambda model_name: None,
            on_batch_delete=lambda model_names: None,
            highlighted_models={"模型A"},
            highlighted_text_color=CODEX_CURRENT_MODEL_CHECK_COLOR,
            on_group_selected=lambda provider: None,
            on_codex_action=lambda model_name: parent.calls.append(("codex", model_name)),
            show_group_move_action=True,
        )

        group_headers = [
            widget
            for widget in popup.findChildren(QWidget)
            if widget.objectName() == "OcrModelMenuGroupHeaderContainer"
        ]
        group_header_buttons = [
            widget
            for widget in popup.findChildren(QPushButton)
            if widget.objectName() == "OcrModelMenuGroupHeader"
        ]
        model_test_buttons = [
            widget
            for widget in popup.findChildren(QWidget)
            if widget.property("model_test_button") is not None
        ]
        group_test_buttons = [
            widget
            for widget in popup.findChildren(QWidget)
            if widget.property("group_test_button") is not None
        ]
        group_move_buttons = [
            widget
            for widget in popup.findChildren(QWidget)
            if widget.property("group_move_button") is not None
        ]
        delete_buttons = [
            widget
            for widget in popup.findChildren(QWidget)
            if widget.property("model_delete_button") is not None
        ]
        codex_buttons = [
            widget
            for widget in popup.findChildren(QWidget)
            if widget.property("model_codex_button") is not None
        ]
        model_containers = [
            widget
            for widget in popup.findChildren(QWidget)
            if widget.property("is_model_container") is not None
        ]
        active_buttons = [
            widget
            for widget in popup.findChildren(QWidget)
            if widget.property("model_active") is True
        ]

        self.assertEqual(len(group_headers), 1)
        self.assertEqual(len(group_header_buttons), 1)
        self.assertEqual(len(model_test_buttons), 2)
        self.assertEqual(len(group_test_buttons), 1)
        self.assertEqual(len(group_move_buttons), 1)
        self.assertGreaterEqual(len(delete_buttons), 3)
        self.assertEqual(len(codex_buttons), 2)
        self.assertEqual(
            sorted(button.text() for button in codex_buttons),
            ["填入Codex", "还原Codex"],
        )
        self.assertEqual(len(active_buttons), 1)
        self.assertTrue(bool(active_buttons[0].property("codex_highlighted")))
        self.assertEqual(active_buttons[0].icon().cacheKey(), popup._highlighted_checked_icon.cacheKey())
        self.assertIn("#eef2f7", popup.styleSheet())
        self.assertIn("QPushButton#OcrModelMenuGroupHeader", popup.styleSheet())
        self.assertIn("text-align: left", popup.styleSheet())
        self.assertIn("font-weight: 800", popup.styleSheet())

        first_codex_btn = model_containers[0].property("codex_btn")
        self.assertFalse(first_codex_btn.isVisible())
        popup.eventFilter(active_buttons[0], QEvent(QEvent.Type.Enter))
        self.assertFalse(first_codex_btn.isHidden())

        group_move_buttons[0].click()
        self.assertIn(("move_group", "ChatGPT Web"), parent.calls)

    def test_provider_model_popup_codex_action_runs_after_close(self):
        from deepcat.ui.post_capture_actions import _OcrModelMenuPopup

        parent = QWidget()
        calls = []
        popup = _OcrModelMenuPopup(
            [("ChatGPT Web", ["模型A"])],
            "模型A",
            lambda model_name: None,
            parent=parent,
            on_delete=None,
            on_batch_delete=None,
            on_codex_action=lambda *args: calls.append(args),
        )
        codex_buttons = [
            widget
            for widget in popup.findChildren(QPushButton)
            if widget.property("model_codex_button") is not None
        ]
        self.assertEqual(len(codex_buttons), 1)

        with patch("deepcat.ui.post_capture_actions.QTimer.singleShot") as single_shot:
            codex_buttons[0].pressed.emit()

        self.assertTrue(single_shot.called)
        self.assertEqual(calls, [])
        single_shot.call_args.args[1]()
        self.assertEqual(calls, [("模型A", "ChatGPT Web")])

    def test_provider_row_selection_clears_fields_for_new_model_without_moving_current_model(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator_current_model = "当前模型"
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItems(["旧分组", "新分组"])
        dummy._translator_provider.setCurrentText("旧分组")
        dummy._translator = {
            "translate_model": "当前模型",
            "qa_model": "当前模型",
            "current_model": "当前模型",
            "removed_models": [],
            "model_configs": {
                "当前模型": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "current-id",
                    "api_key": "key",
                    "use_proxy": False,
                    "model_type": "glm",
                    "provider": "旧分组",
                },
            },
        }
        calls = []
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.setText("current-id")
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("当前模型")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("key")
        dummy._translator_proxy_url = QLineEdit()
        dummy._translator_proxy_url.setText("socks5://127.0.0.1:1080")
        dummy._translator_use_proxy = QCheckBox()
        dummy._model_list_plus_btn = type("Btn", (), {"setEnabled": lambda self, enabled: None})()
        dummy._model_id_selection_creates_config = False
        dummy._fetched_models_list = ["current-id"]
        dummy._fetched_models_base_url = "https://api.example.com/v1"
        dummy._translator_field_config = SettingsDialog._translator_field_config.__get__(dummy, type(dummy))
        dummy._combo_selected_value = SettingsDialog._combo_selected_value.__get__(dummy, type(dummy))
        dummy._set_combo_selected_value = SettingsDialog._set_combo_selected_value.__get__(dummy, type(dummy))
        dummy._fill_model_combos = SettingsDialog._fill_model_combos.__get__(dummy, type(dummy))
        dummy._sync_role_models_from_combos = SettingsDialog._sync_role_models_from_combos.__get__(dummy, type(dummy))
        dummy._model_combos = SettingsDialog._model_combos.__get__(dummy, type(dummy))
        dummy._current_translator_role = SettingsDialog._current_translator_role.__get__(dummy, type(dummy))
        dummy._save_current_translator_model_config = SettingsDialog._save_current_translator_model_config.__get__(dummy, type(dummy))
        dummy._set_translator_provider_value = SettingsDialog._set_translator_provider_value.__get__(dummy, type(dummy))
        dummy._clear_translator_fields_for_new_provider = SettingsDialog._clear_translator_fields_for_new_provider.__get__(dummy, type(dummy))
        dummy._select_provider_for_new_model = SettingsDialog._select_provider_for_new_model.__get__(dummy, type(dummy))
        dummy._sync_translator_config_controls = lambda model_type: calls.append(("sync", model_type))
        dummy._fill_model_name_combobox_items = lambda: calls.append(("fill",))
        dummy._update_api_url_role_label = lambda: calls.append(("role",))
        dummy._fill_model_combos("当前模型", "当前模型")

        SettingsDialog._select_provider_for_new_model(dummy, "新分组")

        self.assertEqual(dummy._translator["model_configs"]["当前模型"]["provider"], "旧分组")
        self.assertEqual(dummy._translator_provider.currentText(), "新分组")
        self.assertEqual(dummy._translator_api_url.text(), "")
        self.assertEqual(dummy._translator_model_name.currentText(), "")
        self.assertEqual(dummy._translator_model_note.text(), "")
        self.assertEqual(dummy._translator_api_key.text(), "")
        self.assertEqual(dummy._translator_current_model, "")

    def test_translator_line_edit_fields_use_custom_context_menu(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_api_url = QLineEdit()
        dummy._translator_model_note = QLineEdit()
        dummy._translator_api_key = QLineEdit()
        dummy._translator_proxy_url = QLineEdit()

        SettingsDialog._install_translator_line_edit_context_menus(dummy)

        for edit in (
            dummy._translator_api_url,
            dummy._translator_model_note,
            dummy._translator_api_key,
            dummy._translator_proxy_url,
        ):
            self.assertEqual(edit.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
            self.assertTrue(bool(edit.property("deepcatCustomContextMenu")))

        with patch("deepcat.ui.settings_dialog.SettingsDialog._show_line_edit_context_menu") as show_menu:
            dummy._translator_api_url.customContextMenuRequested.emit(QPoint(1, 1))
        show_menu.assert_called_once_with(dummy, dummy._translator_api_url, QPoint(1, 1))

    def test_custom_text_context_menus_install_recursively_for_multiline_widgets(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        line_edit = QLineEdit(root)
        text_edit = QTextEdit(root)
        plain_text_edit = QPlainTextEdit(root)
        layout.addWidget(line_edit)
        layout.addWidget(text_edit)
        layout.addWidget(plain_text_edit)

        SettingsDialog._install_custom_text_context_menus(root, root)

        for edit in (line_edit, text_edit, plain_text_edit):
            self.assertEqual(edit.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
            self.assertTrue(bool(edit.property("deepcatCustomContextMenu")))

        with patch("deepcat.ui.settings_dialog.SettingsDialog._show_line_edit_context_menu") as show_menu:
            text_edit.customContextMenuRequested.emit(QPoint(2, 3))
        show_menu.assert_called_once_with(root, text_edit, QPoint(2, 3))

    def test_custom_text_context_menus_skip_widgets_with_own_context_menu_event(self):
        class NotesLikeEditor(QTextEdit):
            def contextMenuEvent(self, event):
                event.accept()

        root = QWidget()
        layout = QVBoxLayout(root)
        normal_edit = QTextEdit(root)
        notes_like_edit = NotesLikeEditor(root)
        layout.addWidget(normal_edit)
        layout.addWidget(notes_like_edit)

        SettingsDialog._install_custom_text_context_menus(root, root)

        self.assertEqual(normal_edit.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
        self.assertTrue(bool(normal_edit.property("deepcatCustomContextMenu")))
        self.assertEqual(notes_like_edit.contextMenuPolicy(), Qt.ContextMenuPolicy.DefaultContextMenu)
        self.assertFalse(bool(notes_like_edit.property("deepcatCustomContextMenu")))

    def test_prompt_edit_dialog_uses_custom_text_context_menu(self):
        parent = QWidget()
        dialog = PromptEditDialog(parent, "回复提示词", "当前内容", "默认内容", "快回", "回复")
        try:
            self.assertEqual(dialog.button_name(), "快回")
            self.assertEqual(dialog._button_name_editor.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
            self.assertTrue(bool(dialog._button_name_editor.property("deepcatCustomContextMenu")))
            self.assertEqual(dialog._editor.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
            self.assertTrue(bool(dialog._editor.property("deepcatCustomContextMenu")))
        finally:
            dialog.deleteLater()
            parent.deleteLater()

    def test_add_chat_card_icon_combo_uses_custom_popup_menu(self):
        from deepcat.ui.settings_dialog.sub_dialogs import AddChatCardDialog

        captured = {}

        class _FakeMenuPopup:
            def __init__(self, items, parent=None, active_index=None, **kwargs):
                captured["items"] = items
                captured["parent"] = parent
                captured["active_index"] = active_index
                captured["kwargs"] = kwargs

            def show_at_pos(self, pos):
                captured["pos"] = pos

        parent = QWidget()
        dialog = AddChatCardDialog(parent, initial_icon_type="write")
        try:
            with patch("deepcat.ui.post_capture_actions.OcrGenericMenuPopup", _FakeMenuPopup):
                dialog._icon_combo.showPopup()

            self.assertIs(captured["parent"], dialog._icon_combo)
            self.assertEqual(captured["active_index"], 10)
            self.assertTrue(captured["kwargs"]["match_parent_width_exact"])
            self.assertEqual(captured["kwargs"]["active_indicator"], "background")
            expected_items = [
                "马斯克分身", "巴菲特分身", "决策助手", "创意写作", "代码专家",
                "营销文案", "情绪疗愈", "求职面试", "英语教练", "周报整理",
                "文本润色", "-", "上传自定义图片..."
            ]
            self.assertEqual([item[0] for item in captured["items"]], expected_items)
            self.assertIsInstance(captured["items"][0][3], QIcon)

            captured["items"][1][1]()
            self.assertEqual(dialog.card_icon_type(), "buffett")
        finally:
            dialog.deleteLater()
            parent.deleteLater()

    def test_chat_placeholder_cards_can_be_reordered_and_persisted(self):
        from types import SimpleNamespace
        from deepcat.ui.chat_bubbles import AIChatPlaceholderWidget

        saved_cards = [
            {"id": "translate", "icon_type": "translate", "title": "多语翻译", "prompt": "翻译"},
            {"id": "search", "icon_type": "search", "title": "AI搜索", "prompt": "搜索"},
            {"id": "write", "icon_type": "write", "title": "文本润色", "prompt": "润色"},
        ]
        persisted: dict[str, object] = {}

        class _FakePromptStore:
            def save_prompt(self, key, value):
                persisted["prompt_key"] = key
                persisted["prompt_value"] = value

        def _fake_load_settings():
            return SimpleNamespace(ui={"chat_placeholder_cards": persisted.get("chat_placeholder_cards", saved_cards)})

        def _fake_update_ui_settings(**kwargs):
            persisted.update(kwargs)
            return SimpleNamespace(ui=dict(persisted))

        with (
            patch("deepcat.settings_store.load_settings", _fake_load_settings),
            patch("deepcat.settings_store.update_ui_settings", _fake_update_ui_settings),
            patch("deepcat.prompt_store.PromptStore", _FakePromptStore),
        ):
            widget = AIChatPlaceholderWidget()
            try:
                self.assertTrue(widget._move_card_to_index("write", 0))
                self.assertEqual(
                    [card["id"] for card in persisted["chat_placeholder_cards"]],
                    ["write", "translate", "search"],
                )
                self.assertEqual(persisted["prompt_key"], "chat_placeholder_cards")
                self.assertEqual(
                    [card["id"] for card in json.loads(persisted["prompt_value"])],
                    ["write", "translate", "search"],
                )
                self.assertEqual(
                    [card["id"] for card in widget._cards_to_show],
                    ["write", "translate", "search"],
                )
            finally:
                widget.deleteLater()

    def test_resource_shortcut_dialog_uses_custom_text_context_menu(self):
        from deepcat.ui.main_window import _ResourceShortcutDialog

        parent = QWidget()
        dialog = _ResourceShortcutDialog(parent)
        try:
            for edit in (dialog._title, dialog._target):
                self.assertEqual(edit.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
                self.assertTrue(bool(edit.property("deepcatCustomContextMenu")))
        finally:
            dialog.deleteLater()
            parent.deleteLater()

    def test_todo_edit_dialog_uses_custom_text_context_menu(self):
        from deepcat.ui.main_window import _TodoEditDialog

        parent = QWidget()
        dialog = _TodoEditDialog(parent, selected_date=QDate.currentDate())
        try:
            for edit in (dialog._title, dialog._content):
                self.assertEqual(edit.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
                self.assertTrue(bool(edit.property("deepcatCustomContextMenu")))
        finally:
            dialog.deleteLater()
            parent.deleteLater()

    def test_drive_cleaner_window_uses_custom_text_context_menu(self):
        from deepcat.drive_cleaner import DriveInfo
        from deepcat.ui import main_window

        class _FakeParent:
            def _asset_icon(self, *_args, **_kwargs):
                return QIcon()

            def windowIcon(self):
                return QIcon()

        with patch.object(
            main_window,
            "enumerate_drives",
            return_value=[DriveInfo("C:", "C:\\", "fixed", 1024, 512, 512)],
        ):
            window = main_window._DriveCleanerWindow(_FakeParent())
        try:
            for edit in (window._software_search, window._log, window._advisor):
                self.assertEqual(edit.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
                self.assertTrue(bool(edit.property("deepcatCustomContextMenu")))
        finally:
            window.deleteLater()

    def test_drive_cleaner_software_search_loads_list_without_refresh(self):
        from deepcat.drive_cleaner import DriveInfo
        from deepcat.ui import main_window

        class _FakeParent:
            def _asset_icon(self, *_args, **_kwargs):
                return QIcon()

            def windowIcon(self):
                return QIcon()

        with patch.object(
            main_window,
            "enumerate_drives",
            return_value=[DriveInfo("C:", "C:\\", "fixed", 1024, 512, 512)],
        ):
            window = main_window._DriveCleanerWindow(_FakeParent())
        try:
            scan_calls = []

            def fake_start_software_scan():
                scan_calls.append("scan")
                window._software_list_loaded = True

            window._start_software_scan = fake_start_software_scan
            window._switch_mode("uninstall")
            window._software_search.setText("Chrome")

            self.assertEqual(scan_calls, ["scan"])
        finally:
            window.deleteLater()

    def test_drive_cleaner_select_all_writes_renderable_check_state(self):
        from deepcat.drive_cleaner import DriveInfo
        from deepcat.ui import main_window

        class _FakeParent:
            def _asset_icon(self, *_args, **_kwargs):
                return QIcon()

            def windowIcon(self):
                return QIcon()

        with patch.object(
            main_window,
            "enumerate_drives",
            return_value=[DriveInfo("C:", "C:\\", "fixed", 1024, 512, 512)],
        ):
            window = main_window._DriveCleanerWindow(_FakeParent())
        try:
            table = window._tables["cleanup"]
            table.setRowCount(1)
            item = QTableWidgetItem("")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(window._RESULT_ROLE_RISK, "安全")
            item.setCheckState(Qt.CheckState.Unchecked)
            table.setItem(0, 0, item)

            window._current_mode = "cleanup"
            window._select_current_table_all()

            stored_state = table.model().data(
                table.model().index(0, 0),
                Qt.ItemDataRole.CheckStateRole,
            )
            self.assertEqual(stored_state, Qt.CheckState.Checked)
            self.assertIsInstance(stored_state, Qt.CheckState)
        finally:
            window.deleteLater()

    def test_drive_cleaner_qss_uses_native_asset_paths(self):
        from deepcat.drive_cleaner import DriveInfo
        from deepcat.ui import main_window

        class _FakeParent:
            def _asset_icon(self, *_args, **_kwargs):
                return QIcon()

            def windowIcon(self):
                return QIcon()

        with patch.object(
            main_window,
            "enumerate_drives",
            return_value=[DriveInfo("C:", "C:\\", "fixed", 1024, 512, 512)],
        ):
            window = main_window._DriveCleanerWindow(_FakeParent())
        try:
            checkbox_icon = (
                Path(main_window.__file__).resolve().parent.parent
                / "assets"
                / "icon_checkbox_check_slate.svg"
            ).as_posix()
            self.assertIn(checkbox_icon, window.styleSheet())
            self.assertNotIn("file:", window.styleSheet().lower())
        finally:
            window.deleteLater()

    def test_drive_cleaner_progress_text_is_centered(self):
        from deepcat.drive_cleaner import DriveInfo
        from deepcat.ui import main_window

        class _FakeParent:
            def _asset_icon(self, *_args, **_kwargs):
                return QIcon()

            def windowIcon(self):
                return QIcon()

        with patch.object(
            main_window,
            "enumerate_drives",
            return_value=[DriveInfo("C:", "C:\\", "fixed", 1024, 512, 512)],
        ):
            window = main_window._DriveCleanerWindow(_FakeParent())
        try:
            self.assertEqual(window._progress.alignment(), Qt.AlignmentFlag.AlignCenter)
            self.assertEqual(window._progress.height(), 30)
            self.assertIn("text-align: center", window.styleSheet())
        finally:
            window.deleteLater()

    def test_drive_cleaner_recycle_error_codes_have_specific_categories(self):
        from deepcat.ui import main_window

        classifier = main_window._DriveCleanerWindow._failure_reason_category
        self.assertEqual(classifier(None, "文件正在被其他程序占用（错误码 32）"), "文件被占用")
        self.assertEqual(classifier(None, "源路径访问被拒绝（错误码 120）"), "权限不足")
        self.assertEqual(classifier(None, "Windows 回收站接口无法处理该路径（错误码 124）"), "路径无效或正在变化")

    def test_line_edit_context_menu_items_follow_edit_state(self):
        edit = QLineEdit("abc")
        edit.selectAll()
        QApplication.clipboard().setText("clip")

        with patch.object(SettingsDialog, "_clipboard_has_paste_content", return_value=True):
            enabled = {
                name: is_enabled
                for name, _slot, is_enabled in SettingsDialog._line_edit_context_menu_items(edit)
                if name != "-"
            }

            self.assertTrue(enabled["剪切"])
            self.assertTrue(enabled["复制"])
            self.assertTrue(enabled["粘贴"])
            self.assertTrue(enabled["删除"])
            self.assertTrue(enabled["全选"])

            edit.setReadOnly(True)
            enabled = {
                name: is_enabled
                for name, _slot, is_enabled in SettingsDialog._line_edit_context_menu_items(edit)
                if name != "-"
            }

            self.assertFalse(enabled["剪切"])
            self.assertTrue(enabled["复制"])
            self.assertFalse(enabled["粘贴"])
            self.assertFalse(enabled["删除"])
            self.assertTrue(enabled["全选"])

    def test_line_edit_context_menu_copy_supports_password_fields(self):
        edit = QLineEdit("sk-secret")
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.selectAll()
        QApplication.clipboard().clear()

        slots = {
            name: slot
            for name, slot, _is_enabled in SettingsDialog._line_edit_context_menu_items(edit)
            if name != "-"
        }
        slots["复制"]()

        self.assertEqual(QApplication.clipboard().text(), "sk-secret")

    def test_plain_text_context_menu_items_follow_multiline_edit_state(self):
        edit = QPlainTextEdit()
        edit.setPlainText("第一行\n第二行")
        cursor = edit.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(2, QTextCursor.MoveMode.KeepAnchor)
        edit.setTextCursor(cursor)
        QApplication.clipboard().setText("clip")

        with patch.object(SettingsDialog, "_clipboard_has_paste_content", return_value=True):
            enabled = {
                name: is_enabled
                for name, _slot, is_enabled in SettingsDialog._line_edit_context_menu_items(edit)
                if name != "-"
            }

            self.assertTrue(enabled["剪切"])
            self.assertTrue(enabled["复制"])
            self.assertTrue(enabled["粘贴"])
            self.assertTrue(enabled["删除"])
            self.assertTrue(enabled["全选"])

        slots = {
            name: slot
            for name, slot, _is_enabled in SettingsDialog._line_edit_context_menu_items(edit)
            if name != "-"
        }
        fake_clipboard = type("FakeClipboard", (), {"value": "", "setText": lambda self, value: setattr(self, "value", value)})()
        fake_app = type("FakeApp", (), {"clipboard": lambda self: fake_clipboard})()
        with patch("deepcat.ui.settings_dialog.QApplication.instance", return_value=fake_app):
            slots["复制"]()
        self.assertEqual(fake_clipboard.value, "第一")

        with patch.object(SettingsDialog, "_clipboard_has_paste_content", return_value=True):
            edit.setReadOnly(True)
            enabled = {
                name: is_enabled
                for name, _slot, is_enabled in SettingsDialog._line_edit_context_menu_items(edit)
                if name != "-"
            }

            self.assertFalse(enabled["剪切"])
            self.assertTrue(enabled["复制"])
            self.assertFalse(enabled["粘贴"])
            self.assertFalse(enabled["删除"])
            self.assertTrue(enabled["全选"])

    def test_edit_provider_removes_old_remark_and_updates_model_configs(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItem("旧分组")
        dummy._translator_provider.setCurrentText("旧分组")
        dummy._translator = {
            "provider_remarks": {
                "旧分组": "这里是旧备注"
            },
            "model_configs": {
                "模型A": {
                    "provider": "旧分组",
                    "model_name": "model-a"
                }
            }
        }
        calls = []
        dummy._fill_model_combos = lambda: calls.append("fill_combos")
        dummy._fill_model_name_combobox_items = lambda: calls.append("fill_names")
        dummy._persist_translator_settings = lambda: calls.append("persist")

        with patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("新分组", True)):
            SettingsDialog._on_edit_provider_clicked(dummy)

        self.assertEqual(dummy._translator["provider_remarks"], {"新分组": ""})
        self.assertEqual(dummy._translator["model_configs"]["模型A"]["provider"], "新分组")
        self.assertEqual(dummy._translator_provider.currentText(), "新分组")
        self.assertIn("fill_combos", calls)
        self.assertIn("fill_names", calls)
        self.assertIn("persist", calls)

    def test_add_provider_restores_removed_provider_name(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItem("默认分组")
        dummy._translator_provider.setCurrentText("默认分组")
        dummy._translator = {
            "provider_remarks": {},
            "removed_providers": ["新分组"],
            "model_configs": {},
        }
        calls = []
        dummy._save_current_translator_model_config = lambda: calls.append("save_current")
        dummy._clear_translator_fields_for_new_provider = lambda provider: calls.append(("clear", provider))
        dummy._persist_translator_settings = lambda: calls.append("persist")

        with patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("新分组", True)):
            SettingsDialog._on_add_provider_clicked(dummy)

        self.assertEqual(dummy._translator["provider_remarks"], {"新分组": ""})
        self.assertEqual(dummy._translator["removed_providers"], [])
        self.assertEqual(dummy._translator_provider.currentText(), "新分组")
        self.assertIn(("clear", "新分组"), calls)
        self.assertIn("persist", calls)

    def test_delete_provider_group_moves_models_to_default_group(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItems(["默认分组", "旧分组"])
        dummy._translator_provider.setCurrentText("旧分组")
        dummy._translator = {
            "provider_remarks": {"旧分组": "旧备注"},
            "model_configs": {
                "模型A": {"provider": "旧分组", "model_name": "model-a"},
                "模型B": {"provider": "其它分组", "model_name": "model-b"},
            },
        }
        calls = []
        dummy._save_current_translator_model_config = lambda: calls.append("save_current")
        dummy._fill_model_combos = lambda: calls.append("fill_combos")
        dummy._fill_model_name_combobox_items = lambda: calls.append("fill_names")
        dummy._load_translator_model_config = lambda model_name: calls.append(("load_model", model_name))
        dummy._repaint_model_combos = lambda: calls.append("repaint")
        dummy._persist_translator_settings = lambda: calls.append("persist")

        with patch(
            "PyQt6.QtWidgets.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            SettingsDialog.delete_provider_group(dummy, "旧分组")

        self.assertEqual(dummy._translator["provider_remarks"], {})
        self.assertEqual(dummy._translator["removed_providers"], ["旧分组"])
        self.assertEqual(dummy._translator["model_configs"]["模型A"]["provider"], "默认分组")
        self.assertEqual(dummy._translator["model_configs"]["模型B"]["provider"], "其它分组")
        self.assertEqual(dummy._translator_provider.currentText(), "默认分组")
        self.assertIn("save_current", calls)
        self.assertIn("fill_combos", calls)
        self.assertIn("fill_names", calls)
        self.assertIn(("load_model", "模型A"), calls)
        self.assertIn("repaint", calls)
        self.assertIn("persist", calls)

    def test_delete_builtin_provider_group_removes_it_from_provider_combo(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItems(["默认分组", "Google Gemini"])
        dummy._translator_provider.setCurrentText("Google Gemini")
        dummy._translator = {
            "provider_remarks": {},
            "model_configs": {
                "Gemini模型": {"provider": "Google Gemini", "model_name": "gemini-id"},
            },
        }
        dummy._save_current_translator_model_config = lambda: None
        dummy._fill_model_combos = lambda: None
        dummy._fill_model_name_combobox_items = lambda: None
        dummy._load_translator_model_config = lambda model_name: None
        dummy._repaint_model_combos = lambda: None
        dummy._persist_translator_settings = lambda: None

        with patch(
            "PyQt6.QtWidgets.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            SettingsDialog.delete_provider_group(dummy, "Google Gemini")

        providers = [dummy._translator_provider.itemText(i) for i in range(dummy._translator_provider.count())]
        self.assertNotIn("Google Gemini", providers)
        self.assertEqual(dummy._translator["model_configs"]["Gemini模型"]["provider"], "默认分组")
        self.assertEqual(dummy._translator["removed_providers"], ["Google Gemini"])

    def test_edit_model_id_updates_model_name_and_note_config(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.setText("old-model-id")
        dummy._translator_model_note = QLineEdit()
        dummy._translator_model_note.setText("模型备注A")
        dummy._translator_current_model = "模型备注A"
        dummy._translator = {
            "model_configs": {
                "模型备注A": {
                    "model_name": "old-model-id",
                    "base_url": "https://api.example.com",
                    "api_key": "abc"
                }
            }
        }
        calls = []
        dummy._translator_field_config = lambda note: {"base_url": "https://api.example.com", "api_key": "abc"}
        dummy._fill_model_name_combobox_items = lambda: calls.append("fill_names")
        dummy._refresh_model_combos_after_catalog_change = lambda note, role: calls.append(("refresh", note, role))
        dummy._current_translator_role = lambda: "translate"
        dummy._persist_translator_settings = lambda: calls.append("persist")

        with patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("new-model-id", True)):
            SettingsDialog._on_edit_model_id_clicked(dummy)

        self.assertEqual(dummy._translator_model_name.currentText(), "new-model-id")
        self.assertEqual(dummy._translator["model_configs"]["模型备注A"]["model_name"], "new-model-id")
        self.assertIn("fill_names", calls)
        self.assertIn(("refresh", "模型备注A", "translate"), calls)
        self.assertIn("persist", calls)

    def test_save_current_model_config_locks_original_provider_for_existing_model(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator_current_model = "当前模型"
        dummy._translator_model_note = type("Note", (), {"text": lambda self: "当前模型"})()
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItems(["旧分组", "新分组"])
        dummy._translator_provider.setCurrentText("新分组")
        dummy._translator = {
            "model_configs": {
                "当前模型": {
                    "provider": "旧分组",
                    "model_name": "current-id",
                },
            },
            "translate_model": "当前模型",
            "qa_model": "当前模型",
            "current_model": "当前模型",
            "removed_models": [],
        }
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator_model_name = PasteAwareComboBox()
        dummy._translator_model_name.setText("current-id")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("key")
        dummy._translator_use_proxy = QCheckBox()

        dummy._translator_field_config = SettingsDialog._translator_field_config.__get__(dummy, type(dummy))
        dummy._combo_selected_value = SettingsDialog._combo_selected_value.__get__(dummy, type(dummy))
        dummy._set_combo_selected_value = SettingsDialog._set_combo_selected_value.__get__(dummy, type(dummy))
        dummy._fill_model_combos = SettingsDialog._fill_model_combos.__get__(dummy, type(dummy))
        dummy._sync_role_models_from_combos = SettingsDialog._sync_role_models_from_combos.__get__(dummy, type(dummy))
        dummy._current_translator_role = SettingsDialog._current_translator_role.__get__(dummy, type(dummy))
        dummy._fill_model_combos("当前模型", "当前模型")
        dummy._fill_model_name_combobox_items = lambda: None

        SettingsDialog._save_current_translator_model_config(dummy)

        configs = dummy._translator["model_configs"]
        self.assertEqual(configs["当前模型"]["provider"], "旧分组")

    def test_clear_provider_models_removes_models_in_group(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator_current_model = "模型A"
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator = {
            "model_configs": {
                "模型A": {"provider": "默认分组", "model_name": "model-a"},
                "模型B": {"provider": "默认分组", "model_name": "model-b"},
                "模型C": {"provider": "其它分组", "model_name": "model-c"},
            },
            "translate_model": "模型A",
            "qa_model": "模型A",
            "current_model": "模型A",
            "removed_models": [],
        }
        calls = []
        dummy._save_current_translator_model_config = lambda: None
        dummy._fill_model_name_combobox_items = lambda: None
        dummy._repaint_model_combos = lambda: None
        dummy._persist_translator_settings = lambda: calls.append("persist")
        dummy._sync_role_models_from_combos = lambda: None
        dummy._combo_selected_value = lambda combo, default="": "模型A"
        dummy._set_combo_selected_value = lambda combo, val: None
        dummy._fill_model_combos = lambda: None
        dummy._load_translator_model_config = lambda model_name: calls.append(("load", model_name))

        with patch(
            "PyQt6.QtWidgets.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            SettingsDialog.clear_provider_models(dummy, "默认分组")

        configs = dummy._translator["model_configs"]
        self.assertNotIn("模型A", configs)
        self.assertNotIn("模型B", configs)
        self.assertIn("模型C", configs)
        self.assertIn("persist", calls)
        self.assertIn(("load", "模型C"), calls)

    def test_delete_translator_models_removes_selected_models(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator_current_model = "模型A"
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._translator = {
            "model_configs": {
                "模型A": {"provider": "默认分组", "model_name": "model-a"},
                "模型B": {"provider": "默认分组", "model_name": "model-b"},
                "模型C": {"provider": "其它分组", "model_name": "model-c"},
            },
            "translate_model": "模型A",
            "qa_model": "模型B",
            "current_model": "模型A",
            "removed_models": [],
        }
        calls = []
        selected = []
        dummy._save_current_translator_model_config = lambda: None
        dummy._fill_model_name_combobox_items = lambda: calls.append("fill_names")
        dummy._repaint_model_combos = lambda: calls.append("repaint")
        dummy._persist_translator_settings = lambda: calls.append("persist")
        dummy._sync_role_models_from_combos = lambda: calls.append("sync")
        dummy._combo_selected_value = lambda combo, default="": "模型A" if combo is dummy._translator_model else "模型B"
        dummy._set_combo_selected_value = lambda combo, val: selected.append((combo, val))
        dummy._fill_model_combos = lambda: calls.append("fill_combos")
        dummy._load_translator_model_config = lambda model_name: calls.append(("load", model_name))

        with patch(
            "PyQt6.QtWidgets.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            SettingsDialog._delete_translator_models(dummy, ["模型A", "模型B"], "translate")

        configs = dummy._translator["model_configs"]
        self.assertNotIn("模型A", configs)
        self.assertNotIn("模型B", configs)
        self.assertIn("模型C", configs)
        self.assertEqual(dummy._translator_current_model, "模型C")
        self.assertIn((dummy._translator_model, "模型C"), selected)
        self.assertIn((dummy._qa_model, "模型C"), selected)
        self.assertIn("fill_combos", calls)
        self.assertIn("sync", calls)
        self.assertIn(("load", "模型C"), calls)
        self.assertIn("persist", calls)

    def test_enable_codex_config_closes_client_before_write_then_launches(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator = {}
        dummy._codex_config_switch = QCheckBox()
        dummy._codex_config_switch.setChecked(True)
        calls = []
        dummy._persist_translator_settings = lambda: calls.append("persist")

        with patch("deepcat.ui.settings_dialog.dialog.list_codex_client_process_names", return_value=["codex.exe"]), \
             patch("deepcat.ui.settings_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("deepcat.ui.settings_dialog.dialog.stop_codex_client", side_effect=lambda: calls.append("stop")) as stop, \
             patch("deepcat.ui.settings_dialog.dialog.apply_codex_config", side_effect=lambda enabled: calls.append(("apply", enabled))) as apply_config, \
             patch("deepcat.ui.settings_dialog.dialog.launch_codex_client", side_effect=lambda: calls.append("launch")) as launch, \
             patch("deepcat.ui.settings_dialog.SettingsDialog._show_codex_config_next_step") as next_step:
            SettingsDialog._apply_codex_config_enabled(dummy)

        self.assertTrue(dummy._translator["codex_config_enabled"])
        self.assertEqual(calls, ["stop", ("apply", True), "launch", "persist"])
        apply_config.assert_called_once_with(True)
        stop.assert_called_once()
        launch.assert_called_once()
        next_step.assert_not_called()

    def test_enable_codex_config_opens_prompt_when_codex_is_not_running(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator = {}
        dummy._codex_config_switch = QCheckBox()
        dummy._codex_config_switch.setChecked(True)
        dummy._persist_translator_settings = lambda: None

        with patch("deepcat.ui.settings_dialog.dialog.apply_codex_config") as apply_config, \
             patch("deepcat.ui.settings_dialog.dialog.list_codex_client_process_names", return_value=[]), \
             patch(
                 "deepcat.ui.settings_dialog.QMessageBox.question",
                 return_value=QMessageBox.StandardButton.Yes,
             ) as question, \
             patch("deepcat.ui.settings_dialog.dialog.launch_codex_client") as launch:
            SettingsDialog._apply_codex_config_enabled(dummy)

        apply_config.assert_called_once_with(True)
        question.assert_called_once()
        launch.assert_called_once()

    def test_codex_config_write_failure_relaunches_original_client(self):
        dummy = type("Dummy", (), {})()
        calls = []

        def fail_write(enabled):
            calls.append(("apply", enabled))
            raise OSError("write failed")

        with patch("deepcat.ui.settings_dialog.dialog.list_codex_client_process_names", return_value=["codex.exe"]), \
             patch("deepcat.ui.settings_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("deepcat.ui.settings_dialog.dialog.stop_codex_client", side_effect=lambda: calls.append("stop")), \
             patch("deepcat.ui.settings_dialog.dialog.apply_codex_config", side_effect=fail_write), \
             patch("deepcat.ui.settings_dialog.dialog.launch_codex_client", side_effect=lambda: calls.append("launch")):
            with self.assertRaisesRegex(OSError, "write failed"):
                SettingsDialog._apply_codex_config_with_client_restart(dummy, True)

        self.assertEqual(calls, ["stop", ("apply", True), "launch"])

    def test_codex_config_next_step_prompts_before_launch(self):
        dummy = type("Dummy", (), {})()
        calls = []

        def question(*_args, **_kwargs):
            calls.append("question")
            return QMessageBox.StandardButton.Yes

        with patch("deepcat.ui.settings_dialog.QMessageBox.question", side_effect=question) as question_mock, \
             patch("deepcat.ui.settings_dialog.dialog.launch_codex_client", side_effect=lambda: calls.append("launch")) as launch:
            SettingsDialog._show_codex_config_next_step(dummy)

        question_mock.assert_called_once()
        launch.assert_called_once()
        self.assertEqual(calls, ["question", "launch"])

    def test_codex_config_next_step_does_not_detect_process_when_cancelled(self):
        dummy = type("Dummy", (), {})()

        with patch(
            "deepcat.ui.settings_dialog.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as question, \
             patch("deepcat.ui.settings_dialog.dialog.launch_codex_client") as launch:
            SettingsDialog._show_codex_config_next_step(dummy)

        question.assert_called_once()
        launch.assert_not_called()

    def test_disable_codex_config_without_running_codex_is_silent(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator = {}
        dummy._codex_config_switch = QCheckBox()
        dummy._codex_config_switch.setChecked(False)
        dummy._persist_translator_settings = lambda: None

        with patch("deepcat.ui.settings_dialog.dialog.apply_codex_config") as apply_config, \
             patch("deepcat.ui.settings_dialog.dialog.list_codex_client_process_names", return_value=[]), \
             patch("deepcat.ui.settings_dialog.dialog.stop_codex_client") as stop:
            SettingsDialog._apply_codex_config_enabled(dummy)

        self.assertFalse(dummy._translator["codex_config_enabled"])
        apply_config.assert_called_once_with(False)
        stop.assert_not_called()

    def test_disable_codex_config_closes_before_restore_then_launches(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator = {}
        dummy._codex_config_switch = QCheckBox()
        dummy._codex_config_switch.setChecked(False)
        dummy._persist_translator_settings = lambda: None

        calls = []
        with patch("deepcat.ui.settings_dialog.dialog.apply_codex_config", side_effect=lambda enabled: calls.append(("apply", enabled))) as apply_config, \
             patch("deepcat.ui.settings_dialog.dialog.list_codex_client_process_names", return_value=["codex.exe"]), \
             patch("deepcat.ui.settings_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("deepcat.ui.settings_dialog.dialog.stop_codex_client", side_effect=lambda: calls.append("stop")) as stop, \
             patch("deepcat.ui.settings_dialog.dialog.launch_codex_client", side_effect=lambda: calls.append("launch")) as launch:
            SettingsDialog._apply_codex_config_enabled(dummy)

        apply_config.assert_called_once_with(False)
        stop.assert_called_once()
        launch.assert_called_once()
        self.assertEqual(calls, ["stop", ("apply", False), "launch"])

    def test_current_model_codex_switch_tracks_bound_qa_model(self):
        dummy = type("Dummy", (), {})()
        dummy._translator = {
            "model_configs": {
                "模型A": {"provider": "默认分组", "model_name": "model-a"},
                "模型B": {"provider": "默认分组", "model_name": "model-b"},
            },
            "translate_model": "模型A",
            "qa_model": "模型A",
            "codex_current_model_config_enabled": True,
            "codex_current_model_config_model": "模型A",
        }
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._qa_model.addItems(["模型A", "模型B"])
        dummy._qa_model.setCurrentText("模型A")
        dummy._translator_current_role = "qa"
        dummy._translator_current_model = "模型A"
        dummy._fill_codex_config_switch = QCheckBox()
        dummy._combo_selected_value = SettingsDialog._combo_selected_value.__get__(dummy, type(dummy))
        dummy._current_translator_role = SettingsDialog._current_translator_role.__get__(dummy, type(dummy))
        dummy._current_model_codex_config_model = SettingsDialog._current_model_codex_config_model.__get__(dummy, type(dummy))
        dummy._ensure_current_model_codex_binding = SettingsDialog._ensure_current_model_codex_binding.__get__(dummy, type(dummy))
        dummy._is_current_model_codex_configured = SettingsDialog._is_current_model_codex_configured.__get__(dummy, type(dummy))

        SettingsDialog._sync_current_model_codex_ui(dummy)

        self.assertTrue(dummy._fill_codex_config_switch.isHidden())
        self.assertTrue(dummy._fill_codex_config_switch.isChecked())
        self.assertEqual(dummy._qa_model.property("activeCheckColor"), "#2563eb")

        dummy._translator_current_model = "模型B"
        dummy._translator["qa_model"] = "模型B"
        dummy._qa_model.setCurrentText("模型B")

        SettingsDialog._sync_current_model_codex_ui(dummy)

        self.assertTrue(dummy._fill_codex_config_switch.isHidden())
        self.assertFalse(dummy._fill_codex_config_switch.isChecked())
        self.assertEqual(dummy._qa_model.property("activeCheckColor"), "#111827")

        dummy._translator_current_role = "translate"
        dummy._translator_current_model = "模型A"

        SettingsDialog._sync_current_model_codex_ui(dummy)

        self.assertTrue(dummy._fill_codex_config_switch.isHidden())

    def test_current_model_codex_popup_action_toggles_existing_apply_logic(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator = {
            "model_configs": {
                "模型A": {
                    "provider": "ChatGPT Web",
                    "base_url": "https://api-a.example.com/v1",
                    "model_name": "model-a",
                    "api_key": "key-a",
                },
                "模型B": {
                    "provider": "ChatGPT Web",
                    "base_url": "https://api-b.example.com/v1",
                    "model_name": "model-b",
                    "api_key": "key-b",
                },
            },
            "qa_model": "模型A",
            "codex_current_model_config_enabled": True,
            "codex_current_model_config_model": "模型A",
        }
        calls = []
        dummy._current_model_codex_config_model = SettingsDialog._current_model_codex_config_model.__get__(dummy, type(dummy))
        dummy._ensure_current_model_codex_binding = SettingsDialog._ensure_current_model_codex_binding.__get__(dummy, type(dummy))
        dummy._is_current_model_codex_configured = SettingsDialog._is_current_model_codex_configured.__get__(dummy, type(dummy))
        dummy._on_translator_model_changed = lambda *args, **kwargs: calls.append(("model", args, kwargs))
        dummy._set_current_model_codex_switch_checked = lambda checked: calls.append(("switch", checked))
        dummy._persist_translator_settings = lambda: calls.append(("persist",))
        dummy._sync_current_model_codex_ui = lambda: calls.append(("sync",))

        with patch("deepcat.ui.settings_dialog.dialog.apply_codex_config") as apply_config, \
             patch("deepcat.ui.settings_dialog.dialog.list_codex_client_process_names", return_value=[]):
            SettingsDialog._apply_current_model_codex_config_for_model(dummy, "模型A")

        apply_config.assert_called_once_with(False)
        self.assertFalse(dummy._translator["codex_current_model_config_enabled"])
        self.assertEqual(dummy._translator["codex_current_model_config_model"], "")
        self.assertIn(("model", ("模型A", "qa"), {"save_current": False}), calls)
        self.assertIn(("switch", False), calls)

        calls.clear()
        lifecycle = []
        with patch(
                 "deepcat.ui.settings_dialog.dialog.apply_codex_config",
                 side_effect=lambda enabled, **kwargs: lifecycle.append(("apply", enabled, kwargs)),
             ) as apply_config, \
             patch("deepcat.ui.settings_dialog.dialog.list_codex_client_process_names", return_value=["codex.exe"]), \
             patch("deepcat.ui.settings_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("deepcat.ui.settings_dialog.dialog.stop_codex_client", side_effect=lambda: lifecycle.append("stop")), \
             patch("deepcat.ui.settings_dialog.dialog.launch_codex_client", side_effect=lambda: lifecycle.append("launch")), \
             patch("deepcat.ui.settings_dialog.SettingsDialog._show_codex_config_next_step") as next_step:
            SettingsDialog._apply_current_model_codex_config_for_model(dummy, "模型B")

        apply_config.assert_called_once_with(
            True,
            base_url="https://api-b.example.com/v1",
            model="model-b",
            api_key="key-b",
        )
        next_step.assert_not_called()
        self.assertEqual(
            lifecycle,
            [
                "stop",
                (
                    "apply",
                    True,
                    {
                        "base_url": "https://api-b.example.com/v1",
                        "model": "model-b",
                        "api_key": "key-b",
                    },
                ),
                "launch",
            ],
        )
        self.assertTrue(dummy._translator["codex_current_model_config_enabled"])
        self.assertEqual(dummy._translator["codex_current_model_config_model"], "模型B")
        self.assertIn(("model", ("模型B", "qa"), {"save_current": False}), calls)
        self.assertIn(("switch", True), calls)

    def test_current_model_codex_popup_action_writes_even_when_model_switch_fails(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator = {
            "model_configs": {
                "模型A": {
                    "provider": "ChatGPT Web",
                    "base_url": "https://api.example.com/v1",
                    "model_name": "model-a",
                    "api_key": "key-a",
                },
            },
            "qa_model": "",
            "codex_current_model_config_enabled": False,
            "codex_current_model_config_model": "",
        }
        calls = []
        dummy._current_model_codex_config_model = SettingsDialog._current_model_codex_config_model.__get__(dummy, type(dummy))
        dummy._ensure_current_model_codex_binding = SettingsDialog._ensure_current_model_codex_binding.__get__(dummy, type(dummy))
        dummy._is_current_model_codex_configured = SettingsDialog._is_current_model_codex_configured.__get__(dummy, type(dummy))
        dummy._on_translator_model_changed = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("switch failed"))
        dummy._set_current_model_codex_switch_checked = lambda checked: calls.append(("switch", checked))
        dummy._persist_translator_settings = lambda: calls.append(("persist",))
        dummy._sync_current_model_codex_ui = lambda: calls.append(("sync",))
        dummy._set_combo_selected_value = lambda combo, model_name: None

        with patch("deepcat.ui.settings_dialog.dialog.apply_codex_config") as apply_config, \
             patch("deepcat.ui.settings_dialog.dialog.list_codex_client_process_names", return_value=[]), \
             patch("deepcat.ui.settings_dialog.SettingsDialog._show_codex_config_next_step") as next_step:
            SettingsDialog._apply_current_model_codex_config_for_model(dummy, "模型A")

        apply_config.assert_called_once_with(
            True,
            base_url="https://api.example.com/v1",
            model="model-a",
            api_key="key-a",
        )
        next_step.assert_called_once()
        self.assertEqual(dummy._translator["qa_model"], "模型A")
        self.assertTrue(dummy._translator["codex_current_model_config_enabled"])
        self.assertEqual(dummy._translator["codex_current_model_config_model"], "模型A")
        self.assertIn(("switch", True), calls)

    def test_current_model_claude_code_popup_action_writes_model_config(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator = {
            "model_configs": {
                "模型CC": {
                    "provider": "Claude Code",
                    "base_url": "https://claude.example.com",
                    "model_name": "claude-sonnet-4.6",
                    "api_key": "sk-cc",
                }
            }
        }
        calls = []
        dummy._on_translator_model_changed = lambda model_name, role: calls.append(("model", model_name, role))

        with patch("deepcat.ui.settings_dialog.dialog.apply_claude_code_config") as apply_config, \
             patch("deepcat.ui.settings_dialog.SettingsDialog._show_feedback_message") as feedback:
            apply_config.return_value = "C:/Users/demo/.claude/settings.json"
            SettingsDialog._apply_current_model_claude_code_config_for_model(dummy, "模型CC")

        self.assertEqual(calls, [("model", "模型CC", "qa")])
        self.assertEqual(dummy._translator["claude_code_current_model_config_model"], "模型CC")
        apply_config.assert_called_once_with(
            base_url="https://claude.example.com",
            model="claude-sonnet-4.6",
            api_key="sk-cc",
        )
        feedback.assert_called_once()

    def test_current_model_claude_code_action_label_toggles_for_bound_model(self):
        dummy = type("Dummy", (), {})()
        dummy._translator = {
            "model_configs": {
                "模型CC": {"provider": "Claude Code"},
                "模型B": {"provider": "Claude Code"},
            },
            "claude_code_current_model_config_model": "模型CC",
        }

        self.assertEqual(SettingsDialog._claude_code_action_label_for_model(dummy, "模型CC"), "还原CC")
        self.assertEqual(SettingsDialog._claude_code_action_label_for_model(dummy, "模型B"), "填入CC")

    def test_current_model_claude_code_popup_action_restores_previous_config(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator = {
            "model_configs": {
                "模型CC": {
                    "provider": "Claude Code",
                    "base_url": "https://claude.example.com",
                    "model_name": "claude-sonnet-4.6",
                    "api_key": "sk-cc",
                }
            },
            "claude_code_current_model_config_model": "模型CC",
        }
        calls = []
        dummy._on_translator_model_changed = lambda model_name, role: calls.append(("model", model_name, role))
        dummy._persist_translator_settings = lambda: calls.append(("persist",))
        dummy._sync_current_model_codex_ui = lambda: calls.append(("sync",))

        with patch("deepcat.ui.settings_dialog.dialog.restore_claude_code_config") as restore_config, \
             patch("deepcat.ui.settings_dialog.dialog.apply_claude_code_config") as apply_config, \
             patch("deepcat.ui.settings_dialog.SettingsDialog._show_feedback_message") as feedback:
            restore_config.return_value = "C:/Users/demo/.claude/settings.json"
            SettingsDialog._apply_current_model_claude_code_config_for_model(dummy, "模型CC")

        restore_config.assert_called_once_with()
        apply_config.assert_not_called()
        self.assertEqual(dummy._translator["claude_code_current_model_config_model"], "")
        self.assertEqual(calls, [("model", "模型CC", "qa"), ("persist",), ("sync",)])
        feedback.assert_called_once()

    def test_current_model_codex_apply_replaces_previous_model_in_one_write(self):
        dummy = type("Dummy", (), {})()
        dummy._translator_loading = False
        dummy._translator = {
            "model_configs": {
                "模型A": {"provider": "默认分组", "model_name": "model-a"},
                "模型B": {"provider": "默认分组", "model_name": "model-b"},
            },
            "translate_model": "模型A",
            "qa_model": "模型B",
            "codex_config_enabled": False,
            "codex_current_model_config_enabled": True,
            "codex_current_model_config_model": "模型A",
        }
        dummy._translator_current_role = "qa"
        dummy._translator_current_model = "模型B"
        dummy._fill_codex_config_switch = QCheckBox()
        dummy._fill_codex_config_switch.setChecked(True)
        dummy._codex_config_switch = QCheckBox()
        dummy._translator_model = TranslatorModelComboBox()
        dummy._qa_model = TranslatorModelComboBox()
        dummy._qa_model.addItems(["模型A", "模型B"])
        dummy._qa_model.setCurrentText("模型B")
        dummy._translator_api_url = QLineEdit()
        dummy._translator_api_url.setText("https://api.example.com/v1")
        dummy._translator_model_name = _FakeField("model-b")
        dummy._translator_api_key = QLineEdit()
        dummy._translator_api_key.setText("key-b")
        dummy._translator_use_proxy = QCheckBox()
        dummy._translator_proxy_url = QLineEdit()
        dummy._translator_proxy_url.setText("socks5://127.0.0.1:1080")
        dummy._translator_provider = QComboBox()
        dummy._translator_provider.addItem("默认分组")
        dummy._remember_current_translator_model = lambda: False
        dummy._persist_translator_settings = lambda: None
        dummy._combo_selected_value = SettingsDialog._combo_selected_value.__get__(dummy, type(dummy))
        dummy._current_translator_role = SettingsDialog._current_translator_role.__get__(dummy, type(dummy))
        dummy._translator_field_config = SettingsDialog._translator_field_config.__get__(dummy, type(dummy))
        dummy._current_translator_runtime_config = SettingsDialog._current_translator_runtime_config.__get__(dummy, type(dummy))
        dummy._set_current_model_codex_switch_checked = SettingsDialog._set_current_model_codex_switch_checked.__get__(dummy, type(dummy))
        dummy._current_model_codex_config_model = SettingsDialog._current_model_codex_config_model.__get__(dummy, type(dummy))
        dummy._ensure_current_model_codex_binding = SettingsDialog._ensure_current_model_codex_binding.__get__(dummy, type(dummy))
        dummy._is_current_model_codex_configured = SettingsDialog._is_current_model_codex_configured.__get__(dummy, type(dummy))
        dummy._sync_current_model_codex_ui = SettingsDialog._sync_current_model_codex_ui.__get__(dummy, type(dummy))

        with patch("deepcat.ui.settings_dialog.dialog.apply_codex_config") as apply_config, \
             patch("deepcat.ui.settings_dialog.dialog.list_codex_client_process_names", return_value=[]), \
             patch("deepcat.ui.settings_dialog.SettingsDialog._show_codex_config_next_step") as next_step:
            SettingsDialog._apply_current_model_codex_config_enabled(dummy)

        apply_config.assert_called_once()
        self.assertEqual(apply_config.call_args.args, (True,))
        self.assertEqual(
            apply_config.call_args.kwargs,
            {"base_url": "https://api.example.com/v1", "model": "model-b", "api_key": "key-b"},
        )
        self.assertTrue(dummy._translator["codex_current_model_config_enabled"])
        self.assertEqual(dummy._translator["codex_current_model_config_model"], "模型B")
        self.assertFalse(dummy._translator["codex_config_enabled"])
        self.assertTrue(dummy._fill_codex_config_switch.isChecked())
        next_step.assert_called_once()

    def test_proxy_worker_success_on_google(self):
        from deepcat.ui.settings_dialog import ProxyConnectionTestWorker
        from deepcat.ui.network_probe import GOOGLE_204_TARGET, NetworkProbeResult

        worker = ProxyConnectionTestWorker("127.0.0.1:1080")
        results = []
        worker.tested.connect(lambda success, msg, elapsed: results.append((success, msg)))

        success = NetworkProbeResult(
            target=GOOGLE_204_TARGET,
            ok=True,
            status_code=204,
            elapsed_ms=120,
            via_proxy=True,
            route="proxy",
            route_elapsed_ms=120,
        )
        with patch("deepcat.ui.network_probe.run_proxy_network_probe", return_value=success) as probe:
            worker.run()

        self.assertEqual(results, [(True, "代理连通成功 (可访问外网)")])
        self.assertEqual(probe.call_args.args[2], "127.0.0.1:1080")

    def test_proxy_worker_success_on_cloudflare_only(self):
        from deepcat.ui.settings_dialog import ProxyConnectionTestWorker
        from deepcat.ui.network_probe import CLOUDFLARE_204_TARGET, GOOGLE_204_TARGET, NetworkProbeResult

        worker = ProxyConnectionTestWorker("http://127.0.0.1:7890")
        results = []
        worker.tested.connect(lambda success, msg, elapsed: results.append((success, msg)))

        google_failure = NetworkProbeResult(
            target=GOOGLE_204_TARGET,
            ok=False,
            status_code=None,
            elapsed_ms=1200,
            error="Timeout connection to Google",
            via_proxy=True,
            route="proxy",
            route_elapsed_ms=1200,
        )
        cloudflare_success = NetworkProbeResult(
            target=CLOUDFLARE_204_TARGET,
            ok=True,
            status_code=204,
            elapsed_ms=90,
            via_proxy=True,
            route="proxy",
            route_elapsed_ms=90,
        )
        with patch(
            "deepcat.ui.network_probe.run_proxy_network_probe",
            side_effect=[google_failure, cloudflare_success],
        ):
            worker.run()

        self.assertEqual(results, [(True, "代理连通成功 (Google 异常，其他外网可访问)")])

    def test_proxy_worker_all_failed(self):
        from deepcat.ui.settings_dialog import ProxyConnectionTestWorker
        from deepcat.ui.network_probe import CLOUDFLARE_204_TARGET, GOOGLE_204_TARGET, NetworkProbeResult

        worker = ProxyConnectionTestWorker("socks5://127.0.0.1:1080")
        results = []
        worker.tested.connect(lambda success, msg, elapsed: results.append((success, msg)))

        failures = [
            NetworkProbeResult(
                target=target,
                ok=False,
                status_code=None,
                elapsed_ms=1200,
                error="Failed to connect",
                via_proxy=True,
                route="proxy",
                route_elapsed_ms=1200,
            )
            for target in (GOOGLE_204_TARGET, CLOUDFLARE_204_TARGET)
        ]
        with patch("deepcat.ui.network_probe.run_proxy_network_probe", side_effect=failures):
            worker.run()

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0][0])
        self.assertIn("Failed to connect", results[0][1])

    def test_chatgpt_web2api_test_timeout_uses_friendly_upstream_message(self):
        import requests

        worker = TranslatorConnectionTestWorker(
            {"base_url": "http://127.0.0.1:8082", "model_name": "auto", "api_key": "cookie"},
            False,
            "",
        )
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == "GET":
                return _Response(200, {"service": "chatgpt_web2api", "models": ["auto"]})
            raise requests.exceptions.ReadTimeout(
                "HTTPConnectionPool(host='127.0.0.1', port=8082): Read timed out. (read timeout=30)"
            )

        worker._request = fake_request

        with self.assertRaises(ValueError) as caught:
            worker._test_local_chatgpt_web2api()

        self.assertIn("上游网络连接失败，请检查代理/网络", str(caught.exception))
        self.assertFalse(calls[1][2]["json"]["enable_conversation_append"])
        self.assertEqual(calls[1][2]["json"]["upstream_bootstrap_timeout_sec"], 2)
        self.assertEqual(calls[1][2]["json"]["upstream_connect_timeout_sec"], 6)

    def test_chatgpt_web2api_test_502_sentinel_curl_error_uses_network_message(self):
        worker = TranslatorConnectionTestWorker(
            {"base_url": "http://127.0.0.1:8082", "model_name": "auto", "api_key": "cookie"},
            False,
            "",
        )

        class _ChatResponse(_Response):
            def raise_for_status(self):
                raise RuntimeError("502 Server Error: Bad Gateway")

        error_text = json.dumps(
            {
                "error": {
                    "message": "ChatGPT Web Sentinel 验证失败：Failed to perform, curl: (28) Connection timed out after 2015 milliseconds.",
                    "type": "api_error",
                    "code": "challenge_required",
                }
            },
            ensure_ascii=False,
        )

        def fake_request(method, url, **kwargs):
            if method == "GET":
                return _Response(200, {"service": "chatgpt_web2api", "models": ["auto"]})
            return _ChatResponse(502, text=error_text)

        worker._request = fake_request

        with self.assertRaises(ValueError) as caught:
            worker._test_local_chatgpt_web2api()

        self.assertEqual(str(caught.exception), "上游网络连接失败，请检查代理/网络后重试。")


if __name__ == "__main__":
    unittest.main()
