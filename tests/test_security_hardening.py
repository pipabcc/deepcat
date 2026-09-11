import json
import tempfile
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


class TestSecretStore(unittest.TestCase):
    def test_roundtrip(self) -> None:
        from deepcat.utils.secret_store import is_protected, protect_text, unprotect_text

        cipher = protect_text("sk-secret-value")
        self.assertTrue(is_protected(cipher))
        self.assertNotIn("sk-secret-value", cipher)
        self.assertEqual(unprotect_text(cipher), "sk-secret-value")

    def test_plaintext_passthrough_and_edge_cases(self) -> None:
        from deepcat.utils.secret_store import protect_text, unprotect_text

        self.assertEqual(unprotect_text("sk-plain"), "sk-plain")
        self.assertEqual(protect_text(""), "")
        self.assertEqual(unprotect_text(""), "")
        cipher = protect_text("abc")
        self.assertEqual(protect_text(cipher), cipher)

    def test_corrupted_cipher_returns_empty(self) -> None:
        from deepcat.utils.secret_store import unprotect_text

        self.assertEqual(unprotect_text("dpapi:not-base64!!!"), "")
        self.assertEqual(unprotect_text("dpapi:AAAA"), "")


class TestSettingsApiKeyStorage(unittest.TestCase):
    def test_api_keys_are_stored_in_plaintext(self) -> None:
        import deepcat.settings_store as ss

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_app_dir = ss.get_app_dir
            ss.get_app_dir = lambda: root  # type: ignore[assignment]
            try:
                s = ss.load_settings()
                ui = dict(s.ui)
                translator = dict(ui["translator"])
                configs = dict(translator["model_configs"])
                configs["测试模型"] = {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "gpt-test",
                    "api_key": "test-key",
                    "use_proxy": False,
                    "model_type": "glm",
                }
                translator["model_configs"] = configs
                translator["local_translation_service_api_key"] = "sk-local-77"
                ui["translator"] = translator
                ss.save_settings(replace(s, ui=ui))

                raw = (root / "settings.json").read_text(encoding="utf-8")
                self.assertIn("test-key", raw)
                self.assertIn("sk-local-77", raw)
                self.assertNotIn("dpapi:", raw)

                s2 = ss.load_settings()
                translator2 = s2.ui["translator"]
                self.assertEqual(translator2["model_configs"]["测试模型"]["api_key"], "test-key")
                self.assertEqual(translator2["local_translation_service_api_key"], "sk-local-77")

                # .bak 也保持与 settings.json 一致的明文格式
                ss.save_settings(s2)
                bak = (root / "settings.json.bak").read_text(encoding="utf-8")
                self.assertIn("test-key", bak)
            finally:
                ss.get_app_dir = old_app_dir  # type: ignore[assignment]

    def test_legacy_plaintext_settings_still_load(self) -> None:
        import deepcat.settings_store as ss

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_app_dir = ss.get_app_dir
            ss.get_app_dir = lambda: root  # type: ignore[assignment]
            try:
                payload = {
                    "version": 6,
                    "ui": {
                        "translator": {
                            "model_configs": {
                                "旧模型": {
                                    "base_url": "https://old.example.com/v1",
                                    "model_name": "old",
                                    "api_key": "sk-legacy-plain",
                                    "use_proxy": False,
                                    "model_type": "glm",
                                }
                            }
                        }
                    },
                }
                (root / "settings.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                s = ss.load_settings()
                self.assertEqual(s.ui["translator"]["model_configs"]["旧模型"]["api_key"], "sk-legacy-plain")
            finally:
                ss.get_app_dir = old_app_dir  # type: ignore[assignment]

    def test_legacy_encrypted_settings_still_load(self) -> None:
        import deepcat.settings_store as ss

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_app_dir = ss.get_app_dir
            ss.get_app_dir = lambda: root  # type: ignore[assignment]
            try:
                payload = {
                    "version": 6,
                    "ui": {
                        "translator": {
                            "local_translation_service_api_key": "dpapi:local",
                            "model_configs": {
                                "旧密文模型": {
                                    "base_url": "https://old.example.com/v1",
                                    "model_name": "old-cipher",
                                    "api_key": "dpapi:model",
                                    "use_proxy": False,
                                    "model_type": "glm",
                                }
                            },
                        }
                    },
                }
                (root / "settings.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

                def fake_unprotect(value: str) -> str:
                    mapping = {
                        "dpapi:model": "sk-legacy-model",
                        "dpapi:local": "sk-legacy-local",
                    }
                    return mapping.get(value, value)

                with patch("deepcat.settings_store.unprotect_text", side_effect=fake_unprotect):
                    s = ss.load_settings()

                translator = s.ui["translator"]
                self.assertEqual(translator["model_configs"]["旧密文模型"]["api_key"], "sk-legacy-model")
                self.assertEqual(translator["local_translation_service_api_key"], "sk-legacy-local")
            finally:
                ss.get_app_dir = old_app_dir  # type: ignore[assignment]

    def test_webdav_password_is_protected_in_settings_payload(self) -> None:
        import deepcat.settings_store as ss

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_app_dir = ss.get_app_dir
            ss.get_app_dir = lambda: root  # type: ignore[assignment]
            try:
                def fake_protect(value: str) -> str:
                    return "dpapi:protected-webdav" if value == "dav-secret" else value

                def fake_unprotect(value: str) -> str:
                    return "dav-secret" if value == "dpapi:protected-webdav" else value

                with patch("deepcat.settings_store.protect_text", side_effect=fake_protect), patch(
                    "deepcat.settings_store.unprotect_text", side_effect=fake_unprotect
                ):
                    s = ss.load_settings()
                    ui = dict(s.ui)
                    data_management = dict(ui["data_management"])
                    data_management["webdav_password"] = "dav-secret"
                    ui["data_management"] = data_management
                    ss.save_settings(replace(s, ui=ui))

                    raw = (root / "settings.json").read_text(encoding="utf-8")
                    self.assertNotIn("dav-secret", raw)
                    self.assertIn("dpapi:protected-webdav", raw)

                    loaded = ss.load_settings()
                    self.assertEqual(loaded.ui["data_management"]["webdav_password"], "dav-secret")
            finally:
                ss.get_app_dir = old_app_dir  # type: ignore[assignment]


class TestModelCatalogCache(unittest.TestCase):
    def test_catalog_cached_until_file_changes(self) -> None:
        import deepcat.settings_store as ss

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_app_dir = ss.get_app_dir
            old_parse = ss._parse_model_catalog
            calls = []

            def counting_parse(path):
                calls.append(str(path))
                return old_parse(path)

            ss.get_app_dir = lambda: root  # type: ignore[assignment]
            ss._parse_model_catalog = counting_parse  # type: ignore[assignment]
            try:
                catalog_path = root / "model_catalog.json"
                catalog_path.write_text(
                    json.dumps({"模型A": {"base_url": "https://a.example.com", "model_name": "a"}}),
                    encoding="utf-8",
                )
                first = ss.default_model_catalog()
                second = ss.default_model_catalog()
                self.assertIn("模型A", first)
                self.assertEqual(first, second)
                self.assertEqual(len(calls), 1)

                # 返回的是副本，调用方修改不应污染缓存
                first["模型A"]["base_url"] = "tampered"
                third = ss.default_model_catalog()
                self.assertEqual(third["模型A"]["base_url"], "https://a.example.com")
            finally:
                ss.get_app_dir = old_app_dir  # type: ignore[assignment]
                ss._parse_model_catalog = old_parse  # type: ignore[assignment]


class TestArgvRedaction(unittest.TestCase):
    def test_redact_argv(self) -> None:
        from deepcat.utils.crash_reporter import redact_argv

        argv = [
            "--gui",
            "--translate-api-key",
            "sk-leaky-value",
            "--translate-api-key=sk-other-leak",
            "--translate-port",
            "11888",
        ]
        redacted = redact_argv(argv)
        joined = " ".join(redacted)
        self.assertNotIn("sk-leaky-value", joined)
        self.assertNotIn("sk-other-leak", joined)
        self.assertIn("--gui", redacted)
        self.assertIn("11888", redacted)
        self.assertIn("--translate-api-key", redacted)

    def test_redact_mapping(self) -> None:
        from deepcat.utils.crash_reporter import redact_mapping

        out = redact_mapping({"translate_api_key": "sk-abc", "gui": True, "translate_port": 11888})
        self.assertEqual(out["translate_api_key"], "<redacted>")
        self.assertEqual(out["gui"], True)
        self.assertEqual(out["translate_port"], 11888)


class TestCodexConfig(unittest.TestCase):
    def test_preserves_other_providers_and_name_keys(self) -> None:
        from deepcat.utils.codex_config import update_toml_content

        original = """model = "gpt-5"
model_provider = "openrouter"

[model_providers.openrouter]
name = "OpenRouter"
base_url = "https://openrouter.ai/api/v1"

[mcp_servers.filesystem]
name = "fs"
command = "npx"

[profiles.work]
model = "gpt-5-mini"
"""
        result = update_toml_content(original)
        data = tomllib.loads(result)

        # deepcat 自己的配置生效
        self.assertEqual(data["model_provider"], "codex_local_access")
        self.assertEqual(data["model"], "deepcat-translate")
        self.assertEqual(data["model_providers"]["codex_local_access"]["name"], "codex_local_access")

        # 用户已有的 provider/MCP/profile 完整保留，name 键不被覆盖
        self.assertEqual(data["model_providers"]["openrouter"]["name"], "OpenRouter")
        self.assertEqual(data["model_providers"]["openrouter"]["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(data["mcp_servers"]["filesystem"]["name"], "fs")
        self.assertEqual(data["mcp_servers"]["filesystem"]["command"], "npx")
        self.assertEqual(data["profiles"]["work"]["model"], "gpt-5-mini")

    def test_idempotent(self) -> None:
        from deepcat.utils.codex_config import update_toml_content

        once = update_toml_content("")
        twice = update_toml_content(once)
        self.assertEqual(tomllib.loads(once), tomllib.loads(twice))

    def test_custom_codex_model_config_values(self) -> None:
        from deepcat.utils.codex_config import update_toml_content

        result = update_toml_content(
            "",
            base_url="http://127.0.0.1:8081",
            model="gemini-3.5-flash-thinking",
        )
        data = tomllib.loads(result)
        self.assertEqual(data["model"], "gemini-3.5-flash-thinking")
        self.assertEqual(
            data["model_providers"]["codex_local_access"]["base_url"],
            "http://127.0.0.1:8081",
        )

    def test_replaces_stale_own_section(self) -> None:
        from deepcat.utils.codex_config import update_toml_content

        original = """[model_providers.codex_local_access]
name = "codex_local_access"
base_url = "http://127.0.0.1:9999/v1"

[model_providers.other]
name = "Other"
"""
        data = tomllib.loads(update_toml_content(original))
        self.assertEqual(
            data["model_providers"]["codex_local_access"]["base_url"],
            "http://127.0.0.1:11888/v1",
        )
        self.assertEqual(data["model_providers"]["other"]["name"], "Other")

    def test_invalid_original_toml_raises(self) -> None:
        from deepcat.utils.codex_config import update_toml_content

        with self.assertRaises(ValueError):
            update_toml_content("this is = not [ valid toml")

    def test_codex_process_detection_uses_exact_client_names(self) -> None:
        import deepcat.utils.codex_config as cc

        self.assertTrue(cc._is_codex_client_process_name("Codex.exe"))
        self.assertTrue(cc._is_codex_client_process_name("codex"))
        self.assertFalse(cc._is_codex_client_process_name("codex-command-runner-0.140.0-alpha.2.exe"))

    def test_windows_tasklist_filters_codex_client_processes(self) -> None:
        import deepcat.utils.codex_config as cc

        result = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": '"Codex.exe","1234","Console","1","10,000 K"\n'
                '"codex-command-runner-0.140.0-alpha.2.exe","5678","Console","1","8,000 K"\n',
            },
        )()
        with patch("deepcat.utils.codex_config.os.name", "nt"), patch(
            "deepcat.utils.codex_config.subprocess.run",
            return_value=result,
        ):
            self.assertEqual(cc.list_codex_client_process_names(), ["Codex.exe"])

    def test_codex_app_user_model_id_from_windowsapps_dir(self) -> None:
        import deepcat.utils.codex_config as cc

        app_dir = Path(
            r"C:\Program Files\WindowsApps\OpenAI.Codex_26.609.3341.0_x64__2p2nqsd0c76g0\app"
        )

        self.assertEqual(
            cc._codex_app_user_model_id_from_app_dir(app_dir),
            "OpenAI.Codex_2p2nqsd0c76g0!App",
        )

    def test_launch_codex_client_prefers_official_package_activation(self) -> None:
        import deepcat.utils.codex_config as cc

        with patch("deepcat.utils.codex_config._activate_codex_packaged_app", return_value=True), patch(
            "deepcat.utils.codex_config.subprocess.Popen"
        ) as popen:
            cc.launch_codex_client()

        popen.assert_not_called()

    def test_packaged_activation_falls_back_to_shell_appsfolder(self) -> None:
        import deepcat.utils.codex_config as cc

        with patch("deepcat.utils.codex_config.os.name", "nt"), patch(
            "deepcat.utils.codex_config._codex_app_user_model_id",
            return_value="OpenAI.Codex_2p2nqsd0c76g0!App",
        ), patch(
            "deepcat.utils.codex_config._activate_windows_packaged_app",
            side_effect=PermissionError("拒绝访问"),
        ), patch("deepcat.utils.codex_config.subprocess.Popen") as popen:
            self.assertTrue(cc._activate_codex_packaged_app())

        popen.assert_called_once()
        self.assertEqual(
            popen.call_args.args[0],
            ["explorer.exe", r"shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App"],
        )
        self.assertNotIn("codex-plus-plus", " ".join(popen.call_args.args[0]).lower())

    def test_launch_codex_client_falls_back_to_codex_executable_not_codex_plus_plus(self) -> None:
        import deepcat.utils.codex_config as cc

        with patch("deepcat.utils.codex_config._activate_codex_packaged_app", return_value=False), patch(
            "deepcat.utils.codex_config.shutil.which",
            side_effect=lambda name: r"C:\Program Files\WindowsApps\OpenAI.Codex_x64__pub\app\codex.exe"
            if name == "codex.exe"
            else None,
        ), patch("deepcat.utils.codex_config.subprocess.Popen") as popen:
            cc.launch_codex_client()

        popen.assert_called_once()
        self.assertIn("codex.exe", popen.call_args.args[0][0].lower())
        self.assertNotIn("codex-plus-plus", popen.call_args.args[0][0].lower())

    def test_restart_codex_client_kills_exact_codex_image_then_launches(self) -> None:
        import deepcat.utils.codex_config as cc

        with patch("deepcat.utils.codex_config.os.name", "nt"), patch(
            "deepcat.utils.codex_config.list_codex_client_process_names",
            side_effect=[["codex.exe"], []],
        ), patch("deepcat.utils.codex_config.subprocess.run") as run, patch(
            "deepcat.utils.codex_config.time.sleep"
        ) as sleep, patch(
            "deepcat.utils.codex_config.launch_codex_client"
        ) as launch:
            run.return_value.returncode = 0
            cc.restart_codex_client()

        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["taskkill", "/F", "/T", "/IM", "codex.exe"])
        sleep.assert_called_once()
        launch.assert_called_once()

    def test_stop_codex_client_kills_exact_codex_image(self) -> None:
        import deepcat.utils.codex_config as cc

        with patch("deepcat.utils.codex_config.os.name", "nt"), patch(
            "deepcat.utils.codex_config.list_codex_client_process_names",
            side_effect=[["codex.exe"], []],
        ), patch("deepcat.utils.codex_config.subprocess.run") as run:
            run.return_value.returncode = 0
            cc.stop_codex_client()

        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["taskkill", "/F", "/T", "/IM", "codex.exe"])


class TestUpdaterInstaller(unittest.TestCase):
    def test_script_contains_hash_check_and_full_backup(self) -> None:
        from deepcat.updater import _installer_script_text

        script = _installer_script_text(
            Path("C:/app/updates/pkg.zip"), Path("C:/app"), "deepcat.exe", 1234, "a" * 64
        )
        self.assertIn("$ExpectedHash = '" + "a" * 64 + "'", script)
        self.assertIn("Get-FileHash", script)
        self.assertIn("sha256 mismatch", script)
        # 整目录备份与回滚
        self.assertIn("Move-Item", script)
        self.assertIn("$BackupReady = $false", script)
        self.assertIn("$BackupReady = $true", script)
        self.assertIn("if ($BackupReady)", script)
        self.assertIn("partial install removed before rollback", script)
        self.assertIn("Remove-Item -LiteralPath $item.FullName -Recurse -Force", script)
        self.assertIn("rollback completed", script)

    def test_launch_installer_rejects_tampered_package(self) -> None:
        import deepcat.updater as up

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            zip_path = root / "pkg.zip"
            zip_path.write_bytes(b"tampered-content")
            with self.assertRaises(up.UpdateError):
                up.launch_installer(
                    zip_path, 0, root, "deepcat.exe", asset_digest="sha256:" + "b" * 64
                )
            # 校验失败时不应写出安装脚本
            self.assertFalse((root / "updates" / "install_update.ps1").exists())


class TestGeminiWeb2ApiHardening(unittest.TestCase):
    def test_default_bind_is_loopback(self) -> None:
        from deepcat.gemini_web2api import DEFAULT_CONFIG

        self.assertEqual(DEFAULT_CONFIG["host"], "127.0.0.1")
        self.assertIsNone(DEFAULT_CONFIG["api_key"])
        self.assertGreater(int(DEFAULT_CONFIG["max_request_body_bytes"]), 0)

    def test_redact_api_keys(self) -> None:
        from deepcat.gemini_web2api import redact_api_keys

        msg = "HTTP Error 400: https://generativelanguage.googleapis.com/v1beta/models/x:generateContent?key=AIzaSyABCDEF1234567890abcdefghij&alt=sse"
        out = redact_api_keys(msg)
        self.assertNotIn("AIzaSy" + "ABCDEF", out)
        self.assertIn("key=***", out)
        # 裸 key（不在 query 里）也要打码
        self.assertNotIn("AIzaSyZZZZZZ", redact_api_keys("oops AIzaSyZZZZZZ1234567890abcdefghij"))

    def test_no_unverified_tls_in_source(self) -> None:
        import deepcat.gemini_web2api as g

        source = Path(g.__file__).read_text(encoding="utf-8")
        self.assertNotIn("_create_unverified_context", source)
        # 官方 API key 经请求头传递，不再拼进 URL query
        self.assertNotIn("?key={api_key}", source)
        self.assertIn("x-goog-api-key", source)

    def test_is_public_http_url_blocks_private_targets(self) -> None:
        import deepcat.gemini_web2api as g

        old = g.CONFIG.get("allow_private_image_urls")
        g.CONFIG["allow_private_image_urls"] = False
        try:
            self.assertFalse(g.is_public_http_url("http://127.0.0.1/x.png"))
            self.assertFalse(g.is_public_http_url("http://localhost/x.png"))
            self.assertFalse(g.is_public_http_url("http://192.168.1.10/x.png"))
            self.assertFalse(g.is_public_http_url("http://169.254.169.254/meta"))
            self.assertFalse(g.is_public_http_url("file:///etc/passwd"))
            self.assertFalse(g.is_public_http_url("ftp://example.com/x.png"))
            self.assertFalse(g.is_public_http_url(""))
            self.assertTrue(g.is_public_http_url("https://8.8.8.8/x.png"))
            g.CONFIG["allow_private_image_urls"] = True
            self.assertTrue(g.is_public_http_url("http://192.168.1.10/x.png"))
        finally:
            g.CONFIG["allow_private_image_urls"] = old

    def _handler_stub(self, headers: dict):
        import deepcat.gemini_web2api as g

        handler = g.GeminiHandler.__new__(g.GeminiHandler)
        handler._request_id = "test"

        class Headers:
            def __init__(self, data: dict) -> None:
                self._data = {k.lower(): v for k, v in data.items()}

            def get(self, key, default=None):
                return self._data.get(str(key).lower(), default)

        handler.headers = Headers(headers)  # type: ignore[assignment]
        responses: list[tuple[dict, int]] = []
        handler.send_json = lambda data, status=200: responses.append((data, status))  # type: ignore[method-assign]
        return handler, responses

    def test_check_auth(self) -> None:
        import deepcat.gemini_web2api as g

        old = g.CONFIG.get("api_key")
        try:
            g.CONFIG["api_key"] = None
            handler, responses = self._handler_stub({})
            self.assertTrue(handler._check_auth())
            self.assertEqual(responses, [])

            g.CONFIG["api_key"] = "sk-test-key"
            handler, responses = self._handler_stub({})
            self.assertFalse(handler._check_auth())
            self.assertEqual(responses[0][1], 401)

            handler, responses = self._handler_stub({"X-API-Key": "sk-test-key"})
            self.assertTrue(handler._check_auth())

            handler, responses = self._handler_stub({"Authorization": "Bearer sk-test-key"})
            self.assertTrue(handler._check_auth())

            handler, responses = self._handler_stub({"Authorization": "Bearer wrong"})
            self.assertFalse(handler._check_auth())
            self.assertEqual(responses[0][1], 401)
        finally:
            g.CONFIG["api_key"] = old


if __name__ == "__main__":
    unittest.main()
