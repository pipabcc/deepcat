import tempfile
import unittest
import sqlite3
from pathlib import Path

from deepcat.clipboard_history.clipboard_database import ClipboardDatabase


class TestClipboardSearchEnhancement(unittest.TestCase):
    def test_write_paths_use_incremental_stats(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            db = ClipboardDatabase(db_path=str(Path(d) / "test_clipboard.db"))
            try:
                db._refresh_stats = lambda _conn: (_ for _ in ()).throw(  # type: ignore[method-assign]
                    AssertionError("写入路径不应全量刷新统计")
                )
                first_id = db.add_record(content="alpha", content_type="text")
                second_id = db.add_record(content="beta", content_type="text", is_pinned=1)
                self.assertEqual(db.get_total_count(), 2)
                self.assertEqual(db.get_pinned_count(), 1)

                self.assertTrue(db.update_record(int(first_id), "alpha-updated"))
                self.assertTrue(db.update_pin_status(int(first_id), True))
                self.assertEqual(db.get_pinned_count(), 2)
                self.assertTrue(db.delete_record(int(second_id)))
                self.assertEqual(db.get_total_count(), 1)
                self.assertEqual(db.get_pinned_count(), 1)
            finally:
                db = None
                import gc
                gc.collect()

    def test_add_record_deduplicates_normalized_content(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test_clipboard.db"
            db = ClipboardDatabase(db_path=str(db_path))
            try:
                first_id = db.add_record(content="  hello\n\nworld  ", content_type="text")
                second_id = db.add_record(content="hello\nworld", content_type="text")

                self.assertIsNotNone(first_id)
                self.assertEqual(first_id, second_id)
                self.assertEqual(db.get_total_count(), 1)
            finally:
                db = None
                import gc
                gc.collect()

    def test_records_data_size_uses_utf8_byte_count(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test_clipboard.db"
            db = ClipboardDatabase(db_path=str(db_path))
            try:
                db.add_record(content="hello", content_type="text")
                db.add_record(content="世界", content_type="text")

                # content 字节数 5 + 6，并保留每条记录默认 metadata "{}" 的 2 字节。
                self.assertEqual(db.get_records_data_size("text"), "15 B")
                self.assertEqual(db.get_records_data_size(), "15 B")
            finally:
                db = None
                import gc
                gc.collect()

    def test_records_data_size_ignores_missing_managed_image_file_size(self) -> None:
        import deepcat.clipboard_history.clipboard_database as clipboard_database

        with tempfile.TemporaryDirectory() as d:
            app_dir = Path(d) / "app"
            image_dir = app_dir / "data" / "clipboard_images"
            image_dir.mkdir(parents=True)
            existing_image = image_dir / "exists.png"
            missing_image = image_dir / "missing.png"
            existing_image.write_bytes(b"img!")

            old_get_app_dir = clipboard_database.get_app_dir
            clipboard_database.get_app_dir = lambda: app_dir  # type: ignore[assignment]
            db = None
            try:
                db = ClipboardDatabase(db_path=str(Path(d) / "test_clipboard.db"))
                db.add_record(
                    content="exists",
                    content_type="image",
                    file_path=str(existing_image),
                    file_size=1000,
                )
                db.add_record(
                    content="missing",
                    content_type="image",
                    file_path=str(missing_image),
                    file_size=5000,
                )

                expected = (
                    len(b"exists")
                    + len(str(existing_image).encode("utf-8"))
                    + len(b"{}")
                    + existing_image.stat().st_size
                    + len(b"missing")
                    + len(str(missing_image).encode("utf-8"))
                    + len(b"{}")
                )
                self.assertEqual(db.get_records_data_size("image"), f"{expected} B")
            finally:
                clipboard_database.get_app_dir = old_get_app_dir  # type: ignore[assignment]
                db = None
                import gc
                gc.collect()

    def test_image_hash_deduplicates_physical_file(self) -> None:
        import deepcat.clipboard_history.clipboard_database as clipboard_database

        with tempfile.TemporaryDirectory() as d:
            app_dir = Path(d) / "app"
            image_dir = app_dir / "data" / "clipboard_images"
            image_dir.mkdir(parents=True)
            first = image_dir / "first.png"
            second = image_dir / "second.png"
            first.write_bytes(b"same-image")
            second.write_bytes(b"same-image")
            old_get_app_dir = clipboard_database.get_app_dir
            clipboard_database.get_app_dir = lambda: app_dir  # type: ignore[assignment]
            try:
                db = ClipboardDatabase(db_path=str(Path(d) / "test_clipboard.db"))
                first_id = db.add_record("first", "image", file_path=str(first))
                second_id = db.add_record("second", "image", file_path=str(second))
                self.assertEqual(first_id, second_id)
                self.assertTrue(first.exists())
                self.assertFalse(second.exists())
                conn = db._connect()
                try:
                    assets = conn.execute(
                        "SELECT file_path, ref_count FROM clipboard_images"
                    ).fetchall()
                finally:
                    conn.close()
                self.assertEqual(len(assets), 1)
                self.assertEqual(int(assets[0]["ref_count"]), 1)
            finally:
                clipboard_database.get_app_dir = old_get_app_dir  # type: ignore[assignment]
                db = None
                import gc
                gc.collect()

    def test_image_reference_count_deletes_file_after_last_record(self) -> None:
        import deepcat.clipboard_history.clipboard_database as clipboard_database

        with tempfile.TemporaryDirectory() as d:
            app_dir = Path(d) / "app"
            image_dir = app_dir / "data" / "clipboard_images"
            image_dir.mkdir(parents=True)
            image_path = image_dir / "shared.png"
            image_path.write_bytes(b"shared-image")
            old_get_app_dir = clipboard_database.get_app_dir
            clipboard_database.get_app_dir = lambda: app_dir  # type: ignore[assignment]
            db = None
            try:
                db = ClipboardDatabase(db_path=str(Path(d) / "test_clipboard.db"))
                first_id = int(db.add_record("first", "image", file_path=str(image_path)))
                conn = db._connect()
                try:
                    row = conn.execute(
                        "SELECT * FROM clipboard_records WHERE id = ?", (first_id,)
                    ).fetchone()
                    cur = conn.execute(
                        """
                        INSERT INTO clipboard_records
                        (content, content_type, file_type, file_path, file_size, source_app,
                         is_pinned, error_info, metadata, normalized_hash, image_hash, data_size_bytes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "second",
                            row["content_type"],
                            row["file_type"],
                            row["file_path"],
                            row["file_size"],
                            row["source_app"],
                            0,
                            row["error_info"],
                            row["metadata"],
                            "second-hash",
                            row["image_hash"],
                            row["data_size_bytes"],
                        ),
                    )
                    second_id = int(cur.lastrowid)
                    conn.execute(
                        "UPDATE clipboard_images SET ref_count = 2 WHERE content_hash = ?",
                        (row["image_hash"],),
                    )
                    db._upsert_fts(conn, second_id)
                    db._refresh_stats(conn)
                    conn.commit()
                finally:
                    conn.close()

                self.assertTrue(db.delete_record(first_id))
                self.assertTrue(image_path.exists())
                self.assertTrue(db.delete_record(second_id))
                self.assertFalse(image_path.exists())
            finally:
                clipboard_database.get_app_dir = old_get_app_dir  # type: ignore[assignment]
                db = None
                import gc
                gc.collect()

    def test_retention_limits_preserve_pinned_records(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            db = ClipboardDatabase(
                db_path=str(Path(d) / "test_clipboard.db"),
                auto_cleanup_enabled=False,
            )
            try:
                pinned_id = db.add_record("pinned", "text", is_pinned=1)
                db.add_record("old-1", "text")
                db.add_record("old-2", "text")
                db.auto_cleanup_enabled = True
                db.max_records = 2
                db.max_capacity_bytes = 20
                deleted = db.cleanup_retention()
                self.assertGreaterEqual(deleted, 1)
                self.assertIsNotNone(db.get_record_by_id(int(pinned_id)))
                self.assertEqual(db.get_pinned_count(), 1)
                self.assertLessEqual(db.get_total_count(), 2)
            finally:
                db = None
                import gc
                gc.collect()

    def test_migration_removes_redundant_content_index(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test_clipboard.db"
            db = ClipboardDatabase(db_path=str(db_path))
            db = None
            import gc
            gc.collect()
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE INDEX idx_clipboard_records_content ON clipboard_records(content)")
            conn.commit()
            conn.close()
            db = ClipboardDatabase(db_path=str(db_path))
            conn = sqlite3.connect(db_path)
            index = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_clipboard_records_content'"
            ).fetchone()
            conn.close()
            self.assertIsNone(index)
            db = None
            gc.collect()

    def test_search_fuzzy_and_fts_combination(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test_clipboard.db"
            db = ClipboardDatabase(db_path=str(db_path))

            # 插入测试数据
            # 1. 包含 2800个，在 FTS 可能会因分词搜不到数字，但 LIKE 能模糊查到
            db.add_record(content="目前2800个team号池，自己站里没啥人用啊。", content_type="text")
            # 2. 其它非相关文本
            db.add_record(content="今天天气真好，去公园散散步。", content_type="text")
            # 3. 另一个也包含数字的文本
            db.add_record(content="我的学号后四位是2800号，请大家记住。", content_type="text")

            # 测试普通模糊检索：当搜索 "2800" 时，预期匹配到含有 2800 的两条记录
            results = db.search_records(keyword="2800", limit=10, offset=0)
            self.assertEqual(len(results), 2)

            contents = [r[1] for r in results]
            self.assertIn("目前2800个team号池，自己站里没啥人用啊。", contents)
            self.assertIn("我的学号后四位是2800号，请大家记住。", contents)

            # 测试分页 limit=1，由于以时间戳降序排列，第一条应该是最新插入的
            results_page1 = db.search_records(keyword="2800", limit=1, offset=0)
            self.assertEqual(len(results_page1), 1)

            results_page2 = db.search_records(keyword="2800", limit=1, offset=1)
            self.assertEqual(len(results_page2), 1)

            # 两个分页项组合起来应该刚好去重得到全部结果
            self.assertNotEqual(results_page1[0][0], results_page2[0][0])

            # 测试搜索不存在的关键词，预期返回空列表
            results_empty = db.search_records(keyword="99999", limit=10, offset=0)
            self.assertEqual(len(results_empty), 0)

            # 显式清除数据库引用并回收垃圾以关闭连接，避免 Windows 文件占用
            db = None
            import gc
            gc.collect()

    def test_normalized_hash_migration_and_deduplication(self) -> None:
        import sqlite3
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test_clipboard.db"

            # 1. 模拟旧版本数据库（无 normalized_hash 列）
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
            CREATE TABLE clipboard_records (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                content       TEXT NOT NULL,
                content_type  TEXT DEFAULT 'text',
                file_type     TEXT DEFAULT '',
                file_path     TEXT DEFAULT '',
                file_size     INTEGER DEFAULT 0,
                source_app    TEXT DEFAULT 'Unknown',
                timestamp     DATETIME DEFAULT CURRENT_TIMESTAMP,
                usage_count   INTEGER DEFAULT 0,
                tags          TEXT DEFAULT '',
                is_favorite   INTEGER DEFAULT 0,
                is_pinned     INTEGER DEFAULT 0,
                error_info    TEXT DEFAULT '',
                metadata      TEXT DEFAULT '{}'
            );
            """)

            # 插入两条无哈希记录
            conn.execute("INSERT INTO clipboard_records (content, content_type) VALUES (?, ?)", ("  hello  ", "text"))
            conn.execute("INSERT INTO clipboard_records (content, content_type) VALUES (?, ?)", ("world", "text"))
            conn.commit()
            conn.close()

            # 2. 用 ClipboardDatabase 载入它，自动触发迁移与哈希回填
            db = ClipboardDatabase(db_path=str(db_path))
            try:
                # 校验添加的列和哈希回填是否成功
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT id, content, normalized_hash FROM clipboard_records ORDER BY id").fetchall()
                self.assertEqual(len(rows), 2)

                # 第一条的规范内容应为 "hello"，其 MD5 值
                h1 = hashlib.md5(b"hello").hexdigest()
                self.assertEqual(rows[0]["normalized_hash"], h1)

                # 第二条的规范内容应为 "world"，其 MD5 值
                h2 = hashlib.md5(b"world").hexdigest()
                self.assertEqual(rows[1]["normalized_hash"], h2)
                conn.close()

                # 3. 校验利用哈希进行 O(1) 去重
                # 再次插入一条规范内容同为 "hello" 的文本，应去重并置顶（即返回 1）
                dup_id = db.add_record(content="hello\n", content_type="text")
                self.assertEqual(dup_id, 1)
                self.assertEqual(db.get_total_count(), 2)
            finally:
                db = None
                import gc
                gc.collect()


if __name__ == "__main__":
    unittest.main()
