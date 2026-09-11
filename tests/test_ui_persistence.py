import tempfile
import sys
import unittest
from pathlib import Path


class TestUiPersistence(unittest.TestCase):
    def test_resource_shortcuts_are_normalized(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过资源快捷方式配置测试")

        normalized = mw._normalize_resource_shortcuts(
            [
                {"kind": "url", "title": "", "target": "example.com", "id": "same"},
                {"kind": "app", "title": "编辑器", "target": "~", "id": "same"},
                {"kind": "url", "target": "ftp://example.com"},
                {"kind": "bad", "target": ""},
            ]
        )

        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0]["kind"], "url")
        self.assertEqual(normalized[0]["target"], "https://example.com")
        self.assertEqual(normalized[0]["title"], "example.com")
        self.assertEqual(normalized[1]["kind"], "app")
        self.assertEqual(normalized[1]["title"], "编辑器")
        self.assertNotEqual(normalized[0]["id"], normalized[1]["id"])

    def test_remembered_resizable_page_sizes_are_normalized(self) -> None:
        try:
            from PyQt6.QtCore import QSize
        except Exception:
            self.skipTest("缺少 PyQt6，跳过界面状态工具测试")
        from types import MethodType, SimpleNamespace

        import deepcat.ui.main_window as mw

        dummy = SimpleNamespace(
            _DEFAULT_WINDOW_WIDTH=800,
            _DEFAULT_WINDOW_HEIGHT=520,
            _MAX_WINDOW_EXTENT=16777215,
            _REMEMBERED_RESIZABLE_PAGE_KEYS={3: "later_read", 4: "clipboard_history", 5: "table_notes"},
        )
        dummy._coerce_remembered_resizable_page_size = MethodType(mw.MainWindow._coerce_remembered_resizable_page_size, dummy)
        dummy._load_remembered_resizable_page_sizes = MethodType(mw.MainWindow._load_remembered_resizable_page_sizes, dummy)
        dummy._remembered_resizable_page_sizes_payload = MethodType(mw.MainWindow._remembered_resizable_page_sizes_payload, dummy)

        dummy._remembered_resizable_page_sizes = dummy._load_remembered_resizable_page_sizes({
            "resizable_page_sizes": {
                "later_read": {"width": 930, "height": 610},
                "4": [940, 620],
                "table_notes": QSize(200, 100),
            }
        })

        self.assertEqual(
            dummy._remembered_resizable_page_sizes_payload(),
            {
                "later_read": {"width": 930, "height": 610},
                "clipboard_history": {"width": 930, "height": 610},
                "table_notes": {"width": 930, "height": 610},
            },
        )

    def test_close_event_flushes_table_notes_before_store_close(self) -> None:
        try:
            from PyQt6.QtWidgets import QApplication, QWidget
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过关闭保存测试")

        app = QApplication.instance() or QApplication([])
        calls: list[str] = []

        class FakeEvent:
            def accept(self) -> None:
                calls.append("accept")

            def ignore(self) -> None:
                calls.append("ignore")

        class FakeTimer:
            def stop(self) -> None:
                calls.append("stop-timer")

        class FakeStore:
            def __init__(self, name: str) -> None:
                self.name = name

            def close(self) -> None:
                calls.append(f"close-{self.name}")

        dummy = QWidget()
        try:
            dummy._allow_quit = True
            dummy._tray = None
            dummy._tray_menu_visible = False
            dummy._table_notes_save_timer = FakeTimer()
            dummy._save_table_notes_settings = lambda: calls.append("save-table-notes")
            dummy._persist_ui_state = lambda: calls.append("persist-ui")
            dummy._table_notes_store = FakeStore("table-notes")
            dummy._todo_store = FakeStore("todo")
            dummy._later_read_store = FakeStore("later-read")
            dummy.cleanup = lambda: calls.append("cleanup")

            mw.MainWindow.closeEvent(dummy, FakeEvent())

            self.assertEqual(
                calls[:4],
                ["stop-timer", "save-table-notes", "persist-ui", "close-table-notes"],
            )
            self.assertIn("accept", calls)
        finally:
            dummy.deleteLater()
            app.processEvents()

    def test_ima_context_menu_only_for_note_tabs(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过 IMA 标签页菜单测试")
        from types import SimpleNamespace

        captured: list[list[str]] = []

        class FakePopup:
            def __init__(self, items, parent=None):
                captured.append([str(item[0]) for item in items])

            def show_at_pos(self, pos):
                return

        class FakeButton:
            def __init__(self, tab_type: str, tab_index: int) -> None:
                self._tab_type = tab_type
                self._tab_index = tab_index

            def mapToGlobal(self, pos):
                return pos

        import deepcat.ui.post_capture_actions as pca

        old_popup = pca.OcrGenericMenuPopup
        pca.OcrGenericMenuPopup = FakePopup  # type: ignore[assignment]
        try:
            dummy = SimpleNamespace(
                _table_tabs=[{"name": "表格1", "password": "", "group_name": ""}],
                _note_tabs=[{"name": "记事本1", "password": "", "group_name": "", "ima_config": {"enabled": False}}],
                _pin_tab_content=lambda *a, **k: None,
                _remove_tab_password=lambda *a, **k: None,
                _set_tab_password=lambda *a, **k: None,
                _set_tab_ima=lambda *a, **k: None,
                _sync_tab_to_ima_note=lambda *a, **k: None,
                _append_tab_to_ima_note=lambda *a, **k: None,
                _download_tab_from_ima_note=lambda *a, **k: None,
                _add_tab_to_ima_knowledge_base=lambda *a, **k: None,
                _import_url_to_ima_knowledge_base=lambda *a, **k: None,
                _search_ima_knowledge_base=lambda *a, **k: None,
                _on_close_tab=lambda *a, **k: None,
                _move_tab_to_group=lambda *a, **k: None,
                _move_tab_to_new_group=lambda *a, **k: None,
            )
            bound = mw.MainWindow._on_tab_context_menu.__get__(dummy, object)

            bound(None, FakeButton("table", 0))
            bound(None, FakeButton("note", 0))
        finally:
            pca.OcrGenericMenuPopup = old_popup  # type: ignore[assignment]

        table_menu = [item for item in captured[0] if item != "-"]
        note_menu = [item for item in captured[1] if item != "-"]
        self.assertNotIn("设置IMA", table_menu)
        self.assertIn("设置IMA", note_menu)
        self.assertIn("下载IMA笔记", note_menu)
        self.assertIn("追加到IMA笔记", note_menu)
        self.assertNotIn("同步到IMA笔记", note_menu)
        self.assertIn("添加到IMA知识库", note_menu)
        self.assertNotIn("导入网址到IMA知识库", note_menu)
        self.assertNotIn("搜索IMA知识库", note_menu)
        self.assertIn("关闭", table_menu)
        self.assertIn("批量管理", table_menu)
        self.assertIn("删除笔记", note_menu)
        self.assertIn("批量管理", note_menu)
        self.assertNotIn("关闭", note_menu)
        delete_index = captured[1].index("删除笔记")
        self.assertEqual(captured[1][delete_index + 1], "批量管理")
        self.assertEqual(captured[1][delete_index + 2], "-")
        self.assertEqual(captured[1][delete_index + 3], "移动到：未分组")

    def test_ima_auto_sync_is_debounced_and_silent(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过 IMA 自动同步测试")
        from types import MethodType, SimpleNamespace

        class FakeTimer:
            def __init__(self) -> None:
                self.started_with: list[int] = []

            def start(self, delay_ms: int) -> None:
                self.started_with.append(delay_ms)

        calls: list[tuple[int, str, bool]] = []
        timer = FakeTimer()
        dummy = SimpleNamespace(
            _loading_table_notes=False,
            _active_note_tab=0,
            _note_tabs=[
                {
                    "name": "项目笔记",
                    "ima_config": {
                        "enabled": True,
                        "auto_sync_enabled": True,
                        "client_id": "client",
                        "api_key": "key",
                    }
                }
            ],
            _ima_auto_sync_pending_note_index=None,
            _ima_auto_sync_timer=timer,
            _IMA_AUTO_SYNC_DELAY_MS=5000,
            _ima_sync_worker=None,
            _run_ima_tab_action=lambda index, action, extra=None, silent=False: calls.append((index, action, silent)),
        )
        dummy._schedule_ima_auto_sync_for_active_note = MethodType(mw.MainWindow._schedule_ima_auto_sync_for_active_note, dummy)
        dummy._run_pending_ima_auto_sync = MethodType(mw.MainWindow._run_pending_ima_auto_sync, dummy)
        dummy._is_default_note_tab_name = mw.MainWindow._is_default_note_tab_name
        dummy._should_sync_note_tab_to_ima = MethodType(mw.MainWindow._should_sync_note_tab_to_ima, dummy)

        dummy._schedule_ima_auto_sync_for_active_note()
        self.assertEqual(timer.started_with, [5000])
        self.assertEqual(dummy._ima_auto_sync_pending_note_index, 0)

        dummy._run_pending_ima_auto_sync()
        self.assertEqual(calls, [(0, "append_note", True)])
        self.assertIsNone(dummy._ima_auto_sync_pending_note_index)

        timer.started_with.clear()
        dummy._note_tabs[0]["ima_config"]["auto_sync_enabled"] = False
        dummy._schedule_ima_auto_sync_for_active_note()
        self.assertEqual(timer.started_with, [])

    def test_ima_prerequisite_errors_use_tray_notification(self) -> None:
        try:
            from PyQt6.QtWidgets import QMessageBox
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过 IMA 通知测试")
        from types import MethodType, SimpleNamespace

        notifications: list[tuple[str, str, int]] = []
        dialogs: list[str] = []
        dummy = SimpleNamespace(
            _note_tabs=[{"name": "项目笔记", "ima_config": {"enabled": False}}],
            _send_tray_notification=lambda title, message, duration: notifications.append((title, message, duration)),
        )
        dummy._show_ima_notification = MethodType(mw.MainWindow._show_ima_notification, dummy)
        dummy._run_ima_tab_action = MethodType(mw.MainWindow._run_ima_tab_action, dummy)
        dummy._is_default_note_tab_name = mw.MainWindow._is_default_note_tab_name
        dummy._should_sync_note_tab_to_ima = MethodType(mw.MainWindow._should_sync_note_tab_to_ima, dummy)

        old_information = QMessageBox.information
        old_warning = QMessageBox.warning
        QMessageBox.information = lambda *args, **kwargs: dialogs.append("information")  # type: ignore[assignment]
        QMessageBox.warning = lambda *args, **kwargs: dialogs.append("warning")  # type: ignore[assignment]
        try:
            dummy._run_ima_tab_action(0, "append_note")
            dummy._note_tabs[0]["ima_config"] = {"enabled": True}
            dummy._run_ima_tab_action(0, "download_note")
            dummy._note_tabs[0]["ima_config"] = {"enabled": True, "knowledge_base_enabled": False}
            dummy._run_ima_tab_action(0, "add_note_to_kb")
        finally:
            QMessageBox.information = old_information  # type: ignore[assignment]
            QMessageBox.warning = old_warning  # type: ignore[assignment]

        self.assertEqual(dialogs, [])
        self.assertEqual(
            notifications,
            [
                ("IMA", "请先在“设置IMA”中启用并保存配置。", 2600),
                ("IMA", "请先在“设置IMA”中填写绑定笔记 ID。", 2600),
                ("IMA", "请先在“设置IMA”中启用知识库并填写知识库 ID。", 2600),
            ],
        )

    def test_ima_settings_are_shared_without_remote_binding(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过 IMA 配置同步测试")
        from types import MethodType, SimpleNamespace

        config = {
            "enabled": True,
            "auto_sync_enabled": True,
            "client_id": "client",
            "api_key": "key",
            "note_folder_id": "folder-1",
            "note_folder_name": "常用",
            "remote_note_id": "note-1",
            "remote_note_title": "常用命令",
            "knowledge_base_enabled": True,
            "knowledge_base_id": "kb-1",
            "knowledge_base_name": "知识库",
            "knowledge_folder_id": "kf-1",
            "knowledge_folder_name": "文件夹",
            "knowledge_media_id": "media-1",
            "last_sync_at": "2026-06-19 12:00:00",
        }
        dummy = SimpleNamespace(
            _note_tabs=[
                {"name": "常用命令", "ima_config": {}},
                {"name": "提示词", "ima_config": {"remote_note_id": "old-note", "remote_note_title": "旧绑定"}},
            ]
        )
        dummy._apply_shared_ima_config_to_note_tabs = MethodType(mw.MainWindow._apply_shared_ima_config_to_note_tabs, dummy)

        dummy._apply_shared_ima_config_to_note_tabs(0, config)

        source_config = dummy._note_tabs[0]["ima_config"]
        target_config = dummy._note_tabs[1]["ima_config"]
        self.assertEqual(source_config["remote_note_id"], "note-1")
        self.assertEqual(source_config["remote_note_title"], "常用命令")
        self.assertEqual(target_config["client_id"], "client")
        self.assertEqual(target_config["api_key"], "key")
        self.assertEqual(target_config["note_folder_id"], "folder-1")
        self.assertEqual(target_config["knowledge_base_id"], "kb-1")
        self.assertTrue(target_config["enabled"])
        self.assertTrue(target_config["auto_sync_enabled"])
        self.assertEqual(target_config["remote_note_id"], "")
        self.assertEqual(target_config["remote_note_title"], "")
        self.assertEqual(target_config["knowledge_media_id"], "")
        self.assertEqual(target_config["last_sync_at"], "")

    def test_ima_download_apply_stops_pending_auto_append(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过 IMA 下载应用测试")
        from types import MethodType, SimpleNamespace

        class FakeTimer:
            def __init__(self) -> None:
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        class FakeEditor:
            def __init__(self) -> None:
                self.html = ""
                self.blocked: list[bool] = []

            def blockSignals(self, blocked: bool) -> bool:
                self.blocked.append(bool(blocked))
                return False

            def setHtml(self, html: str) -> None:
                self.html = html

        timer = FakeTimer()
        editor = FakeEditor()
        dummy = SimpleNamespace(
            _note_tabs=[{"html": "旧内容"}],
            _active_note_tab=0,
            _notes_editor=editor,
            _loading_table_notes=False,
            _ima_auto_sync_timer=timer,
            _ima_auto_sync_pending_note_index=0,
            _active_pinned_notes={},
        )
        dummy._clear_pending_ima_auto_sync = MethodType(mw.MainWindow._clear_pending_ima_auto_sync, dummy)
        dummy._latest_ima_appended_note_content = mw.MainWindow._latest_ima_appended_note_content
        dummy._ima_note_content_to_html = mw.MainWindow._ima_note_content_to_html
        dummy._apply_downloaded_ima_note_content = MethodType(mw.MainWindow._apply_downloaded_ima_note_content, dummy)

        dummy._apply_downloaded_ima_note_content(0, "# 标题\n\n远端正文")

        self.assertTrue(timer.stopped)
        self.assertIsNone(dummy._ima_auto_sync_pending_note_index)
        self.assertIn("远端正文", dummy._note_tabs[0]["html"])
        self.assertIn("远端正文", editor.html)
        self.assertEqual(editor.blocked, [True, False])
        self.assertFalse(dummy._loading_table_notes)

    def test_ima_download_keeps_latest_markdown_appended_version(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过 IMA 下载截取测试")

        content = (
            "# 常用命令\n\n"
            "旧版本内容\n"
            "123456\n\n"
            "---\n\n"
            "# 常用命令\n\n"
            "最新版本内容\n"
            "ABC\n"
        )

        latest = mw.MainWindow._latest_ima_appended_note_content(content)

        self.assertIn("最新版本内容", latest)
        self.assertIn("ABC", latest)
        self.assertNotIn("旧版本内容", latest)
        self.assertNotIn("123456", latest)
        self.assertTrue(latest.startswith("# 常用命令"))

    def test_ima_download_keeps_latest_html_appended_version(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过 IMA HTML 下载截取测试")

        content = (
            "<h1>常用命令</h1>"
            "<p>旧版本内容</p>"
            "<hr />"
            "<h1>常用命令</h1>"
            "<p>最新版本内容</p>"
        )

        latest = mw.MainWindow._latest_ima_appended_note_content(content)

        self.assertIn("最新版本内容", latest)
        self.assertNotIn("旧版本内容", latest)
        self.assertTrue(latest.lower().startswith("<h1"))

    def test_tab_list_menu_uses_grouped_popup_with_all_tabs(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过标签列表菜单测试")
        from types import MethodType, SimpleNamespace

        captured: dict[str, object] = {}

        class FakeSignal:
            def connect(self, callback):
                captured["destroyed_callback"] = callback

        class FakePopup:
            def __init__(self, items, on_selected, **kwargs):
                captured["items"] = items
                captured["active_index"] = kwargs.get("active_index")
                captured["initially_expanded"] = kwargs.get("initially_expanded")
                captured["max_height"] = kwargs.get("max_height")
                captured["on_context_menu"] = kwargs.get("on_context_menu")
                self.destroyed = FakeSignal()

            def close(self):
                return

            def show_for_anchor(self, *args, **kwargs):
                captured["shown"] = True

        old_popup = mw.notes.GroupedNoteListPopup
        mw.notes.GroupedNoteListPopup = FakePopup  # type: ignore[assignment]
        try:
            dummy = SimpleNamespace(
                _table_tabs=[
                    {"name": "未分组表", "group_name": ""},
                    {"name": "研发表", "group_name": "研发"},
                    {"name": "归档表", "group_name": "归档"},
                ],
                _active_table_tab=1,
                _tab_list_menu=None,
                _select_tab_from_list_menu=lambda *a, **k: None,
                _show_tab_list_context_menu=lambda *a, **k: None,
            )
            dummy._tab_list_display_name = MethodType(mw.MainWindow._tab_list_display_name, dummy)
            bound = mw.MainWindow._show_tab_list_menu.__get__(dummy, object)
            bound("table", object())
        finally:
            mw.notes.GroupedNoteListPopup = old_popup  # type: ignore[assignment]

        items = captured["items"]
        self.assertEqual([item["group_name"] for item in items], ["", "研发", "归档"])
        self.assertEqual(captured["active_index"], 1)
        self.assertTrue(captured["initially_expanded"])
        self.assertEqual(captured["max_height"], 450)
        self.assertTrue(callable(captured["on_context_menu"]))
        self.assertTrue(captured["shown"])

    def test_grouped_tab_list_popup_initially_expands_child_items(self) -> None:
        try:
            from PyQt6.QtWidgets import QApplication, QToolButton
            from deepcat.ui.tab_list_popup import GroupedNoteListPopup
        except Exception:
            self.skipTest("缺少 PyQt6，跳过分组列表弹窗测试")

        app = QApplication.instance() or QApplication([])
        popup = GroupedNoteListPopup(
            [
                {"name": "笔记A", "text": "笔记A", "index": 0, "group_name": "分组A"},
                {"name": "笔记B", "text": "笔记B", "index": 1, "group_name": "分组B"},
            ],
            lambda item: None,
            initially_expanded=True,
        )
        try:
            from PyQt6.QtWidgets import QPushButton
            sub_items = [
                button
                for button in popup.findChildren(QPushButton)
                if str(button.objectName()).startswith("RoundedListPopupSubItem")
            ] + [
                button
                for button in popup.findChildren(QToolButton)
                if str(button.objectName()).startswith("RoundedListPopupSubItem")
            ]
            self.assertEqual(len(sub_items), 2)
            self.assertTrue(all(not button.isHidden() for button in sub_items))
        finally:
            popup.close()
            popup.deleteLater()
            app.processEvents()

    def test_grouped_tab_list_popup_search_content(self) -> None:
        try:
            from PyQt6.QtWidgets import QApplication, QPushButton
            from deepcat.ui.tab_list_popup import GroupedNoteListPopup
        except Exception:
            self.skipTest("缺少 PyQt6，跳过弹窗内容搜索测试")

        app = QApplication.instance() or QApplication([])
        popup = GroupedNoteListPopup(
            [
                {
                    "name": "Python\u7b14\u8bb0",
                    "text": "Python\u7b14\u8bb0",
                    "index": 0,
                    "group_name": "\u5f00\u53d1",
                    "html": "<html><body>\u8fd9\u662f\u5173\u4e8ePython\u7f16\u7a0b\u548c\u5143\u7f16\u7a0b\u7684\u5185\u5bb9</body></html>"
                },
                {
                    "name": "Go\u7b14\u8bb0",
                    "text": "Go\u7b14\u8bb0",
                    "index": 1,
                    "group_name": "\u5f00\u53d1",
                    "html": "<html><body>\u8fd9\u662f\u5173\u4e8eGo\u534f\u7a0b\u548c\u901a\u9053\u7684\u5185\u5bb9</body></html>"
                },
                {
                    "name": "\u5355\u5143\u6d4b\u8bd5\u8868",
                    "text": "\u5355\u5143\u6d4b\u8bd5\u8868",
                    "index": 2,
                    "group_name": "\u6d4b\u8bd5",
                    "data": [["test", "\u5355\u5143\u6d4b\u8bd5"], ["code", "\u8986\u76d6\u7387"]]
                }
            ],
            lambda item: None,
        )
        try:
            # 1. 搜内容 "元编程"
            popup._on_search_text_changed("\u5143\u7f16\u7a0b")
            visible_btns = [
                btn for _, btn, item, _ in popup._note_button_widgets
                if not btn.isHidden()
            ]
            self.assertEqual(len(visible_btns), 1)
            self.assertEqual(visible_btns[0].toolTip(), "Python\u7b14\u8bb0")

            # 2. 搜内容 "协程"
            popup._on_search_text_changed("\u534f\u7a0b")
            visible_btns = [
                btn for _, btn, item, _ in popup._note_button_widgets
                if not btn.isHidden()
            ]
            self.assertEqual(len(visible_btns), 1)
            self.assertEqual(visible_btns[0].toolTip(), "Go\u7b14\u8bb0")

            # 3. 搜单元格数据 "覆盖率"
            popup._on_search_text_changed("\u8986\u76d6\u7387")
            visible_btns = [
                btn for _, btn, item, _ in popup._note_button_widgets
                if not btn.isHidden()
            ]
            self.assertEqual(len(visible_btns), 1)
            self.assertEqual(visible_btns[0].toolTip(), "\u5355\u5143\u6d4b\u8bd5\u8868")

            # 4. 搜标题 "笔记"
            popup._on_search_text_changed("\u7b14\u8bb0")
            visible_btns = [
                btn for _, btn, item, _ in popup._note_button_widgets
                if not btn.isHidden()
            ]
            self.assertEqual(len(visible_btns), 2)
            self.assertSetEqual(
                {btn.toolTip() for btn in visible_btns},
                {"Python\u7b14\u8bb0", "Go\u7b14\u8bb0"}
            )
        finally:
            popup.close()
            popup.deleteLater()
            app.processEvents()

    def test_tab_list_context_menu_can_delete_right_clicked_note(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过列表右键菜单测试")
        from types import SimpleNamespace

        captured: dict[str, object] = {}
        calls: list[tuple[str, int] | str] = []

        class FakePopup:
            def __init__(self, items, parent=None):
                captured["items"] = items

            def show_at_pos(self, pos):
                captured["pos"] = pos

        import deepcat.ui.post_capture_actions as pca

        old_popup = pca.OcrGenericMenuPopup
        pca.OcrGenericMenuPopup = FakePopup  # type: ignore[assignment]
        try:
            dummy = SimpleNamespace(
                _note_tabs=[
                    {"name": "笔记A", "group_name": ""},
                    {"name": "笔记B", "group_name": "研发"},
                ],
                _tab_list_menu=SimpleNamespace(close=lambda: calls.append("close")),
                _on_close_tab=lambda tab_type, index: calls.append((tab_type, index)),
                _show_tab_batch_delete_dialog=lambda tab_type: calls.append(("batch", -1)),
            )
            bound = mw.MainWindow._show_tab_list_context_menu.__get__(dummy, object)
            bound("note", object(), {"index": 1})
        finally:
            pca.OcrGenericMenuPopup = old_popup  # type: ignore[assignment]

        menu_items = captured["items"]
        self.assertEqual([item[0] for item in menu_items], ["删除笔记", "-", "批量管理"])
        menu_items[0][1]()
        self.assertEqual(calls, ["close", ("note", 1)])

    def test_close_tab_deletes_single_tab_after_confirmation(self) -> None:
        try:
            import deepcat.ui.main_window as mw
            from PyQt6.QtWidgets import QMessageBox
        except Exception:
            self.skipTest("缺少 PyQt6，跳过标签删除测试")
        from types import MethodType, SimpleNamespace

        calls = []
        old_question = QMessageBox.question
        QMessageBox.question = lambda *args, **kwargs: QMessageBox.StandardButton.Yes
        try:
            dummy = SimpleNamespace(
                _note_tabs=[{"name": "营销策划专家"}],
                _table_tabs=[],
                _delete_tabs_by_indices=lambda tab_type, indices: calls.append((tab_type, indices)) or True,
                _show_table_notes_status=lambda text, **kwargs: calls.append(("status", text)),
            )
            dummy._tab_list_display_name = MethodType(mw.MainWindow._tab_list_display_name, dummy)
            dummy._on_close_tab = MethodType(mw.MainWindow._on_close_tab, dummy)

            dummy._on_close_tab("note", 0)
        finally:
            QMessageBox.question = old_question

        self.assertEqual(calls[0], ("note", [0]))
        self.assertEqual(calls[1][0], "status")

    def test_batch_delete_group_checkbox_selects_group_items(self) -> None:
        try:
            from PyQt6.QtWidgets import QApplication, QWidget
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过批量删除分组勾选测试")
        from types import MethodType

        app = QApplication.instance() or QApplication([])
        dummy = QWidget()
        dummy._note_tabs = [
            {"name": "A", "group_name": "", "password": ""},
            {"name": "B", "group_name": "研发", "password": ""},
            {"name": "C", "group_name": "研发", "password": ""},
        ]
        dummy._tab_list_display_name = MethodType(mw.MainWindow._tab_list_display_name, dummy)
        dummy._show_tab_batch_delete_dialog = MethodType(mw.MainWindow._show_tab_batch_delete_dialog, dummy)

        checked_after_group_click: dict[str, bool] = {}
        old_exec = mw.QDialog.exec

        def fake_exec(dialog):
            boxes = {box.text(): box for box in dialog.findChildren(mw.QCheckBox)}
            boxes["研发（2）"].click()
            app.processEvents()
            checked_after_group_click["B"] = boxes["B"].isChecked()
            checked_after_group_click["C"] = boxes["C"].isChecked()
            checked_after_group_click["A"] = boxes["A"].isChecked()
            return mw.QDialog.DialogCode.Rejected

        mw.QDialog.exec = fake_exec  # type: ignore[assignment]
        try:
            dummy._show_tab_batch_delete_dialog("note")
        finally:
            mw.QDialog.exec = old_exec  # type: ignore[assignment]
            dummy.deleteLater()
            app.processEvents()

        self.assertTrue(checked_after_group_click["B"])
        self.assertTrue(checked_after_group_click["C"])
        self.assertFalse(checked_after_group_click["A"])

    def test_batch_delete_tabs_keeps_active_group_visible(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过批量删除标签测试")
        from types import MethodType, SimpleNamespace

        calls: list[str] = []
        dummy = SimpleNamespace(
            _table_tabs=[
                {"name": "A", "group_name": "", "password": ""},
                {"name": "B", "group_name": "研发", "password": ""},
                {"name": "C", "group_name": "研发", "password": ""},
            ],
            _active_table_tab=2,
            _current_table_group="研发",
            _save_current_table_tab_data=lambda: calls.append("save-current"),
            _load_current_table_tab_data=lambda: calls.append("load-current"),
            _recalculate_table_sums=lambda: calls.append("sum"),
            _refresh_tab_bars=lambda: calls.append("refresh"),
            _save_table_notes_settings=lambda: calls.append("save-settings"),
            _scroll_active_tab_into_view=lambda tab_type: calls.append("scroll-active"),
        )
        dummy._active_index_after_deletion = mw.MainWindow._active_index_after_deletion
        dummy._set_current_group_label = MethodType(mw.MainWindow._set_current_group_label, dummy)
        dummy._ensure_active_tab_visible_in_current_group = MethodType(mw.MainWindow._ensure_active_tab_visible_in_current_group, dummy)
        dummy._delete_tabs_by_indices = MethodType(mw.MainWindow._delete_tabs_by_indices, dummy)

        dummy._delete_tabs_by_indices("table", [1])

        self.assertEqual([tab["name"] for tab in dummy._table_tabs], ["A", "C"])
        self.assertEqual(dummy._active_table_tab, 1)
        self.assertEqual(dummy._current_table_group, "研发")
        self.assertEqual(calls, ["save-current", "load-current", "sum", "refresh", "save-settings", "scroll-active"])

    def test_external_new_note_inherits_ima_config_and_auto_appends(self) -> None:
        try:
            import deepcat.ui.main_window as mw
        except Exception:
            self.skipTest("缺少 PyQt6，跳过外部笔记 IMA 自动追加测试")
        from types import MethodType, SimpleNamespace

        calls: list[tuple[int, str, bool]] = []
        saves: list[str] = []
        source_config = {
            "enabled": True,
            "auto_sync_enabled": True,
            "client_id": "cid",
            "api_key": "key",
            "remote_note_id": "old-remote",
            "remote_note_title": "旧远端",
        }
        dummy = SimpleNamespace(
            _note_tabs=[
                {"name": "已有", "ima_config": source_config},
                {"name": "新建", "ima_config": mw.default_ima_config()},
            ],
            _active_note_tab=0,
            _ima_sync_worker=None,
            _ima_auto_sync_timer=SimpleNamespace(start=lambda *_: None),
            _IMA_AUTO_SYNC_DELAY_MS=5000,
            _save_table_notes_settings=lambda: saves.append("save"),
            _run_ima_tab_action=lambda index, action, silent=False: calls.append((index, action, silent)),
        )
        dummy._ima_auto_sync_config_ready = mw.MainWindow._ima_auto_sync_config_ready
        dummy._is_default_note_tab_name = mw.MainWindow._is_default_note_tab_name
        dummy._should_sync_note_tab_to_ima = MethodType(mw.MainWindow._should_sync_note_tab_to_ima, dummy)
        dummy._inherited_ima_config_for_new_note = MethodType(mw.MainWindow._inherited_ima_config_for_new_note, dummy)
        dummy._sync_external_note_to_ima = MethodType(mw.MainWindow._sync_external_note_to_ima, dummy)

        dummy._sync_external_note_to_ima(1, inherit_config=True)

        inherited = dummy._note_tabs[1]["ima_config"]
        self.assertTrue(inherited["enabled"])
        self.assertTrue(inherited["auto_sync_enabled"])
        self.assertEqual(inherited["client_id"], "cid")
        self.assertEqual(inherited["api_key"], "key")
        self.assertEqual(inherited["remote_note_id"], "")
        self.assertEqual(calls, [(1, "append_note", True)])
        self.assertEqual(saves, ["save"])

    def test_reopen_restores_ui_state(self) -> None:
        if sys.platform.startswith("win") and sys.version_info >= (3, 13):
            self.skipTest("Python 3.13 + PyQt6 on Windows may crash during QApplication teardown")
        try:
            from PyQt6.QtCore import QEvent, QRect
            from PyQt6.QtWidgets import QApplication, QWidget
            from PyQt6.QtGui import QPalette
        except Exception:
            self.skipTest("缺少 PyQt6，跳过界面集成测试")

        import deepcat.settings_store as ss
        import deepcat.ui.main_window as mw

        class _DummyTray:
            def setIcon(self, *a, **k):
                return

            def setToolTip(self, *a, **k):
                return

            def setContextMenu(self, *a, **k):
                return

            def show(self, *a, **k):
                return

            def setVisible(self, *a, **k):
                return

            def showMessage(self, *a, **k):
                return

            class _Sig:
                def connect(self, *a, **k):
                    return

            activated = _Sig()

        old_init_tray = mw.MainWindow._init_tray
        old_close_event = mw.MainWindow.closeEvent
        old_startup = mw.MainWindow._start_deferred_startup_services
        old_hotkey = mw.GlobalStartHotkey

        class _DummyHotkey:
            def __init__(self, *a, **k):
                return

            def start(self):
                return

            def cleanup(self):
                return

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_get_app_dir = ss.get_app_dir
            old_get_files_dir = ss.get_files_dir
            windows = []
            app = None
            try:
                ss.get_app_dir = lambda: root  # type: ignore[assignment]

                def _files_dir():
                    p = root / "files"
                    p.mkdir(parents=True, exist_ok=True)
                    return p

                ss.get_files_dir = _files_dir  # type: ignore[assignment]

                mw.GlobalStartHotkey = _DummyHotkey  # type: ignore[assignment]
                mw.MainWindow._init_tray = lambda self: _DummyTray()  # type: ignore[assignment]
                mw.MainWindow.closeEvent = lambda self, e: e.accept()  # type: ignore[assignment]
                mw.MainWindow._start_deferred_startup_services = lambda self: None  # type: ignore[assignment]

                app = QApplication.instance() or QApplication([])

                w1 = mw.MainWindow()
                windows.append(w1)
                w1.move(120, 140)
                self.assertNotIn("全屏截图", [w1._mode.itemText(i) for i in range(w1._mode.count())])
                w1._mode.setCurrentText("滚动截图")
                w1._adaptive_wait.setChecked(True)
                w1._boost_scroll.setChecked(False)
                w1._speed.setValue(12)
                w1._fmt_pdf.setChecked(True)
                w1._merge_pdf.setChecked(True)
                w1._merge_image.setChecked(False)
                w1._dual_output.setChecked(True)
                w1._persist_ui_state()

                w2 = mw.MainWindow()
                windows.append(w2)
                w2.show()
                app.processEvents()

                self.assertEqual(w2._mode.currentText(), "滚动截图")
                self.assertEqual(w2._adaptive_wait.isChecked(), True)
                self.assertEqual(w2._boost_scroll.isChecked(), False)
                self.assertEqual(w2._speed.value(), 12)
                self.assertEqual(w2._fmt_pdf.isChecked(), True)
                self.assertEqual(w2._merge_pdf.isChecked(), True)
                self.assertEqual(w2._merge_image.isChecked(), False)
                self.assertEqual(w2._dual_output.isChecked(), True)

                g = w2.geometry()
                self.assertEqual((g.width(), g.height()), (800, 520))
                available = w2.screen().availableGeometry()
                if available.contains(QRect(120, 140, 800, 520)):
                    self.assertEqual((g.x(), g.y()), (120, 140))
                else:
                    # 无显示器的 CI 屏幕较小，Qt 会把恢复后的窗口移回可见区域。
                    self.assertTrue(available.contains(w2.frameGeometry().topLeft()))

                w2._switch_page(3)
                self.assertEqual((w2.minimumWidth(), w2.minimumHeight()), (800, 520))
                self.assertGreater(w2.maximumWidth(), 800)
                self.assertGreater(w2.maximumHeight(), 520)
                self.assertTrue(w2.autoFillBackground())
                self.assertTrue(w2.centralWidget().autoFillBackground())
                self.assertTrue(w2._stack.currentWidget().autoFillBackground())
                w2.resize(930, 610)
                app.processEvents()
                w2._switch_page(0)
                self.assertEqual((w2.width(), w2.height()), (800, 520))
                w2._switch_page(3)
                self.assertEqual((w2.width(), w2.height()), (930, 610))

                w2._switch_page(4)
                self.assertEqual((w2.width(), w2.height()), (930, 610))
                w2.resize(940, 620)
                app.processEvents()
                w2._switch_page(0)
                self.assertEqual((w2.width(), w2.height()), (800, 520))
                w2._switch_page(3)
                self.assertEqual((w2.width(), w2.height()), (940, 620))
                w2._switch_page(4)
                self.assertEqual((w2.width(), w2.height()), (940, 620))

                w2._switch_page(5)
                self.assertEqual((w2.width(), w2.height()), (940, 620))
                w2.resize(950, 630)
                app.processEvents()
                w2._switch_page(0)
                self.assertEqual((w2.width(), w2.height()), (800, 520))
                w2._switch_page(4)
                self.assertEqual((w2.width(), w2.height()), (950, 630))
                w2._switch_page(5)
                self.assertEqual((w2.width(), w2.height()), (950, 630))

                w2._switch_page(0)
                self.assertEqual(
                    (w2.minimumWidth(), w2.minimumHeight(), w2.maximumWidth(), w2.maximumHeight()),
                    (800, 520, 800, 520),
                )

            finally:
                for window in reversed(windows):
                    window.cleanup()
                    window.close()
                    window.deleteLater()
                if app is not None:
                    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    app.processEvents()
                ss.get_app_dir = old_get_app_dir  # type: ignore[assignment]
                ss.get_files_dir = old_get_files_dir  # type: ignore[assignment]
                mw.MainWindow._init_tray = old_init_tray  # type: ignore[assignment]
                mw.MainWindow.closeEvent = old_close_event  # type: ignore[assignment]
                mw.MainWindow._start_deferred_startup_services = old_startup  # type: ignore[assignment]
                mw.GlobalStartHotkey = old_hotkey  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
