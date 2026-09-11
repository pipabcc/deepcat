import json
import logging
import tempfile
import unittest
from pathlib import Path


class TestLogger(unittest.TestCase):
    def test_default_logger_records_errors_only(self) -> None:
        from deepcat.utils.logger import get_logger

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "app.log"
            name = "deepcat_test_errors_only"
            logger = get_logger(name, enable_console=False, file_path=p)
            try:
                logger.info("normal capture")
                logger.warning("normal warning")
                logger.error("real error")
                for handler in logger.handlers:
                    handler.flush()
                text = p.read_text(encoding="utf-8")
                self.assertIn("real error", text)
                self.assertNotIn("normal capture", text)
                self.assertNotIn("normal warning", text)
            finally:
                for handler in list(logger.handlers):
                    logger.removeHandler(handler)
                    handler.close()
                logger.setLevel(logging.NOTSET)

    def test_app_log_file_is_controlled_by_setting(self) -> None:
        import deepcat.utils.log_settings as log_settings
        import deepcat.utils.logger as logger_module

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_log_settings_app_dir = log_settings.get_app_dir
            old_logger_app_dir = logger_module.get_app_dir
            log_settings.get_app_dir = lambda: root  # type: ignore[assignment]
            logger_module.get_app_dir = lambda: root  # type: ignore[assignment]
            name = "deepcat_test_app_log_switch"
            logger = logger_module.get_logger(name, enable_console=False)
            try:
                logger.error("disabled error")
                for handler in logger.handlers:
                    handler.flush()
                app_log = root / "logs" / "app.log"
                self.assertFalse(app_log.exists())

                (root / "settings.json").write_text(
                    json.dumps({"ui": {"logging": {"app_log_enabled": True}}}),
                    encoding="utf-8",
                )
                logger.error("enabled error")
                for handler in logger.handlers:
                    handler.flush()
                text = app_log.read_text(encoding="utf-8")
                self.assertIn("enabled error", text)
                self.assertNotIn("disabled error", text)
            finally:
                for handler in list(logger.handlers):
                    logger.removeHandler(handler)
                    handler.close()
                logger.setLevel(logging.NOTSET)
                log_settings.get_app_dir = old_log_settings_app_dir  # type: ignore[assignment]
                logger_module.get_app_dir = old_logger_app_dir  # type: ignore[assignment]

    def test_shared_file_handler_across_multiple_loggers(self) -> None:
        import deepcat.utils.logger as logger_module

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "shared_app.log"
            logger_a = logger_module.get_logger("test_multi_logger_a", enable_console=False, file_path=p)
            logger_b = logger_module.get_logger("test_multi_logger_b", enable_console=False, file_path=p)
            try:
                handlers_a = [h for h in logger_a.handlers if isinstance(h, logger_module._LazyDirRotatingFileHandler)]
                handlers_b = [h for h in logger_b.handlers if isinstance(h, logger_module._LazyDirRotatingFileHandler)]
                self.assertEqual(len(handlers_a), 1)
                self.assertEqual(len(handlers_b), 1)
                self.assertIs(handlers_a[0], handlers_b[0])

                logger_a.error("msg from a")
                logger_b.error("msg from b")
                handlers_a[0].flush()
                text = p.read_text(encoding="utf-8")
                self.assertIn("msg from a", text)
                self.assertIn("msg from b", text)
            finally:
                for log in (logger_a, logger_b):
                    for handler in list(log.handlers):
                        log.removeHandler(handler)
                        handler.close()
                    log.setLevel(logging.NOTSET)

    def test_safe_rollover_handles_file_lock_without_error(self) -> None:
        import deepcat.utils.logger as logger_module

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "lock_test.log"
            handler = logger_module._LazyDirRotatingFileHandler(
                str(p),
                maxBytes=100,
                backupCount=3,
                encoding="utf-8",
            )
            try:
                # 触发写入
                record = logging.LogRecord("test", logging.ERROR, __file__, 1, "test message" * 10, (), None)
                handler.emit(record)
                handler.flush()

                # 模拟 Windows 句柄锁住源文件，验证 doRollover 不会抛出 WinError 32
                def mock_rotate_fail(src, dest):
                    err = PermissionError("另一个程序正在使用此文件，进程无法访问。")
                    err.winerror = 32
                    raise err

                handler.rotate = mock_rotate_fail
                # 执行 doRollover，验证异常被捕获且 stream 重新打开
                handler.doRollover()
                self.assertIsNotNone(handler.stream)
            finally:
                handler.close()

    def test_prompt_store_multi_threading_access(self) -> None:
        import threading
        from deepcat.prompt_store import PromptStore

        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "prompt.db"
            store = PromptStore(db_path=db_path)
            # 在主线程初始化并加载
            initial = store.load_all_prompts()
            self.assertIsInstance(initial, dict)

            errors: list[Exception] = []

            def worker_task():
                try:
                    for _ in range(10):
                        res = store.load_all_prompts()
                        assert len(res) > 0
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker_task) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            store.close()
            self.assertEqual(len(errors), 0, f"SQLite 跨线程访问发生错误: {errors}")


if __name__ == "__main__":
    unittest.main()
