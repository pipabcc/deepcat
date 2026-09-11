import atexit
import json
import os
import tempfile
import unittest
from pathlib import Path


def _reset_log_settings_cache() -> None:
    import deepcat.utils.log_settings as log_settings

    log_settings._CACHE_SIGNATURE = None
    log_settings._CACHE_VALUE = None


class TestLogSettingsCache(unittest.TestCase):
    def setUp(self) -> None:
        _reset_log_settings_cache()

    def tearDown(self) -> None:
        _reset_log_settings_cache()

    def test_cache_avoids_reparse_until_file_changes(self) -> None:
        import deepcat.utils.log_settings as log_settings

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_app_dir = log_settings.get_app_dir
            old_parse = log_settings._parse_log_settings
            calls = []

            def counting_parse(path):
                calls.append(str(path))
                return old_parse(path)

            log_settings.get_app_dir = lambda: root  # type: ignore[assignment]
            log_settings._parse_log_settings = counting_parse  # type: ignore[assignment]
            try:
                settings_path = root / "settings.json"
                settings_path.write_text(
                    json.dumps({"ui": {"logging": {"app_log_enabled": True, "crash_log_enabled": False}}}),
                    encoding="utf-8",
                )
                first = log_settings.read_log_settings()
                second = log_settings.read_log_settings()
                self.assertTrue(first["app_log_enabled"])
                self.assertEqual(first, second)
                self.assertEqual(len(calls), 1)

                settings_path.write_text(
                    json.dumps({"ui": {"logging": {"app_log_enabled": False, "crash_log_enabled": True}}}),
                    encoding="utf-8",
                )
                # 同尺寸快速重写可能落在文件系统时间戳粒度内，显式 bump mtime 模拟真实的"稍后修改"
                st = settings_path.stat()
                os.utime(settings_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
                third = log_settings.read_log_settings()
                self.assertFalse(third["app_log_enabled"])
                self.assertTrue(third["crash_log_enabled"])
                self.assertEqual(len(calls), 2)
            finally:
                log_settings.get_app_dir = old_app_dir  # type: ignore[assignment]
                log_settings._parse_log_settings = old_parse  # type: ignore[assignment]

    def test_missing_file_returns_defaults(self) -> None:
        import deepcat.utils.log_settings as log_settings

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_app_dir = log_settings.get_app_dir
            log_settings.get_app_dir = lambda: root  # type: ignore[assignment]
            try:
                value = log_settings.read_log_settings()
                self.assertEqual(value, {"app_log_enabled": False, "crash_log_enabled": False})
            finally:
                log_settings.get_app_dir = old_app_dir  # type: ignore[assignment]

    def test_cached_result_is_copy(self) -> None:
        import deepcat.utils.log_settings as log_settings

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_app_dir = log_settings.get_app_dir
            log_settings.get_app_dir = lambda: root  # type: ignore[assignment]
            try:
                (root / "settings.json").write_text(
                    json.dumps({"ui": {"logging": {"app_log_enabled": True}}}),
                    encoding="utf-8",
                )
                first = log_settings.read_log_settings()
                first["app_log_enabled"] = False
                second = log_settings.read_log_settings()
                self.assertTrue(second["app_log_enabled"])
            finally:
                log_settings.get_app_dir = old_app_dir  # type: ignore[assignment]


class TestCrashLogRotation(unittest.TestCase):
    def setUp(self) -> None:
        _reset_log_settings_cache()

    def tearDown(self) -> None:
        _reset_log_settings_cache()

    def test_breadcrumbs_rotate_crash_log(self) -> None:
        import deepcat.utils.crash_reporter as cr
        import deepcat.utils.log_settings as log_settings
        import deepcat.utils.logger as logger_module

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_log_settings_app_dir = log_settings.get_app_dir
            old_logger_app_dir = logger_module.get_app_dir
            old_max_bytes = cr._MAX_CRASH_LOG_BYTES
            log_settings.get_app_dir = lambda: root  # type: ignore[assignment]
            logger_module.get_app_dir = lambda: root  # type: ignore[assignment]
            cr._MAX_CRASH_LOG_BYTES = 1024
            try:
                (root / "settings.json").write_text(
                    json.dumps({"ui": {"logging": {"crash_log_enabled": True}}}),
                    encoding="utf-8",
                )
                filler = "x" * 200
                for i in range(30):
                    cr.write_crash_breadcrumb("test.rotation", index=i, filler=filler)
                cr.uninstall_crash_reporter()

                crash_log = root / "logs" / "crash.log"
                backup = root / "logs" / "crash.log.1"
                self.assertTrue(crash_log.exists())
                self.assertTrue(backup.exists())
                self.assertLessEqual(crash_log.stat().st_size, 2048)
            finally:
                cr.uninstall_crash_reporter()
                cr._MAX_CRASH_LOG_BYTES = old_max_bytes
                log_settings.get_app_dir = old_log_settings_app_dir  # type: ignore[assignment]
                logger_module.get_app_dir = old_logger_app_dir  # type: ignore[assignment]

    def test_disabled_crash_log_writes_nothing(self) -> None:
        import deepcat.utils.crash_reporter as cr
        import deepcat.utils.log_settings as log_settings
        import deepcat.utils.logger as logger_module

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_log_settings_app_dir = log_settings.get_app_dir
            old_logger_app_dir = logger_module.get_app_dir
            log_settings.get_app_dir = lambda: root  # type: ignore[assignment]
            logger_module.get_app_dir = lambda: root  # type: ignore[assignment]
            try:
                cr.write_crash_breadcrumb("test.disabled")
                self.assertFalse((root / "logs" / "crash.log").exists())
            finally:
                cr.uninstall_crash_reporter()
                log_settings.get_app_dir = old_log_settings_app_dir  # type: ignore[assignment]
                logger_module.get_app_dir = old_logger_app_dir  # type: ignore[assignment]


class TestLlamaServerLogRotation(unittest.TestCase):
    def test_rotate_log_if_needed(self) -> None:
        import deepcat.local_hunyuan_server as srv

        # 防止 atexit 钩子在测试进程退出时 taskkill 外部 llama-server
        atexit.unregister(srv.stop_server_now)
        old_max = srv.LOG_MAX_BYTES
        srv.LOG_MAX_BYTES = 100
        try:
            with tempfile.TemporaryDirectory() as d:
                log_path = Path(d) / "llama_cpp_server_test.log"

                srv._rotate_log_if_needed(log_path)
                self.assertFalse(log_path.exists())

                log_path.write_text("a" * 50, encoding="utf-8")
                srv._rotate_log_if_needed(log_path)
                self.assertTrue(log_path.exists())

                log_path.write_text("b" * 200, encoding="utf-8")
                srv._rotate_log_if_needed(log_path)
                backup1 = log_path.with_name(log_path.name + ".1")
                self.assertFalse(log_path.exists())
                self.assertEqual(backup1.read_text(encoding="utf-8"), "b" * 200)

                log_path.write_text("c" * 200, encoding="utf-8")
                srv._rotate_log_if_needed(log_path)
                backup2 = log_path.with_name(log_path.name + ".2")
                self.assertEqual(backup1.read_text(encoding="utf-8"), "c" * 200)
                self.assertEqual(backup2.read_text(encoding="utf-8"), "b" * 200)

                log_path.write_text("e" * 200, encoding="utf-8")
                srv._rotate_log_if_needed(log_path)
                self.assertEqual(backup1.read_text(encoding="utf-8"), "e" * 200)
                self.assertEqual(backup2.read_text(encoding="utf-8"), "c" * 200)
                self.assertFalse(log_path.with_name(log_path.name + ".3").exists())
        finally:
            srv.LOG_MAX_BYTES = old_max


class TestAtomicSettingsWrite(unittest.TestCase):
    def test_atomic_write_json_creates_bak_and_no_tmp_leftover(self) -> None:
        import deepcat.settings_store as ss

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_app_dir = ss.get_app_dir
            ss.get_app_dir = lambda: root  # type: ignore[assignment]
            try:
                p = root / "settings.json"
                ss._atomic_write_json(p, {"version": 1})
                self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"version": 1})
                self.assertFalse((root / "settings.json.tmp").exists())
                self.assertFalse((root / "settings.json.bak").exists())

                ss._atomic_write_json(p, {"version": 2})
                self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"version": 2})
                bak = root / "settings.json.bak"
                self.assertEqual(json.loads(bak.read_text(encoding="utf-8")), {"version": 1})
                self.assertFalse((root / "settings.json.tmp").exists())
                self.assertFalse((root / "settings.json.bak.tmp").exists())
            finally:
                ss.get_app_dir = old_app_dir  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
