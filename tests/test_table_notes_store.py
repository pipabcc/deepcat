import tempfile
import unittest
from pathlib import Path

from deepcat.table_notes_store import TableNotesStore


class TestTableNotesStore(unittest.TestCase):
    def test_group_name_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test_notes.db"
            store = TableNotesStore(db_path=db_path)

            # 测试初始状态
            table_tabs, active_table = store.load_table_tabs()
            self.assertEqual(len(table_tabs), 1)
            self.assertEqual(table_tabs[0]["group_name"], "")

            note_tabs, active_note = store.load_note_tabs()
            self.assertEqual(len(note_tabs), 1)
            self.assertEqual(note_tabs[0]["group_name"], "")

            # 写入带 group_name 的数据
            test_table_tabs = [
                {"name": "表格A", "data": [], "column_widths": [], "group_name": "分组1", "password": ""},
                {"name": "表格B", "data": [], "column_widths": [], "group_name": "分组2", "password": ""},
            ]
            store.save_all_table_tabs(test_table_tabs, 1)

            test_note_tabs = [
                {"name": "笔记A", "html": "a", "group_name": "分组1", "password": ""},
                {"name": "笔记B", "html": "b", "group_name": "", "password": ""},
            ]
            store.save_all_note_tabs(test_note_tabs, 0)

            # 重新加载验证
            loaded_table_tabs, active_table = store.load_table_tabs()
            self.assertEqual(len(loaded_table_tabs), 2)
            self.assertEqual(loaded_table_tabs[0]["name"], "表格A")
            self.assertEqual(loaded_table_tabs[0]["group_name"], "分组1")
            self.assertEqual(loaded_table_tabs[1]["name"], "表格B")
            self.assertEqual(loaded_table_tabs[1]["group_name"], "分组2")
            self.assertEqual(active_table, 1)

            loaded_note_tabs, active_note = store.load_note_tabs()
            self.assertEqual(len(loaded_note_tabs), 2)
            self.assertEqual(loaded_note_tabs[0]["name"], "笔记A")
            self.assertEqual(loaded_note_tabs[0]["group_name"], "分组1")
            self.assertIn("ima_config", loaded_note_tabs[0])
            self.assertEqual(loaded_note_tabs[1]["name"], "笔记B")
            self.assertEqual(loaded_note_tabs[1]["group_name"], "")
            self.assertEqual(active_note, 0)

            store.close()

    def test_note_ima_config_persistence_and_secret_protection(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test_notes.db"
            store = TableNotesStore(db_path=db_path)

            store.save_all_note_tabs(
                [
                    {
                        "name": "笔记A",
                        "html": "a",
                        "group_name": "",
                        "password": "",
                        "ima_config": {
                            "enabled": True,
                            "auto_sync_enabled": True,
                            "client_id": "client-id",
                            "api_key": "api-key",
                            "remote_note_id": "note-1",
                            "remote_note_title": "远端笔记",
                            "knowledge_base_enabled": True,
                            "knowledge_base_id": "kb-1",
                            "knowledge_base_name": "知识库",
                        },
                    }
                ],
                0,
            )

            raw = store._db.execute("SELECT ima_config FROM note_tabs WHERE tab_index = 0").fetchone()
            self.assertIsNotNone(raw)
            self.assertNotIn("api-key", str(raw["ima_config"]))

            loaded_note_tabs, active_note = store.load_note_tabs()
            self.assertEqual(active_note, 0)
            cfg = loaded_note_tabs[0]["ima_config"]
            self.assertTrue(cfg["enabled"])
            self.assertTrue(cfg["auto_sync_enabled"])
            self.assertEqual(cfg["client_id"], "client-id")
            self.assertEqual(cfg["api_key"], "api-key")
            self.assertEqual(cfg["remote_note_id"], "note-1")
            self.assertTrue(cfg["knowledge_base_enabled"])
            self.assertEqual(cfg["knowledge_base_id"], "kb-1")

            store.close()

    def test_user_renamed_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test_notes.db"
            store = TableNotesStore(db_path=db_path)

            # 初始状态
            note_tabs, _ = store.load_note_tabs()
            self.assertEqual(len(note_tabs), 1)
            self.assertFalse(note_tabs[0].get("_user_renamed", False))

            # 写入带 _user_renamed 的数据
            test_note_tabs = [
                {"name": "笔记A", "html": "a", "group_name": "分组1", "password": "", "_user_renamed": True},
                {"name": "笔记B", "html": "b", "group_name": "", "password": "", "_user_renamed": False},
            ]
            store.save_all_note_tabs(test_note_tabs, 0)

            # 重新加载验证
            loaded_note_tabs, _ = store.load_note_tabs()
            self.assertEqual(len(loaded_note_tabs), 2)
            self.assertTrue(loaded_note_tabs[0]["_user_renamed"])
            self.assertFalse(loaded_note_tabs[1]["_user_renamed"])

            # 测试单个保存
            store.save_note_tab(0, "笔记A修改", "a_modified")
            loaded_note_tabs2, _ = store.load_note_tabs()
            self.assertTrue(loaded_note_tabs2[0]["_user_renamed"])

            store.close()

    def test_single_tab_save_persists_all_fields(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            store = TableNotesStore(db_path=Path(d) / "test_notes.db")

            store.save_table_tab(0, {
                "name": "项目表",
                "data": [["任务", "完成"]],
                "column_widths": [180, 96],
                "password": "table-password-hash",
                "group_name": "项目组",
            })
            store.save_note_tab(0, {
                "name": "项目笔记",
                "html": "<p>正文</p>",
                "password": "note-password-hash",
                "group_name": "项目组",
                "ima_config": {
                    "enabled": True,
                    "client_id": "client-id",
                    "api_key": "api-key",
                },
                "_user_renamed": True,
            })

            table_tabs, _ = store.load_table_tabs()
            note_tabs, _ = store.load_note_tabs()
            self.assertEqual(table_tabs[0]["data"], [["任务", "完成"]])
            self.assertEqual(table_tabs[0]["column_widths"], [180, 96])
            self.assertEqual(table_tabs[0]["password"], "table-password-hash")
            self.assertEqual(table_tabs[0]["group_name"], "项目组")
            self.assertEqual(note_tabs[0]["html"], "<p>正文</p>")
            self.assertEqual(note_tabs[0]["password"], "note-password-hash")
            self.assertEqual(note_tabs[0]["group_name"], "项目组")
            self.assertEqual(note_tabs[0]["ima_config"]["client_id"], "client-id")
            self.assertEqual(note_tabs[0]["ima_config"]["api_key"], "api-key")
            self.assertTrue(note_tabs[0]["_user_renamed"])
            store.close()

if __name__ == "__main__":
    unittest.main()
