import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch


class TestSettingsStore(unittest.TestCase):
    def test_network_probe_mode_defaults_to_204_and_normalizes_ping_alias(self) -> None:
        import deepcat.settings_store as ss

        self.assertEqual(
            ss.normalize_network_probe_settings(None),
            {"enabled": False, "mode": ss.NETWORK_PROBE_MODE_HTTP_204},
        )
        self.assertEqual(
            ss.normalize_network_probe_settings({"enabled": True, "mode": "ping"}),
            {"enabled": True, "mode": ss.NETWORK_PROBE_MODE_TCP_CONNECT},
        )
        self.assertEqual(
            ss.normalize_network_probe_settings({"enabled": True, "mode": "invalid"})["mode"],
            ss.NETWORK_PROBE_MODE_HTTP_204,
        )

    def test_ai_window_position_defaults_to_untrusted_until_user_moves_it(self) -> None:
        import deepcat.settings_store as ss

        defaults = ss._defaults().ui
        self.assertIsNone(defaults["qa_window_pos"])
        self.assertFalse(defaults["qa_window_pos_user_moved"])

        normalized = ss._merge_ui(defaults, {"qa_window_pos": [120, 160]})
        self.assertEqual(normalized["qa_window_pos"], [120, 160])
        self.assertFalse(normalized["qa_window_pos_user_moved"])

        normalized = ss._merge_ui(
            defaults,
            {"qa_window_pos": [120, 160], "qa_window_pos_user_moved": True},
        )
        self.assertTrue(normalized["qa_window_pos_user_moved"])

    def test_prompt_store_failure_is_logged_without_masking_settings_load(self) -> None:
        import deepcat.settings_store as ss

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_app_dir = ss.get_app_dir
            ss.get_app_dir = lambda: root  # type: ignore[assignment]
            try:
                ss.save_settings(ss._defaults())
                with patch("deepcat.prompt_store.PromptStore", side_effect=RuntimeError("prompt db unavailable")), patch.object(
                    ss.logger, "error"
                ) as log_error:
                    loaded = ss.load_settings()

                self.assertIsInstance(loaded, ss.AppSettings)
                log_error.assert_called_once()
                self.assertIn("prompt db unavailable", str(log_error.call_args.args[1]))
            finally:
                ss.get_app_dir = old_app_dir  # type: ignore[assignment]

    def test_copy_on_capture_mode_defaults_and_legacy_bool(self) -> None:
        import deepcat.settings_store as ss

        default_ui = ss._defaults().ui

        fresh = ss._merge_ui(default_ui, {})
        self.assertEqual(fresh["copy_on_capture_mode"], ss.COPY_ON_CAPTURE_MODE_OFF)
        self.assertFalse(fresh["copy_on_capture"])

        legacy_enabled = ss._merge_ui(default_ui, {"copy_on_capture": True})
        self.assertEqual(legacy_enabled["copy_on_capture_mode"], ss.COPY_ON_CAPTURE_MODE_COPY_KEEP)
        self.assertTrue(legacy_enabled["copy_on_capture"])

        explicit_close = ss._merge_ui(default_ui, {"copy_on_capture_mode": "复制关闭"})
        self.assertEqual(explicit_close["copy_on_capture_mode"], ss.COPY_ON_CAPTURE_MODE_COPY_CLOSE)
        self.assertTrue(explicit_close["copy_on_capture"])

        explicit_mode_wins = ss._merge_ui(
            default_ui,
            {"copy_on_capture": True, "copy_on_capture_mode": "关闭复制"},
        )
        self.assertEqual(explicit_mode_wins["copy_on_capture_mode"], ss.COPY_ON_CAPTURE_MODE_OFF)
        self.assertFalse(explicit_mode_wins["copy_on_capture"])

    def test_normalize_openai_chat_base_url(self) -> None:
        import deepcat.settings_store as ss

        self.assertEqual(ss.normalize_openai_chat_base_url("https://www.ai8.my"), "https://www.ai8.my/v1")
        self.assertEqual(ss.normalize_openai_chat_base_url("https://www.ai8.my/"), "https://www.ai8.my/v1")
        self.assertEqual(ss.normalize_openai_chat_base_url("https://www.ai8.my/v1"), "https://www.ai8.my/v1")
        self.assertEqual(
            ss.normalize_openai_chat_base_url("https://www.ai8.my/v1/chat/completions"),
            "https://www.ai8.my/v1",
        )
        self.assertEqual(
            ss.normalize_openai_chat_base_url("https://open.bigmodel.cn/api/paas/v4"),
            "https://open.bigmodel.cn/api/paas/v4",
        )

    def test_openai_responses_url_infers_responses_type(self) -> None:
        import deepcat.settings_store as ss

        self.assertEqual(
            ss.normalize_openai_responses_url("https://api.openai.com"),
            "https://api.openai.com/v1/responses",
        )
        self.assertEqual(
            ss.normalize_openai_responses_url("https://api.openai.com/v1"),
            "https://api.openai.com/v1/responses",
        )
        self.assertEqual(
            ss.normalize_openai_responses_url("https://api.openai.com/v1/responses"),
            "https://api.openai.com/v1/responses",
        )
        self.assertEqual(
            ss.normalize_openai_chat_base_url("https://api.openai.com/v1/responses"),
            "https://api.openai.com/v1",
        )
        self.assertEqual(
            ss.infer_translator_model_type(
                "gpt-5",
                {
                    "base_url": "https://api.openai.com/v1/responses",
                    "model_name": "gpt-5",
                    "model_type": "glm",
                },
            ),
            "openai_responses",
        )

    def test_openai_images_url_infers_images_type(self) -> None:
        import deepcat.settings_store as ss

        self.assertEqual(
            ss.normalize_openai_images_url("https://api.openai.com"),
            "https://api.openai.com/v1/images/generations",
        )
        self.assertEqual(
            ss.normalize_openai_images_url("https://api.openai.com/v1"),
            "https://api.openai.com/v1/images/generations",
        )
        self.assertEqual(
            ss.normalize_openai_images_url("https://api.openai.com/v1/images"),
            "https://api.openai.com/v1/images/generations",
        )
        self.assertEqual(
            ss.normalize_openai_images_url("https://api.openai.com/v1/images/generations"),
            "https://api.openai.com/v1/images/generations",
        )
        self.assertEqual(
            ss.normalize_openai_images_url("https://api.openai.com/v1/chat/completions"),
            "https://api.openai.com/v1/images/generations",
        )
        # 显式 model_type 优先
        self.assertEqual(
            ss.infer_translator_model_type(
                "agnes-image",
                {"base_url": "https://www.ai8.my/v1", "model_name": "agnes-image-2.1-flash", "model_type": "openai_images"},
            ),
            "openai_images",
        )
        # base_url 以 /images/generations 结尾时自动推断
        self.assertEqual(
            ss.infer_translator_model_type(
                "agnes-image",
                {
                    "base_url": "https://www.ai8.my/v1/images/generations",
                    "model_name": "agnes-image-2.1-flash",
                    "model_type": "glm",
                },
            ),
            "openai_images",
        )

    def test_builtin_chatgpt_web_image_model_uses_images_endpoint(self) -> None:
        import deepcat.settings_store as ss

        catalog = ss.default_model_catalog()
        cfg = catalog["ChatGPT Web 生图"]

        self.assertEqual(cfg["base_url"], "http://127.0.0.1:8082/v1/images/generations")
        self.assertEqual(cfg["model_name"], "gpt-image-2")
        self.assertEqual(cfg["model_type"], "openai_images")
        self.assertEqual(cfg["provider"], "ChatGPT Web")

    def test_anthropic_messages_url_infers_anthropic_type(self) -> None:
        import deepcat.settings_store as ss

        self.assertEqual(
            ss.normalize_anthropic_messages_url("https://api.anthropic.com"),
            "https://api.anthropic.com/v1/messages",
        )
        self.assertEqual(
            ss.normalize_anthropic_messages_url("https://api.anthropic.com/v1/messages"),
            "https://api.anthropic.com/v1/messages",
        )
        self.assertEqual(
            ss.infer_translator_model_type(
                "claude-sonnet",
                {
                    "base_url": "https://api.anthropic.com/v1/messages",
                    "model_name": "claude-3-5-sonnet-latest",
                    "model_type": "glm",
                },
            ),
            "anthropic",
        )

    def test_gemini_named_openai_relay_stays_openai_compatible(self) -> None:
        import deepcat.settings_store as ss

        self.assertEqual(
            ss.infer_translator_model_type(
                "gemini-3.5-flash-thinking",
                {
                    "base_url": "https://www.ai8.my",
                    "model_name": "gemini-3.5-flash-thinking",
                    "model_type": "gemini",
                },
            ),
            "glm",
        )
        self.assertEqual(
            ss.infer_translator_model_type(
                "gemini-2.5-flash",
                {
                    "base_url": "https://generativelanguage.googleapis.com",
                    "model_name": "gemini-2.5-flash",
                },
            ),
            "gemini",
        )

    def test_save_load_roundtrip(self) -> None:
        import deepcat.settings_store as ss

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)

            old_get_app_dir = ss.get_app_dir
            old_get_files_dir = ss.get_files_dir
            try:
                ss.get_app_dir = lambda: root  # type: ignore[assignment]
                def _files_dir():
                    p = root / "files"
                    p.mkdir(parents=True, exist_ok=True)
                    return p

                ss.get_files_dir = _files_dir  # type: ignore[assignment]

                s0 = ss.load_settings()
                self.assertTrue((root / "files").exists())
                self.assertEqual(s0.hotkey, "<f1>")
                self.assertEqual(s0.ui.get("mode"), "框选截图")
                self.assertEqual(s0.ui.get("scroll_hotkey"), ss.DEFAULT_SCROLL_HOTKEY)
                self.assertTrue(s0.notifications_enabled)
                self.assertFalse(s0.auto_save)
                self.assertEqual(s0.ui.get("save_mode"), "手动保存")
                self.assertEqual(s0.ui.get("save_button_mode"), "auto")
                self.assertEqual(s0.ui.get("previous_capture_action"), "pin")
                self.assertEqual(s0.ui.get("post_capture_button_style"), "icon")
                self.assertTrue(bool(s0.ui.get("auto_snap_enabled")))
                self.assertFalse(bool(s0.ui.get("updater", {}).get("auto_update_enabled")))
                self.assertEqual(s0.ui.get("updater", {}).get("last_check_at"), "")
                self.assertEqual(s0.ui.get("updater", {}).get("last_downloaded_version"), "")
                self.assertEqual(s0.ui.get("annotation_style", {}).get("line_style"), ss.DEFAULT_ANNOTATION_STYLE["line_style"])
                self.assertEqual(
                    s0.ui.get("annotation_style", {}).get("selection_border_color"),
                    ss.DEFAULT_ANNOTATION_STYLE["selection_border_color"],
                )

                s1 = ss.AppSettings(
                    version=int(ss.SETTINGS_VERSION),
                    autostart=True,
                    auto_save=False,
                    image_output_dir=str(root / "img"),
                    pdf_output_dir=str(root / "pdf"),
                    hotkey="<ctrl>+<alt>+a",
                    ui=dict(s0.ui),
                    notifications_enabled=True,
                )
                ss.save_settings(s1)
                s2 = ss.load_settings()
                self.assertEqual(s2.autostart, True)
                self.assertEqual(Path(s2.image_output_dir).name, "img")
                self.assertEqual(Path(s2.pdf_output_dir).name, "pdf")
                self.assertEqual(s2.hotkey, "<ctrl>+<alt>+a")
                self.assertTrue(s2.notifications_enabled)
                self.assertTrue(isinstance(s2.ui, dict))
                self.assertEqual(str(s2.ui.get("output_format")), str(s0.ui.get("output_format")))

                payload = ss.to_payload(s2)
                payload["ui"]["save_mode"] = "手动保存（自己选路径）"
                payload["ui"]["save_button_mode"] = "手动保存"
                payload["ui"]["previous_capture_action"] = "保存前图"
                payload["ui"]["post_capture_button_style"] = "文字按钮"
                (root / "settings.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                s3 = ss.load_settings()
                self.assertEqual(s3.ui.get("save_mode"), "手动保存")
                self.assertEqual(s3.ui.get("save_button_mode"), "manual")
                self.assertEqual(s3.ui.get("previous_capture_action"), "save")
                self.assertEqual(s3.ui.get("post_capture_button_style"), "text")
                payload["ui"]["previous_capture_action"] = "暂存前图"
                (root / "settings.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                s3_stash = ss.load_settings()
                self.assertEqual(s3_stash.ui.get("previous_capture_action"), "stash")
                payload["ui"]["previous_capture_action"] = "关闭前图"
                (root / "settings.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                s3_close = ss.load_settings()
                self.assertEqual(s3_close.ui.get("previous_capture_action"), "pin")
                payload["ui"]["save_button_mode"] = "AI识别"
                (root / "settings.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                s3_ai = ss.load_settings()
                self.assertEqual(s3_ai.ui.get("save_button_mode"), "auto")
                payload["ui"]["annotation_style"] = {"line_style": "实线", "arrow_color": "bad"}
                (root / "settings.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                s4 = ss.load_settings()
                self.assertEqual(s4.ui.get("annotation_style", {}).get("line_style"), "solid")
                self.assertEqual(s4.ui.get("annotation_style", {}).get("arrow_color"), ss.DEFAULT_ANNOTATION_STYLE["arrow_color"])
            finally:
                ss.get_app_dir = old_get_app_dir  # type: ignore[assignment]
                ss.get_files_dir = old_get_files_dir  # type: ignore[assignment]

    def test_bak_recovery(self) -> None:
        import deepcat.settings_store as ss
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_get_app_dir = ss.get_app_dir
            old_get_files_dir = ss.get_files_dir
            try:
                ss.get_app_dir = lambda: root  # type: ignore[assignment]

                def _files_dir():
                    p = root / "files"
                    p.mkdir(parents=True, exist_ok=True)
                    return p

                ss.get_files_dir = _files_dir  # type: ignore[assignment]

                good = ss.to_payload(ss.load_settings())
                (root / "settings.json.bak").write_text(json.dumps(good, ensure_ascii=False, indent=2), encoding="utf-8")
                (root / "settings.json").write_text("{bad json", encoding="utf-8")

                s = ss.load_settings()
                self.assertEqual(s.version, ss.SETTINGS_VERSION)
                self.assertTrue(isinstance(s.ui, dict))
            finally:
                ss.get_app_dir = old_get_app_dir  # type: ignore[assignment]
                ss.get_files_dir = old_get_files_dir  # type: ignore[assignment]

    def test_legacy_manual_scroll_mode_becomes_region_selection(self) -> None:
        import json
        import deepcat.settings_store as ss

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_get_app_dir = ss.get_app_dir
            old_get_files_dir = ss.get_files_dir
            try:
                ss.get_app_dir = lambda: root  # type: ignore[assignment]

                def _files_dir():
                    p = root / "files"
                    p.mkdir(parents=True, exist_ok=True)
                    return p

                ss.get_files_dir = _files_dir  # type: ignore[assignment]

                payload = ss.to_payload(ss.load_settings())
                payload["ui"]["mode"] = "手动滚动"
                (root / "settings.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

                s = ss.load_settings()
                self.assertEqual(s.ui.get("mode"), "框选截图")
            finally:
                ss.get_app_dir = old_get_app_dir  # type: ignore[assignment]
                ss.get_files_dir = old_get_files_dir  # type: ignore[assignment]

    def test_legacy_default_scroll_mode_becomes_region_selection(self) -> None:
        import json
        import deepcat.settings_store as ss

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_get_app_dir = ss.get_app_dir
            old_get_files_dir = ss.get_files_dir
            try:
                ss.get_app_dir = lambda: root  # type: ignore[assignment]

                def _files_dir():
                    p = root / "files"
                    p.mkdir(parents=True, exist_ok=True)
                    return p

                ss.get_files_dir = _files_dir  # type: ignore[assignment]

                payload = ss.to_payload(ss.load_settings())
                payload["version"] = 3
                payload["ui"]["mode"] = "滚动截屏"
                (root / "settings.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

                s = ss.load_settings()
                self.assertEqual(s.ui.get("mode"), "框选截图")
            finally:
                ss.get_app_dir = old_get_app_dir  # type: ignore[assignment]
                ss.get_files_dir = old_get_files_dir  # type: ignore[assignment]

    def test_legacy_default_hotkey_becomes_f1(self) -> None:
        import json
        import deepcat.settings_store as ss

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_get_app_dir = ss.get_app_dir
            old_get_files_dir = ss.get_files_dir
            try:
                ss.get_app_dir = lambda: root  # type: ignore[assignment]

                def _files_dir():
                    p = root / "files"
                    p.mkdir(parents=True, exist_ok=True)
                    return p

                ss.get_files_dir = _files_dir  # type: ignore[assignment]

                payload = ss.to_payload(ss.load_settings())
                payload["version"] = 2
                payload["hotkey"] = "<ctrl>+<shift>+s"
                (root / "settings.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

                s = ss.load_settings()
                self.assertEqual(s.hotkey, "<f1>")
            finally:
                ss.get_app_dir = old_get_app_dir  # type: ignore[assignment]
                ss.get_files_dir = old_get_files_dir  # type: ignore[assignment]

    def test_translator_defaults_and_legacy_names(self) -> None:
        import deepcat.settings_store as ss

        normalized = ss.normalize_translator_settings(
            {
                "current_model": "免费微软翻译",
                "model_configs": {
                    "免费微软翻译": {
                        "base_url": "https://api-edge.cognitive.microsofttranslator.com",
                        "model_name": "microsoft-free",
                        "api_key": "",
                        "use_proxy": False,
                    },
                    "DeepLX": {
                        "base_url": "http://127.0.0.1:1188",
                        "model_name": "deeplx",
                        "api_key": "",
                        "use_proxy": False,
                    },
                },
            }
        )
        self.assertEqual(normalized["current_model"], "Google翻译")
        self.assertEqual(normalized["translate_model"], "Google翻译")
        self.assertEqual(normalized["qa_model"], "gemini-3.5-flash-thinking")
        self.assertNotIn("微软翻译", normalized["model_configs"])
        self.assertNotIn("免费微软翻译", normalized["model_configs"])
        self.assertEqual(normalized["model_configs"]["Google翻译"]["provider"], "免费翻译")
        self.assertEqual(normalized["model_configs"]["DeepLX"]["provider"], "免费翻译")
        self.assertNotIn("gemini-2.5-flash", normalized["model_configs"])
        self.assertNotIn("glm-4-flash", normalized["model_configs"])
        self.assertNotIn("agenes-ai", normalized["model_configs"])
        self.assertEqual(normalized["model_configs"]["gemini-3.6-flash"]["provider"], "Google Gemini")
        self.assertEqual(normalized["model_configs"]["gemini-3.7-flash"]["provider"], "Google Gemini")
        self.assertEqual(normalized["model_configs"]["gemini-3.8-flash"]["provider"], "Google Gemini")
        self.assertEqual(normalized["model_configs"]["腾讯模型MT1.5"]["provider"], "本地部署")
        self.assertEqual(normalized["model_configs"]["DeepLX"]["base_url"], ss.DEEPLX_INFO_ADDRESS)
        self.assertEqual(normalized["model_configs"]["DeepLX"]["model_name"], "deepLX-free")
        self.assertTrue(normalized["selection_translate_enabled"])
        self.assertFalse(normalized["selection_popup_enabled"])
        self.assertFalse(normalized["ocr_translate_enabled"])
        self.assertEqual(ss.default_translator_settings()["current_model"], "gemini-3.5-flash-thinking")
        self.assertEqual(ss.default_translator_settings()["translate_model"], "gemini-3.5-flash-thinking")
        self.assertEqual(ss.default_translator_settings()["qa_model"], "gemini-3.5-flash-thinking")
        self.assertTrue(ss.default_translator_settings()["selection_translate_enabled"])
        self.assertFalse(ss.default_translator_settings()["selection_popup_enabled"])
        self.assertFalse(ss.default_translator_settings()["ocr_translate_enabled"])
        self.assertTrue(ss.normalize_translator_settings({"ocr_translate_enabled": True})["ocr_translate_enabled"])
        self.assertIn("[划词内容]", ss.default_translator_settings()["reply_prompt"])
        self.assertIn("20", ss.default_translator_settings()["reply_prompt"])
        self.assertIn("50", ss.default_translator_settings()["reply_prompt"])
        self.assertIn("[划词内容]", ss.default_translator_settings()["explain_prompt"])
        self.assertIn("[划词内容]", ss.default_translator_settings()["summary_prompt"])
        self.assertIn("[划词内容]", ss.default_translator_settings()["optimize_prompt"])
        self.assertIn("[划词内容]", ss.default_translator_settings()["ai_search_prompt"])
        self.assertEqual(ss.default_translator_settings()["reply_prompt_button_name"], "回复")
        self.assertEqual(ss.default_translator_settings()["ai_search_prompt_button_name"], "搜索")
        self.assertEqual(ss.default_translator_settings()["explain_prompt_button_name"], "解释")
        self.assertEqual(ss.default_translator_settings()["summary_prompt_button_name"], "总结")
        self.assertFalse(ss.default_translator_settings()["codex_current_model_config_enabled"])
        self.assertEqual(ss.default_translator_settings()["codex_current_model_config_model"], "")

    def test_normalize_translator_settings_keeps_prompt_button_names(self) -> None:
        import deepcat.settings_store as ss

        normalized = ss.normalize_translator_settings(
            {
                "reply_prompt_button_name": " 快回 ",
                "ai_search_prompt_button_name": "",
                "explain_prompt_button_name": "abcdefghijklmnop",
                "summary_prompt_button_name": 123,
            }
        )

        self.assertEqual(normalized["reply_prompt_button_name"], "快回")
        self.assertEqual(normalized["ai_search_prompt_button_name"], "搜索")
        self.assertEqual(normalized["explain_prompt_button_name"], "abcdefghijkl")
        self.assertEqual(normalized["summary_prompt_button_name"], "总结")

    def test_clipboard_history_monitor_is_enabled_by_default(self) -> None:
        import deepcat.settings_store as ss

        self.assertTrue(ss.DEFAULT_CLIPBOARD_HISTORY["monitor_enabled"])
        self.assertTrue(ss._defaults().ui["clipboard_history"]["monitor_enabled"])
        self.assertTrue(ss.normalize_clipboard_history_settings({})["monitor_enabled"])

    def test_clipboard_history_cleanup_policy_is_normalized(self) -> None:
        import deepcat.settings_store as ss

        normalized = ss.normalize_clipboard_history_settings(
            {
                "auto_cleanup_enabled": False,
                "retention_days": "30",
                "max_records": "2500",
                "max_capacity_mb": "512",
            }
        )
        self.assertFalse(normalized["auto_cleanup_enabled"])
        self.assertEqual(normalized["retention_days"], 30)
        self.assertEqual(normalized["max_records"], 2500)
        self.assertEqual(normalized["max_capacity_mb"], 512)

    def test_normalize_translator_settings_keeps_codex_current_model_binding(self) -> None:
        import deepcat.settings_store as ss

        normalized = ss.normalize_translator_settings(
            {
                "qa_model": "问答模型",
                "codex_current_model_config_enabled": True,
                "model_configs": {
                    "问答模型": {
                        "base_url": "https://api.example.com/v1",
                        "model_name": "qa-model",
                        "api_key": "secret",
                        "use_proxy": False,
                    }
                },
            }
        )

        self.assertTrue(normalized["codex_current_model_config_enabled"])
        self.assertEqual(normalized["codex_current_model_config_model"], "问答模型")

        normalized = ss.normalize_translator_settings(
            {
                "codex_config_enabled": True,
                "codex_current_model_config_enabled": True,
                "codex_current_model_config_model": "问答模型",
            }
        )

        self.assertTrue(normalized["codex_config_enabled"])
        self.assertFalse(normalized["codex_current_model_config_enabled"])
        self.assertEqual(normalized["codex_current_model_config_model"], "")

    def test_default_model_catalog_is_not_written_to_settings_payload(self) -> None:
        import deepcat.settings_store as ss

        defaults = ss._defaults()
        payload = ss.to_payload(defaults)
        translator_payload = payload["ui"]["translator"]
        self.assertNotIn("model_configs", translator_payload)

        ui = dict(defaults.ui)
        translator = ss.normalize_translator_settings(ui.get("translator"))
        translator["model_configs"]["custom-model"] = {
            "base_url": "http://127.0.0.1:9999",
            "model_name": "custom-model",
            "api_key": "local-key",
            "use_proxy": False,
            "model_type": "glm",
            "provider": "其它",
        }
        ui["translator"] = translator
        custom_settings = ss.AppSettings(
            version=defaults.version,
            autostart=defaults.autostart,
            auto_save=defaults.auto_save,
            image_output_dir=defaults.image_output_dir,
            pdf_output_dir=defaults.pdf_output_dir,
            hotkey=defaults.hotkey,
            ui=ui,
            notifications_enabled=defaults.notifications_enabled,
        )
        custom_payload = ss.to_payload(custom_settings)
        model_configs = custom_payload["ui"]["translator"].get("model_configs", {})
        self.assertIn("custom-model", model_configs)
        self.assertNotIn("gemini-3.5-flash-thinking", model_configs)

    def test_todo_items_are_normalized(self) -> None:
        import deepcat.settings_store as ss

        items = ss.normalize_todo_items(
            [
                {
                    "id": "",
                    "title": "",
                    "location": "旧地点字段",
                    "start": "2026-05-15T09:00:00",
                    "end": "2026-05-15T10:00:00",
                    "important": True,
                    "reminder_minutes": -5,
                    "snooze_minutes": 999999,
                    "repeat": "weekly",
                    "repeat_weekday": 9,
                    "repeat_month_day": 0,
                    "reminded_occurrences": ["2026-05-15", ""],
                },
                "bad",
            ]
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "未命名待办")
        self.assertEqual(items[0]["content"], "旧地点字段")
        self.assertTrue(items[0]["important"])
        self.assertEqual(items[0]["reminder_minutes"], 0)
        self.assertEqual(items[0]["snooze_minutes"], 24 * 60)
        self.assertEqual(items[0]["repeat"], "weekly")
        self.assertEqual(items[0]["repeat_weekday"], [7])
        self.assertEqual(items[0]["repeat_month_day"], [1])
        self.assertEqual(items[0]["reminded_occurrences"], ["2026-05-15"])

    def test_later_read_settings_are_normalized(self) -> None:
        import deepcat.settings_store as ss

        data = ss.normalize_later_read_settings(
            {
                "enabled": True,
                "filter_keywords": "纯水，AI,tag,tag",
                "items": [
                    {
                        "id": "",
                        "title": "",
                        "url": "https://www.example.com/path",
                        "created_at": "2026-05-17T14:32:00",
                        "read": True,
                        "is_pinned": True,
                    },
                    {"url": "javascript:void(0)"},
                ],
            }
        )

        self.assertTrue(data["enabled"])
        self.assertEqual(data["filter_keywords"], ["纯水", "AI", "tag"])
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["title"], "https://www.example.com/path")
        self.assertEqual(data["items"][0]["site"], "example.com")
        self.assertTrue(data["items"][0]["read"])
        self.assertTrue(data["items"][0]["is_pinned"])

    def test_updater_settings_are_normalized(self) -> None:
        import deepcat.settings_store as ss

        self.assertEqual(
            ss.normalize_updater_settings(
                {
                    "auto_update_enabled": True,
                    "last_check_at": "2026-05-27",
                    "last_downloaded_version": "1.0.1",
                }
            ),
            {
                "auto_update_enabled": True,
                "last_check_at": "2026-05-27",
                "last_downloaded_version": "1.0.1",
            },
        )
        self.assertEqual(ss.normalize_updater_settings(None), ss.DEFAULT_UPDATER)

    def test_data_management_interval_prefers_minutes_and_migrates_hours(self) -> None:
        import deepcat.settings_store as ss

        migrated = ss.normalize_data_management_settings({"auto_backup_interval_hours": 2})
        self.assertEqual(migrated["auto_backup_interval_minutes"], 120)
        self.assertEqual(migrated["auto_backup_interval_hours"], 2)

        normalized = ss.normalize_data_management_settings(
            {"auto_backup_interval_minutes": 5, "auto_backup_interval_hours": 24}
        )
        self.assertEqual(normalized["auto_backup_interval_minutes"], 5)
        self.assertEqual(normalized["auto_backup_interval_hours"], 1)

    def test_data_management_cleanup_records_keep_recent_three(self) -> None:
        import deepcat.settings_store as ss

        normalized = ss.normalize_data_management_settings(
            {
                "cleanup_records": [
                    {"title": "一键轻量清理", "detail": "处理 2 个文件", "created_at": "2026-06-18 08:00:00"},
                    {"title": "Log日志文件", "detail": "已清空", "created_at": "2026-06-18 07:00:00"},
                    {"title": "休息待办记录", "detail": "已清空", "created_at": "2026-06-18 06:00:00"},
                    {"title": "旧记录", "detail": "不应保留", "created_at": "2026-06-18 05:00:00"},
                ]
            }
        )

        records = normalized["cleanup_records"]
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["title"], "一键轻量清理")
        self.assertEqual(records[-1]["title"], "休息待办记录")

    def test_update_settings_merges_with_fresh_disk_state(self) -> None:
        import deepcat.settings_store as ss

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_get_app_dir = ss.get_app_dir
            old_get_files_dir = ss.get_files_dir
            try:
                ss.get_app_dir = lambda: root  # type: ignore[assignment]

                def _files_dir():
                    p = root / "files"
                    p.mkdir(parents=True, exist_ok=True)
                    return p

                ss.get_files_dir = _files_dir  # type: ignore[assignment]

                # 两个组件先后通过 helper 各写各的键，互不覆盖
                ss.update_ui_settings(theme="dark")
                ss.update_ui_settings(auto_snap_enabled=True)
                s = ss.load_settings()
                self.assertEqual(s.ui.get("theme"), "dark")
                self.assertTrue(bool(s.ui.get("auto_snap_enabled")))

                # 顶层字段增量更新，不影响 ui 键
                ss.update_settings_fields(autostart=True)
                s = ss.load_settings()
                self.assertTrue(s.autostart)
                self.assertEqual(s.ui.get("theme"), "dark")

                # mutator 返回 None 时放弃写入
                before = ss.get_settings_path().read_text(encoding="utf-8")
                ss.update_settings(lambda cur: None)
                after = ss.get_settings_path().read_text(encoding="utf-8")
                self.assertEqual(before, after)
            finally:
                ss.get_app_dir = old_get_app_dir  # type: ignore[assignment]
                ss.get_files_dir = old_get_files_dir  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
