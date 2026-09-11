import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path


class TestDataManagerBackupRestore(unittest.TestCase):
    def test_backup_sqlite_snapshot_includes_committed_wal_data(self) -> None:
        import deepcat.core.data_manager as dm

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app_dir = root / "app"
            data_dir = app_dir / "data"
            data_dir.mkdir(parents=True)
            db_path = data_dir / "clipboard_history.db"

            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, content TEXT)")
                conn.execute("INSERT INTO records (content) VALUES (?)", ("wal-row",))
                conn.commit()

                old_get_app_dir = dm.get_app_dir
                dm.get_app_dir = lambda: app_dir  # type: ignore[assignment]
                try:
                    backup_path = root / "backup.zip"
                    ok, msg = dm.backup_data(backup_path, {"clipboard": True})
                finally:
                    dm.get_app_dir = old_get_app_dir  # type: ignore[assignment]
            finally:
                conn.close()

            self.assertTrue(ok, msg)
            with zipfile.ZipFile(backup_path, "r") as zipf:
                names = zipf.namelist()
                self.assertIn("data/clipboard_history.db", names)
                self.assertNotIn("data/clipboard_history.db.db-wal", names)
                extracted_db = root / "snapshot.db"
                extracted_db.write_bytes(zipf.read("data/clipboard_history.db"))

            with closing(sqlite3.connect(str(extracted_db))) as check_conn:
                row = check_conn.execute("SELECT content FROM records").fetchone()
            self.assertEqual(row[0], "wal-row")

    def test_restore_rejects_zip_path_traversal(self) -> None:
        import deepcat.core.data_manager as dm

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app_dir = root / "app"
            app_dir.mkdir()
            malicious_zip = root / "malicious.zip"
            with zipfile.ZipFile(malicious_zip, "w") as zipf:
                zipf.writestr("../evil.txt", "owned")

            old_get_app_dir = dm.get_app_dir
            dm.get_app_dir = lambda: app_dir  # type: ignore[assignment]
            try:
                ok, msg = dm.restore_data(malicious_zip)
            finally:
                dm.get_app_dir = old_get_app_dir  # type: ignore[assignment]

            self.assertFalse(ok)
            self.assertIn("不允许", msg)
            self.assertFalse((root / "evil.txt").exists())

    def test_restore_allows_known_deepcat_files(self) -> None:
        import deepcat.core.data_manager as dm

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app_dir = root / "app"
            app_dir.mkdir()
            backup_zip = root / "backup.zip"
            with zipfile.ZipFile(backup_zip, "w") as zipf:
                zipf.writestr("settings.json", '{"version": 6}')
                zipf.writestr("data/table_notes.db", b"sqlite-bytes")

            old_get_app_dir = dm.get_app_dir
            dm.get_app_dir = lambda: app_dir  # type: ignore[assignment]
            try:
                ok, msg = dm.restore_data(backup_zip)
            finally:
                dm.get_app_dir = old_get_app_dir  # type: ignore[assignment]

            self.assertTrue(ok, msg)
            self.assertEqual((app_dir / "settings.json").read_text(encoding="utf-8"), '{"version": 6}')
            self.assertEqual((app_dir / "data" / "table_notes.db").read_bytes(), b"sqlite-bytes")

    def test_backup_and_restore_include_ai_history_and_todo_databases(self) -> None:
        import deepcat.core.data_manager as dm

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app_dir = root / "app"
            data_dir = app_dir / "data"
            data_dir.mkdir(parents=True)

            for db_name, table_name in (
                ("translation_history.db", "history_records"),
                ("todo.db", "items"),
            ):
                with closing(sqlite3.connect(str(data_dir / db_name))) as conn:
                    conn.execute(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY, content TEXT)")
                    conn.execute(f"INSERT INTO {table_name} (content) VALUES ('ok')")
                    conn.commit()

            old_get_app_dir = dm.get_app_dir
            dm.get_app_dir = lambda: app_dir  # type: ignore[assignment]
            try:
                backup_path = root / "backup.zip"
                ok, msg = dm.backup_data(
                    backup_path,
                    {"ai_chat_history": True, "todo": True},
                )
            finally:
                dm.get_app_dir = old_get_app_dir  # type: ignore[assignment]

            self.assertTrue(ok, msg)
            with zipfile.ZipFile(backup_path, "r") as zipf:
                names = set(zipf.namelist())
            self.assertIn("data/translation_history.db", names)
            self.assertIn("data/todo.db", names)

            restored_app = root / "restored_app"
            restored_app.mkdir()
            old_get_app_dir = dm.get_app_dir
            dm.get_app_dir = lambda: restored_app  # type: ignore[assignment]
            try:
                ok, msg = dm.restore_data(backup_path)
            finally:
                dm.get_app_dir = old_get_app_dir  # type: ignore[assignment]

            self.assertTrue(ok, msg)
            self.assertTrue((restored_app / "data" / "translation_history.db").exists())
            self.assertTrue((restored_app / "data" / "todo.db").exists())

    def test_clear_logs_removes_log_files(self) -> None:
        import deepcat.core.data_manager as dm

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app_dir = root / "app"
            log_dir = app_dir / "logs"
            nested_dir = log_dir / "old"
            nested_dir.mkdir(parents=True)
            (log_dir / "app.log").write_text("main log", encoding="utf-8")
            (log_dir / "app.log.1").write_text("rotated log", encoding="utf-8")
            (nested_dir / "trace.log").write_text("trace", encoding="utf-8")

            old_get_app_dir = dm.get_app_dir
            dm.get_app_dir = lambda: app_dir  # type: ignore[assignment]
            try:
                ok, msg = dm.clear_data_class("logs", object())
            finally:
                dm.get_app_dir = old_get_app_dir  # type: ignore[assignment]

            self.assertTrue(ok, msg)
            self.assertTrue(log_dir.exists())
            self.assertEqual([p for p in log_dir.rglob("*") if p.is_file()], [])

    def test_light_cleanup_clears_logs_and_app_temp(self) -> None:
        import deepcat.core.data_manager as dm

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app_dir = root / "app"
            log_dir = app_dir / "logs"
            temp_dir = app_dir / "data" / "temp"
            log_dir.mkdir(parents=True)
            temp_dir.mkdir(parents=True)
            (log_dir / "app.log").write_bytes(b"log")
            (temp_dir / "download.tmp").write_bytes(b"temp")

            old_get_app_dir = dm.get_app_dir
            dm.get_app_dir = lambda: app_dir  # type: ignore[assignment]
            try:
                self.assertEqual(dm.estimate_light_cleanup_size(), 7)
                ok, msg = dm.clear_data_class("light_cleanup", object())
            finally:
                dm.get_app_dir = old_get_app_dir  # type: ignore[assignment]

            self.assertTrue(ok, msg)
            self.assertEqual([p for p in log_dir.rglob("*") if p.is_file()], [])
            self.assertEqual([p for p in temp_dir.rglob("*") if p.is_file()], [])

    def test_clear_todo_does_not_refresh_unbuilt_todo_page(self) -> None:
        import deepcat.core.data_manager as dm

        class DummyTodoStore:
            def __init__(self) -> None:
                self.saved_items = None

            def save_items(self, items):
                self.saved_items = list(items)

        class DummyMainWindow:
            def __init__(self) -> None:
                self._todo_store = DummyTodoStore()
                self._todo_items = [{"id": "todo-1", "title": "旧待办"}]
                self.tray_refreshed = False
                self.checks_scheduled = False

            def _refresh_todo_list(self) -> None:
                raise AssertionError("待办页未构建时不应刷新列表")

            def _refresh_todo_calendar_markers(self) -> None:
                raise AssertionError("待办页未构建时不应刷新日历")

            def _refresh_tray_tooltip(self) -> None:
                self.tray_refreshed = True

            def _schedule_todo_checks(self) -> None:
                self.checks_scheduled = True

        main_window = DummyMainWindow()
        ok, msg = dm.clear_data_class("todo", main_window)

        self.assertTrue(ok, msg)
        self.assertEqual(main_window._todo_store.saved_items, [])
        self.assertEqual(main_window._todo_items, [])
        self.assertTrue(main_window.tray_refreshed)
        self.assertTrue(main_window.checks_scheduled)

    def test_data_size_ignores_empty_sqlite_shell_for_todo(self) -> None:
        import deepcat.ui.data_management_page as dmp

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app_dir = root / "app"
            data_dir = app_dir / "data"
            data_dir.mkdir(parents=True)
            db_path = data_dir / "todo.db"
            with closing(sqlite3.connect(str(db_path))) as conn:
                conn.execute(
                    "CREATE TABLE items (id TEXT PRIMARY KEY, data TEXT NOT NULL DEFAULT '{}', sort_order INTEGER NOT NULL DEFAULT 0)"
                )
                conn.commit()
            db_path.with_name("todo.db-shm").write_bytes(b"x" * 32768)

            old_get_app_dir = dmp.get_app_dir
            dmp.get_app_dir = lambda: app_dir  # type: ignore[assignment]
            try:
                self.assertEqual(dmp._calculate_data_size_str("todo"), "0 KB")
                with closing(sqlite3.connect(str(db_path))) as conn:
                    conn.execute(
                        "INSERT INTO items (id, data, sort_order) VALUES (?, ?, ?)",
                        ("todo-1", '{"title":"待办内容"}', 0),
                    )
                    conn.commit()
                self.assertNotEqual(dmp._calculate_data_size_str("todo"), "0 KB")
            finally:
                dmp.get_app_dir = old_get_app_dir  # type: ignore[assignment]

    def test_ai_history_size_counts_translation_and_qa_user_content(self) -> None:
        import deepcat.ui.data_management_page as dmp

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app_dir = root / "app"
            data_dir = app_dir / "data"
            data_dir.mkdir(parents=True)
            db_path = data_dir / "translation_history.db"
            with closing(sqlite3.connect(str(db_path))) as conn:
                conn.execute(
                    """
                    CREATE TABLE history_records (
                        id INTEGER PRIMARY KEY,
                        task_type TEXT NOT NULL DEFAULT 'translate',
                        source_text TEXT NOT NULL DEFAULT '',
                        result_text TEXT NOT NULL DEFAULT '',
                        prompt_text TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL DEFAULT '',
                        model_name TEXT NOT NULL DEFAULT '',
                        source_lang TEXT NOT NULL DEFAULT '',
                        target_lang TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO history_records (task_type, source_text, result_text) VALUES ('translate', ?, ?)",
                    ("source", "result"),
                )
                conn.commit()

            old_get_app_dir = dmp.get_app_dir
            dmp.get_app_dir = lambda: app_dir  # type: ignore[assignment]
            try:
                self.assertEqual(dmp._calculate_data_size_bytes("ai_chat_history"), len(b"sourceresult"))
                with closing(sqlite3.connect(str(db_path))) as conn:
                    conn.execute(
                        "INSERT INTO history_records (task_type, source_text, result_text, prompt_text) VALUES ('qa', ?, ?, ?)",
                        ("question", "answer", "context"),
                    )
                    conn.commit()
                self.assertEqual(
                    dmp._calculate_data_size_bytes("ai_chat_history"),
                    len(b"sourceresultquestionanswercontext"),
                )
            finally:
                dmp.get_app_dir = old_get_app_dir  # type: ignore[assignment]

    def test_clear_ai_history_clears_translation_and_qa_records(self) -> None:
        import deepcat.core.data_manager as dm
        from deepcat.translation_history_store import TranslationHistoryStore

        class DummyPanel:
            def __init__(self, store: TranslationHistoryStore) -> None:
                self._history_store = store
                self._history_sidebar = None
                self._history_showing = False

            def objectName(self) -> str:
                return "dummy-panel"

        class DummyMainWindow:
            def __init__(self, panel: DummyPanel) -> None:
                self._selection_translate_panel = panel
                self._post_actions = None

        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "translation_history.db"
            store = TranslationHistoryStore(db_path)
            try:
                store.add_record("translate", "source", "result", "model", "auto", "zh", 0.1)
                store.add_record("qa", "question", "answer", "model", "auto", "zh", 0.2)

                ok, msg = dm.clear_data_class("ai_chat_history", DummyMainWindow(DummyPanel(store)))

                self.assertTrue(ok, msg)
                self.assertEqual(store.get_count(), 0)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
